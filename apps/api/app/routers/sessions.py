"""Sessions, messages, and the chat stream.

Ordering rules for a streaming turn:

* user message committed before the upstream call
* assistant message and credit deduction committed together
* no charge for a turn that produced no output
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, update
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.deps import CurrentUser, DbSession, client_ip
from app.models.chat import (
    ChatSession,
    Message,
    Role,
    RoutingMode,
    SessionKind,
    TurnFailure,
)
from app.models.user import AuditEvent, User, utcnow
from app.models.workspace import Agent as WorkspaceAgent
from app.models.workspace import (
    AgentVisibility,
    Artifact,
    ArtifactKind,
    ArtifactVersion,
    Job,
    Memory,
    MemoryType,
    Project,
    Skill,
    SkillSource,
    StoredFile,
)
from app.models.workspace import Visibility as SkillVisibility
from app.schemas.auth import Preferences
from app.schemas.chat import (
    AudioRequest,
    ChooseVariant,
    CompareRequest,
    DiagramOut,
    DiagramRequest,
    DiagramStore,
    FigureSuggestion,
    FigureSuggestRequest,
    ImageRequest,
    MessageOut,
    SendMessage,
    SessionBulkDelete,
    SessionCreate,
    SessionMade,
    SessionOut,
    SessionPatch,
    made_from_artifacts,
    snippet,
)
from app.schemas.workspace import ArtifactOut
from app.services import (
    adaptive_routing,
    artifact_extract,
    audiogen,
    design_templates,
    figures,
    governance,
    grounding,
    imagegen,
    lint,
    revise,
    richtext,
    settings_store,
)
from app.services import agent as agent_service
from app.services import auto_memory as auto_memory_service
from app.services import chat as chat_service
from app.services import deck as deck_service
from app.services import design as design_service
from app.services import (
    diagram as diagram_service,
)
from app.services import files as file_service
from app.services import litellm as litellm_service
from app.services import models as model_service
from app.services import page as page_service
from app.services import report as report_service
from app.services.context import build_messages, with_pictures
from app.services.credits import charge_for_tokens, has_headroom, settle
from app.services.tools.base import Tool, ToolContext, openai_snapshot
from app.services.tools.registry import build_tools
from app.services.workspace_context import (
    ContextBlock,
    ContextFile,
    WorkspaceContext,
    WorkspaceContextError,
    agent_settings,
    assemble,
    design_for,
    reads_pictures,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])


@dataclass(slots=True)
class _PrivacyResolution:
    models: list[dict]
    action: str
    findings: list[governance.Finding]
    routing: dict[str, Any]
    mask_outbound: bool = False

    @property
    def strict_local(self) -> bool:
        return bool(self.models) and all(
            model.get("dataBoundary") == "self_hosted" and model.get("strictLocal") is True
            for model in self.models
        )


def _strict_model(model: dict) -> bool:
    return model.get("dataBoundary") == "self_hosted" and model.get("strictLocal") is True


_STRICT_LOCAL_TOOL_NAMES = frozenset(
    {
        # These tools only stage rows in this API process or search a shelf
        # already loaded into memory. Network-backed code execution is not
        # safe merely because its registry source says "builtin".
        "search_knowledge",
        "create_artifact",
        "create_chart",
    }
)


def _strict_local_tools(tools: list[Tool]) -> list[Tool]:
    """Keeps only tools whose current implementation has no network egress.

    This allowlist is deliberately implementation-specific. A future internal
    execution service needs its own explicit data-boundary contract before it
    can be admitted here; an HTTP endpoint configured by an administrator is
    not proof that protected arguments remain inside the boundary.
    """
    return [
        tool for tool in tools if tool.source == "builtin" and tool.name in _STRICT_LOCAL_TOOL_NAMES
    ]


def _serialized_tool_definitions(definitions: list[dict[str, Any]]) -> str:
    """Stable text for both privacy inspection and decision-token hashing."""
    return json.dumps(
        definitions,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _tool_definition_source(tools: list[Tool]) -> str:
    """The exact ordered schema array the OpenAI request would carry."""
    return _serialized_tool_definitions(openai_snapshot(tools))


def _drop_sensitive_tool_definitions(tools: list[Tool], *, legacy: bool = False) -> list[Tool]:
    """Drops, rather than rewrites, a sensitive schema for masked egress.

    Rewriting a function name can make it invalid and desynchronise the runner
    registry. Removing the complete tool guarantees no filename, connector
    description or nested JSON-Schema text found during preflight reaches the
    external model.
    """
    return [
        tool
        for tool in tools
        if not governance.findings(
            {"tool_definitions": _tool_definition_source([tool])},
            legacy=legacy,
        )
    ]


def _combined_boundary(models: list[dict]) -> str:
    boundaries = {str(model.get("dataBoundary") or "unknown") for model in models}
    return next(iter(boundaries)) if len(boundaries) == 1 else "mixed"


def _with_actual_model(routing: dict, routed_model: str, actual_model: str) -> dict:
    """Returns routing metadata updated from LiteLLM's provider-reported id."""
    actual = list(routing.get("actualModels") or [])
    if actual_model not in actual:
        actual.append(actual_model)
    routes = []
    matched = False
    for route in routing.get("modelRoutes") or []:
        if route.get("routedModel") == routed_model:
            routes.append({**route, "actualModel": actual_model})
            matched = True
        else:
            routes.append(route)
    if not matched:
        routes.append(
            {
                "routedModel": routed_model,
                "actualModel": actual_model,
                "dataBoundary": "unknown",
            }
        )
    updated = {
        **routing,
        "actualModels": actual,
        "modelRoutes": routes,
    }
    if len(routing.get("routedModels") or []) == 1:
        updated["actualModel"] = actual_model
    else:
        updated.pop("actualModel", None)
    return updated


def _allowed_models(user: User, catalogue: list[dict], *, kind: str) -> list[dict]:
    """Returns models allowed for this account and surface.

    An empty user allowlist means the whole live catalogue. Every fallback goes
    through this helper so a stale session id cannot escape the restriction.
    """
    allowed = set(user.allowed_models or [])
    return [
        model
        for model in catalogue
        if kind in model.get("kinds", []) and (not allowed or model.get("id") in allowed)
    ]


#: How far a model's answers travel, lowest first. `hybrid` sits with the
#: The rule lives with the candidate filters that use it, in `adaptive_routing`.
#: Routing may change what answers a turn; it may never change how far the turn
#: travels, and the outline model answers to the same rule for the same reason.
_widens_boundary = adaptive_routing.widens_boundary

#: Both Auto lanes. Everything that gates Auto — chat-only, the quality ceiling
#: it routes from, the ceiling surviving a turn-only override — is true of the
#: quality lane for exactly the reasons it is true of the cost one.
_AUTO_MODES = frozenset({RoutingMode.auto, RoutingMode.auto_quality})


def _planner_model(
    wanted: str | None,
    *,
    user: User,
    catalogue: list[dict],
    kind: str,
    writer: dict,
    strict_local: bool,
) -> dict | None:
    """The catalogue row the outline call should use, or `None` for the writer.

    A row, not an id: the call is billed at its own price.

    Refused when the account's allowlist, the surface, an inward privacy route,
    or the writer's own boundary would not allow it — an admin setting must not
    widen where a document's text goes.
    """
    if not wanted or strict_local:
        return None
    planner = model_service.find(_allowed_models(user, catalogue, kind=kind), str(wanted))
    if planner is None or _widens_boundary(planner, writer):
        log.info("outline model %s unusable here", wanted)
        return None
    return planner


async def _enrichment_model(writer: dict, *, strict_local: bool, disable_fallbacks: bool) -> dict:
    """The catalogue row that titles the session and extracts its memories.

    A row, not an id: side work billed at its own price, since titles and memory
    are the only calls nobody asks for.

    Falls back to the turn's own model when `title_model` is unset, and is then
    billed there. Usually a free self-hosted model, so usually zero.
    """
    if strict_local or disable_fallbacks:
        return writer
    resolved = await model_service.resolve_enrichment_model()
    if not resolved or resolved == writer["id"]:
        return writer
    catalogue = await model_service.list_models()
    return model_service.find(catalogue["models"], resolved) or writer


def _find_auto_quality_model(models: list[dict], model_id: str | None) -> dict | None:
    """Finds an answer model; privacy-only capacity is classifier-only."""
    model = model_service.find(models, model_id or "")
    return model if model is not None and model.get("privacyOnly") is not True else None


async def _require_auto_quality_model(user: User, model_id: str | None) -> None:
    """Requires Auto's quality ceiling to be live, allowed and chat-capable."""
    catalogue = await model_service.list_models_for_egress()
    usable = _allowed_models(user, catalogue.get("models", []), kind=SessionKind.chat.value)
    if _find_auto_quality_model(usable, model_id) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="auto_quality_model_required",
        )


def _cost_routing(
    *,
    decision: str,
    reason_code: str,
    requested_model: dict,
    selected_model: dict,
    mode: str = "auto",
    classifier_model: str | None = None,
    classification: adaptive_routing.Classification | None = None,
) -> dict[str, Any]:
    """One turn's routing decision, whichever direction it went.

    The wire key stays `costRouting` across both lanes: it is the envelope every
    reader already knows, and `mode` inside it says which lane wrote it.
    """
    route: dict[str, Any] = {
        "mode": mode,
        "decision": decision,
        "reasonCode": reason_code,
        "requestedModel": requested_model["id"],
        "selectedModel": selected_model["id"],
        "classifierVersion": adaptive_routing.CLASSIFIER_VERSION,
    }
    if classifier_model:
        route["classifierModel"] = classifier_model
    if classification is not None:
        route.update(
            {
                "complexity": classification.complexity,
                "confidence": classification.confidence,
                "classifierInputTokens": classification.input_tokens,
                "classifierOutputTokens": classification.output_tokens,
            }
        )
    return route


async def _resolve_cost_routing(
    *,
    #: The column behind this is a plain String, so a bare `str` is as ordinary
    #: an argument here as the enum. Compared by value throughout for that reason.
    mode: RoutingMode | str = RoutingMode.auto,
    db: DbSession,
    user: User,
    policy,
    catalogue: list[dict],
    quality_model: dict,
    classifier_messages: list[dict[str, str]],
    classifier_tool_definitions: list[dict[str, Any]],
    context_tokens: int,
    unsupported_reason: str | None,
) -> tuple[dict, dict[str, Any]]:
    """Returns an Auto turn's effective model and value-free route metadata.

    Both lanes run the same classifier over the same envelope and read a
    different half of its answer: cost acts on `low`, quality on `high`.
    Everything else — an unusable classifier, an empty candidate list, a
    confidence under the bar, any refusal at all — keeps the model the
    person chose. Neither lane has a way to act on a guess.
    """
    lane = str(getattr(mode, "value", mode))
    upgrading = lane == RoutingMode.auto_quality.value
    #: Each direction is switched on separately — they are opposite decisions
    #: about money, and an instance may want either without the other.
    lane_enabled = policy.adaptive_quality_enabled if upgrading else policy.adaptive_routing_enabled
    if not lane_enabled:
        return quality_model, _cost_routing(
            mode=lane,
            decision="bypassed",
            reason_code="disabled",
            requested_model=quality_model,
            selected_model=quality_model,
        )
    if unsupported_reason:
        return quality_model, _cost_routing(
            mode=lane,
            decision="bypassed",
            reason_code=unsupported_reason,
            requested_model=quality_model,
            selected_model=quality_model,
        )

    allowed = set(user.allowed_models or [])
    classifier_id = str(policy.adaptive_classifier_model_id or "")
    classifier_model = model_service.find(catalogue, classifier_id)
    if not adaptive_routing.classifier_is_usable(classifier_model, allowed_model_ids=allowed):
        return quality_model, _cost_routing(
            mode=lane,
            decision="classifier_unavailable",
            reason_code="classifier_unavailable",
            requested_model=quality_model,
            selected_model=quality_model,
            classifier_model=classifier_id or None,
        )

    if upgrading:
        candidates = adaptive_routing.quality_candidates(
            catalogue,
            list(policy.adaptive_quality_model_ids or [])[:3],
            quality_model=quality_model,
            allowed_model_ids=allowed,
            context_tokens=context_tokens,
            # Kept, unlike the economy lane's. Routing up to answer a turn the
            # smaller model could not, and removing its tools on the way, would
            # charge more for less.
            requires_tools=bool(classifier_tool_definitions),
        )
    else:
        candidates = adaptive_routing.economy_candidates(
            catalogue,
            list(policy.adaptive_economy_model_ids or [])[:3],
            quality_model=quality_model,
            allowed_model_ids=allowed,
            context_tokens=context_tokens,
            # A routed economy call deliberately receives no tools. This prevents
            # an unbounded tool result from invalidating the preflight context fit.
            requires_tools=False,
        )
    if not candidates:
        return quality_model, _cost_routing(
            mode=lane,
            decision="kept_quality",
            reason_code="no_quality_model" if upgrading else "no_economy_model",
            requested_model=quality_model,
            selected_model=quality_model,
            classifier_model=classifier_id,
        )

    classifier_input = adaptive_routing.classifier_context(
        classifier_messages,
        classifier_tool_definitions,
    )
    if classifier_input is None:
        return quality_model, _cost_routing(
            mode=lane,
            decision="kept_quality",
            reason_code="input_too_long",
            requested_model=quality_model,
            selected_model=quality_model,
            classifier_model=classifier_id,
        )

    # Never call this path through ``credentials_for``: that helper may fall
    # back to the master key. Auto classification is optional and therefore
    # requires the user's already-issued, account-scoped virtual key.
    api_key = litellm_service.user_key(user) or await litellm_service.ensure_key(user)
    if not api_key:
        return quality_model, _cost_routing(
            mode=lane,
            decision="classifier_unavailable",
            reason_code="classifier_key_unavailable",
            requested_model=quality_model,
            selected_model=quality_model,
            classifier_model=classifier_id,
        )
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    classification = await adaptive_routing.classify(
        model_id=classifier_id,
        context=classifier_input,
        user_id=user.id,
        api_key=api_key,
    )
    if classification is None:
        return quality_model, _cost_routing(
            mode=lane,
            decision="classifier_unavailable",
            reason_code="classifier_unavailable",
            requested_model=quality_model,
            selected_model=quality_model,
            classifier_model=classifier_id,
        )
    wanted = "high" if upgrading else "low"
    bar = adaptive_routing.MIN_HIGH_CONFIDENCE if upgrading else adaptive_routing.MIN_LOW_CONFIDENCE
    if classification.complexity != wanted or classification.confidence < bar:
        if classification.complexity == "uncertain":
            reason = "uncertain"
        elif classification.complexity != wanted:
            reason = f"{classification.complexity}_complexity"
        else:
            reason = "low_confidence"
        return quality_model, _cost_routing(
            mode=lane,
            decision="kept_quality",
            reason_code=reason,
            requested_model=quality_model,
            selected_model=quality_model,
            classifier_model=classifier_id,
            classification=classification,
        )

    selected = candidates[0]
    return selected, _cost_routing(
        mode=lane,
        decision="routed",
        reason_code=f"{wanted}_complexity",
        requested_model=quality_model,
        selected_model=selected,
        classifier_model=classifier_id,
        classification=classification,
    )


def _apply_effective_model(routing: dict[str, Any], model: dict) -> dict[str, Any]:
    """Updates privacy routing after a clean Auto decision."""
    model_id = model["id"]
    return {
        **routing,
        "routedModels": [model_id],
        "effectiveModels": [model_id],
        "actualModels": [],
        "dataBoundary": model.get("dataBoundary") or "unknown",
        "modelRoutes": [
            {
                "routedModel": model_id,
                "actualModel": None,
                "dataBoundary": model.get("dataBoundary") or "unknown",
            }
        ],
    }


def _substitution_routing(requested: dict, effective: dict) -> dict[str, Any]:
    """Routing metadata for a fallback made outside the privacy decision.

    Only chat resolves privacy, so only chat had somewhere to record a
    substitution. Cut back to what a substitution alone knows — the two ids the
    transcript's badge compares.
    """
    effective_id = effective["id"]
    boundary = effective.get("dataBoundary") or "unknown"
    return {
        "requestedModels": [requested["id"]],
        "routedModels": [effective_id],
        "effectiveModels": [effective_id],
        # The document runners never see a provider-reported id, so the model
        # they were handed is the whole truth about what ran.
        "actualModels": [effective_id],
        "actualModel": effective_id,
        "action": "none",
        "dataBoundary": boundary,
        "modelRoutes": [
            {
                "routedModel": effective_id,
                "actualModel": effective_id,
                "dataBoundary": boundary,
            }
        ],
    }


def _privacy_sources(
    content: str,
    history: list[Message],
    blocks: tuple[ContextBlock, ...] | list[ContextBlock] | None = None,
) -> dict[str, str | list[str]]:
    sources: dict[str, str | list[str]] = {
        "current_input": content,
        "conversation_history": [message.content for message in history],
    }
    source_kinds = {
        "agent.instructions": "agent",
        "project.instructions": "project_instructions",
        "project.design": "project_design",
        "attachment": "attachments",
        "project.knowledge": "project_knowledge",
        "memory": "memory",
    }
    for block in blocks or []:
        source = (
            "skills"
            if block.source.startswith("skill:")
            else source_kinds.get(block.source, block.source)
        )
        existing = sources.get(source)
        if existing is None:
            sources[source] = [block.text]
        elif isinstance(existing, list):
            existing.append(block.text)
    return sources


def _privacy_response(
    *,
    rows: list[governance.Finding],
    requested: list[dict],
    safe: list[dict],
    token: str,
    allow_raw: bool,
) -> JSONResponse:
    actions = ["mask_external"]
    if safe:
        actions.insert(0, "route_strict_local")
    if allow_raw:
        actions.append("send_raw_external")
    actions.append("edit")
    actions.append("cancel")
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "code": "privacy_decision_required",
            "findings": [row.wire() for row in rows],
            "requestedModels": [model["id"] for model in requested],
            "safeModels": [{"id": model["id"], "label": model["label"]} for model in safe],
            "allowedActions": actions,
            "decisionToken": token,
            "detectorVersion": governance.DETECTOR_VERSION,
            "policyVersion": governance.POLICY_VERSION,
        },
    )


async def _resolve_privacy(
    *,
    user: User,
    session: ChatSession,
    policy,
    catalogue: list[dict],
    requested: list[dict],
    sources: dict[str, str | list[str]],
    explicit_action: str | None,
    decision_token: str | None,
) -> _PrivacyResolution | JSONResponse:
    """Makes one atomic egress decision before persistence, billing or upstream.

    Only explicit proxy metadata can create a safe candidate. Missing and
    unknown boundaries remain external for policy purposes.
    """
    rows = (
        governance.findings(sources, legacy=policy.pii_masking)
        if (policy.external_data_guard or policy.pii_masking)
        else []
    )
    requested_ids = [model["id"] for model in requested]
    digest = governance.envelope_digest(sources)

    allowed_models = set(user.allowed_models or [])
    safe: list[dict] = []
    safe_ids = set(policy.privacy_safe_model_ids or [])
    # Priority is the visible live catalogue order. The admin screen stores in
    # that same order, so mouse and keyboard selection cannot create a hidden
    # click-order priority that differs from what the screen shows.
    for model in catalogue:
        model_id = model.get("id")
        if (
            model_id in safe_ids
            and _strict_model(model)
            and "chat" in model.get("kinds", [])
            and (not allowed_models or model_id in allowed_models)
        ):
            safe.append(model)

    action = "none"
    effective = requested
    mask_outbound = False
    all_strict = all(_strict_model(model) for model in requested)
    allow_raw = policy.allow_user_raw_external and not policy.pii_masking

    if rows and policy.pii_masking:
        # The legacy organisation-wide setting is always the strongest rule.
        action = "mask_external"
        mask_outbound = True
    elif rows and all_strict:
        action = "strict_local"
    elif rows and policy.external_data_guard:
        preference = Preferences.of(user).privacy_default_action
        chosen = explicit_action or preference
        if chosen == "send_raw_external" and not allow_raw:
            chosen = "ask"
        if chosen == "route_strict_local" and not safe:
            chosen = "ask"

        # A request-supplied choice is a retry and must match the exact user,
        # session, models and envelope that produced the warning.
        if explicit_action and not governance.verify_decision_token(
            decision_token,
            user_id=user.id,
            session_id=session.id,
            requested_models=requested_ids,
            digest=digest,
        ):
            chosen = "ask"

        if chosen == "route_strict_local":
            action = chosen
            # A comparison collapses to one safe call rather than silently
            # comparing a local model with external columns.
            effective = [safe[0]]
        elif chosen == "mask_external":
            action = chosen
            mask_outbound = True
        elif chosen == "send_raw_external" and allow_raw:
            action = chosen
        else:
            token = governance.issue_decision_token(
                user_id=user.id,
                session_id=session.id,
                requested_models=requested_ids,
                digest=digest,
            )
            return _privacy_response(
                rows=rows,
                requested=requested,
                safe=safe,
                token=token,
                allow_raw=allow_raw,
            )

    routing = {
        "requestedModels": requested_ids,
        "routedModels": [model["id"] for model in effective],
        "effectiveModels": [model["id"] for model in effective],
        "actualModels": [],
        "action": action,
        "dataBoundary": _combined_boundary(effective),
        "modelRoutes": [
            {
                "routedModel": model["id"],
                "actualModel": None,
                "dataBoundary": model.get("dataBoundary") or "unknown",
            }
            for model in effective
        ],
        "detectorVersion": governance.DETECTOR_VERSION,
        "policyVersion": governance.POLICY_VERSION,
        "findingCounts": [row.wire() for row in rows],
        "compareCollapsed": len(requested) > 1 and len(effective) == 1,
    }
    return _PrivacyResolution(effective, action, rows, routing, mask_outbound)


