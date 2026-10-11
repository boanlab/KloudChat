"""Sessions, messages, and the chat stream.

Ordering rules for a streaming turn:

* user message committed before the upstream call
* assistant message and credit deduction committed together
* no charge for a turn that produced no output
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import and_, exists, func, or_
from sqlmodel import col, delete, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import logs
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.deps import CurrentUser, DbSession, client_ip
from app.models.chat import (
    ChatSession,
    Message,
    PipelineRun,
    Role,
    RoutingMode,
    SessionKind,
    TurnFailure,
    follow_document_title,
    owned_by,
)
from app.models.governance import Governance
from app.models.user import AuditEvent, User, UserRole, utcnow
from app.models.workspace import Agent as WorkspaceAgent
from app.models.workspace import (
    AgentVisibility,
    Artifact,
    ArtifactKind,
    ArtifactVersion,
    DesignSystem,
    Job,
    Memory,
    MemoryType,
    Project,
    Skill,
    SkillSource,
    StoredFile,
)
from app.models.workspace import Visibility as SkillVisibility
from app.pipeline import chat_stages, export_stages, revise_stages
from app.pipeline import registry as pipeline_registry
from app.pipeline import runs as pipeline_runs
from app.pipeline.bundle import Bundle as PipelineBundle
from app.pipeline.bundle import Request as PipelineRequest
from app.pipeline.runner import Runner as PipelineRunner
from app.pipeline.runner import records_default as pipeline_records_on
from app.pipeline.stage import Context as PipelineContext
from app.pipeline.stage import StepRecord
from app.pipeline.stage import Stop as PipelineStop
from app.routers.workspace import (
    forget_session_files,
    reindex_session_files,
)
from app.schemas.auth import Preferences
from app.schemas.chat import (
    AudioRequest,
    DiagramOut,
    DiagramRequest,
    FigureSuggestion,
    FigureSuggestRequest,
    HandoffIn,
    HandoffOut,
    ImageRequest,
    MessageForkOut,
    MessageOut,
    SendMessage,
    SessionBulkDelete,
    SessionCreate,
    SessionMade,
    SessionOut,
    SessionPage,
    SessionPatch,
    made_from_artifacts,
    snippet,
)
from app.schemas.workspace import ArtifactOut, FileOut
from app.services import (
    adaptive_routing,
    answer_plan,
    app_help,
    artifact_extract,
    audiogen,
    calculation_policy,
    chart_code,
    check_skills,
    conversation_summary,
    design_templates,
    figures,
    file_schema,
    freshness,
    gap_questions,
    governance,
    grounding,
    imagegen,
    index_client,
    lint,
    message_links,
    publishing,
    revise,
    settings_store,
    skill_specs,
    starter,
    stop_signal,
    user_templates,
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
from app.services import research as research_service
from app.services.context import (
    build_messages,
    declines_web_search,
    envelope_tokens,
    fit_history,
    history_budget,
    literature_request,
    requests_web_search,
    search_hints,
    search_needs_planning,
    search_plan,
    search_query,
    summary_block,
    weather_location,
    with_pictures,
)
from app.services.credits import charge_for_tokens, has_headroom, record_searches, settle
from app.services.tools import sandbox_tools
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
    file_budget,
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
        # No network egress; a "builtin" source alone is not proof of that.
        "calculate",
        "search_knowledge",
        "create_artifact",
        "create_chart",
        # In-process: hands back the text of a skill the person installed.
        "use_skill",
        # The self-hosted sandbox, networking off; `build_tools` offers these only when
        # `sandbox_in_boundary` holds, and run_code then writes on a strict-local model.
        "run_skill_script",
        "run_code",
    }
)


async def _knowledge_shelf(
    db: AsyncSession, user: User, session: ChatSession, agent: WorkspaceAgent | None
) -> tuple[list[tuple[str, str, str | None]], str]:
    """Documents `search_knowledge` can look through this turn, and their index collection.

    An agent's knowledge and the files uploaded into this conversation, whole text
    because the tool runs inside the stream with no DB session. The collection is
    the agent's when there is one — the tool searches one — else the conversation's.
    """
    rows = (
        await db.exec(
            select(StoredFile)
            .where(
                StoredFile.user_id == user.id,
                or_(
                    StoredFile.session_id == session.id,
                    StoredFile.agent_id == (session.agent_id or ""),
                    StoredFile.project_id == (session.project_id or ""),
                ),
            )
            .order_by(col(StoredFile.created_at))
        )
    ).all()
    shelf = [
        (row.name, row.text, row.source_url)
        for row in rows
        if row.text
        and (
            (row.agent_id and row.agent_id == session.agent_id)
            or (row.session_id == session.id and row.project_id is None and row.agent_id is None)
            # Project knowledge too: what the budget could not carry whole, the
            # model can still look up by passage.
            or (row.project_id and row.project_id == session.project_id and row.agent_id is None)
        )
    ]
    if session.agent_id:
        # The vector collection is the owner's; someone running a shared agent
        # searches the lexical shelf of their own uploads only.
        owned = agent is not None and agent.owner_id == user.id
        return shelf, (agent.index_key or "") if owned else ""
    if session.project_id:
        # Project knowledge and the project's conversations' uploads share one
        # collection; uploads indexed before the project had one live under the
        # conversation's own key, so both are searched.
        project = await db.get(Project, session.project_id)
        if project is not None and project.index_key:
            keys = [project.index_key] + ([session.index_key] if session.index_key else [])
            return shelf, ",".join(keys)
    return shelf, session.index_key or ""


#: Skills offered to the model per turn; the description must stay a paragraph, not a catalogue.
_SKILL_SHELF_LIMIT = 12


def _agent_allows_skill(agent: WorkspaceAgent | None, skill: Skill) -> bool:
    """The agent's skill allowlist, read the way `_resolve_skills` reads it."""
    if agent is None or agent.skill_ids is None:
        return True
    allowed = set(agent.skill_ids)
    # A store install is a copy; an allowlist naming the shared origin covers it.
    return skill.id in allowed or bool(skill.origin_id and skill.origin_id in allowed)


async def _standing_skill_ids(
    db: AsyncSession, user: User, session: ChatSession, agent: WorkspaceAgent | None
) -> list[str]:
    """The conversation's standing skills that still apply.

    A skill since deleted, disabled or barred drops out quietly and the stored set is
    pruned; a skill picked for this turn is still checked strictly by `_resolve_skills`.
    """
    ids = [str(x) for x in (session.skill_ids or [])]
    if not ids:
        return []
    rows = (
        await db.exec(
            select(Skill).where(Skill.owner_id == user.id, col(Skill.id).in_(ids))
        )
    ).all()
    by_id = {skill.id: skill for skill in rows}
    kept = [
        skill_id
        for skill_id in ids
        if (skill := by_id.get(skill_id)) is not None
        and skill.enabled
        and (not skill.kinds or session.kind.value in skill.kinds)
        and _agent_allows_skill(agent, skill)
    ]
    if kept != ids:
        session.skill_ids = kept
        db.add(session)
    return kept


async def _skill_shelf(
    db: AsyncSession, user: User, *, agent: WorkspaceAgent | None = None, exclude: set[str]
) -> list[tuple[str, str, str]]:
    """Installed, enabled chat skills the person did not switch on this turn, for `use_skill`.

    Offered by their `when_to_use`; an agent's allowlist bounds the shelf as it bounds a pick.
    """
    rows = (
        await db.exec(
            select(Skill)
            .where(Skill.owner_id == user.id, Skill.enabled == True)  # noqa: E712
            .order_by(col(Skill.created_at).desc())
        )
    ).all()
    shelf: list[tuple[str, str, str]] = []
    for skill in rows:
        if skill.id in exclude or not (skill.body.strip() or skill.when_to_use.strip()):
            continue
        if skill.kinds and "chat" not in skill.kinds:
            continue
        if not _agent_allows_skill(agent, skill):
            continue
        shelf.append((skill.name, skill.when_to_use, skill.body))
        if len(shelf) >= _SKILL_SHELF_LIMIT:
            break
    return shelf


#: A claim older than this is a crash's leftover, not a running turn.
_CLAIM_TTL_SEC = 600
#: Claim tokens by session, handed from the request to the turn that releases them.
_CLAIMS: dict[str, str] = {}


async def _claim_turn(db: AsyncSession, session: ChatSession) -> bool:
    """Takes this session's turn across replicas with one conditional update.

    Free, or held by a claim older than `_CLAIM_TTL_SEC`, is ours. Not committed here: the
    caller's commit lands it with the question row."""
    token = uuid.uuid4().hex
    stale = utcnow() - timedelta(seconds=_CLAIM_TTL_SEC)
    try:
        result = await db.exec(
            update(ChatSession)
            .where(
                ChatSession.id == session.id,
                or_(
                    col(ChatSession.running_turn).is_(None),
                    col(ChatSession.running_since).is_(None),
                    col(ChatSession.running_since) < stale,
                ),
            )
            .values(running_turn=token, running_since=utcnow())
        )
    except Exception as exc:  # noqa: BLE001 — the in-process guard still stands
        log.warning("turn claim for %s skipped: %s", session.id, exc)
        return True
    rowcount = getattr(result, "rowcount", None)
    if isinstance(rowcount, int) and rowcount >= 0 and rowcount != 1:
        return False
    _CLAIMS[session.id] = token
    return True


async def _release_turn(session_id: str, token: str | None = None) -> None:
    """Lets the session's turn go — only ours, unless `token` is None (stop pressed)."""
    if token is None:
        token = _CLAIMS.pop(session_id, None)
        if token is None:
            # This turn never claimed (a retry harness, a non-chat surface): nothing to free.
            return
    else:
        _CLAIMS.pop(session_id, None)
    try:
        async with SessionLocal() as db:
            query = update(ChatSession).where(ChatSession.id == session_id)
            if token:
                query = query.where(ChatSession.running_turn == token)
            await db.exec(query.values(running_turn=None, running_since=None))
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — a stuck claim expires; the answer must not fail
        log.warning("turn claim for %s not released: %s", logs.safe(session_id), logs.safe(exc))


def _strict_local_tools(tools: list[Tool]) -> list[Tool]:
    """Keeps only tools whose implementation has no network egress.

    An admin-configured HTTP endpoint is not proof its arguments stay inside the boundary.
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
    """Drops whole tools whose schema carries a finding.

    Renaming a schema key would desync the runner registry.
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
    """Models allowed for this account and surface; an empty allowlist means the whole catalogue."""
    allowed = set(user.allowed_models or [])
    return [
        model
        for model in catalogue
        if kind in model.get("kinds", []) and (not allowed or model.get("id") in allowed)
    ]


#: Boundary rule for the outline model; the rule lives in `adaptive_routing`.
_widens_boundary = adaptive_routing.widens_boundary

#: Both Auto lanes; every Auto gate applies to both.
_AUTO_MODES = frozenset({RoutingMode.auto, RoutingMode.auto_quality})
#: Surfaces whose writing model an Auto lane may choose: the chat, and the report and
#: deck writers (`_document_route`).
_AUTO_KINDS = frozenset({SessionKind.chat, SessionKind.report, SessionKind.slides})


def _planner_model(
    wanted: str | None,
    *,
    user: User,
    catalogue: list[dict],
    kind: str,
    writer: dict,
    strict_local: bool,
) -> dict | None:
    """Catalogue row for the outline call, or `None` to use the writer.

    Refused when the allowlist, surface, strict-local route or writer boundary would not allow it.
    """
    if not wanted:
        return None
    wanted = str(wanted)
    # On the strict-local route the same model's strict-local entry plans, so internal
    # material stays on the self-hosted route.
    allowed = _allowed_models(user, catalogue, kind=kind)
    planner = None
    if strict_local and wanted.startswith("local/"):
        planner = model_service.find(allowed, "strict-local/" + wanted.removeprefix("local/"))
    planner = planner or model_service.find(allowed, wanted)
    # On the strict-local route only a self-hosted planner may judge (「strict-local/…」):
    # internal material never leaves for an outline, a figure or a repair.
    if strict_local and (planner is None or planner.get("dataBoundary") != "self_hosted"):
        return None
    if planner is None or _widens_boundary(planner, writer):
        log.info("outline model %s unusable here", wanted)
        return None
    return planner


def _local_planner(
    *, user: User, catalogue: list[dict], kind: str, writer: dict, strict_local: bool
) -> dict | None:
    """The default local model as the judge for an external writer's instructions, or
    `None` when the writer is already self-hosted (or the default is not allowed here)."""
    if strict_local or writer.get("dataBoundary") in ("self_hosted", "hybrid"):
        return None
    return _planner_model(
        settings.default_chat_model, user=user, catalogue=catalogue, kind=kind,
        writer=writer, strict_local=strict_local,
    )


async def _enrichment_model(writer: dict, *, strict_local: bool, disable_fallbacks: bool) -> dict:
    """Catalogue row that titles the session and extracts memories.

    The writer itself when `title_model` is unset.
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
    """One turn's routing decision.

    Wire key is `costRouting` for both lanes; `mode` names the lane.
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
    #: Stored as a plain string, so compared by value.
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
    """An Auto turn's effective model and value-free route metadata.

    Cost acts on `low`, quality on `high`; any refusal keeps the chosen model.
    """
    lane = str(getattr(mode, "value", mode))
    upgrading = lane == RoutingMode.auto_quality.value
    # Each lane has its own switch.
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
            # Upgraded turns keep their tools.
            requires_tools=bool(classifier_tool_definitions),
        )
    else:
        candidates = adaptive_routing.economy_candidates(
            catalogue,
            list(policy.adaptive_economy_model_ids or [])[:3],
            quality_model=quality_model,
            allowed_model_ids=allowed,
            context_tokens=context_tokens,
            # Economy turns run tool-free, so the preflight context fit holds.
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

    # Not `credentials_for`: it may fall back to the master key. The classifier
    # needs the user's own key.
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
    """Routing metadata for a fallback made outside the privacy decision (report and slides)."""
    effective_id = effective["id"]
    boundary = effective.get("dataBoundary") or "unknown"
    return {
        "requestedModels": [requested["id"]],
        "routedModels": [effective_id],
        "effectiveModels": [effective_id],
        # Document runners never see a provider-reported id.
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


#: Context sources that are the organisation's own material.
_INTERNAL_SOURCES = (
    "attachment", "project.knowledge", "project.instructions", "project.design", "memory"
)


def _internal_data_present(
    session: ChatSession,
    blocks: tuple[ContextBlock, ...] | list[ContextBlock] | None,
    attachment_ids: list[str] | None = None,
    history: list[Message] | None = None,
) -> bool:
    """Whether this turn handles internal material.

    A file attached now or earlier, a project, a stored memory in the context, or a turn
    already moved strict-local: once a session has handled internal material it stays so."""
    if attachment_ids or getattr(session, "project_id", None):
        return True
    for message in history or []:
        if getattr(message, "attachments", None):
            return True
        routing = getattr(message, "routing", None)
        if isinstance(routing, dict) and routing.get("action") == "internal_strict_local":
            return True
    return any(block.source in _INTERNAL_SOURCES for block in blocks or [])


def _strict_route_possible(
    model: dict, catalogue: list[dict], policy: Governance, user: User, *, auto_turn: bool
) -> bool:
    """Whether privacy routing could land this turn on a strict-local model, so its
    in-boundary tools must be ready before the decision."""
    if _strict_model(model):
        return True
    # Internal material moves the turn to the strict-local twin.
    if policy.internal_data_strict_local and _strict_twin(
        model, catalogue, set(user.allowed_models or [])
    ):
        return True
    safe_ids = set(policy.privacy_safe_model_ids or [])
    economy_ids = set(policy.adaptive_economy_model_ids or []) if auto_turn else set()
    return any(
        m.get("id") in safe_ids | economy_ids and _strict_model(m) for m in catalogue
    )


def _strict_twin(model: dict, catalogue: list[dict], allowed: set[str]) -> dict | None:
    """The `strict-local/` build of a `local/` model — the same weights behind a route with
    no external fallback — when the catalogue lists it and the person may use it."""
    model_id = str(model.get("id") or "")
    if not model_id.startswith("local/"):
        return None
    twin_id = "strict-local/" + model_id.split("/", 1)[1]
    twin = next((m for m in catalogue if m.get("id") == twin_id), None)
    if twin is None or not _strict_model(twin) or "chat" not in twin.get("kinds", []):
        return None
    return twin if not allowed or twin_id in allowed else None


def _planned_web_search(history: list[Message]) -> bool | str:
    """The web-search setting of the latest question that recorded one (`turnOptions`);
    off when none did."""
    for message in reversed(history):
        if message.role is not Role.user:
            continue
        options = (message.routing or {}).get("turnOptions") or {}
        if "webSearch" in options:
            value = options["webSearch"]
            return value if value in (True, "auto") else False
    return False


def _brings_material(content: str) -> bool:
    """A request with its document pasted under it (a proposal, a report, notes) has
    what a question would ask for: the writer proceeds instead of asking."""
    from app.services.context import pasted_material

    return len(pasted_material(content or "")) >= 1500


async def _document_route(
    *,
    db: DbSession,
    user: User,
    session: ChatSession,
    policy,
    catalogue: list[dict],
    model: dict,
    blocks,
    attachments: list[str] | None,
    history: list[Message],
    request: str,
) -> tuple[dict, bool, dict[str, Any] | None] | JSONResponse:
    """A report or deck turn's writer, whether it is strict-local, and the route note.

    Internal material goes to the strict-local twin of the chosen model (else the first
    privacy-safe model, else a refusal) before an Auto lane is consulted; a strict-local
    writer is never moved outward."""
    allowed = set(user.allowed_models or [])
    if (
        policy.internal_data_strict_local
        and not _strict_model(model)
        and _internal_data_present(session, blocks, attachments, history)
    ):
        safe = [
            m for m in catalogue
            if m.get("id") in set(policy.privacy_safe_model_ids or []) and _strict_model(m)
            and (not allowed or m.get("id") in allowed)
        ]
        twin = _strict_twin(model, catalogue, allowed) or (safe[0] if safe else None)
        if twin is None:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "detail": "internal_data_requires_strict_local",
                    "action": "internal_data_requires_strict_local",
                    "requestedModels": [model.get("id")],
                    "safeModels": [],
                    "findings": [],
                    "policyVersion": governance.POLICY_VERSION,
                    "detectorVersion": governance.DETECTOR_VERSION,
                },
            )
        return twin, True, {
            "action": "internal_strict_local",
            "requestedModels": [model.get("id")],
            "routedModels": [twin.get("id")],
            "effectiveModels": [twin.get("id")],
            "dataBoundary": twin.get("dataBoundary") or "self_hosted",
        }
    if _strict_model(model) or session.routing_mode not in _AUTO_MODES:
        return model, _strict_model(model), None
    routed, cost_routing = await _resolve_cost_routing(
        mode=session.routing_mode,
        db=db,
        user=user,
        policy=policy,
        catalogue=catalogue,
        quality_model=model,
        classifier_messages=[{"role": "user", "content": request[:6000]}],
        classifier_tool_definitions=[],
        context_tokens=adaptive_routing.estimated_context_tokens([request]),
        unsupported_reason=None,
    )
    return routed, _strict_model(routed), {"costRouting": cost_routing}


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
        "user.instructions": "user_instructions",
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
    internal_data: bool = False,
    force_strict_local: bool = False,
) -> _PrivacyResolution | JSONResponse:
    """Makes one egress decision before persistence, billing or upstream.

    Only explicit proxy metadata makes a safe candidate; unknown boundaries count as external.
    A turn with internal material goes strict-local before any finding is weighed;
    `force_strict_local` (a published bot's turn) holds it there whatever the policy says.
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
    # Priority is catalogue order, the order the admin screen shows.
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

    if internal_data and (policy.internal_data_strict_local or force_strict_local) \
            and not all_strict:
        # Internal material never leaves: each chosen model becomes its strict-local
        # twin, or the first privacy-safe model; with neither, the turn is refused.
        moved: list[dict] = []
        for model in requested:
            if _strict_model(model):
                moved.append(model)
                continue
            twin = _strict_twin(model, catalogue, allowed_models) or (safe[0] if safe else None)
            if twin is None:
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={
                        "detail": "internal_data_requires_strict_local",
                        "action": "internal_data_requires_strict_local",
                        "requestedModels": requested_ids,
                        "safeModels": [],
                        "findings": [],
                        "policyVersion": governance.POLICY_VERSION,
                        "detectorVersion": governance.DETECTOR_VERSION,
                    },
                )
            if all(m.get("id") != twin.get("id") for m in moved):
                moved.append(twin)
        effective = moved
        all_strict = True
        action = "internal_strict_local"
    elif rows and policy.pii_masking:
        # The organisation-wide masking setting is always the strongest rule.
        action = "mask_external"
        mask_outbound = True
    elif rows and all_strict:
        action = "strict_local"
    elif rows and action == "internal_strict_local":
        pass
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
            # The turn goes to the first privacy-safe model.
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


def _protected_values(
    sources: dict[str, str | list[str]], *, legacy: bool = False
) -> frozenset[str]:
    """The raw values egress masking takes out of the user's side of the envelope."""
    return frozenset(
        value
        for raw in sources.values()
        for text in (raw if isinstance(raw, list) else [raw])
        for value in governance.protected_values(text or "", legacy=legacy)
    )


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
    # Same answer for "missing" and "someone else's". A published bot's conversation is the
    # publisher's row but not theirs to open: only the visitor reads it, through
    # `routers/publications`, and the owner only what `publishing.owner_may_read` allows.
    if session is None or session.user_id != user.id or session.publication_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session_not_found")
    return session


#: A visitor's turn on a published bot, set by `routers/publications` around its call to
#: `send_message`: the session is then the bot's conversation, run on the owner's account.
_PUBLISHED: ContextVar[publishing.PublishedTurn | None] = ContextVar("published_turn",
                                                                    default=None)


async def _visited(db: DbSession, user: User, session_id: str,
                   turn: publishing.PublishedTurn) -> ChatSession:
    """The bot conversation a visitor's turn runs on: the publisher's row, this publication's,
    this visitor's."""
    session = await db.get(ChatSession, session_id)
    if (session is None or session.user_id != user.id
            or session.publication_id != turn.publication_id
            or session.visitor_id != turn.visitor_id):
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
    }
    raise HTTPException(
        status_code=(
            status.HTTP_404_NOT_FOUND
            if missing
            # Literal 422: the Starlette constant name differs across supported versions.
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
        # Structured fields beside the display strings, for audit.
        "skills": list(event.get("skills") or []),
        "estimatedTokens": int(event.get("estimatedTokens") or 0),
        "detail": (" · ".join(names) + f" · 약 {int(event.get('estimatedTokens') or 0):,} 토큰"),
    }


#: Per-file detail line, keyed by `ContextFile.state`.
_FILE_NOTE = {
    "truncated": "{name} {kept:,}자만 반영",
    "omitted": "{name} 분량을 넘겨 제외",
    "unreadable": "{name} 읽지 못함",
    # Readable file, but this model cannot see pictures.
    "picture_unseen": "{name} 그림 · 이 모델은 보지 못함",
    # Scanned pages still being read; what is read so far was used.
    "reading": "{name} 글자 읽는 중",
}

#: Names listed before the rest are counted.
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
    # A picture the model saw counts as included.
    whole = {"included", "picture"}
    short = [file for file in files if file.state not in whole]
    cut = sum(1 for file in short if file.state == "truncated")
    reading = sum(1 for file in short if file.state == "reading")
    dropped = len(short) - cut - reading
    fates = [f"{cut}개 잘림"] if cut else []
    if reading:
        fates.append(f"{reading}개 글자 읽는 중")
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
        # Structured beside the display strings, for audit.
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
        # Names only, never bodies.
        "memories": names,
        # The client re-renders this line in the reader's language.
        "totalMemories": workspace.total_memories,
    }


def _personal_context_step(workspace: WorkspaceContext) -> dict | None:
    """One line saying personal settings shaped the turn: which half, never the text."""
    block = next((b for b in workspace.blocks if b.source == "user.instructions"), None)
    if block is None:
        return None
    parts = []
    if "# 사용자에 대해" in block.text:
        parts.append("나에 대해")
    if "# 사용자가 바라는 답변 방식" in block.text:
        parts.append("답변 방식")
    return {
        "id": "context-personal",
        "type": "thinking",
        "label": "개인 맞춤 설정 적용",
        "status": "done",
        "detail": " · ".join(parts),
        "personal": parts,
    }


