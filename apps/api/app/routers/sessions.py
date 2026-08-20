"""Sessions, messages, and the chat stream.

Ordering rules for a streaming turn:

* user message committed before the upstream call
* assistant message and credit deduction committed together
* no charge for a turn that produced no output
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, update
from sqlmodel import col, delete, select

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
from app.models.workspace import AgentVisibility, Artifact, ArtifactKind, Project, StoredFile
from app.schemas.auth import Preferences
from app.schemas.chat import (
    AudioRequest,
    ChooseVariant,
    CompareRequest,
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
    governance,
    imagegen,
    lint,
    settings_store,
)
from app.services import agent as agent_service
from app.services import auto_memory as auto_memory_service
from app.services import chat as chat_service
from app.services import deck as deck_service
from app.services import design as design_service
from app.services import litellm as litellm_service
from app.services import models as model_service
from app.services import page as page_service
from app.services import report as report_service
from app.services.context import build_messages
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
#: external ones because it may fall back to them mid-turn, and `unknown` with
#: them because a boundary nobody could establish is not a boundary.
_BOUNDARY_RANK = {"self_hosted": 0, "hybrid": 1, "external": 1, "unknown": 1}


def _widens_boundary(candidate: dict, chosen: dict) -> bool:
    """True when `candidate` would send further than `chosen` already does.

    Used for the outline model, which is the one call in a document a policy
    row can redirect. The person picked the writer, or privacy did; neither
    should be undone by a setting on another screen.
    """
    if _BOUNDARY_RANK.get(str(candidate.get("dataBoundary")), 1) > _BOUNDARY_RANK.get(
        str(chosen.get("dataBoundary")), 1
    ):
        return True
    # Strict-local is a stronger claim than self-hosted: no external fallback
    # exists for it at all.
    return bool(chosen.get("strictLocal")) and not candidate.get("strictLocal")


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

    A row rather than an id: its tokens are billed at its own price, and a
    call billed at the writer's price is a ledger that says the wrong thing
    about where the money went.

    A whole function for one policy field because everything it has to refuse
    is easy to forget: the account's allowlist, the surface, a turn privacy
    routed inward, and a boundary the writer does not already have. The outline
    carries the same request and context the body does, so a setting on the
    admin screen must not widen where any of it goes.
    """
    if not wanted or strict_local:
        return None
    planner = model_service.find(_allowed_models(user, catalogue, kind=kind), str(wanted))
    if planner is None or _widens_boundary(planner, writer):
        log.info("outline model %s unusable here", wanted)
        return None
    return planner