def _decision_audit_metadata(response: JSONResponse, session_id: str) -> dict[str, Any]:
    """Extracts only the value-free fields from a privacy 409 contract."""
    contract = json.loads(response.body)
    return {
        "sessionId": session_id,
        "policyVersion": contract["policyVersion"],
        "detectorVersion": contract["detectorVersion"],
        "findings": contract["findings"],
        "requestedModels": contract["requestedModels"],
        "action": "decision_required",
    }


def _mask_list(values: list[str], *, legacy: bool = False) -> list[str]:
    masker = governance.mask_legacy if legacy else governance.mask
    return [masker(value)[0] for value in values]


def _mask_text_tree(value: Any, masker) -> Any:
    """Returns a detached tree with every persisted string sanitized."""
    if isinstance(value, str):
        return masker(value)[0]
    if isinstance(value, dict):
        return {key: _mask_text_tree(item, masker) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_text_tree(item, masker) for item in value]
    if isinstance(value, tuple):
        return tuple(_mask_text_tree(item, masker) for item in value)
    return value


async def _require_egress_policy():
    """Loads the authoritative policy or refuses before any other egress work."""
    try:
        return await governance.current_for_egress()
    except governance.GovernanceUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="governance_unavailable",
        ) from None


async def _owned(db: DbSession, user: User, session_id: str) -> ChatSession:
    session = await db.get(ChatSession, session_id)
    # Same answer for "missing" and "someone else's".
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session_not_found")
    return session


async def _history(db: DbSession, session_id: str) -> list[Message]:
    result = await db.exec(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(col(Message.created_at), col(Message.id))
    )
    return list(result.all())


def _raise_workspace_error(exc: WorkspaceContextError) -> None:
    code = str(exc)
    missing = code in {
        "agent_not_found",
        "project_not_found",
        "attachment_not_found",
        "starting_template_not_found",
    }
    raise HTTPException(
        status_code=(
            status.HTTP_404_NOT_FOUND
            if missing
            # 422 is stable across Starlette releases; the named constant was
            # renamed from ENTITY to CONTENT and each side warns or breaks on
            # a different supported version.
            else 422
        ),
        detail=code,
    ) from exc


async def _validate_session_links(
    db: DbSession,
    user: User,
    kind: SessionKind,
    *,
    project_id: str | None,
    agent_id: str | None,
) -> None:
    if project_id:
        project = await db.get(Project, project_id)
        if project is None or project.user_id != user.id:
            raise HTTPException(status_code=404, detail="project_not_found")
    if agent_id:
        agent = await db.get(WorkspaceAgent, agent_id)
        allowed = agent is not None and (
            agent.owner_id == user.id or agent.visibility == AgentVisibility.org
        )
        if not allowed:
            raise HTTPException(status_code=404, detail="agent_not_found")
        if not agent.enabled:
            raise HTTPException(status_code=422, detail="agent_disabled")
        if agent.kinds and kind.value not in agent.kinds:
            raise HTTPException(status_code=422, detail="agent_kind_mismatch")


def _skill_step(event: dict | None) -> dict | None:
    if not event:
        return None
    names = [str(skill.get("name") or "") for skill in event.get("skills") or []]
    return {
        "id": "skills-applied",
        "type": "thinking",
        "label": f"스킬 {len(names)}개 적용",
        "status": "done",
        # Keep the structured contract in message JSONB as well as the SSE.
        # The timeline uses label/detail today; audit or future clients do not
        # have to parse those display strings to recover what ran.
        "skills": list(event.get("skills") or []),
        "estimatedTokens": int(event.get("estimatedTokens") or 0),
        "detail": (" · ".join(names) + f" · 약 {int(event.get('estimatedTokens') or 0):,} 토큰"),
    }


#: Each says what one file gave up; a per-file line is the point, because
#: "3개 중 1개" does not tell anybody which document the answer was missing.
_FILE_NOTE = {
    "truncated": "{name} {kept:,}자만 반영",
    "omitted": "{name} 분량을 넘겨 제외",
    "unreadable": "{name} 읽지 못함",
    # A picture this model cannot look at. Not the same as unreadable: the file
    # is fine and another model would see it, which is the part somebody can
    # act on.
    "picture_unseen": "{name} 그림 · 이 모델은 보지 못함",
}

#: Enough names to recognise the turn by, before the line stops being a line.
_NAMES_SHOWN = 6


def _named(notes: list[str], unit: str) -> str:
    """A detail line that names what it can and counts the rest."""
    shown = notes[:_NAMES_SHOWN]
    line = " · ".join(shown)
    if len(notes) > len(shown):
        line += f" 외 {len(notes) - len(shown)}{unit}"
    return line


def _file_context_step(step_id: str, subject: str, files: tuple[ContextFile, ...]) -> dict | None:
    if not files:
        return None
    # A picture that was looked at gave nothing up, so it is not a shortfall —
    # the same standing as a document that arrived whole.
    whole = {"included", "picture"}
    short = [file for file in files if file.state not in whole]
    # Cut and dropped are counted apart: half a document and no document are
    # different things to have been answered without.
    cut = sum(1 for file in short if file.state == "truncated")
    dropped = len(short) - cut
    fates = [f"{cut}개 잘림"] if cut else []
    if dropped:
        fates.append(f"{dropped}개 빠짐")
    label = (
        f"{subject} {len(files)}개 중 " + ", ".join(fates)
        if fates
        else f"{subject} {len(files)}개 반영"
    )
    notes = [_FILE_NOTE[file.state].format(name=file.name, kept=file.kept_chars) for file in short]
    detail = _named(notes or [file.name for file in files], "개")
    return {
        "id": step_id,
        "type": "thinking",
        "label": label,
        "status": "done",
        "detail": detail,
        # Structured beside the display strings, as the skill step keeps its
        # skills: an audit should not have to parse Korean to see how much of
        # a document an answer was built on.
        "files": [
            {
                "name": file.name,
                "state": file.state,
                "keptChars": file.kept_chars,
                "totalChars": file.total_chars,
            }
            for file in files
        ],
    }


def _memory_context_step(workspace: WorkspaceContext) -> dict | None:
    names = list(workspace.loaded_memories)
    if not names:
        return None
    detail = _named(names, "건")
    if workspace.total_memories > len(names):
        detail += f" · 저장된 {workspace.total_memories}건 중 최근 {len(names)}건"
    return {
        "id": "context-memories",
        "type": "thinking",
        "label": f"메모리 {len(names)}건 참고",
        "status": "done",
        "detail": detail,
        # Names, never bodies: this line is on screen while somebody presents.
        "memories": names,
        # The client rewrites this line in the reader's language, so it needs the
        # number rather than the Korean sentence.
        "totalMemories": workspace.total_memories,
    }


def _context_steps(workspace: WorkspaceContext) -> list[dict]:
    """What the turn was handed but never said out loud.

    Memories, attachments and project knowledge reach the model without passing
    through the conversation. Each becomes one timeline line, including when a
    document was truncated to fit.
    """
    steps = [
        _memory_context_step(workspace),
        _file_context_step("context-attachments", "첨부", workspace.attachments),
        _file_context_step("context-knowledge", "프로젝트 지식", workspace.knowledge),
    ]
    return [step for step in steps if step]


def _memory_saved_step(written: int) -> dict:
    return {
        "id": "memory-saved",
        "type": "thinking",
        "label": f"메모리 {written}건 저장",
        "status": "done",
        "detail": "자동 메모리에 추가됨",
        "memoriesWritten": written,
    }


def _step_event(step: dict) -> dict:
    """One stored step, addressed for the wire.

    A stored step spends `type` on its display category; a stream event spends
    it on the event name, so the category rides alongside.
    """
    return {**step, "type": "step", "category": step["type"]}


def _prelude_steps(skills_event: dict | None, context_steps: list[dict] | None) -> list[dict]:
    """The steps a turn opens with: what it was given, before it did anything."""
    applied = _skill_step(skills_event)
    return ([applied] if applied else []) + list(context_steps or [])


async def _owned_attachments(
    db: DbSession, user: User, attachment_ids: list[str] | None
) -> tuple[list[StoredFile], list[dict] | None]:
    """Resolves every requested upload without silently dropping foreign ids."""
    if not attachment_ids:
        return [], None
    ids = list(dict.fromkeys(attachment_ids))
    found = (
        await db.exec(
            select(StoredFile).where(
                col(StoredFile.id).in_(ids),
                StoredFile.user_id == user.id,
            )
        )
    ).all()
    by_id = {row.id: row for row in found}
    if len(by_id) != len(ids):
        # Same response for a missing id and another user's id.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="attachment_not_found",
        )
    rows = [by_id[file_id] for file_id in ids]
    metadata = [
        {
            "id": row.id,
            "name": row.name,
            "size": row.size,
            "type": row.mime,
            "error": row.error,
        }
        for row in rows
    ]
    return rows, metadata


def _worth_listing(rows: list[ChatSession], empty: set[str]) -> list[ChatSession]:
    """Conversations that happened, plus anything still on its way.

    Opening 새 보고서 and changing your mind used to leave a row behind, and
    the row said 새 작업 and led to a blank screen. Four hundred of them
    accumulated on this instance, which is what somebody's work list looks like
    after a fortnight of trying things: a column of identical labels, none of
    which is the thing they are looking for.

    An empty conversation is only hidden once it is a minute old and holds
    nothing. The minute is for the turn in flight — a session is written before
    the first message is — and the artifact check is for the surfaces where the
    answer is a file rather than a sentence.
    """
    fresh = utcnow() - timedelta(minutes=1)
    return [
        row
        for row in rows
        if row.id not in empty or row.artifact_id or row.pinned or row.created_at > fresh
    ]


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    user: CurrentUser,
    db: DbSession,
    kind: SessionKind | None = None,
    project_id: str | None = None,
):
    query = select(ChatSession).where(ChatSession.user_id == user.id)
    if kind is not None:
        query = query.where(ChatSession.kind == kind)
    if project_id is not None:
        query = query.where(ChatSession.project_id == project_id)
    # Sidebar order: pinned first, then most recently touched.
    query = query.order_by(col(ChatSession.pinned).desc(), col(ChatSession.updated_at).desc())
    rows = (await db.exec(query)).all()

    # One aggregate for the page — the sidebar asks for every conversation.
    ids = [s.id for s in rows]
    previews = await _previews(db, ids)
    # `_previews` is absent for a conversation with no messages, which is the
    # same question `_worth_listing` asks — so it is asked once.
    rows = _worth_listing(rows, {sid for sid in ids if sid not in previews})
    ids = [s.id for s in rows]
    # Empty body, not a missing row: a media answer holds the artifact and
    # quotes nothing.
    made = await _made(db, [sid for sid in ids if not previews.get(sid, (None, 0))[0]])
    return [
        SessionOut.of(
            s,
            preview=previews.get(s.id, (None, 0))[0],
            message_count=previews.get(s.id, (None, 0))[1],
            made=made.get(s.id),
        )
        for s in rows
    ]


async def _previews(db: DbSession, session_ids: list[str]) -> dict[str, tuple[str | None, int]]:
    """`{session_id: (latest message snippet, message count)}`.

    Absent for a conversation with no messages at all, which is what tells the
    caller to look at what the session produced instead.
    """
    if not session_ids:
        return {}
    counts = dict(
        (
            await db.exec(
                select(Message.session_id, func.count())
                .where(col(Message.session_id).in_(session_ids))
                .group_by(col(Message.session_id))
            )
        ).all()
    )
    # DISTINCT ON: newest row per conversation, no per-row subquery. Postgres-only.
    latest = (
        await db.exec(
            select(Message.session_id, Message.content)
            .where(col(Message.session_id).in_(session_ids))
            .order_by(col(Message.session_id), col(Message.created_at).desc())
            .distinct(col(Message.session_id))
        )
    ).all()
    return {sid: (snippet(content), counts.get(sid, 0)) for sid, content in latest}


async def _made(db: DbSession, session_ids: list[str]) -> dict[str, SessionMade]:
    """`{session_id: what it produced}`, for the conversations that said nothing.

    A picture or clip answers with a thing, so there is no last message to put
    under the title. Its shape is the one fact the prompt-as-title lacks.

    One query for the page, and only for ids the message query left empty.
    """
    if not session_ids:
        return {}
    rows = (
        await db.exec(
            select(Artifact.session_id, Artifact.kind, Artifact.data)
            .where(col(Artifact.session_id).in_(session_ids))
            .order_by(col(Artifact.session_id), col(Artifact.created_at).desc())
        )
    ).all()
    by_session: dict[str, list[tuple[str, dict | None]]] = {}
    for session_id, kind, data in rows:
        if session_id is not None:
            by_session.setdefault(session_id, []).append((str(kind), data))
    summarised = {sid: made_from_artifacts(made) for sid, made in by_session.items()}
    return {sid: made for sid, made in summarised.items() if made is not None}


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: str, user: CurrentUser, db: DbSession):
    session = await _owned(db, user, session_id)
    history = await _history(db, session_id)
    # Also on the single-session response — opening a conversation replaces
    # the row the list handed over.
    made = {} if any(m.content for m in history) else await _made(db, [session_id])
    return SessionOut.of(session, history, made=made.get(session_id))


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(payload: SessionCreate, user: CurrentUser, db: DbSession):
    # Server-side refusal, not just a hidden menu entry.
    if payload.kind.value not in await settings_store.enabled_kinds():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="이 기능은 사용할 수 없습니다."
        )
    await _validate_session_links(
        db,
        user,
        payload.kind,
        project_id=payload.project_id,
        agent_id=payload.agent_id,
    )
    if payload.routing_mode in _AUTO_MODES and payload.kind is not SessionKind.chat:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="auto_routing_chat_only",
        )
    if payload.model == "auto":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="auto_is_not_a_model_id",
        )
    if payload.routing_mode in _AUTO_MODES:
        await _require_auto_quality_model(user, payload.model)
    session = ChatSession(
        user_id=user.id,
        kind=payload.kind,
        project_id=payload.project_id,
        agent_id=payload.agent_id,
        model=payload.model or "",
        routing_mode=payload.routing_mode,
        render_template_id=await _project_render_template(db, payload.project_id, payload.kind),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionOut.of(session, [])


async def _template_skills(
    db: AsyncSession,
    template: design_templates.DesignTemplate | None,
    kind: SessionKind,
    skills_event: dict | None,
) -> tuple[list[str], dict | None]:
    """`(context blocks, skills_applied event)` with the 서식's skills joined.

    Catalogue rows only, found by `catalog_key`: a 서식 ships with the product
    and can only promise procedures that ship with it. A key nobody seeded is
    skipped rather than an error — the document still generates, in the shape
    the 서식 gives it, minus a rule it named.

    The event is extended rather than replaced so a hand-activated skill and a
    서식's own appear in the one list the screen already draws.
    """
    if template is None or not template.skills:
        return [], skills_event
    rows = (
        await db.exec(
            select(Skill)
            .where(
                col(Skill.catalog_key).in_(list(template.skills)),
                Skill.source == SkillSource.built_in,
                Skill.visibility == SkillVisibility.org,
            )
            .order_by(col(Skill.id))
        )
    ).all()
    by_key = {row.catalog_key: row for row in rows}
    blocks: list[str] = []
    applied: list[dict] = []
    for key in template.skills:
        skill = by_key.get(key)
        if skill is None or (skill.kinds and kind.value not in skill.kinds):
            continue
        head = f"# 서식 스킬 — {skill.name}"
        body = skill.body.strip() or skill.description.strip()
        blocks.append(f"{head}\n{body}" if body else head)
        applied.append(
            {
                "id": skill.id,
                "name": skill.name,
                "catalogKey": skill.catalog_key,
                "estimatedTokens": skill.estimated_tokens,
                # The screen can say where it came from: 서식이 켠 스킬.
                "fromTemplate": True,
            }
        )
    if not applied:
        return blocks, skills_event
    event = dict(skills_event or {"type": "skills_applied", "skills": []})
    event["skills"] = list(event.get("skills") or []) + applied
    # The total travels with the merged list. Without agent skills the seed
    # event above has no total at all, and the browser read the missing key
    # off a template-only turn and took the whole stream down with it.
    event["estimatedTokens"] = sum(
        int(skill.get("estimatedTokens") or 0) for skill in event["skills"]
    )
    return blocks, event


async def _project_render_template(
    db: DbSession, project_id: str | None, kind: SessionKind
) -> str | None:
    """The format the project this session starts in works in, if any.

    Copied onto the row: a project changing its default must not change the
    shape of a conversation already under way.

    Ownership is settled by `_validate_session_links` before this is asked.
    """
    if not project_id:
        return None
    project = await db.get(Project, project_id)
    return design_templates.default_for(project.render_templates, kind) if project else None


def _resolved_template_id(requested: str | None, kind: SessionKind) -> str | None:
    """A rendering template id this surface can use, or `None`.

    `""` clears the choice, `None` means the payload did not mention it; only
    the caller can tell those apart. An unresolvable id is refused rather than
    dropped — a silent fallback bills for a document in the wrong shape.
    """
    if not requested:
        return None
    chosen = design_templates.get(requested)
    if chosen is None or chosen.kind not in design_templates.HTML_KINDS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="design_template_not_found"
        )
    if chosen.surface is not kind:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="design_template_surface_mismatch",
        )
    return chosen.id


@router.patch("/{session_id}", response_model=SessionOut)
async def patch_session(session_id: str, payload: SessionPatch, user: CurrentUser, db: DbSession):
    session = await _owned(db, user, session_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("model") == "auto":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="auto_is_not_a_model_id",
        )
    if changes.get("routing_mode") in _AUTO_MODES and session.kind is not SessionKind.chat:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="auto_routing_chat_only",
        )
    # A direct real-model selection is manual unless the caller explicitly
    # updates the quality ceiling and asks to keep Auto in the same patch.
    if "model" in changes and "routing_mode" not in changes:
        changes["routing_mode"] = RoutingMode.manual
    # Validate the effective post-patch state, including an unrelated update to
    # a session that is already Auto. A model-only patch becomes manual above.
    if changes.get("routing_mode", session.routing_mode) in _AUTO_MODES:
        await _require_auto_quality_model(user, changes.get("model", session.model))
    if "render_template_id" in changes:
        changes["render_template_id"] = _resolved_template_id(
            changes["render_template_id"], session.kind
        )
    if "project_id" in changes:
        await _validate_session_links(
            db,
            user,
            session.kind,
            project_id=changes["project_id"],
            agent_id=session.agent_id,
        )
    for field, value in changes.items():
        setattr(session, field, value)
    session.updated_at = utcnow()
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionOut.of(session)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str, user: CurrentUser, db: DbSession):
    session = await _owned(db, user, session_id)
    await db.exec(delete(Message).where(Message.session_id == session.id))
    # A clip's job row references the session and nothing cascades it, so a
    # conversation that made one cannot be deleted until it goes. The clip
    # itself survives: the artifact is detached, not deleted, and the ledger
    # keeps the charge. What the job holds is progress and a way to retry,
    # neither of which means anything without the conversation to sit in.
    await db.exec(delete(Job).where(Job.session_id == session.id))
    await db.exec(update(Artifact).where(Artifact.session_id == session.id).values(session_id=None))
    await db.delete(session)
    await db.commit()