def _context_steps(workspace: WorkspaceContext) -> list[dict]:
    """Timeline lines for context handed to the model silently: memories, attachments, knowledge."""
    steps = [
        _personal_context_step(workspace),
        _memory_context_step(workspace),
        _file_context_step("context-attachments", "첨부", workspace.attachments),
        _file_context_step("context-earlier", "이전 첨부", workspace.carried),
        _file_context_step("context-knowledge", "프로젝트 지식", workspace.knowledge),
    ]
    return [step for step in steps if step]


def _answer_in_flight(last: Message) -> bool:
    """Whether another replica is most likely still writing this session's answer.

    `_STOPPING` is one process's own; the transcript is every replica's. A question
    with no reply and no failure mark, asked within the stall window, is being
    answered somewhere — or was orphaned by a crash, which the window bounds.
    """
    if last.role is not Role.user or last.failure is not None or last.created_at is None:
        return False
    asked = last.created_at
    if asked.tzinfo is None:
        asked = asked.replace(tzinfo=UTC)
    return (utcnow() - asked).total_seconds() < settings.chat_stall_sec


def _reads_pasted_material(content: str) -> bool:
    """A request about the material pasted under it (a commit log, notes, a source pack):
    the dates and prices in that material are what it says, not a question about now. The
    request's own words can still ask about the present (「지금 환율로」)."""
    from app.services.context import instruction_part, pasted_material

    if pasted_material(content):
        return not freshness.names_the_present(instruction_part(content))
    head, gap, rest = (content or "").partition("\n\n")
    lines = [line for line in rest.splitlines() if line.strip()]
    looks_pasted = len(rest.strip()) >= 120 and (
        len(lines) >= 3 or bool(re.match(r"[\[#|「<]", rest.lstrip())))
    return bool(gap) and len(head) <= 300 and looks_pasted and not freshness.names_the_present(
        head)


def _grounded_in_files(workspace: WorkspaceContext) -> bool:
    """Whether this turn carries readable file text: an attachment, an earlier upload or
    project knowledge that reached the model at least in part."""
    return any(
        file.state in ("included", "truncated")
        for file in (*workspace.attachments, *workspace.carried, *workspace.knowledge)
    )


def _asked_about(content: str, history: list[Message]) -> str:
    """What to read a carried file around: this question, then the one before it.

    A follow-up often names nothing the file contains; the previous question usually does.
    """
    previous = next((m.content for m in reversed(history) if m.role is Role.user), "")
    return f"{content} {previous}".strip() if previous else content


def _summary_context_step(dropped: int, *, summarised: bool) -> dict:
    """Timeline line for earlier turns the window could not hold."""
    return {
        "id": "context-summary",
        "type": "thinking",
        "label": (
            f"앞선 대화 {dropped}개 메시지를 요약해 전달"
            if summarised
            else f"앞선 대화 {dropped}개 메시지는 길이 때문에 생략"
        ),
        "status": "done",
        "detail": (
            "모델의 문맥 길이를 넘어 오래된 턴은 요약으로 대신했습니다"
            if summarised
            else "요약을 만들지 못해 오래된 턴은 보내지 않았습니다"
        ),
        "summarisedTurns": dropped,
        "summarised": summarised,
    }


async def _conversation_summary(
    db: AsyncSession,
    session: ChatSession,
    *,
    rows: list[Message],
    turns: list[dict],
    writer: dict,
    api_key: str,
    strict_local: bool,
    disable_fallbacks: bool,
    masker,
    mask_at_rest: bool,
    redact_logging: bool,
) -> tuple[str | None, int]:
    """`(summary, credits)` standing in for `rows`, the earliest messages past the window.

    Cached on the session by the id of the last message it covers, so a later turn only
    summarises the turns since. `None` when the summariser failed, with nothing cached.
    """
    if not rows or len(rows) != len(turns):
        return None, 0
    cached = dict(session.summary or {})
    through = str(cached.get("through") or "")
    previous = str(cached.get("text") or "") or None
    if previous and through == rows[-1].id:
        # The cache was written under an earlier policy; today's masking applies to it.
        return (masker(previous)[0] if masker else previous), 0
    covered = next((i for i, row in enumerate(rows) if row.id == through), None)
    if previous is None or covered is None:
        # No usable earlier summary: everything dropped is summarised afresh.
        previous, fresh = None, turns
    else:
        fresh = turns[covered + 1 :]
    enrichment = await _enrichment_model(
        writer, strict_local=strict_local, disable_fallbacks=disable_fallbacks
    )
    text, usage = await conversation_summary.summarize(
        enrichment["id"],
        previous,
        fresh,
        api_key,
        masker=masker,
        strict_local=strict_local,
        disable_fallbacks=disable_fallbacks,
        redact_logging=redact_logging,
    )
    credits = charge_for_tokens(enrichment, usage["inputTokens"], usage["outputTokens"])
    if text is None:
        return None, credits
    stored = masker(text)[0] if mask_at_rest and masker is not None else text
    session.summary = {"through": rows[-1].id, "text": stored, "turns": len(rows)}
    db.add(session)
    await db.commit()
    return text, credits


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
    """A stored step addressed for the wire.

    `type` becomes the event name; the display category rides as `category`.
    """
    return {**step, "type": "step", "category": step["type"]}


def _searches_in(research_log: dict | None) -> int:
    """Queries a document's research step sent to the search backend; 0 when none ran."""
    if not research_log or not research_log.get("searched"):
        return 0
    return len(research_log.get("queries") or [])


def _prelude_steps(skills_event: dict | None, context_steps: list[dict] | None) -> list[dict]:
    """Steps a turn opens with: applied skills, then context."""
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
    """List non-empty sessions and recently created in-flight sessions."""
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
    query = select(ChatSession).where(owned_by(user.id))
    if kind is not None:
        query = query.where(ChatSession.kind == kind)
    if project_id is not None:
        query = query.where(ChatSession.project_id == project_id)
    # Sidebar order: pinned first, then most recently touched.
    query = query.order_by(col(ChatSession.pinned).desc(), col(ChatSession.updated_at).desc())
    rows = (await db.exec(query)).all()

    ids = [s.id for s in rows]
    previews = await _previews(db, ids)
    # Absent from `_previews` means no messages.
    rows = _worth_listing(rows, {sid for sid in ids if sid not in previews})
    ids = [s.id for s in rows]
    # A media answer has an artifact and an empty body.
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


#: Conversations per page of 대화 기록.
HISTORY_PAGE = 50


@router.get("/paged", response_model=SessionPage)
async def page_sessions(
    user: CurrentUser,
    db: DbSession,
    page: int = Query(1, ge=1, le=100_000),
    size: int = Query(HISTORY_PAGE, ge=1, le=200),
):
    """대화 기록 a page at a time: the conversations the sidebar would list, newest first.

    Same rule as `_worth_listing`, in SQL so the count covers every page: a conversation with
    messages, a result, a pin, or created in the last minute.
    """
    fresh = utcnow() - timedelta(minutes=1)
    listed = and_(
        owned_by(user.id),
        or_(
            exists().where(col(Message.session_id) == col(ChatSession.id)),
            col(ChatSession.artifact_id).is_not(None),
            col(ChatSession.pinned).is_(True),
            col(ChatSession.created_at) > fresh,
        ),
    )
    total = int((await db.exec(select(func.count()).select_from(ChatSession).where(listed))).one())
    last = max(1, -(-total // size))
    page = min(page, last)
    rows = (
        await db.exec(
            select(ChatSession)
            .where(listed)
            .order_by(col(ChatSession.updated_at).desc(), col(ChatSession.id))
            .offset((page - 1) * size)
            .limit(size)
        )
    ).all()
    return SessionPage(items=[SessionOut.of(s) for s in rows], total=total, page=page, size=size)


async def _previews(db: DbSession, session_ids: list[str]) -> dict[str, tuple[str | None, int]]:
    """`{session_id: (latest message snippet, message count)}`.

    Absent for a session with no messages.
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
    """`{session_id: what it produced}` for sessions with no message text (pictures, clips)."""
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
    made = {} if any(m.content for m in history) else await _made(db, [session_id])
    links = await message_links.run_links(db, session_id)
    return SessionOut.of(session, history, made=made.get(session_id), links=links)


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(payload: SessionCreate, user: CurrentUser, db: DbSession):
    agent = await db.get(WorkspaceAgent, payload.agent_id) if payload.agent_id else None
    if agent is not None and agent.owner_id != user.id and agent.visibility != AgentVisibility.org:
        agent = None
    # A kind the caller named wins; otherwise the agent's surface.
    if "kind" not in payload.model_fields_set and agent is not None and agent.surface:
        payload.kind = SessionKind(agent.surface)
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
    if agent is not None and agent.retired:
        # Taken out of the catalogue: its conversations still open, no new one starts.
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="agent_retired")
    if agent is not None and user.role is not UserRole.admin and (
            agent.catalog_key or "") in starter.draft_agents():
        # Still in evaluation: administrators alone use it.
        raise HTTPException(status_code=404, detail="agent_not_found")
    if payload.routing_mode in _AUTO_MODES and payload.kind not in _AUTO_KINDS:
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
        render_template_id=(await _agent_template_loaded(db, user, agent, payload.kind))
        or await _project_render_template(db, payload.project_id, payload.kind),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionOut.of(session, [])


def _fork_attachment_ids(messages: list[Message]) -> list[str]:
    ids = []
    seen = set()
    for message in messages:
        for metadata in message.attachments or []:
            file_id = metadata.get("id") if isinstance(metadata, dict) else None
            if not isinstance(file_id, str) or not file_id:
                raise HTTPException(status_code=404, detail="attachment_not_found")
            if file_id not in seen:
                ids.append(file_id)
                seen.add(file_id)
    return ids


@router.post(
    "/{session_id}/messages/{message_id}/fork",
    response_model=MessageForkOut,
    status_code=status.HTTP_201_CREATED,
)
async def fork_message(session_id: str, message_id: str, user: CurrentUser, db: DbSession):
    """Copy only the prefix before a user message, without generation or prior consent."""
    source = (
        await db.exec(
            select(ChatSession)
            .where(ChatSession.id == session_id, owned_by(user.id))
            .with_for_update()
        )
    ).first()
    if source is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    if source.kind is not SessionKind.chat:
        raise HTTPException(status_code=422, detail="fork_chat_only")
    history = await _history(db, source.id)
    at = next((i for i, row in enumerate(history) if row.id == message_id), None)
    if at is None:
        raise HTTPException(status_code=404, detail="fork_target_not_found")
    target = history[at]
    if target.role is not Role.user:
        raise HTTPException(status_code=422, detail="fork_user_message_required")
    jobs = (
        await db.exec(
            select(Job).where(
                Job.session_id == source.id, col(Job.status).in_(["queued", "running"])
            )
        )
    ).all()
    # The trailing unanswered question also catches a stream on another replica.
    unanswered = history[-1].role is Role.user and history[-1].failure is None
    if source.pending or _STOPPING.get(source.id) or jobs or unanswered:
        raise HTTPException(status_code=409, detail="session_busy")
    if "chat" not in await settings_store.enabled_kinds():
        raise HTTPException(status_code=403, detail="이 기능은 사용할 수 없습니다.")
    await _validate_session_links(
        db, user, source.kind, project_id=source.project_id, agent_id=source.agent_id
    )

    prefix = history[:at]
    prefix_files = _fork_attachment_ids(prefix)
    prefix_file_ids = set(prefix_files)
    target_file_ids = _fork_attachment_ids([target])
    file_ids = list(dict.fromkeys([*prefix_files, *target_file_ids]))
    uploads, _metadata = await _owned_attachments(db, user, file_ids)
    artifact_ids = list(dict.fromkeys(
        artifact_id for message in prefix for artifact_id in (message.artifact_ids or [])
    ))
    artifacts = (
        (
            await db.exec(
                select(Artifact).where(
                    col(Artifact.id).in_(artifact_ids), Artifact.user_id == user.id
                )
            )
        ).all()
        if artifact_ids else []
    )
    if len(artifacts) != len(artifact_ids):
        raise HTTPException(status_code=404, detail="artifact_not_found")

    # Bound the whole fork, not each file, before creating any row or blob.
    copied_bytes = 0
    for item in [*uploads, *artifacts]:
        if not item.storage_key:
            continue
        try:
            copied_bytes += file_service.blob_size(item.storage_key)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="attachment_not_found") from None
        if copied_bytes > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(
                status_code=413, detail=f"fork_files_too_large_{settings.max_upload_mb}mb"
            )

    branch = ChatSession(
        user_id=user.id, kind=SessionKind.chat,
        title=f"{source.title[:190]} (편집)" if source.title else "",
        project_id=source.project_id, agent_id=source.agent_id, model=source.model,
        routing_mode=source.routing_mode, render_template_id=source.render_template_id,
    )
    copied_files: dict[str, StoredFile] = {}
    copied_artifacts: dict[str, Artifact] = {}
    copied_messages: list[Message] = []
    new_keys: list[str] = []
    try:
        for stored in uploads:
            copied = StoredFile(
                user_id=user.id, session_id=branch.id if stored.id in prefix_file_ids else None,
                name=stored.name, size=stored.size, mime=stored.mime, text=stored.text,
                tokens=stored.tokens, error=stored.error, source_url=stored.source_url,
            )
            if stored.storage_key:
                copied.storage_key = file_service.copy_blob(
                    user.id, copied.id, copied.name, stored.storage_key
                )
                new_keys.append(copied.storage_key)
            copied_files[stored.id] = copied
        for artifact in artifacts:
            copied = Artifact(
                user_id=user.id, session_id=branch.id, project_id=branch.project_id,
                kind=artifact.kind, title=artifact.title, data=deepcopy(artifact.data),
            )
            if artifact.storage_key:
                copied.storage_key = file_service.copy_blob(
                    user.id, copied.id, copied.title or "artifact", artifact.storage_key
                )
                new_keys.append(copied.storage_key)
            copied_artifacts[artifact.id] = copied
        prior_time = None
        for message in prefix:
            created = message.created_at
            if prior_time is not None and created <= prior_time:
                created = prior_time + timedelta(microseconds=1)
            prior_time = created
            attachments = [
                {**deepcopy(item), "id": copied_files[item["id"]].id}
                for item in message.attachments or []
            ] or None
            copied_messages.append(Message(
                session_id=branch.id, role=message.role, content=message.content,
                attachments=attachments, model=message.model, failure=message.failure,
                started_from=deepcopy(message.started_from), created_at=created,
                artifact_ids=[copied_artifacts[key].id for key in message.artifact_ids or []]
                or None,
            ))
        output = MessageForkOut(
            session=SessionOut.of(branch, copied_messages),
            attachments=[FileOut.of(copied_files[key]) for key in target_file_ids],
            attachment_id_map={key: copied_files[key].id for key in target_file_ids},
        )
        for row in [branch, *copied_files.values(), *copied_artifacts.values(), *copied_messages]:
            db.add(row)
        await db.commit()
    except BaseException as exc:
        try:
            await db.rollback()
        finally:
            for key in new_keys:
                file_service.delete_blob(key)
        if isinstance(exc, FileNotFoundError):
            raise HTTPException(status_code=404, detail="attachment_not_found") from None
        raise
    return output


#: Characters of conversation carried into a document; the tail is kept.
_HANDOFF_CHARS = 30_000
#: Files from the conversation carried with it, most recent last.
_HANDOFF_FILES = 8
_HANDOFF_ROLE = {Role.user: "사용자", Role.assistant: "답변"}


def handoff_transcript(messages: list[Message]) -> str:
    """The conversation as plain text for the document writer; `""` when there is none.

    Failed and empty turns are left out; past the bound, the oldest turns go first."""
    turns = [
        f"[{_HANDOFF_ROLE[m.role]}]\n{m.content.strip()}"
        for m in messages
        if m.role in _HANDOFF_ROLE and m.failure is None and (m.content or "").strip()
    ]
    kept: list[str] = []
    used = 0
    for turn in reversed(turns):
        if kept and used + len(turn) > _HANDOFF_CHARS:
            break
        kept.append(turn[-_HANDOFF_CHARS:])
        used += len(turn) + 2
    return "\n\n".join(reversed(kept))


@router.post(
    "/{session_id}/handoff",
    response_model=HandoffOut,
    status_code=status.HTTP_201_CREATED,
)
async def handoff_session(
    session_id: str, payload: HandoffIn, user: CurrentUser, db: DbSession
):
    """Opens a report or slides session from a chat, keeping its agent and project.

    The new session takes the agent's 서식 for that surface. The conversation travels as a
    text file the caller attaches to the document request, with the chat's own files."""
    source = await _owned(db, user, session_id)
    if source.kind is not SessionKind.chat:
        raise HTTPException(status_code=422, detail="handoff_from_chat_only")
    kind = SessionKind(payload.kind)
    if kind.value not in await settings_store.enabled_kinds():
        raise HTTPException(status_code=403, detail="이 기능은 사용할 수 없습니다.")
    await _validate_session_links(
        db, user, kind, project_id=source.project_id, agent_id=source.agent_id
    )
    agent = await db.get(WorkspaceAgent, source.agent_id) if source.agent_id else None
    history = await _history(db, source.id)
    earlier = list(dict.fromkeys(
        str(item["id"])
        for message in history
        for item in message.attachments or []
        if isinstance(item, dict) and item.get("id")
    ))[-_HANDOFF_FILES:]
    carried = (
        (
            await db.exec(
                select(StoredFile).where(
                    col(StoredFile.id).in_(earlier), StoredFile.user_id == user.id
                )
            )
        ).all()
        if earlier else []
    )
    by_id = {row.id: row for row in carried}
    target = ChatSession(
        user_id=user.id,
        kind=kind,
        project_id=source.project_id,
        agent_id=source.agent_id,
        routing_mode=RoutingMode.manual,
        render_template_id=(await _agent_template_loaded(db, user, agent, kind))
        or await _project_render_template(db, source.project_id, kind),
    )
    files: list[StoredFile] = []
    key = ""
    if text := handoff_transcript(history):
        record = StoredFile(
            user_id=user.id,
            session_id=target.id,
            name="대화 기록.txt",
            mime="text/plain",
            text=text,
            tokens=file_service.estimate_tokens(text),
        )
        data = text.encode()
        record.size = len(data)
        key = record.storage_key = file_service.write_blob(user.id, record.id, record.name, data)
        files.append(record)
    try:
        db.add(target)
        for row in files:
            db.add(row)
        await db.commit()
    except BaseException:
        await db.rollback()
        if key:
            file_service.delete_blob(key)
        raise
    await db.refresh(target)
    files.extend(by_id[file_id] for file_id in earlier if file_id in by_id)
    return HandoffOut(session=SessionOut.of(target, []), attachments=[FileOut.of(f) for f in files])


async def _template_skills(
    db: AsyncSession,
    template: design_templates.DesignTemplate | None,
    kind: SessionKind,
    skills_event: dict | None,
) -> tuple[list[str], dict | None]:
    """`(context blocks, skills_applied event)` with the template's catalogue skills joined.

    Built-in org-visible rows only, by `catalog_key`; unknown keys are skipped.
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
        # A document or deck is written without tools: a skill that calls one is the
        # pipeline's own check there (see `report_checks`), not words for the writer.
        if skill.required_tools and kind is not SessionKind.chat:
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
                # Lets the screen mark template-activated skills.
                "fromTemplate": True,
            }
        )
    if not applied:
        return blocks, skills_event
    event = dict(skills_event or {"type": "skills_applied", "skills": []})
    event["skills"] = list(event.get("skills") or []) + applied
    # The browser requires `estimatedTokens` on the event.
    event["estimatedTokens"] = sum(
        int(skill.get("estimatedTokens") or 0) for skill in event["skills"]
    )
    return blocks, event


async def _project_render_template(
    db: DbSession, project_id: str | None, kind: SessionKind
) -> str | None:
    """The project's default template for this kind.

    Copied onto the session so a later project change does not affect it.
    """
    if not project_id:
        return None
    project = await db.get(Project, project_id)
    return design_templates.default_for(project.render_templates, kind) if project else None


async def _agent_template_loaded(db: DbSession, user: User, agent: WorkspaceAgent | None,
                                 kind: SessionKind) -> str | None:
    """`agent_template` with a person's template loaded first, when the caller may use it."""
    if agent is not None and agent.render_template_id and not await user_templates.usable(
            db, user.id, agent.render_template_id):
        return None
    return agent_template(agent, kind)


def agent_template(agent: WorkspaceAgent | None, kind: SessionKind) -> str | None:
    """The agent's bound 서식 when it belongs to this surface, else `None`.

    It wins over the project's default 서식: the agent exists to make that document. A 서식
    picked later in the composer is stored on the session and wins over both."""
    if agent is None or not agent.render_template_id:
        return None
    chosen = design_templates.choosable(agent.render_template_id)
    if chosen is None or chosen.kind not in design_templates.HTML_KINDS:
        return None
    return chosen.id if chosen.surface is kind else None


def _resolved_template_id(requested: str | None, kind: SessionKind,
                          keep: str | None = None) -> str | None:
    """A rendering template id usable on this surface, or `None`.

    Unknown ids are refused, not dropped; so is a retired 서식, unless it is the one the
    conversation already has (`keep`): an old conversation goes on in it.
    """
    if not requested:
        return None
    chosen = (design_templates.get(requested) if requested == keep
              else design_templates.choosable(requested))
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


def _english(text: str) -> bool:
    """Whether a message is written in English (more Latin letters than Hangul)."""
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    return latin > 3 * len(re.findall(r"[가-힣]", text or ""))


async def _usable_design(db: DbSession, user: User, design_id: str | None) -> str | None:
    """A design the person may bind an agent to: the one named when theirs or shared, else
    their own design of the same name, else None."""
    if not design_id:
        return None
    row = await db.get(DesignSystem, design_id)
    if row is None:
        return None
    if row.owner_id == user.id or row.shared:
        return row.id
    own = (await db.exec(select(DesignSystem).where(
        DesignSystem.owner_id == user.id, DesignSystem.name == row.name))).first()
    return own.id if own is not None else None


async def _draft_decided(db: AsyncSession, user: User, session: ChatSession, *,
                         distill: bool = True) -> tuple[list[str], str, bool]:
    """`(lines, status, strict)`: what the conversation settled, distilled from the person's
    own messages by the model the conversation ran on (a strict-local one when any turn ran
    strict-local). `status` is 「ok」 (lines), 「empty」 (read, nothing settled), 「failed」
    (the model could not be read: no headroom, timed out after its retry) or 「pending」
    (not asked: `distill=False`)."""
    from app.services import agent_draft as drafts

    rows = (await db.exec(select(Message).where(Message.session_id == session.id)
                          .order_by(col(Message.created_at)))).all()
    said = [m.content for m in rows if m.role is Role.user and (m.content or "").strip()]
    catalogue = (await model_service.list_models_for_egress())["models"]
    used = [model_service.find(catalogue, m.model or "") for m in rows
            if m.role is Role.assistant and m.model]
    used = [m for m in used if m is not None]
    strict_rows = [m for m in used if _strict_model(m)]
    model = (strict_rows[-1] if strict_rows else used[-1] if used
             else model_service.find(catalogue, session.model or ""))
    strict = bool(strict_rows) or (model is not None and _strict_model(model))
    if not distill:
        return [], "pending", strict
    if not said:
        return [], "empty", strict
    if model is None or not has_headroom(user, model):
        return [], "failed", strict
    _, api_key = await litellm_service.credentials_for(user)
    # What leaves the instance is masked unless it stays on the strict-local model.
    try:
        lines, spent = await drafts.distill(
            model["id"], said, api_key, strict_local=strict,
            masker=None if strict else governance.mask)
    except drafts.DistillFailed:
        return [], "failed", strict
    if cost := charge_for_tokens(model, spent["inputTokens"], spent["outputTokens"]):
        settle(db, user, cost, reason="agent.draft", session_id=session.id,
               model=model["id"])
        await db.commit()
    lines = [line for line in lines if drafts.keep(line)]
    return lines, "ok" if lines else "empty", strict


@router.get("/{session_id}/agent-draft/decided")
async def agent_draft_decided(session_id: str, user: CurrentUser, db: DbSession):
    """「에이전트로 저장」's 지시 alone: the distillation the form asks for after the rest of the
    draft, and again on 「다시 시도」. `prompt` is the 지시 section to add."""
    from app.services import agent_draft as drafts

    session = await _owned(db, user, session_id)
    lines, state, _ = await _draft_decided(db, user, session)
    return {"decided": lines, "prompt": drafts.prompt_from(lines), "distilled": state}


@router.get("/{session_id}/agent-draft")
async def agent_draft(session_id: str, user: CurrentUser, db: DbSession, distill: bool = True):
    """「에이전트로 저장」: this conversation as a draft agent to prefill the form. Nothing is
    saved. What it settled is distilled from the person's own messages by the model the
    conversation ran on (a strict-local one when any turn ran strict-local); a sealed agent's
    prompt is never copied. `distill=false` answers at once without the 지시 (`distilled`
    「pending」); the form then asks `…/agent-draft/decided`."""
    from app.services import agent_draft as drafts
    from app.services import bases, doc_formats

    session = await _owned(db, user, session_id)
    lines, distilled, strict = await _draft_decided(db, user, session, distill=distill)
    agent = await db.get(WorkspaceAgent, session.agent_id) if session.agent_id else None
    readable = agent is not None and not agent.sealed and (
        agent.owner_id == user.id or (agent.visibility == AgentVisibility.org
                                      and agent.share_mode != "sealed"))
    is_admin = user.role is UserRole.admin
    key = await _agent_catalog_key(db, agent) if agent is not None else None
    # An official agent (or a copy of one) carries its instructions through the base: the 지시
    # starts from what this conversation settled only. A person's own agent's prompt is theirs
    # to keep.
    prompt = "\n\n".join(x for x in (
        agent.system_prompt.strip()
        if readable and agent is not None and not agent.catalog_key else "",
        drafts.prompt_from(lines)) if x)
    base = bases.get(key, drafts=is_admin) if key else None
    if base is None and agent is None:
        # Inferred from what the conversation made.
        fmt = doc_formats.TEMPLATE_FORMATS.get(session.render_template_id or "")
        guess = {"report": bases.BY_FORMAT.get(fmt or "", "document"),
                 "slides": "slides"}.get(session.kind.value)
        base = bases.get(guess, drafts=is_admin) if guess else None
    await user_templates.load(db, user.id, [session.render_template_id])
    template = design_templates.choosable(session.render_template_id)
    design_id = agent.design_system_id if agent is not None else None
    if design_id is None and session.project_id:
        project = await db.get(Project, session.project_id)
        design_id = project.design_system_id if project is not None else None
    # Every field as `POST /agents` checks it: a theme the person cannot use (an official
    # agent's, an administrator's) becomes their own of the same name, else none.
    design_id = await _usable_design(db, user, design_id)
    own_skills = set((await db.exec(select(Skill.id).where(Skill.owner_id == user.id))).all())
    files = (await db.exec(select(StoredFile).where(
        StoredFile.session_id == session.id, StoredFile.user_id == user.id,
        col(StoredFile.agent_id).is_(None), col(StoredFile.text) != "")
        .order_by(col(StoredFile.created_at)))).all()
    title = (session.title or "").strip()
    shown_template = template if template is not None and template.surface is session.kind \
        else None
    return {
        "name": drafts.name_for(title, shown_template.name if shown_template else ""),
        "description": "",
        "systemPrompt": prompt,
        "decided": lines,
        "kinds": [session.kind.value],
        "surface": session.kind.value,
        "renderTemplateId": template.id if template is not None and
        template.surface is session.kind else None,
        "designSystemId": design_id,
        "skillIds": [s for s in (session.skill_ids or []) if s in own_skills],
        "mode": "base" if base is not None else "direct",
        "base": base.key if base is not None else None,
        "bundles": (list(agent.bundles) if agent is not None and agent.bundles is not None
                    else list(base.bundles) if base is not None else []),
        "files": [{"id": f.id, "name": f.name, "size": f.size} for f in files],
        "fromAgentId": agent.id if agent is not None else None,
        "strictLocal": strict,
        "distilled": distilled,
    }


@router.patch("/{session_id}", response_model=SessionOut)
async def patch_session(session_id: str, payload: SessionPatch, user: CurrentUser, db: DbSession):
    session = await _owned(db, user, session_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("model") == "auto":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="auto_is_not_a_model_id",
        )
    if changes.get("routing_mode") in _AUTO_MODES and session.kind not in _AUTO_KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="auto_routing_chat_only",
        )
    # A model-only patch switches to manual.
    if "model" in changes and "routing_mode" not in changes:
        changes["routing_mode"] = RoutingMode.manual
    # Validate the post-patch state.
    if changes.get("routing_mode", session.routing_mode) in _AUTO_MODES:
        await _require_auto_quality_model(user, changes.get("model", session.model))
    if "render_template_id" in changes:
        if not await user_templates.usable(db, user.id, changes["render_template_id"]):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="design_template_not_found")
        changes["render_template_id"] = _resolved_template_id(
            changes["render_template_id"], session.kind, keep=session.render_template_id
        )
    if "project_id" in changes:
        await _validate_session_links(
            db,
            user,
            session.kind,
            project_id=changes["project_id"],
            agent_id=session.agent_id,
        )
    moved_project = "project_id" in changes and changes["project_id"] != session.project_id
    if changes.get("title") is not None:
        # Theirs from now on: the document's title no longer renames the conversation.
        # Clearing the name hands it back to the document.
        changes["title"] = " ".join(changes["title"].split())[:200]
        changes["title_set_by_user"] = bool(changes["title"])
    else:
        changes.pop("title", None)
    for field, value in changes.items():
        setattr(session, field, value)
    session.updated_at = utcnow()
    db.add(session)
    await db.commit()
    await db.refresh(session)
    if moved_project:
        # Its uploads follow it: out of the old project's collection, into the new home's.
        await reindex_session_files(db, user.id, session)
    return SessionOut.of(session)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str, user: CurrentUser, db: DbSession):
    session = await _owned(db, user, session_id)
    await db.exec(delete(Message).where(Message.session_id == session.id))
    # Job rows reference the session without cascade.
    await db.exec(delete(Job).where(Job.session_id == session.id))
    await _delete_artifacts_of(db, [session.id])
    await forget_session_files(db, [session.id])
    if session.index_key:
        await index_client.forget_collection(collection=session.index_key)
    await db.delete(session)
    await db.commit()