async def _enrichment_model(
    writer: dict, *, strict_local: bool, disable_fallbacks: bool
) -> dict:
    """The catalogue row that titles the session and extracts its memories.

    A row rather than an id, for the reason `_planner_model` returns one: this
    is a second model doing side work, and its tokens are billed at its own
    price. Titles and memory are the only calls a person never asks for, which
    makes billing them at the answer's price the easiest ledger line to
    disbelieve.

    Most deployments point `title_model` at a free self-hosted model, and then
    the charge is zero without anybody having to write a rule for it. Where one
    is not configured the work falls to the turn's own model and is billed
    there — the alternative, refusing to run at all above zero cost, would trade
    a working feature for a credit or two.
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
    classifier_model: str | None = None,
    classification: adaptive_routing.Classification | None = None,
) -> dict[str, Any]:
    route: dict[str, Any] = {
        "mode": "auto",
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
    """Returns an Auto turn's effective model and value-free route metadata."""
    if not policy.adaptive_routing_enabled:
        return quality_model, _cost_routing(
            decision="bypassed",
            reason_code="disabled",
            requested_model=quality_model,
            selected_model=quality_model,
        )
    if unsupported_reason:
        return quality_model, _cost_routing(
            decision="bypassed",
            reason_code=unsupported_reason,
            requested_model=quality_model,
            selected_model=quality_model,
        )

    allowed = set(user.allowed_models or [])
    classifier_id = str(policy.adaptive_classifier_model_id or "")
    classifier_model = model_service.find(catalogue, classifier_id)
    if not adaptive_routing.classifier_is_usable(
        classifier_model, allowed_model_ids=allowed
    ):
        return quality_model, _cost_routing(
            decision="classifier_unavailable",
            reason_code="classifier_unavailable",
            requested_model=quality_model,
            selected_model=quality_model,
            classifier_model=classifier_id or None,
        )

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
            decision="kept_quality",
            reason_code="no_economy_model",
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
            decision="classifier_unavailable",
            reason_code="classifier_unavailable",
            requested_model=quality_model,
            selected_model=quality_model,
            classifier_model=classifier_id,
        )
    if (
        classification.complexity != "low"
        or classification.confidence < adaptive_routing.MIN_LOW_CONFIDENCE
    ):
        if classification.complexity == "high":
            reason = "high_complexity"
        elif classification.complexity == "uncertain":
            reason = "uncertain"
        else:
            reason = "low_confidence"
        return quality_model, _cost_routing(
            decision="kept_quality",
            reason_code=reason,
            requested_model=quality_model,
            selected_model=quality_model,
            classifier_model=classifier_id,
            classification=classification,
        )

    selected = candidates[0]
    return selected, _cost_routing(
        decision="routed",
        reason_code="low_complexity",
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

    Only chat resolves privacy, so only chat had somewhere to record that the
    model the turn asked for is no longer the model that answered. A report or
    a deck made the same substitution in silence. This is the note chat stores,
    cut back to what a substitution on its own knows: no findings, no action,
    no decision — just the two ids, which is the comparison the transcript's
    badge makes.
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
        source = "skills" if block.source.startswith("skill:") else source_kinds.get(
            block.source, block.source
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


def _masked_outbound_context(
    history: list[str], extra: list[str], *, legacy: bool = False
) -> tuple[list[str], list[str]]:
    """Masks each outbound context collection exactly once."""
    return _mask_list(history, legacy=legacy), _mask_list(extra, legacy=legacy)


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
        "detail": (
            " · ".join(names)
            + f" · 약 {int(event.get('estimatedTokens') or 0):,} 토큰"
        ),
    }


#: Each says what one file gave up; a per-file line is the point, because
#: "3개 중 1개" does not tell anybody which document the answer was missing.
_FILE_NOTE = {
    "truncated": "{name} {kept:,}자만 반영",
    "omitted": "{name} 분량을 넘겨 제외",
    "unreadable": "{name} 읽지 못함",
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
    short = [file for file in files if file.state != "included"]
    # Cut and dropped are counted apart: a document that arrived at half length
    # and one that never arrived are different things to have been answered
    # without, and one number covering both would hide the worse of them.
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
    notes = [
        _FILE_NOTE[file.state].format(name=file.name, kept=file.kept_chars) for file in short
    ]
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
        # The client rewrites this line in the reader's language, so it needs
        # the number the Korean sentence above was built from rather than the
        # sentence.
        "totalMemories": workspace.total_memories,
    }


def _context_steps(workspace: WorkspaceContext) -> list[dict]:
    """What the turn was handed but never said out loud.

    Memories, attachments and project knowledge all reach the model without
    passing through the conversation, so nothing on screen could tell a person
    which of them shaped the answer — or that the document they had just
    watched a chip appear for was cut in half to fit. Each becomes one quiet
    line in the timeline the applied skills already use.
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
    made = await _made(db, [sid for sid in ids if sid not in previews])
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

    A picture or clip surface runs no turn, so these rows have no last message
    to put under their title. What they do have is the thing they made, and its
    shape is the one fact the title — the person's own prompt — does not
    already carry.

    Asked for once for the whole page, like the previews above, and only for
    the ids the message query returned nothing for: a report's transcript is
    the better subtitle wherever there is one.
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
    # Also on the single-session response, and not only in the list: opening a
    # conversation replaces the row the list handed the client, so leaving this
    # out here would blank the line under a title the moment somebody looked at
    # what it names.
    made = {} if history else await _made(db, [session_id])
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
    if payload.routing_mode == RoutingMode.auto and payload.kind is not SessionKind.chat:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="auto_routing_chat_only",
        )
    if payload.model == "auto":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="auto_is_not_a_model_id",
        )
    if payload.routing_mode == RoutingMode.auto:
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


async def _project_render_template(
    db: DbSession, project_id: str | None, kind: SessionKind
) -> str | None:
    """The format the project this session is starting in works in, if any.

    Copied onto the row rather than read through the project on every turn.
    The composer shows the shape it is about to write in, and a project whose
    default changes afterwards must not silently change the shape of a
    conversation somebody is already having — the same reason the composer's
    own pick is stored here rather than resent.

    Ownership is settled by `_validate_session_links` before this is asked.
    """
    if not project_id:
        return None
    project = await db.get(Project, project_id)
    return design_templates.default_for(project.render_templates, kind) if project else None


def _resolved_template_id(requested: str | None, kind: SessionKind) -> str | None:
    """A rendering template id this surface can actually use, or `None`.

    `""` and `None` both mean "no template" — the first is somebody clearing
    the choice, the second is a payload that did not mention it, and only the
    caller can tell those apart. An id that does not resolve is refused rather
    than dropped: a turn that quietly falls back to the built-in track produces
    a document in the wrong shape and bills for it.
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
    if changes.get("routing_mode") == RoutingMode.auto and session.kind is not SessionKind.chat:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="auto_routing_chat_only",
        )
    # A direct real-model selection is manual unless the caller explicitly
    # updates the quality ceiling and asks to keep Auto in the same patch.
    if "model" in changes and "routing_mode" not in changes:
        changes["routing_mode"] = RoutingMode.manual
    # Validate the effective post-patch state, including an unrelated update
    # to a session that is already Auto. A model-only patch becomes manual
    # above and intentionally keeps the historical manual-session contract.
    if changes.get("routing_mode", session.routing_mode) == RoutingMode.auto:
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
    await db.delete(session)
    await db.commit()