def _record_media(
    db: DbSession,
    session: ChatSession,
    prompt: str,
    made: list[Artifact],
    *,
    model: str = "",
    credits: int = 0,
    failed: bool = False,
) -> None:
    """Writes the turn a picture or a clip is, as an ordinary turn.

    The prompt is a user message like any other. The reply is an assistant
    message with no words in it, carrying the ids of what was made — a picture
    is not a sentence, and prose invented about one would be the model quoted
    saying something it never said.

    Nothing made marks the prompt and leaves no reply, as a chat turn does when
    it dies before its first word. A batch that broke halfway keeps what arrived
    and says it is less than what was asked for.
    """
    if not session.title:
        # A title, once set, stays. A second batch is more of the same work, not
        # a new subject.
        session.title = chat_service.provisional_title(prompt)
    db.add(chat_service.media_prompt(session.id, prompt, unanswered=failed and not made))
    if made:
        db.add(
            chat_service.media_answer(
                session.id,
                [artifact.id for artifact in made],
                model=model,
                credits=credits,
                partial=failed,
            )
        )
        # Newest result, for the panel and 원본 작업 열기. Per-batch results live
        # on the messages.
        #
        # Except where the session already has a document of its own. On the
        # image and audio surfaces the newest thing made *is* the document, and
        # on a chat there is nothing else for the pointer to mean. A report or a
        # slides session is different: `artifact_id` is the report or the deck,
        # and it is what the panel opens, what 원본 작업 열기 opens, and what
        # `_revise_document` reads when somebody types "슬라이드 2 다시 써 줘".
        #
        # That last one is how this was found. Pictures could only be made on
        # the image surface until the document pickers learned to make their
        # own, and the first one made from inside a deck moved the deck's
        # pointer onto the picture. Every instruction typed afterwards was read
        # against an image artifact, which has neither slides nor sections, and
        # answered "고칠 내용이 없습니다" — about a deck of eleven slides.
        if session.kind not in (SessionKind.report, SessionKind.slides):
            session.artifact_id = made[-1].id
    # The sidebar sorts on this. Making something is the clearest case there is
    # of the conversation having been touched.
    session.updated_at = utcnow()
    db.add(session)


@router.post("/{session_id}/figure-suggestion", response_model=FigureSuggestion)
async def suggest_figure(
    session_id: str, payload: FigureSuggestRequest, user: CurrentUser, db: DbSession
):
    """What picture to put here — proposed, not asked for.

    The picker opened on an empty box with the 장's name in the placeholder,
    which asks somebody who wanted a picture to first become somebody who can
    describe one. This fills it in. Nothing is drawn and nothing is charged:
    the answer is two lines of text the person then edits or throws away, and
    the credit is only spent by pressing 만들기.
    """
    session = await _owned(db, user, session_id)
    catalogue = await model_service.list_models()
    model = model_service.find(catalogue["models"], session.model or "")
    if model is None or "chat" not in model.get("kinds", []):
        usable = sorted(
            (m for m in catalogue["models"] if "chat" in m.get("kinds", [])),
            key=model_service.fallback_order,
        )
        if not usable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_chat_models"
            )
        model = usable[0]
    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)
    figure = await figures.suggest(
        title=payload.title or session.title or "",
        about=payload.about,
        context=payload.context,
        model=str(model["id"]),
        api_key=api_key,
    )
    # A suggestion that did not come back is not an error the person can act
    # on — the box simply stays theirs to fill, exactly as it was before.
    if figure is None:
        return FigureSuggestion(caption="", prompt="")
    return FigureSuggestion(caption=figure.caption, prompt=figure.prompt)


@router.post("/{session_id}/images", response_model=list[ArtifactOut])
async def generate_images(session_id: str, payload: ImageRequest, user: CurrentUser, db: DbSession):
    """Makes pictures and stores them as artifacts.

    Synchronous, one image per upstream call. Charged from reported usage, not
    an estimate: prices across these models span two orders of magnitude.
    """
    session = await _owned(db, user, session_id)
    catalogue = await model_service.list_models()
    model = model_service.find(catalogue["models"], payload.model or session.model or "")
    if model is None or "image" not in model["kinds"]:
        usable = sorted(
            (m for m in catalogue["models"] if "image" in m["kinds"]),
            key=model_service.fallback_order,
        )
        if not usable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_image_models"
            )
        model = usable[0]
    if not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )

    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    base_url, api_key = await litellm_service.credentials_for(user)
    picture_template = design_templates.get(payload.template_id)
    if payload.template_id and (picture_template is None or picture_template.kind != "image"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="design_template_not_found"
        )
    composed = imagegen.compose_prompt(
        payload.prompt,
        aspect=payload.aspect,
        style=payload.style,
        template=picture_template.prompt_suffix if picture_template else "",
        design=design_service.image_clause(await design_for(db, user, session)),
        figure=payload.figure,
    )

    made: list[Artifact] = []
    charged = 0
    failure: str | None = None
    for _ in range(payload.count):
        try:
            image = await imagegen.generate(
                base_url=base_url, api_key=api_key, model=model["id"], prompt=composed
            )
        except imagegen.ImageError as exc:
            # Images produced before the failure are kept and billed — upstream
            # charged for them.
            failure = str(exc)
            break

        file_id, key = imagegen.store(user.id, image)
        db.add(
            StoredFile(
                id=file_id,
                user_id=user.id,
                session_id=session.id,
                name=f"{payload.prompt[:40] or 'image'}.png",
                mime=image.mime,
                size=len(image.data),
                storage_key=key,
                tokens=0,
            )
        )
        artifact = Artifact(
            user_id=user.id,
            session_id=session.id,
            project_id=session.project_id,
            kind=ArtifactKind.image,
            title=payload.prompt[:200] or "이미지",
            data={
                "kind": "image",
                "jobId": None,
                # Prompt as typed, without the appended aspect and style phrases.
                "prompt": payload.prompt,
                "aspect": payload.aspect,
                # Requested ratio beside the delivered one: the ratio is a phrase in the
                # prompt, not a parameter, and the two often disagree.
                "actualAspect": image.aspect,
                "width": image.width,
                "height": image.height,
                "style": payload.style,
                "seed": 0,
                "model": model["id"],
                "src": f"{settings.api_prefix}/files/{file_id}/content",
            },
        )
        db.add(artifact)
        made.append(artifact)
        charged += charge_for_tokens(model, image.input_tokens, image.output_tokens)

    if charged:
        settle(
            db,
            user,
            charged,
            reason="image.generate",
            session_id=session.id,
            model=model["id"],
            surface=session.kind.value,
        )
    _record_media(
        db,
        session,
        payload.prompt,
        made,
        model=model["id"],
        credits=charged,
        failed=failure is not None,
    )
    await db.commit()
    for artifact in made:
        await db.refresh(artifact)

    if not made:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=failure or "image_failed"
        )
    return [ArtifactOut.of(a) for a in made]


@router.post("/{session_id}/diagrams", response_model=DiagramOut)
async def write_diagram(session_id: str, payload: DiagramRequest, user: CurrentUser, db: DbSession):
    """Writes a labelled figure as mermaid from a description of the method.

    The image path draws shapes and cannot spell; a figure for a paper needs
    its labels. This asks a language model for the *diagram* — nodes, zones,
    arrows, each named — in the house style, and the client renders it. What
    is returned is source, which is what gets stored: a picture is derived
    from it and can be derived again after an edit.
    """
    session = await _owned(db, user, session_id)
    catalogue = await model_service.list_models()
    usable = sorted(
        (m for m in catalogue["models"] if "chat" in m["kinds"]),
        key=model_service.fallback_order,
    )
    # 글을 쓰는 모델이어야 한다. This is an image session, so `session.model`
    # is the picture model — handed to chat/completions it answers 400. Take
    # the asked-for model only if it can write; otherwise the cheapest that can.
    asked = model_service.find(catalogue["models"], payload.model or session.model or "")
    model = asked if asked and "chat" in asked["kinds"] else (usable[0] if usable else None)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
        )
    if not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )
    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)

    try:
        source, caption, usage = await diagram_service.draw(
            description=payload.description,
            figure=payload.figure,
            model=model["id"],
            api_key=api_key,
            language=payload.language,
            broken=payload.broken,
            error=payload.error,
        )
    except Exception as exc:  # noqa: BLE001 — the caller gets a reason, not a 500
        log.warning("diagram failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="diagram_failed"
        ) from exc

    charged = charge_for_tokens(
        model, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    )
    if charged:
        settle(
            db,
            user,
            charged,
            reason="image.diagram",
            session_id=session.id,
            model=model["id"],
            surface=session.kind.value,
        )
        await db.commit()
    return DiagramOut(source=source, caption=caption, model=model["id"], credits=charged)


@router.post("/{session_id}/diagrams/store", response_model=ArtifactOut)
async def store_diagram_image(
    session_id: str, payload: DiagramStore, user: CurrentUser, db: DbSession
):
    """Keeps a rendered figure as an image artifact, with its source beside it.

    The client drew the mermaid and rasterised it; the server has no browser
    and no fonts, and a figure has to be drawn in the face it will be printed
    in. The PNG is what exports and galleries show; the source is what makes
    it a figure rather than a screenshot of one.
    """
    session = await _owned(db, user, session_id)
    try:
        blob = base64.b64decode(payload.png, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="bad_png"
        ) from exc
    if len(blob) > 8_000_000:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="png_too_large"
        )
    file_id = uuid.uuid4().hex
    key = file_service.write_blob(user.id, file_id, "figure.png", blob)
    title = (payload.title or payload.caption or "도식")[:200]
    db.add(
        StoredFile(
            id=file_id,
            user_id=user.id,
            session_id=session.id,
            name=f"{title[:40]}.png",
            mime="image/png",
            size=len(blob),
            storage_key=key,
            tokens=0,
        )
    )
    artifact = Artifact(
        user_id=user.id,
        session_id=session.id,
        project_id=session.project_id,
        kind=ArtifactKind.image,
        title=title,
        data={
            "kind": "image",
            "jobId": None,
            "prompt": payload.description,
            "aspect": "16:9",
            "actualAspect": f"{payload.width}:{payload.height}",
            "width": payload.width,
            "height": payload.height,
            "style": "figure",
            "seed": 0,
            "model": payload.model,
            "src": f"{settings.api_prefix}/files/{file_id}/content",
            # 그림이 아니라 도식이다. The source is the artifact; the PNG is
            # one rendering of it.
            "figure": payload.figure,
            "source": payload.source,
            "caption": payload.caption,
        },
    )
    db.add(artifact)
    _record_media(db, session, payload.description, [artifact], model=payload.model, credits=0)
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.post("/{session_id}/audio", response_model=ArtifactOut)
async def generate_audio(session_id: str, payload: AudioRequest, user: CurrentUser, db: DbSession):
    """Makes one sound clip and stores it as an artifact.

    Speech and music are separate model families, selected by the requested
    kind. No sound-effect option — nothing serves them.
    """
    session = await _owned(db, user, session_id)
    speech = payload.audio_kind == "narration"
    catalogue = await model_service.list_models()

    def _audio_models():
        return [m for m in catalogue["models"] if "av" in m["kinds"]]

    model = model_service.find(catalogue["models"], payload.model or "")
    # Model must match the requested kind, not merely the surface. A mismatch
    # is a proxy 400.
    wanted_id = "gpt-audio" if speech else "lyria"
    if model is None or "av" not in model["kinds"] or wanted_id not in model["id"]:
        # Speech: OpenAI audio. Music: Lyria. Selected by id.
        candidates = [m for m in _audio_models() if wanted_id in m["id"]]
        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_audio_models"
            )
        model = candidates[0]
    if not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )

    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    base_url, api_key = await litellm_service.credentials_for(user)

    try:
        audio = await audiogen.generate(
            base_url=base_url,
            api_key=api_key,
            model=model["id"],
            prompt=payload.prompt,
            speech=speech,
            voice=payload.voice,
            seconds=payload.seconds,
        )
    except audiogen.AudioError as exc:
        # The request is kept even when nothing came of it — otherwise a refusal
        # leaves a blank screen and no trace but an unbilled credits line.
        _record_media(db, session, payload.prompt, [], failed=True)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    file_id, key = audiogen.store(user.id, audio)
    db.add(
        StoredFile(
            id=file_id,
            user_id=user.id,
            session_id=session.id,
            name=f"{payload.prompt[:40] or 'audio'}.{audio.extension}",
            mime=audio.mime,
            size=len(audio.data),
            storage_key=key,
            tokens=0,
        )
    )
    artifact = Artifact(
        user_id=user.id,
        session_id=session.id,
        project_id=session.project_id,
        kind=ArtifactKind.audio,
        title=payload.prompt[:200] or "오디오",
        data={
            "kind": "audio",
            "jobId": None,
            "prompt": payload.prompt,
            "audioKind": payload.audio_kind,
            "voice": payload.voice if speech else "",
            # Requested length beside the delivered one — a length is a phrase in the
            # prompt, not a parameter. Same reason as `aspect`/`actualAspect`.
            "requestedSec": payload.seconds,
            "durationSec": audiogen.duration_seconds(audio),
            "model": model["id"],
            "transcript": audio.transcript,
            # Flat placeholder waveform; the real one would mean decoding the clip.
            "waveform": [],
            "src": f"{settings.api_prefix}/files/{file_id}/content",
        },
    )
    db.add(artifact)

    # Lyria: flat $0.04 per song, four reported tokens. Token billing reads as free.
    charged = max(
        charge_for_tokens(model, audio.input_tokens, audio.output_tokens),
        int(model.get("creditPerCall") or 0),
    )
    if charged:
        settle(
            db,
            user,
            charged,
            reason="audio.generate",
            session_id=session.id,
            model=model["id"],
            surface=session.kind.value,
        )
    _record_media(db, session, payload.prompt, [artifact], model=model["id"], credits=charged)
    await db.commit()
    await db.refresh(artifact)
    return ArtifactOut.of(artifact)


@router.post("/delete")
async def delete_sessions(payload: SessionBulkDelete, user: CurrentUser, db: DbSession):
    """Deletes many conversations in one request.

    `all` is separate from a client-supplied id list: the caller cannot know
    what arrived since the page loaded.
    """
    query = select(ChatSession).where(ChatSession.user_id == user.id)
    if not payload.all:
        if not payload.ids:
            return {"deleted": 0}
        query = query.where(col(ChatSession.id).in_(payload.ids))
    rows = (await db.exec(query)).all()
    if not rows:
        return {"deleted": 0}

    ids = [row.id for row in rows]
    await db.exec(delete(Message).where(col(Message.session_id).in_(ids)))
    # See `delete_session`: the job row has to go or the delete is refused.
    await db.exec(delete(Job).where(col(Job.session_id).in_(ids)))

    made = (await db.exec(select(Artifact.id).where(col(Artifact.session_id).in_(ids)))).all()
    if payload.artifacts and made:
        # Asked for: the versions go first, then the rows. A shared link to one
        # dies with it — the token points at an artifact, and `shares` cascades.
        await db.exec(delete(ArtifactVersion).where(col(ArtifactVersion.artifact_id).in_(made)))
        await db.exec(delete(Artifact).where(col(Artifact.id).in_(made)))
    else:
        # Detached, not deleted: an artifact is a thing in its own right on the
        # gallery, and may be in a project or behind a link.
        await db.exec(
            update(Artifact).where(col(Artifact.session_id).in_(ids)).values(session_id=None)
        )

    await db.exec(delete(ChatSession).where(col(ChatSession.id).in_(ids)))
    await db.commit()
    return {"deleted": len(ids), "artifactsDeleted": len(made) if payload.artifacts else 0}


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(session_id: str, user: CurrentUser, db: DbSession):
    await _owned(db, user, session_id)
    return [MessageOut.of(m) for m in await _history(db, session_id)]


def _regeneration_summary(request: str) -> str:
    """What the version list says about the copy this run replaced.

    The request rather than a timestamp: a history of "재생성" repeated six times
    is a list nobody can choose from, and the sentence that produced each
    version is the only thing that tells them apart.
    """
    # The first line only: everything after it is the conditions block that
    # `merge_answers` appends, which is already reflected in the version it
    # produced and reads as noise in a list of choices.
    said = " ".join(request.split("덧붙인 조건:")[0].split())[:80]
    return f"재생성 전 · {said}" if said else "재생성 전"


async def _store_document(
    db: AsyncSession,
    session: ChatSession,
    *,
    user_id: str,
    project_id: str | None,
    kind: ArtifactKind,
    title: str,
    data: dict,
    summary: str,
) -> str:
    """Writes a generated document, keeping the one it replaces.

    Regenerating used to create a fresh artifact row and move the session's
    pointer to it. The old row survived in the gallery, so nothing was deleted
    — but from inside the conversation the previous document was simply gone,
    with no way back to it. A deck somebody had built over an afternoon could
    be displaced by one request and there was no 되돌리기 anywhere on the screen.

    Editing an artifact has always snapshotted the old body into
    `artifact_versions` first. Generating never did, although it is the more
    destructive of the two. So it does now: a document produced into a session
    that already holds one of the same kind becomes the next version of that
    artifact rather than a new one, and every version before it stays
    restorable through the history the panel already draws.

    A different kind — a report in a session that last made a deck — is a
    different thing and gets its own artifact, because a version history that
    alternates between two documents is not a history of either.
    """
    existing = await db.get(Artifact, session.artifact_id) if session.artifact_id else None
    if (
        existing is not None
        and existing.kind is kind
        and existing.user_id == user_id
        and existing.session_id == session.id
    ):
        db.add(
            ArtifactVersion(
                artifact_id=existing.id,
                version=existing.version,
                data=existing.data,
                storage_key=existing.storage_key,
                summary=summary,
            )
        )
        existing.version += 1
        existing.title = title
        existing.data = data
        existing.updated_at = utcnow()
        db.add(existing)
        return existing.id

    artifact = Artifact(
        user_id=user_id,
        session_id=session.id,
        project_id=project_id,
        kind=kind,
        title=title,
        data=data,
    )
    db.add(artifact)
    await db.flush()
    return artifact.id


async def _settle_plan_turn(
    *,
    session_id: str,
    user_id: str,
    request: str,
    attachments: list[str],
    answers: dict[str, str],
    model: dict,
    outline_model: dict | None,
    usage: dict[str, int],
    proposal: dict | None,
    questions: list[dict] | None,
    #: The second question, when there is one to ask. Present only after an
    #: outline has been approved and the planner found somewhere a picture
    #: would help — which on most work documents it does not.
    figures: dict | None = None,
) -> None:
    """Stores a turn that planned or asked, and charged for doing so.

    The planning call is a real model call whether or not a document came out
    of it, and the ledger has to say so — the old formula billed zero when
    nothing was written, which was right when nothing written meant nothing
    run, and is wrong now that a turn can stop on purpose.

    No artifact is created and `session.artifact_id` is not touched. That is
    the entire protection: whatever the session already holds stays the thing
    the session holds until somebody approves a replacement.
    """
    credits = charge_for_tokens(model, usage.get("inputTokens", 0), usage.get("outputTokens", 0))
    credits += charge_for_tokens(
        outline_model or model,
        usage.get("outlineInputTokens", 0),
        usage.get("outlineOutputTokens", 0),
    )
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        user = await db.get(User, user_id)
        if session is None or user is None:
            return
        stage = "clarify" if questions else "figures" if figures else "outline"
        session.pending = {
            "stage": stage,
            "request": request,
            "attachments": attachments,
            "answers": answers,
            **({"questions": questions} if questions else {}),
            **({"plan": proposal} if proposal else {}),
            # The outline is carried through the figure question unchanged:
            # the person already approved it, and asking again would be a
            # third card nobody agreed to look at.
            **(figures or {}),
        }
        db.add(
            Message(
                session_id=session_id,
                role=Role.assistant,
                content=(
                    "시작하기 전에 확인할 것이 있습니다."
                    if questions
                    else "그림을 넣을지 확인해 주세요."
                    if figures
                    else "이렇게 구성하려고 합니다. 확인해 주세요."
                ),
                usage={**usage, "credits": credits},
                model=model["id"],
            )
        )
        settle(
            db,
            user,
            credits,
            reason="document.plan",
            session_id=session_id,
            model=(outline_model or model)["id"],
            # 「document」 is the one reason that does not say which surface —
            # both 보고서 and 슬라이드 plan and stop. The session does.
            surface=session.kind.value,
        )
        session.updated_at = utcnow()
        db.add(session)
        await db.commit()


#: More parts than any surface writes. `deck._MAX_SLIDES` is 50 and the
#: document track stops at 24, so this refuses a payload rather than bounding a
#: real outline.
_MAX_PLANNED = 60


def _edited_plan(sent: dict | None, stored: dict) -> dict:
    """The stored outline with the person's edits folded in, where they fit.

    Titles and their order are theirs; everything else — the layouts, the
    surface's own keys — comes from the proposal. An edit that does not
    typecheck is not an error to report: the card cannot produce one, so the
    only way to see it is a client that was not this card, and the right answer
    to that is the outline everybody already looked at.
    """
    if not isinstance(sent, dict):
        return stored
    out = dict(stored)
    for key in ("sections", "slides", "blocks"):
        items = sent.get(key)
        if key not in stored or not isinstance(items, list) or not items:
            continue
        if len(items) > _MAX_PLANNED:
            continue
        # `sections` is a list of headings; `slides` and `blocks` are objects
        # carrying a layout the seed styles. The layout is never taken from the
        # browser — a name the 서식 does not style is a slide with no design.
        if all(isinstance(row, str) for row in items):
            kept = [row.strip()[:200] for row in items if row.strip()]
        else:
            layouts = {
                str(row.get("layout") or "") for row in stored.get(key, []) if isinstance(row, dict)
            }
            kept = []
            for row in items:
                if not isinstance(row, dict):
                    continue
                title = str(row.get("title") or "").strip()[:200]
                layout = str(row.get("layout") or "")
                if not title or layout not in layouts:
                    continue
                kept.append({"title": title, "layout": layout})
        if kept:
            out[key] = kept
    if title := str(sent.get("title") or "").strip():
        out["title"] = title[:200]
    visual_style = str(sent.get("visualStyle") or "").strip()
    if visual_style in ("editorial", "poster", "minimal"):
        out["visualStyle"] = visual_style
    density = str(sent.get("density") or "").strip()
    if density in ("speaker", "reading"):
        out["density"] = density
    return out