async def _delete_artifacts_of(db: DbSession, session_ids: list[str]) -> int:
    """Deletes what these conversations produced and returns how many artifacts went.

    A conversation and its artifacts are one record: deleting the one deletes the other,
    so nothing outlives the request that made it. Versions go first; shares cascade.
    """
    made = (
        await db.exec(select(Artifact.id).where(col(Artifact.session_id).in_(session_ids)))
    ).all()
    if made:
        await db.exec(delete(ArtifactVersion).where(col(ArtifactVersion.artifact_id).in_(made)))
        await db.exec(delete(Artifact).where(col(Artifact.id).in_(made)))
    return len(made)


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
    """Writes a picture or clip turn as an ordinary user/assistant message pair.

    Nothing made leaves the prompt unanswered; a partial batch keeps what arrived.
    """
    if not session.title:
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
        # Newest result for the panel, except on document surfaces where
        # `artifact_id` is the report or deck.
        if session.kind not in (SessionKind.report, SessionKind.slides):
            session.artifact_id = made[-1].id
    session.updated_at = utcnow()
    db.add(session)


@router.post("/{session_id}/figure-suggestion", response_model=FigureSuggestion)
async def suggest_figure(
    session_id: str, payload: FigureSuggestRequest, user: CurrentUser, db: DbSession
):
    """Proposes a caption and prompt for a figure. Nothing is drawn or charged."""
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
        look=payload.visual_style,
    )
    # No suggestion is not an error; the box stays empty.
    if figure is None:
        return FigureSuggestion(caption="", prompt="")
    return FigureSuggestion(
        caption=figure.caption,
        prompt=figure.prompt,
        figure=figure.figure,
        description=figure.description,
        style=figure.style,
    )


@router.post("/{session_id}/images", response_model=list[ArtifactOut])
async def generate_images(session_id: str, payload: ImageRequest, user: CurrentUser, db: DbSession):
    """Makes pictures and stores them as artifacts, charged from reported usage."""
    session = await _owned(db, user, session_id)
    catalogue = await model_service.list_models()
    if payload.style == "차트":
        # A chart is drawn from code, not painted: see `chart_code`.
        return await _draw_chart(session, payload, user, db, catalogue)
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
    # Image 서식 are retired; a client that still names one draws a plain picture.
    if payload.template_id and not design_templates.is_retired_media(payload.template_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="design_template_not_found"
        )
    # A language model expands the prompt unless the person sends back an edited one (`raw`).
    planned = payload.prompt
    if not payload.raw and payload.style != "없음":
        planner = model_service.find(catalogue["models"], session.model or "")
        if planner is None or "chat" not in planner.get("kinds", []):
            chat_models = sorted(
                (m for m in catalogue["models"] if "chat" in m.get("kinds", [])),
                key=model_service.fallback_order,
            )
            planner = chat_models[0] if chat_models else None
        if planner is not None:
            planned, _ = await imagegen.plan(
                payload.prompt,
                style=payload.style,
                labels=payload.labels,
                figure=payload.figure,
                model=str(planner["id"]),
                api_key=api_key,
            )
    composed = imagegen.compose_prompt(
        planned,
        aspect=payload.aspect,
        style=payload.style,
        design=design_service.image_clause(await design_for(db, user, session)),
        # A planned prompt already says it is one figure with no text.
        figure=payload.figure and planned == payload.prompt,
        square_only=not imagegen.honours_aspect(str(model["id"])),
    )

    made: list[Artifact] = []
    charged = 0
    failure: str | None = None
    for _ in range(payload.count):
        try:
            image = await imagegen.generate(
                base_url=base_url,
                api_key=api_key,
                model=model["id"],
                prompt=composed,
                aspect=payload.aspect,
            )
        except imagegen.ImageError as exc:
            # Images made before the failure are kept and billed.
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
                # Requested ratio beside the delivered one; the two can differ.
                "actualAspect": image.aspect,
                "width": image.width,
                "height": image.height,
                "style": payload.style,
                "labels": payload.labels,
                # What the model was sent; editable and resendable as `raw`.
                "composedPrompt": composed,
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


async def _draw_chart(
    session: ChatSession, payload: ImageRequest, user: User, db: DbSession, catalogue: dict
) -> list[ArtifactOut]:
    """The 차트 style: matplotlib code from a language model, run in the sandbox.

    Billed as the language model's tokens; the code is stored as `composedPrompt` for re-runs.
    """
    writer = model_service.find(catalogue["models"], session.model or "")
    if writer is None or "chat" not in writer.get("kinds", []):
        chat_models = sorted(
            (m for m in catalogue["models"] if "chat" in m.get("kinds", [])),
            key=model_service.fallback_order,
        )
        if not chat_models:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_chat_models"
            )
        writer = chat_models[0]
    if not has_headroom(user, writer):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )
    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)
    try:
        chart = await chart_code.draw(
            payload.prompt,
            aspect=payload.aspect,
            model=str(writer["id"]),
            api_key=api_key,
            code=payload.prompt if payload.raw else None,
        )
    except chart_code.ChartError as exc:
        _record_media(db, session, payload.prompt, [], model=str(writer["id"]), failed=True)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    image = imagegen.GeneratedImage(
        data=chart.png,
        mime="image/png",
        input_tokens=chart.input_tokens,
        output_tokens=chart.output_tokens,
    )
    image.width, image.height = imagegen._measure(chart.png)
    file_id, key = imagegen.store(user.id, image)
    db.add(
        StoredFile(
            id=file_id,
            user_id=user.id,
            session_id=session.id,
            name=f"{payload.prompt[:40] or 'chart'}.png",
            mime="image/png",
            size=len(chart.png),
            storage_key=key,
            tokens=0,
        )
    )
    artifact = Artifact(
        user_id=user.id,
        session_id=session.id,
        project_id=session.project_id,
        kind=ArtifactKind.image,
        title=(payload.prompt if not payload.raw else "차트")[:200] or "차트",
        data={
            "kind": "image",
            "jobId": None,
            "prompt": payload.prompt if not payload.raw else "차트",
            "aspect": payload.aspect,
            "actualAspect": image.aspect,
            "width": image.width,
            "height": image.height,
            "style": "차트",
            "labels": payload.labels,
            "engine": "matplotlib",
            "composedPrompt": chart.code,
            "seed": 0,
            "model": str(writer["id"]),
            "src": f"{settings.api_prefix}/files/{file_id}/content",
        },
    )
    db.add(artifact)
    charged = charge_for_tokens(writer, chart.input_tokens, chart.output_tokens)
    if charged:
        settle(
            db,
            user,
            charged,
            reason="image.chart",
            session_id=session.id,
            model=str(writer["id"]),
            surface=session.kind.value,
        )
    _record_media(db, session, payload.prompt, [artifact], model=str(writer["id"]), credits=charged)
    await db.commit()
    await db.refresh(artifact)
    return [ArtifactOut.of(artifact)]


@router.post("/{session_id}/diagrams", response_model=DiagramOut)
async def write_diagram(session_id: str, payload: DiagramRequest, user: CurrentUser, db: DbSession):
    """Writes a labelled figure as flowchart source, draws it with the deck export's shapes and
    keeps it as an image artifact. 422 when it cannot be drawn so; there is no browser path."""
    session = await _owned(db, user, session_id)
    catalogue = await model_service.list_models()
    usable = sorted(
        (m for m in catalogue["models"] if "chat" in m["kinds"]),
        key=model_service.fallback_order,
    )
    # `session.model` is a picture model on this surface; a chat model is needed.
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

    png: bytes | None = None
    failure = ""
    try:
        source, caption, usage, png = await diagram_service.draw_native(
            description=payload.description,
            figure=payload.figure,
            model=model["id"],
            api_key=api_key,
            language=payload.language,
        )
    except diagram_service.Undrawable as exc:
        # Not drawable with the shapes even after one rewrite, or no renderer: no picture,
        # and no browser drawing in its place. What the writer spent is still charged.
        log.warning("diagram not drawn: %s", exc.reason)
        source, caption, usage, failure = "", "", exc.usage, exc.reason
    except Exception as exc:  # noqa: BLE001 — the caller gets a reason, not a 500
        log.warning("diagram failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="diagram_failed"
        ) from exc

    charged = charge_for_tokens(
        model,
        int(usage.get("inputTokens") or usage.get("prompt_tokens") or 0),
        int(usage.get("outputTokens") or usage.get("completion_tokens") or 0),
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
    if png is None:
        if charged:
            await db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="diagram_not_drawable" if failure == "not_drawable" else "diagram_unrendered",
        )
    # Drawn with the deck file's own shapes, as the report and deck writers draw theirs.
    width, height = _png_size(png)
    artifact = _store_figure(
        db, user, session, png=png, width=width, height=height, source=source,
        caption=caption, description=payload.description, figure=payload.figure,
        title=(caption or payload.description.split("\n")[0][:80]), model=str(model["id"]),
        renderer="pptx",
    )
    await db.commit()
    await db.refresh(artifact)
    return DiagramOut(
        source=source, caption=caption, model=model["id"], credits=charged,
        artifact=ArtifactOut.of(artifact),
    )


def _png_size(png: bytes) -> tuple[int, int]:
    """Width and height of a PNG from its header; zeros when it is not one."""
    if png[:8] != b"\x89PNG\r\n\x1a\n" or len(png) < 24:
        return 0, 0
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def _store_figure(
    db: AsyncSession,
    user: User,
    session: ChatSession,
    *,
    png: bytes,
    width: int,
    height: int,
    source: str,
    caption: str,
    description: str,
    figure: str,
    title: str,
    model: str,
    renderer: str = "",
) -> Artifact:
    """A drawn figure kept as an image artifact with its mermaid source; added, not committed."""
    file_id = uuid.uuid4().hex
    key = file_service.write_blob(user.id, file_id, "figure.png", png)
    title = (title or caption or "도식")[:200]
    db.add(
        StoredFile(
            id=file_id,
            user_id=user.id,
            session_id=session.id,
            name=f"{title[:40]}.png",
            mime="image/png",
            size=len(png),
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
            "prompt": description,
            "aspect": "16:9",
            "actualAspect": f"{width}:{height}",
            "width": width,
            "height": height,
            "style": "figure",
            "seed": 0,
            "model": model,
            "src": f"{settings.api_prefix}/files/{file_id}/content",
            # The source is the artifact; the PNG is one rendering.
            "figure": figure,
            "source": source,
            "caption": caption,
            # `pptx`: drawn by the server with the deck export's shapes.
            **({"renderer": renderer} if renderer else {}),
        },
    )
    db.add(artifact)
    _record_media(db, session, description, [artifact], model=model, credits=0)
    return artifact


@router.post("/{session_id}/audio", response_model=ArtifactOut)
async def generate_audio(session_id: str, payload: AudioRequest, user: CurrentUser, db: DbSession):
    """Makes one sound clip (speech or music) and stores it as an artifact."""
    session = await _owned(db, user, session_id)
    speech = payload.audio_kind == "narration"
    catalogue = await model_service.list_models()

    def _audio_models():
        return [m for m in catalogue["models"] if "av" in m["kinds"]]

    model = model_service.find(catalogue["models"], payload.model or "")
    # The model must match the requested kind; a mismatch is a proxy 400.
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
        # The prompt is recorded even when nothing came of it.
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
            # Requested length beside the delivered one.
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
    """Deletes many conversations in one request. `all` is separate from an id list."""
    query = select(ChatSession).where(owned_by(user.id))
    if not payload.all:
        if not payload.ids:
            return {"deleted": 0}
        query = query.where(col(ChatSession.id).in_(payload.ids))
    rows = (await db.exec(query)).all()
    if not rows:
        return {"deleted": 0}

    ids = [row.id for row in rows]
    await db.exec(delete(Message).where(col(Message.session_id).in_(ids)))
    # Job rows reference the session without cascade.
    await db.exec(delete(Job).where(col(Job.session_id).in_(ids)))

    artifacts_deleted = await _delete_artifacts_of(db, ids)
    await forget_session_files(db, ids)
    for row in rows:
        if row.index_key:
            await index_client.forget_collection(collection=row.index_key)

    await db.exec(delete(ChatSession).where(col(ChatSession.id).in_(ids)))
    await db.commit()
    return {"deleted": len(ids), "artifactsDeleted": artifacts_deleted}


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(session_id: str, user: CurrentUser, db: DbSession):
    await _owned(db, user, session_id)
    links = await message_links.run_links(db, session_id)
    return [message_links.linked(m, links) for m in await _history(db, session_id)]


def _earlier_answer(row: Message) -> dict[str, Any]:
    """An answer as the row that replaces it carries it: words, steps, artifacts, route."""
    return {
        "id": row.id,
        "content": row.content,
        "model": row.model,
        "usage": row.usage,
        "steps": row.steps,
        "artifactIds": row.artifact_ids,
        "routing": row.routing,
        "rating": row.rating.value if row.rating else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def _regeneration_summary(request: str) -> str:
    """Distinct version label derived from the regeneration request."""
    # First line only; the conditions block `merge_answers` appends is noise here.
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
    """Store same-kind regeneration as a version; create a row for a new kind.

    A report or slides conversation takes the document's title as its own name, unless the
    person renamed the conversation.
    """
    follow_document_title(session, title)
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
    #: The figure question, asked only after the outline is approved.
    figures: dict | None = None,
    #: A restructure of an existing document: the artifact whose untouched parts the
    #: approved rewrite carries over (`revise.carry_parts`).
    carry_from: str | None = None,
    #: The restructure instruction itself; parts it names are written fresh.
    carry_note: str = "",
    #: The planning pass's stage records (`_run_summary`), kept on the message.
    run: dict | None = None,
    #: The run a card waited on before this pass, carried to the writing pass.
    run_parent: str | None = None,
) -> None:
    """Stores a turn that planned or asked, and charges for the planning call.

    No artifact is created and `session.artifact_id` is untouched.
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
            # The approved outline is carried through the figure question.
            **(figures or {}),
            **({"carryFrom": carry_from, "carryNote": carry_note} if carry_from else {}),
            **({"runId": run_parent} if run_parent and not questions else {}),
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
                **({"routing": {"run": run}} if run else {}),
            )
        )
        settle(
            db,
            user,
            credits,
            reason="document.plan",
            session_id=session_id,
            model=(outline_model or model)["id"],
            # The reason does not name the surface; the session does.
            surface=session.kind.value,
        )
        session.updated_at = utcnow()
        db.add(session)
        await db.commit()


#: Above any surface's real outline size; refuses a bad payload, never bounds a real one.
_MAX_PLANNED = 60


def _edited_plan(sent: dict | None, stored: dict) -> dict:
    """The stored outline with the person's edits folded in.

    Titles and order come from the browser; layouts and every other key from the proposal.
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
        # Layouts must be ones the proposal already used.
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
    if visual_style in design_service.VISUAL_STYLES:
        out["visualStyle"] = visual_style
    density = str(sent.get("density") or "").strip()
    if density in ("speaker", "reading"):
        out["density"] = density
    return out


def _with_condition(request: str, line: str) -> str:
    """`request` with one more line in its 「덧붙인 조건」 block, opening one if needed."""
    if line in request:
        return request
    if "\n\n덧붙인 조건:\n" in request:
        return f"{request}\n- {line}"
    return grounding.merge_answers(request, {"_server": line})


async def _agent_catalog_key(db: DbSession, agent: WorkspaceAgent | None) -> str | None:
    """The shipped agent whose process `agent` runs, for its checks, question card and
    pipeline: itself, the official base a person's own agent starts from (`base_key`), or
    the one it was copied from."""
    if agent is None:
        return None
    if agent.catalog_key:
        return agent.catalog_key
    if agent.base_key:
        return agent.base_key
    if not agent.origin_id:
        return None
    origin = await db.get(WorkspaceAgent, agent.origin_id)
    if origin is None:
        return None
    return origin.catalog_key or origin.base_key


async def _skill_specs(
    db: DbSession, user: User, agent: WorkspaceAgent | None,
) -> tuple[dict[str, skill_specs.Spec], dict[str, dict[str, Any]]]:
    """What the server runs for the turn's skills and the person's own scripts
    (`skill_specs.for_turn`)."""
    return await skill_specs.for_turn(
        db, user.id, agent_checks=agent.check_skills if agent is not None else None,
        agent_owner=agent.owner_id if agent is not None else "")


def _skill_keys(applied, specs: dict[str, skill_specs.Spec]) -> tuple[str, ...]:
    """The turn's skills by the key the checks read: a shipped copy's catalogue key, a
    person's `skill:<id>` when its fields ask the server for something."""
    out = []
    for skill in applied:
        if skill.catalog_key:
            out.append(skill.catalog_key)
        elif (key := f"{skill_specs.USER}{skill.id}") in specs:
            out.append(key)
    return tuple(out)


def render_template_checks(session: ChatSession) -> tuple[str, ...]:
    """The check skills the session's 서식 attaches."""
    template = design_templates.get(session.render_template_id)
    return template.check_skills if template is not None else ()


def _gap_judge_model(user: User, catalogue_models: list[dict],
                     requested: dict | None, planner: str | None = None) -> str:
    """The model that judges 빠진 정보 질문 of the judge type: the turn's own when it is
    strict-local, else the planner's strict-local entry, else the first strict-local chat
    model the person may use; "" for none.

    The planner is the judging model elsewhere; on 60 questions it found every answer the
    material held (the 27B missed 2), so it judges here too when the person may use it."""
    if requested and requested.get("strictLocal"):
        return str(requested["id"])
    usable = _allowed_models(user, catalogue_models, kind="chat")
    if planner:
        twin = ("strict-local/" + planner.removeprefix("local/")
                if planner.startswith("local/") else planner)
        found = model_service.find(usable, twin)
        if found is not None and found.get("strictLocal"):
            return str(found["id"])
    return next((str(m["id"]) for m in sorted(usable, key=model_service.fallback_order)
                 if m.get("strictLocal")), "")