def _record_media(
    db: DbSession, session: ChatSession, prompt: str, artifact_id: str | None
) -> None:
    """Leaves the conversation a record of what was asked for and what came back.

    The writing surfaces get this from their turn: the message is stored, the
    title is set from it, and the finished document is hung on the session. A
    picture or a clip runs no turn, so until now nothing wrote any of it — the
    row stayed an untitled "새 작업" pointing at nothing, and a week later seven
    clips of the same request were one from another indistinguishable.

    No message is written here on purpose. This surface has no turn to record:
    the reply is an artifact, not a sentence, and a stored prompt with nothing
    answering it would render as a conversation that broke off. The prompt is
    kept where it can be read at a glance instead, as the name of the session.
    """
    if not session.title:
        # A title somebody chose is theirs. So, deliberately, is the one the
        # first prompt left behind: a second batch in the same session is more
        # of the same work, not a new subject, and renaming the row underneath
        # somebody mid-session is how a list stops being somewhere to look
        # things up.
        session.title = chat_service.provisional_title(prompt)
    if artifact_id:
        # None when every picture in the batch failed. The name still stands —
        # an attempt is a record of what was asked for, and the alternative is
        # the anonymous row this whole change exists to get rid of — but the
        # session must not point at anything, because nothing exists to point at.
        session.artifact_id = artifact_id
    # The sidebar sorts on this. Making something is the clearest case there is
    # of the conversation having been touched.
    session.updated_at = utcnow()
    db.add(session)


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
            key=lambda m: m["creditCost"],
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
                # What came back, beside what was asked for. The two disagree
                # often enough — the ratio is a phrase in the prompt, not a
                # parameter — that showing only the request is a claim the
                # picture does not back up.
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
        settle(db, user, charged, reason="image.generate", session_id=session.id)
    _record_media(db, session, payload.prompt, made[-1].id if made else None)
    await db.commit()
    for artifact in made:
        await db.refresh(artifact)

    if not made:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=failure or "image_failed"
        )
    return [ArtifactOut.of(a) for a in made]


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
            # What was asked for, beside what came back. A length is a phrase
            # in the prompt rather than a parameter, and the two disagree often
            # enough that showing only the request would be a claim the clip
            # does not support — `aspect` and `actualAspect` on the image
            # surface exist for the same reason.
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
        settle(db, user, charged, reason="audio.generate", session_id=session.id)
    _record_media(db, session, payload.prompt, artifact.id)
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
    # Artifacts outlive their conversation — detached, not deleted.
    await db.exec(update(Artifact).where(col(Artifact.session_id).in_(ids)).values(session_id=None))
    await db.exec(delete(ChatSession).where(col(ChatSession.id).in_(ids)))
    await db.commit()
    return {"deleted": len(ids)}


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(session_id: str, user: CurrentUser, db: DbSession):
    await _owned(db, user, session_id)
    return [MessageOut.of(m) for m in await _history(db, session_id)]