async def _ask_before_writing(
    db: DbSession,
    session: ChatSession,
    *,
    request: str,
    typed: str,
    attachments: list[str],
    questions: list[grounding.Question],
) -> JSONResponse:
    """Stops the turn on a question, and writes no document.

    The whole defence is in the last half of that sentence. A request whose
    material came up short used to produce a deck anyway — the outline prompt
    told the model in so many words not to say the material was thin — and that
    deck replaced whatever the session already had. Somebody attached a paper
    and got a presentation about presentations where their afternoon's work had
    been.

    Now the turn ends here. The question is stored on the session so a reload
    finds it, and an assistant message carries it into the transcript so the
    conversation reads as a conversation rather than as a request that vanished.

    Answered as JSON rather than as a stream: nothing is being generated, and a
    one-event SSE response would make the browser wait on a socket to be told
    that nothing is coming.
    """
    session.pending = {
        "stage": "clarify",
        "request": request,
        "attachments": attachments,
        "questions": [q.wire() for q in questions],
        "answers": {},
    }
    session.updated_at = utcnow()
    db.add(session)
    db.add(
        Message(
            session_id=session.id,
            role=Role.user,
            content=typed,
            attachments=None,
        )
    )
    said = "시작하기 전에 확인할 것이 있습니다."
    db.add(
        Message(
            session_id=session.id,
            role=Role.assistant,
            content=said,
        )
    )
    await db.commit()
    # The sentence travels with the questions. It is already stored, so a
    # reload showed it — but the turn in flight had an empty answer bubble, and
    # an empty bubble is what the panel draws 생각하는 중 into. The card was up,
    # the question was asked, and above it a spinner said the model was still
    # thinking about a turn that had already stopped.
    return JSONResponse({"pending": session.pending, "message": said})


def _plans_first(session: ChatSession) -> bool:
    """Whether this surface offers an outline before it writes.

    The two that produce a document somebody keeps. Chat has always been a
    conversation, and the media surfaces are jobs with their own endpoints —
    neither has an outline to show or an artifact to overwrite.
    """
    return session.kind in (SessionKind.report, SessionKind.slides)