def _today() -> dt.date:
    return dt.datetime.now(ZoneInfo(settings.timezone)).date()


async def _ask_before_writing(
    db: DbSession,
    session: ChatSession,
    *,
    request: str,
    typed: str,
    attachments: list[str],
    questions: list[dict],
    pre: PipelineBundle | None = None,
) -> JSONResponse:
    """Persist a clarification question without generating a document.

    The card is the `pre` run's `gap-card` stage, waiting: the run is kept, and its id rides
    on the pending turn so the pass the answers start is filed under it."""
    run_id = uuid4().hex
    session.pending = {
        "stage": "clarify",
        "request": request,
        "attachments": attachments,
        "questions": questions,
        "answers": {},
        "runId": run_id,
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
    pipeline = str((pre.request.turn.get("pipeline") if pre is not None else "") or "")
    summary = (_run_summary(pre, pipeline, "pre", run_id=run_id)
               if pre is not None else None)
    for row in (summary or {}).get("stages") or []:
        row["runId"], row["id"] = run_id, f"{run_id}:{row['name']}:{row['attempt']}"
    assistant = Message(
        session_id=session.id,
        role=Role.assistant,
        content=said,
        # Read by `_last_card`, so the same card is never put up twice in a row.
        routing={
            "askedCard": {
                "ids": [q["id"] for q in questions],
                "attachments": sorted(attachments),
            },
            **({"run": summary} if summary else {}),
        },
    )
    db.add(assistant)
    await db.commit()
    waiting = next((r for r in (pre.steps if pre is not None else [])
                    if r.status == "waiting"), None)
    if pre is not None:
        await _persist_run(pre, _waiting_recorder(pre, run_id), session_id=session.id,
                           user_id=session.user_id, saved={"messageId": assistant.id},
                           pipeline=pipeline, flow="pre")
    # The sentence travels with the questions so the in-flight bubble is not empty.
    return JSONResponse({
        "pending": session.pending, "message": said, "runId": run_id,
        **({"stage": {**waiting.stage_wire(), "runId": run_id,
                      "id": f"{run_id}:{waiting.stage}:{waiting.attempt}"}}
           if waiting is not None else {}),
    })


def _waiting_recorder(pre: PipelineBundle, run_id: str) -> pipeline_runs.Recorder:
    """A recorder for a `pre` run that is kept but never rerun: records, no snapshots."""
    recorder = pipeline_runs.Recorder(run_id=run_id)
    for record in pre.steps:
        record.run_id = run_id
    return recorder


async def _close_waiting_run(run_id: str, *, answers: dict[str, str], as_is: bool,
                             blanks: int) -> None:
    """The card answered: its `gap-card` record done, saying how (best effort)."""
    try:
        async with SessionLocal() as db:
            run = await db.get(PipelineRun, run_id)
            if run is None or run.status != "waiting":
                return
            given = sum(1 for v in answers.values() if str(v or "").strip())
            said = (f"있는 자료로 진행 · 빈칸 {blanks}개" if as_is else f"답 {given}개")
            rows = []
            for row in run.stages or []:
                if row.get("status") == "waiting":
                    row = {**row, "status": "done", "summary": said, "reason": ""}
                rows.append(row)
            run.stages = rows
            run.status = "done"
            run.updated_at = utcnow()
            db.add(run)
            # The card's message carries the compact run; a reloaded transcript shows it answered.
            message = await db.get(Message, run.message_id) if run.message_id else None
            compact = (message.routing or {}).get("run") if message is not None else None
            if isinstance(compact, dict) and compact.get("id") == run_id:
                stages = [{**row, "status": "done", "summary": said, "reason": ""}
                          if row.get("status") == "waiting" else row
                          for row in compact.get("stages") or []]
                message.routing = {**message.routing,
                                   "run": {**compact, "status": "done", "stages": stages}}
                db.add(message)
            await db.commit()
    except Exception:  # noqa: BLE001
        log.exception("waiting run %s not closed", run_id)


async def _gaps(keys: tuple[str, ...], *, questions: list[dict], request: str, material: str,
                first_draft: bool, doc_format: str | None, agent_key: str | None,
                card: dict | None = None, specs: dict | None = None,
                judge: tuple[str, str] = ("", "")) -> PipelineBundle:
    """The `pre` flow of the turn's pipeline: what the format needs and the material does
    not give (`check:gaps`), and the card it puts up (`gap-card`, which waits). The bundle
    comes back with `findings.gaps`, `findings.questions` and the stage records."""
    bundle = PipelineBundle(request=PipelineRequest(
        text=request, model=judge[0], api_key=judge[1], check_keys=tuple(keys),
        gap_questions=list(questions),
        check_material=material, today=_today(), first_draft=first_draft,
        turn={"card": card} if card is not None else {}, skill_specs=dict(specs or {})))
    pipe = (pipeline_registry.for_report(doc_format) if doc_format
            else pipeline_registry.for_chat(agent_key))
    runner = PipelineRunner(pipe.stages("pre"), name=f"{pipe.name}.pre", emit_records=False)
    async for _event in runner.run(bundle):
        pass
    bundle.request.turn["pipeline"] = pipe.name
    return bundle


def _last_card(history: list[Message], attachments) -> list[str]:
    """The question ids of the card the last answer put up, when over the same files."""
    last = next((m for m in reversed(history) if m.role is Role.assistant), None)
    card = ((last.routing or {}).get("askedCard") or {}) if last is not None else {}
    if sorted(card.get("attachments") or []) != sorted(attachments or []):
        return []
    return [str(x) for x in card.get("ids") or []]


def _freshness_routing(reason: str) -> dict[str, Any]:
    return {
        "answerOrigin": "server_policy",
        "actualModel": None,
        "freshness": {"status": "unverified", "reason": reason},
    }


def _freshness_followup_index(history: list[Message], session_id: str, content: str) -> int | None:
    """Retain search consent for a short same-topic nudge, including legacy holds."""
    if not freshness.is_same_fact_followup(content):
        return None
    for index in range(len(history) - 2, -1, -2):
        question, answer = history[index : index + 2]
        routing = answer.routing or {}
        verification = routing.get("freshness") or {}
        grounded = (routing.get("accuracy") or {}).get("policy") == freshness.ACCURACY_POLICY
        legacy_hold = (
            answer.model is None
            and routing.get("answerOrigin") == "server_policy"
            and routing.get("actualModel") is None
            and verification.get("status") == "unverified"
        )
        if (
            question.session_id != session_id or answer.session_id != session_id
            or question.role is not Role.user or answer.role is not Role.assistant
            or not (grounded or legacy_hold)
        ):
            return None
        if not freshness.is_same_fact_followup(question.content) and (
            grounded or freshness.fresh_fact_required(question.content)
        ):
            return index
        if not freshness.is_same_fact_followup(question.content):
            return None
    return None


def _plans_first(session: ChatSession) -> bool:
    """Whether this surface offers an outline before writing (report and slides)."""
    return session.kind in (SessionKind.report, SessionKind.slides)


@router.post("/{session_id}/messages")
async def send_message(
    session_id: str, payload: SendMessage, request: Request, user: CurrentUser, db: DbSession
):
    # A visitor's turn on a published bot (`routers/publications`): the publisher's account,
    # the bot's fixed agent copy, a strict-local model, and nothing else of the owner's.
    published = _PUBLISHED.get()
    if published is not None:
        session = await _visited(db, user, session_id, published)
        # What a visitor may send is a question: no files, model, skills, search or 서식.
        payload = SendMessage(content=payload.content, retry_of=payload.retry_of)
    else:
        session = await _owned(db, user, session_id)

    #: The user message being rerun; its stored words and attachments replace the client's.
    retry_of: Message | None = None
    if payload.retry_of:
        retry_of = await db.get(Message, payload.retry_of)
        if retry_of is None or retry_of.session_id != session.id or retry_of.role is not Role.user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="retry_target_not_found"
            )
        # Retry is a replay of the stored turn, not merely of its sentence.
        turn_options = (retry_of.routing or {}).get("turnOptions") or {}
        payload = payload.model_copy(
            update={
                "content": retry_of.content,
                "attachments": payload.attachments
                or [a["id"] for a in (retry_of.attachments or []) if a.get("id")]
                or None,
                "web_search": turn_options.get("webSearch", payload.web_search),
                "activated_skill_ids": turn_options.get(
                    "activatedSkillIds", payload.activated_skill_ids
                ),
            }
        )

    # A document surface plans before it writes; `session.pending` holds the
    # half-finished turn, and a message while one is pending is a note on it.
    # A chat agent's gap card (`check_skills`) waits the same way, and only at that stage.
    pending = (
        dict(session.pending or {})
        if _plans_first(session) or (session.pending or {}).get("stage") == "clarify"
        else {}
    )
    #: Set only via approval; a plain message never carries one.
    approved_plan: dict | None = None
    #: Set by the figure card; `None` until either button is pressed.
    approved_figures: list[dict] | None = None
    #: A message working on the finished document rather than asking for a new
    #: one. `services.revise` decides what it lands on.
    revising = bool(
        _plans_first(session)
        and session.artifact_id
        and not pending
        and not payload.approve
        and payload.answers is None
        and not revise.obviously_new(payload.content)
    )
    focus = ""
    #: What the person typed, as opposed to the merged request the model gets.
    typed_content = payload.content
    #: A restructure of the document on screen: its untouched parts are carried over.
    carry_from: str | None = (str(pending.get("carryFrom") or "") or None) if pending else None
    carry_note: str = str(pending.get("carryNote") or "") if pending else ""
    #: "있는 자료로 진행" sends an empty answers object; it tells the next pass not to ask again.
    proceed_as_is = bool(
        pending and not payload.approve and payload.answers is not None and not payload.answers
    )
    #: Files the reply brings that the pending turn did not have; only those are checked again.
    reply_brings_files = bool(
        pending
        and payload.attachments
        and set(payload.attachments) != set(pending.get("attachments") or [])
    )
    if pending:
        answers = {**(pending.get("answers") or {}), **(payload.answers or {})}
        if pending.get("stage") == "clarify" and not payload.approve and payload.answers is None:
            # A typed reply answers the card's open questions; left unanswered, the next
            # pass would put the same card up again. 「자료대로 진행해 줘」 answers no fact, and
            # one reply to several facts is a note on all of them, not each one's value.
            open_ids = [str(q.get("id") or "") for q in pending.get("questions") or []]
            facts_asked = [q for q in open_ids if q.startswith(check_skills.GAP_PREFIX)]
            for qid in open_ids:
                if not qid or str(answers.get(qid) or "").strip() or not typed_content.strip():
                    continue
                if qid.startswith(check_skills.GAP_PREFIX) and (
                        check_skills.goes_on(typed_content) or len(facts_asked) > 1):
                    continue
                answers[qid] = typed_content.strip()
        pending["answers"] = answers
        focus = grounding.focus_terms(answers)
        # Approval is the one path that does not plan again.
        if payload.approve and pending.get("plan"):
            approved_plan = _edited_plan(payload.plan, dict(pending["plan"]))
        # Pictures agreed to, or `[]` when declined.
        if pending.get("stage") == "figures" and payload.include_figures is not None:
            approved_figures = list(pending.get("figures") or []) if payload.include_figures else []
        # A fact the gap card asked reads as that fact: 「회의 일시: 10월 7일」.
        shown = check_skills.labelled(answers, list(pending.get("questions") or []))
        # The card's own label for picked answers is not a second note.
        note = "" if payload.answers and typed_content.strip() == " · ".join(
            str(v).strip() for v in payload.answers.values()) else typed_content
        payload = payload.model_copy(
            update={
                "content": grounding.merge_answers(
                    str(pending.get("request") or ""),
                    # An approval is not a condition on the request.
                    shown if payload.approve else {**shown, "_note": note},
                ),
                # A reply carries the original request's attachments.
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

    # Refused before any write, never silently dropped.
    if payload.render_template_id is not None:
        if not await user_templates.usable(db, user.id, payload.render_template_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="design_template_not_found")
        session.render_template_id = _resolved_template_id(
            payload.render_template_id, session.kind, keep=session.render_template_id)
    # The conversation's own template (a person's), loaded in this process for the turn.
    await user_templates.load(db, user.id, [session.render_template_id])

    # One answer at a time: a second message while this process is still
    # writing one (and nobody pressed stop) would be stored before the first
    # reply and leave the transcript as question, question, answer, answer.
    # A stopped turn is already on its way out, so a message after 중단 goes through.
    if session.kind is SessionKind.chat and any(
        not signal.is_set() for signal in _STOPPING.get(session.id, set())
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session_busy")

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
    # Model precedence: turn override, then session, then agent.
    try:
        agent_model, agent_tools, agent_temperature = await agent_settings(db, user, session)
        # 0.4 unless the agent sets its own: keeps the house style consistent.
        if agent_temperature is None:
            agent_temperature = 0.4
    except WorkspaceContextError as exc:
        _raise_workspace_error(exc)
    model_id = session.model if auto_turn else payload.model or session.model or agent_model
    if published is not None:
        # The version's strict-local model, or no answer: never a fallback that may leave.
        model_id = published.model_id
        fixed = model_service.find(usable, model_id)
        if fixed is None or not _strict_model(fixed):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="published_model_unavailable")
        agent_tools = [t for t in (agent_tools or []) if t in publishing.PUBLISHABLE_TOOLS]
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
        # Auto is a ceiling, not permission to substitute a cheaper model.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="auto_quality_model_required",
        )
    # Kept so routing metadata and the transcript can name the substitute.
    revoked_model = model if model is not None and model not in usable else None
    if model not in usable:
        model = None
    if model is None:
        # Fallback stays inside the allowlist.
        usable = sorted(usable, key=model_service.fallback_order)
        if not usable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
            )
        model = usable[0]
    requested_model = model

    history = await _history(db, session_id)
    # With several replicas the in-process signals above see only this one's turns;
    # the transcript every replica shares says whether a fresh question is still
    # being answered elsewhere. One replica needs no such guess.
    if (
        session.kind is SessionKind.chat
        and retry_of is None
        and stop_signal.needed()
        and history
        and _answer_in_flight(history[-1])
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session_busy")
    #: What a retry replaces: the failed reply, if any. Neither it nor the
    #: question stays in the model's history.
    superseded: list[Message] = []
    if retry_of is not None:
        at = next((i for i, m in enumerate(history) if m.id == retry_of.id), None)
        if at is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="retry_target_not_found"
            )
        # Only the latest question may be rerun.
        if any(m.role is Role.user for m in history[at + 1 :]):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="retry_not_latest")
        superseded = history[at + 1 :]
        history = history[:at]

    # Ownership resolved before privacy assembly.
    rows, attachment_meta = await _owned_attachments(db, user, payload.attachments)

    #: Skills this turn runs with: the ones picked for it, else the conversation's
    #: standing set. A pick replaces the standing set from here on.
    active_skill_ids = list(payload.activated_skill_ids)

    stored_content = content
    outbound_history = [message.content for message in history]
    privacy_resolution: _PrivacyResolution | None = None
    tools: list[Tool] = []
    tool_definitions: list[dict[str, Any]] = []
    candidate_tools: list[Tool] = []
    strict_tools: list[Tool] = []

    # Tool definitions are outbound prompt data, so they are built before the
    # privacy decision.
    agent_row: WorkspaceAgent | None = None
    shelf: list[tuple[str, str, str | None]] = []
    shelf_key = ""
    if session.agent_id:
        agent_row = await db.get(WorkspaceAgent, session.agent_id)
    # What the server runs for the turn's skills, and the person's own scripts the sandbox
    # may run through `run_skill_script`.
    specs, own_scripts = await _skill_specs(db, user, agent_row)
    if published is not None:
        active_skill_ids = []
    elif not active_skill_ids and session.kind is SessionKind.chat:
        # The standing set, pruned of skills removed or barred since they were switched on.
        active_skill_ids = await _standing_skill_ids(db, user, session, agent_row)
    if session.kind is SessionKind.chat and requested_model.get("supportsTools"):
        shelf, shelf_key = await _knowledge_shelf(db, user, session, agent_row)

    requested_is_strict = _strict_model(requested_model)
    strict_candidate_available = _strict_route_possible(
        requested_model, catalogue_models, policy, user, auto_turn=auto_turn
    )
    # What the toggle (on / off / auto) and the sentence mean for this turn: whether the
    # web tools are offered, and the tool the first hop must call. Resolved before
    # tools are built.
    if pending and payload.web_search is False:
        # An approval or an answer to the planner's question carries no search toggle;
        # the turn that started the plan chose it, and the writing pass researches too.
        payload = payload.model_copy(update={"web_search": _planned_web_search(history)})
    effective_web_search, forced_tool = search_plan(payload.web_search, content)
    if published is not None:
        # A bot answers from the material it was published with; it has no web to look in.
        effective_web_search, forced_tool = False, None
    fresh_followup_index = (
        _freshness_followup_index(history, session.id, content)
        if session.kind is SessionKind.chat and not payload.attachments and published is None
        else None
    )
    if (
        fresh_followup_index is not None
        and declines_web_search(history[fresh_followup_index].content)
        and payload.web_search is not True
        and not requests_web_search(content)
    ):
        # Auto is not renewed consent for the same explicitly offline question.
        effective_web_search, forced_tool = False, None
    fresh_fact = published is None and (freshness.current_fact_required(content) or (
        fresh_followup_index is not None
        and freshness.current_fact_required(history[fresh_followup_index].content)
    ))
    #: Whether the first search below was forced by the current-fact rule alone.
    forced_by_freshness = False
    if fresh_fact and effective_web_search and forced_tool != "weather":
        # The request is for a current fact, not for the model to decide whether
        # verification is necessary. Availability is checked again after privacy.
        forced_by_freshness = forced_tool is None
        forced_tool = "web_search"
    web_search_auto = payload.web_search == "auto" and forced_tool is None
    # An agent whose allowlist leaves web search out chose that on purpose. The toggle
    # is moot for it, and a 「웹 검색 없이 답합니다」 preamble on every answer would
    # only be noise about a tool the agent was never meant to have.
    if session.agent_id and agent_tools is not None and "web_search" not in agent_tools:
        effective_web_search = False
    if session.kind is SessionKind.chat and requested_model.get("supportsTools"):
        skill_shelf = [] if published is not None else await _skill_shelf(
            db, user, agent=agent_row, exclude=set(active_skill_ids)
        )
        if not requested_is_strict:
            candidate_tools = sorted(
                await build_tools(
                    db,
                    user,
                    web_search=effective_web_search,
                    allowed=agent_tools,
                    knowledge=shelf,
                    knowledge_collection=shelf_key,
                    skills=skill_shelf,
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
                        skills=skill_shelf,
                    )
                ),
                key=lambda tool: (tool.name, tool.source),
            )
        if own_scripts:
            # The person's own scripts join the shipped ones in `run_skill_script`'s choices.
            candidate_tools = sandbox_tools.with_own_scripts(candidate_tools, own_scripts)
            strict_tools = sandbox_tools.with_own_scripts(strict_tools, own_scripts)

    requested_tools = strict_tools if requested_is_strict else candidate_tools
    # A question about using this app is answered from its guide alone (below): memories and
    # 개인 맞춤 설정 stay out of that turn, or the answer passes them on as advice.
    how_to = session.kind is SessionKind.chat and app_help.about_app(content) and published is None
    try:
        workspace = await assemble(
            db,
            user,
            session,
            published=published is not None,
            attachment_ids=payload.attachments,
            # Chat re-assembles below against the model privacy settles on.
            vision=reads_pictures(requested_model),
            file_budget=file_budget(requested_model),
            activated_skill_ids=active_skill_ids,
            # Empty focus takes the head of the file.
            focus=focus or (content if session.kind is not SessionKind.chat else ""),
            question=_asked_about(content, history),
            how_to=how_to,
            # Report and deck writers do not run the chat tool loop.
            available_tool_names=(
                {tool.name for tool in requested_tools}
                if session.kind is SessionKind.chat
                else set()
            ),
        )
    except WorkspaceContextError as exc:
        _raise_workspace_error(exc)

    if (
        fresh_fact
        and session.kind is SessionKind.chat
        and (
            (_grounded_in_files(workspace) and not freshness.names_the_present(content))
            or _reads_pasted_material(content)
        )
    ):
        # A question about an attached or pasted document asks what it says, not what holds
        # today: read it rather than look it up or withhold it.
        fresh_fact = False
        if forced_by_freshness and forced_tool == "web_search":
            forced_tool = None
            web_search_auto = payload.web_search == "auto"

    requested_tools = strict_tools if requested_is_strict else candidate_tools

    # Attachment shortfalls are known server-side, so ask before spending a planning call.
    # A message while a turn is pending was checked when that turn began; it is asked
    # about again only for files it brings itself.
    short = grounding.file_shortfalls(workspace.attachments) if _plans_first(session) else []
    # What the format needs and the material does not give (the attached gap check): asked on
    # the same card, once, before the first draft — never on a revision or a follow-up chat turn.
    agent_key = await _agent_catalog_key(db, agent_row)
    # The agent's own checks and strictness, as its row holds them (catalogue or not).
    check_keys = check_skills.attached(
        agent_checks=(agent_row.check_skills or []) if agent_row is not None else [],
        template_checks=render_template_checks(session), specs=specs)
    check_options = check_skills.clean_options(
        agent_row.check_options if agent_row is not None else None)
    # A conversation bound to a user pipeline asks through its own steps (`human:gaps`).
    from app.routers import user_pipelines

    user_pipe = (await user_pipelines.bound(db, user, session, agent_row)
                 if published is None else None)
    if user_pipe is not None:
        check_keys = ()
    if published is not None:
        # A visitor gets the answer; the checks after writing run, a question card does not.
        check_keys = tuple(check_skills.of_stage(check_keys, "post", specs))
    if agent_row is not None and agent_row.contact.strip():
        # 담당 부서·연락처, said verbatim by the checks that point to it.
        check_options = {**check_options, "contact": agent_row.contact.strip()}
    # 빠진 정보 질문: the 서식's (a report's) and the agent's own, else its base's.
    asking = gap_questions.resolve(
        own=agent_row.gap_questions if agent_row is not None else None, base_key=agent_key,
        template_id=session.render_template_id if session.kind is SessionKind.report else None,
        doc_format=workspace.doc_format) if check_skills.of_stage(check_keys, "pre", specs) else []
    judge_model, judge_key = "", ""
    if gap_questions.needs_judge(asking):
        # The judge reads the material before the privacy route is settled: a model that keeps
        # it in the boundary, or nothing is asked on a guess.
        judge_model = _gap_judge_model(user, catalogue_models, requested_model,
                                       policy.outline_model_id)
        judge_key = (await litellm_service.credentials_for(user))[1] if judge_model else ""
    first_draft = not revising and not approved_plan and (_plans_first(session) or not any(
        m.role is Role.assistant and not (m.routing or {}).get("askedCard") for m in history))
    # The card's file questions: what did not arrive, or the attachment the request names.
    file_questions = grounding.questions_for(short)
    if not file_questions and (
            gap := grounding.missing_attachment(content, workspace.attachments)):
        file_questions = [gap]
    pre = await _gaps(check_keys, questions=asking, judge=(judge_model, judge_key),
                      request=content,
                      material="\n".join(workspace.untrusted), first_draft=first_draft,
                      doc_format=workspace.doc_format, agent_key=agent_key, specs=specs, card={
                          "plans_first": _plans_first(session),
                          "may_ask": not pending or reply_brings_files,
                          "files": [q.wire() for q in file_questions],
                          # The card just answered is not put up again, whatever the reply said.
                          "asked": _last_card(history, payload.attachments),
                      })
    missing = pre.findings.gaps
    if pre.findings.questions:
        return await _ask_before_writing(
            db,
            session,
            request=content,
            typed=typed_content,
            attachments=list(payload.attachments or []),
            questions=pre.findings.questions,
            pre=pre,
        )
    if missing and (blanks := check_skills.rule(check_keys, missing, specs)):
        # Going on without the facts the card asked for: each stays a bracket blank.
        content = _with_condition(content, blanks)
    if skipped := [f.name for f in short if f.state == "unreadable"]:
        # Going on past the card: the writer is told which file is missing, by name.
        content = _with_condition(
            content,
            f"읽지 못한 첨부({', '.join(skipped)})는 빼고 읽은 자료로 진행한다. "
            "문서에 그 파일을 읽지 못해 반영하지 않았다고 이름을 들어 밝힌다.",
        )

    protected_values: frozenset[str] = frozenset()
    if session.kind is SessionKind.chat:
        privacy_sources = _privacy_sources(content, history, workspace.blocks)
        if requested_tools:
            privacy_sources["tool_definitions"] = _tool_definition_source(requested_tools)
        # Auto scans the full envelope before any classifier, key or model call.
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
            internal_data=published is not None or _internal_data_present(
                session, workspace.blocks, payload.attachments, history
            ),
            force_strict_local=published is not None,
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
            # `actualModelChanged` compares against the requested model.
            resolved.routing = {
                **resolved.routing,
                "requestedModels": [revoked_model["id"]],
            }
        if auto_turn and auto_preflight_findings:
            # Privacy owns this turn; the classifier never sees the envelope.
            cost_routing = _cost_routing(
                mode=str(getattr(session.routing_mode, "value", session.routing_mode)),
                decision="bypassed",
                reason_code="privacy_detected",
                requested_model=requested_model,
                selected_model=resolved.models[0],
            )
            resolved.routing = {**resolved.routing, "costRouting": cost_routing}
        model = resolved.models[0]
        strict_local = resolved.strict_local
        # Privacy routing cannot add tool support.
        if requested_model.get("supportsTools") and model.get("supportsTools"):
            tools = strict_tools if strict_local else candidate_tools
        masker = governance.mask_legacy if policy.pii_masking else governance.mask
        # What egress masking takes out of the user's side of the envelope — the
        # words, earlier turns, attachments, instructions; the answer at rest masks
        # exactly these (and secrets), nothing public.
        protected_values = _protected_values(privacy_sources, legacy=policy.pii_masking)
        if resolved.findings:
            # Findings are stored masked whatever the action.
            stored_content = masker(content)[0]
            if attachment_meta:
                # Filenames and errors are user content too.
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
            # Whole tools are dropped; schema keys cannot be rewritten safely.
            tools = _drop_sensitive_tool_definitions(
                tools,
                legacy=policy.pii_masking,
            )
        tool_definitions = openai_snapshot(tools)
    elif policy.pii_masking:
        # The decision flow is chat-only; report and slides always mask.
        masker = governance.mask_legacy
        content, masked = masker(content)
        stored_content = content
        if attachment_meta:
            # A filename or extraction error is user content.
            attachment_meta = _mask_text_tree(attachment_meta, masker)
        if masked:
            await _audit_policy(user, request, "pii.masked", f"{masked}건")

    # Re-resolve skills against the final tool snapshot.
    if session.kind is SessionKind.chat:
        try:
            workspace = await assemble(
                db,
                user,
                session,
                published=published is not None,
                attachment_ids=payload.attachments,
                vision=reads_pictures(model),
                file_budget=file_budget(model),
                activated_skill_ids=active_skill_ids,
                question=_asked_about(content, history),
                available_tool_names={tool.name for tool in tools},
                how_to=how_to,
            )
        except WorkspaceContextError as exc:
            _raise_workspace_error(exc)

    trusted_context = workspace.trusted
    untrusted_context = workspace.untrusted
    #: This request's own attachments. In chat they sit beside the question
    #: (`context.build_messages`); the document writers keep them with the rest.
    turn_context: list[str] = []
    if session.kind is SessionKind.chat:
        turn_context = [b.text for b in workspace.blocks if b.source == "attachment"]
        untrusted_context = [
            b.text for b in workspace.blocks if not b.trusted and b.source != "attachment"
        ]
    if privacy_resolution and privacy_resolution.mask_outbound:
        trusted_context = _mask_list(trusted_context, legacy=policy.pii_masking)
        untrusted_context = _mask_list(untrusted_context, legacy=policy.pii_masking)
        turn_context = _mask_list(turn_context, legacy=policy.pii_masking)
    elif policy.pii_masking:
        # Always-mask covers the context blocks too.
        trusted_context = _mask_text_tree(trusted_context, governance.mask_legacy)
        untrusted_context = _mask_text_tree(untrusted_context, governance.mask_legacy)
        turn_context = _mask_text_tree(turn_context, governance.mask_legacy)
    skills_event = workspace.skills_event()
    # Template skills join the same event as hand-activated ones.
    template_blocks, skills_event = await _template_skills(
        db, design_templates.get(session.render_template_id), session.kind, skills_event
    )
    if template_blocks:
        trusted_context = list(trusted_context) + template_blocks
    if how_to:
        # A question about this app itself reads its own user guide (in process, local).
        if guide := app_help.block(content, lang="en" if _english(content) else "ko"):
            trusted_context = [*trusted_context, guide]
    context_steps = _context_steps(workspace)
    if policy.pii_masking:
        # Skill names are user-controlled, like filenames.
        if skills_event:
            skills_event = _mask_text_tree(skills_event, governance.mask_legacy)
        # Same for memory names.
        context_steps = _mask_text_tree(context_steps, governance.mask_legacy)

    # Report and slides have no privacy resolution to carry a substitution.
    document_routing = (
        _substitution_routing(revoked_model, model)
        if privacy_resolution is None and revoked_model is not None
        else None
    )
    turn_options = {
        "webSearch": payload.web_search,
        "activatedSkillIds": active_skill_ids,
    }
    # Stored for a retry to replay; a turn on the defaults stores nothing, so a document
    # turn on the model it asked for still carries no route note.
    if any(turn_options.values()):
        if privacy_resolution is not None:
            privacy_resolution.routing = {
                **privacy_resolution.routing,
                "turnOptions": turn_options,
            }
        else:
            document_routing = {**(document_routing or {}), "turnOptions": turn_options}

    strict_local = bool(privacy_resolution and privacy_resolution.strict_local)
    if session.kind in (SessionKind.report, SessionKind.slides):
        # A document turn has no chat privacy resolution; its route is settled here:
        # internal material keeps it strict-local, and an Auto session may move the
        # writing up (or down) the way a chat turn does.
        decided = await _document_route(
            db=db, user=user, session=session, policy=policy, catalogue=catalogue_models,
            model=model, blocks=workspace.blocks, attachments=payload.attachments,
            history=history, request=content,
        )
        if isinstance(decided, JSONResponse):
            return decided
        model, document_strict, note = decided
        strict_local = strict_local or document_strict
        if note:
            document_routing = {**(document_routing or {}), **note}
    # Missing verification changes the answer's qualification, not its availability.
    # Privacy decisions, missing attachments and required tool gates remain independent.
    if session.kind is not SessionKind.chat:
        # Document planners/writers do not expose a complete actual-model trace.
        trusted_context = [
            *trusted_context,
            freshness.answer_instruction({**model, "knowledgeCutoff": None}, structured=True),
        ]
    # Figures out of an attached table come from code run on the file, not from the
    # model reading the rows in its context: the first hop is made to call run_code.
    tables = (
        _table_attachments(rows)
        if session.kind is SessionKind.chat
        and forced_tool is None
        and any(tool.name == "run_code" for tool in tools)
        and calculation_policy.asks_table_analysis(content)
        else []
    )
    if tables:
        forced_tool = "run_code"
        brief = _table_analysis_instruction(tables)
        if privacy_resolution and privacy_resolution.mask_outbound:
            brief = _mask_list([brief], legacy=policy.pii_masking)[0]
        trusted_context = [*trusted_context, brief]
    calculation_required = (
        session.kind is SessionKind.chat and not tables and (
            calculation_policy.requires_calculation(content)
            or (
                not payload.attachments
                and _calculation_followup_required(history, outbound_history, session.id, content)
            )
        )
    )
    preflight_tool = None
    if calculation_required:
        preflight_tool = _calculation_preflight_tool(tools)
        trusted_context = [*trusted_context, _CALCULATION_INSTRUCTION]
    wire_history = [
        {"role": message.role.value, "content": body}
        for message, body in zip(history, outbound_history, strict=True)
    ]
    wire_history.append({"role": "user", "content": content})

    def envelope(wire: list[dict], *, economy: bool = False) -> list[dict]:
        """The request for `wire`: system turn, reference data, attachments, pictures.

        Reads `trusted_context` when called, so a block appended later (the
        conversation summary) is in the envelope built after it. `economy`: the
        tool-free shape a downward auto route is classified on and sent.
        """
        built = build_messages(
            session.kind,
            wire,
            with_tools=False if economy else bool(tools),
            web_search=False if economy else effective_web_search,
            # Whether a search tool survived, so the answer does not read as searched.
            web_search_available=any(t.name == "web_search" for t in tools),
            web_search_auto=web_search_auto,
            extra=trusted_context,
            untrusted_context=untrusted_context,
            turn_context=turn_context,
            remind_standing=session.kind is SessionKind.chat,
        )
        # After `build_messages`, never inside it — see `context.with_pictures`.
        return with_pictures(built, [picture.uri for picture in workspace.pictures])

    messages = envelope(wire_history)
    #: Whether auto routing swapped in the tool-free envelope.
    routed_down = False

    if auto_turn and not auto_preflight_findings:
        unsupported = bool(
            payload.attachments
            or forced_tool
            or active_skill_ids
            or session.agent_id
            or session.project_id
        )
        # Economy turns are tool-free; the context fit is checked on that envelope.
        economy_messages = envelope(wire_history, economy=True)
        # A quality upgrade keeps the full messages and tool definitions.
        routing_context = (
            {"messages": messages, "tools": tool_definitions}
            if session.routing_mode == RoutingMode.auto_quality
            else economy_messages
        )
        routed_model, cost_routing = await _resolve_cost_routing(
            mode=session.routing_mode,
            db=db,
            user=user,
            policy=policy,
            catalogue=catalogue_models,
            # The model privacy settled on, not the one asked for: a turn moved to a
            # strict-local twin for its internal material stays there, and no upgrade
            # can widen that boundary.
            quality_model=model,
            classifier_messages=messages,
            classifier_tool_definitions=tool_definitions,
            context_tokens=adaptive_routing.estimated_context_tokens(
                [
                    json.dumps(
                        routing_context,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                ]
            ),
            unsupported_reason=(
                "calculation_required"
                if calculation_required and session.routing_mode != RoutingMode.auto_quality
                else "unsupported_turn" if unsupported else None
            ),
        )
        resolved.models = [routed_model]
        resolved.routing = _apply_effective_model(resolved.routing, routed_model)
        resolved.routing = {**resolved.routing, "costRouting": cost_routing}
        privacy_resolution = resolved
        model = routed_model
        # Only a downward route swaps to the tool-free envelope.
        if (
            cost_routing.get("decision") == "routed"
            and session.routing_mode != RoutingMode.auto_quality
        ):
            tools = []
            tool_definitions = []
            messages = economy_messages
            routed_down = True
        strict_local = resolved.strict_local

    if not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )

    for stored in rows:
        stored.session_id = session.id
        db.add(stored)

    #: Answers the rerun replaces, carried on the new answer so nothing is lost.
    earlier_answers: list[dict[str, Any]] = []
    #: Their rows, deleted only in the transaction that stores the new answer: a
    #: rerun that dies on the way leaves the answer it was going to replace.
    superseded_ids: list[str] = []
    if retry_of is not None:
        # Reuse the question row. A failed reply goes now; a good one stays until
        # the new answer is written, then travels on it as an earlier answer.
        for row in superseded:
            if row.role is Role.assistant and row.content and row.failure is None:
                earlier_answers = [*(row.superseded or []), _earlier_answer(row)]
                superseded_ids.append(row.id)
            else:
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
            # What they typed, not the merged request.
            content=typed_content if pending else stored_content,
            attachments=attachment_meta,
            routing=privacy_resolution.routing if privacy_resolution else document_routing,
        )
    if session.kind is SessionKind.chat and not await _claim_turn(db, session):
        # Another replica holds this session's turn. Claimed in the same transaction as
        # the question below, so two replicas serialize on the row and the second sees it.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session_busy")
    db.add(user_message)
    if session.kind is SessionKind.chat and pending:
        # This turn answers (or passes over) the chat agent's gap card.
        session.pending = None
    # Request models are turn-only; lasting choices use the session settings API.
    if payload.model is None:
        # A substitute is for this turn only.
        if revoked_model is None:
            session.model = requested_model["id"]
    session.updated_at = utcnow()
    if session.kind is SessionKind.chat and payload.activated_skill_ids:
        # Picked for this turn, kept for the next ones.
        session.skill_ids = list(payload.activated_skill_ids)
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
    if pending.get("stage") == "clarify" and pending.get("runId"):
        # The card is answered (or passed over): the run that waited on it is closed.
        await _close_waiting_run(str(pending["runId"]), answers=dict(pending.get("answers") or {}),
                                 as_is=proceed_as_is, blanks=len(missing))

    # Also issues the key to an account provisioned during a proxy outage.
    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)

    is_first_turn = len(history) == 0

    # The planner is bound by the same allowlist, surface and boundary as the writer.
    # Unset, it is the default local model: an outline, a proposal and a revision plan
    # are judgements, and the model that writes the prose need not be the one making them.
    outline_model = _planner_model(
        policy.outline_model_id or settings.default_chat_model,
        user=user,
        catalogue=catalogue_models,
        kind=session.kind.value,
        writer=model,
        strict_local=strict_local,
    )
    if outline_model is not None and outline_model["id"] == model["id"]:
        outline_model = None  # the writer plans for itself; nothing to account for apart

    #: The image default draws a document's figures; none means no figure card.
    image_model = next(
        (
            m
            for m in _allowed_models(user, catalogue_models, kind="image")
            if "image" in (m.get("kinds") or [])
        ),
        None,
    )

    render_template = design_templates.get(session.render_template_id)
    # See `revising` above.
    #: 「좋아, PDF로 줘」 asks for the document as a file, not for an edit; 「견적 고치고
    #: PDF로」 revises first and hands the file over after.
    asked_files = revise.export_request(typed_content) if revising else None
    # Which pipeline the turn goes to (the `route` stage): a page, a file hand-over, a
    # revision, a report, a deck or a chat answer.
    route, route_record = await _route_turn({
        "kind": session.kind.value,
        # Document and deck templates go through the report and deck writers; only
        # other template kinds use the block writer.
        "page_template": bool(
            render_template is not None and render_template.kind not in ("document", "deck")),
        "revising": revising,
        "files_only": asked_files is not None and not asked_files[1],
    })
    _ROUTE.set(route_record)
    if route == "page":
        return StreamingResponse(
            _survive_disconnect(
                _run_page(
                    may_ask=not proceed_as_is and not _brings_material(content),
                    user_id=user.id,
                    api_key=api_key,
                    session_id=session.id,
                    # `None` plans and offers again.
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
                    # No network on a strict-local route.
                    web_search=effective_web_search and not strict_local,
                )
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if route == "handover":
        return StreamingResponse(
            _survive_disconnect(
                _hand_over_document(
                    user_id=user.id,
                    session_id=session.id,
                    formats=asked_files[0],
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
    if asked_files is not None:
        content = revise.without_export(content)
    if route == "revise":
        # Reading the instruction is a judge's job: the local planner does it, so an
        # external writer's reasoning budget or output habits never decide the route.
        planner = outline_model or _local_planner(
            user=user, catalogue=catalogue_models, kind=session.kind.value, writer=model,
            strict_local=strict_local,
        )
        revision, skeleton = await _revision_plan(
            db, session, instruction=content, model=planner or model, api_key=api_key
        )
        if revision is not None and revision.restructures:
            # A skeleton change is planned again through the outline-and-confirm pass, not
            # patched part by part. The judge's note states the target shape in absolute
            # terms (「9장」 rather than 「3장 더」).
            content = grounding.merge_answers(
                revise.without_counts(await _original_request(db, session) or content),
                {"_note": revision.note},
            )
            # The planner reads the skeleton; the writer reads the parts' own words as
            # material, where a merged part's table and a renamed part's dates count
            # as facts of this document rather than numbers the request never gave.
            current = await db.get(Artifact, session.artifact_id) if session.artifact_id else None
            trusted_context = [*trusted_context, revise.outline_block(skeleton)]
            if current is not None and any(texts := _part_texts(current)):
                untrusted_context = [*untrusted_context, revise.document_block(skeleton, texts)]
            carry_from = session.artifact_id
            carry_note = f"{typed_content} {revision.note}"
            route_record.summary = "구조 변경 → 다시 계획"
        else:
            return StreamingResponse(
                _survive_disconnect(
                    _revise_document(
                        user_id=user.id,
                        api_key=api_key,
                        session_id=session.id,
                        model=model,
                        instruction=content,
                        routing=document_routing,
                        plan=revision,
                        planner=planner,
                        export_after=asked_files[0] if asked_files else None,
                    )
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

    if user_pipe is not None and route in ("report", "chat"):
        # The person's own pipeline runs the turn (`user_pipelines`), or resumes it.
        outbound = content
        if privacy_resolution is not None and privacy_resolution.mask_outbound:
            outbound = _mask_list([content], legacy=policy.pii_masking)[0]
        elif policy.pii_masking:
            outbound = _mask_text_tree([content], governance.mask_legacy)[0]
        return await user_pipelines.turn_response(
            db, user, session, user_pipe, pending=pending, approved_plan=approved_plan,
            proceed_as_is=proceed_as_is, request=outbound, model=model,
            outline_model=outline_model, api_key=api_key,
            routing=(privacy_resolution.routing if privacy_resolution else document_routing),
            trusted=list(trusted_context), untrusted=[*turn_context, *untrusted_context],
            design_tokens=workspace.design_tokens, skills_event=skills_event,
            context_steps=context_steps, web_search=effective_web_search and not strict_local,
            attachments=list(payload.attachments or []), catalogue=catalogue_models,
            agent=agent_row,
        )

    if session.kind is SessionKind.report:
        return StreamingResponse(
            _survive_disconnect(
                _run_report(
                    # The run the card waited on, when this pass follows it.
                    parent_run_id=str(pending.get("runId") or "") or None,
                    may_ask=not proceed_as_is and not _brings_material(content),
                    figures_plan=approved_figures,
                    image_model=image_model,
                    template=render_template,
                    format_id=workspace.doc_format,
                    check_keys=check_keys,
                    check_options=check_options,
                    skill_specs=specs,
                    process=agent_row.process_settings if agent_row is not None else None,
                    user_id=user.id,
                    api_key=api_key,
                    session_id=session.id,
                    # `None` plans and offers again.
                    approved_plan=approved_plan,
                    attachments=list(payload.attachments or []),
                    answers=dict(pending.get("answers") or {}),
                    model=model,
                    request=content,
                    project_id=session.project_id,
                    routing=document_routing,
                    trusted_context=trusted_context,
                    carry_from=carry_from,
                    carry_note=carry_note,
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
                    # No network on a strict-local route.
                    web_search=effective_web_search and not strict_local,
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
                    template=render_template,
                    may_ask=not proceed_as_is and not _brings_material(content),
                    figures_plan=approved_figures,
                    image_model=image_model,
                    user_id=user.id,
                    api_key=api_key,
                    session_id=session.id,
                    # `None` plans and offers again.
                    approved_plan=approved_plan,
                    attachments=list(payload.attachments or []),
                    answers=dict(pending.get("answers") or {}),
                    model=model,
                    request=content,
                    project_id=session.project_id,
                    routing=document_routing,
                    trusted_context=trusted_context,
                    carry_from=carry_from,
                    carry_note=carry_note,
                    untrusted_context=untrusted_context,
                    design_tokens=workspace.design_tokens,
                    skills_event=skills_event,
                    context_steps=context_steps,
                    outline_model=outline_model,
                    # No network on a strict-local route.
                    web_search=effective_web_search and not strict_local,
                )
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # The conversation is fitted to the model's window here, once the model is
    # final: earlier turns that no longer fit are replaced by a running summary
    # (`services.conversation_summary`) rather than sent whole and refused.
    summary_credits = 0
    window = int(model.get("contextWindow") or 0)
    if window > 0 and len(wire_history) > 1:
        question = wire_history[-1]
        fixed = envelope_tokens(
            envelope([question], economy=routed_down),
            None if routed_down else tool_definitions,
        )
        kept, dropped = fit_history(wire_history[:-1], history_budget(window, fixed))
        if dropped:
            routed = bool(cost_route and cost_route.get("decision") == "routed")
            summary_text, summary_credits = await _conversation_summary(
                db,
                session,
                rows=history[: len(dropped)],
                turns=dropped,
                writer=model,
                api_key=api_key,
                strict_local=strict_local,
                disable_fallbacks=routed,
                masker=(
                    (governance.mask_legacy if policy.pii_masking else governance.mask)
                    if policy.pii_masking or policy.external_data_guard
                    else None
                ),
                mask_at_rest=bool(
                    policy.pii_masking or (privacy_resolution and privacy_resolution.findings)
                ),
                redact_logging=policy.pii_masking or policy.external_data_guard,
            )
            trusted_context = [*trusted_context, summary_block(summary_text, len(dropped))]
            messages = envelope([*kept, question], economy=routed_down)
            context_steps = [
                *context_steps,
                _summary_context_step(len(dropped), summarised=summary_text is not None),
            ]

    tool_names = {t.name for t in tools}
    preset_call: tuple[str, dict[str, Any]] | None = None
    if forced_tool == "web_search" and "web_search" in tool_names:
        # Reuse only the history already processed by this turn's privacy decision.
        # Never persist or append the earlier raw question as new prompt content.
        lookup_content = (
            outbound_history[fresh_followup_index]
            if fresh_followup_index is not None else content
        )
        planned = (
            await research_service.literature_queries(lookup_content, model["id"], api_key)
            if literature_request(lookup_content)
            else []
        )
        if planned:
            # A literature question is looked up across its axes at once — attacks,
            # each defence family, benchmarks — not through one phrasing of it.
            preset_call = (
                "web_search", {
                    "query": planned[0],
                    "queries": planned,
                    "kind": "papers",
                    "scholarly": True,
                    **search_hints(lookup_content),
                },
            )
        elif not search_needs_planning(lookup_content):
            preset_call = (
                "web_search", {
                    "query": search_query(lookup_content, prefer_primary=fresh_fact),
                    **search_hints(lookup_content),
                },
            )
    elif forced_tool == "weather" and "weather" in tool_names:
        place = weather_location(content)
        if place:
            preset_call = ("weather", {"location": place})
    # A substantial chat request gets a short plan first: what an expert's answer would
    # hold, placed beside the question. Not on a turn routed down to an economy model.
    # A turn whose numbers must go through the calculator first is left to that
    # procedure: a plan beside the question pulls the model straight into prose.
    if (
        session.kind is SessionKind.chat
        and not routed_down
        and not calculation_required
        and not tables
        and answer_plan.wanted(content)
    ):
        plan_model, plan_key = model["id"], api_key

        async def plan_messages(current: list[dict]) -> list[dict]:
            outline = await answer_plan.plan(
                current, complete=report_service._complete, model=plan_model, api_key=plan_key
            )
            return answer_plan.attach(current, outline)
    else:
        plan_messages = None
    return StreamingResponse(
        _survive_disconnect(
            _run_turn(
                user_id=user.id,
                api_key=api_key,
                # A visitor's words are never remembered on the owner's account.
                auto_memory=Preferences.of(user).auto_memory and published is None,
                published=published,
                session_id=session.id,
                # How far a shared note reaches, and whose byline it carries.
                project_id=session.project_id or "",
                agent_name=(agent_row.name if agent_row else ""),
                model=model,
                messages=messages,
                tools=tools,
                tool_definitions=tool_definitions,
                # None leaves the upstream default.
                temperature=agent_temperature,
                plan_messages=plan_messages,
                first_user_message=stored_content,
                # A turn with no answer records the failure on this row.
                user_message_id=user_message.id,
                is_first_turn=is_first_turn,
                skills_event=skills_event,
                context_steps=context_steps,
                routing=privacy_resolution.routing if privacy_resolution else document_routing,
                quality_model=requested_model if cost_route else None,
                judge_model=(outline_model or model)["id"],
                disable_fallbacks=bool(cost_route and cost_route.get("decision") == "routed"),
                # Model output can introduce new findings, so it is masked at rest too.
                mask_at_rest=policy.pii_masking or policy.external_data_guard,
                protected_values=protected_values,
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
                # Short lookups are server presets. Long requests and weather
                # without a named place use the allowed tool's forced planning hop.
                preset_call=preset_call,
                freshness_request=content if fresh_fact else None,
                caveat_request=(
                    content
                    if not fresh_fact
                    and freshness.ballpark_current_value(content)
                    and not _grounded_in_files(workspace)
                    and not _reads_pasted_material(content)
                    else None
                ),
                force_tool=(
                    forced_tool
                    if forced_tool and preset_call is None and forced_tool in tool_names
                    else None
                ),
                preflight_tool=preflight_tool,
                check_keys=tuple(check_skills.of_stage(check_keys, "post", specs)),
                skill_keys=_skill_keys(workspace.applied_skills, specs),
                skill_specs=specs,
                own_scripts=own_scripts,
                check_options=check_options,
                # The agent's 담당 부서·연락처 is a value the answer may repeat as written.
                check_material="\n".join([*workspace.untrusted, *(
                    [agent_row.contact] if agent_row is not None and agent_row.contact.strip()
                    else [])]),
                agent_key=agent_key or "",
                calculation_required=calculation_required,
                calculation_expression=(
                    calculation_policy.direct_calculation_expression(content)
                    if calculation_required and preflight_tool == "calculate" else None
                ),
                side_credits=summary_credits,
                earlier_answers=earlier_answers or None,
                superseded_ids=superseded_ids or None,
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


async def _route_turn(facts: dict[str, Any]) -> tuple[str, StepRecord]:
    """The `route` stage over what the router settled: the target and its record."""
    from app.pipeline.route_stages import TARGETS

    work = PipelineBundle(request=PipelineRequest(text="", model="", api_key="",
                                                  turn={"route": facts}))
    async for _event in PipelineRunner([pipeline_registry.stage("route")], name="route",
                                       emit_records=False).run(work):
        pass
    record = work.steps[-1]
    record.summary = TARGETS.get(work.plan.route, work.plan.route)
    return work.plan.route, record


#: The record of the `route` stage of the turn being answered: `_survive_disconnect` shows
#: it at the head of the stream it wraps (set by `send_message` before it dispatches).
_ROUTE: ContextVar[StepRecord | None] = ContextVar("turn_route", default=None)


async def _routed(record: StepRecord, events: AsyncIterator[str]) -> AsyncIterator[str]:
    if not pipeline_records_on():
        async for chunk in events:
            yield chunk
        return
    shown = False
    async for chunk in events:
        if not shown and '"privacy_route"' not in chunk[:40]:
            shown = True
            yield chat_service.sse(record.wire())
        yield chunk
    if not shown:
        yield chat_service.sse(record.wire())


#: Active cancellation signals grouped by session, local to this process.
#: `stop_turn` also broadcasts over Postgres (`services.stop_signal`) so a
#: request landing on a different replica still reaches whichever process is
#: actually streaming the turn.
_STOPPING: dict[str, set[asyncio.Event]] = {}


def fire_stop_locally(session_id: str) -> None:
    """Sets every stop signal this process holds for `session_id` — called
    directly by `stop_turn` and by the cross-process broadcast listener."""
    for signal in _STOPPING.get(session_id, set()):
        signal.set()

#: A key echoed in an upstream error body; the reason is shown on screen.
_SECRET_IN_REASON = re.compile(r"sk-[A-Za-z0-9_\-]+")


#: How providers word a prompt that does not fit: vLLM, OpenAI, Anthropic, LiteLLM.
_CONTEXT_OVERFLOW = re.compile(
    r"context[ _]length|maximum context|max(?:imum)? (?:input|prompt)? ?tokens?|"
    r"too many tokens|prompt is too long|input is too long|exceeds? the (?:model|context)|"
    r"token limit|context window|input length|ContextWindowExceeded",
    re.I,
)


def _context_overflow(failure: str) -> bool:
    """Whether an upstream 4xx is the prompt not fitting the model's window."""
    head, _, tail = failure.partition(": ")
    return head.strip() in ("upstream_400", "upstream_413", "upstream_422") and bool(
        _CONTEXT_OVERFLOW.search(tail)
    )


def _error_event(
    message: str, exc: BaseException | None = None, *, code: str | None = None
) -> dict[str, Any]:
    """SSE error with a machine code and bounded, redacted upstream reason.

    `code` names the failure more precisely than the upstream status when known.
    """
    reason = ""
    if isinstance(exc, chat_service.ChatStreamError):
        head, _, tail = str(exc).partition(": ")
        code = code or head.strip() or "upstream_failed"
        reason = _SECRET_IN_REASON.sub("sk-…", tail.strip())[:200]
    code = code or "internal_error"
    event: dict[str, Any] = {"type": "error", "code": code, "message": message}
    if reason:
        event["reason"] = reason
    return event


async def _until_stopped(
    events: AsyncIterator[dict[str, Any]], stopping: asyncio.Event
) -> AsyncIterator[dict[str, Any]]:
    """Race stream delivery against cancellation and close the upstream generator."""
    iterator = events.__aiter__()
    waiting = asyncio.ensure_future(stopping.wait())
    try:
        while not stopping.is_set():
            nxt = asyncio.ensure_future(anext(iterator))
            done, _ = await asyncio.wait({nxt, waiting}, return_when=asyncio.FIRST_COMPLETED)
            if nxt not in done:
                # Cancel and await the pending read before `aclose()`, or it
                # raises on a running generator.
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


#: Strong references; a task with no reader is otherwise collectible mid-turn.
_DETACHED: set[asyncio.Task] = set()

#: Idle gap before an SSE comment is sent. Under the 60s proxy idle timeout.
HEARTBEAT_SEC = 15.0


async def _heartbeat(events: AsyncIterator[str]) -> AsyncIterator[str]:
    """Relays a stream, sending an SSE comment after `HEARTBEAT_SEC` of silence.

    The pending read is kept across heartbeats; `wait_for` could drop an event
    on a coincident timeout.
    """
    iterator = events.__aiter__()
    nxt = asyncio.ensure_future(anext(iterator))
    try:
        while True:
            done, _ = await asyncio.wait({nxt}, timeout=HEARTBEAT_SEC)
            if not done:
                yield ": keep-alive\n\n"
                continue
            try:
                event = nxt.result()
            except StopAsyncIteration:
                return
            yield event
            nxt = asyncio.ensure_future(anext(iterator))
    finally:
        if not nxt.done():
            nxt.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await nxt
        await iterator.aclose()


def _survive_disconnect(events: AsyncIterator[str]) -> AsyncIterator[str]:
    """A turn's response: finishes behind a departed reader and stays alive while silent.
    A turn `send_message` routed shows its route record first (`_ROUTE`)."""
    if (record := _ROUTE.get()) is not None:
        _ROUTE.set(None)
        events = _routed(record, events)
    return _heartbeat(_detached(events))


async def _detached(events: AsyncIterator[str]) -> AsyncIterator[str]:
    """Lets a turn finish when nobody is reading.

    The work runs in its own task; this relays its queue.
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
    """Writes a turn's handoff notes as memories, scoped to the project or else this session.

    `key` dedupes within the scope.
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
        # Byline on the description, not the body.
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
                # A finding, not something the person said about themselves.
                type=MemoryType.reference,
                body=note["body"],
                scope=scope,
            )
        )


_CALCULATION_INSTRUCTION = (
    "이 요청은 수치 계산이 필요합니다. 답변 전에 지정된 계산 도구를 호출하세요. "
    "계산이 아닌 질문(절차·조언·설명)이 함께 있어도 첫 응답은 계산 도구 호출이어야 하며, "
    "나머지 질문에는 계산 결과를 받은 뒤 함께 답하세요. "
    "문제에 주어진 값·분모·가중치·단위를 보존하여 식을 작성하고, 없는 조건을 만들지 마세요. "
    "계산 결과를 설명할 때 도구의 값과 대조하고, 검산하지 않은 다른 수치를 "
    "해설에 보태지 마세요. 도구는 입력한 식의 산술만 검증하며 문제 해석까지 보증하지 않습니다."
)


def _calculation_followup_required(
    history: list[Message], outbound_bodies: list[str], session_id: str, content: str,
) -> bool:
    """Bind up to eight completed arithmetic pairs without certifying the answer."""
    if (
        not calculation_policy.is_calculation_followup(content)
        or len(history) != len(outbound_bodies)
    ):
        return False
    for index in range(len(history) - 2, max(-1, len(history) - 18), -2):
        question, answer = history[index:index + 2]
        if (
            question.session_id != session_id or answer.session_id != session_id
            or question.role is not Role.user or answer.role is not Role.assistant
            or not answer.model
            or (answer.routing or {}).get("answerOrigin") not in {None, "model"}
            or any(
                row.attachments or row.variants or row.artifact_ids or row.failure
                for row in (question, answer)
            )
            or not calculation_policy.is_plain_numeric_answer(outbound_bodies[index + 1])
        ):
            return False
        # Only the privacy-processed bodies establish a seed or a continuation.
        if calculation_policy.requires_calculation(outbound_bodies[index]):
            return True
        if not calculation_policy.is_calculation_followup(outbound_bodies[index]):
            return False
    return False


def _material_text(messages: list[dict]) -> str:
    """What the person gave this conversation (their turns and attached text), for grounding."""
    parts = []
    for one in messages:
        if one.get("role") in ("assistant", "system"):
            continue
        content = one.get("content")
        if isinstance(content, list):
            content = " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
        parts.append(str(content or ""))
    return "\n".join(parts)


def _calculation_preflight_tool(tools: list[Tool]) -> str:
    available = {tool.name for tool in tools if tool.source == "builtin" and tool.read_only}
    if "calculate" in available:
        return "calculate"
    raise HTTPException(status_code=409, detail="calculation_tool_unavailable")


_TABLE_SUFFIXES = (".csv", ".tsv", ".xlsx", ".xlsm", ".xls")
_TABLE_MIMES = frozenset({
    "text/csv",
    "text/tab-separated-values",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel.sheet.macroenabled.12",
})


def _table_attachments(rows: list[StoredFile]) -> list[StoredFile]:
    """This turn's attachments that are data tables, by name or by detected type."""
    return [
        row for row in rows
        if row.storage_key
        and (row.name.lower().endswith(_TABLE_SUFFIXES) or (row.mime or "").lower() in _TABLE_MIMES)
    ]


def _table_analysis_instruction(files: list[StoredFile]) -> str:
    """The forced run_code hop's brief: which files to read, under which columns."""
    lines = []
    for row in files[:6]:
        try:
            schema = file_schema.describe(row.name, file_service.read_blob(row.storage_key))
        except OSError:
            schema = ""
        # Names and headers only: the preview rows are what the model must not copy.
        heads = [
            line.strip() for line in schema.splitlines()[1:]
            if line.lstrip().startswith(("열(", "- 시트", "시트 "))
        ][:6]
        lines.append(f"- {row.name}" + (": " + " / ".join(heads) if heads else ""))
    return (
        "이 요청은 첨부한 표의 수치를 다룹니다. 첫 응답은 run_code 도구 호출이어야 합니다. 표의 "
        "값을 답에 옮겨 적어 손으로 계산하지 말고, task 에 어느 파일의 어느 열로 무엇을 계산해 "
        "print 로 보여 줄지 적으세요(files 에 파일 이름). 결과를 받은 뒤 답의 수치는 실행 결과에 "
        "나온 값만 쓰고, 결과에 없는 수치가 필요하면 「[수치]」처럼 대괄호로 비워 두세요.\n"
        "샌드박스 현재 디렉터리에 있는 파일:\n" + "\n".join(lines)
    )


async def _run_turn(*, published: publishing.PublishedTurn | None = None,
                    **kwargs: Any) -> AsyncIterator[str]:
    """Drives one assistant turn (`_answer_turn`); a published bot's turn gives its
    running-turn slot back when it ends, however it ends."""
    events = _answer_turn(published=published, **kwargs)
    if published is not None:
        events = publishing.holding(events, published)
    async for event in events:
        yield event


async def _answer_turn(
    *,
    user_id: str,
    api_key: str,
    auto_memory: bool,
    #: A visitor's turn on a published bot: nothing is stored in the owner's account beyond
    #: the conversation itself, and the spend is marked as the bot's.
    published: publishing.PublishedTurn | None = None,
    session_id: str,
    project_id: str = "",
    agent_name: str = "",
    model: dict,
    messages: list[dict],
    tools: list[Tool],
    tool_definitions: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
    #: Planning to do inside the stream, so the response opens at once (see answer_plan).
    plan_messages: Callable[[list[dict]], Awaitable[list[dict]]] | None = None,
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
    #: Model that judges the finished answer (the planner when one is set).
    judge_model: str = "",
    #: A tool the first hop must call. See `agent.run_turn`.
    force_tool: str | None = None,
    preflight_tool: str | None = None,
    calculation_required: bool = False,
    calculation_expression: str | None = None,
    #: The server's own first call. See `agent.run_turn`.
    preset_call: tuple[str, dict[str, Any]] | None = None,
    freshness_request: str | None = None,
    caveat_request: str | None = None,
    #: Values masked out of the user's own words this turn; the answer at rest
    #: masks these and secrets, and leaves public contact details readable.
    protected_values: frozenset[str] = frozenset(),
    #: Credits spent before the turn on its behalf (the conversation summary),
    #: settled with the answer so one row carries the turn's whole cost.
    side_credits: int = 0,
    #: Answers this rerun replaces, oldest first, kept on the new answer.
    earlier_answers: list[dict[str, Any]] | None = None,
    #: Rows of the answers being replaced; deleted with the new answer's insert,
    #: and left alone when the rerun produced nothing.
    superseded_ids: list[str] | None = None,
    #: The agent's `post` check skills, run over the finished answer (`check_skills`).
    check_keys: tuple[str, ...] = (),
    #: Catalogue keys of the skills on the turn; a copy skill brings the copy check.
    skill_keys: tuple[str, ...] = (),
    #: What the server runs for those keys (`_skill_specs`).
    skill_specs: dict | None = None,
    #: The person's own scripts by skill key, for `run_skill_script` in the sandbox.
    own_scripts: dict | None = None,
    #: The agent's fact set, which sets how strict the trace is.
    #: The agent's `check_options`: how strict the trace is.
    check_options: dict | None = None,
    #: Attached and carried files' text: what the answer's values may come from too.
    check_material: str = "",
    #: The catalogue agent's key: which pipeline finishes the answer.
    agent_key: str = "",
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
    server_abstention = False
    tool_result_answer = False
    length_check: dict | None = None

    # Set by the stop button, not by a closed socket.
    stopping = asyncio.Event()
    # The pre-flight 409 and this registration are far apart; two sends that both passed
    # the check meet here. A fresh send never supersedes a live turn — only a regenerate
    # (`superseded_ids`, `earlier_answers`) or a turn after 중단 may. The late one bows out
    # and takes its own question row with it, so the transcript stays question, answer.
    live = any(not event.is_set() for event in _STOPPING.get(session_id, set()))
    if live and not superseded_ids and not earlier_answers:
        async with SessionLocal() as db:
            if user_message_id and (row := await db.get(Message, user_message_id)):
                await db.delete(row)
                await db.commit()
        yield chat_service.sse(
            {
                "type": "error",
                "code": "session_busy",
                "message": "앞선 답변이 아직 쓰이고 있습니다.",
            }
        )
        yield chat_service.sse({"type": "done"})
        await _release_turn(session_id)
        return
    # An earlier turn on this session is superseded, wherever it is running.
    fire_stop_locally(session_id)
    await stop_signal.broadcast(session_id)
    _STOPPING.setdefault(session_id, set()).add(stopping)

    ctx = ToolContext(
        user_id=user_id,
        session_id=session_id,
        api_key=api_key,
        project_id=project_id,
        agent_name=agent_name,
        own_scripts=dict(own_scripts or {}),
        # The latest user message, not the conversation's opening line.
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
    ctx.strict_local = strict_local
    cost_routing = dict((routing or {}).get("costRouting") or {}) or None

    def classify_tool_output(value: str) -> list[dict[str, Any]]:
        return [
            row.wire()
            for row in governance.findings(
                {"tool_output": value},
                legacy=legacy_masking,
                scope="tool",
            )
        ]

    def mask_tool_output(value: str) -> tuple[str, int]:
        # A page from the public web: a switchboard or a press mailbox is the
        # answer, not a leak. Only secrets come out.
        return masker(value, scope="tool")

    if routing:
        # First event, so the model badge updates before any token.
        yield chat_service.sse({"type": "privacy_route", **routing})
    if cost_routing:
        yield chat_service.sse({"type": "model_route", **cost_routing})
    if skills_event:
        yield chat_service.sse(skills_event)
    for step in context_steps or ():
        yield chat_service.sse(_step_event(step))
    if plan_messages is not None:
        # The stream is open and says what it is doing; a slow model server shows as a
        # running step, never as a request that has not started.
        yield chat_service.sse(
            {"type": "step", "category": "thinking", "id": "answer_plan", "label": "답 구성 중",
             "status": "running"}
        )
        messages = await plan_messages(messages)
        yield chat_service.sse(
            {"type": "step", "category": "thinking", "id": "answer_plan", "label": "답 구성",
             "status": "done"}
        )
    try:
        async for event in _until_stopped(
            agent_service.run_turn(
                model["id"],
                freshness.with_answer_policy(
                    messages, model, current_fact=bool(freshness_request),
                ),
                tools,
                ctx,
                tool_definitions=tool_definitions,
                temperature=temperature,
                sanitize_tool_output=(mask_tool_output if sanitize_tool_output else None),
                sanitize_step_detail=mask_tool_output if protect_enrichment else None,
                classify_tool_output=classify_tool_output if protect_enrichment else None,
                strict_local=strict_local,
                disable_fallbacks=disable_fallbacks,
                redact_logging=mask_at_rest,
                force_tool=force_tool,
                **({"preflight_tool": preflight_tool} if preflight_tool else {}),
                **({"calculation_required": True} if calculation_required else {}),
                **(
                    {"calculation_expression": calculation_expression}
                    if calculation_expression is not None else {}
                ),
                preset_call=preset_call,
                **({"freshness_request": freshness_request} if freshness_request else {}),
                **({"caveat_request": caveat_request} if caveat_request else {}),
            ),
            stopping,
        ):
            if event["type"] == "freshness_abstention":
                server_abstention = True
                actual_model = None
                cost_routing = None
                routing = _freshness_routing(str(event["reason"]))
                yield chat_service.sse({"type": "freshness_abstention", **routing})
                continue
            if event == agent_service.TOOL_RESULT_ANSWER_EVENT:
                tool_result_answer = True
                actual_model = None
                cost_routing = None
                routing = {**(routing or {}), **{k: v for k, v in event.items() if k != "type"}}
                routing.pop("costRouting", None)
                yield chat_service.sse(event)
                continue
            if event["type"] == "delta":
                text_parts.append(event["text"])
            elif event["type"] == "retract":
                # Narration the agent took back; see `agent.run_turn`.
                joined = "".join(text_parts).replace(event["text"], "", 1)
                text_parts[:] = [joined]
            elif event["type"] == "step":
                # Stored without `type`; one row per step id, the done event
                # replacing the running one.
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
        # A stopped turn still settles; only the label differs.
        if stopping.is_set():
            failed = "stopped"
    except chat_service.ChatStreamError as exc:
        log.warning("chat stream failed for session %s: %s", session_id, exc)
        failed = str(exc)
        if failed.startswith("upstream_429"):
            yield chat_service.sse(
                _error_event("모델 사용량 한도에 닿았습니다. 잠시 후 다시 시도해 주세요.", exc)
            )
        elif _context_overflow(failed):
            yield chat_service.sse(
                _error_event(
                    "대화가 모델의 문맥 길이를 넘었습니다. 첨부를 줄이거나 문맥이 더 큰 "
                    "모델을 고르거나 새 대화에서 이어 가세요.",
                    exc,
                    code="context_length_exceeded",
                )
            )
        else:
            yield chat_service.sse(_error_event("모델 응답을 받지 못했습니다.", exc))
    except Exception as exc:  # noqa: BLE001 — turn still has to settle and close
        log.exception("chat stream crashed for session %s", session_id)
        failed = "internal_error"
        yield chat_service.sse(_error_event("요청 처리 중 오류가 발생했습니다.", exc))

    # This is answer-generation scope; earlier classifier/search work keeps its audit.
    skip_completion_work = server_abstention or tool_result_answer
    content = "".join(text_parts)
    if content.strip() and not failed and not skip_completion_work:
        # The agent's finish pipeline (`app/pipelines/*.toml` `finish`): normalized, traced
        # by its check skills, a cover letter measured, the streamed text replaced if any
        # of it changed.
        finish = chat_stages.bundle_for(
            answer=content, request=ctx.request, model=model, actual_model=actual_model,
            api_key=api_key, session_id=session_id, today=_today(),
            first_message=first_user_message, messages=messages,
            material=_material_text(messages), check_keys=check_keys,
            check_material=check_material, tool_outputs=list(ctx.outputs), judge=judge_model,
            kept_steps=steps, usage=usage, skill_keys=skill_keys, check_options=check_options,
            skill_specs=skill_specs,
        )
        pipe = pipeline_registry.for_chat(agent_key)
        runner = PipelineRunner(pipe.stages("finish"), name=f"{pipe.name}.finish")
        async for event in runner.run(finish, PipelineContext(options=dict(pipe.options))):
            yield chat_service.sse(event)
        steps, content = finish.document.kept_steps, finish.document.answer
        length_check = finish.document.length_check
        routing = {
            **(routing or {}),
            "accuracy": freshness.accuracy_metadata(model, actual_model),
            **({"lengthCheck": length_check} if length_check else {}),
        }
        yield chat_service.sse({"type": "privacy_route", **routing})
        # The answer keeps its finish records (`routing.run`), so a reloaded transcript lists
        # them beside the tool steps as the stream did.
        if summary := _run_summary(finish, pipe.name, "finish"):
            routing = {**routing, "run": summary}
    if failed == "stopped" and not any(usage.values()) and not skip_completion_work:
        # A stopped stream never reaches the usage chunk; estimate, marked as one.
        usage = {
            "inputTokens": file_service.estimate_tokens(
                "".join(str(m.get("content") or "") for m in messages)
            ),
            "outputTokens": file_service.estimate_tokens(content),
            "estimated": True,
        }
    stored_content = (
        masker(content, scope="answer", protected=set(protected_values))[0]
        if mask_at_rest or tool_output_findings
        else content
    )
    protect_persistence = mask_at_rest or bool(tool_output_findings)
    # Tell the browser what was masked; it holds the streamed original.
    answer_findings = (
        governance.findings(
            {"assistant_output": content},
            legacy=legacy_masking,
            scope="answer",
            protected=set(protected_values),
        )
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

    # Everything persisted from this turn is masked the way the answer is: the
    # user's own details and secrets out, public contact details readable.
    def at_rest(value: str) -> tuple[str, int]:
        return masker(value, scope="answer", protected=set(protected_values))

    stored_steps = _mask_text_tree(steps, at_rest) if protect_persistence else steps
    stored_actual_model = (
        at_rest(actual_model)[0] if protect_persistence and actual_model else actual_model
    )
    credits = (
        0 if not content or skip_completion_work
        else charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
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
    stored_routing = _mask_text_tree(routing, at_rest) if protect_persistence else routing

    # Stored and announced before the title call and the message transaction, so the
    # panel catches up with the closing text.
    new_artifact: str | None = None
    if stored_content and not failed and not skip_completion_work and published is None:
        new_artifact = await _store_artifacts(
            user_id=user_id,
            session_id=session_id,
            project_id=project_id,
            content=stored_content,
            requested_artifacts=ctx.pending_artifacts,
            protect_privacy=protect_enrichment,
            legacy_masking=legacy_masking,
            protected_values=protected_values,
        )
        if new_artifact:
            yield chat_service.sse(
                {
                    "type": "artifact",
                    "artifactId": new_artifact,
                    # Whether the panel should open: only for an artifact that was asked for.
                    "deliberate": bool(ctx.pending_artifacts),
                }
            )

    title: str | None = None
    title_credits = 0
    title_model: str | None = None
    if is_first_turn and stored_content and not failed and not skip_completion_work:
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
                    artifact_ids=[new_artifact] if new_artifact else None,
                    superseded=earlier_answers or None,
                    # A partial answer is kept and labelled by who ended it.
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
                # The replaced answers leave in the same transaction that writes their successor.
                for old_id in superseded_ids or []:
                    old_row = await db.get(Message, old_id)
                    if old_row is not None:
                        await db.delete(old_row)
                if not skip_completion_work:
                    settle(
                        db,
                        user,
                        credits,
                        reason="chat.completion",
                        session_id=session_id,
                        model=stored_actual_model,
                        publication_id=published.publication_id if published else None,
                    )
            elif superseded_ids:
                # A rerun that produced nothing: the answer it was to replace is still
                # there, untouched, and the question is not marked unanswered.
                answer_id = superseded_ids[-1]
            else:
                # No answer: the question row carries the outcome and the retry.
                question = await db.get(Message, user_message_id) if user_message_id else None
                if question is not None:
                    # Stopped before the first token is still stopped, not unanswered.
                    question.failure = (
                        TurnFailure.stopped if failed == "stopped" else TurnFailure.no_answer
                    )
                    db.add(question)
            # Searches ran whether or not an answer came back; the engines' quota did.
            record_searches(
                db,
                user,
                ctx.tool_calls.get("web_search", 0),
                session_id=session_id,
                surface=session.kind.value,
                publication_id=published.publication_id if published else None,
            )
            # Notes are kept from an empty completion but not from a failed turn.
            if ctx.pending_notes and not failed and not skip_completion_work \
                    and published is None:
                await _store_notes(db, user_id, session_id, project_id, ctx.pending_notes)
            if title and not session.title_set_by_user:
                session.title = title
            # Own ledger line: a different model may have run it.
            if not skip_completion_work:
                settle(
                    db,
                    user,
                    title_credits,
                    reason="chat.title",
                    session_id=session_id,
                    model=title_model,
                    publication_id=published.publication_id if published else None,
                )
            if side_credits:
                # Spent before the model call, whatever became of the turn.
                settle(db, user, side_credits, reason="chat.summary", session_id=session_id,
                       publication_id=published.publication_id if published else None)
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
                    audit_metadata = dict(cost_routing or {})
                    if skip_completion_work:
                        # Keep the selection decision without claiming that its model ran.
                        audit_metadata = {
                            **(routing_audit.event_metadata or {}),
                            **(stored_routing or {}),
                            "executedModel": None,
                        }
                        if protect_persistence:
                            audit_metadata = _mask_text_tree(audit_metadata, at_rest)
                    routing_audit.event_metadata = audit_metadata
                    db.add(routing_audit)
            session.updated_at = utcnow()
            if new_artifact:
                session.artifact_id = new_artifact
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

    if published is not None:
        # The owner's statistics — counts, topics, what it could not answer — in their own
        # transaction, after the answer is durable.
        try:
            async with SessionLocal() as db:
                await publishing.after_turn(
                    db, published, question=published.question or first_user_message,
                    answer=content, usage=usage, failed=bool(failed))
                await db.commit()
        except Exception as exc:  # noqa: BLE001 — the answer is kept without its count
            log.warning("bot statistics for %s not counted: %s",
                        logs.safe(session_id), logs.safe(exc))

    # Own transaction, after the answer is durable: auto-memory needs `answer_id`.
    memory_step: dict | None = None
    if stored_content and not failed and not skip_completion_work and published is None:
        memory_step = await _enrich_memory(
            user_id=user_id,
            session_id=session_id,
            content=stored_content,
            first_user_message=first_user_message,
            api_key=api_key,
            model=model,
            auto_memory=auto_memory,
            protect_privacy=protect_enrichment,
            strict_local=strict_local,
            disable_fallbacks=disable_fallbacks,
            redact_logging=mask_at_rest or bool(tool_output_findings),
            legacy_masking=legacy_masking,
            message_id=answer_id,
            protected_values=protected_values,
        )

    if memory_step:
        yield chat_service.sse(_step_event(memory_step))
    if cost_routing:
        yield chat_service.sse({"type": "model_route", **cost_routing})
    yield chat_service.sse({"type": "usage", **usage, "credits": credits})
    if title:
        yield chat_service.sse({"type": "title", "title": title})
    # The stored id travels with `done`; the browser's streaming id is provisional.
    yield chat_service.sse({"type": "done", **({"messageId": answer_id} if answer_id else {})})

    # Only this turn's signal; a newer turn may own the set.
    live = _STOPPING.get(session_id)
    if live is not None:
        live.discard(stopping)
        if not live:
            del _STOPPING[session_id]
    await _release_turn(session_id)


@router.post("/{session_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def stop_turn(session_id: str, user: CurrentUser, db: DbSession):
    """Asks the turn running on this session to stop. Idempotent."""
    # Closing the socket means the opposite: the answer is still wanted.
    await _owned(db, user, session_id)
    # This process first — no round trip needed when it holds the turn itself —
    # then every other replica, in case a superseded turn is running on one of them.
    fire_stop_locally(session_id)
    await stop_signal.broadcast(session_id)
    # The stopped turn's claim goes with it, so the next message is not refused.
    await _release_turn(session_id, token="")


async def _store_artifacts(
    *,
    user_id: str,
    session_id: str,
    project_id: str,
    content: str,
    requested_artifacts: list[dict] | None = None,
    protect_privacy: bool = False,
    legacy_masking: bool = False,
    protected_values: frozenset[str] = frozenset(),
) -> str | None:
    """Artifacts derived from a finished turn; never raises.

    Own transaction, committed before the turn's message so the panel catches up promptly.
    """
    masker = governance.mask_legacy if legacy_masking else governance.mask

    def at_rest(value: str) -> tuple[str, int]:
        # Like the answer: the user's own details and secrets out, public details in.
        return masker(value, scope="answer", protected=set(protected_values))

    privacy_masker = at_rest if protect_privacy else None
    artifact_id: str | None = None
    async with SessionLocal() as db:
        try:
            # A `create_artifact` call wins over extraction from the transcript.
            # Both run: a turn can do each once.
            requested_id = await artifact_extract.store_requested(
                db,
                user_id=user_id,
                session_id=session_id,
                project_id=project_id,
                requests=requested_artifacts or [],
                masker=privacy_masker,
            )
            extracted_id = await artifact_extract.extract(
                db,
                user_id=user_id,
                session_id=session_id,
                project_id=project_id,
                content=privacy_masker(content)[0] if privacy_masker else content,
            )
            artifact_id = requested_id or extracted_id
            await db.commit()
        except Exception:  # noqa: BLE001
            log.exception("artifact extraction failed for session %s", session_id)
            return None
    return artifact_id


async def _enrich_memory(
    *,
    user_id: str,
    session_id: str,
    content: str,
    first_user_message: str,
    api_key: str,
    model: dict,
    auto_memory: bool,
    protect_privacy: bool = False,
    strict_local: bool = False,
    disable_fallbacks: bool = False,
    redact_logging: bool = False,
    legacy_masking: bool = False,
    message_id: str | None = None,
    protected_values: frozenset[str] = frozenset(),
) -> dict | None:
    """Facts auto-memory pulls from a finished turn; never raises.

    Own transaction, after the answer is durable; the memory step is appended to it.
    """
    if not auto_memory:
        return None
    memory_step: dict | None = None
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return None
        masker = governance.mask_legacy if legacy_masking else governance.mask

        def at_rest(value: str) -> tuple[str, int]:
            return masker(value, scope="answer", protected=set(protected_values))

        privacy_masker = at_rest if protect_privacy else None
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
            # Charged whether or not a fact came out.
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
            return None

        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            log.exception("memory commit failed for session %s", session_id)
            return None
    return memory_step


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
    #: `None` plans and offers; anything else is written as approved.
    approved_plan: dict | None = None,
    #: False after "있는 자료로 진행", so the writer does not ask again.
    may_ask: bool = True,
    #: Carried so a stored proposal is later written against the same files.
    attachments: list[str] | None = None,
    #: Carried with the proposal for the same reason.
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
    #: Composer's search toggle; off on a strict-local route.
    web_search: bool = True,
    project_sources: list[dict[str, Any]] | None = None,
) -> AsyncIterator[str]:
    """Drives one HTML artifact to completion and settles it.

    A half-written page is still stored. The work is the `page` pipeline's `make` flow:
    `page-write`, `page-save`.
    """
    if routing:
        # First, so the model badge updates.
        yield chat_service.sse({"type": "privacy_route", **routing})
    if skills_event:
        yield chat_service.sse(skills_event)
    for step in context_steps or ():
        yield chat_service.sse(_step_event(step))
    turn = {
        "user_id": user_id, "model": model, "outline_model": outline_model,
        "project_id": project_id, "routing": routing, "skills_event": skills_event,
        "context_steps": list(context_steps or []),
        "writer": {
            "web_search": web_search, "request": request, "approved_plan": approved_plan,
            "may_ask": may_ask, "model": model["id"],
            "outline_model": (outline_model or {}).get("id", ""),
            "tokens": design_tokens, "trusted_context": trusted_context,
            "untrusted_context": untrusted_context, "project_sources": project_sources,
        },
    }
    work = PipelineBundle(request=PipelineRequest(
        text=request, model=model["id"], api_key=api_key, approved_plan=approved_plan,
        template_id=template.id, design_tokens=design_tokens, session_id=session_id,
        turn=turn))
    work.usage = {"inputTokens": 0, "outputTokens": 0}
    async for chunk in _make_pass(work, pipeline_registry.pipeline("page"),
                                  crashed="문서를 만들지 못했습니다.",
                                  attachments=attachments, answers=answers):
        yield chunk


async def _page_save(work: PipelineBundle) -> AsyncIterator[dict]:
    """The page stored: report blocks as HTML sections of a report, any other page as an
    html artifact (billed); the message."""
    from app.pipeline.save_stages import rerun_note

    turn, request, usage = work.request.turn, work.request.text, work.usage
    model, outline_model = turn["model"], turn.get("outline_model")
    user_id, session_id = turn["user_id"], work.request.session_id
    template = design_templates.get(work.request.template_id)
    design_tokens = work.request.design_tokens
    html = str(work.document.data.get("html") or "")
    blocks = list(work.document.data.get("blocks") or [])
    doc_title = str((work.wire.get("title") or {}).get("title") or "").strip()
    sources = list((work.wire.get("sources") or {}).get("sources") or [])
    research_log = (work.wire.get("research") or {}).get("research")
    research_log = dict(research_log) if research_log is not None else None
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
    saved: dict[str, Any] = {"artifactId": None, "credits": credits, "messageId": None}
    artifact_id: str | None = None
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        user = await db.get(User, user_id)
        if session is not None and user is not None and template is not None:
            title = (doc_title or session.title or request.strip()[:60] or template.name)[:200]
            summary = (rerun_note(turn) or _regeneration_summary(request))[:80]
            if written and html and session.kind is SessionKind.report:
                # Report blocks are stored as HTML sections so the page view can
                # re-render them under any template; decks keep the html artifact.
                artifact_id = await _store_document(
                    db,
                    session,
                    user_id=user_id,
                    project_id=turn.get("project_id"),
                    kind=ArtifactKind.report,
                    title=title,
                    summary=summary,
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
                            len(re.sub(r"<[^>]{1,2000}>", " ", b.get("html") or "").split())
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
                    project_id=turn.get("project_id"),
                    kind=ArtifactKind.html,
                    title=title,
                    summary=summary,
                    data={
                        "kind": "html",
                        "language": "html",
                        "content": html,
                        "templateId": template.id,
                        # Blocks are the source, `content` the render; both kept
                        # for per-block rewrites.
                        "blocks": [
                            {"title": b["title"], "layout": b["layout"], "html": b["html"]}
                            for b in blocks
                        ],
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
                # The proposal has become the document.
                session.pending = None

                settle(
                    db,
                    user,
                    credits,
                    reason="page.generate",
                    session_id=session_id,
                    model=model["id"],
                )
                record_searches(
                    db,
                    user,
                    _searches_in(research_log),
                    session_id=session_id,
                    surface=session.kind.value,
                )
            if artifact_id:
                message = Message(
                    session_id=session_id,
                    role=Role.assistant,
                    content=rerun_note(turn)
                    or f"{template.name}으로 {len(written)}개 부분을 작성했습니다.",
                    usage={**usage, "credits": credits},
                    model=model["id"],
                    steps=_prelude_steps(turn.get("skills_event"),
                                         turn.get("context_steps")) or None,
                    routing=turn.get("routing"),
                )
                db.add(message)
                saved.update(artifactId=artifact_id, messageId=message.id)
            session.updated_at = utcnow()
            db.add(session)
            await db.commit()
    work.document.saved = saved
    return
    yield  # an async generator


async def _run_deck(
    *,
    #: Deck template: rules to the planner, `look` as the face, id on the artifact for export.
    template: design_templates.DesignTemplate | None = None,
    outline_model: dict | None = None,
    #: `None` plans and offers; anything else is written as approved.
    approved_plan: dict | None = None,
    #: False after "있는 자료로 진행", so the writer does not ask again.
    may_ask: bool = True,
    #: Carried so a stored proposal is later written against the same files.
    attachments: list[str] | None = None,
    #: Carried with the proposal for the same reason.
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
    #: Composer's search toggle; off on a strict-local route.
    web_search: bool = True,
    #: Pictures agreed to on the figure card: `None` on the planning pass, `[]`
    #: when declined. Only the report draws them.
    figures_plan: list[dict] | None = None,
    #: Draws the figures; the image default, not the writer.
    image_model: dict | None = None,
    #: A restructure: the artifact whose untouched parts are carried over after writing.
    carry_from: str | None = None,
    carry_note: str = "",
) -> AsyncIterator[str]:
    """Drives one deck to completion and settles it.

    Same contract as `_run_report`: the deck is an artifact, not a chat message. The work is
    the `deck` pipeline's `make` flow: `deck-write`, `deck-carry`, `deck-save`.
    """
    if routing:
        # First, so the model badge updates.
        yield chat_service.sse({"type": "privacy_route", **routing})
    if skills_event:
        yield chat_service.sse(skills_event)
    for step in context_steps or ():
        yield chat_service.sse(_step_event(step))
    if template is not None and template.instructions.strip():
        # Template genre rules reach the planner; its typesetting rules do not.
        trusted_context = [f"[서식: {template.name}]\n{template.instructions.strip()}"] + list(
            trusted_context or []
        )
    turn = {
        "user_id": user_id, "model": model, "outline_model": outline_model,
        "project_id": project_id, "routing": routing, "skills_event": skills_event,
        "context_steps": list(context_steps or []), "carry_from": carry_from,
        "carry_note": carry_note,
        "writer": {
            "web_search": web_search, "figures_plan": figures_plan, "image_model": image_model,
            "request": request, "approved_plan": approved_plan, "may_ask": may_ask,
            "model": model["id"], "outline_model": (outline_model or {}).get("id", ""),
            "trusted_context": list(trusted_context or []),
            "untrusted_context": list(untrusted_context or []), "tokens": design_tokens,
        },
    }
    work = PipelineBundle(request=PipelineRequest(
        text=request, model=model["id"], api_key=api_key, approved_plan=approved_plan,
        template_id=template.id if template else None, design_tokens=design_tokens,
        session_id=session_id, turn=turn))
    work.usage = {"inputTokens": 0, "outputTokens": 0}
    async for chunk in _make_pass(work, pipeline_registry.pipeline("deck"),
                                  crashed="슬라이드를 만들지 못했습니다.",
                                  attachments=attachments, answers=answers):
        yield chunk


async def _make_pass(work: PipelineBundle, pipe: Any, *, crashed: str,
                     attachments: list[str] | None = None, answers: dict | None = None,
                     start: str | None = None, recorder: pipeline_runs.Recorder | None = None,
                     carried: dict | None = None, attempts: dict | None = None,
                     parent: Any = None) -> AsyncIterator[str]:
    """A one-pass writer's `make` flow (deck, page): it plans and offers (settled here as a
    plan turn), or writes the approved outline and its save stage stores it. Then the
    artifact, the usage, done."""
    turn, user_id, session_id = work.request.turn, work.request.turn["user_id"], \
        work.request.session_id
    model, outline_model = turn["model"], turn.get("outline_model")
    if recorder is None and work.request.approved_plan is not None:
        recorder = pipeline_runs.Recorder(run_id=uuid4().hex)
    runner = PipelineRunner(pipe.stages("make"), name=f"{pipe.name}.make", recorder=recorder)
    runner.attempts = dict(attempts or {})
    if recorder is not None:
        recorder.pipeline, recorder.flow = pipe.name, "make"
        turn["run_id"] = recorder.run_id
        recorder.begin(work, carried=carried)
        if start is None and (event := _run_event(recorder.run_id, pipe.name, "make")):
            yield chat_service.sse(event)
    try:
        async for event in runner.run(work, PipelineContext(
                options=dict(pipe.options), run_id=recorder.run_id if recorder else ""),
                start=start):
            yield chat_service.sse(event)
    except Exception as exc:  # noqa: BLE001 — the turn must still settle
        log.exception("%s generation crashed for session %s", pipe.name, session_id)
        yield chat_service.sse(_error_event(crashed, exc))
    finally:
        if parent is not None:
            _carry_records(work, parent, start)
    proposal, questions = work.plan.proposal, work.findings.questions or None
    usage = work.usage
    # Planned or asked: stored on the session, no artifact written.
    if proposal is not None or questions is not None:
        drawn = (proposal or {}).pop("figures", None)
        await _settle_plan_turn(
            session_id=session_id,
            user_id=user_id,
            request=work.request.text,
            attachments=list(attachments or []),
            answers=dict(answers or {}),
            model=model,
            outline_model=outline_model,
            usage=usage,
            proposal=proposal,
            questions=questions,
            figures={"plan": proposal, **drawn} if drawn and proposal else None,
            carry_from=turn.get("carry_from"),
            carry_note=str(turn.get("carry_note") or ""),
            run=_run_summary(work, pipe.name, "make"),
        )
        yield chat_service.sse({"type": "usage", **usage, "credits": 0})
        yield chat_service.sse({"type": "done"})
        return
    if work.document.saved is None and turn.get("user_id"):
        # The writer died: what there is is stored as it stands (a deck records the
        # failure), by the flow's own save stage.
        save = pipeline_registry.stage(pipe.flow("make")[-1])
        async for event in PipelineRunner([save], name=f"{pipe.name}.save",
                                          emit_records=False).run(work):
            yield chat_service.sse(event)
    saved = work.document.saved or {}
    if recorder is not None:
        await _persist_run(work, recorder, session_id=session_id, user_id=user_id, saved=saved,
                           parent=parent, from_stage=start)
    if saved.get("artifactId"):
        yield chat_service.sse({"type": "artifact", "artifactId": saved["artifactId"]})
    yield chat_service.sse({"type": "usage", **usage, "credits": int(saved.get("credits") or 0)})
    yield chat_service.sse({"type": "done"})


async def _document_write(work: PipelineBundle, service: Any, payload: str
                          ) -> AsyncIterator[dict]:
    """A one-pass writer's stream (`deck_service.write`, `page_service.write`): its
    proposal or question kept to settle the plan turn, its document onto the bundle, the
    rest passed on."""
    stream = service.write(**work.request.turn["writer"], api_key=work.request.api_key,
                           **({"template": design_templates.get(work.request.template_id)}
                              if payload == "page" else {}))
    async for event in stream:
        if event["type"] in ("proposal", "needs"):
            # Stopped on purpose: forwarded, and settled by the caller instead of an artifact.
            if event["type"] == "proposal":
                work.plan.proposal = event["plan"]
            else:
                work.findings.questions = list(event["questions"] or [])
            yield event
            continue
        if event["type"] == payload:
            if payload == "deck":
                work.document.sections = list(event["slides"])
            else:
                work.document.data = {"html": event["html"], "blocks": event["blocks"]}
            continue
        if event["type"] == "usage":
            work.usage = {k: v for k, v in event.items() if k != "type"}
            continue
        yield event
    if work.plan.proposal is not None or work.findings.questions:
        raise PipelineStop("proposal offered" if work.plan.proposal is not None else "needs")


def _deck_write(work: PipelineBundle) -> AsyncIterator[dict]:
    return _document_write(work, deck_service, "deck")


def _page_write(work: PipelineBundle) -> AsyncIterator[dict]:
    return _document_write(work, page_service, "page")


async def _deck_carry(work: PipelineBundle) -> AsyncIterator[dict]:
    """A restructure: the slides the instruction did not touch carried over from the deck
    it restructures."""
    turn, request = work.request.turn, work.request.text
    carry_from, carry_note = turn.get("carry_from"), str(turn.get("carry_note") or "")
    slides = work.document.sections
    if not deck_service.filled(slides):
        return
    async with SessionLocal() as db:
        previous = await db.get(Artifact, carry_from)
    if previous is None or not previous.data:
        return
    old_slides = list(previous.data.get("slides") or [])
    slides, carried = revise.carry_parts(
        slides, old_slides, is_deck=True, mentioned=carry_note
    )
    # The slides a merge absorbed lend their rows; a carried agenda lists the
    # deck as it now is.
    slides = revise.absorb_rows(slides, old_slides, mentioned=carry_note)
    slides = revise.unmix_rows(slides, old_slides, mentioned=carry_note)
    slides = revise.carry_by_content(slides, old_slides, mentioned=carry_note)
    slides = revise.keep_timeline_layout(
        slides, old_slides, mentioned=carry_note, request=request
    )
    slides = revise.refresh_agenda(slides)
    # Carrying old slides back can drop a number the draft had restated; the
    # person's figures are checked once more against the deck as it now stands.
    deck_service.restate_missing_facts(slides, request)
    work.document.sections = slides
    if carried:
        yield {"type": "step", "id": "carry", "label": f"그대로 둔 장 {carried}개",
               "status": "done"}
        for slide in slides:
            yield {"type": "slide", "slide": slide, "done": True}


async def _deck_save(work: PipelineBundle) -> AsyncIterator[dict]:
    """The deck stored (a version when the session holds one), the message, the bill; a
    deck with nothing written records the failure so a reload shows it."""
    from app.pipeline.save_stages import rerun_note

    turn, request, usage = work.request.turn, work.request.text, work.usage
    model, outline_model = turn["model"], turn.get("outline_model")
    user_id, session_id = turn["user_id"], work.request.session_id
    carry_from, carry_note = turn.get("carry_from"), str(turn.get("carry_note") or "")
    skills_event, context_steps = turn.get("skills_event"), turn.get("context_steps")
    routing, approved_plan = turn.get("routing"), work.request.approved_plan
    design_tokens = work.request.design_tokens
    template = design_templates.get(work.request.template_id) if work.request.template_id \
        else None
    slides = work.document.sections
    doc_title = str((work.wire.get("title") or {}).get("title") or "").strip()
    research_log = (work.wire.get("research") or {}).get("research")
    research_log = dict(research_log) if research_log is not None else None
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
    saved: dict[str, Any] = {"artifactId": None, "credits": credits, "messageId": None}
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        user = await db.get(User, user_id)
        if session is not None and user is not None:
            title = (doc_title or session.title or request.strip()[:60] or "슬라이드")[:200]
            if carry_from and not revise.requested_title(carry_note):
                # A restructure keeps the deck's name; only an asked-for rename changes it.
                kept = await db.get(Artifact, carry_from)
                if kept is not None and kept.title:
                    title = kept.title
            if written:
                artifact_design = design_tokens
                if template is not None and template.look:
                    # The template's look outranks the project default and the request.
                    artifact_design = design_service.normalise_tokens(
                        {**(design_tokens or {}), "visualStyle": template.look}
                    )
                elif not artifact_design:
                    requested_style = str(
                        (approved_plan or {}).get("visualStyle")
                        or design_service.visual_style_for(request)
                    )
                    # The deck's own colour travels with it, so the design panel and the
                    # bulk accent control start from what the slides wear, not the
                    # product's purple.
                    deck_accent = str(
                        next((sl.get("accent") for sl in slides if sl.get("accent")), "")
                        or deck_service.topic_accent(request)
                    )
                    artifact_design = design_service.normalise_tokens(
                        {
                            "visualStyle": requested_style,
                            "accent": deck_accent,
                            "font": design_service.font_for(request, requested_style),
                        }
                    )
                artifact_id = await _store_document(
                    db,
                    session,
                    user_id=user_id,
                    project_id=turn.get("project_id"),
                    kind=ArtifactKind.deck,
                    title=title,
                    summary=(rerun_note(turn) or _regeneration_summary(request))[:80],
                    data={
                        "kind": "deck",
                        "theme": "기본",
                        # Export uses the template's PowerPoint half.
                        **({"templateId": template.id} if template else {}),
                        # Snapshot: a later project change must not repaint the deck.
                        **({"design": artifact_design} if artifact_design else {}),
                        "lint": lint.wire(lint.check(lint.from_slides(slides), slides=True)),
                        # Every slide, including unwritten ones — a gap stays
                        # visible so it can be fixed.
                        "slides": slides,
                    },
                )
                session.artifact_id = artifact_id
                # The proposal has become the document.
                session.pending = None
                message = Message(
                    session_id=session_id,
                    role=Role.assistant,
                    content=rerun_note(turn) or f"{len(written)}장짜리 슬라이드를 만들었습니다.",
                    usage={**usage, "credits": credits},
                    model=model["id"],
                    steps=_prelude_steps(skills_event, context_steps) or None,
                    routing=routing,
                )
                db.add(message)
                settle(
                    db,
                    user,
                    credits,
                    reason="deck.generate",
                    session_id=session_id,
                    model=model["id"],
                )
                record_searches(
                    db,
                    user,
                    _searches_in(research_log),
                    session_id=session_id,
                    surface=session.kind.value,
                )
                saved.update(artifactId=artifact_id, messageId=message.id)
            else:
                # Nothing written: record the failure so a reload shows it.
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
    work.document.saved = saved
    return
    yield  # an async generator


#: The reference style a document's format uses; APA where the format names none.
_CITATION_STYLES = {"paper": "IEEE", "lab": "IEEE", "review": "IEEE"}


def _skeleton(artifact: Artifact) -> list[str]:
    """The document's part names in order: slide titles, or section headings."""
    data = artifact.data or {}
    if artifact.kind is ArtifactKind.deck:
        return [str(p.get("title") or "") for p in data.get("slides") or []]
    return [str(p.get("heading") or "") for p in data.get("sections") or []]


def _part_texts(artifact: Artifact) -> list[str]:
    """Each part's own words, in order — what a restructure must not lose."""
    data = artifact.data or {}
    if artifact.kind is ArtifactKind.deck:
        out = []
        for slide in data.get("slides") or []:
            pieces = [deck_service._drafted_text(slide)]
            for row in slide.get("rows") or []:
                if isinstance(row, list):
                    pieces.append(" | ".join(str(c) for c in row))
            for metric in slide.get("metrics") or []:
                if isinstance(metric, dict):
                    pieces.append(" ".join(str(v) for v in metric.values()))
            out.append("\n".join(piece for piece in pieces if piece))
        return out
    return [
        re.sub(r"<[^>]+>", " ", str(p.get("content") or "")) for p in data.get("sections") or []
    ]


async def _revision_plan(
    db: AsyncSession,
    session: ChatSession,
    *,
    instruction: str,
    model: dict,
    api_key: str,
) -> tuple[revise.Plan | None, list[str]]:
    """Where an instruction under the document lands, and the document's skeleton.

    `(None, [])` when there is no document to read it against; `_revise_document`
    then reports that itself.
    """
    artifact = await db.get(Artifact, session.artifact_id) if session.artifact_id else None
    if artifact is None or not artifact.data:
        return None, []
    skeleton = _skeleton(artifact)
    if not skeleton:
        return None, []
    if (swap := revise.order_swap(instruction, skeleton)) is not None:
        # Two named parts trading places is done in place by `_revise_document`; the
        # planner would read 「순서」 as a restructure and redraw the whole deck.
        first, second = swap
        return revise.Plan(
            scope="parts", targets=[], note=f"{skeleton[first]} ↔ {skeleton[second]}"
        ), skeleton
    if (added := revise.explicit_insert(instruction, skeleton)) is not None:
        # 「위험 장을 하나 추가해 줘」 is an insert in so many words; a planner that reads it
        # as a new document would refuse a plain edit.
        return added, skeleton
    plan = await revise.plan(
        message=instruction,
        title=artifact.title or "",
        parts=skeleton,
        model=model["id"],
        api_key=api_key,
    )
    return plan, skeleton


async def _original_request(db: AsyncSession, session: ChatSession) -> str:
    """What the document was first asked for: the conversation's first user message."""
    first = (
        await db.exec(
            select(Message)
            .where(Message.session_id == session.id, Message.role == Role.user)
            .order_by(col(Message.created_at))
            .limit(1)
        )
    ).first()
    return str(first.content or "").strip() if first else ""


async def _revise_document(
    *,
    user_id: str,
    api_key: str,
    session_id: str,
    model: dict,
    instruction: str,
    routing: dict[str, Any] | None = None,
    plan: revise.Plan | None = None,
    planner: dict | None = None,
    #: Formats asked for in the same turn (「견적 고치고 PDF로」), handed over after the edit.
    export_after: list[str] | None = None,
) -> AsyncIterator[str]:
    """Applies one instruction to the document on screen.

    `services.revise` says which parts it lands on (`plan`, when the caller already
    asked); those are rewritten with the surface's own machinery. Failures are
    reported, never turned into a regeneration. The flow is the document's `revise`
    pipeline: `revise-load`, then the route, widen, rewrite and align stages, then
    `revise-save`; the run is kept so it can be rerun from a stage.
    """
    usage = {"inputTokens": 0, "outputTokens": 0}
    if routing:
        yield chat_service.sse({"type": "privacy_route", **routing})

    turn = {"user_id": user_id, "model": model, "routing": routing,
            "export_after": export_after}
    # `revise-load` first: which pipeline revises it depends on what is on screen.
    loading = PipelineBundle(request=PipelineRequest(
        text="", model=model["id"], api_key=api_key, instruction=instruction,
        session_id=session_id, turn=turn))
    load = PipelineRunner([pipeline_registry.stage("revise-load")], name="revise.load",
                          emit_records=False)
    async for event in load.run(loading):
        yield chat_service.sse(event)
    loaded = loading.document.loaded or {}
    if loaded.get("missing") or not loaded:
        yield chat_service.sse({"type": "error", "message": "고칠 문서를 찾지 못했습니다."})
        yield chat_service.sse({"type": "done"})
        return
    is_deck = loaded["kind"] == ArtifactKind.deck.value
    data = dict(loaded["data"])
    parts = list(data.get("slides") if is_deck else data.get("sections") or [])
    if not parts:
        yield chat_service.sse({"type": "error", "message": "고칠 내용이 없습니다."})
        yield chat_service.sse({"type": "done"})
        return

    # The document's revise flow (`app/pipelines/*.toml` `revise`): route, widen to what
    # rests on a changed fact, rewrite, align, store. A stage that ends the turn early has
    # sent its own closing events.
    doc_format = str((data.get("titleBlock") or {}).get("format") or "")
    work = revise_stages.bundle_for(
        request=str(loaded.get("request") or ""), instruction=instruction, model=model,
        planner=planner, api_key=api_key, is_deck=is_deck, title=str(loaded.get("title") or ""),
        data=data, parts=parts, material=list(loaded.get("material") or []), plan=plan,
        format_id=doc_format, usage=usage, session_id=session_id, turn=turn,
    )
    work.steps.extend(loading.steps)
    pipe = (pipeline_registry.pipeline("deck") if is_deck
            else pipeline_registry.for_report(doc_format))
    recorder = pipeline_runs.Recorder(run_id=uuid4().hex)
    recorder.pipeline, recorder.flow = pipe.name, "revise"
    turn["run_id"] = recorder.run_id
    if run_event := _run_event(recorder.run_id, pipe.name, "revise"):
        yield chat_service.sse(run_event)
    if pipeline_records_on():
        for record in loading.steps:
            record.run_id = recorder.run_id
            yield chat_service.sse(record.wire())
    async for chunk in _revise_pass(work, pipe, recorder, session_id=session_id,
                                    user_id=user_id):
        yield chunk


def _revise_stages(pipe: Any) -> list:
    """A pipeline's revise flow after `revise-load` (run apart, before the pipeline is
    known)."""
    return [stage for stage in pipe.stages("revise") if stage.name != "revise-load"]


async def _revise_pass(work: PipelineBundle, pipe: Any, recorder: pipeline_runs.Recorder, *,
                       session_id: str, user_id: str, start: str | None = None,
                       carried: dict | None = None, attempts: dict | None = None,
                       parent: Any = None) -> AsyncIterator[str]:
    """The revise flow over `work` (from `start` on a rerun), then what the stored revision
    says: the artifact, the files handed over, the reply, the usage, done."""
    runner = PipelineRunner(_revise_stages(pipe), name=f"{pipe.name}.revise", recorder=recorder)
    runner.attempts = dict(attempts or {})
    recorder.begin(work, carried=carried)
    try:
        async for event in runner.run(work, PipelineContext(options=dict(pipe.options),
                                                            run_id=recorder.run_id),
                                      start=start):
            yield chat_service.sse(event)
    finally:
        if parent is not None:
            _carry_records(work, parent, start)
    saved = work.document.saved or {}
    await _persist_run(work, recorder, session_id=session_id, user_id=user_id, saved=saved,
                       parent=parent, from_stage=start)
    if any(r.status == "stopped" for r in work.steps):
        return
    export_after = work.request.turn.get("export_after")
    if saved.get("artifactId"):
        yield chat_service.sse({"type": "artifact", "artifactId": saved["artifactId"]})
    if export_after is not None and saved.get("artifactId"):
        if saved.get("handed"):
            yield chat_service.sse({"type": "step", **saved["handed"]})
        yield chat_service.sse({"type": "delta", "text": saved.get("reply") or ""})
    elif saved.get("artifactId") and start is not None:
        # A rerun says what it did; a revision's reply is the changed count it stores.
        yield chat_service.sse({"type": "delta", "text": saved.get("reply") or ""})
    yield chat_service.sse({"type": "usage", **work.usage,
                            "credits": int(saved.get("credits") or 0)})
    yield chat_service.sse({"type": "done"})


def _carry_records(work: PipelineBundle, parent: Any, start: str | None) -> None:
    """A rerun's records of the stages it did not run again: the parent run's own."""
    if not start:
        return
    earlier = {str(r.get("stage")): r for r in (parent.stages or [])}
    for index, record in enumerate(work.steps):
        if record.status == "skipped" and record.reason == f"resumed from {start}":
            if (row := earlier.get(record.stage)) is not None:
                work.steps[index] = StepRecord.from_dict(row)


async def _export_files(
    artifact: Artifact, formats: list[str], *, user_id: str, session_id: str
) -> tuple[dict | None, str]:
    """The document as the asked formats, kept as chat files: the step that carries
    their download chips (`None` when none was made) and what the reply says.

    The `export` stage (`app.pipeline.export_stages`); the document itself is not touched.
    """
    work = export_stages.bundle_for(artifact=artifact, formats=formats, user_id=user_id,
                                    session_id=session_id)
    async for _event in PipelineRunner([pipeline_registry.stage("export")],
                                       name="export").run(work):
        pass
    return work.document.handed, work.document.reply


async def _hand_over_document(
    *,
    user_id: str,
    session_id: str,
    formats: list[str],
    routing: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """Answers a turn that only asks for the document as a file.

    Never revises: the artifact and its version stay as they are.
    """
    if routing:
        yield chat_service.sse({"type": "privacy_route", **routing})
    yield chat_service.sse(
        {"type": "step", "id": "export", "label": "파일 만드는 중", "status": "running"}
    )
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        artifact = (
            await db.get(Artifact, session.artifact_id) if session and session.artifact_id else None
        )
        if session is None or artifact is None or not artifact.data:
            yield chat_service.sse(
                {"type": "step", "id": "export", "label": "파일 만들기", "status": "error"}
            )
            yield chat_service.sse({"type": "error", "message": "내보낼 문서를 찾지 못했습니다."})
            yield chat_service.sse({"type": "done"})
            return
        step, said = await _export_files(
            artifact, formats, user_id=user_id, session_id=session_id
        )
        if step is None:
            said = said or "파일을 만들지 못했습니다. 파일 탭 → 내보내기에서 받아 주세요."
        db.add(
            Message(
                session_id=session_id,
                role=Role.assistant,
                content=said,
                usage={"inputTokens": 0, "outputTokens": 0, "credits": 0},
                routing=routing,
                steps=[step] if step else None,
            )
        )
        session.updated_at = utcnow()
        db.add(session)
        await db.commit()
    yield chat_service.sse(
        {"type": "step", **step}
        if step
        else {"type": "step", "id": "export", "label": "파일 만들기", "status": "error"}
    )
    yield chat_service.sse({"type": "delta", "text": said})
    yield chat_service.sse({"type": "usage", "inputTokens": 0, "outputTokens": 0, "credits": 0})
    yield chat_service.sse({"type": "done"})


async def _run_report(
    *,
    template: design_templates.DesignTemplate | None = None,
    #: The agent's bound `doc_formats` id; the 서식's own format wins over it.
    format_id: str | None = None,
    outline_model: dict | None = None,
    #: `None` plans and offers; anything else is written as approved.
    approved_plan: dict | None = None,
    #: False after "있는 자료로 진행", so the writer does not ask again.
    may_ask: bool = True,
    #: Carried so a stored proposal is later written against the same files.
    attachments: list[str] | None = None,
    #: Carried with the proposal for the same reason.
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
    #: Composer's search toggle; off on a strict-local route.
    web_search: bool = True,
    #: Pictures agreed to on the figure card: `None` on the planning pass, `[]`
    #: when declined. Only the report draws them.
    figures_plan: list[dict] | None = None,
    #: Draws the figures; the image default, not the writer.
    image_model: dict | None = None,
    project_sources: list[dict[str, Any]] | None = None,
    #: A restructure: the artifact whose untouched parts are carried over after writing.
    carry_from: str | None = None,
    carry_note: str = "",
    #: Check skills the agent and the 서식 attach; None leaves it to the 서식.
    check_keys: tuple[str, ...] | None = None,
    #: The agent's `check_options`: how strict the trace is.
    check_options: dict | None = None,
    #: What the server runs for a person's check skill among `check_keys`.
    skill_specs: dict | None = None,
    #: The agent's own 진행 과정 설정 (`Agent.process_settings`), over its 서식's.
    process: dict | None = None,
    #: The run this pass follows (the card that waited before it).
    parent_run_id: str | None = None,
) -> AsyncIterator[str]:
    """Drives one report to completion and settles it. The document is an artifact with versions.

    The writing pass is the format's `write` flow ending in `report-save`, which stores the
    artifact, the message and the bill; the run is kept (`PipelineRun`) so it can be run
    again from any of its stages. A planning pass stops at its outline or question and is
    settled here."""
    proposal: dict | None = None
    questions: list[dict] | None = None
    usage = {"inputTokens": 0, "outputTokens": 0}
    writing = approved_plan is not None
    recorder = pipeline_runs.Recorder(run_id=uuid4().hex) if writing else None
    built: list = []
    announced = False
    turn = _report_turn(
        user_id=user_id, model=model, outline_model=outline_model, project_id=project_id,
        routing=routing, skills_event=skills_event, context_steps=context_steps,
        carry_from=carry_from, carry_note=carry_note,
        run_id=recorder.run_id if recorder else "",
    )
    #: What the stream said, for a writer that does not store its own result.
    told: dict[str, Any] = {}

    if routing:
        # First, so the model badge updates.
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
            template_id=template.id if template else None,
            format_id=format_id,
            design_tokens=design_tokens,
            check_keys=check_keys,
            check_options=check_options,
            skill_specs=skill_specs,
            process=process,
            session_id=session_id,
            turn=turn,
            recorder=recorder,
            bundle_out=built,
        )
        async for event in stream:
            if not announced and built and recorder is not None:
                announced = True
                if run_event := _run_event(recorder.run_id, built[0][1], built[0][2]):
                    yield chat_service.sse(run_event)
            if event["type"] in ("proposal", "needs"):
                # Stopped on purpose: forwarded, and stored below instead of an artifact.
                if event["type"] == "proposal":
                    proposal = event["plan"]
                else:
                    questions = event["questions"]
                yield chat_service.sse(event)
                continue
            if event["type"] in ("report", "titleBlock", "sources", "research", "title"):
                told[event["type"]] = event
            if event["type"] == "report":
                continue
            if event["type"] == "usage":
                usage = {k: v for k, v in event.items() if k != "type"}
                continue
            yield chat_service.sse(event)
    except Exception as exc:  # noqa: BLE001 — the turn must still settle
        log.exception("report generation crashed for session %s", session_id)
        yield chat_service.sse(_error_event("보고서를 만들지 못했습니다.", exc))

    work = built[0][0] if built else None
    # Planned or asked: stored on the session, no artifact written.
    if proposal is not None or questions is not None:
        # The figure question is held until the outline is approved.
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
            carry_from=carry_from,
            carry_note=carry_note,
            run=_run_summary(work, built[0][1], built[0][2]) if work is not None else None,
            run_parent=parent_run_id,
        )
        yield chat_service.sse({"type": "usage", **usage, "credits": 0})
        yield chat_service.sse({"type": "done"})
        return

    if (work is None or work.document.saved is None) and "report" in told:
        # A writer whose flow did not store the document (one stopped before its save
        # stage, a replaced writer): `report-save` on what it sent.
        work = _told_bundle(told, usage=usage, turn=turn, request=request,
                            session_id=session_id, approved_plan=approved_plan,
                            template=template, design_tokens=design_tokens, model=model)
        try:
            async for event in PipelineRunner([pipeline_registry.stage("report-save")],
                                              name="report.save", emit_records=False).run(work):
                yield chat_service.sse(event)
        except Exception as exc:  # noqa: BLE001 — the turn must still settle
            log.exception("report store failed for session %s", session_id)
            yield chat_service.sse(_error_event("보고서를 만들지 못했습니다.", exc))
        recorder = None
    async for chunk in _report_tail(work, recorder=recorder, session_id=session_id,
                                    user_id=user_id, usage=usage, parent=parent_run_id):
        yield chunk


def _told_bundle(told: dict[str, Any], *, usage: dict, turn: dict, request: str,
                 session_id: str, approved_plan: dict | None, template: Any,
                 design_tokens: dict | None, model: dict) -> PipelineBundle:
    """A bundle holding what a writer's events said, for `report-save` alone."""
    work = PipelineBundle(request=PipelineRequest(
        text=request, model=model["id"], api_key="", approved_plan=approved_plan,
        template_id=template.id if template else None, design_tokens=design_tokens,
        session_id=session_id, turn=turn))
    work.document.sections = list((told.get("report") or {}).get("sections") or [])
    work.wire = {k: v for k, v in told.items() if k != "report"}
    work.usage = {"inputTokens": 0, "outputTokens": 0, **usage}
    return work


# ── pipeline runs ─────────────────────────────────────────────────────


#: Above any real run's snapshots (a long proposal's are a few MB); refuses a runaway one.
_MAX_SNAPSHOTS = 24_000_000


def _run_event(run_id: str, pipeline: str, flow: str, *, parent_id: str | None = None,
               from_stage: str | None = None) -> dict | None:
    """The `run` event a recorded pass opens with (records on only)."""
    if not pipeline_records_on():
        return None
    return {"type": "run", "runId": run_id, "pipeline": pipeline, "flow": flow,
            "parentId": parent_id, "fromStage": from_stage}


def _run_status(records: list) -> str:
    statuses = [r.status for r in records]
    return next((x for x in ("failed", "cancelled", "waiting", "stopped") if x in statuses),
                "done")


def _run_summary(work: PipelineBundle, pipeline: str, flow: str, *, run_id: str = "",
                 parent_id: str | None = None, from_stage: str | None = None) -> dict | None:
    """The compact run kept on the message (`routing.run`): records without evidence."""
    if not pipeline_records_on() or not work.steps:
        return None
    return {"id": run_id, "pipeline": pipeline, "flow": flow,
            "status": _run_status(work.steps), "parentId": parent_id, "fromStage": from_stage,
            "stages": [r.stage_wire(evidence=False) for r in work.steps]}


async def _persist_run(work: PipelineBundle, recorder: pipeline_runs.Recorder, *,
                       session_id: str, user_id: str, saved: dict | None = None,
                       parent: Any = None, from_stage: str | None = None,
                       pipeline: str = "", flow: str = "") -> str | None:
    """Keeps the run (`PipelineRun`) and its compact copy on the message it settled.

    Best effort: a run that cannot be kept costs the rerun, never the turn."""
    saved = saved or {}
    pipeline, flow = pipeline or recorder.pipeline, flow or recorder.flow
    snapshots = recorder.snapshots()
    if len(json.dumps(snapshots, ensure_ascii=False, default=str)) > _MAX_SNAPSHOTS:
        # Kept without its snapshots: the records stand, the run cannot be rerun.
        log.warning("pipeline run %s too large to keep its snapshots", recorder.run_id)
        snapshots = {}
    try:
        async with SessionLocal() as db:
            artifact = (await db.get(Artifact, saved["artifactId"])
                        if saved.get("artifactId") else None)
            row = PipelineRun(
                id=recorder.run_id, session_id=session_id, user_id=user_id,
                message_id=saved.get("messageId"), artifact_id=saved.get("artifactId"),
                artifact_version=getattr(artifact, "version", None),
                parent_id=parent if isinstance(parent, str) else getattr(parent, "id", None),
                from_stage=from_stage,
                pipeline=pipeline, flow=flow, status=_run_status(work.steps),
                params={"request": recorder.request},
                stages=[r.as_dict() for r in work.steps],
                snapshots=snapshots,
            )
            db.add(row)
            if saved.get("messageId") and (
                    summary := _run_summary(work, pipeline, flow, run_id=recorder.run_id,
                                            parent_id=row.parent_id, from_stage=from_stage)):
                message = await db.get(Message, saved["messageId"])
                if message is not None:
                    message.routing = {**(message.routing or {}), "run": summary}
                    db.add(message)
            await db.commit()
        return recorder.run_id
    except Exception:  # noqa: BLE001
        log.exception("pipeline run %s for session %s not kept", recorder.run_id, session_id)
        return None


def _report_turn(*, user_id: str, model: dict, outline_model: dict | None,
                 project_id: str | None, routing: dict | None, skills_event: dict | None,
                 context_steps: list[dict] | None, carry_from: str | None, carry_note: str,
                 run_id: str = "", rerun_from: str = "") -> dict[str, Any]:
    """`Request.turn` of a report's writing pass: what `report-save` stores it with."""
    return {
        "user_id": user_id, "model": model, "outline_model": outline_model,
        "project_id": project_id, "routing": routing, "skills_event": skills_event,
        "context_steps": list(context_steps or []), "carry_from": carry_from,
        "carry_note": carry_note, "run_id": run_id,
        **({"rerun_from": rerun_from} if rerun_from else {}),
    }


async def _report_tail(work: PipelineBundle | None, *, recorder: pipeline_runs.Recorder | None,
                       session_id: str, user_id: str, usage: dict, parent: Any = None,
                       from_stage: str | None = None) -> AsyncIterator[str]:
    """After a report's writing pass: the run kept, the artifact named, the usage, done."""
    saved = (work.document.saved if work is not None else None) or {}
    written = [s for s in (work.document.sections if work is not None else [])
               if (s.get("content") or "").strip()]
    if work is not None and recorder is not None:
        await _persist_run(work, recorder, session_id=session_id, user_id=user_id,
                           saved=saved, parent=parent, from_stage=from_stage)
    if saved.get("artifactId"):
        yield chat_service.sse({"type": "artifact", "artifactId": saved["artifactId"]})
    elif not written:
        # The writer came back with no body (a reply spent on thinking, an empty answer):
        # said, so the turn never ends on a blank page that looks finished.
        log.warning("report for session %s came back with no section text", session_id)
        yield chat_service.sse(_error_event(
            "보고서 본문을 쓰지 못했습니다. 다시 시도하거나 다른 모델을 골라 주세요.",
            code="empty_report",
        ))
    credits = int(saved.get("credits") or 0)
    yield chat_service.sse({"type": "usage", **usage, "credits": credits})
    yield chat_service.sse({"type": "done"})