@router.post("/{session_id}/messages")
async def send_message(
    session_id: str, payload: SendMessage, request: Request, user: CurrentUser, db: DbSession
):
    session = await _owned(db, user, session_id)
    if session.kind not in (SessionKind.chat, SessionKind.report, SessionKind.slides):
        # Image and a/v are jobs with their own endpoints, not this path.
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="surface_not_implemented"
        )
    auto_turn = bool(
        session.kind is SessionKind.chat
        and session.routing_mode == RoutingMode.auto
        and payload.model is None
    )

    # Refused rather than ignored, and before any write: a turn that silently
    # falls back to the built-in track produces a document in the wrong shape
    # and bills for it.
    if payload.render_template_id is not None:
        session.render_template_id = _resolved_template_id(
            payload.render_template_id, session.kind
        )

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
    # Model precedence: turn override → session → agent. The agent supplies a
    # default, not a lock — which only means anything if a session started
    # against an agent is left without a model of its own, so the client does
    # not send one.
    try:
        agent_model, agent_tools, agent_temperature = await agent_settings(db, user, session)
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
    # The row the turn asked for, when it exists and the account may no longer
    # use it. A revocation is not a reason to answer silently at another price:
    # the id is kept so the routing metadata can report it as the requested
    # model and the transcript says a substitute ran.
    revoked_model = model if model is not None and model not in usable else None
    if model not in usable:
        model = None
    if model is None:
        # A stale session/agent id may fall back, but only inside the user's
        # allowed intersection.
        usable = sorted(usable, key=lambda m: m["creditCost"])
        if not usable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
            )
        model = usable[0]
    requested_model = model

    history = await _history(db, session_id)

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

    # Build every model-visible tool definition before the privacy decision and
    # before the first write. Agent shelf filenames/headings and connector JSON
    # schemas are outbound prompt data just as much as the user's sentence is.
    # Rebuilding this snapshot on every retry also binds a decision token to
    # connector/schema/shelf changes instead of a stale registry object.
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
    strict_candidate_available = requested_is_strict or any(
        model.get("id") in set(policy.privacy_safe_model_ids or []) and _strict_model(model)
        for model in catalogue_models
    ) or (
        auto_turn
        and any(
            model.get("id") in set(policy.adaptive_economy_model_ids or [])
            and _strict_model(model)
            for model in catalogue_models
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
            activated_skill_ids=payload.activated_skill_ids,
            starting_template_id=payload.starting_template_id,
            # Report and deck writers do not run the chat tool loop.
            available_tool_names=(
                {tool.name for tool in requested_tools}
                if session.kind is SessionKind.chat
                else set()
            ),
        )
    except WorkspaceContextError as exc:
        _raise_workspace_error(exc)

    if session.kind is SessionKind.chat:
        privacy_sources = _privacy_sources(content, history, workspace.blocks)
        if requested_tools:
            privacy_sources["tool_definitions"] = _tool_definition_source(requested_tools)
        # Auto always performs the deterministic full-envelope scan before a
        # classifier, key operation or model call, even when the organisation
        # has disabled the optional external-data decision UI.
        auto_preflight_findings = (
            governance.findings(privacy_sources, legacy=policy.pii_masking)
            if auto_turn
            else []
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
            # Name the model that was asked for, not the one that answered.
            # `actualModelChanged` in the transcript is exactly this comparison,
            # so restoring the pre-fallback id here is the whole of telling the
            # reader that a substitute ran — and it is stored on the message,
            # so it survives a reload the way every other route note does.
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
            outbound_history = _mask_list(
                outbound_history, legacy=policy.pii_masking
            )
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
            # Document surfaces now persist the same attachment metadata as
            # chat. A filename or extraction error is user content too, so the
            # legacy organisation-wide policy must cover it even when the
            # request sentence itself is clean.
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
        # Report and slide generation gained source-separated workspace context
        # with the skill runtime. Preserve the pre-existing always-mask policy
        # across those new outbound fields instead of protecting only the
        # request sentence.
        trusted_context = _mask_text_tree(trusted_context, governance.mask_legacy)
        untrusted_context = _mask_text_tree(untrusted_context, governance.mask_legacy)
    skills_event = workspace.skills_event()
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
    # slides resolve no privacy, so until this existed the swap happened on
    # those surfaces with nothing on the turn to carry the news. The document
    # runners take it from here and store it on their own message, so the badge
    # that already reads this comparison fires wherever the substitution
    # happened.
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
        # An agent allowlist may have removed the tool the toggle enabled, and a
        # strict-local route never had it. Carry the toggle through either way:
        # dropping it here produced an answer that read exactly like a searched
        # one, so the turn now says out loud that it did not search.
        web_search_available=any(t.name == "web_search" for t in tools),
        extra=trusted_context,
        untrusted_context=untrusted_context,
    )

    if auto_turn and not auto_preflight_findings:
        unsupported = bool(
            payload.attachments
            or payload.web_search
            or payload.activated_skill_ids
            or payload.starting_template_id
            or session.agent_id
            or session.project_id
        )
        # Economy turns are intentionally tool-free. The classifier sees the
        # complete quality-model envelope and exact tool definitions, but its
        # prompt says those tools will not exist after a route. This preserves
        # Auto for ordinary tool-capable models without letting a later tool
        # result overflow the candidate context window checked here.
        economy_messages = build_messages(
            session.kind,
            wire_history,
            with_tools=False,
            web_search=False,
            extra=trusted_context,
            untrusted_context=untrusted_context,
        )
        routed_model, cost_routing = await _resolve_cost_routing(
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
        if cost_routing.get("decision") == "routed":
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

    user_message = Message(
        session_id=session.id,
        role=Role.user,
        content=stored_content,
        attachments=attachment_meta,
        routing=privacy_resolution.routing if privacy_resolution else document_routing,
        started_from=workspace.started_from,
    )
    db.add(user_message)
    # A strict privacy route and SendMessage.model are turn-only overrides. An
    # Auto session's persisted model is its quality ceiling, changed through
    # PATCH rather than by a one-off message request. Preserve the historical
    # manual-session behaviour for clients that still select a model per turn.
    if session.routing_mode != RoutingMode.auto or payload.model is None:
        # A substitute is for this turn only. Writing it back would outlive the
        # revocation that caused it: the day the allowlist covers the person's
        # model again, the session would still be on the cheap one nobody
        # chose, and nothing would ever move it back.
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

    # A rendering template replaces the surface's built-in track. Resolved
    # before either of them, because picking one is a choice about what comes
    # out, not a hint the generator may take or leave.
    # The planner, when an administrator has named one.
    #
    # The outline call carries the same request and the same context the body
    # does, so it is subject to everything the body is subject to: this
    # account's allowlist, this surface, and the boundary the turn was decided
    # on. A turn that privacy routed to a strict-local model does not get a
    # planner at all — that route exists precisely so the text does not leave —
    # and a planner may never be less contained than the writer, or naming one
    # would quietly widen every document's egress. Anything that fails these
    # falls back to the writing model: a document planned slightly worse beats
    # a turn that fails, and beats one that leaks.
    outline_model = _planner_model(
        policy.outline_model_id,
        user=user,
        catalogue=catalogue_models,
        kind=session.kind.value,
        writer=model,
        strict_local=strict_local,
    )

    render_template = design_templates.get(session.render_template_id)
    if render_template is not None:
        return StreamingResponse(
            _run_page(
                user_id=user.id,
                api_key=api_key,
                session_id=session.id,
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
            _run_report(
                user_id=user.id,
                api_key=api_key,
                session_id=session.id,
                model=model,
                request=content,
                project_id=session.project_id,
                routing=document_routing,
                # The same blocks the chat surface gets. Without this a report
                # or a deck saw the request sentence alone — no project
                # instructions, no memories, no attached form.
                trusted_context=trusted_context,
                untrusted_context=untrusted_context,
                design_tokens=workspace.design_tokens,
                skills_event=skills_event,
                context_steps=context_steps,
                outline_model=outline_model,
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
            _run_deck(
                user_id=user.id,
                api_key=api_key,
                session_id=session.id,
                model=model,
                request=content,
                project_id=session.project_id,
                routing=document_routing,
                # The same blocks the chat surface gets. Without this a report
                # or a deck saw the request sentence alone — no project
                # instructions, no memories, no attached form.
                trusted_context=trusted_context,
                untrusted_context=untrusted_context,
                design_tokens=workspace.design_tokens,
                skills_event=skills_event,
                context_steps=context_steps,
                outline_model=outline_model,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return StreamingResponse(
        _run_turn(
            user_id=user.id,
            api_key=api_key,
            auto_memory=Preferences.of(user).auto_memory,
            session_id=session.id,
            model=model,
            messages=messages,
            tools=tools,
            tool_definitions=tool_definitions,
            # Sampling belongs to the agent, not to the surface. A turn with no
            # agent passes None and the upstream default stands, exactly as it
            # did before this was carried at all.
            temperature=agent_temperature,
            first_user_message=stored_content,
            # The question is already committed. A turn that produces no answer
            # has to come back and say so on this exact row, or the transcript
            # keeps a prompt with silence under it and no account of why.
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
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Without this, nginx buffers SSE.
            "X-Accel-Buffering": "no",
        },
    )


async def _run_turn(
    *,
    user_id: str,
    api_key: str,
    auto_memory: bool,
    session_id: str,
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

    ctx = ToolContext(user_id=user_id, session_id=session_id, api_key=api_key)
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
        async for event in agent_service.run_turn(
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
        ):
            if event["type"] == "delta":
                text_parts.append(event["text"])
            elif event["type"] == "step":
                # Stored without the SSE envelope key: `Step.type` in the UI is
                # a display category, not the event name.
                steps.append({k: v for k, v in event.items() if k != "type"})
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
    except chat_service.ChatStreamError as exc:
        log.warning("chat stream failed for session %s: %s", session_id, exc)
        failed = str(exc)
        yield chat_service.sse({"type": "error", "message": "모델 응답을 받지 못했습니다."})
    except Exception:  # noqa: BLE001 — turn still has to settle and close
        log.exception("chat stream crashed for session %s", session_id)
        failed = "internal_error"
        yield chat_service.sse({"type": "error", "message": "요청 처리 중 오류가 발생했습니다."})

    content = "".join(text_parts)
    stored_content = masker(content)[0] if mask_at_rest or tool_output_findings else content
    protect_persistence = mask_at_rest or bool(tool_output_findings)
    # What the guard took out of the answer on its way to the record. The
    # prompt's own masking is already told this way — the findings ride on the
    # message's routing and the bubble reads them off it — and the answer has
    # the same gap for the same reason: the browser keeps the streamed original
    # until the session is reopened, so a week later somebody copies what is on
    # screen and gets placeholders. What is stored stays masked; only the
    # silence about it ends here.
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
                    # Half an answer is worth keeping, and worth labelling. The
                    # browser already says the stream broke; storing it is what
                    # makes the same thing true tomorrow.
                    failure=TurnFailure.interrupted if failed else None,
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
                # There is no answer to store — a broken stream, a refusal, or a
                # model that returned nothing at all. Inventing an assistant
                # message here would put words in its mouth, so the question
                # carries the outcome instead: it is the row the reader is
                # actually looking at, and the row a retry is offered under.
                question = await db.get(Message, user_message_id) if user_message_id else None
                if question is not None:
                    question.failure = TurnFailure.no_answer
                    db.add(question)
            if title:
                session.title = title
            # Its own line rather than folded into the answer's figure: a
            # different model may have run it, the message's stored `credits`
            # has to keep explaining the message's own tokens, and the one
            # thing the ledger owes somebody who never asked for a title is a
            # row that says a title is what they paid for. Nothing is charged
            # when the title ran on free capacity, which is the usual case.
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
        yield chat_service.sse({"type": "artifact", "artifactId": new_artifact})
    if cost_routing:
        yield chat_service.sse({"type": "model_route", **cost_routing})
    yield chat_service.sse({"type": "usage", **usage, "credits": credits})
    if title:
        yield chat_service.sse({"type": "title", "title": title})
    yield chat_service.sse({"type": "done"})


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
    attachment_rows, attachment_meta = await _owned_attachments(
        db, user, payload.attachments
    )
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

    async with SessionLocal() as db:
        db.add(
            Message(
                session_id=session_id,
                role=Role.assistant,
                content=chosen["content"] if chosen else "",
                variants=variants,
                usage={"credits": total},
                steps=stored_prelude or None,
                model=chosen["actualModel"] if chosen else None,
                routing=stored_routing,
            )
        )
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

    yield chat_service.sse({"type": "done", "credits": total})


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

    All optional, and nothing here may raise — the turn is already stored.

    Returns the new artifact and, when auto-memory wrote anything, the timeline
    step saying so. The step is appended to the message row on the way out: the
    answer was durable before this ran, and a line that vanished on reload
    would be a worse account than none.
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
                # Charged whether or not a fact came out, at the model that
                # read the turn. The extractor runs on every turn once the
                # preference is on, and a run that decided there was nothing
                # to remember cost the same as one that wrote two rows.
                settle(
                    db,
                    user,
                    charge_for_tokens(
                        enrichment, spent["inputTokens"], spent["outputTokens"]
                    ),
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
) -> AsyncIterator[str]:
    """Drives one HTML artifact to completion and settles it.

    Same contract as `_run_deck` and `_run_report`: the page is an artifact,
    not a chat message. What differs is that the whole file is the output, so
    a half-written page is still stored — the blocks that failed are simply
    absent from it, which is what the reader sees and can ask to fix.
    """
    blocks: list[dict] = []
    html = ""
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
        stream = page_service.write(
            request=request,
            model=model["id"],
            outline_model=(outline_model or {}).get("id", ""),
            api_key=api_key,
            template=template,
            tokens=design_tokens,
            trusted_context=trusted_context,
            untrusted_context=untrusted_context,
        )
        async for event in stream:
            if event["type"] == "page":
                html = event["html"]
                blocks = event["blocks"]
                continue
            if event["type"] == "title":
                doc_title = str(event.get("title") or "").strip()
            if event["type"] == "usage":
                usage = {k: v for k, v in event.items() if k != "type"}
                continue
            yield chat_service.sse(event)
    except Exception:  # noqa: BLE001 — the turn must still settle
        log.exception("page generation crashed for session %s", session_id)
        yield chat_service.sse({"type": "error", "message": "문서를 만들지 못했습니다."})

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
            if written and html:
                artifact = Artifact(
                    user_id=user_id,
                    session_id=session_id,
                    project_id=project_id,
                    kind=ArtifactKind.html,
                    title=title,
                    data={
                        "kind": "html",
                        "language": "html",
                        "content": html,
                        "templateId": template.id,
                        # The blocks are the source and `content` is what they
                        # render to. Kept whole — markup included — so one of
                        # them can be rewritten without reading the finished
                        # file back apart and hoping the seams land where they
                        # did when it was assembled.
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
                db.add(artifact)
                await db.flush()
                artifact_id = artifact.id
                session.artifact_id = artifact_id

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
) -> AsyncIterator[str]:
    """Drives one deck to completion and settles it.

    Same contract as `_run_report`: the deck is an artifact, not a chat message.
    """
    slides: list[dict] = []
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
            request=request,
            model=model["id"],
            outline_model=(outline_model or {}).get("id", ""),
            api_key=api_key,
            trusted_context=trusted_context,
            untrusted_context=untrusted_context,
            tokens=design_tokens,
        )
        async for event in stream:
            if event["type"] == "deck":
                slides = event["slides"]
                continue
            if event["type"] == "title":
                doc_title = str(event.get("title") or "").strip()
            if event["type"] == "usage":
                usage = {k: v for k, v in event.items() if k != "type"}
                continue
            yield chat_service.sse(event)
    except Exception:  # noqa: BLE001 — the turn must still settle
        log.exception("deck generation crashed for session %s", session_id)
        yield chat_service.sse({"type": "error", "message": "슬라이드를 만들지 못했습니다."})

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
                artifact = Artifact(
                    user_id=user_id,
                    session_id=session_id,
                    project_id=project_id,
                    kind=ArtifactKind.deck,
                    title=title,
                    data={
                        "kind": "deck",
                        "theme": "기본",
                        # Copied onto the artifact rather than looked up at
                        # export time: the accent already works this way, and a
                        # deck presented last month should not repaint itself
                        # because the project changed its design system since.
                        **({"design": design_tokens} if design_tokens else {}),
                        "lint": lint.wire(
                            lint.check(lint.from_slides(slides), slides=True)
                        ),
                        # Every slide, including unwritten ones — a gap stays
                        # visible so it can be fixed.
                        "slides": slides,
                    },
                )
                db.add(artifact)
                await db.flush()
                artifact_id = artifact.id
                session.artifact_id = artifact_id

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
) -> AsyncIterator[str]:
    """Drives one report to completion and settles it.

    The document is an artifact, not a chat message: it has versions and belongs
    on the artifacts screen.
    """
    sections: list[dict] = []
    usage = {"inputTokens": 0, "outputTokens": 0}
    failed = False
    #: Written by the outline step. Empty when the model gave no title.
    doc_title = ""
    #: The shelf the sections cited from, kept so the artifact carries the same
    #: numbering the prose refers to.
    sources: list[dict] = []

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
            request=request,
            model=model["id"],
            outline_model=(outline_model or {}).get("id", ""),
            api_key=api_key,
            trusted_context=trusted_context,
            untrusted_context=untrusted_context,
        )
        async for event in stream:
            if event["type"] == "report":
                sections = event["sections"]
                continue
            if event["type"] == "sources":
                sources = list(event.get("sources") or [])
                # Forwarded too: the panel shows the shelf while the sections
                # are still being written.
            if event["type"] == "title":
                doc_title = str(event.get("title") or "").strip()
                # Forwarded — until this arrives the panel heads the draft with
                # the request.
            if event["type"] == "usage":
                usage = {k: v for k, v in event.items() if k != "type"}
                continue
            if event["type"] == "error":
                failed = True
            yield chat_service.sse(event)
    except Exception:  # noqa: BLE001 — the turn must still settle
        log.exception("report generation crashed for session %s", session_id)
        failed = True
        yield chat_service.sse({"type": "error", "message": "보고서를 만들지 못했습니다."})

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
                artifact = Artifact(
                    user_id=user_id,
                    session_id=session_id,
                    project_id=project_id,
                    kind=ArtifactKind.report,
                    title=title,
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
                        "lint": lint.wire(lint.check(lint.from_sections(sections))),
                        # Same snapshot rule as the deck: the exporters read
                        # this, not the project the report came from.
                        **({"design": design_tokens} if design_tokens else {}),
                        "citationStyle": "APA",
                        "wordCount": report_service.word_count(sections),
                    },
                )
                db.add(artifact)
                await db.flush()
                artifact_id = artifact.id
                session.artifact_id = artifact_id

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