@router.post("/{session_id}/messages")
async def send_message(
    session_id: str, payload: SendMessage, request: Request, user: CurrentUser, db: DbSession
):
    session = await _owned(db, user, session_id)

    #: The question being run again in place, when 다시 시도 sent one. Its stored
    #: words and attachments replace whatever the client echoed, so the turn
    #: that reran is the turn that failed.
    retry_of: Message | None = None
    if payload.retry_of:
        retry_of = await db.get(Message, payload.retry_of)
        if retry_of is None or retry_of.session_id != session.id or retry_of.role is not Role.user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="retry_target_not_found"
            )
        payload = payload.model_copy(
            update={
                "content": retry_of.content,
                "attachments": payload.attachments
                or [a["id"] for a in (retry_of.attachments or []) if a.get("id")]
                or None,
            }
        )

    # A document surface no longer writes on the first request. It stops to show
    # what it intends to write, and stops earlier still to ask when the material
    # cannot carry the request; `session.pending` is where that half-finished
    # turn waits between the two.
    #
    # So a message arriving while something is pending is a note on it — the
    # person adjusting the outline in front of them — rather than a fresh
    # document that would replace what is already there. That is the ping-pong
    # these surfaces never had: until now every sentence typed here, including
    # a question, regenerated the deck.
    pending = dict(session.pending or {}) if _plans_first(session) else {}
    #: Set only by `/continue` below, which re-enters this handler with the
    #: outline already agreed to. A plain message never carries one — typing
    #: while a proposal is up is a revision, and revising means planning again.
    approved_plan: dict | None = None
    #: Set by the figure card's two buttons. `None` until one of them is
    #: pressed, which is how the writer tells "no pictures" from "not asked".
    approved_figures: list[dict] | None = None
    #: Whether this message is somebody working on the document in front of
    #: them rather than asking for another one.
    #:
    #: `pending` is cleared the moment a document is written, so every sentence
    #: after that met a planner with nothing to plan against: "3절을 좀 더 짧게"
    #: produced a whole new report about being shorter and offered it as a
    #: proposal, waiting to replace the one on screen. The chat and the document
    #: were two windows that could not see each other, and everything the person
    #: could actually do to their document they did with a panel button and a
    #: note in a box.
    #:
    #: What it lands on is decided by `services.revise`, which reads the
    #: instruction against the document's own outline. An approval, an answer,
    #: and a plainly-worded "새로 써 줘" are all excluded here rather than left
    #: for that call to get right.
    revising = bool(
        _plans_first(session)
        and session.artifact_id
        and not pending
        and not payload.approve
        and payload.answers is None
        and not revise.obviously_new(payload.content)
    )
    focus = ""
    #: What this person typed just now, as opposed to the merged request the
    #: model is given. The transcript shows the first: nobody wrote the merge.
    typed_content = payload.content
    #: "있는 자료로 진행" — the card's second button, which sends an empty answers
    #: The outline the writer will follow: what was proposed, or what the
    #: person made of it on the card.
    #:
    #: Trusted for its words and not for its shape. The plan drives a run that
    #: costs real money and cannot be half-formed, so every key is taken from
    #: the proposal and only the strings are taken from the browser — a client
    #: that sends `sections: 4` or thirty thousand of them gets the stored plan
    #: back, which is what approving used to do in every case.
    #: object where a typed note sends none at all. Folded back into the
    #: request it changes nothing, so the planner meets the same sentence and
    #: asks the same question; the button then loops for as long as anybody
    #: keeps pressing it. This is what tells the next pass not to ask.
    proceed_as_is = bool(
        pending and not payload.approve and payload.answers is not None and not payload.answers
    )
    if pending:
        answers = {**(pending.get("answers") or {}), **(payload.answers or {})}
        pending["answers"] = answers
        focus = grounding.focus_terms(answers)
        # Approval is the one path that does not plan again. Everything else —
        # an answer, a note, a plain sentence — goes back through planning, so
        # what finally gets written is always something somebody saw first.
        if payload.approve and pending.get("plan"):
            approved_plan = _edited_plan(payload.plan, dict(pending["plan"]))
        #: The pictures somebody agreed to pay for, or `[]` when they said no.
        #:
        #: Two questions rather than one card with a checkbox on it. A figure
        #: changes the prose beside it — a section written expecting a diagram
        #: says 아래 그림과 같이 — so "with pictures" and "without" are two
        #: different documents, and asking about them together invites somebody
        #: to change the expensive half by mistake.
        if pending.get("stage") == "figures" and payload.include_figures is not None:
            approved_figures = list(pending.get("figures") or []) if payload.include_figures else []
        payload = payload.model_copy(
            update={
                "content": grounding.merge_answers(
                    str(pending.get("request") or ""),
                    # An approval is not a condition on the request. Folding
                    # "이대로 생성" in put it into the writing prompts as
                    # something the person had asked for, and into the version
                    # history as the reason the previous document was replaced.
                    answers if payload.approve else {**answers, "_note": typed_content},
                ),
                # The attachments belong to the request being revised. A reply
                # carries none of its own, and re-planning without them would
                # quietly drop the paper the whole thing is about.
                "attachments": payload.attachments or list(pending.get("attachments") or []),
            }
        )

    if session.kind not in (SessionKind.chat, SessionKind.report, SessionKind.slides):
        # Image and a/v are jobs with their own endpoints, not this path.
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="surface_not_implemented"
        )
    auto_turn = bool(
        session.kind is SessionKind.chat
        and session.routing_mode in _AUTO_MODES
        and payload.model is None
    )

    # Refused rather than ignored, and before any write: a turn that silently
    # falls back to the built-in track produces a document in the wrong shape
    # and bills for it.
    if payload.render_template_id is not None:
        session.render_template_id = _resolved_template_id(payload.render_template_id, session.kind)

    # Policy before any write, billing entry, virtual-key issue or model call.
    policy = await _require_egress_policy()
    content = payload.content
    if policy.intent_filter and (hit := governance.blocked_by(content, policy.blocked_categories)):
        await _audit_policy(user, request, "filter.blocked", hit)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"blocked_category:{hit}",
        )
    catalogue = await model_service.list_models_for_egress()
    catalogue_models = catalogue["models"]
    usable = _allowed_models(user, catalogue_models, kind=session.kind.value)
    # Model precedence: turn override → session → agent. The agent's is a
    # default, used only when the session carries none.
    try:
        agent_model, agent_tools, agent_temperature = await agent_settings(db, user, session)
        # 집 문체를 지키려면 편차를 줄여야 한다.
        #
        # A turn with no agent used to send no temperature, and the model
        # shipped at ~0.7. Same question, same rules, three runs: one in
        # paragraphs, one in a numbered list with bold labels, one with an
        # analogy about toy cars and a summary — the rules were followed
        # about half the time. At 0.4 the same rules held run after run, and
        # the answers were still written, not stiff. An agent that sets its
        # own temperature keeps it.
        if agent_temperature is None:
            agent_temperature = 0.4
    except WorkspaceContextError as exc:
        _raise_workspace_error(exc)
    model_id = session.model if auto_turn else payload.model or session.model or agent_model
    model = model_service.find(catalogue_models, model_id) if model_id else None
    if payload.model and (model is None or session.kind.value not in model.get("kinds", [])):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="model_unavailable",
        )
    allowed_ids = set(user.allowed_models or [])
    if payload.model and allowed_ids and payload.model not in allowed_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="model_not_allowed",
        )
    if auto_turn and _find_auto_quality_model(usable, model_id) is None:
        # Auto is a ceiling, not permission to replace a stale/denied quality
        # model (or classifier-only capacity) with whichever cheap model
        # happens to be first today.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="auto_quality_model_required",
        )
    # The requested model, kept even when the account may no longer use it, so
    # routing metadata can report it and the transcript can name the substitute.
    revoked_model = model if model is not None and model not in usable else None
    if model not in usable:
        model = None
    if model is None:
        # A stale session/agent id may fall back, but only inside the user's
        # allowed intersection.
        usable = sorted(usable, key=model_service.fallback_order)
        if not usable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
            )
        model = usable[0]
    requested_model = model

    history = await _history(db, session_id)
    #: What a retry replaces: the failed reply, if any, under the question. The
    #: question itself is reused, so neither may stay in the history the model
    #: sees — it would meet the same sentence twice.
    superseded: list[Message] = []
    if retry_of is not None:
        at = next((i for i, m in enumerate(history) if m.id == retry_of.id), None)
        if at is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="retry_target_not_found"
            )
        # Only the latest question runs again in place. A later question means
        # the conversation moved on, and rerunning an earlier turn would have to
        # erase everything since to keep the transcript straight.
        if any(m.role is Role.user for m in history[at + 1 :]):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="retry_not_latest")
        superseded = history[at + 1 :]
        history = history[:at]

    # Stored as id + name + readability: what the transcript renders later,
    # without a join per message. Resolve ownership before privacy assembly.
    rows, attachment_meta = await _owned_attachments(db, user, payload.attachments)

    # The running agent's own documents, loaded once for the turn. Text lives in
    # the row, so this is the whole shelf and not a handle to fetch it later —
    # the tool runs inside the stream, where there is no database session.
    stored_content = content
    outbound_history = [message.content for message in history]
    privacy_resolution: _PrivacyResolution | None = None
    tools: list[Tool] = []
    tool_definitions: list[dict[str, Any]] = []
    candidate_tools: list[Tool] = []
    strict_tools: list[Tool] = []

    # Every model-visible tool definition, built before the privacy decision and
    # the first write: shelf filenames and connector schemas are outbound prompt
    # data. Rebuilding per retry binds the decision token to their current state.
    agent_row: WorkspaceAgent | None = None
    shelf: list[tuple[str, str, str | None]] = []
    shelf_key = ""
    if session.agent_id:
        agent_row = await db.get(WorkspaceAgent, session.agent_id)
    if (
        session.kind is SessionKind.chat
        and requested_model.get("supportsTools")
        and session.agent_id
    ):
        shelf_key = (agent_row.index_key or "") if agent_row else ""
        shelf = [
            (row.name, row.text, row.source_url)
            for row in (
                await db.exec(
                    select(StoredFile).where(
                        StoredFile.agent_id == session.agent_id,
                        StoredFile.user_id == user.id,
                    )
                )
            ).all()
            if row.text
        ]

    requested_is_strict = _strict_model(requested_model)
    strict_candidate_available = (
        requested_is_strict
        or any(
            model.get("id") in set(policy.privacy_safe_model_ids or []) and _strict_model(model)
            for model in catalogue_models
        )
        or (
            auto_turn
            and any(
                model.get("id") in set(policy.adaptive_economy_model_ids or [])
                and _strict_model(model)
                for model in catalogue_models
            )
        )
    )
    if session.kind is SessionKind.chat and requested_model.get("supportsTools"):
        if not requested_is_strict:
            candidate_tools = sorted(
                await build_tools(
                    db,
                    user,
                    web_search=payload.web_search,
                    allowed=agent_tools,
                    knowledge=shelf,
                    knowledge_collection=shelf_key,
                ),
                key=lambda tool: (tool.name, tool.source),
            )
        if strict_candidate_available:
            strict_tools = sorted(
                _strict_local_tools(
                    await build_tools(
                        db,
                        user,
                        web_search=False,
                        allowed=agent_tools,
                        knowledge=shelf,
                        knowledge_collection="",
                        include_connectors=False,
                        strict_local=True,
                    )
                ),
                key=lambda tool: (tool.name, tool.source),
            )

    requested_tools = strict_tools if requested_is_strict else candidate_tools
    try:
        workspace = await assemble(
            db,
            user,
            session,
            attachment_ids=payload.attachments,
            # Only a contained model that says it reads pictures gets one. The
            # requested model is what is known here; the chat path re-assembles
            # below against whatever privacy actually settled on.
            vision=reads_pictures(requested_model),
            activated_skill_ids=payload.activated_skill_ids,
            starting_template_id=payload.starting_template_id,
            # What the person said to concentrate on, when they were told the
            # file would not fit whole and answered. Empty takes the head, which
            # is what an unasked request has always got.
            focus=focus or (content if session.kind is not SessionKind.chat else ""),
            # Report and deck writers do not run the chat tool loop.
            available_tool_names=(
                {tool.name for tool in requested_tools}
                if session.kind is SessionKind.chat
                else set()
            ),
        )
    except WorkspaceContextError as exc:
        _raise_workspace_error(exc)

    # The first gate, and it costs nothing: the server already knows exactly
    # what became of every attachment, so where a file arrived short there is
    # no reason to spend a planning call discovering it — and a question built
    # from the real numbers is a better question than one a model infers from a
    # gap in its context. This is also what stops the model explaining the
    # failure wrongly, which is what it did: told nothing, it announced the file
    # had never arrived and asked for the text to be pasted in.
    if _plans_first(session) and not pending.get("answers"):
        short = grounding.file_shortfalls(workspace.attachments)
        questions = grounding.questions_for(short)
        # Only when nothing arrived at all, so it can never displace a question
        # about a file that did — `questions_for` is empty in exactly that case.
        if not questions and (gap := grounding.missing_attachment(content, workspace.attachments)):
            questions = [gap]
        if questions:
            return await _ask_before_writing(
                db,
                session,
                request=content,
                typed=typed_content,
                attachments=list(payload.attachments or []),
                questions=questions,
            )

    if session.kind is SessionKind.chat:
        privacy_sources = _privacy_sources(content, history, workspace.blocks)
        if requested_tools:
            privacy_sources["tool_definitions"] = _tool_definition_source(requested_tools)
        # Auto always performs the deterministic full-envelope scan before a
        # classifier, key operation or model call, even when the organisation
        # has disabled the optional external-data decision UI.
        auto_preflight_findings = (
            governance.findings(privacy_sources, legacy=policy.pii_masking) if auto_turn else []
        )
        resolved = await _resolve_privacy(
            user=user,
            session=session,
            policy=policy,
            catalogue=catalogue_models,
            requested=[model],
            sources=privacy_sources,
            explicit_action=payload.privacy_action,
            decision_token=payload.privacy_decision_token,
        )
        if isinstance(resolved, JSONResponse):
            await _audit_policy(
                user,
                request,
                "privacy.decision_required",
                governance.DETECTOR_VERSION,
                metadata=_decision_audit_metadata(resolved, session.id),
            )
            return resolved
        privacy_resolution = resolved
        if revoked_model is not None:
            # The requested model, not the one that answered. `actualModelChanged` is
            # this comparison, and it is stored on the message.
            resolved.routing = {
                **resolved.routing,
                "requestedModels": [revoked_model["id"]],
            }
        if auto_turn and auto_preflight_findings:
            # Privacy owns this turn. Do not send the original envelope to the
            # complexity classifier even when the selected privacy action is
            # masking or a strict-local route.
            cost_routing = _cost_routing(
                decision="bypassed",
                reason_code="privacy_detected",
                requested_model=requested_model,
                selected_model=resolved.models[0],
            )
            resolved.routing = {**resolved.routing, "costRouting": cost_routing}
        model = resolved.models[0]
        strict_local = resolved.strict_local
        # A model that did not support tools at request time cannot gain new
        # capabilities merely because privacy routing picked another model.
        if requested_model.get("supportsTools") and model.get("supportsTools"):
            tools = strict_tools if strict_local else candidate_tools
        masker = governance.mask_legacy if policy.pii_masking else governance.mask
        if resolved.findings:
            # Raw text exists only for this request. Every accepted privacy
            # action stores the detected parts masked.
            stored_content = masker(content)[0]
            if attachment_meta:
                # Preserve the uploaded file itself, but do not copy a finding
                # from its display name/error into message metadata.
                attachment_meta = [
                    {
                        **item,
                        "name": masker(str(item.get("name") or ""))[0],
                        "error": (
                            masker(str(item["error"]))[0]
                            if item.get("error")
                            else item.get("error")
                        ),
                    }
                    for item in attachment_meta
                ]
        if resolved.mask_outbound:
            content = masker(content)[0]
            outbound_history = _mask_list(outbound_history, legacy=policy.pii_masking)
            # A JSON-Schema name/key cannot safely be rewritten while retaining
            # its runtime argument mapping. Drop the complete sensitive tool;
            # clean tools keep the exact detached definitions inspected above.
            tools = _drop_sensitive_tool_definitions(
                tools,
                legacy=policy.pii_masking,
            )
        tool_definitions = openai_snapshot(tools)
    elif policy.pii_masking:
        # Report/slides keep their legacy always-mask behaviour. The new
        # decision flow is intentionally chat-only in this release.
        masker = governance.mask_legacy
        content, masked = masker(content)
        stored_content = content
        if attachment_meta:
            # Document surfaces persist the same attachment metadata as chat, and a
            # filename or extraction error is user content.
            attachment_meta = _mask_text_tree(attachment_meta, masker)
        if masked:
            await _audit_policy(user, request, "pii.masked", f"{masked}건")

    # A privacy action can remove tools after the first validation. Re-resolve
    # the same explicit skills against the exact final tool snapshot before any
    # message, ledger entry, key issue, or upstream request is created.
    if session.kind is SessionKind.chat:
        try:
            workspace = await assemble(
                db,
                user,
                session,
                attachment_ids=payload.attachments,
                vision=reads_pictures(model),
                activated_skill_ids=payload.activated_skill_ids,
                starting_template_id=payload.starting_template_id,
                available_tool_names={tool.name for tool in tools},
            )
        except WorkspaceContextError as exc:
            _raise_workspace_error(exc)

    trusted_context = workspace.trusted
    untrusted_context = workspace.untrusted
    if privacy_resolution and privacy_resolution.mask_outbound:
        trusted_context = _mask_list(trusted_context, legacy=policy.pii_masking)
        untrusted_context = _mask_list(untrusted_context, legacy=policy.pii_masking)
    elif policy.pii_masking:
        # Report and slide context is source-separated; the always-mask policy
        # covers those outbound fields, not only the request sentence.
        trusted_context = _mask_text_tree(trusted_context, governance.mask_legacy)
        untrusted_context = _mask_text_tree(untrusted_context, governance.mask_legacy)
    skills_event = workspace.skills_event()
    # The 서식's own skills, joined the same way a hand-activated one is.
    # A 공문 without the 공문 문체 rules is a notice-shaped essay; the 서식
    # names its procedures in `template.toml` and they ride in here, announced
    # in the same skills_applied event so the person sees what joined.
    template_blocks, skills_event = await _template_skills(
        db, design_templates.get(session.render_template_id), session.kind, skills_event
    )
    if template_blocks:
        trusted_context = list(trusted_context) + template_blocks
    context_steps = _context_steps(workspace)
    if policy.pii_masking:
        # The document runners persist this event directly as a timeline step.
        # Treat the selected skill's display name as user-controlled metadata,
        # just like an attachment filename.
        if skills_event:
            skills_event = _mask_text_tree(skills_event, governance.mask_legacy)
        # A filename and a memory's name are user-controlled in exactly the
        # same way, and these steps are persisted and re-served.
        context_steps = _mask_text_tree(context_steps, governance.mask_legacy)

    # Chat carries a substitution inside its privacy resolution; report and
    # slides resolve none, so the runners take it from here and store it on
    # their own message.
    document_routing = (
        _substitution_routing(revoked_model, model)
        if privacy_resolution is None and revoked_model is not None
        else None
    )

    strict_local = bool(privacy_resolution and privacy_resolution.strict_local)
    wire_history = [
        {"role": message.role.value, "content": body}
        for message, body in zip(history, outbound_history, strict=True)
    ]
    wire_history.append({"role": "user", "content": content})
    messages = build_messages(
        session.kind,
        wire_history,
        with_tools=bool(tools),
        web_search=payload.web_search,
        # The toggle travels even when no search tool survives an agent allowlist
        # or a strict-local route — otherwise the answer reads like a searched one.
        web_search_available=any(t.name == "web_search" for t in tools),
        extra=trusted_context,
        untrusted_context=untrusted_context,
    )
    # After `build_messages`, never inside it — see `context.with_pictures`.
    messages = with_pictures(messages, [picture.uri for picture in workspace.pictures])

    if auto_turn and not auto_preflight_findings:
        unsupported = bool(
            payload.attachments
            or payload.web_search
            or payload.activated_skill_ids
            or payload.starting_template_id
            or session.agent_id
            or session.project_id
        )
        # Economy turns are tool-free. The classifier sees the full quality-model
        # envelope and is told the tools will not exist after a route, so a later
        # tool result cannot overflow the candidate window checked here.
        economy_messages = build_messages(
            session.kind,
            wire_history,
            with_tools=False,
            web_search=False,
            extra=trusted_context,
            untrusted_context=untrusted_context,
        )
        routed_model, cost_routing = await _resolve_cost_routing(
            mode=session.routing_mode,
            db=db,
            user=user,
            policy=policy,
            catalogue=catalogue_models,
            quality_model=requested_model,
            classifier_messages=messages,
            classifier_tool_definitions=tool_definitions,
            context_tokens=adaptive_routing.estimated_context_tokens(
                [
                    json.dumps(
                        economy_messages,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                ]
            ),
            unsupported_reason="unsupported_turn" if unsupported else None,
        )
        resolved.models = [routed_model]
        resolved.routing = _apply_effective_model(resolved.routing, routed_model)
        resolved.routing = {**resolved.routing, "costRouting": cost_routing}
        privacy_resolution = resolved
        model = routed_model
        # Only downwards. The economy envelope is the tool-free one the
        # classifier was shown; an upgraded turn keeps the envelope it already
        # had, tools included, because that is what it was routed up to use.
        if (
            cost_routing.get("decision") == "routed"
            and session.routing_mode != RoutingMode.auto_quality
        ):
            tools = []
            tool_definitions = []
            messages = economy_messages
        strict_local = resolved.strict_local

    if not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )

    for stored in rows:
        stored.session_id = session.id
        db.add(stored)

    if retry_of is not None:
        # The same question, run again: one row in the transcript, the failed
        # reply under it gone, the mark that said nothing came cleared so the
        # new answer is not born labelled.
        for row in superseded:
            await db.delete(row)
        user_message = retry_of
        user_message.failure = None
        user_message.routing = (
            privacy_resolution.routing if privacy_resolution else document_routing
        )
    else:
        user_message = Message(
            session_id=session.id,
            role=Role.user,
            # What they typed, not the merge. A reply to a proposal carries the
            # original request and every answer so far folded in behind it, and
            # putting that blob in the transcript would attribute to somebody a
            # paragraph they never wrote.
            content=typed_content if pending else stored_content,
            attachments=attachment_meta,
            routing=privacy_resolution.routing if privacy_resolution else document_routing,
            started_from=workspace.started_from,
        )
    db.add(user_message)
    # A strict privacy route and SendMessage.model are turn-only. An Auto
    # session's persisted model is its ceiling, changed through PATCH.
    if session.routing_mode not in _AUTO_MODES or payload.model is None:
        # A substitute is for this turn only: written back, it would outlive the
        # revocation that caused it and nothing would move the session back.
        if revoked_model is None:
            session.model = requested_model["id"]
    session.updated_at = utcnow()
    # `session.render_template_id` was resolved at the top of this handler; the
    # assignment lands with the rest of the turn rather than in its own commit.
    if not session.title:
        # Provisional title, replaced once the first turn completes.
        session.title = chat_service.provisional_title(stored_content)
    db.add(session)
    privacy_audit_id: str | None = None
    if privacy_resolution and privacy_resolution.findings:
        privacy_audit = AuditEvent(
            actor_id=user.id,
            action=f"privacy.{privacy_resolution.action}",
            target=session.id,
            detail=governance.DETECTOR_VERSION,
            event_metadata={
                **governance.finding_metadata(privacy_resolution.findings),
                **privacy_resolution.routing,
            },
            ip=client_ip(request),
            severity="warn",
        )
        privacy_audit_id = privacy_audit.id
        db.add(privacy_audit)
    routing_audit_id: str | None = None
    cost_route = (
        privacy_resolution.routing.get("costRouting")
        if privacy_resolution and privacy_resolution.routing
        else None
    )
    if cost_route:
        routing_audit = AuditEvent(
            actor_id=user.id,
            action="routing.auto",
            target=session.id,
            detail=adaptive_routing.CLASSIFIER_VERSION,
            event_metadata=dict(cost_route),
            ip=client_ip(request),
        )
        routing_audit_id = routing_audit.id
        db.add(routing_audit)
    if agent_row is not None:
        agent_row.runs += 1
        db.add(agent_row)
    await db.commit()

    # Resolved per turn while a DB session is open. Also issues the key to an
    # account provisioned during a proxy outage.
    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)

    is_first_turn = len(history) == 0

    # A rendering template replaces the surface's built-in track, resolved
    # before either: it is a choice about the output, not a hint.
    #
    # The planner, when an administrator has named one. It carries the same
    # request and context as the body, so it is bound by the same allowlist,
    # surface and boundary — a strict-local turn gets no planner, and a planner
    # may never be less contained than the writer. Anything failing those falls
    # back to the writing model.
    outline_model = _planner_model(
        policy.outline_model_id,
        user=user,
        catalogue=catalogue_models,
        kind=session.kind.value,
        writer=model,
        strict_local=strict_local,
    )

    #: The model that draws a document's figures — the image default, not the
    #: one writing the prose. A deployment with no image model simply never
    #: shows the figure card, which is the right silence: there is nothing to
    #: offer.
    image_model = next(
        (
            m
            for m in _allowed_models(user, catalogue_models, kind="image")
            if "image" in (m.get("kinds") or [])
        ),
        None,
    )

    render_template = design_templates.get(session.render_template_id)
    if render_template is not None:
        return StreamingResponse(
            _survive_disconnect(
                _run_page(
                    may_ask=not proceed_as_is,
                    figures_plan=approved_figures,
                    image_model=image_model,
                    user_id=user.id,
                    api_key=api_key,
                    session_id=session.id,
                    # Set only on the second pass, when somebody has approved what
                    # the first pass offered. `None` plans and offers again.
                    approved_plan=approved_plan,
                    attachments=list(payload.attachments or []),
                    answers=dict(pending.get("answers") or {}),
                    model=model,
                    request=content,
                    project_id=session.project_id,
                    routing=document_routing,
                    template=render_template,
                    trusted_context=trusted_context,
                    untrusted_context=untrusted_context,
                    design_tokens=workspace.design_tokens,
                    skills_event=skills_event,
                    context_steps=context_steps,
                    outline_model=outline_model,
                    project_sources=[
                        {
                            "id": item.id,
                            "name": item.name,
                            "state": item.state,
                            "sourceUrl": item.source_url,
                            "locations": list(item.locations),
                        }
                        for item in workspace.knowledge
                    ],
                    # A strict-local route is given no network anywhere else, and
                    # a document is not the place to make the one exception.
                    web_search=payload.web_search and not strict_local,
                )
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # A sentence typed under a finished document works on that document. See
    # `revising` above and `_revise_document` — before this, it planned another
    # one and offered to replace the one on screen.
    if revising and session.kind in (SessionKind.report, SessionKind.slides):
        return StreamingResponse(
            _survive_disconnect(
                _revise_document(
                    user_id=user.id,
                    api_key=api_key,
                    session_id=session.id,
                    model=model,
                    instruction=content,
                    routing=document_routing,
                )
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if session.kind is SessionKind.report:
        return StreamingResponse(
            _survive_disconnect(
                _run_report(
                    may_ask=not proceed_as_is,
                    figures_plan=approved_figures,
                    image_model=image_model,
                    user_id=user.id,
                    api_key=api_key,
                    session_id=session.id,
                    # Set only on the second pass, when somebody has approved what
                    # the first pass offered. `None` plans and offers again.
                    approved_plan=approved_plan,
                    attachments=list(payload.attachments or []),
                    answers=dict(pending.get("answers") or {}),
                    model=model,
                    request=content,
                    project_id=session.project_id,
                    routing=document_routing,
                    # The same context blocks the chat surface gets: project instructions,
                    # memories, attached forms.
                    trusted_context=trusted_context,
                    untrusted_context=untrusted_context,
                    design_tokens=workspace.design_tokens,
                    skills_event=skills_event,
                    context_steps=context_steps,
                    outline_model=outline_model,
                    project_sources=[
                        {
                            "id": item.id,
                            "name": item.name,
                            "state": item.state,
                            "sourceUrl": item.source_url,
                            "locations": list(item.locations),
                        }
                        for item in workspace.knowledge
                    ],
                    # A strict-local route is given no network anywhere else, and
                    # a document is not the place to make the one exception.
                    web_search=payload.web_search and not strict_local,
                )
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if session.kind is SessionKind.slides:
        return StreamingResponse(
            _survive_disconnect(
                _run_deck(
                    may_ask=not proceed_as_is,
                    figures_plan=approved_figures,
                    image_model=image_model,
                    user_id=user.id,
                    api_key=api_key,
                    session_id=session.id,
                    # Set only on the second pass, when somebody has approved what
                    # the first pass offered. `None` plans and offers again.
                    approved_plan=approved_plan,
                    attachments=list(payload.attachments or []),
                    answers=dict(pending.get("answers") or {}),
                    model=model,
                    request=content,
                    project_id=session.project_id,
                    routing=document_routing,
                    # The same context blocks the chat surface gets: project instructions,
                    # memories, attached forms.
                    trusted_context=trusted_context,
                    untrusted_context=untrusted_context,
                    design_tokens=workspace.design_tokens,
                    skills_event=skills_event,
                    context_steps=context_steps,
                    outline_model=outline_model,
                    # A strict-local route is given no network anywhere else, and
                    # a document is not the place to make the one exception.
                    web_search=payload.web_search and not strict_local,
                )
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return StreamingResponse(
        _survive_disconnect(
            _run_turn(
                user_id=user.id,
                api_key=api_key,
                auto_memory=Preferences.of(user).auto_memory,
                session_id=session.id,
                # How far a shared note reaches, and whose byline it carries.
                project_id=session.project_id or "",
                agent_name=(agent_row.name if agent_row else ""),
                model=model,
                messages=messages,
                tools=tools,
                tool_definitions=tool_definitions,
                # Sampling belongs to the agent, not the surface. None leaves the upstream
                # default standing.
                temperature=agent_temperature,
                first_user_message=stored_content,
                # The question is already committed. A turn with no answer comes back and
                # says so on this row, or the transcript keeps a prompt and silence.
                user_message_id=user_message.id,
                is_first_turn=is_first_turn,
                skills_event=skills_event,
                context_steps=context_steps,
                routing=privacy_resolution.routing if privacy_resolution else document_routing,
                quality_model=requested_model if cost_route else None,
                disable_fallbacks=bool(cost_route and cost_route.get("decision") == "routed"),
                # Guarded organisations mask every persisted model-generated
                # string, even when the inbound envelope was clean: a provider or
                # tool can introduce a new email/key in its response.
                mask_at_rest=policy.pii_masking or policy.external_data_guard,
                sanitize_tool_output=bool(
                    policy.pii_masking
                    or (
                        policy.external_data_guard
                        and privacy_resolution
                        and not privacy_resolution.strict_local
                    )
                ),
                legacy_masking=policy.pii_masking,
                protect_enrichment=policy.pii_masking or policy.external_data_guard,
                privacy_audit_id=privacy_audit_id,
                routing_audit_id=routing_audit_id,
                # The toggle is a request, not a suggestion. Left on `auto` a
                # small model reads the nudge as advice and answers from what
                # it remembers, under a lit globe that says it looked. Only the
                # first hop is forced; after it the loop is free again.
                force_tool=(
                    "web_search"
                    if payload.web_search and any(t.name == "web_search" for t in tools)
                    else None
                ),
            )
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Without this, nginx buffers SSE.
            "X-Accel-Buffering": "no",
        },
    )


#: Turns generating right now, by session. Read only by the stop button:
#: 중단 and a closed tab are the same event on a socket, opposite intentions.
#:
#: A *set* per session, not one event. It used to be one, so a second turn
#: starting on a session replaced the first turn's signal — and the first turn,
#: still running detached, could no longer be stopped by anything. That is half
#: of why a cancelled answer turned up later underneath an unrelated question.
_STOPPING: dict[str, set[asyncio.Event]] = {}

#: A proxy or provider key echoed back in an upstream error body. LiteLLM masks
#: most of it, but the reason is about to be shown on a screen.
_SECRET_IN_REASON = re.compile(r"sk-[A-Za-z0-9_\-]+")


def _error_event(message: str, exc: BaseException | None = None) -> dict[str, Any]:
    """The SSE `error` event, carrying the reason the log already had.

    Every failure used to reach the screen as the one sentence in `message`,
    while `ChatStreamError` — `upstream_502: <detail>`, `upstream_unreachable:
    <exc>` — went to the log alone. A person could not tell a backend that is
    down from a model that is missing from an account that is out of quota,
    and neither could an operator reading over their shoulder. `code` is the
    machine half, for the client's own vocabulary; `reason` is the upstream's
    sentence, bounded and with any key blanked out. `message` stays for a
    client that knows nothing else.
    """
    code, reason = "internal_error", ""
    if isinstance(exc, chat_service.ChatStreamError):
        head, _, tail = str(exc).partition(": ")
        code = head.strip() or "upstream_failed"
        reason = _SECRET_IN_REASON.sub("sk-…", tail.strip())[:200]
    event: dict[str, Any] = {"type": "error", "code": code, "message": message}
    if reason:
        event["reason"] = reason
    return event


async def _until_stopped(
    events: AsyncIterator[dict[str, Any]], stopping: asyncio.Event
) -> AsyncIterator[dict[str, Any]]:
    """Relays a turn's events, and gives up the moment 중단 is pressed.

    The other half of the same bug. The stop used to be checked between events:

        async for event in run_turn(...):
            if stopping.is_set():
                break

    which only runs when the next event arrives. A model that has accepted the
    request and gone quiet produces no next event, so the check never ran, the
    turn stayed alive against a 15-minute upstream timeout, and when it finally
    spoke it wrote its answer into a conversation that had moved on — appearing
    under whatever had been typed since. Pressing 중단 stopped the screen and
    nothing else.

    Racing the two means the stop is acted on while the turn is silent, which is
    exactly when somebody presses it. Closing the generator propagates
    `GeneratorExit` down through the agent loop to the streaming request, so the
    upstream call is actually abandoned rather than left running unread.
    """
    iterator = events.__aiter__()
    waiting = asyncio.ensure_future(stopping.wait())
    try:
        while not stopping.is_set():
            nxt = asyncio.ensure_future(anext(iterator))
            done, _ = await asyncio.wait({nxt, waiting}, return_when=asyncio.FIRST_COMPLETED)
            if nxt not in done:
                # Stopped while this one was still in the air. Cancelling it is
                # what releases the socket underneath — and it has to be waited
                # on before the generator is closed below, or `aclose()` finds
                # it still running and raises instead of unwinding it.
                nxt.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await nxt
                return
            try:
                event = nxt.result()
            except StopAsyncIteration:
                return
            yield event
    finally:
        waiting.cancel()
        await iterator.aclose()


#: Strong references to tasks whose reader has gone — otherwise the loop is
#: the only thing holding them and they are collectible mid-turn.
_DETACHED: set[asyncio.Task] = set()


async def _survive_disconnect(events: AsyncIterator[str]) -> AsyncIterator[str]:
    """Lets a turn finish even when nobody is left reading it.

    The response and the work behind it are separate tasks: the turn produces
    into a queue and this relays it. A reader leaving cancels the relay only, so
    the turn still reaches the block that stores the answer, charges for it and
    names the conversation.

    The queue is unbounded — one turn's worth of small strings.

    This is the contract the client documents: stop aborts the request; the
    server still stores what it produced.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for event in events:
                await queue.put(event)
        except Exception:  # noqa: BLE001 — nobody is left to receive a raise
            log.exception("detached turn failed")
        finally:
            await queue.put(None)

    task = asyncio.create_task(pump())
    _DETACHED.add(task)
    task.add_done_callback(_DETACHED.discard)

    while (event := await queue.get()) is not None:
        yield event


async def _store_notes(
    db: AsyncSession,
    user_id: str,
    session_id: str,
    project_id: str,
    notes: list[dict],
) -> None:
    """Writes what one turn left for the next.

    Stored as memories rather than as a table of their own, because that is
    exactly what they are — a durable fact this account's work should keep
    seeing — and because the context assembler already reads that table and
    puts it in front of every turn. A parallel store would have needed its own
    injection path, its own screen and its own retention rule to end up in the
    same place.

    The scope is what makes it a handoff. `project_id` reaches every
    conversation and every agent in that project; without one it is this
    conversation's own, which is a note to the next turn rather than a broadcast.

    `key` is matched inside the scope, so the same finding revised twice leaves
    one note that is current instead of three that disagree.
    """
    scope = project_id or session_id
    for note in notes:
        name = note["key"]
        existing = (
            await db.exec(
                select(Memory).where(
                    Memory.user_id == user_id,
                    Memory.scope == scope,
                    Memory.name == name,
                )
            )
        ).first()
        # The byline is on the description rather than in the body: the body is
        # what the next agent acts on, and a sentence about who wrote it is not
        # part of the finding.
        author = note.get("author") or ""
        description = f"{note['title']} — {author}" if author else note["title"]
        if existing is not None:
            existing.description = description[:400]
            existing.body = note["body"]
            existing.updated_at = utcnow()
            db.add(existing)
            continue
        db.add(
            Memory(
                user_id=user_id,
                name=name,
                description=description[:400],
                # `reference` rather than `project`: this is something the work
                # found, not something the person told us about themselves.
                type=MemoryType.reference,
                body=note["body"],
                scope=scope,
            )
        )


async def _run_turn(
    *,
    user_id: str,
    api_key: str,
    auto_memory: bool,
    session_id: str,
    project_id: str = "",
    agent_name: str = "",
    model: dict,
    messages: list[dict],
    tools: list[Tool],
    tool_definitions: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
    first_user_message: str,
    user_message_id: str | None = None,
    is_first_turn: bool,
    skills_event: dict | None = None,
    context_steps: list[dict] | None = None,
    routing: dict | None = None,
    quality_model: dict | None = None,
    disable_fallbacks: bool = False,
    mask_at_rest: bool = False,
    sanitize_tool_output: bool = False,
    legacy_masking: bool = False,
    protect_enrichment: bool = False,
    privacy_audit_id: str | None = None,
    routing_audit_id: str | None = None,
    #: A tool the first hop must call rather than may. See `agent.run_turn`.
    #: Set to `web_search` when somebody switched the search toggle on and the
    #: tool survived into this turn.
    force_tool: str | None = None,
) -> AsyncIterator[str]:
    """Drives one assistant turn to completion and settles it.

    Own DB session: the request-scoped one closes when the route returns the
    StreamingResponse.
    """
    text_parts: list[str] = []
    steps: list[dict] = _prelude_steps(skills_event, context_steps)
    usage = {"inputTokens": 0, "outputTokens": 0}
    failed: str | None = None
    answer_id: str | None = None
    tool_output_masked = 0
    tool_output_findings: dict[tuple[str, str], int] = {}
    actual_model = model["id"]

    # Pressing 중단 sets this. A closed tab does not — that is the whole point
    # of it being a separate signal from the socket.
    stopping = asyncio.Event()
    # Anything still running on this session is superseded by this turn. Two
    # turns writing into one conversation interleave into a transcript neither
    # of them wrote, which is the state the browser sees as an old answer
    # arriving under a new question.
    for earlier in _STOPPING.get(session_id, set()):
        earlier.set()
    _STOPPING.setdefault(session_id, set()).add(stopping)

    ctx = ToolContext(
        user_id=user_id,
        session_id=session_id,
        api_key=api_key,
        project_id=project_id,
        agent_name=agent_name,
        # The last thing the person said, which is what this turn answers.
        # `first_user_message` is the conversation's opening line and would
        # have a tool five turns later reading a request nobody made here.
        request=next(
            (
                str(one.get("content") or "")
                for one in reversed(messages)
                if one.get("role") == "user"
            ),
            "",
        ),
    )
    masker = governance.mask_legacy if legacy_masking else governance.mask
    strict_local = _strict_model(model)
    cost_routing = dict((routing or {}).get("costRouting") or {}) or None

    def classify_tool_output(value: str) -> list[dict[str, Any]]:
        return [
            row.wire()
            for row in governance.findings(
                {"tool_output": value},
                legacy=legacy_masking,
            )
        ]

    if routing:
        # First event: the browser can update its model badge before any answer
        # token, and a new-session composer can navigate without losing a 409.
        yield chat_service.sse({"type": "privacy_route", **routing})
    if cost_routing:
        yield chat_service.sse({"type": "model_route", **cost_routing})
    if skills_event:
        yield chat_service.sse(skills_event)
    for step in context_steps or ():
        yield chat_service.sse(_step_event(step))
    try:
        async for event in _until_stopped(
            agent_service.run_turn(
                model["id"],
                messages,
                tools,
                ctx,
                tool_definitions=tool_definitions,
                temperature=temperature,
                sanitize_tool_output=(masker if sanitize_tool_output else None),
                sanitize_step_detail=masker if protect_enrichment else None,
                classify_tool_output=classify_tool_output if protect_enrichment else None,
                strict_local=strict_local,
                disable_fallbacks=disable_fallbacks,
                redact_logging=mask_at_rest,
                force_tool=force_tool,
            ),
            stopping,
        ):
            if event["type"] == "delta":
                text_parts.append(event["text"])
            elif event["type"] == "retract":
                # Narration the agent took back — see `agent.run_turn`. Out of
                # the stored answer here, off the screen by the same event.
                joined = "".join(text_parts).replace(event["text"], "", 1)
                text_parts[:] = [joined]
            elif event["type"] == "step":
                # Stored without the SSE envelope key: `Step.type` in the UI is
                # a display category, not the event name. One row per step: the
                # running event and the done event share an id, and appending
                # both stored every tool call twice — the first copy still
                # saying 검색 중 after a reload.
                row = {k: v for k, v in event.items() if k != "type"}
                at = next((i for i, s in enumerate(steps) if s.get("id") == row.get("id")), None)
                if at is None:
                    steps.append(row)
                else:
                    steps[at] = row
            elif event["type"] == "usage":
                usage = {k: v for k, v in event.items() if k != "type"}
                continue  # re-emitted below with the credit figure
            elif event["type"] == "model_route":
                actual_model = str(event["actualModel"])
                routing = _with_actual_model(
                    routing or {},
                    str(event["routedModel"]),
                    actual_model,
                )
                yield chat_service.sse({"type": "privacy_route", **routing})
                if cost_routing:
                    cost_routing = {**cost_routing, "executedModel": actual_model}
                    routing = {**(routing or {}), "costRouting": cost_routing}
                    # Do not leak the agent loop's partial provider-route shape.
                    yield chat_service.sse({"type": "model_route", **cost_routing})
                continue
            elif event["type"] == "privacy_route" and event.get("source") == "tool_output":
                count = int(event.get("count") or 0)
                if event.get("action") == "mask_external":
                    tool_output_masked += count
                for finding in event.get("findings") or []:
                    key = (
                        str(finding.get("category") or "unknown"),
                        str(finding.get("source") or "tool_output"),
                    )
                    tool_output_findings[key] = tool_output_findings.get(key, 0) + int(
                        finding.get("count") or 0
                    )
                routing = {
                    **(routing or {}),
                    **(
                        {"initialAction": routing.get("action")}
                        if routing and "initialAction" not in routing
                        else {}
                    ),
                    "action": event.get("action") or "mask_external",
                    "toolOutputMasked": tool_output_masked,
                    "toolOutputFindings": [
                        {"category": category, "source": source, "count": total}
                        for (category, source), total in sorted(tool_output_findings.items())
                    ],
                }
                yield chat_service.sse({"type": "privacy_route", **routing})
                continue
            yield chat_service.sse(event)
        # Stopped, and the settling below still runs: the partial answer is
        # worth keeping and the spent tokens worth charging. Only the label
        # differs. Set here rather than inside the loop, because a stopped turn
        # is exactly the one that does not go round the loop again.
        if stopping.is_set():
            failed = "stopped"
    except chat_service.ChatStreamError as exc:
        log.warning("chat stream failed for session %s: %s", session_id, exc)
        failed = str(exc)
        yield chat_service.sse(
            _error_event(
                "모델 사용량 한도에 닿았습니다. 잠시 후 다시 시도해 주세요."
                if failed.startswith("upstream_429")
                else "모델 응답을 받지 못했습니다.",
                exc,
            )
        )
    except Exception as exc:  # noqa: BLE001 — turn still has to settle and close
        log.exception("chat stream crashed for session %s", session_id)
        failed = "internal_error"
        yield chat_service.sse(_error_event("요청 처리 중 오류가 발생했습니다.", exc))

    content = "".join(text_parts)
    if failed == "stopped" and not any(usage.values()):
        # The proxy reports usage on its final chunk, and a stopped stream never
        # reaches it — so a turn somebody cut off used to settle as 0 in · 0 out
        # and cost nothing, while the tokens had been spent. What went up and
        # what came down are both in hand; an estimate from them, marked as one,
        # is the honest figure. Left alone when a completed hop already reported.
        usage = {
            "inputTokens": file_service.estimate_tokens(
                "".join(str(m.get("content") or "") for m in messages)
            ),
            "outputTokens": file_service.estimate_tokens(content),
            "estimated": True,
        }
    stored_content = masker(content)[0] if mask_at_rest or tool_output_findings else content
    protect_persistence = mask_at_rest or bool(tool_output_findings)
    # What the guard took out of the answer. The browser keeps the streamed
    # original until the session is reopened, so without this somebody copies
    # what is on screen a week later and gets placeholders.
    answer_findings = (
        governance.findings({"assistant_output": content}, legacy=legacy_masking)
        if stored_content != content
        else []
    )
    if answer_findings:
        routing = {
            **(routing or {}),
            "findingCounts": [
                *((routing or {}).get("findingCounts") or []),
                *(row.wire() for row in answer_findings),
            ],
        }
        yield chat_service.sse({"type": "privacy_route", **routing})
    stored_steps = _mask_text_tree(steps, masker) if protect_persistence else steps
    stored_actual_model = masker(actual_model)[0] if protect_persistence else actual_model
    credits = (
        0 if not content else charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
    )
    if cost_routing:
        cost_routing = {**cost_routing, "executedModel": actual_model}
        if content and quality_model is not None and cost_routing.get("decision") == "routed":
            quality_credits = charge_for_tokens(
                quality_model,
                usage["inputTokens"],
                usage["outputTokens"],
            )
            cost_routing["estimatedCreditsSaved"] = max(0, quality_credits - credits)
        routing = {**(routing or {}), "costRouting": cost_routing}
    stored_routing = _mask_text_tree(routing, masker) if protect_persistence else routing

    new_artifact: str | None = None
    title: str | None = None
    title_credits = 0
    title_model: str | None = None
    if is_first_turn and stored_content and not failed:
        enrichment = await _enrichment_model(
            model, strict_local=strict_local, disable_fallbacks=disable_fallbacks
        )
        title, title_usage = await chat_service.generate_title(
            enrichment["id"],
            first_user_message,
            stored_content,
            api_key,
            masker=masker if protect_enrichment else None,
            strict_local=strict_local,
            disable_fallbacks=disable_fallbacks,
            redact_logging=mask_at_rest or bool(tool_output_findings),
        )
        title_credits = charge_for_tokens(
            enrichment, title_usage["inputTokens"], title_usage["outputTokens"]
        )
        title_model = enrichment["id"]

    # One transaction: assistant message, deduction, title.
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        user = await db.get(User, user_id)
        if session is not None and user is not None:
            if content:
                answer = Message(
                    session_id=session_id,
                    role=Role.assistant,
                    content=stored_content,
                    steps=stored_steps or None,
                    usage={**usage, "credits": credits},
                    model=stored_actual_model,
                    routing=stored_routing,
                    # Half an answer is worth keeping, and worth labelling: the browser says the
                    # stream broke, and storing it says the same thing tomorrow. Which label
                    # depends on who ended it — a pressed 중단 is not a broken stream.
                    failure=(
                        TurnFailure.stopped
                        if failed == "stopped"
                        else TurnFailure.interrupted
                        if failed
                        else None
                    ),
                )
                db.add(answer)
                answer_id = answer.id
                settle(
                    db,
                    user,
                    credits,
                    reason="chat.completion",
                    session_id=session_id,
                    model=stored_actual_model,
                )
            else:
                # No answer to store — broken stream, refusal, or an empty completion.
                # An invented assistant message would put words in its mouth, so the
                # question carries the outcome and the retry.
                question = await db.get(Message, user_message_id) if user_message_id else None
                if question is not None:
                    # Stopped before the first token is still stopped, not
                    # unanswered: 답이 오지 않았습니다 under a prompt somebody
                    # cut off themselves reads as the product failing.
                    question.failure = (
                        TurnFailure.stopped if failed == "stopped" else TurnFailure.no_answer
                    )
                    db.add(question)
            # Handoffs, written in the same transaction as the answer that
            # produced them. Deliberately not gated on `content`: a turn can do
            # real work through tools and end with an empty completion, and the
            # finding is still worth passing on. Gated on `failed` — a note left
            # by a turn that broke is a conclusion nobody reached.
            if ctx.pending_notes and not failed:
                await _store_notes(db, user_id, session_id, project_id, ctx.pending_notes)
            if title:
                session.title = title
            # Its own ledger line: a different model may have run it, the message's
            # `credits` explains the message's own tokens, and somebody who never asked
            # for a title is owed a row saying so. Usually free capacity, so zero.
            settle(
                db,
                user,
                title_credits,
                reason="chat.title",
                session_id=session_id,
                model=title_model,
            )
            if privacy_audit_id:
                privacy_audit = await db.get(AuditEvent, privacy_audit_id)
                if privacy_audit is not None:
                    privacy_audit.event_metadata = {
                        **(privacy_audit.event_metadata or {}),
                        **(stored_routing or {}),
                    }
                    db.add(privacy_audit)
            if routing_audit_id:
                routing_audit = await db.get(AuditEvent, routing_audit_id)
                if routing_audit is not None:
                    routing_audit.event_metadata = dict(cost_routing or {})
                    db.add(routing_audit)
            session.updated_at = utcnow()
            db.add(session)
            if tool_output_findings:
                db.add(
                    AuditEvent(
                        actor_id=user_id,
                        action=(
                            "privacy.mask_tool_output"
                            if tool_output_masked
                            else "privacy.strict_tool_output"
                        ),
                        target=session_id,
                        detail=governance.DETECTOR_VERSION,
                        event_metadata={
                            "detectorVersion": governance.DETECTOR_VERSION,
                            "policyVersion": governance.POLICY_VERSION,
                            "findings": [
                                {
                                    "category": category,
                                    "source": source,
                                    "count": count,
                                }
                                for (category, source), count in sorted(
                                    tool_output_findings.items()
                                )
                            ],
                            **(stored_routing or {}),
                        },
                        severity="warn",
                    )
                )
            await db.commit()

    # Enrichment after the answer is durable, in its own transaction. Sharing
    # the turn's would hold it open for an extra query and a model call, and a
    # failure would roll the reply back.
    memory_step: dict | None = None
    if stored_content and not failed:
        new_artifact, memory_step = await _enrich(
            user_id=user_id,
            session_id=session_id,
            content=stored_content,
            first_user_message=first_user_message,
            api_key=api_key,
            model=model,
            auto_memory=auto_memory,
            requested_artifacts=ctx.pending_artifacts,
            protect_privacy=protect_enrichment,
            strict_local=strict_local,
            disable_fallbacks=disable_fallbacks,
            redact_logging=mask_at_rest or bool(tool_output_findings),
            legacy_masking=legacy_masking,
            message_id=answer_id,
        )

    if memory_step:
        yield chat_service.sse(_step_event(memory_step))
    if new_artifact:
        yield chat_service.sse(
            {
                "type": "artifact",
                "artifactId": new_artifact,
                # Whether the panel should take the screen. A document somebody
                # asked for is the deliverable and belongs open; a nine-line
                # example lifted out of an answer is not, and opening for it
                # squeezed the conversation into a third of the width — where
                # the table in that same answer came out one glyph per line.
                "deliberate": bool(ctx.pending_artifacts),
            }
        )
    if cost_routing:
        yield chat_service.sse({"type": "model_route", **cost_routing})
    yield chat_service.sse({"type": "usage", **usage, "credits": credits})
    if title:
        yield chat_service.sse({"type": "title", "title": title})
    # The stored row's id travels with `done`. The browser made its own id for
    # the answer while it streamed, and everything addressed to the message
    # afterwards — a rating, a comparison's choice — went out under that made-up
    # id and met a 404 until the session was reopened.
    yield chat_service.sse({"type": "done", **({"messageId": answer_id} if answer_id else {})})

    # Only if it is still ours: a second turn on this session has already
    # replaced it, and popping then would leave that one unstoppable.
    live = _STOPPING.get(session_id)
    if live is not None:
        live.discard(stopping)
        if not live:
            del _STOPPING[session_id]


@router.post("/{session_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def stop_turn(session_id: str, user: CurrentUser, db: DbSession):
    """Asks the turn running on this session to stop where it is.

    Separate from closing the connection, which means the opposite: a reader who
    navigates away still wants the answer. The socket cannot tell them apart, so
    the button says so before it aborts.

    Idempotent, and silent about whether anything was running — by the time it
    lands the turn has often just finished.
    """
    await _owned(db, user, session_id)
    # Every turn on this session, not the newest: a turn that was superseded is
    # still running and still the one somebody may be waiting on.
    for signal in _STOPPING.get(session_id, set()):
        signal.set()


@router.post("/{session_id}/compare")
async def compare_models(
    session_id: str, payload: CompareRequest, request: Request, user: CurrentUser, db: DbSession
):
    """Runs one prompt against two or three models and streams all of them.

    Every column is a real completion on the caller's key, billed separately
    and stored on one assistant message.
    """
    session = await _owned(db, user, session_id)
    if session.kind is not SessionKind.chat:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="surface_not_implemented"
        )

    # The policy read precedes even the live model-catalogue lookup. If the
    # authoritative row is unavailable, no cached/default policy can authorize
    # an external or strict-local comparison.
    policy = await _require_egress_policy()
    content = payload.content
    if policy.intent_filter and (hit := governance.blocked_by(content, policy.blocked_categories)):
        await _audit_policy(user, request, "filter.blocked", hit)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"blocked_category:{hit}"
        )

    catalogue = await model_service.list_models_for_egress()
    if len(set(payload.models)) != len(payload.models):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="models_must_be_distinct"
        )
    catalogue_models = catalogue["models"]
    chosen = [model_service.find(catalogue_models, model_id) for model_id in payload.models]
    if any(model is None or "chat" not in model.get("kinds", []) for model in chosen):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="models_unavailable")
    allowed_ids = set(user.allowed_models or [])
    if allowed_ids and any(model_id not in allowed_ids for model_id in payload.models):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="model_not_allowed",
        )
    # The None case was rejected above; keeping request order is part of the
    # comparison contract and of the decision-token binding.
    chosen = [model for model in chosen if model is not None]

    history = await _history(db, session.id)
    attachment_rows, attachment_meta = await _owned_attachments(db, user, payload.attachments)
    try:
        workspace = await assemble(
            db,
            user,
            session,
            attachment_ids=payload.attachments,
            activated_skill_ids=payload.activated_skill_ids,
            starting_template_id=payload.starting_template_id,
            # Comparison intentionally exposes no tools. A skill that requires
            # one is refused before any column starts or any charge is made.
            available_tool_names=set(),
        )
    except WorkspaceContextError as exc:
        _raise_workspace_error(exc)
    resolved = await _resolve_privacy(
        user=user,
        session=session,
        policy=policy,
        catalogue=catalogue_models,
        requested=chosen,
        sources=_privacy_sources(content, history, workspace.blocks),
        explicit_action=payload.privacy_action,
        decision_token=payload.privacy_decision_token,
    )
    if isinstance(resolved, JSONResponse):
        await _audit_policy(
            user,
            request,
            "privacy.decision_required",
            governance.DETECTOR_VERSION,
            metadata={
                **_decision_audit_metadata(resolved, session.id),
                "compare": True,
            },
        )
        return resolved
    chosen = resolved.models

    # Headroom checked only after a possible collapse to strict-local.
    if not has_headroom(user, max(chosen, key=lambda m: m["creditCost"])):
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="no_credits")

    masker = governance.mask_legacy if policy.pii_masking else governance.mask
    stored_content = masker(content)[0] if resolved.findings else content
    if resolved.findings and attachment_meta:
        attachment_meta = [
            {
                **item,
                "name": masker(str(item.get("name") or ""))[0],
                "error": (
                    masker(str(item["error"]))[0] if item.get("error") else item.get("error")
                ),
            }
            for item in attachment_meta
        ]
    outbound_history = [message.content for message in history]
    trusted_context = workspace.trusted
    untrusted_context = workspace.untrusted
    if resolved.mask_outbound:
        content = masker(content)[0]
        outbound_history = _mask_list(outbound_history, legacy=policy.pii_masking)
        trusted_context = _mask_list(trusted_context, legacy=policy.pii_masking)
        untrusted_context = _mask_list(untrusted_context, legacy=policy.pii_masking)

    for stored in attachment_rows:
        stored.session_id = session.id
        db.add(stored)

    db.add(
        Message(
            session_id=session.id,
            role=Role.user,
            content=stored_content,
            attachments=attachment_meta,
            routing=resolved.routing,
            started_from=workspace.started_from,
        )
    )
    session.updated_at = utcnow()
    if not session.title:
        session.title = chat_service.provisional_title(stored_content)
    db.add(session)
    privacy_audit_id: str | None = None
    if resolved.findings:
        privacy_audit = AuditEvent(
            actor_id=user.id,
            action=f"privacy.{resolved.action}",
            target=session.id,
            detail=governance.DETECTOR_VERSION,
            event_metadata={
                **governance.finding_metadata(resolved.findings),
                **resolved.routing,
            },
            ip=client_ip(request),
            severity="warn",
        )
        privacy_audit_id = privacy_audit.id
        db.add(privacy_audit)
    await db.commit()

    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)

    wire = [
        {"role": message.role.value, "content": body}
        for message, body in zip(history, outbound_history, strict=True)
    ]
    wire.append({"role": "user", "content": content})
    messages = build_messages(
        session.kind,
        wire,
        with_tools=False,
        web_search=False,
        extra=trusted_context,
        untrusted_context=untrusted_context,
    )

    return StreamingResponse(
        _run_comparison(
            user_id=user.id,
            api_key=api_key,
            session_id=session.id,
            models=chosen,
            messages=messages,
            skills_event=workspace.skills_event(),
            # A comparison is answered from the same memories and the same
            # attachments as a single-model turn, and spends several times the
            # credits doing it, so it accounts for them the same way.
            context_steps=_context_steps(workspace),
            routing=resolved.routing,
            mask_at_rest=policy.pii_masking or policy.external_data_guard,
            legacy_masking=policy.pii_masking,
            privacy_audit_id=privacy_audit_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_comparison(
    *,
    user_id: str,
    api_key: str,
    session_id: str,
    models: list[dict],
    messages: list[dict],
    skills_event: dict | None = None,
    context_steps: list[dict] | None = None,
    routing: dict,
    mask_at_rest: bool = False,
    legacy_masking: bool = False,
    privacy_audit_id: str | None = None,
) -> AsyncIterator[str]:
    """Fans out, merges the streams, then settles every column in one transaction.

    Interleaved on one connection: the turn is stored and billed as one thing
    even when a column fails.
    """
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    routing_holder = {"value": routing}
    results: dict[str, dict] = {
        model["id"]: {
            "model": model["id"],
            "routedModel": model["id"],
            "actualModel": None,
            "dataBoundary": model.get("dataBoundary") or "unknown",
            "content": "",
            "usage": None,
            "error": None,
        }
        for model in models
    }

    yield chat_service.sse({"type": "privacy_route", **routing})

    async def run(model: dict) -> None:
        slot = results[model["id"]]
        try:
            async for event in chat_service.stream_completion(
                model["id"],
                messages,
                user_id,
                api_key,
                strict_local=_strict_model(model),
                redact_logging=mask_at_rest,
            ):
                if event["type"] == "delta":
                    slot["content"] += event["text"]
                    await queue.put(
                        {"type": "variant", "model": model["id"], "text": event["text"]}
                    )
                elif event["type"] == "retract":
                    slot["content"] = slot["content"].replace(event["text"], "", 1)
                    await queue.put(
                        {"type": "variant_retract", "model": model["id"], "text": event["text"]}
                    )
                elif event["type"] == "usage":
                    slot["usage"] = {k: v for k, v in event.items() if k != "type"}
                elif event["type"] == "model_route":
                    slot["actualModel"] = str(event["actualModel"])
                    routing_holder["value"] = _with_actual_model(
                        routing_holder["value"],
                        model["id"],
                        slot["actualModel"],
                    )
                    await queue.put({"type": "privacy_route", **routing_holder["value"]})
        except Exception as exc:  # noqa: BLE001 — one dead column must not kill the row
            log.warning("comparison column failed (%s): %s", model["id"], exc)
            slot["error"] = "모델 응답을 받지 못했습니다."
        finally:
            usage = slot["usage"] or {"inputTokens": 0, "outputTokens": 0}
            credits = (
                0
                if not slot["content"]
                else charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
            )
            slot["credits"] = credits
            await queue.put(
                {
                    "type": "variant_done",
                    "model": model["id"],
                    "routedModel": model["id"],
                    "actualModel": slot["actualModel"] or model["id"],
                    "credits": credits,
                    "error": slot["error"],
                    **usage,
                }
            )

    async def drive() -> None:
        await asyncio.gather(*(run(m) for m in models))
        await queue.put(None)

    task = asyncio.create_task(drive())
    try:
        if skills_event:
            yield chat_service.sse(skills_event)
        for step in context_steps or ():
            yield chat_service.sse(_step_event(step))
        while (event := await queue.get()) is not None:
            yield chat_service.sse(event)
    finally:
        await task

    masker = governance.mask_legacy if legacy_masking else governance.mask
    variants = [
        {
            "model": r["model"],
            "routedModel": r["routedModel"],
            "actualModel": r["actualModel"] or r["routedModel"],
            "dataBoundary": r["dataBoundary"],
            "content": r["content"],
            "credits": r.get("credits", 0),
            "usage": r["usage"],
            "error": r["error"],
        }
        for r in results.values()
    ]
    if mask_at_rest:
        variants = _mask_text_tree(variants, masker)
    stored_routing = (
        _mask_text_tree(routing_holder["value"], masker)
        if mask_at_rest
        else routing_holder["value"]
    )
    total = sum(v["credits"] for v in variants)

    # The chosen column is the turn's answer for the next turn; empty content
    # would leave a silent assistant message in the history. First successful
    # column is the default, and the stored answer follows a later choice.
    chosen = next((v for v in variants if v["content"] and not v["error"]), None)
    for variant in variants:
        variant["chosen"] = variant is chosen
    stored_prelude = _prelude_steps(skills_event, context_steps)
    if stored_prelude and mask_at_rest:
        stored_prelude = _mask_text_tree(stored_prelude, masker)

    answer = Message(
        session_id=session_id,
        role=Role.assistant,
        content=chosen["content"] if chosen else "",
        variants=variants,
        usage={"credits": total},
        steps=stored_prelude or None,
        model=chosen["actualModel"] if chosen else None,
        routing=stored_routing,
    )
    async with SessionLocal() as db:
        db.add(answer)
        settled = await db.get(User, user_id)
        if privacy_audit_id:
            privacy_audit = await db.get(AuditEvent, privacy_audit_id)
            if privacy_audit is not None:
                privacy_audit.event_metadata = {
                    **(privacy_audit.event_metadata or {}),
                    **stored_routing,
                }
                db.add(privacy_audit)
        if settled is not None and total:
            # No model on this row: one charge covered several of them, and
            # naming any one of them on the usage screen would be a lie about
            # where the money went. It lands in "기타" instead, which is true.
            settle(db, settled, total, reason="chat.compare", session_id=session_id)
        await db.commit()

    # With the stored id: 이 답변으로 계속 posts to this message, and the id the
    # browser gave the row while it streamed is not one the server knows.
    yield chat_service.sse({"type": "done", "credits": total, "messageId": answer.id})


@router.post("/{session_id}/messages/{message_id}/variant", response_model=MessageOut)
async def choose_variant(
    session_id: str, message_id: str, payload: ChooseVariant, user: CurrentUser, db: DbSession
):
    """Marks which of a comparison's answers the conversation continues from.

    Server-side: the choice is a statement about the conversation and has to
    survive a reload.
    """
    await _owned(db, user, session_id)
    message = await db.get(Message, message_id)
    if message is None or message.session_id != session_id or not message.variants:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="variant_not_found")

    variants = [dict(v) for v in message.variants]
    if not any(v.get("model") == payload.model for v in variants):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_model")

    for variant in variants:
        variant["chosen"] = variant.get("model") == payload.model
    message.variants = variants
    message.content = next(v.get("content") or "" for v in variants if v["chosen"])
    selected = next(v for v in variants if v["chosen"])
    message.model = selected.get("actualModel") or payload.model
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return MessageOut.of(message)


async def _enrich(
    *,
    user_id: str,
    session_id: str,
    content: str,
    first_user_message: str,
    api_key: str,
    model: dict,
    auto_memory: bool,
    requested_artifacts: list[dict] | None = None,
    protect_privacy: bool = False,
    strict_local: bool = False,
    disable_fallbacks: bool = False,
    redact_logging: bool = False,
    legacy_masking: bool = False,
    message_id: str | None = None,
) -> tuple[str | None, dict | None]:
    """Artifacts and memories derived from a finished turn.

    All optional, and nothing here may raise: the turn is already stored.

    Returns the new artifact and, when auto-memory wrote anything, the timeline
    step saying so. The step is appended to the stored message, not only
    streamed, so it survives a reload.
    """
    artifact_id: str | None = None
    memory_step: dict | None = None
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        user = await db.get(User, user_id)
        if session is None or user is None:
            return None, None
        privacy_masker = (
            (governance.mask_legacy if legacy_masking else governance.mask)
            if protect_privacy
            else None
        )
        try:
            # A `create_artifact` call wins over extraction from the transcript.
            # Both run: a turn can do each once.
            requested_id = await artifact_extract.store_requested(
                db,
                user_id=user_id,
                session_id=session_id,
                project_id=session.project_id,
                requests=requested_artifacts or [],
                masker=privacy_masker,
            )
            extracted_id = await artifact_extract.extract(
                db,
                user_id=user_id,
                session_id=session_id,
                project_id=session.project_id,
                content=privacy_masker(content)[0] if privacy_masker else content,
            )
            artifact_id = requested_id or extracted_id
            if artifact_id:
                session.artifact_id = artifact_id
                db.add(session)
                # And on the turn that made it. The session pointer names the newest
                # result only and cannot say which answer produced which document.
                message = await db.get(Message, message_id) if message_id else None
                if message is not None:
                    message.artifact_ids = [*(message.artifact_ids or []), artifact_id]
                    db.add(message)
        except Exception:  # noqa: BLE001
            log.exception("artifact extraction failed for session %s", session_id)
            artifact_id = None

        if auto_memory:
            try:
                enrichment = await _enrichment_model(
                    model, strict_local=strict_local, disable_fallbacks=disable_fallbacks
                )
                written, spent = await auto_memory_service.extract(
                    db,
                    user,
                    user_message=first_user_message,
                    assistant_message=content,
                    api_key=api_key,
                    model=enrichment["id"],
                    masker=privacy_masker,
                    strict_local=strict_local,
                    disable_fallbacks=disable_fallbacks,
                    redact_logging=redact_logging,
                )
                if written:
                    log.info("auto-memory wrote %d fact(s) for user %s", written, user.id)
                    memory_step = _memory_saved_step(written)
                    message = await db.get(Message, message_id) if message_id else None
                    if message is not None:
                        message.steps = [*(message.steps or []), memory_step]
                        db.add(message)
                # Charged whether or not a fact came out, at the model that read the turn:
                # deciding there was nothing to remember costs the same as writing two rows.
                settle(
                    db,
                    user,
                    charge_for_tokens(enrichment, spent["inputTokens"], spent["outputTokens"]),
                    reason="chat.memory",
                    session_id=session_id,
                    model=enrichment["id"],
                )
            except Exception:  # noqa: BLE001
                log.exception("auto-memory failed for session %s", session_id)

        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            log.exception("enrichment commit failed for session %s", session_id)
            return None, None
    return artifact_id, memory_step


async def _audit_policy(
    user: User,
    request: Request,
    action: str,
    detail: str,
    *,
    metadata: dict | None = None,
) -> None:
    """Writes a policy event on its own connection.

    Separate from the turn's transaction: the record of a refusal must survive
    the request.
    """
    audit_target = (
        str(metadata["sessionId"]) if metadata and metadata.get("sessionId") else user.email
    )
    async with SessionLocal() as db:
        db.add(
            AuditEvent(
                actor_id=user.id,
                action=action,
                target=audit_target,
                detail=detail,
                event_metadata=metadata,
                ip=client_ip(request),
                severity="warn",
            )
        )
        await db.commit()


async def _run_page(
    *,
    outline_model: dict | None = None,
    #: The outline somebody approved, when this run is the second half of one.
    #: `None` means plan and offer; anything else means write exactly this.
    approved_plan: dict | None = None,
    #: False on the pass that follows "있는 자료로 진행", so the button that
    #: promises not to ask again keeps that promise. See the writers.
    may_ask: bool = True,
    #: The attachments the request was made with, carried so a proposal stored
    #: now can be written against the same files later.
    attachments: list[str] | None = None,
    #: What has been answered so far. Carried for the same reason: a proposal
    #: stored without them would forget which part of the file was asked for,
    #: and the next revision would quietly go back to reading the beginning.
    answers: dict[str, str] | None = None,
    user_id: str,
    api_key: str,
    session_id: str,
    model: dict,
    request: str,
    project_id: str | None,
    routing: dict[str, Any] | None = None,
    template: design_templates.DesignTemplate,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    design_tokens: dict[str, str] | None = None,
    skills_event: dict | None = None,
    context_steps: list[dict] | None = None,
    #: The composer's search toggle, for the surfaces that write a document.
    #:
    #: A document is not argued with the way a chat answer is: it is exported,
    #: attached to a mail, and read by people who were not here when it was
    #: written. So the writers research before they write, and this is what
    #: says whether they may — off for a strict-local route, which is given no
    #: network anywhere else either.
    web_search: bool = True,
    #: Pictures somebody agreed to on the second card. `None` on the planning
    #: pass, `[]` when the card was answered 그림 없이.
    #:
    #: Accepted by every document runner so the dispatch is uniform; only the
    #: report draws them today. A deck's pictures are a different question —
    #: `slide-image` already puts one on a slide, and a figure on every slide is
    #: not what anybody wants — so it takes the argument and ignores it until
    #: that question is answered.
    figures_plan: list[dict] | None = None,
    #: The model that draws them — the image default, not the writer's model.
    image_model: dict | None = None,
    project_sources: list[dict[str, Any]] | None = None,
) -> AsyncIterator[str]:
    """Drives one HTML artifact to completion and settles it.

    Same contract as `_run_deck` and `_run_report`: the page is an artifact,
    not a chat message. What differs is that the whole file is the output, so
    a half-written page is still stored — the blocks that failed are simply
    absent from it, which is what the reader sees and can ask to fix.
    """
    blocks: list[dict] = []
    proposal: dict | None = None
    questions: list[dict] | None = None
    html = ""
    usage = {"inputTokens": 0, "outputTokens": 0}
    doc_title = ""
    #: What the research pass read, kept so a report stores the same shelf the
    #: plain track does — the citations belong to the document, not to the
    #: track it happened to be written on.
    sources: list[dict] = []
    research_log: dict[str, Any] | None = None

    if routing:
        # Before the first block of the document, for the same reason chat
        # sends it first: the model badge on screen is wrong until it arrives.
        yield chat_service.sse({"type": "privacy_route", **routing})
    if skills_event:
        yield chat_service.sse(skills_event)
    for step in context_steps or ():
        yield chat_service.sse(_step_event(step))
    try:
        stream = page_service.write(
            web_search=web_search,
            request=request,
            approved_plan=approved_plan,
            may_ask=may_ask,
            model=model["id"],
            outline_model=(outline_model or {}).get("id", ""),
            api_key=api_key,
            template=template,
            tokens=design_tokens,
            trusted_context=trusted_context,
            untrusted_context=untrusted_context,
            project_sources=project_sources,
        )
        async for event in stream:
            if event["type"] in ("proposal", "needs"):
                # The turn stopped on purpose. Passed on so the browser can
                # draw it, and held so the block below stores it in place of an
                # artifact — which is what leaves the existing document alone.
                if event["type"] == "proposal":
                    proposal = event["plan"]
                else:
                    questions = event["questions"]
                yield chat_service.sse(event)
                continue
            if event["type"] == "page":
                html = event["html"]
                blocks = event["blocks"]
                continue
            if event["type"] == "sources":
                sources = list(event.get("sources") or [])
            if event["type"] == "research":
                research_log = dict(event.get("research") or {})
            if event["type"] == "title":
                doc_title = str(event.get("title") or "").strip()
            if event["type"] == "usage":
                usage = {k: v for k, v in event.items() if k != "type"}
                continue
            yield chat_service.sse(event)
    except Exception as exc:  # noqa: BLE001 — the turn must still settle
        log.exception("page generation crashed for session %s", session_id)
        yield chat_service.sse(_error_event("문서를 만들지 못했습니다.", exc))

    # Planned or asked, and nothing written. Stored on the session and settled
    # here, and then the artifact block below is skipped entirely — which is
    # what actually keeps the document already on screen from being replaced by
    # a run nobody confirmed.
    if proposal is not None or questions is not None:
        await _settle_plan_turn(
            session_id=session_id,
            user_id=user_id,
            request=request,
            attachments=list(attachments or []),
            answers=dict(answers or {}),
            model=model,
            outline_model=outline_model,
            usage=usage,
            proposal=proposal,
            questions=questions,
        )
        yield chat_service.sse({"type": "usage", **usage, "credits": 0})
        yield chat_service.sse({"type": "done"})
        return

    written = page_service.filled(blocks)
    credits = (
        0
        if not written
        else charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
        # The planner's own tokens at the planner's own price, when one ran.
        + charge_for_tokens(
            outline_model or model,
            usage.get("outlineInputTokens", 0),
            usage.get("outlineOutputTokens", 0),
        )
    )

    artifact_id: str | None = None
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        user = await db.get(User, user_id)
        if session is not None and user is not None:
            title = (doc_title or session.title or request.strip()[:60] or template.name)[:200]
            if written and html and session.kind is SessionKind.report:
                # A report written into a 서식 is a report, not a file.
                #
                # It used to be stored as an `html` artifact — the finished
                # document, rendered — and that is what made choosing a 서식 a
                # one-way door: the panel for an `html` artifact is a sandboxed
                # frame, so the document could be read, exported and rewritten
                # a block at a time, but never typed in. Choosing no 서식 gave
                # prose that could be edited and had no shape. Neither could
                # become the other.
                #
                # Stored as sections, both are the same document. The blocks
                # the writer produced are already HTML, so they become
                # `format: "html"` sections with nothing lost in between, and
                # the page view re-renders them through whichever 서식 is
                # chosen — including a different one, later.
                #
                # The deck surface keeps the `html` artifact below: a slide is
                # not a section, and its panel is a stage rather than a page.
                artifact_id = await _store_document(
                    db,
                    session,
                    user_id=user_id,
                    project_id=project_id,
                    kind=ArtifactKind.report,
                    title=title,
                    summary=_regeneration_summary(request),
                    data={
                        "kind": "report",
                        "templateId": template.id,
                        "sections": [
                            {
                                "id": f"s{index}_{uuid4().hex[:6]}",
                                "heading": block["title"],
                                "level": 1,
                                "status": "done",
                                "content": block["html"],
                                "format": "html",
                            }
                            for index, block in enumerate(blocks)
                            if block.get("layout") != "cover"
                        ],
                        "sources": sources,
                        **({"research": research_log} if research_log is not None else {}),
                        "citationStyle": "APA",
                        "lint": lint.wire(
                            lint.check(
                                lint.from_blocks(blocks),
                                slides=False,
                                limits=template.limits,
                            )
                        ),
                        "wordCount": sum(
                            len(re.sub(r"<[^>]+>", " ", b.get("html") or "").split())
                            for b in blocks
                        ),
                        **({"design": design_tokens} if design_tokens else {}),
                    },
                )
                session.artifact_id = artifact_id
                session.pending = None
            elif written and html:
                artifact_id = await _store_document(
                    db,
                    session,
                    user_id=user_id,
                    project_id=project_id,
                    kind=ArtifactKind.html,
                    title=title,
                    summary=_regeneration_summary(request),
                    data={
                        "kind": "html",
                        "language": "html",
                        "content": html,
                        "templateId": template.id,
                        # Blocks are the source, `content` what they render to. Both kept whole so
                        # one block can be rewritten without parsing the finished file back apart.
                        "blocks": [
                            {"title": b["title"], "layout": b["layout"], "html": b["html"]}
                            for b in blocks
                        ],
                        # Read back before it is stored. Costs no model call,
                        # so it runs on every document; acting on what it finds
                        # stays the person's decision.
                        "lint": lint.wire(
                            lint.check(
                                lint.from_blocks(blocks),
                                slides=template.kind == "deck",
                                limits=template.limits,
                            )
                        ),
                        **({"design": design_tokens} if design_tokens else {}),
                    },
                )
                session.artifact_id = artifact_id
                # The proposal has become the document. Nothing is waiting any
                # more, and leaving it set would make the next plain message
                # read as a note on an outline that has already been written.
                session.pending = None

                db.add(
                    Message(
                        session_id=session_id,
                        role=Role.assistant,
                        content=f"{template.name}으로 {len(written)}개 부분을 작성했습니다.",
                        usage={**usage, "credits": credits},
                        model=model["id"],
                        steps=_prelude_steps(skills_event, context_steps) or None,
                        routing=routing,
                    )
                )
                settle(
                    db,
                    user,
                    credits,
                    reason="page.generate",
                    session_id=session_id,
                    model=model["id"],
                )
            session.updated_at = utcnow()
            db.add(session)
            await db.commit()

    if artifact_id:
        yield chat_service.sse({"type": "artifact", "artifactId": artifact_id})
    yield chat_service.sse({"type": "usage", **usage, "credits": credits})
    yield chat_service.sse({"type": "done"})


async def _run_deck(
    *,
    outline_model: dict | None = None,
    #: The outline somebody approved, when this run is the second half of one.
    #: `None` means plan and offer; anything else means write exactly this.
    approved_plan: dict | None = None,
    #: False on the pass that follows "있는 자료로 진행", so the button that
    #: promises not to ask again keeps that promise. See the writers.
    may_ask: bool = True,
    #: The attachments the request was made with, carried so a proposal stored
    #: now can be written against the same files later.
    attachments: list[str] | None = None,
    #: What has been answered so far. Carried for the same reason: a proposal
    #: stored without them would forget which part of the file was asked for,
    #: and the next revision would quietly go back to reading the beginning.
    answers: dict[str, str] | None = None,
    user_id: str,
    api_key: str,
    session_id: str,
    model: dict,
    request: str,
    project_id: str | None,
    routing: dict[str, Any] | None = None,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    design_tokens: dict[str, str] | None = None,
    skills_event: dict | None = None,
    context_steps: list[dict] | None = None,
    #: The composer's search toggle, for the surfaces that write a document.
    #:
    #: A document is not argued with the way a chat answer is: it is exported,
    #: attached to a mail, and read by people who were not here when it was
    #: written. So the writers research before they write, and this is what
    #: says whether they may — off for a strict-local route, which is given no
    #: network anywhere else either.
    web_search: bool = True,
    #: Pictures somebody agreed to on the second card. `None` on the planning
    #: pass, `[]` when the card was answered 그림 없이.
    #:
    #: Accepted by every document runner so the dispatch is uniform; only the
    #: report draws them today. A deck's pictures are a different question —
    #: `slide-image` already puts one on a slide, and a figure on every slide is
    #: not what anybody wants — so it takes the argument and ignores it until
    #: that question is answered.
    figures_plan: list[dict] | None = None,
    #: The model that draws them — the image default, not the writer's model.
    image_model: dict | None = None,
) -> AsyncIterator[str]:
    """Drives one deck to completion and settles it.

    Same contract as `_run_report`: the deck is an artifact, not a chat message.
    """
    slides: list[dict] = []
    proposal: dict | None = None
    questions: list[dict] | None = None
    usage = {"inputTokens": 0, "outputTokens": 0}
    doc_title = ""

    if routing:
        # Before the first block of the document, for the same reason chat
        # sends it first: the model badge on screen is wrong until it arrives.
        yield chat_service.sse({"type": "privacy_route", **routing})
    if skills_event:
        yield chat_service.sse(skills_event)
    for step in context_steps or ():
        yield chat_service.sse(_step_event(step))
    try:
        stream = deck_service.write(
            web_search=web_search,
            figures_plan=figures_plan,
            image_model=image_model,
            request=request,
            approved_plan=approved_plan,
            may_ask=may_ask,
            model=model["id"],
            outline_model=(outline_model or {}).get("id", ""),
            api_key=api_key,
            trusted_context=trusted_context,
            untrusted_context=untrusted_context,
            tokens=design_tokens,
        )
        async for event in stream:
            if event["type"] in ("proposal", "needs"):
                # The turn stopped on purpose. Passed on so the browser can
                # draw it, and held so the block below stores it in place of an
                # artifact — which is what leaves the existing document alone.
                if event["type"] == "proposal":
                    proposal = event["plan"]
                else:
                    questions = event["questions"]
                yield chat_service.sse(event)
                continue
            if event["type"] == "deck":
                slides = event["slides"]
                continue
            if event["type"] == "title":
                doc_title = str(event.get("title") or "").strip()
            if event["type"] == "usage":
                usage = {k: v for k, v in event.items() if k != "type"}
                continue
            yield chat_service.sse(event)
    except Exception as exc:  # noqa: BLE001 — the turn must still settle
        log.exception("deck generation crashed for session %s", session_id)
        yield chat_service.sse(_error_event("슬라이드를 만들지 못했습니다.", exc))

    # Planned or asked, and nothing written. Stored on the session and settled
    # here, and then the artifact block below is skipped entirely — which is
    # what actually keeps the document already on screen from being replaced by
    # a run nobody confirmed.
    if proposal is not None or questions is not None:
        drawn = (proposal or {}).pop("figures", None)
        await _settle_plan_turn(
            session_id=session_id,
            user_id=user_id,
            request=request,
            attachments=list(attachments or []),
            answers=dict(answers or {}),
            model=model,
            outline_model=outline_model,
            usage=usage,
            proposal=proposal,
            questions=questions,
            figures={"plan": proposal, **drawn} if drawn and proposal else None,
        )
        yield chat_service.sse({"type": "usage", **usage, "credits": 0})
        yield chat_service.sse({"type": "done"})
        return

    written = deck_service.filled(slides)
    credits = (
        0
        if not written
        else charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
        # The planner's own tokens at the planner's own price, when one ran.
        + charge_for_tokens(
            outline_model or model,
            usage.get("outlineInputTokens", 0),
            usage.get("outlineOutputTokens", 0),
        )
    )

    artifact_id: str | None = None
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        user = await db.get(User, user_id)
        if session is not None and user is not None:
            title = (doc_title or session.title or request.strip()[:60] or "슬라이드")[:200]
            if written:
                artifact_design = design_tokens
                if not artifact_design:
                    requested_style = str(
                        (approved_plan or {}).get("visualStyle")
                        or design_service.visual_style_for(request)
                    )
                    if requested_style != "editorial":
                        artifact_design = design_service.normalise_tokens(
                            {"visualStyle": requested_style}
                        )
                artifact_id = await _store_document(
                    db,
                    session,
                    user_id=user_id,
                    project_id=project_id,
                    kind=ArtifactKind.deck,
                    title=title,
                    summary=_regeneration_summary(request),
                    data={
                        "kind": "deck",
                        "theme": "기본",
                        # Copied onto the artifact rather than resolved at export time: a deck
                        # presented last month should not repaint itself when the project changes.
                        **({"design": artifact_design} if artifact_design else {}),
                        "lint": lint.wire(lint.check(lint.from_slides(slides), slides=True)),
                        # Every slide, including unwritten ones — a gap stays
                        # visible so it can be fixed.
                        "slides": slides,
                    },
                )
                session.artifact_id = artifact_id
                # The proposal has become the document. Nothing is waiting any
                # more, and leaving it set would make the next plain message
                # read as a note on an outline that has already been written.
                session.pending = None

                db.add(
                    Message(
                        session_id=session_id,
                        role=Role.assistant,
                        content=f"{len(written)}장짜리 슬라이드를 만들었습니다.",
                        usage={**usage, "credits": credits},
                        model=model["id"],
                        steps=_prelude_steps(skills_event, context_steps) or None,
                        routing=routing,
                    )
                )
                settle(
                    db,
                    user,
                    credits,
                    reason="deck.generate",
                    session_id=session_id,
                    model=model["id"],
                )
            else:
                # Nothing was written, and until now nothing was recorded of it
                # either: the error went out as one SSE frame and the turn left
                # no trace at all. On the next load the person found their own
                # question, no answer, and no way to tell whether it had been
                # asked — after a model call they had already paid for. The
                # surfaces already know how to draw this; it only had to be
                # written down.
                db.add(
                    Message(
                        session_id=session_id,
                        role=Role.assistant,
                        content="",
                        failure=TurnFailure.no_answer,
                        model=model["id"],
                        steps=_prelude_steps(skills_event, context_steps) or None,
                        routing=routing,
                    )
                )
            session.updated_at = utcnow()
            db.add(session)
            await db.commit()

    if artifact_id:
        yield chat_service.sse({"type": "artifact", "artifactId": artifact_id})
    yield chat_service.sse({"type": "usage", **usage, "credits": credits})
    yield chat_service.sse({"type": "done"})


async def _revise_document(
    *,
    user_id: str,
    api_key: str,
    session_id: str,
    model: dict,
    instruction: str,
    routing: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """One instruction, applied to the document already on screen.

    This is the loop the document surfaces did not have. Everything a person
    could do to their document, they did with a panel button and a note in a
    box; everything they typed in the chat planned a new document and offered
    to replace this one. Two windows that could not see each other.

    The shape is the same for a report and a deck because the difference is
    only what a part is called. `services.revise` reads the instruction against
    the document's own outline and says which parts it lands on; the surfaces
    rewrite those parts with the machinery they already had.

    The original request travels with it. A section fixed in isolation forgets
    what the document is about, and the numbered citations in its prose would
    renumber against an empty shelf.

    Every failure falls back to saying so rather than to regenerating: a
    revision that quietly became a new document is the behaviour this replaces.
    """
    usage = {"inputTokens": 0, "outputTokens": 0}
    if routing:
        yield chat_service.sse({"type": "privacy_route", **routing})

    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        artifact = await db.get(Artifact, session.artifact_id) if session else None
        if session is None or artifact is None or not artifact.data:
            yield chat_service.sse({"type": "error", "message": "고칠 문서를 찾지 못했습니다."})
            yield chat_service.sse({"type": "done"})
            return
        kind = artifact.kind
        data = dict(artifact.data)
        request = str(session.title or "")
        title = artifact.title or ""

    is_deck = kind is ArtifactKind.deck
    parts = list(data.get("slides") if is_deck else data.get("sections") or [])
    names = [str(p.get("title" if is_deck else "heading") or "") for p in parts]
    if not parts:
        yield chat_service.sse({"type": "error", "message": "고칠 내용이 없습니다."})
        yield chat_service.sse({"type": "done"})
        return

    yield chat_service.sse(
        {"type": "step", "id": "route", "label": "무엇을 고칠지 보는 중", "status": "running"}
    )
    plan = await revise.plan(
        message=instruction, title=title, parts=names, model=model["id"], api_key=api_key
    )
    usage["inputTokens"] += plan.usage["inputTokens"]
    usage["outputTokens"] += plan.usage["outputTokens"]
    if not plan.revises:
        # Read as a request for a different document. Said rather than acted
        # on: replacing what is on screen is the person's call, not a
        # classifier's.
        yield chat_service.sse(
            {"type": "step", "id": "route", "label": "새 문서 요청", "status": "done"}
        )
        yield chat_service.sse(
            {
                "type": "delta",
                "text": "이건 지금 문서를 고치는 요청으로 보이지 않습니다. "
                "새로 쓰려면 '새로 써 줘' 라고 알려 주세요.",
            }
        )
        yield chat_service.sse({"type": "usage", **usage, "credits": 0})
        yield chat_service.sse({"type": "done"})
        return

    yield chat_service.sse(
        {
            "type": "step",
            "id": "route",
            "label": revise.label(plan, names),
            "status": "done",
        }
    )

    changed = 0
    for index in plan.targets:
        part = parts[index]
        label = names[index] or f"{index + 1}"
        yield chat_service.sse(
            {"type": "step", "id": f"r{index}", "label": label, "status": "running"}
        )
        try:
            if is_deck:
                written, spent = await deck_service.rewrite_slide(
                    request=request,
                    slides=parts,
                    target_id=str(part.get("id") or ""),
                    model=model["id"],
                    api_key=api_key,
                    note=plan.note,
                )
                parts[index] = written
                yield chat_service.sse({"type": "slide", "slide": written, "done": True})
            else:
                body, spent = await report_service.rewrite_section(
                    request=request,
                    heading=label,
                    sections=richtext.normalise(parts),
                    target_id=str(part.get("id") or ""),
                    model=model["id"],
                    api_key=api_key,
                    note=plan.note,
                    sources=list(data.get("sources") or []),
                )
                if not body.strip():
                    raise ValueError("빈 결과")
                # Back to Markdown: what the model wrote *is* Markdown, and
                # leaving an old `html` flag on would render `**가**` as
                # literal asterisks. See `services/richtext`.
                parts[index] = {
                    **part,
                    "content": richtext.tidy_tables(body),
                    "format": "markdown",
                    "status": "done",
                }
                parts[index].pop("factCheck", None)
                yield chat_service.sse(
                    {
                        "type": "section",
                        "sectionId": part.get("id"),
                        "heading": label,
                        "content": body,
                        "done": True,
                    }
                )
        except Exception as exc:  # noqa: BLE001 — one bad part is not a failed turn
            log.warning("revision of %r failed: %s", label, exc)
            yield chat_service.sse(
                {"type": "step", "id": f"r{index}", "label": label, "status": "error"}
            )
            continue
        usage["inputTokens"] += spent["inputTokens"]
        usage["outputTokens"] += spent["outputTokens"]
        changed += 1
        yield chat_service.sse(
            {"type": "step", "id": f"r{index}", "label": label, "status": "done"}
        )

    if not changed:
        yield chat_service.sse({"type": "error", "message": "고치지 못했습니다."})
        yield chat_service.sse({"type": "usage", **usage, "credits": 0})
        yield chat_service.sse({"type": "done"})
        return

    credits = charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
    artifact_id: str | None = None
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        user = await db.get(User, user_id)
        if session is not None and user is not None:
            data["slides" if is_deck else "sections"] = parts
            if not is_deck:
                data["wordCount"] = report_service.word_count(parts)
                data["lint"] = lint.wire(lint.check(lint.from_sections(parts)))
            # `_store_document` snapshots the previous body, so 저장 시점 has
            # the document as it stood before this instruction.
            artifact_id = await _store_document(
                db,
                session,
                user_id=user_id,
                project_id=session.project_id,
                kind=kind,
                title=title or "문서",
                summary=instruction.strip()[:80] or "문서 수정",
                data=data,
            )
            session.artifact_id = artifact_id
            db.add(
                Message(
                    session_id=session_id,
                    role=Role.assistant,
                    content=f"{changed}곳을 고쳤습니다.",
                    usage={**usage, "credits": credits},
                    model=model["id"],
                    routing=routing,
                )
            )
            settle(
                db,
                user,
                credits,
                reason="document.revise",
                session_id=session_id,
                model=model["id"],
                surface=session.kind.value,
            )
            session.updated_at = utcnow()
            db.add(session)
            await db.commit()

    if artifact_id:
        yield chat_service.sse({"type": "artifact", "artifactId": artifact_id})
    yield chat_service.sse({"type": "usage", **usage, "credits": credits})
    yield chat_service.sse({"type": "done"})


async def _run_report(
    *,
    outline_model: dict | None = None,
    #: The outline somebody approved, when this run is the second half of one.
    #: `None` means plan and offer; anything else means write exactly this.
    approved_plan: dict | None = None,
    #: False on the pass that follows "있는 자료로 진행", so the button that
    #: promises not to ask again keeps that promise. See the writers.
    may_ask: bool = True,
    #: The attachments the request was made with, carried so a proposal stored
    #: now can be written against the same files later.
    attachments: list[str] | None = None,
    #: What has been answered so far. Carried for the same reason: a proposal
    #: stored without them would forget which part of the file was asked for,
    #: and the next revision would quietly go back to reading the beginning.
    answers: dict[str, str] | None = None,
    user_id: str,
    api_key: str,
    session_id: str,
    model: dict,
    request: str,
    project_id: str | None,
    routing: dict[str, Any] | None = None,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    design_tokens: dict[str, str] | None = None,
    skills_event: dict | None = None,
    context_steps: list[dict] | None = None,
    #: The composer's search toggle, for the surfaces that write a document.
    #:
    #: A document is not argued with the way a chat answer is: it is exported,
    #: attached to a mail, and read by people who were not here when it was
    #: written. So the writers research before they write, and this is what
    #: says whether they may — off for a strict-local route, which is given no
    #: network anywhere else either.
    web_search: bool = True,
    #: Pictures somebody agreed to on the second card. `None` on the planning
    #: pass, `[]` when the card was answered 그림 없이.
    #:
    #: Accepted by every document runner so the dispatch is uniform; only the
    #: report draws them today. A deck's pictures are a different question —
    #: `slide-image` already puts one on a slide, and a figure on every slide is
    #: not what anybody wants — so it takes the argument and ignores it until
    #: that question is answered.
    figures_plan: list[dict] | None = None,
    #: The model that draws them — the image default, not the writer's model.
    image_model: dict | None = None,
    project_sources: list[dict[str, Any]] | None = None,
) -> AsyncIterator[str]:
    """Drives one report to completion and settles it.

    The document is an artifact, not a chat message: it has versions and belongs
    on the artifacts screen.
    """
    sections: list[dict] = []
    proposal: dict | None = None
    questions: list[dict] | None = None
    usage = {"inputTokens": 0, "outputTokens": 0}
    failed = False
    #: Written by the outline step. Empty when the model gave no title.
    doc_title = ""
    #: The shelf the sections cited from, kept so the artifact carries the same
    #: numbering the prose refers to.
    sources: list[dict] = []
    research_log: dict[str, Any] | None = None

    if routing:
        # Before the first block of the document, for the same reason chat
        # sends it first: the model badge on screen is wrong until it arrives.
        yield chat_service.sse({"type": "privacy_route", **routing})
    if skills_event:
        yield chat_service.sse(skills_event)
    for step in context_steps or ():
        yield chat_service.sse(_step_event(step))
    try:
        stream = report_service.write(
            web_search=web_search,
            figures_plan=figures_plan,
            image_model=image_model,
            request=request,
            approved_plan=approved_plan,
            may_ask=may_ask,
            model=model["id"],
            outline_model=(outline_model or {}).get("id", ""),
            api_key=api_key,
            trusted_context=trusted_context,
            untrusted_context=untrusted_context,
            project_sources=project_sources,
        )
        async for event in stream:
            if event["type"] in ("proposal", "needs"):
                # The turn stopped on purpose. Passed on so the browser can
                # draw it, and held so the block below stores it in place of an
                # artifact — which is what leaves the existing document alone.
                if event["type"] == "proposal":
                    proposal = event["plan"]
                else:
                    questions = event["questions"]
                yield chat_service.sse(event)
                continue
            if event["type"] == "report":
                sections = event["sections"]
                continue
            if event["type"] == "sources":
                sources = list(event.get("sources") or [])
                # Forwarded too: the panel shows the shelf while the sections
                # are still being written.
            if event["type"] == "research":
                research_log = dict(event.get("research") or {})
            if event["type"] == "title":
                doc_title = str(event.get("title") or "").strip()
                # Forwarded: until it arrives the panel heads the draft with
                # the request.
            if event["type"] == "usage":
                usage = {k: v for k, v in event.items() if k != "type"}
                continue
            if event["type"] == "error":
                failed = True
            yield chat_service.sse(event)
    except Exception as exc:  # noqa: BLE001 — the turn must still settle
        log.exception("report generation crashed for session %s", session_id)
        failed = True
        yield chat_service.sse(_error_event("보고서를 만들지 못했습니다.", exc))

    # Planned or asked, and nothing written. Stored on the session and settled
    # here, and then the artifact block below is skipped entirely — which is
    # what actually keeps the document already on screen from being replaced by
    # a run nobody confirmed.
    if proposal is not None or questions is not None:
        # The outline card and the figure card are two questions, asked in
        # order. The planner proposes both at once because it has the outline
        # in front of it; the second is held here and asked only once the first
        # is answered, so an expensive decision is never approved by a button
        # somebody pressed for a cheap one.
        drawn = (proposal or {}).pop("figures", None)
        await _settle_plan_turn(
            session_id=session_id,
            user_id=user_id,
            request=request,
            attachments=list(attachments or []),
            answers=dict(answers or {}),
            model=model,
            outline_model=outline_model,
            usage=usage,
            proposal=proposal,
            questions=questions,
            figures={"plan": proposal, **drawn} if drawn and proposal else None,
        )
        yield chat_service.sse({"type": "usage", **usage, "credits": 0})
        yield chat_service.sse({"type": "done"})
        return

    written = [s for s in sections if (s.get("content") or "").strip()]
    credits = (
        0
        if not written
        else charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
        # The planner's own tokens at the planner's own price, when one ran.
        + charge_for_tokens(
            outline_model or model,
            usage.get("outlineInputTokens", 0),
            usage.get("outlineOutputTokens", 0),
        )
    )

    artifact_id: str | None = None
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        user = await db.get(User, user_id)
        if session is not None and user is not None:
            # Generated title first: `session.title` is the conversation title
            # model's output and reads as the raw prompt on a cover page.
            title = (doc_title or session.title or request.strip()[:60] or "보고서")[:200]
            if written:
                artifact_design = design_tokens
                if not artifact_design:
                    requested_style = str(
                        (approved_plan or {}).get("visualStyle")
                        or design_service.visual_style_for(request)
                    )
                    if requested_style != "editorial":
                        artifact_design = design_service.normalise_tokens(
                            {"visualStyle": requested_style}
                        )
                artifact_id = await _store_document(
                    db,
                    session,
                    user_id=user_id,
                    project_id=project_id,
                    kind=ArtifactKind.report,
                    title=title,
                    summary=_regeneration_summary(request),
                    data={
                        "sections": [
                            {
                                "id": s["id"],
                                "heading": s["heading"],
                                "level": s.get("level", 1),
                                "status": "done" if (s.get("content") or "").strip() else "pending",
                                "content": s.get("content") or "",
                            }
                            for s in sections
                        ],
                        "sources": sources,
                        # What the document was asked for, kept with it: a
                        # section rewritten later is held to the same numbers.
                        "request": request[:4000],
                        **({"research": research_log} if research_log is not None else {}),
                        "lint": lint.wire(lint.check(lint.from_sections(sections))),
                        # Same snapshot rule as the deck: the exporters read
                        # this, not the project the report came from.
                        **({"design": artifact_design} if artifact_design else {}),
                        "citationStyle": "APA",
                        "wordCount": report_service.word_count(sections),
                    },
                )
                session.artifact_id = artifact_id
                # The proposal has become the document. Nothing is waiting any
                # more, and leaving it set would make the next plain message
                # read as a note on an outline that has already been written.
                session.pending = None

                # Short transcript entry, so a reopened session shows what was
                # asked and what came of it.
                db.add(
                    Message(
                        session_id=session_id,
                        role=Role.assistant,
                        content=f"{len(written)}개 섹션으로 보고서를 작성했습니다.",
                        usage={**usage, "credits": credits},
                        model=model["id"],
                        steps=_prelude_steps(skills_event, context_steps) or None,
                        routing=routing,
                    )
                )
                settle(
                    db,
                    user,
                    credits,
                    reason="report.generate",
                    session_id=session_id,
                    model=model["id"],
                )
            session.updated_at = utcnow()
            db.add(session)
            await db.commit()

    if artifact_id:
        yield chat_service.sse({"type": "artifact", "artifactId": artifact_id})
    yield chat_service.sse({"type": "usage", **usage, "credits": credits})
    yield chat_service.sse({"type": "done"})
    if failed and not written:
        return
