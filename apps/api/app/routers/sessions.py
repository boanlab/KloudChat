"""Sessions, messages, chat stream: question before upstream; answer+charge together."""

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
    StoredFile,
)
from app.models.workspace import Visibility as SkillVisibility
from app.pipeline import chat_stages, export_stages, report_stages, revise_stages
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
    delete_session_files,
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
    date_rules,
    design_templates,
    exact_facts,
    fallback_watch,
    figures,
    file_schema,
    freshness,
    gap_questions,
    governance,
    grounding,
    illustrations,
    imagegen,
    index_client,
    lint,
    message_links,
    model_tier,
    output_language,
    prompt_tier,
    publishing,
    red_flags,
    revise,
    server_notes,
    settings_store,
    skill_specs,
    skill_turn,
    starter,
    stop_signal,
    user_templates,
)
from app.services import agent as agent_service
from app.services import audit as audit_service
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
from app.services import (
    outline as outline_rules,
)
from app.services import page as page_service
from app.services import report as report_service
from app.services import research as research_service
from app.services.context import (
    build_messages,
    declines_web_search,
    envelope_tokens,
    fit_history,
    history_budget,
    is_small_talk,
    literature_request,
    requests_web_search,
    search_hints,
    search_needs_planning,
    search_plan,
    search_query,
    search_subject,
    summary_block,
    weather_location,
    with_pictures,
)
from app.services.credits import (
    charge_for_image,
    charge_for_tokens,
    has_headroom,
    record_searches,
    settle,
)
from app.services.tools import sandbox_tools
from app.services.tools.base import Tool, ToolContext, openai_snapshot
from app.services.tools.builtin import MAX_GIVEN_LINKS
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
    surface_body,
    uses_memory,
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
        # no network egress; "builtin" source alone not proof
        "calculate",
        "search_knowledge",
        "create_artifact",
        "create_chart",
        "use_skill",
        # self-hosted sandbox, networking off; offered only when `sandbox_in_boundary`
        "run_skill_script",
        "run_code",
    }
)


async def _knowledge_shelf(
    db: AsyncSession, user: User, session: ChatSession, agent: WorkspaceAgent | None
) -> tuple[list[tuple[str, str, str | None]], str]:
    """Documents `search_knowledge` may search this turn, and their index collection."""
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
            # project knowledge too: overflow still searchable by passage
            or (row.project_id and row.project_id == session.project_id and row.agent_id is None)
        )
    ]
    if session.agent_id:
        # vector collection is the owner's; shared-agent users search own uploads lexically only
        owned = agent is not None and agent.owner_id == user.id
        return shelf, (agent.index_key or "") if owned else ""
    if session.project_id:
        # pre-project uploads indexed under the conversation key, so both searched
        project = await db.get(Project, session.project_id)
        if project is not None and project.index_key:
            keys = [project.index_key] + ([session.index_key] if session.index_key else [])
            return shelf, ",".join(keys)
    return shelf, session.index_key or ""


async def _has_agent_material(db: AsyncSession, user: User, session: ChatSession) -> bool:
    """Whether the agent has its own shelf material, which nothing else puts in the prompt."""
    if not session.agent_id:
        return False
    found = (
        await db.exec(
            select(StoredFile.id).where(
                StoredFile.user_id == user.id,
                StoredFile.agent_id == session.agent_id,
                StoredFile.text != "",
            ).limit(1)
        )
    ).first()
    return found is not None


#: Skills offered per turn; description stays a paragraph, not a catalogue.
_SKILL_SHELF_LIMIT = 12


def _agent_allows_skill(agent: WorkspaceAgent | None, skill: Skill) -> bool:
    """The agent's skill allowlist, read the way `_resolve_skills` reads it."""
    if agent is None or agent.skill_ids is None:
        return True
    allowed = set(agent.skill_ids)
    # store install is a copy; allowlist naming the origin covers it
    return skill.id in allowed or bool(skill.origin_id and skill.origin_id in allowed)


async def _standing_skill_ids(
    db: AsyncSession, user: User, session: ChatSession, agent: WorkspaceAgent | None
) -> list[str]:
    """Conversation's standing skills still applicable; stale ones pruned quietly."""
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
    """Enabled chat skills not picked this turn, for `use_skill`; bounded by the allowlist."""
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
        shelf.append((skill.name, skill.when_to_use, surface_body(skill.body, SessionKind.chat)))
        if len(shelf) >= _SKILL_SHELF_LIMIT:
            break
    return shelf


#: Claim older than this = crash leftover.
_CLAIM_TTL_SEC = 600
#: Claim tokens by session, request → releasing turn.
_CLAIMS: dict[str, str] = {}


async def _claim_turn(db: AsyncSession, session: ChatSession) -> bool:
    """Claims this session's turn across replicas via one conditional update; caller commits."""
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
    """Releases the session's turn — only ours, unless `token` is None (stop)."""
    if token is None:
        token = _CLAIMS.pop(session_id, None)
        if token is None:
            # unclaimed turn (retry harness, non-chat surface)
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
    """Keeps only tools with no network egress; admin HTTP endpoints don't qualify."""
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
    """Drops whole tools whose schema has a finding; renaming keys desyncs the registry."""
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
    """Routing metadata updated from LiteLLM's provider-reported id."""
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


def _document_image_model(user: User, catalogue: dict, *, strict_local: bool) -> dict | None:
    """Figure model: strict-local turns take the person's strict-local pick; else the default."""
    usable = _allowed_models(user, catalogue["models"], kind="image")
    if strict_local:
        inside = sorted((m for m in usable if _strict_model(m)), key=model_service.fallback_order)
        return model_service.strict_image_model(inside, Preferences.of(user).strict_image_model)
    return model_service.pick_default(usable, catalogue, "image")


def _auto_image_model(user: User, catalogue: dict) -> dict | None:
    """A self-hosted image model this account may use: documents then draw their own pictures
    with it, as they draw diagrams (`illustrations`); none leaves pictures to the figure card."""
    usable = _allowed_models(user, catalogue["models"], kind="image")
    inside = sorted((m for m in usable if illustrations.self_hosted(m)),
                    key=model_service.fallback_order)
    return model_service.strict_image_model(inside, Preferences.of(user).strict_image_model)


def _allowed_models(user: User, catalogue: list[dict], *, kind: str) -> list[dict]:
    """Models allowed for this account and surface; empty allowlist = whole catalogue."""
    allowed = set(user.allowed_models or [])
    return [
        model
        for model in catalogue
        if kind in model.get("kinds", []) and (not allowed or model.get("id") in allowed)
    ]


@dataclass(slots=True)
class _MediaBoundary:
    """Where a picture, chart or figure call may go, and the text it may carry."""

    strict: bool
    text: str
    #: "" | strict_session | internal_data | mask_external | personal_data
    reason: str
    findings: list[governance.Finding]
    legacy_mask: bool = False

    def carry(self, text: str) -> str:
        """Another field of the same call, masked the same way."""
        if self.reason != "mask_external" or not text:
            return text
        masker = governance.mask_legacy if self.legacy_mask else governance.mask
        return masker(text)[0]


async def _media_boundary(
    db: DbSession, session: ChatSession, catalogue: list[dict], text: str
) -> _MediaBoundary:
    """The chat turn's egress rules for media calls, without the decision dialog."""
    policy = await _require_egress_policy()
    current = model_service.find(catalogue, session.model or "")
    if current is not None and _strict_model(current):
        return _MediaBoundary(True, text, "strict_session", [])
    if policy.internal_data_strict_local and _internal_data_present(
        session, None, None, await _history(db, session.id)
    ):
        return _MediaBoundary(True, text, "internal_data", [])
    rows = (
        governance.findings({"prompt": text}, legacy=policy.pii_masking)
        if (policy.external_data_guard or policy.pii_masking)
        else []
    )
    if not rows:
        return _MediaBoundary(False, text, "", [])
    # no dialog on media calls: masked, as for reports and decks
    masked = _MediaBoundary(False, text, "mask_external", rows, policy.pii_masking)
    masked.text = masked.carry(text)
    return masked


async def _edit_boundary(
    db: DbSession,
    session: ChatSession | None,
    catalogue: list[dict],
    text: str,
    *,
    own_file: bool = False,
) -> _MediaBoundary:
    """A document edit's egress: a media call's, but personal data stays on strict-local.

    The edit is written back into the document, so masking would leave placeholders
    in it. `own_file`: the text is the person's uploaded file, internal material.
    """
    if session is not None:
        boundary = await _media_boundary(db, session, catalogue, text)
    else:
        policy = await _require_egress_policy()
        if own_file and policy.internal_data_strict_local:
            return _MediaBoundary(True, text, "internal_data", [])
        rows = (
            governance.findings({"prompt": text}, legacy=policy.pii_masking)
            if (policy.external_data_guard or policy.pii_masking)
            else []
        )
        boundary = _MediaBoundary(False, text, "mask_external" if rows else "", rows)
    if boundary.reason == "mask_external":
        return _MediaBoundary(True, text, "personal_data", boundary.findings)
    return boundary


def _media_model(
    user: User,
    catalogue: list[dict],
    *,
    kind: str,
    boundary: _MediaBoundary,
    asked: str = "",
    default: str | None = None,
    defaults: tuple[str, ...] = (),
) -> dict | None:
    """Asked model, else the admin's `defaults` (`configured_defaults`); allowed only,
    strict-local only when required. Never the cheapest: past the defaults a chat surface takes
    the first allowed model, and a picture only when its default is outside the allowlist.

    Kept strict-local, a picture asked on an outside model goes to `strict_image_model`, the rule
    the settings screen shows.
    """
    if default:
        defaults = (default, *defaults)
    usable = sorted(_allowed_models(user, catalogue, kind=kind), key=model_service.fallback_order)
    if boundary.strict:
        usable = [m for m in usable if _strict_model(m)]
        if kind == "image":
            return model_service.find(usable, asked or "") or model_service.strict_image_model(
                usable, Preferences.of(user).strict_image_model
            )
        # a default's strict-local entry, as an asked `local/` model's
        defaults = tuple(
            "strict-local/" + wanted.removeprefix("local/") if wanted.startswith("local/")
            else wanted
            for wanted in defaults
        )
    for wanted in (asked, *defaults):
        found = model_service.find(usable, wanted or "")
        if found is not None:
            return found
    served = any(model_service.find(catalogue, wanted) for wanted in defaults)
    if kind not in model_service.CHAT_SURFACES and not served:
        return None
    return usable[0] if usable else None


def _refuse_unallowed(user: User, catalogue: list[dict], asked: str | None) -> None:
    """An explicitly asked model outside the account's allowlist is refused, not swapped."""
    allowed = set(user.allowed_models or [])
    if asked and allowed and asked not in allowed and model_service.find(catalogue, asked):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="model_not_allowed")


def _refuse_outside_boundary(boundary: _MediaBoundary, kind: str) -> HTTPException:
    """No model for this call: strict-local missing, or nothing allowed at all."""
    if boundary.strict:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="internal_data_requires_strict_local"
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"image": "no_image_models", "av": "no_video_models"}.get(kind, "no_chat_models"),
    )


async def _refuse_blocked(user: User, request: Request, policy, text: str) -> None:
    """Forbidden categories, before any write, key or model call."""
    if policy.intent_filter and (hit := governance.blocked_by(text, policy.blocked_categories)):
        await _audit_policy(user, request, "filter.blocked", hit)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"blocked_category:{hit}",
        )


async def _media_gate(
    db: DbSession, user: User, request: Request, session: ChatSession, catalogue: list[dict],
    text: str, *, asked: str | None = None,
) -> _MediaBoundary:
    """A media call's pre-call policy, as a chat turn's: forbidden categories, an asked model
    on the allowlist, then the data boundary (strict-local or masked text)."""
    await _refuse_blocked(user, request, await _require_egress_policy(), text)
    _refuse_unallowed(user, catalogue, asked)
    return await _media_boundary(db, session, catalogue, text)


async def _audit_media_boundary(
    user: User, request: Request, session: ChatSession | None, boundary: _MediaBoundary, *,
    refused: bool = False, surface: str = "media",
) -> None:
    """Media and edit calls kept inside, masked or refused leave the chat turn's trail."""
    if not boundary.reason or (boundary.reason == "strict_session" and not refused):
        return
    action = (
        "internal_data_requires_strict_local" if refused
        else "strict_local" if boundary.reason == "personal_data"
        else "internal_strict_local" if boundary.strict else boundary.reason
    )
    await _audit_policy(
        user,
        request,
        f"privacy.{action}",
        governance.DETECTOR_VERSION,
        metadata={
            "sessionId": session.id if session is not None else None,
            "surface": surface,
            "reason": boundary.reason,
            **governance.finding_metadata(boundary.findings),
        },
    )


_widens_boundary = adaptive_routing.widens_boundary

_AUTO_MODES = frozenset({RoutingMode.auto, RoutingMode.auto_quality})
#: Surfaces whose writer an Auto lane may choose (`_document_route`).
_AUTO_KINDS = frozenset({SessionKind.chat, SessionKind.report, SessionKind.slides})
#: kinds whose picked skills stay on for later turns (a plan's approval included)
_STANDING_KINDS = frozenset({SessionKind.chat, SessionKind.report, SessionKind.slides})


def _planner_model(
    wanted: str | None,
    *,
    user: User,
    catalogue: list[dict],
    kind: str,
    writer: dict,
    strict_local: bool,
) -> dict | None:
    """Outline-call catalogue row, or `None` to use the writer."""
    if not wanted:
        return None
    wanted = str(wanted)
    # strict-local route: same model's strict-local entry plans
    allowed = _allowed_models(user, catalogue, kind=kind)
    planner = None
    if strict_local and wanted.startswith("local/"):
        planner = model_service.find(allowed, "strict-local/" + wanted.removeprefix("local/"))
    planner = planner or model_service.find(allowed, wanted)
    # strict-local: self-hosted planner only; internal material never leaves
    if strict_local and (planner is None or planner.get("dataBoundary") != "self_hosted"):
        return None
    if planner is None or _widens_boundary(planner, writer):
        log.info("outline model %s unusable here", wanted)
        return None
    return planner


def _document_role(roles: settings_store.ModelRoles) -> str:
    """The model that plans and judges when the governance row names none: the report
    writer's (27B by default), not the chat default, which is the faster model."""
    return roles.report or roles.chat


def _local_planner(
    *, user: User, catalogue: list[dict], kind: str, writer: dict, strict_local: bool
) -> dict | None:
    """Default local model as judge for an external writer; `None` if self-hosted."""
    if strict_local or writer.get("dataBoundary") in ("self_hosted", "hybrid"):
        return None
    return _planner_model(
        _document_role(settings_store.cached_model_roles()), user=user, catalogue=catalogue,
        kind=kind,
        writer=writer, strict_local=strict_local,
    )


async def _rescue_model(
    *, user: User, catalogue: list[dict], writer: dict, strict_local: bool,
    outline: dict | None,
) -> dict | None:
    """Rescue writer for an empty or announced-only answer: the other self-hosted model.

    Read from the model roles (chat, report, slides, code fallback), else the outline model;
    `None` when there is none. An external writer gets the chat model."""
    roles = await settings_store.model_roles()
    if not str(writer.get("id") or "").startswith(("local/", "strict-local/")):
        if strict_local or not roles.chat or roles.chat == writer.get("id"):
            return None
        return _planner_model(roles.chat, user=user, catalogue=catalogue, kind="chat",
                              writer=writer, strict_local=strict_local)

    def bare(model_id: str) -> str:
        return model_id.split("/", 1)[-1]

    for wanted in (roles.chat, roles.report, roles.slides, roles.code_fallback,
                   (outline or {}).get("id")):
        if not wanted or not str(wanted).startswith(("local/", "strict-local/")) or (
                bare(str(wanted)) == bare(str(writer["id"]))):
            continue
        found = _planner_model(wanted, user=user, catalogue=catalogue, kind="chat",
                               writer=writer, strict_local=strict_local)
        if found is not None and found.get("dataBoundary") == "self_hosted":
            return found
    return None


async def _enrichment_model(writer: dict, *, strict_local: bool, disable_fallbacks: bool) -> dict:
    """Catalogue row for titles and memory extraction; writer when `title_model` unset.
    Strict-local: the chosen model's `strict-local/` twin, else the writer."""
    if disable_fallbacks:
        return writer
    resolved = await model_service.resolve_enrichment_model()
    if strict_local and resolved.startswith("local/"):
        resolved = "strict-local/" + resolved.split("/", 1)[1]
    elif strict_local and not resolved.startswith("strict-local/"):
        return writer
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
    """One turn's routing decision; wire key `costRouting` for both lanes."""
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
    # stored as plain string, compared by value
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
    """Auto turn's effective model and value-free route metadata; refusal keeps the model."""
    lane = str(getattr(mode, "value", mode))
    upgrading = lane == RoutingMode.auto_quality.value
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
            requires_tools=bool(classifier_tool_definitions),
        )
    else:
        candidates = adaptive_routing.economy_candidates(
            catalogue,
            list(policy.adaptive_economy_model_ids or [])[:3],
            quality_model=quality_model,
            allowed_model_ids=allowed,
            context_tokens=context_tokens,
            # economy turns tool-free, so preflight context fit holds
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

    # not `credentials_for` (may fall back to master key); classifier needs user's own key
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
    """Routing metadata for a non-privacy fallback (report, slides)."""
    effective_id = effective["id"]
    boundary = effective.get("dataBoundary") or "unknown"
    return {
        "requestedModels": [requested["id"]],
        "routedModels": [effective_id],
        "effectiveModels": [effective_id],
        # document runners: no provider-reported id
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
    """Whether this turn handles internal material; once true for a session, stays true."""
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
    """Whether privacy routing could go strict-local (in-boundary tools needed first)."""
    if _strict_model(model):
        return True
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
    """`strict-local/` twin of a `local/` model (no external fallback), if listed and allowed."""
    model_id = str(model.get("id") or "")
    if not model_id.startswith("local/"):
        return None
    twin_id = "strict-local/" + model_id.split("/", 1)[1]
    twin = next((m for m in catalogue if m.get("id") == twin_id), None)
    if twin is None or not _strict_model(twin) or "chat" not in twin.get("kinds", []):
        return None
    return twin if not allowed or twin_id in allowed else None


def _planned_web_search(history: list[Message]) -> bool | str:
    """Web-search setting of the latest question that recorded one; off if none."""
    for message in reversed(history):
        if message.role is not Role.user:
            continue
        options = (message.routing or {}).get("turnOptions") or {}
        if "webSearch" in options:
            value = options["webSearch"]
            return value if value in (True, "auto") else False
    return False


def _brings_material(content: str) -> bool:
    """Whether a request carries its pasted document, so the writer proceeds without asking."""
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
    """Report/deck writer, strict-local flag, route note; internal → strict-local twin."""
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
    summary: str | None = None,
) -> dict[str, str | list[str]]:
    sources: dict[str, str | list[str]] = {
        "current_input": content,
        # the cached summary goes out with the turn: scanned under today's policy
        "conversation_history": [message.content for message in history]
        + ([summary] if summary else []),
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
    """One egress decision before persistence, billing or upstream; unknown = external."""
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
    # priority = catalogue order (admin screen order)
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
        # internal material: strict-local twin, else first privacy-safe model, else refusal
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
        # org-wide masking always wins
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

        # request-supplied choice = retry; must match user, session, models, envelope
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
    """Value-free fields from a privacy 409 contract."""
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
    """Raw values egress masking removes from the user's side of the envelope."""
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
    """Detached tree with every persisted string sanitized."""
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
    # same answer for missing/foreign; bot conversations only via `routers/publications`
    if session is None or session.user_id != user.id or session.publication_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session_not_found")
    return session


#: Visitor's turn on a published bot, set by `routers/publications` around `send_message`.
_PUBLISHED: ContextVar[publishing.PublishedTurn | None] = ContextVar("published_turn",
                                                                    default=None)


async def _visited(db: DbSession, user: User, session_id: str,
                   turn: publishing.PublishedTurn) -> ChatSession:
    """Bot conversation for a visitor's turn: publisher's row, this publication, this visitor."""
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
            # literal 422: Starlette constant name varies by version
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
        # structured fields for audit
        "skills": list(event.get("skills") or []),
        "estimatedTokens": int(event.get("estimatedTokens") or 0),
        "detail": (" · ".join(names) + f" · 약 {int(event.get('estimatedTokens') or 0):,} 토큰"),
    }


#: Per-file detail line by `ContextFile.state`.
_FILE_NOTE = {
    "truncated": "{name} {kept:,}자만 반영",
    "omitted": "{name} 분량을 넘겨 제외",
    "unreadable": "{name} 읽지 못함",
    "picture_unseen": "{name} 그림 · 이 모델은 보지 못함",
    "reading": "{name} 글자 읽는 중",
}

_NAMES_SHOWN = 6


def _named(notes: list[str], unit: str) -> str:
    """Detail line naming some files and counting the rest."""
    shown = notes[:_NAMES_SHOWN]
    line = " · ".join(shown)
    if len(notes) > len(shown):
        line += f" 외 {len(notes) - len(shown)}{unit}"
    return line


def _file_context_step(step_id: str, subject: str, files: tuple[ContextFile, ...]) -> dict | None:
    if not files:
        return None
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
        # structured, for audit
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
        # names only, never bodies
        "memories": names,
        # client re-renders in reader's language
        "totalMemories": workspace.total_memories,
    }


def _personal_context_step(workspace: WorkspaceContext) -> dict | None:
    """One line: which personal settings shaped the turn, never the text."""
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
    """Timeline lines for silently supplied context: memories, attachments, knowledge."""
    steps = [
        _personal_context_step(workspace),
        _memory_context_step(workspace),
        _file_context_step("context-attachments", "첨부", workspace.attachments),
        _file_context_step("context-earlier", "이전 첨부", workspace.carried),
        _file_context_step("context-knowledge", "프로젝트 지식", workspace.knowledge),
    ]
    return [step for step in steps if step]


def _answer_in_flight(last: Message) -> bool:
    """Whether another replica is likely still writing this answer (stall window)."""
    if last.role is not Role.user or last.failure is not None or last.created_at is None:
        return False
    asked = last.created_at
    if asked.tzinfo is None:
        asked = asked.replace(tzinfo=UTC)
    return (utcnow() - asked).total_seconds() < settings.chat_stall_sec


#: Question shape: 「얼마」, 「몇」, 「누구」, 「언제」, "?".
_ASKS = re.compile(
    r"얼마|몇\s{0,2}[가-힣]|누구|언제|어디|인가요|나요|\?|？"
    r"|\b(?:what|who|when|how\s+much)\b", re.I)
#: Ask to write/arrange/proofread what follows: 「안건 초안을 잡아 줘」, 「교정자가 되어 줘 …
#: 글을 주면」 (its pasted 「마감일이 … 변경」 is the text to fix, not a present fact).
_DRAFTS = re.compile(
    r"초안|써\s{0,2}줘|작성|정리|잡아\s{0,2}줘|만들어|요약|다듬|"
    r"교정|교열|첨삭|윤문|퇴고|맞춤법|띄어쓰기|문법|고쳐\s{0,2}(?:줘|주세요)|번역|proofread|translate",
    re.I,
)


def _asks_about_now(instruction: str) -> bool:
    """Whether the instruction asks a present-state question (「지금 주가랑」)."""
    return freshness.names_the_present(instruction) and (
        freshness.current_fact_required(instruction) or bool(_ASKS.search(instruction)))


def _reads_pasted_material(content: str) -> bool:
    """Request about pasted material, whose dates/prices are as stated, not about now."""
    from app.services.context import instruction_part, pasted_material

    if pasted_material(content):
        return not _asks_about_now(instruction_part(content))
    head = (content or "").partition("\n\n")[0]
    if "\n\n" not in (content or ""):
        head = (content or "").partition("\n")[0]
    return _memo_pasted(content) and (
        (bool(_POINTS_AT_MEMO.search(head)) and not _NOW_WORD.search(head))
        or not _asks_about_now(head))


#: an ask pointing at the memo below it: 「이번 업데이트 내용으로」, 「아래 목록대로」
_POINTS_AT_MEMO = re.compile(
    r"(?:이|이번|아래|다음|위)\s{0,2}(?:[가-힣]{1,8}\s{0,2})?(?:내용|목록|자료|메모|기록)"
    r"(?:으로|을|를|대로|에서|만)")
_NOW_WORD = re.compile(r"지금|현재|오늘|최신|요즘|실시간")


#: a memo line: a list item or a labelled line (「- fix(docs): …」, 「상황: …」)
_MEMO_LINE = re.compile(r"\s{0,8}(?:[-*•·]|\d{1,2}[.)]|\(|[가-힣A-Za-z ]{1,20}[:：])\s{0,4}\S")


#: a line about the person themselves: 「- 나이: 34세 / 성별: 여성」, 「키 165cm, 몸무게 72kg」
_PROFILE_LINE = re.compile(
    r"나이|성별|키\s{0,2}\d|신장|몸무게|체중|혈액형|직업|목표|생활\s{0,2}(?:습관|패턴)|운동\s{0,2}(?:경험|제약|습관|수준)"
    r"|식(?:단|습관)|수면|병력|질환|복용|알레르기|체력|부상|통증|가족\s{0,2}(?:구성|력)|흡연|음주|활동량")
#: the label over such a list: 「고객 정보:」, 「내 정보」, 「프로필」
_PROFILE_HEAD = re.compile(
    r"(?:고객|회원|환자|본인|내|제|나의?|대상자?)\s{0,2}(?:정보|프로필|상황|상태)|프로필")


def _profile(rest: str) -> bool:
    """Whether the pasted part is a profile of the person (or their client), not a memo.

    A short labelled list most of whose lines describe the person; tracing it as material
    would turn advice built on it into values to source."""
    lines = [line for line in (rest or "").splitlines() if line.strip()]
    if not 3 <= len(lines) <= 30 or len(rest) > 2000:
        return False
    about = sum(1 for line in lines if _PROFILE_LINE.search(line))
    return bool(_PROFILE_HEAD.search(lines[0])) and about >= 0.4 * len(lines) or (
        about >= 0.6 * len(lines))


#: an ask that works on the pasted text itself (summarise, report, translate, proofread)
_WORKS_ON_TEXT = re.compile(
    r"정리|요약|보고\s{0,2}(?:용|서)|번역|교정|윤문|다듬|옮겨|바꿔|작성|써\s{0,2}줘|만들어|발표문|보도"
    r"|맞춤법|띄어쓰기|어투|문체|군더더기|오탈자")


def _ask_of(content: str) -> str:
    """The ask that decides a turn's forced search and advice reading: the instruction alone
    when it works on text the person pasted under it (a 「재정 진단」 in a pasted press release
    asked to be summarised makes no medical question); else the whole message
    (lab results pasted under 「봐 줘」 stay a medical question)."""
    from app.services.context import lead_instruction, pasted_material

    if edited := report_service.edit_ask(content):
        return edited
    if pasted_material(content) or _memo_pasted(content):
        ask = lead_instruction(content)
        if ask and _WORKS_ON_TEXT.search(ask):
            return ask
    return content


def _memo_pasted(content: str) -> bool:
    """A short ask, a blank line, then a memo or document of the person's (a profile of the
    person is neither)."""
    head, gap, rest = (content or "").partition("\n\n")
    if gap and _profile(rest):
        return False
    if not gap:
        # 「…써 줘.\n(변경 목록)\n- …\n- …」: the ask's line, then a listed memo, no blank line
        head, _, rest = (content or "").partition("\n")
        lines = [line for line in rest.splitlines() if line.strip()]
        listed = sum(1 for line in lines if _MEMO_LINE.match(line))
        return (len(head) <= 300 and len(rest.strip()) >= 120 and len(lines) >= 4
                and listed >= 0.6 * len(lines) and bool(_DRAFTS.search(head)))
    lines = [line for line in rest.splitlines() if line.strip()]
    looks_pasted = len(rest.strip()) >= 120 and (
        len(lines) >= 3 or bool(re.match(r"[\[#|「<]", rest.lstrip()))
        or bool(_DRAFTS.search(head)))
    return len(head) <= 300 and looks_pasted


#: Context blocks holding the person's own material (files, project knowledge).
_MATERIAL_BLOCKS = frozenset({"attachment", "attachment.earlier", "project.knowledge"})


def _carries_material(workspace: WorkspaceContext) -> bool:
    """Whether the turn reads attached, carried or project-file text."""
    return any(block.source in _MATERIAL_BLOCKS and block.text.strip()
               for block in workspace.blocks)


def _pasted_in(turns: list[dict]) -> bool:
    """Whether the person pasted material into the conversation (`turns`: theirs, no envelope)."""
    from app.services.context import pasted_material

    return any(pasted_material(text) or _memo_pasted(text)
               for one in turns if one.get("role") == "user"
               for text in (_material_text([one]),))


def _traced(keys: tuple[str, ...], material: bool) -> tuple[str, ...]:
    """Check skills with the grounding check added on the person's material."""
    from app.pipeline.check_stages import TRACE

    return (*keys, TRACE) if material and TRACE not in keys else tuple(keys)


def _advice_checks(keys: tuple[str, ...], advice: str | None) -> tuple[str, ...]:
    """A legal, tax, financial or medical turn carries the grounding check, as a person's
    agent that turned it on would."""
    from app.pipeline.check_stages import TRACE

    return (*keys, TRACE) if advice and TRACE not in keys else tuple(keys)


def _material_only_turn(session: ChatSession, payload: Any, forced_tool: str | None,
                        content: str, workspace: WorkspaceContext, *, published: bool) -> bool:
    """A chat turn on 「auto」 search whose material the person gives and whose ask names
    nothing to look up (`report.given_material_suffices`): the search tool is not offered.
    A forced first search (a current fact, a hotline, advice) stays."""
    return (session.kind is SessionKind.chat and payload.web_search == "auto"
            and forced_tool is None and not published
            and report_service.given_material_suffices(
                content, has_files=_grounded_in_files(workspace)))


def _grounded_in_files(workspace: WorkspaceContext) -> bool:
    """Whether this turn carries readable file text that reached the model."""
    return any(
        file.state in ("included", "truncated")
        for file in (*workspace.attachments, *workspace.carried, *workspace.knowledge)
    )


#: Words pointing past the files: 「웹에서」, 「인터넷」, 「공식 사이트」.
_NAMES_THE_WEB = re.compile(r"웹|인터넷|온라인|외부\s{0,2}자료|공식\s{0,2}(?:사이트|홈페이지)|링크")


def _answers_from_files(content: str, workspace: WorkspaceContext) -> bool:
    """Whether the carried files are the question's subject (no search/now ask)."""
    return (
        _grounded_in_files(workspace)
        and not requests_web_search(content)
        and not _NAMES_THE_WEB.search(content or "")
        and not freshness.names_the_present(content)
        and not literature_request(content)
    )


def _asked_about(content: str, history: list[Message]) -> str:
    """Text to read a carried file around: this question, then the previous one."""
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
    """`(summary, credits)` for rows past the window, cached on the session; `None` on failure."""
    if not rows or len(rows) != len(turns):
        return None, 0
    cached = dict(session.summary or {})
    through = str(cached.get("through") or "")
    previous = str(cached.get("text") or "") or None
    if previous and through == rows[-1].id:
        # cache may predate policy; apply current masking
        return (masker(previous)[0] if masker else previous), 0
    covered = next((i for i, row in enumerate(rows) if row.id == through), None)
    if previous is None or covered is None:
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


def _setup_step(model_id: str) -> dict:
    """Timeline line: the writer got the light setup (`model_tier.strong`)."""
    return {"id": "setup-light", "type": "thinking", "label": prompt_tier.SETUP_LABEL,
            "status": "done", "detail": f"{prompt_tier.SETUP_DETAIL} · {model_id}"}


def _step_event(step: dict) -> dict:
    """Stored step for the wire: `type` → event name, display category → `category`."""
    return {**step, "type": "step", "category": step["type"]}


def _searches_in(research_log: dict | None) -> int:
    """Queries a document's research step sent; 0 when none."""
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
    """Resolves every requested upload; foreign ids are refused, not dropped."""
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
        # same response for missing and foreign id
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
    """Lists non-empty sessions and recent in-flight ones."""
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
    # pinned first, then most recently touched
    query = query.order_by(col(ChatSession.pinned).desc(), col(ChatSession.updated_at).desc())
    rows = (await db.exec(query)).all()

    ids = [s.id for s in rows]
    previews = await _previews(db, ids)
    # absent from `_previews` = no messages
    rows = _worth_listing(rows, {sid for sid in ids if sid not in previews})
    ids = [s.id for s in rows]
    # media answer: artifact, empty body
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


#: Conversations per 대화 기록 page.
HISTORY_PAGE = 50


@router.get("/paged", response_model=SessionPage)
async def page_sessions(
    user: CurrentUser,
    db: DbSession,
    page: int = Query(1, ge=1, le=100_000),
    size: int = Query(HISTORY_PAGE, ge=1, le=200),
):
    """대화 기록 page, newest first; `_worth_listing` rule in SQL for the count."""
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
    """`{session_id: (latest snippet, message count)}`; absent when no messages."""
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
    # DISTINCT ON: newest row per conversation; Postgres-only
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
    """`{session_id: product}` for sessions without message text (pictures, clips)."""
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
    if payload.id and (existing := await db.get(ChatSession, payload.id)) is not None:
        # a retried first send finds its conversation; another account's id is refused
        if existing.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session_id_taken")
        return SessionOut.of(existing, [])
    agent = await db.get(WorkspaceAgent, payload.agent_id) if payload.agent_id else None
    if agent is not None and agent.owner_id != user.id and agent.visibility != AgentVisibility.org:
        agent = None
    # explicit kind wins, else agent's surface
    if "kind" not in payload.model_fields_set and agent is not None and agent.surface:
        payload.kind = SessionKind(agent.surface)
    # server-side refusal, not just hidden menu
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
        # retired: old conversations open, no new ones
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="agent_retired")
    if agent is not None and user.role is not UserRole.admin and (
            agent.catalog_key or "") in starter.draft_agents():
        # in evaluation: admins only
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
        **({"id": payload.id} if payload.id else {}),
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
    """Copies the prefix before a user message, without generation or prior consent."""
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
    # trailing unanswered question also catches a stream on another replica
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

    # bound the whole fork before creating any row or blob
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
                origin=stored.origin,
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


#: Conversation chars carried into a document; tail kept.
_HANDOFF_CHARS = 30_000
#: Conversation files carried along, most recent last.
_HANDOFF_FILES = 8
_HANDOFF_ROLE = {Role.user: "사용자", Role.assistant: "답변"}


def handoff_transcript(messages: list[Message]) -> str:
    """Conversation as plain text for the document writer; oldest turns cut first."""
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
    """Opens a report/slides session from a chat, keeping agent, project and files."""
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
            origin="generated",
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
    """`(context blocks, skills_applied event)` with the template's catalogue skills."""
    if template is None or not template.skills:
        return [], skills_event
    rows = (
        await db.exec(
            select(Skill)
            .where(
                col(Skill.catalog_key).in_(list(template.skills)),
                # the catalogue owner's shared rows, not someone's shared copy
                Skill.owner_id == starter.catalog_owner_query().scalar_subquery(),
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
        # documents written tool-free: tool skills are pipeline checks (`report_checks`)
        if skill.required_tools and kind is not SessionKind.chat:
            continue
        head = f"# 서식 스킬 — {skill.name}"
        body = surface_body(skill.body.strip(), kind) or skill.description.strip()
        blocks.append(f"{head}\n{body}" if body else head)
        applied.append(
            {
                "id": skill.id,
                "name": skill.name,
                "catalogKey": skill.catalog_key,
                "estimatedTokens": skill.estimated_tokens,
                "fromTemplate": True,
            }
        )
    if not applied:
        return blocks, skills_event
    event = dict(skills_event or {"type": "skills_applied", "skills": []})
    event["skills"] = list(event.get("skills") or []) + applied
    # browser requires `estimatedTokens`
    event["estimatedTokens"] = sum(
        int(skill.get("estimatedTokens") or 0) for skill in event["skills"]
    )
    return blocks, event


async def _project_render_template(
    db: DbSession, project_id: str | None, kind: SessionKind
) -> str | None:
    """Project's default template for this kind; copied so later project changes don't apply."""
    if not project_id:
        return None
    project = await db.get(Project, project_id)
    return design_templates.default_for(project.render_templates, kind) if project else None


async def _agent_template_loaded(db: DbSession, user: User, agent: WorkspaceAgent | None,
                                 kind: SessionKind) -> str | None:
    """`agent_template` with a person's template first, when usable by the caller."""
    if agent is not None and agent.render_template_id and not await user_templates.usable(
            db, user.id, agent.render_template_id):
        return None
    return agent_template(agent, kind)


def agent_template(agent: WorkspaceAgent | None, kind: SessionKind) -> str | None:
    """Agent's bound 서식 for this surface, else `None`; beats the project default."""
    if agent is None or not agent.render_template_id:
        return None
    chosen = design_templates.choosable(agent.render_template_id)
    if chosen is None or chosen.kind not in design_templates.HTML_KINDS:
        return None
    return chosen.id if chosen.surface is kind else None


def _resolved_template_id(requested: str | None, kind: SessionKind,
                          keep: str | None = None) -> str | None:
    """Usable rendering template id or `None`; unknown or retired ids refused (except `keep`)."""
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
    """Whether a message is English (more Latin letters than Hangul)."""
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    return latin > 3 * len(re.findall(r"[가-힣]", text or ""))


async def _usable_design(db: DbSession, user: User, design_id: str | None) -> str | None:
    """Bindable design: the named one if own/shared, else own design of same name, else None."""
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
    """`(lines, status, strict)`: what the conversation settled, and how it went."""
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
    # masked unless kept on the strict-local model
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
    """「에이전트로 저장」's 지시 alone (after the rest, and on 「다시 시도」)."""
    from app.services import agent_draft as drafts

    session = await _owned(db, user, session_id)
    lines, state, _ = await _draft_decided(db, user, session)
    return {"decided": lines, "prompt": drafts.prompt_from(lines), "distilled": state}


@router.get("/{session_id}/agent-draft")
async def agent_draft(session_id: str, user: CurrentUser, db: DbSession, distill: bool = True):
    """「에이전트로 저장」: conversation as an unsaved draft agent; sealed prompts never copied."""
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
    # official agent: 지시 from this conversation only; own agent keeps its prompt
    prompt = "\n\n".join(x for x in (
        agent.system_prompt.strip()
        if readable and agent is not None and not agent.catalog_key else "",
        drafts.prompt_from(lines)) if x)
    base = bases.get(key, drafts=is_admin) if key else None
    if base is None and agent is None:
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
    # validated as `POST /agents`: unusable theme → own same-name theme, else none
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
    # model-only patch → manual
    if "model" in changes and "routing_mode" not in changes:
        changes["routing_mode"] = RoutingMode.manual
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
        # set title sticks; clearing returns naming to the document
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
        # uploads move to the new project's collection
        await reindex_session_files(db, user.id, session)
    return SessionOut.of(session)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    user: CurrentUser,
    db: DbSession,
    request: Request = None,  # type: ignore[assignment]
    files: bool = False,
):
    """`files`: the files attached or made in it go too; else they stay in the file box."""
    session = await _owned(db, user, session_id)
    counts = await _conversation_counts(db, [session.id])
    await db.exec(delete(Message).where(Message.session_id == session.id))
    # Job rows: no cascade
    await db.exec(delete(Job).where(Job.session_id == session.id))
    counts["artifacts"] = await _delete_artifacts_of(db, [session.id])
    gone = await delete_session_files(db, [session.id]) if files else 0
    await forget_session_files(db, [session.id])
    if session.index_key:
        await index_client.forget_collection(collection=session.index_key)
    await db.delete(session)
    db.add(audit_service.deletion(
        user, request, action="conversation.delete", ids=[session.id], counts=counts,
        note=_files_note(counts["files"], gone),
    ))
    await db.commit()


#: conversation uploads stay in the file box; only their search entries go
_FILES_KEPT = "첨부 파일은 지우지 않음"


def _files_note(files: int, gone: int) -> str:
    """The audit note on a conversation's files: kept in the file box, or deleted with it."""
    if not files:
        return ""
    return f"파일 {gone}개도 지움" if gone else _FILES_KEPT


async def _conversation_counts(db: DbSession, session_ids: list[str]) -> dict[str, int]:
    """What deleting these conversations takes (messages) or touches (uploads)."""
    messages = (
        await db.exec(
            select(func.count()).select_from(Message)
            .where(col(Message.session_id).in_(session_ids))
        )
    ).one()
    files = (
        await db.exec(
            select(func.count()).select_from(StoredFile)
            .where(col(StoredFile.session_id).in_(session_ids))
        )
    ).one()
    return {"sessions": len(session_ids), "messages": int(messages), "files": int(files)}


async def _delete_artifacts_of(db: DbSession, session_ids: list[str]) -> int:
    """Deletes these conversations' artifacts (versions first); returns the count."""
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
    """Records a picture/clip turn as a user/assistant pair; partial batch keeps what arrived."""
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
        # document surfaces: `artifact_id` is the report or deck
        if session.kind not in (SessionKind.report, SessionKind.slides):
            session.artifact_id = made[-1].id
    session.updated_at = utcnow()
    db.add(session)


@router.post("/{session_id}/figure-suggestion", response_model=FigureSuggestion)
async def suggest_figure(
    session_id: str,
    payload: FigureSuggestRequest,
    user: CurrentUser,
    db: DbSession,
    request: Request,
):
    """Proposes a figure caption and prompt; nothing drawn or charged."""
    session = await _owned(db, user, session_id)
    catalogue = await model_service.list_models()
    said = "\n".join((payload.title or session.title or "", payload.about, payload.context))
    boundary = await _media_boundary(db, session, catalogue["models"], said)
    model = _media_model(
        user, catalogue["models"], kind="chat", boundary=boundary,
        asked=session.model or "", default=catalogue.get("defaultChatModel"),
    )
    if model is None:
        await _audit_media_boundary(user, request, session, boundary, refused=True)
        raise _refuse_outside_boundary(boundary, "chat")
    await _audit_media_boundary(user, request, session, boundary)
    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)
    figure = await figures.suggest(
        title=boundary.carry(payload.title or session.title or ""),
        about=boundary.carry(payload.about),
        context=boundary.carry(payload.context),
        model=str(model["id"]),
        api_key=api_key,
        look=payload.visual_style,
    )
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
async def generate_images(
    session_id: str, payload: ImageRequest, user: CurrentUser, db: DbSession, request: Request
):
    """Makes pictures as artifacts, charged from reported usage."""
    session = await _owned(db, user, session_id)
    catalogue = await model_service.list_models()
    boundary = await _media_gate(db, user, request, session, catalogue["models"],
                                 payload.prompt, asked=payload.model)
    if payload.style == "차트":
        # chart drawn from code: see `chart_code`
        return await _draw_chart(session, payload, user, db, catalogue, boundary, request)
    model = _media_model(
        user, catalogue["models"], kind="image", boundary=boundary,
        asked=payload.model or session.model or "",
        default=(catalogue.get("defaultModelByKind") or {}).get("image"),
    )
    if model is None:
        await _audit_media_boundary(user, request, session, boundary, refused=True)
        if not boundary.strict and _allowed_models(user, catalogue["models"], kind="image"):
            # pictures exist but the admin has not chosen which one draws
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="default_image_model_unset"
            )
        raise _refuse_outside_boundary(boundary, "image")
    await _audit_media_boundary(user, request, session, boundary)
    if not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )

    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    base_url, api_key = await litellm_service.credentials_for(user)
    # retired image 서식 → plain picture; other ids refused
    if payload.template_id and not design_templates.is_retired_media(payload.template_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="design_template_not_found"
        )
    # LM expands the prompt unless an edited one comes back (`raw`)
    planned = boundary.text
    if not payload.raw and payload.style != "없음":
        # same boundary as the picture; none inside it = drawn as asked
        planner = _media_model(
            user, catalogue["models"], kind="chat", boundary=boundary,
            asked=session.model or "", default=catalogue.get("defaultChatModel"),
        )
        if planner is not None:
            planned, _ = await imagegen.plan(
                boundary.text,
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
        figure=payload.figure and planned == boundary.text,
        square_only=not imagegen.honours_aspect(str(model["id"])),
        labels=payload.labels,
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
            # images made before the failure kept and billed
            failure = str(exc)
            break

        file_id, key = imagegen.store(user.id, image)
        db.add(
            StoredFile(
                id=file_id,
                user_id=user.id,
                origin="generated",
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
                # as typed, without appended aspect/style
                "prompt": payload.prompt,
                "aspect": payload.aspect,
                # requested vs delivered ratio may differ
                "actualAspect": image.aspect,
                "width": image.width,
                "height": image.height,
                "style": payload.style,
                "labels": payload.labels,
                # sent to the model; resendable as `raw`
                "composedPrompt": composed,
                "seed": 0,
                "model": model["id"],
                "src": f"{settings.api_prefix}/files/{file_id}/content",
            },
        )
        db.add(artifact)
        made.append(artifact)
        charged += charge_for_image(model, image.input_tokens, image.output_tokens, image.cost_usd)

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
    session: ChatSession,
    payload: ImageRequest,
    user: User,
    db: DbSession,
    catalogue: dict,
    boundary: _MediaBoundary,
    request: Request,
) -> list[ArtifactOut]:
    """차트 style: LM-written matplotlib run in the sandbox; code kept as `composedPrompt`."""
    writer = _media_model(
        user, catalogue["models"], kind="chat", boundary=boundary,
        asked=session.model or "", default=catalogue.get("defaultChatModel"),
    )
    if writer is None:
        await _audit_media_boundary(user, request, session, boundary, refused=True)
        raise _refuse_outside_boundary(boundary, "chat")
    await _audit_media_boundary(user, request, session, boundary)
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
            boundary.text,
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
            origin="generated",
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
async def write_diagram(
    session_id: str,
    payload: DiagramRequest,
    user: CurrentUser,
    db: DbSession,
    request: Request,
):
    """Labelled figure as flowchart source drawn with deck-export shapes; 422 if not drawable."""
    session = await _owned(db, user, session_id)
    catalogue = await model_service.list_models()
    _refuse_unallowed(user, catalogue["models"], payload.model)
    boundary = await _media_boundary(db, session, catalogue["models"], payload.description)
    # `session.model` is a picture model here; chat model needed
    model = _media_model(
        user, catalogue["models"], kind="chat", boundary=boundary,
        asked=payload.model or session.model or "",
    )
    if model is None:
        await _audit_media_boundary(user, request, session, boundary, refused=True)
        if boundary.strict:
            raise _refuse_outside_boundary(boundary, "chat")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
        )
    await _audit_media_boundary(user, request, session, boundary)
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
            description=boundary.text,
            figure=payload.figure,
            model=model["id"],
            api_key=api_key,
            language=payload.language,
        )
    except diagram_service.Undrawable as exc:
        # undrawable or no renderer: no picture, no browser fallback; writer tokens still charged
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
    """PNG width and height from its header; zeros if not a PNG."""
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
    """Drawn figure as image artifact with mermaid source; added, not committed."""
    file_id = uuid.uuid4().hex
    key = file_service.write_blob(user.id, file_id, "figure.png", png)
    title = (title or caption or "도식")[:200]
    db.add(
        StoredFile(
            id=file_id,
            user_id=user.id,
            origin="generated",
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
            # source is the artifact; PNG one rendering
            "figure": figure,
            "source": source,
            "caption": caption,
            **({"renderer": renderer} if renderer else {}),
        },
    )
    db.add(artifact)
    _record_media(db, session, description, [artifact], model=model, credits=0)
    return artifact


@router.post("/{session_id}/audio", response_model=ArtifactOut)
async def generate_audio(session_id: str, payload: AudioRequest, user: CurrentUser, db: DbSession):
    """Makes one sound clip (speech or music) as an artifact."""
    session = await _owned(db, user, session_id)
    speech = payload.audio_kind == "narration"
    catalogue = await model_service.list_models()

    def _audio_models():
        return [m for m in catalogue["models"] if "av" in m["kinds"]]

    # asked, else the admin's audio default; none chosen: no silent pick
    default = (catalogue.get("defaultAvModelByMode") or {}).get("audio") or ""
    if not payload.model and not default:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="default_audio_model_unset"
        )
    model = model_service.find(catalogue["models"], payload.model or default)
    # model must match kind; mismatch = proxy 400
    wanted_id = "gpt-audio" if speech else "lyria"
    if model is None or "av" not in model["kinds"] or wanted_id not in model["id"]:
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
        # prompt recorded even on failure
        _record_media(db, session, payload.prompt, [], failed=True)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    file_id, key = audiogen.store(user.id, audio)
    db.add(
        StoredFile(
            id=file_id,
            user_id=user.id,
            origin="generated",
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
            "requestedSec": payload.seconds,
            "durationSec": audiogen.duration_seconds(audio),
            "model": model["id"],
            "transcript": audio.transcript,
            # flat placeholder; real waveform needs decoding
            "waveform": [],
            "src": f"{settings.api_prefix}/files/{file_id}/content",
        },
    )
    db.add(artifact)

    # Lyria: flat $0.04/song, 4 reported tokens; token billing reads as free
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
async def delete_sessions(
    payload: SessionBulkDelete,
    user: CurrentUser,
    db: DbSession,
    request: Request = None,  # type: ignore[assignment]
):
    """Deletes many conversations; `all` separate from an id list. One audit event."""
    query = select(ChatSession).where(owned_by(user.id))
    if not payload.all:
        if not payload.ids:
            return {"deleted": 0}
        query = query.where(col(ChatSession.id).in_(payload.ids))
    rows = (await db.exec(query)).all()
    if not rows:
        return {"deleted": 0}

    ids = [row.id for row in rows]
    counts = await _conversation_counts(db, ids)
    await db.exec(delete(Message).where(col(Message.session_id).in_(ids)))
    # Job rows: no cascade
    await db.exec(delete(Job).where(col(Job.session_id).in_(ids)))

    artifacts_deleted = await _delete_artifacts_of(db, ids)
    counts["artifacts"] = artifacts_deleted
    gone = await delete_session_files(db, ids) if payload.files else 0
    await forget_session_files(db, ids)
    for row in rows:
        if row.index_key:
            await index_client.forget_collection(collection=row.index_key)

    await db.exec(delete(ChatSession).where(col(ChatSession.id).in_(ids)))
    db.add(audit_service.deletion(
        user, request, action="conversation.bulk_delete", ids=ids, counts=counts,
        everything=payload.all, note=_files_note(counts["files"], gone),
    ))
    await db.commit()
    return {"deleted": len(ids), "artifactsDeleted": artifacts_deleted, "filesDeleted": gone}


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def list_messages(session_id: str, user: CurrentUser, db: DbSession):
    await _owned(db, user, session_id)
    links = await message_links.run_links(db, session_id)
    return [message_links.linked(m, links) for m in await _history(db, session_id)]


def _earlier_answer(row: Message) -> dict[str, Any]:
    """Answer fields carried by its replacement row: words, steps, artifacts, route."""
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
    """Version label from the regeneration request."""
    # first line only; `merge_answers` conditions are noise
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
    """Same-kind regeneration → version; new kind → new row."""
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
    #: figure question, asked after outline approval
    figures: dict | None = None,
    #: restructure source whose untouched parts carry over (`revise.carry_parts`)
    carry_from: str | None = None,
    #: restructure instruction; parts it names written fresh
    carry_note: str = "",
    #: planning stage records (`_run_summary`)
    run: dict | None = None,
    #: run a card waited on, carried to the writing pass
    run_parent: str | None = None,
) -> None:
    """Stores a planning/asking turn and charges for it; no artifact."""
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
            surface=session.kind.value,
        )
        session.updated_at = utcnow()
        db.add(session)
        await db.commit()


#: Above any real outline size; rejects bad payloads only.
_MAX_PLANNED = 60


def _edited_plan(sent: dict | None, stored: dict) -> dict:
    """Stored outline with the person's edits: titles/order from browser, rest from proposal."""
    if not isinstance(sent, dict):
        return stored
    out = dict(stored)
    for key in ("sections", "slides", "blocks"):
        items = sent.get(key)
        if key not in stored or not isinstance(items, list) or not items:
            continue
        if len(items) > _MAX_PLANNED:
            continue
        # layouts must come from the proposal
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
    """`request` with one more 「덧붙인 조건」 line, opening the block if needed."""
    if line in request:
        return request
    if "\n\n덧붙인 조건:\n" in request:
        return f"{request}\n- {line}"
    return grounding.merge_answers(request, {"_server": line})


async def _agent_catalog_key(db: DbSession, agent: WorkspaceAgent | None) -> str | None:
    """Shipped agent whose process `agent` runs: itself, its `base_key` base, or its copy origin."""
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
    """Server-run specs for the turn's skills and own scripts (`skill_specs.for_turn`)."""
    return await skill_specs.for_turn(
        db, user.id, agent_checks=agent.check_skills if agent is not None else None,
        agent_owner=agent.owner_id if agent is not None else "")


_LINK = re.compile(r"https?://[^\s<>()\[\]「」\"']+")


def _source_call(applied, tools: list[Tool], content: str) -> tuple[str, dict[str, Any]] | None:
    """`(tool, {param: link})` for a connector tool an applied skill declares, when this message
    gives a link and the tool's only required input is a URL; None otherwise."""
    link = _LINK.search(content or "")
    if link is None:
        return None
    by_name = {tool.name: tool for tool in tools}
    for skill in applied:
        for name in getattr(skill, "required_tools", ()) or ():
            tool = by_name.get(name)
            if tool is None or not name.startswith("mcp__") or tool.read_only is not True:
                continue
            required = list((tool.parameters or {}).get("required") or [])
            if len(required) == 1 and "url" in str(required[0]).lower():
                return name, {required[0]: link.group(0).rstrip(".,;:!?)」")}
    return None


def _skill_keys(applied, specs: dict[str, skill_specs.Spec]) -> tuple[str, ...]:
    """Turn's skills keyed for the checks: catalogue key, or `skill:<id>`."""
    out = []
    for skill in applied:
        if skill.catalog_key:
            out.append(skill.catalog_key)
        elif (key := f"{skill_specs.USER}{skill.id}") in specs:
            out.append(key)
    return tuple(out)


def _deep_search(keys, specs: dict | None) -> bool:
    """Whether a skill on the turn searches deep (its 진행 과정 설정 「깊이 검색」)."""
    # a shipped skill with no stored fields reads its package's (`skill_specs.resolve`)
    return any(bool((spec := skill_specs.resolve(key, specs)) is not None
                    and spec.process.get("deep_search")) for key in keys)


def render_template_checks(session: ChatSession) -> tuple[str, ...]:
    """Check skills attached by the session's 서식."""
    template = design_templates.get(session.render_template_id)
    return template.check_skills if template is not None else ()


def _gap_judge_model(user: User, catalogue_models: list[dict],
                     requested: dict | None, planner: str | None = None) -> str:
    """Judge model for 빠진 정보 질문 (strict-local only); "" if none."""
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
    """Persists a clarification card without generating; keeps the `pre` run."""
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
        # read by `_last_card`: no repeat card
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
    # sentence keeps the in-flight bubble non-empty
    return JSONResponse({
        "pending": session.pending, "message": said, "runId": run_id,
        **({"stage": {**waiting.stage_wire(), "runId": run_id,
                      "id": f"{run_id}:{waiting.stage}:{waiting.attempt}"}}
           if waiting is not None else {}),
    })


def _waiting_recorder(pre: PipelineBundle, run_id: str) -> pipeline_runs.Recorder:
    """Recorder for a kept, never-rerun `pre` run: records, no snapshots."""
    recorder = pipeline_runs.Recorder(run_id=run_id)
    for record in pre.steps:
        record.run_id = run_id
    return recorder


async def _close_waiting_run(run_id: str, *, answers: dict[str, str], as_is: bool,
                             blanks: int) -> None:
    """Marks the card's `gap-card` record done (best effort)."""
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
    """`pre` pipeline flow (`check:gaps`, `gap-card`); bundle with gaps and questions."""
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
    """Question ids of the last answer's card, if over the same files."""
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
    """Keeps search consent for a short same-topic nudge, server-policy holds included."""
    if not freshness.is_same_fact_followup(content):
        return None
    for index in range(len(history) - 2, -1, -2):
        question, answer = history[index : index + 2]
        routing = answer.routing or {}
        verification = routing.get("freshness") or {}
        grounded = (routing.get("accuracy") or {}).get("policy") == freshness.ACCURACY_POLICY
        policy_hold = (
            answer.model is None
            and routing.get("answerOrigin") == "server_policy"
            and routing.get("actualModel") is None
            and verification.get("status") == "unverified"
        )
        if (
            question.session_id != session_id or answer.session_id != session_id
            or question.role is not Role.user or answer.role is not Role.assistant
            or not (grounded or policy_hold)
        ):
            return None
        if not freshness.is_same_fact_followup(question.content) and (
            grounded or freshness.fresh_fact_required(question.content)
        ):
            return index
        if not freshness.is_same_fact_followup(question.content):
            return None
    return None


#: Agent turn whose material the server searched first.
_MATERIAL_FIRST_RULE = (
    "Material first: the server has already searched this agent's attached material with the "
    "question, and the passages are the first search_knowledge result, numbered like [1]. "
    "Where they answer the question, answer from them and put the passage number at the end "
    "of each sentence that uses one, like [1]. If they do not hold the answer, search again "
    "with other words; what the material does not cover, say so instead of filling it in "
    "from memory."
)


_GIVEN_LINKS_RULE = (
    "Links given: the person put links in this message and the server has already read them "
    "(the first fetch_url result, numbered like [1]). Answer about those links from what was "
    "read and cite them. A link that could not be read is named as unread, not as missing; "
    "never say a link the person gave did not appear in your search results. Search the web "
    "after that only for what the links leave open."
)


def given_links_in(content: str) -> list[str]:
    """Links in the person's message, in order, at most `MAX_GIVEN_LINKS`."""
    links = [m.rstrip(".,;:!?)」") for m in _LINK.findall(content or "")]
    return list(dict.fromkeys(link for link in links if "://" in link))[: MAX_GIVEN_LINKS]


def _with_system_rule(messages: list[dict], rule: str) -> list[dict]:
    """`messages` with `rule` appended to the system turn (copy; envelope shared)."""
    if messages and messages[0].get("role") == "system":
        head = {**messages[0], "content": f"{messages[0].get('content') or ''}\n\n{rule}"}
        return [head, *messages[1:]]
    return [{"role": "system", "content": rule}, *messages]


def _agent_web_search(agent: WorkspaceAgent | None) -> bool | str:
    """Toggle for a turn that sent none: the agent's `web_search_default`, else off."""
    if agent is None:
        return False
    return {"auto": "auto", "on": True}.get(agent.web_search_default, False)


def _sets_card_aside(session: ChatSession, payload: SendMessage) -> bool:
    """Whether a typed message while a card is open on a finished document is not the card's
    answer: a question about the document (「4장 수치는 어디서 나온 거야?」), or an edit that
    names its part by number (「4장을 번호 목록으로 바꿔줘」) — on a proposed outline only one
    that leaves the structure as it is (「4장은 빼고 진행해 줘」 is a note on the outline)."""
    pending = session.pending or {}
    if (not pending or not session.artifact_id or payload.approve
            or payload.answers is not None or not _plans_first(session)):
        return False
    text = payload.content or ""
    if revise.asks_question(text):
        # any card (questions, a proposed outline): a question is not its answer or a note
        return True
    #: one named part changed in place, the structure kept
    part_edit = bool(revise.numbered_parts(text, [""] * 50))
    if pending.get("stage") == "clarify":
        # a question card asks no shape: a named part put into one, the verb left out
        # (「3장을 번호 목록으로」, 「4장 항목을 하나로」), is an edit too
        shaped = bool(deck_service.requested_layout(text) or revise.joins_items(text))
        return (revise.asks_edit(text) or shaped) and (
            bool(revise.named_slides(text)) or part_edit)
    return revise.asks_edit(text) and part_edit


def _plans_first(session: ChatSession) -> bool:
    """Whether this surface offers an outline first (report, slides)."""
    return session.kind in (SessionKind.report, SessionKind.slides)


@router.post("/{session_id}/messages")
async def send_message(
    session_id: str, payload: SendMessage, request: Request, user: CurrentUser, db: DbSession
):
    # published-bot visitor: publisher's account, fixed agent, strict-local, nothing else
    published = _PUBLISHED.get()
    if published is not None:
        session = await _visited(db, user, session_id, published)
        # visitor may send only a question: no files, model, skills, search, 서식
        payload = SendMessage(content=payload.content, retry_of=payload.retry_of)
    else:
        session = await _owned(db, user, session_id)

    #: user message being rerun; stored words/attachments replace the client's
    retry_of: Message | None = None
    if payload.retry_of:
        retry_of = await db.get(Message, payload.retry_of)
        if retry_of is None or retry_of.session_id != session.id or retry_of.role is not Role.user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="retry_target_not_found"
            )
        # retry replays the stored turn, not just its sentence
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

    #: a question about the document, or an edit of a named part, while a card waits: not
    #: its answer; the card stays for a question, goes for an edit
    card_aside = _sets_card_aside(session, payload)
    if card_aside and not revise.asks_question(payload.content):
        session.pending = None
    # document surfaces plan first; a message while `session.pending` is a note on it
    pending = {} if card_aside else (
        dict(session.pending or {})
        if _plans_first(session) or (session.pending or {}).get("stage") == "clarify"
        else {}
    )
    #: set only via approval
    approved_plan: dict | None = None
    #: set by the figure card; `None` until a button is pressed
    approved_figures: list[dict] | None = None
    #: message working on the finished document (`services.revise` decides the target)
    revising = bool(
        _plans_first(session)
        and session.artifact_id
        and not pending
        and not payload.approve
        and payload.answers is None
        and not revise.obviously_new(payload.content)
    )
    focus = ""
    #: as typed, not the merged request
    typed_content = payload.content
    #: restructure of the on-screen document; untouched parts carried over
    carry_from: str | None = (str(pending.get("carryFrom") or "") or None) if pending else None
    carry_note: str = str(pending.get("carryNote") or "") if pending else ""
    #: "있는 자료로 진행" sends empty answers → don't ask again
    proceed_as_is = bool(
        pending and not payload.approve and payload.answers is not None and not payload.answers
    )
    #: a question card answered once — picked, typed, or 「알아서 진행」 — is not asked again
    #: in the same run
    asked_once = bool(pending and pending.get("stage") == "clarify" and not payload.approve)
    #: files new to this reply; only these rechecked
    reply_brings_files = bool(
        pending
        and payload.attachments
        and set(payload.attachments) != set(pending.get("attachments") or [])
    )
    if pending:
        answers = {**(pending.get("answers") or {}), **(payload.answers or {})}
        if pending.get("stage") == "clarify" and not payload.approve and payload.answers is None:
            # typed reply answers open questions (else the card returns)
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
        # approval: the one path that doesn't replan
        if payload.approve and pending.get("plan"):
            approved_plan = _edited_plan(payload.plan, dict(pending["plan"]))
        # pictures agreed, or `[]` when declined
        if pending.get("stage") == "figures" and payload.include_figures is not None:
            approved_figures = list(pending.get("figures") or []) if payload.include_figures else []
        # gap answer reads as the fact: 「회의 일시: 10월 7일」
        shown = check_skills.labelled(answers, list(pending.get("questions") or []))
        # card's own label for picked answers isn't a second note
        note = "" if payload.answers and typed_content.strip() == " · ".join(
            str(v).strip() for v in payload.answers.values()) else typed_content
        payload = payload.model_copy(
            update={
                "content": grounding.merge_answers(
                    str(pending.get("request") or ""),
                    # approval isn't a condition on the request
                    shown if payload.approve else {**shown, "_note": note},
                ),
                # reply carries the original attachments
                "attachments": payload.attachments or list(pending.get("attachments") or []),
            }
        )

    if session.kind not in (SessionKind.chat, SessionKind.report, SessionKind.slides):
        # image and a/v: own job endpoints
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="surface_not_implemented"
        )
    auto_turn = bool(
        session.kind is SessionKind.chat
        and session.routing_mode in _AUTO_MODES
        and payload.model is None
    )

    # refused before any write, never dropped
    if payload.render_template_id is not None:
        if not await user_templates.usable(db, user.id, payload.render_template_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="design_template_not_found")
        session.render_template_id = _resolved_template_id(
            payload.render_template_id, session.kind, keep=session.render_template_id)
    await user_templates.load(db, user.id, [session.render_template_id])

    # one answer at a time per process (else Q,Q,A,A); a stopped turn lets the next through
    if session.kind is SessionKind.chat and any(
        not signal.is_set() for signal in _STOPPING.get(session.id, set())
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session_busy")

    # policy before any write, billing, key issue or model call
    policy = await _require_egress_policy()
    content = payload.content
    await _refuse_blocked(user, request, policy, content)
    catalogue = await model_service.list_models_for_egress()
    catalogue_models = catalogue["models"]
    usable = _allowed_models(user, catalogue_models, kind=session.kind.value)
    # model precedence: turn override, session, agent
    try:
        agent_model, agent_tools, agent_temperature = await agent_settings(db, user, session)
        # 0.4 unless agent sets its own: consistent house style
        if agent_temperature is None:
            agent_temperature = 0.4
    except WorkspaceContextError as exc:
        _raise_workspace_error(exc)
    model_id = session.model if auto_turn else payload.model or session.model or agent_model
    if published is not None:
        # version's strict-local model or no answer: never a fallback that may leave
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
        # Auto is a ceiling, not license to substitute cheaper
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="auto_quality_model_required",
        )
    # kept so routing metadata and transcript can name the substitute
    revoked_model = model if model is not None and model not in usable else None
    if model not in usable:
        model = None
    if model is None:
        # the admin's default for the surface; fallback stays inside the allowlist
        model = model_service.pick_default(usable, catalogue, session.kind.value)
        if model is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
            )
    requested_model = model

    history = await _history(db, session_id)
    # multi-replica: the shared transcript says if a fresh question is answered elsewhere
    if (
        session.kind is SessionKind.chat
        and retry_of is None
        and stop_signal.needed()
        and history
        and _answer_in_flight(history[-1])
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session_busy")
    #: retry's superseded failed reply; neither it nor the question stays in history
    superseded: list[Message] = []
    if retry_of is not None:
        at = next((i for i, m in enumerate(history) if m.id == retry_of.id), None)
        if at is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="retry_target_not_found"
            )
        # only the latest question may be rerun
        if any(m.role is Role.user for m in history[at + 1 :]):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="retry_not_latest")
        superseded = history[at + 1 :]
        history = history[:at]

    # ownership before privacy assembly
    rows, attachment_meta = await _owned_attachments(db, user, payload.attachments)

    #: picked skills, else the standing set; a pick replaces the standing set
    active_skill_ids = list(payload.activated_skill_ids)

    stored_content = content
    # the server's notes under a stored answer are not the model's words (`server_notes`)
    outbound_history = [server_notes.strip(message.content) if message.role is Role.assistant
                        else message.content for message in history]
    privacy_resolution: _PrivacyResolution | None = None
    tools: list[Tool] = []
    tool_definitions: list[dict[str, Any]] = []
    candidate_tools: list[Tool] = []
    strict_tools: list[Tool] = []

    # tool definitions are outbound prompt data: built before the privacy decision
    agent_row: WorkspaceAgent | None = None
    shelf: list[tuple[str, str, str | None]] = []
    shelf_key = ""
    #: agent's own shelf material: searched before the model answers
    agent_material = False
    if session.agent_id:
        agent_row = await db.get(WorkspaceAgent, session.agent_id)
    if payload.web_search is None:
        # caller sent no toggle (API, MCP): the agent's default, else off
        payload = payload.model_copy(update={"web_search": _agent_web_search(agent_row)})
    specs, own_scripts = await _skill_specs(db, user, agent_row)
    if published is not None:
        active_skill_ids = []
    elif not active_skill_ids and session.kind in _STANDING_KINDS:
        # standing set, pruned of since-removed or barred skills (a plan's approval too)
        active_skill_ids = await _standing_skill_ids(db, user, session, agent_row)
    # what the turn's skills bring besides their words (template, checks, questions, settings)
    skills_bring = skill_turn.Brought() if published is not None else await skill_turn.brought(
        db, user, session, agent_row, active_skill_ids)
    if (picked_template := await skill_turn.template_for(db, user, session, skills_bring)):
        session.render_template_id = picked_template
    if session.kind is SessionKind.chat and requested_model.get("supportsTools"):
        shelf, shelf_key = await _knowledge_shelf(db, user, session, agent_row)
        agent_material = bool(shelf) and await _has_agent_material(db, user, session)

    requested_is_strict = _strict_model(requested_model)
    strict_candidate_available = _strict_route_possible(
        requested_model, catalogue_models, policy, user, auto_turn=auto_turn
    )
    # web toggle + sentence → tools offered and forced first tool; before tool build
    if pending and payload.web_search is False:
        # approval/answer has no toggle: the plan's starting turn chose it
        payload = payload.model_copy(update={"web_search": _planned_web_search(history)})
    # text pasted under an editing ask is not a question
    effective_web_search, forced_tool = search_plan(
        payload.web_search,
        _ask_of(content) if report_service.edits_given_text(content) else content)
    if published is not None:
        # bots answer from published material only; no web
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
        # Auto isn't renewed consent for an explicitly offline question
        effective_web_search, forced_tool = False, None
    #: a draft, edit, translation or code fix of the person's own facts on 「auto」: no
    #: search forced or offered, every model alike; a named lookup or world value still searches
    own_work = (
        session.kind is SessionKind.chat and payload.web_search == "auto"
        and published is None and fresh_followup_index is None
        and forced_tool != "weather" and not requests_web_search(content)
        and exact_facts.own_work(_ask_of(content))
    )
    if own_work:
        forced_tool = None
    # the ask, not the material pasted under it, decides
    fresh_fact = not own_work and published is None and (
        freshness.current_fact_required(_ask_of(content)) or (
            fresh_followup_index is not None
            and freshness.current_fact_required(history[fresh_followup_index].content)
        ))
    #: first search forced by the current-fact rule alone
    forced_by_freshness = False
    if fresh_fact and effective_web_search and forced_tool != "weather":
        # current fact asked: search forced; availability rechecked after privacy
        forced_by_freshness = forced_tool is None
        forced_tool = "web_search"
    earlier_asks = [m.content for m in history if m.role is Role.user]
    #: the ask without the material pasted under it: a 「재정 진단」 in a pasted press release
    #: makes no medical question
    asked = _ask_of(content)
    #: why the first search is forced though no current-fact wording asked for it
    #: (a hotline, a tax rule, a rate, advice): `exact_facts`, every model alike
    exact_reason = (
        exact_facts.search_reason(asked, earlier_asks)
        if session.kind is SessionKind.chat and published is None and not fresh_fact
        and not own_work and effective_web_search and forced_tool is None
        and not is_small_talk(content)
        else None
    )
    if exact_reason:
        forced_by_freshness = True
        forced_tool = "web_search"
    #: legal, tax, financial or medical advice: the answer's concrete claims are traced
    advice = (
        exact_facts.advice_domain(asked, earlier_asks)
        if session.kind is SessionKind.chat and published is None
        else None
    )
    #: stroke signs, chest pain, anaphylaxis, suicidal intent…: the answer opens with 119/109
    urgent = (
        red_flags.detect(content, earlier_asks) if session.kind is SessionKind.chat else None
    )
    web_search_auto = payload.web_search == "auto" and forced_tool is None
    # agent allowlist without web_search is deliberate: no 「웹 검색 없이」 noise
    if session.agent_id and agent_tools is not None and "web_search" not in agent_tools:
        effective_web_search = False
    if session.kind is SessionKind.chat and requested_model.get("supportsTools"):
        skill_shelf = [] if published is not None else await _skill_shelf(
            db, user, agent=agent_row,
            exclude={*active_skill_ids, *((agent_row.active_skill_ids or []) if agent_row else [])}
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
            # own scripts join shipped ones in `run_skill_script`
            candidate_tools = sandbox_tools.with_own_scripts(candidate_tools, own_scripts)
            strict_tools = sandbox_tools.with_own_scripts(strict_tools, own_scripts)
        if not uses_memory(agent_row):
            # an agent kept out of memory writes no shared note (a memory row) either
            candidate_tools = [t for t in candidate_tools if t.name != "share_note"]
            strict_tools = [t for t in strict_tools if t.name != "share_note"]

    requested_tools = strict_tools if requested_is_strict else candidate_tools
    # app how-to: guide only; memories and 개인 맞춤 설정 kept out
    how_to = session.kind is SessionKind.chat and app_help.about_app(content) and published is None
    try:
        workspace = await assemble(
            db,
            user,
            session,
            published=published is not None,
            attachment_ids=payload.attachments,
            # chat re-assembles below against the privacy-settled model
            vision=reads_pictures(requested_model),
            file_budget=file_budget(requested_model),
            activated_skill_ids=active_skill_ids,
            # empty focus = head of file
            focus=focus or (content if session.kind is not SessionKind.chat else ""),
            question=_asked_about(content, history),
            how_to=how_to,
            # report/deck writers: no chat tool loop
            available_tool_names=(
                {tool.name for tool in requested_tools}
                if session.kind is SessionKind.chat
                else set()
            ),
        )
    except WorkspaceContextError as exc:
        _raise_workspace_error(exc)

    if (
        (fresh_fact or exact_reason)
        and session.kind is SessionKind.chat
        and (
            (_grounded_in_files(workspace) and not freshness.names_the_present(content))
            or _reads_pasted_material(content)
        )
    ):
        # question about an attached/pasted document: read it, not search or withhold
        fresh_fact = False
        exact_reason = None
        if forced_by_freshness and forced_tool == "web_search":
            forced_tool = None
            web_search_auto = payload.web_search == "auto"

    if own_work or _material_only_turn(session, payload, forced_tool, content, workspace,
                                       published=published is not None):
        # the person gave the material (pasted, attached or marked made up) and asked for
        # nothing to be looked up: no automatic search, every model alike
        effective_web_search = web_search_auto = False
        candidate_tools = [t for t in candidate_tools if t.name != "web_search"]
        strict_tools = [t for t in strict_tools if t.name != "web_search"]

    if (
        forced_tool == "web_search"
        and session.kind is SessionKind.chat
        and payload.web_search == "auto"
        and _answers_from_files(content, workspace)
    ):
        # question the files answer reads them (a year isn't news); tool still offered
        forced_tool = None
        web_search_auto = True

    requested_tools = strict_tools if requested_is_strict else candidate_tools
    if session.kind is SessionKind.chat and published is None and (
            deep := _deep_research_tool(content, requested_tools)):
        # 「심층 리서치로」 named: the deep mode runs, not a quick search the model picks
        forced_tool = deep

    # attachment shortfalls asked before planning; pending replies: new files only
    short = grounding.file_shortfalls(workspace.attachments) if _plans_first(session) else []
    # gap check: same card, once, before the first draft; never on revisions or follow-ups
    agent_key = await _agent_catalog_key(db, agent_row)
    # agent's own checks and strictness from its row
    check_keys = check_skills.attached(
        agent_checks=skills_bring.checks_with(
            agent_row.check_skills if agent_row is not None else None),
        template_checks=render_template_checks(session), specs=specs)
    if session.kind is not SessionKind.chat:
        # document writers run no tools: a turned-on check skill runs as the server's check
        check_keys = tuple(dict.fromkeys((*check_keys, *check_skills.attached(
            agent_checks=list(_skill_keys(workspace.applied_skills, specs)), specs=specs))))
    check_options = skills_bring.options_with(check_skills.clean_options(
        agent_row.check_options if agent_row is not None else None))
    # user-pipeline conversation asks through its own steps (`human:gaps`)
    from app.routers import user_pipelines

    user_pipe = (await user_pipelines.bound(db, user, session, agent_row)
                 if published is None else None)
    if user_pipe is not None:
        check_keys = ()
    if published is not None:
        # visitor: post-writing checks run, no question card
        check_keys = tuple(check_skills.of_stage(check_keys, "post", specs))
    if agent_row is not None and agent_row.contact.strip():
        # 담당 부서·연락처, quoted verbatim by checks
        check_options = {**check_options, "contact": agent_row.contact.strip()}
    # 빠진 정보 질문: 서식's and agent's own, else its base's
    asking = skills_bring.questions_with(gap_questions.resolve(
        own=agent_row.gap_questions if agent_row is not None else None, base_key=agent_key,
        template_id=session.render_template_id if session.kind is SessionKind.report else None,
        doc_format=workspace.doc_format)) if check_skills.of_stage(check_keys, "pre", specs) \
        else []
    judge_model, judge_key = "", ""
    if gap_questions.needs_judge(asking):
        # judge reads material before routing: in-boundary model only, else nothing asked
        judge_model = _gap_judge_model(user, catalogue_models, requested_model,
                                       policy.outline_model_id)
        judge_key = (await litellm_service.credentials_for(user))[1] if judge_model else ""
    first_draft = not revising and not approved_plan and (_plans_first(session) or not any(
        m.role is Role.assistant and not (m.routing or {}).get("askedCard") for m in history))
    # card's file questions: missing files, or the attachment the request names
    file_questions = grounding.questions_for(short)
    if not file_questions and (
            gap := grounding.missing_attachment(content, workspace.attachments)):
        file_questions = [gap]
    pre = await _gaps(check_keys, questions=asking, judge=(judge_model, judge_key),
                      request=content,
                      # what the agent's 지시 already states is not asked again
                      material="\n".join([*workspace.untrusted, *workspace.standing]),
                      first_draft=first_draft,
                      doc_format=workspace.doc_format, agent_key=agent_key, specs=specs, card={
                          "plans_first": _plans_first(session),
                          "may_ask": not pending or reply_brings_files,
                          "files": [q.wire() for q in file_questions],
                          # just-answered card not repeated
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
        # proceeding without asked facts: each stays a bracket blank
        content = _with_condition(content, blanks)
    if skipped := [f.name for f in short if f.state == "unreadable"]:
        # proceeding past the card: writer told the missing file by name
        content = _with_condition(
            content,
            f"읽지 못한 첨부({', '.join(skipped)})는 빼고 읽은 자료로 진행한다. "
            "문서에 그 파일을 읽지 못해 반영하지 않았다고 이름을 들어 밝힌다.",
        )

    protected_values: frozenset[str] = frozenset()
    if session.kind is SessionKind.chat:
        privacy_sources = _privacy_sources(
            content, history, workspace.blocks,
            summary=str((session.summary or {}).get("text") or "") or None,
        )
        if requested_tools:
            privacy_sources["tool_definitions"] = _tool_definition_source(requested_tools)
        # Auto scans the full envelope before any classifier, key or model call
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
            # `actualModelChanged` compares against the requested model
            resolved.routing = {
                **resolved.routing,
                "requestedModels": [revoked_model["id"]],
            }
        if auto_turn and auto_preflight_findings:
            # privacy owns this turn; classifier never sees the envelope
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
        # privacy routing can't add tool support
        if requested_model.get("supportsTools") and model.get("supportsTools"):
            tools = strict_tools if strict_local else candidate_tools
        masker = governance.mask_legacy if policy.pii_masking else governance.mask
        # egress-masked user-side values; the stored answer masks exactly these (and secrets)
        protected_values = _protected_values(privacy_sources, legacy=policy.pii_masking)
        if resolved.findings:
            # findings stored masked regardless of action
            stored_content = masker(content)[0]
            if attachment_meta:
                # filenames and errors are user content
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
            # drop whole tools; schema keys can't be rewritten safely
            tools = _drop_sensitive_tool_definitions(
                tools,
                legacy=policy.pii_masking,
            )
        tool_definitions = openai_snapshot(tools)
    elif policy.pii_masking:
        # decision flow chat-only; report/slides always mask
        masker = governance.mask_legacy
        content, masked = masker(content)
        stored_content = content
        if attachment_meta:
            attachment_meta = _mask_text_tree(attachment_meta, masker)
        if masked:
            await _audit_policy(user, request, "pii.masked", f"{masked}건")

    # re-resolve skills against the final tool snapshot
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
    #: this request's attachments (beside the question in chat; with the rest for documents)
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
        # always-mask covers context blocks too
        trusted_context = _mask_text_tree(trusted_context, governance.mask_legacy)
        untrusted_context = _mask_text_tree(untrusted_context, governance.mask_legacy)
        turn_context = _mask_text_tree(turn_context, governance.mask_legacy)
    skills_event = workspace.skills_event()
    template_blocks, skills_event = await _template_skills(
        db, design_templates.get(session.render_template_id), session.kind, skills_event
    )
    if template_blocks:
        trusted_context = list(trusted_context) + template_blocks
    if how_to:
        # app question: reads its own user guide (in-process, local)
        if guide := app_help.block(content, lang="en" if _english(content) else "ko"):
            trusted_context = [*trusted_context, guide]
    context_steps = _context_steps(workspace)
    if policy.pii_masking:
        # skill names are user-controlled
        if skills_event:
            skills_event = _mask_text_tree(skills_event, governance.mask_legacy)
        # memory names too
        context_steps = _mask_text_tree(context_steps, governance.mask_legacy)

    # report/slides: no privacy resolution to carry a substitution
    document_routing = (
        _substitution_routing(revoked_model, model)
        if privacy_resolution is None and revoked_model is not None
        else None
    )
    if (
        session.kind is not SessionKind.chat
        and any(block.source == "agent.language" for block in workspace.blocks)
        and agent_row is not None
    ):
        # the agent's language as a condition the document writers read with the request
        content = _with_condition(content, output_language.condition(agent_row.output_language))
    turn_options = {
        "webSearch": payload.web_search,
        "activatedSkillIds": active_skill_ids,
    }
    # stored for retry replay; defaults store nothing
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
        # document route settled here: internal → strict-local; Auto may move the writer
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
    # missing verification qualifies the answer, doesn't block it
    if session.kind is not SessionKind.chat:
        # document planners/writers expose no full actual-model trace
        trusted_context = [
            *trusted_context,
            freshness.answer_instruction({**model, "knowledgeCutoff": None}, structured=True,
                                         light=model_tier.strong(model["id"])),
        ]
    # attached-table figures come from code on the file: first hop forced to run_code
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
    # 「다음 주 월요일」 counted from today by code, not by the writer
    if session.kind is SessionKind.chat and (dated := date_rules.today_block(content, _today())):
        trusted_context = [*trusted_context, dated]
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
        """Request for `wire`; reads `trusted_context` at call time; `economy` = tool-free shape."""
        built = build_messages(
            session.kind,
            wire,
            with_tools=False if economy else bool(tools),
            web_search=False if economy else effective_web_search,
            # whether a search tool survived, so the answer doesn't read as searched
            web_search_available=any(t.name == "web_search" for t in tools),
            web_search_auto=web_search_auto,
            extra=trusted_context,
            untrusted_context=untrusted_context,
            turn_context=turn_context,
            remind_standing=session.kind is SessionKind.chat,
            # the writer at call time: an auto route may change it (`model_tier`)
            light=model_tier.strong(model["id"]),
        )
        # after `build_messages`, never inside — see `context.with_pictures`
        return with_pictures(built, [picture.uri for picture in workspace.pictures])

    messages = envelope(wire_history)
    #: the tier `messages` was built for; an auto route to the other tier rebuilds them
    built_light = model_tier.strong(model["id"])
    #: auto routing swapped in the tool-free envelope
    routed_down = False

    if auto_turn and not auto_preflight_findings:
        unsupported = bool(
            payload.attachments
            or forced_tool
            or active_skill_ids
            or session.agent_id
            or session.project_id
        )
        # economy turns tool-free; context fit checked on that envelope
        economy_messages = envelope(wire_history, economy=True)
        # quality upgrade keeps full messages and tools
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
            # privacy-settled model: strict-local twin stays; no upgrade widens the boundary
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
        # only a downward route swaps to the tool-free envelope
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
    if session.kind is SessionKind.chat and model_tier.strong(model["id"]) != built_light:
        # routed to a writer of the other tier: its own setup
        messages = envelope(wire_history, economy=routed_down)
    if model_tier.strong(model["id"]):
        # the step log says which setup the writer got (evaluation reads it)
        context_steps = [*context_steps, _setup_step(model["id"])]

    for stored in rows:
        stored.session_id = session.id
        db.add(stored)

    #: answers the rerun replaces, carried on the new answer
    earlier_answers: list[dict[str, Any]] = []
    #: their rows, deleted only with the new answer's commit
    superseded_ids: list[str] = []
    if retry_of is not None:
        # reuse question row; failed reply goes now, good one on commit as an earlier answer
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
            # typed, not the merged request
            content=typed_content if pending else stored_content,
            attachments=attachment_meta,
            routing=privacy_resolution.routing if privacy_resolution else document_routing,
        )
    if session.kind is SessionKind.chat and not await _claim_turn(db, session):
        # another replica holds the turn; claim shares the question's transaction
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session_busy")
    db.add(user_message)
    if session.kind is SessionKind.chat and pending:
        # turn answers (or skips) the chat agent's gap card
        session.pending = None
    # request models are turn-only; lasting choices via session settings API
    if payload.model is None:
        # substitute is turn-only
        if revoked_model is None:
            session.model = requested_model["id"]
    session.updated_at = utcnow()
    if session.kind in _STANDING_KINDS and payload.activated_skill_ids:
        # picked for this turn, kept for later
        session.skill_ids = list(payload.activated_skill_ids)
    if not session.title:
        # provisional title until the first turn completes
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
        # card answered or skipped: close the waiting run
        await _close_waiting_run(str(pending["runId"]), answers=dict(pending.get("answers") or {}),
                                 as_is=proceed_as_is, blanks=len(missing))

    # also issues a key to accounts provisioned during a proxy outage
    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)

    is_first_turn = len(history) == 0

    # planner: writer's allowlist, surface, boundary; default = local model
    outline_model = _planner_model(
        policy.outline_model_id or _document_role(await settings_store.model_roles()),
        user=user,
        catalogue=catalogue_models,
        kind=session.kind.value,
        writer=model,
        strict_local=strict_local,
    )
    if outline_model is not None and outline_model["id"] == model["id"]:
        outline_model = None  # the writer plans for itself; nothing to account for apart

    #: draws document figures; none = no figure card
    image_model = _document_image_model(user, catalogue, strict_local=strict_local)
    #: draws pictures on its own, like diagrams; only inside the data boundary
    auto_image_model = _auto_image_model(user, catalogue)

    render_template = design_templates.get(session.render_template_id)
    #: 「좋아, PDF로 줘」 = file hand-over, not an edit; 「견적 고치고 PDF로」 revises first
    asked_files = revise.export_request(typed_content) if revising else None
    # `route` stage: page, file hand-over, revision, report, deck or chat
    route, route_record = await _route_turn({
        "kind": session.kind.value,
        # document/deck templates use the report/deck writers; others the block writer
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
                    may_ask=not (proceed_as_is or asked_once) and not _brings_material(content),
                    user_id=user.id,
                    api_key=api_key,
                    session_id=session.id,
                    # `None` plans and offers again
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
                    # no network on strict-local
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
        # local planner judges the instruction, not the external writer
        planner = outline_model or _local_planner(
            user=user, catalogue=catalogue_models, kind=session.kind.value, writer=model,
            strict_local=strict_local,
        )
        revision, skeleton = await _revision_plan(
            db, session, instruction=content, model=planner or model, api_key=api_key
        )
        if revision is not None and revision.restructures:
            # skeleton change re-planned via outline-and-confirm; absolute terms (「9장」)
            content = grounding.merge_answers(
                revise.without_counts(await _original_request(db, session) or content),
                {"_note": revision.note},
            )
            # planner reads skeleton; writer reads part text as this document's facts
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

    # the person's files or pasted material: what the answer or document is traced to
    material_turn = _carries_material(workspace) or _pasted_in(wire_history)

    if user_pipe is not None and route in ("report", "chat"):
        # person's own pipeline runs or resumes the turn (`user_pipelines`)
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
                    # run the card waited on, if any
                    parent_run_id=str(pending.get("runId") or "") or None,
                    may_ask=not (proceed_as_is or asked_once) and not _brings_material(content),
                    figures_plan=approved_figures,
                    image_model=image_model,
                    auto_image_model=auto_image_model,
                    template=render_template,
                    format_id=workspace.doc_format,
                    check_keys=_traced(check_keys, material_turn),
                    check_options=check_options,
                    skill_specs=specs,
                    process=skills_bring.process_with(
                        agent_row.process_settings if agent_row is not None else None),
                    user_id=user.id,
                    api_key=api_key,
                    session_id=session.id,
                    # `None` plans and offers again
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
                    ] + [
                        # attached files are sources with their own number, like project files
                        {"id": item.id, "name": item.name, "state": item.state, "attached": True}
                        for item in (*workspace.attachments, *workspace.carried)
                    ],
                    # no network on strict-local
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
                    traced=material_turn,
                    # turned-on check skills run as the server's checks, as on a report
                    check_keys=check_keys,
                    code_calc=bool((skills_bring.process_with(
                        agent_row.process_settings if agent_row is not None else None) or {}
                                    ).get("code_calc")),
                    may_ask=not (proceed_as_is or asked_once) and not _brings_material(content),
                    figures_plan=approved_figures,
                    image_model=image_model,
                    auto_image_model=auto_image_model,
                    user_id=user.id,
                    api_key=api_key,
                    session_id=session.id,
                    # `None` plans and offers again
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
                    # no network on strict-local; on 「auto」 a talk built from the person's own
                    # material looks nothing up
                    web_search=effective_web_search and not strict_local and not (
                        payload.web_search == "auto"
                        and report_service.given_material_suffices(
                            content, has_files=_grounded_in_files(workspace))),
                )
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # fit history to the final model's window; overflow → running summary
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
    # links the person gave in this message are read before anything is said about them
    given_links = (
        given_links_in(content)
        if session.kind is SessionKind.chat and "fetch_url" in tool_names
        and not preflight_tool and not calculation_required
        and published is None and not agent_material and not declines_web_search(content)
        else []
    )
    if forced_tool == "web_search" and "web_search" in tool_names and not given_links:
        # reuse only privacy-processed history; never re-send the earlier raw question
        lookup_content = (
            outbound_history[fresh_followup_index]
            if fresh_followup_index is not None else content
        )
        planned, entities = (
            await research_service.literature_plan(lookup_content, model["id"], api_key)
            if literature_request(lookup_content)
            else ([], [])
        )
        if planned:
            # literature question searched across its axes at once; named entities are
            # checked in the papers' own text
            preset_call = (
                "web_search", {
                    "query": planned[0],
                    "queries": planned,
                    "kind": "papers",
                    "scholarly": True,
                    **({"verify": entities} if entities else {}),
                    **search_hints(lookup_content),
                },
            )
        elif not search_needs_planning(search_subject(lookup_content)) \
                and not (exact_reason and history):
            # a follow-up forced by `exact_facts` leans on earlier turns: the model plans it
            preset_call = (
                "web_search", {
                    # the question, not a role-play setup around it
                    "query": search_query(search_subject(lookup_content),
                                          prefer_primary=fresh_fact),
                    **search_hints(lookup_content),
                },
            )
    elif forced_tool == "weather" and "weather" in tool_names:
        place = weather_location(content)
        if place:
            preset_call = ("weather", {"location": place})
    # a skill's declared connector given its link here: the server fetches it first, and the
    # answer is made of that result or says it could not get it
    source_fetch = (
        _source_call(workspace.applied_skills, tools, content)
        if session.kind is SessionKind.chat and not preflight_tool and not calculation_required
        else None
    )
    if source_fetch is not None and preset_call is None and (
            forced_tool is None or (forced_tool == "web_search" and exact_reason)):
        # the link's content is the turn's material: read first; a search forced by the topic
        # alone (「금리」 → financial) stays offered, not forced
        preset_call = source_fetch
        forced_tool = None
    elif given_links and source_fetch is None:
        preset_call = (
            "fetch_url",
            {"url": given_links[0]} if len(given_links) == 1 else {"urls": given_links},
        )
        messages = _with_system_rule(messages, _GIVEN_LINKS_RULE)
    #: published bot's answer when its material doesn't cover the question
    not_covered: str | None = None
    if (
        agent_material
        and preset_call is None
        and forced_tool is None
        and not preflight_tool
        and "search_knowledge" in tool_names
    ):
        # agent material only via `search_knowledge`, which models may skip: server searches first
        preset_call = ("search_knowledge", {"query": content})
        contact = agent_row.contact.strip() if agent_row is not None else ""
        if published is not None:
            not_covered = publishing.not_covered_answer(contact)
        messages = _with_system_rule(
            messages,
            publishing.MATERIAL_ONLY_RULE if published is not None else _MATERIAL_FIRST_RULE,
        )
    # substantial chat request: short plan beside it; not on economy/calculator turns
    if (
        session.kind is SessionKind.chat
        and not routed_down
        and not calculation_required
        and not tables
        # every tier, strong models included
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
                # visitor's words never remembered on the owner's account
                auto_memory=Preferences.of(user).auto_memory and published is None
                and uses_memory(agent_row),
                published=published,
                session_id=session.id,
                # shared note's reach and byline
                project_id=session.project_id or "",
                agent_name=(agent_row.name if agent_row else ""),
                model=model,
                messages=messages,
                tools=tools,
                tool_definitions=tool_definitions,
                # None = upstream default
                temperature=agent_temperature,
                plan_messages=plan_messages,
                first_user_message=stored_content,
                # no-answer failure recorded on this row
                user_message_id=user_message.id,
                is_first_turn=is_first_turn,
                skills_event=skills_event,
                context_steps=context_steps,
                routing=privacy_resolution.routing if privacy_resolution else document_routing,
                quality_model=requested_model if cost_route else None,
                judge_model=(outline_model or model)["id"],
                disable_fallbacks=bool(cost_route and cost_route.get("decision") == "routed"),
                # model output may add findings: masked at rest too
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
                # short lookups: server presets; long ones: forced planning hop
                preset_call=preset_call,
                source_tools=(frozenset({source_fetch[0]}) if source_fetch is not None
                              else frozenset()),
                preset_empty_answer=not_covered,
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
                check_keys=_advice_checks(
                    tuple(check_skills.of_stage(check_keys, "post", specs)), advice),
                advice=advice or "",
                urgent=urgent.kind if urgent else "",
                skill_keys=_skill_keys(workspace.applied_skills, specs),
                skill_specs=specs,
                own_scripts=own_scripts,
                check_options=check_options,
                # agent's 담당 부서·연락처 may be repeated verbatim
                check_material="\n".join([*workspace.untrusted, *(
                    [agent_row.contact] if agent_row is not None and agent_row.contact.strip()
                    else [])]),
                carries_material=material_turn,
                agent_key=agent_key or "",
                calculation_required=calculation_required,
                # calculator-skipping writer hands off to the outline model
                calculation_handoff=(
                    outline_model
                    if calculation_required and preflight_tool == "calculate" else None
                ),
                # an answer the writer left empty or only announced: the other local model
                # writes it once more
                rescue=await _rescue_model(
                    user=user, catalogue=catalogue_models, writer=model,
                    strict_local=strict_local, outline=outline_model),
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
            # stops nginx buffering SSE
            "X-Accel-Buffering": "no",
        },
    )


#: an explicit ask for the deep research mode
_DEEP_RESEARCH = re.compile(r"심층\s{0,2}(?:리서치|조사)|딥\s{0,2}리서치|deep\s{0,2}research", re.I)


def _deep_research_tool(content: str, tools: list[Tool]) -> str | None:
    """The deep-research connector's thorough mode when the ask names deep research and the
    connector offers it (else its quick mode), or None."""
    if not _DEEP_RESEARCH.search(_ask_of(content)):
        return None
    names = sorted(tool.name for tool in tools)
    for mode in ("detailed_research", "quick_research"):
        if found := next((name for name in names if name.endswith(f"__{mode}")), None):
            return found
    return None


async def _route_turn(facts: dict[str, Any]) -> tuple[str, StepRecord]:
    """`route` stage over the router's result: target and record."""
    from app.pipeline.route_stages import TARGETS

    work = PipelineBundle(request=PipelineRequest(text="", model="", api_key="",
                                                  turn={"route": facts}))
    async for _event in PipelineRunner([pipeline_registry.stage("route")], name="route",
                                       emit_records=False).run(work):
        pass
    record = work.steps[-1]
    record.summary = TARGETS.get(work.plan.route, work.plan.route)
    return work.plan.route, record


#: Current turn's `route` record, shown first by `_survive_disconnect` (set by `send_message`).
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


#: Per-process stop signals; `stop_turn` also broadcasts (`services.stop_signal`).
_STOPPING: dict[str, set[asyncio.Event]] = {}


def turn_running_here(session_id: str) -> bool:
    """Whether this process is still writing an unstopped answer here (→ `session_busy`)."""
    return any(not signal.is_set() for signal in _STOPPING.get(session_id, set()))


def fire_stop_locally(session_id: str) -> None:
    """Sets this process's stop signals for `session_id`."""
    for signal in _STOPPING.get(session_id, set()):
        signal.set()

#: Key echoed in an upstream error body; reason shown on screen.
_SECRET_IN_REASON = re.compile(r"sk-[A-Za-z0-9_\-]+")


#: Provider wordings for prompt overflow: vLLM, OpenAI, Anthropic, LiteLLM.
_CONTEXT_OVERFLOW = re.compile(
    r"context[ _]length|maximum context|max(?:imum)? (?:input|prompt)? ?tokens?|"
    r"too many tokens|prompt is too long|input is too long|exceeds? the (?:model|context)|"
    r"token limit|context window|input length|ContextWindowExceeded",
    re.I,
)


def _context_overflow(failure: str) -> bool:
    """Whether an upstream 4xx is a context-window overflow."""
    head, _, tail = failure.partition(": ")
    return head.strip() in ("upstream_400", "upstream_413", "upstream_422") and bool(
        _CONTEXT_OVERFLOW.search(tail)
    )


def _error_event(
    message: str, exc: BaseException | None = None, *, code: str | None = None
) -> dict[str, Any]:
    """SSE error with a machine code and bounded, redacted upstream reason."""
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
    """Races stream delivery against cancellation and closes the upstream generator."""
    iterator = events.__aiter__()
    waiting = asyncio.ensure_future(stopping.wait())
    try:
        while not stopping.is_set():
            nxt = asyncio.ensure_future(anext(iterator))
            done, _ = await asyncio.wait({nxt, waiting}, return_when=asyncio.FIRST_COMPLETED)
            if nxt not in done:
                # cancel+await pending read before `aclose()`, else it raises on a running generator
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


#: Strong refs; reader-less tasks are otherwise collectible mid-turn.
_DETACHED: set[asyncio.Task] = set()

#: Idle gap before an SSE comment; under the 60s proxy idle timeout.
HEARTBEAT_SEC = 15.0


async def _heartbeat(events: AsyncIterator[str]) -> AsyncIterator[str]:
    """Relays a stream with SSE heartbeats; pending read kept (`wait_for` could drop an event)."""
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
    """Turn response: survives a departed reader, heartbeats while silent, route record first."""
    if (record := _ROUTE.get()) is not None:
        _ROUTE.set(None)
        events = _routed(record, events)
    return _heartbeat(_detached(events))


async def _detached(events: AsyncIterator[str]) -> AsyncIterator[str]:
    """Lets a turn finish unread: work in its own task, this relays its queue."""
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
    """Writes handoff notes as memories scoped to project else session; `key` dedupes."""
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
        # byline on the description, not the body
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
                # a finding, not self-description
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
    """Binds up to eight completed arithmetic pairs without certifying the answer."""
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
        # only privacy-processed bodies establish a seed or continuation
        if calculation_policy.requires_calculation(outbound_bodies[index]):
            return True
        if not calculation_policy.is_calculation_followup(outbound_bodies[index]):
            return False
    return False


def _material_text(messages: list[dict]) -> str:
    """What the person gave this conversation (turns, attached text), for grounding."""
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
    """This turn's data-table attachments, by name or detected type."""
    return [
        row for row in rows
        if row.storage_key
        and (row.name.lower().endswith(_TABLE_SUFFIXES) or (row.mime or "").lower() in _TABLE_MIMES)
    ]


def _table_analysis_instruction(files: list[StoredFile]) -> str:
    """Forced run_code hop's brief: files to read and their columns."""
    lines = []
    for row in files[:6]:
        try:
            schema = file_schema.describe(row.name, file_service.read_blob(row.storage_key))
        except OSError:
            schema = ""
        # names and headers only: model must not copy preview rows
        heads = [
            line.strip() for line in schema.splitlines()[1:]
            if line.lstrip().startswith(("열(", "- 시트", "시트 "))
        ][:6]
        lines.append(f"- {row.name}" + (": " + " / ".join(heads) if heads else ""))
    return (
        "이 요청은 첨부한 표의 수치를 다룹니다. 첫 응답은 run_code 도구 호출이어야 합니다. 표의 "
        "값을 답에 옮겨 적어 손으로 계산하지 말고, task 에 어느 파일의 어느 열로 무엇을 계산해 "
        "print 로 보여 줄지 적으세요(files 에 파일 이름). 결과를 받은 뒤 답의 수치는 실행 결과에 "
        "나온 값만 쓰세요. 결과에 없는 수치가 필요하면 한 번 더 실행해 구하고, "
        "파일로 구할 수 없는 값은 지어내지 말고 그 값 없이도 맞는 문장으로 쓰세요.\n"
        "샌드박스 현재 디렉터리에 있는 파일:\n" + "\n".join(lines)
    )


async def _run_turn(*, published: publishing.PublishedTurn | None = None,
                    **kwargs: Any) -> AsyncIterator[str]:
    """Drives one assistant turn; a published bot's turn releases its slot however it ends."""
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
    #: published-bot visitor turn: only the conversation stored; spend marked as the bot's
    published: publishing.PublishedTurn | None = None,
    session_id: str,
    project_id: str = "",
    agent_name: str = "",
    model: dict,
    messages: list[dict],
    tools: list[Tool],
    tool_definitions: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
    #: in-stream planning so the response opens at once (see answer_plan)
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
    #: judge of the finished answer (planner when set)
    judge_model: str = "",
    #: tool the first hop must call (`agent.run_turn`)
    force_tool: str | None = None,
    preflight_tool: str | None = None,
    calculation_required: bool = False,
    #: model for a calculation the writer skipped (`agent.run_turn`)
    calculation_handoff: dict | None = None,
    #: model that writes an empty or announced-only answer once more (`agent.run_turn`)
    rescue: dict | None = None,
    calculation_expression: str | None = None,
    #: server's own first call (`agent.run_turn`)
    preset_call: tuple[str, dict[str, Any]] | None = None,
    #: tools the answer is made of (`agent.run_turn`)
    source_tools: frozenset[str] = frozenset(),
    #: whole answer when that material search finds nothing
    preset_empty_answer: str | None = None,
    freshness_request: str | None = None,
    caveat_request: str | None = None,
    #: values masked from the user's words; answer at rest masks these and secrets
    protected_values: frozenset[str] = frozenset(),
    #: pre-turn spend (summary), settled on the answer's row
    side_credits: int = 0,
    #: answers this rerun replaces, oldest first
    earlier_answers: list[dict[str, Any]] | None = None,
    #: their rows; deleted with the new answer, kept if the rerun produced nothing
    superseded_ids: list[str] | None = None,
    #: agent's `post` check skills (`check_skills`)
    check_keys: tuple[str, ...] = (),
    #: catalogue keys of the turn's skills
    skill_keys: tuple[str, ...] = (),
    #: server specs for those keys (`_skill_specs`)
    skill_specs: dict | None = None,
    #: own scripts by skill key, for `run_skill_script`
    own_scripts: dict | None = None,
    #: agent's `check_options`: trace strictness
    check_options: dict | None = None,
    #: attached/carried file text: also a valid value source
    check_material: str = "",
    #: turn reads files, project knowledge or pasted material: the answer is traced to them
    carries_material: bool = False,
    #: catalogue agent key: which pipeline finishes the answer
    agent_key: str = "",
    #: `exact_facts.advice_domain` of the turn: writer rule and grounding check
    advice: str = "",
    #: `red_flags.detect` kind of the turn: the answer opens with the emergency action
    urgent: str = "",
) -> AsyncIterator[str]:
    """Drives one assistant turn to completion; own DB session (request one closes)."""
    text_parts: list[str] = []
    steps: list[dict] = _prelude_steps(skills_event, context_steps)
    usage = {"inputTokens": 0, "outputTokens": 0}
    failed: str | None = None
    answer_id: str | None = None
    tool_output_masked = 0
    tool_output_findings: dict[tuple[str, str], int] = {}
    actual_model = model["id"]
    #: the asked model failed over and LiteLLM's fallback answered (`fallback_watch.record`)
    fallback: dict | None = None
    server_abstention = False
    tool_result_answer = False
    length_check: dict | None = None

    # set by the stop button, not a closed socket
    stopping = asyncio.Event()
    # concurrent sends: only regenerate or post-중단 supersedes; late one withdraws
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
    # supersede any earlier turn, wherever it runs
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
        # latest user message, not the opening line
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
        # public web page: switchboard/press contacts aren't leaks; only secrets removed
        return masker(value, scope="tool")

    if routing:
        # first event: model badge updates before any token
        yield chat_service.sse({"type": "privacy_route", **routing})
    if cost_routing:
        yield chat_service.sse({"type": "model_route", **cost_routing})
    if skills_event:
        yield chat_service.sse(skills_event)
    for step in context_steps or ():
        yield chat_service.sse(_step_event(step))
    if plan_messages is not None:
        # slow model server shows as a running step, not an unstarted request
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
                    messages, model, current_fact=bool(freshness_request), advice=advice,
                    urgent=urgent, light=model_tier.strong(model["id"]),
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
                    {"calculation_handoff_model": calculation_handoff["id"]}
                    if calculation_handoff is not None else {}
                ),
                **({"rescue_model": rescue["id"]} if rescue is not None else {}),
                **(
                    {"calculation_expression": calculation_expression}
                    if calculation_expression is not None else {}
                ),
                preset_call=preset_call,
                **({"source_tools": source_tools} if source_tools else {}),
                **({"preset_empty_answer": preset_empty_answer} if preset_empty_answer else {}),
                **({"freshness_request": freshness_request} if freshness_request else {}),
                **({"caveat_request": caveat_request} if caveat_request else {}),
                **({"material_given": True} if carries_material else {}),
                **({"deep_search": True} if _deep_search(skill_keys, skill_specs) else {}),
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
                # narration the agent retracted; see `agent.run_turn`
                joined = "".join(text_parts).replace(event["text"], "", 1)
                text_parts[:] = [joined]
            elif event["type"] == "step":
                # stored without `type`; one row per step id, done replaces running
                row = {k: v for k, v in event.items() if k != "type"}
                at = next((i for i, s in enumerate(steps) if s.get("id") == row.get("id")), None)
                if at is None:
                    steps.append(row)
                else:
                    steps[at] = row
            elif event["type"] == "usage":
                usage = {k: v for k, v in event.items() if k != "type"}
                continue  # re-emitted below with the credit figure
            elif event["type"] == "calculation_handoff":
                # handed-off calculation: tokens as `model:<id>:` counters, name on routing
                routing = {**(routing or {}), "calculationModel": str(event["actualModel"])}
                yield chat_service.sse({"type": "privacy_route", **routing})
                continue
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
                    # don't leak the agent loop's partial provider-route shape
                    yield chat_service.sse({"type": "model_route", **cost_routing})
                continue
            elif event["type"] == "fallback_route":
                try:
                    listed = (await model_service.list_models())["models"]
                except Exception:  # noqa: BLE001 — unlisted answering model reads as unknown
                    listed = []
                fallback = fallback_watch.record(model, str(event["servedBy"]), listed,
                                                 int(event.get("attempts") or 1))
                routing = {**(routing or {}), "fallback": fallback}
                yield chat_service.sse({"type": "privacy_route", **routing})
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
        # stopped turn still settles; only the label differs
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

    # answer-generation scope; earlier classifier/search work keeps its audit
    skip_completion_work = server_abstention or tool_result_answer
    content = "".join(text_parts)
    if content.strip() and not failed and not skip_completion_work:
        # agent finish pipeline (`app/pipelines/*.toml` `finish`); streamed text replaced if changed
        finish = chat_stages.bundle_for(
            answer=content, request=ctx.request, model=model, actual_model=actual_model,
            api_key=api_key, session_id=session_id, today=_today(),
            first_message=first_user_message, messages=messages,
            material=_material_text(messages), check_keys=check_keys,
            check_material=check_material, tool_outputs=list(ctx.outputs), judge=judge_model,
            evidence=list(ctx.searched),
            kept_steps=steps, usage=usage, skill_keys=skill_keys, check_options=check_options,
            skill_specs=skill_specs, traced=carries_material, advice=advice,
            urgent=urgent,
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
        # finish records kept on `routing.run` for reloaded transcripts
        if summary := _run_summary(finish, pipe.name, "finish"):
            routing = {**routing, "run": summary}
    if not content.strip() and not failed and not skip_completion_work and not superseded_ids:
        # no answer, from the agent or once the finish stages cleaned it: said, not silent
        log.warning("chat turn in session %s ended without an answer", session_id)
        yield chat_service.sse(_error_event(
            "답을 완성하지 못했습니다. 다시 시도하거나 다른 모델을 골라 보세요.",
            code="no_answer"))
    if failed == "stopped" and not any(usage.values()) and not skip_completion_work:
        # stopped stream has no usage chunk: estimate, marked
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
    # tell the browser what was masked; it holds the streamed original
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

    # persisted turn data masked like the answer: user details and secrets out, public contacts in
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
    # calculation hand-off and a rewrite by the rescue model: each model's price, own ledger
    # line (one line when they are the same model, its tokens counted once)
    partners = [(row, "chat.calculation" if row is calculation_handoff else "chat.completion")
                for row in (calculation_handoff, rescue) if row is not None]
    partners = [p for k, p in enumerate(partners)
                if all(p[0]["id"] != q[0]["id"] for q in partners[:k])]
    partner_credits: list[tuple[dict, str, int]] = []
    if content and not skip_completion_work:
        for row, reason in partners:
            prefix = f"model:{row['id']}:"
            spent = charge_for_tokens(
                row,
                int(usage.get(prefix + "inputTokens") or 0),
                int(usage.get(prefix + "outputTokens") or 0),
            )
            if spent:
                partner_credits.append((row, reason, spent))
    partner_total = sum(spent for _, _, spent in partner_credits)
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

    # stored and announced before title call and message commit, so the panel catches up
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
                    # open the panel only for a requested artifact
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

    # one transaction: assistant message, deduction, title
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
                    usage={**usage, "credits": credits + partner_total},
                    model=stored_actual_model,
                    routing=stored_routing,
                    artifact_ids=[new_artifact] if new_artifact else None,
                    superseded=earlier_answers or None,
                    # partial answer kept, labelled by who ended it
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
                # replaced answers leave in the successor's transaction
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
                    for row, reason, spent in partner_credits:
                        settle(
                            db,
                            user,
                            spent,
                            reason=reason,
                            session_id=session_id,
                            model=row["id"],
                            publication_id=published.publication_id if published else None,
                        )
            elif superseded_ids:
                # empty rerun: replaced answer untouched, question not marked unanswered
                answer_id = superseded_ids[-1]
            else:
                # no answer: question row carries outcome and retry
                question = await db.get(Message, user_message_id) if user_message_id else None
                if question is not None:
                    # stopped before first token = stopped, not unanswered
                    question.failure = (
                        TurnFailure.stopped if failed == "stopped" else TurnFailure.no_answer
                    )
                    db.add(question)
            # searches spent quota regardless of outcome
            record_searches(
                db,
                user,
                ctx.tool_calls.get("web_search", 0),
                session_id=session_id,
                surface=session.kind.value,
                publication_id=published.publication_id if published else None,
            )
            # notes kept from empty completions, not failed turns
            if ctx.pending_notes and not failed and not skip_completion_work \
                    and published is None:
                await _store_notes(db, user_id, session_id, project_id, ctx.pending_notes)
            if title and not session.title_set_by_user:
                session.title = title
            # own ledger line: maybe a different model
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
                # spent before the model call, regardless of outcome
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
                        # keep the selection decision without claiming its model ran
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
            if fallback is not None:
                # ops record: a self-hosted answer that LiteLLM's fallback served instead
                db.add(AuditEvent(
                    actor_id=user_id,
                    action="model.fallback",
                    target=session_id,
                    detail=f"{fallback['requestedModel']} → {fallback['servedBy']}",
                    event_metadata={**fallback, "surface": "chat",
                                    "publicationId": published.publication_id
                                    if published else None},
                    severity="warn" if fallback["external"] else "info",
                ))
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
        # owner statistics in own transaction, after the answer is durable
        try:
            async with SessionLocal() as db:
                await publishing.after_turn(
                    db, published, question=published.question or first_user_message,
                    answer=content, usage=usage, failed=bool(failed),
                    credits=credits + title_credits + side_credits)
                await db.commit()
        except Exception as exc:  # noqa: BLE001 — the answer is kept without its count
            log.warning("bot statistics for %s not counted: %s",
                        logs.safe(session_id), logs.safe(exc))

    # own transaction after durability: auto-memory needs `answer_id`
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
    yield chat_service.sse({"type": "usage", **usage, "credits": credits + partner_total})
    if title:
        yield chat_service.sse({"type": "title", "title": title})
    # stored id with `done`; browser's streaming id provisional
    yield chat_service.sse({"type": "done", **({"messageId": answer_id} if answer_id else {})})

    # only this turn's signal; a newer turn may own the set
    live = _STOPPING.get(session_id)
    if live is not None:
        live.discard(stopping)
        if not live:
            del _STOPPING[session_id]
    await _release_turn(session_id)


@router.post("/{session_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def stop_turn(session_id: str, user: CurrentUser, db: DbSession):
    """Asks the turn running on this session to stop. Idempotent."""
    # closed socket = answer still wanted
    await _owned(db, user, session_id)
    # this process first, then other replicas (superseded turn may run there)
    fire_stop_locally(session_id)
    await stop_signal.broadcast(session_id)
    # release the claim so the next message isn't refused
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
    """Artifacts from a finished turn; never raises; committed before the message."""
    masker = governance.mask_legacy if legacy_masking else governance.mask

    def at_rest(value: str) -> tuple[str, int]:
        # like the answer: user details and secrets out, public details in
        return masker(value, scope="answer", protected=set(protected_values))

    privacy_masker = at_rest if protect_privacy else None
    artifact_id: str | None = None
    async with SessionLocal() as db:
        try:
            # `create_artifact` call wins over transcript extraction; both run
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
    """Auto-memory facts from a finished turn; never raises; own transaction after durability."""
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
            # charged whether or not a fact came out
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
    """Writes a policy event on its own connection so a refusal's record survives the request."""
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
    #: `None` plans and offers; otherwise written as approved
    approved_plan: dict | None = None,
    #: False after "있는 자료로 진행": writer won't ask again
    may_ask: bool = True,
    #: so a stored proposal is written against the same files
    attachments: list[str] | None = None,
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
    #: composer search toggle; off on strict-local
    web_search: bool = True,
    project_sources: list[dict[str, Any]] | None = None,
) -> AsyncIterator[str]:
    """Drives one HTML artifact (`page` pipeline); partial pages stored."""
    if routing:
        # first: model badge updates
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
    """Stores the page (report sections, else a billed html artifact) and the message."""
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
        # planner tokens at planner price, if one ran
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
                # report blocks as HTML sections (re-renderable); decks keep html
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
                        # blocks = source, `content` = render; both kept for per-block rewrites
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
                # proposal became the document
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
    #: deck template: planner rules, `look` face, id for export
    template: design_templates.DesignTemplate | None = None,
    #: the person's files or pasted material: slide values are traced to them
    traced: bool = False,
    #: attached check skills (`check_skills.attached`); the grounding one traces slides too
    check_keys: tuple[str, ...] = (),
    #: derived values computed by code even when the request names none (a calculation skill)
    code_calc: bool = False,
    outline_model: dict | None = None,
    #: `None` plans and offers; otherwise written as approved
    approved_plan: dict | None = None,
    #: False after "있는 자료로 진행": writer won't ask again
    may_ask: bool = True,
    #: so a stored proposal is written against the same files
    attachments: list[str] | None = None,
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
    #: composer search toggle; off on strict-local
    web_search: bool = True,
    #: figure-card pictures: `None` when planning, `[]` declined; report only
    figures_plan: list[dict] | None = None,
    #: draws figures (image default, not the writer)
    image_model: dict | None = None,
    #: self-hosted image model: the document draws its own pictures
    auto_image_model: dict | None = None,
    #: restructure source; untouched parts carried over
    carry_from: str | None = None,
    carry_note: str = "",
) -> AsyncIterator[str]:
    """Drives one deck to completion (`deck` pipeline `make`); deck is an artifact."""
    #: this call's arguments, for writing a plan with nothing to decide straight away
    given = dict(locals())
    if routing:
        # first: model badge updates
        yield chat_service.sse({"type": "privacy_route", **routing})
    if skills_event:
        yield chat_service.sse(skills_event)
    for step in context_steps or ():
        yield chat_service.sse(_step_event(step))
    if template is not None and template.instructions.strip():
        # template genre rules reach the planner; typesetting rules don't
        trusted_context = [f"[서식: {template.name}]\n{template.instructions.strip()}"] + list(
            trusted_context or []
        )
    turn = {
        "user_id": user_id, "model": model, "outline_model": outline_model,
        "project_id": project_id, "routing": routing, "skills_event": skills_event,
        "context_steps": list(context_steps or []), "carry_from": carry_from,
        "carry_note": carry_note, "traced": traced, "check_keys": list(check_keys),
        "writer": {
            "web_search": web_search, "figures_plan": figures_plan, "image_model": image_model,
            "auto_image_model": auto_image_model,
            "code_calc": code_calc,
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
    straight = approved_plan is None and (lambda plan: outline_rules.nothing_to_approve(
        plan, request, has_material=traced or any(t.strip() for t in untrusted_context or ())))
    async for chunk in _make_pass(work, pipeline_registry.pipeline("deck"),
                                  crashed="슬라이드를 만들지 못했습니다.",
                                  attachments=attachments, answers=answers,
                                  straight=straight or None):
        yield chunk
    if straight and work.plan.proposal is not None and not work.findings.questions \
            and straight(work.plan.proposal):
        # nothing to decide in the outline: written as planned (pictures declined: none offered)
        planned = {k: v for k, v in work.plan.proposal.items() if k != "figures"}
        async for chunk in _run_deck(**{**given, "approved_plan": planned, "figures_plan": [],
                                        "routing": None, "skills_event": None,
                                        "context_steps": None}):
            yield chunk


async def _make_pass(work: PipelineBundle, pipe: Any, *, crashed: str,
                     attachments: list[str] | None = None, answers: dict | None = None,
                     start: str | None = None, recorder: pipeline_runs.Recorder | None = None,
                     carried: dict | None = None, attempts: dict | None = None,
                     parent: Any = None,
                     straight: Callable[[dict], bool] | None = None) -> AsyncIterator[str]:
    """One-pass writer's `make` flow (deck, page): plan turn, or write approved outline and save.

    A plan `straight` accepts is neither shown nor stored: the caller writes it at once.
    """
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
            if straight is not None and event.get("type") == "proposal" and straight(
                    event.get("plan") or {}):
                continue
            yield chat_service.sse(event)
    except Exception as exc:  # noqa: BLE001 — the turn must still settle
        log.exception("%s generation crashed for session %s", pipe.name, session_id)
        yield chat_service.sse(_error_event(crashed, exc))
    finally:
        if parent is not None:
            _carry_records(work, parent, start)
    proposal, questions = work.plan.proposal, work.findings.questions or None
    if straight is not None and proposal is not None and questions is None \
            and straight(proposal):
        return
    usage = work.usage
    # planned or asked: stored on session, no artifact
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
        # writer died: flow's save stage stores what exists (deck records the failure)
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
    """One-pass writer stream: proposal/question kept, document onto the bundle."""
    stream = service.write(**work.request.turn["writer"], api_key=work.request.api_key,
                           **({"template": design_templates.get(work.request.template_id)}
                              if payload == "page" else {}))
    async for event in stream:
        if event["type"] in ("proposal", "needs"):
            # stopped on purpose: settled by the caller, no artifact
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
        if event["type"] == "computed":
            # code-computed values, shown in the stage record
            work.computed.values = str(event.get("values") or "")
            continue
        if event["type"] == "sources":
            # what the slides may cite, for the grounding check after writing
            work.material.sources = list(event.get("sources") or [])
        yield event
    if work.plan.proposal is not None or work.findings.questions:
        raise PipelineStop("proposal offered" if work.plan.proposal is not None else "needs")


def _deck_write(work: PipelineBundle) -> AsyncIterator[dict]:
    return _document_write(work, deck_service, "deck")


def _page_write(work: PipelineBundle) -> AsyncIterator[dict]:
    return _document_write(work, page_service, "page")


async def _deck_carry(work: PipelineBundle) -> AsyncIterator[dict]:
    """Restructure: carries over slides the instruction didn't touch."""
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
    # merged slides lend their rows; carried agenda reflects the current deck
    slides = revise.absorb_rows(slides, old_slides, mentioned=carry_note)
    slides = revise.unmix_rows(slides, old_slides, mentioned=carry_note)
    # one old item's parts spread over the columns: one value for every column
    slides = revise.spread_rows(slides, old_slides)
    slides = revise.carry_by_content(slides, old_slides, mentioned=carry_note)
    slides = revise.keep_timeline_layout(
        slides, old_slides, mentioned=carry_note, request=request
    )
    # the old deck's wording of a figure pair, its ask and its order kept
    slides = revise.keep_phrasing(slides, old_slides)
    slides = revise.keep_ask(slides, old_slides, mentioned=carry_note)
    slides = revise.keep_order(slides, old_slides, mentioned=carry_note)
    slides = revise.refresh_agenda(slides)
    # carried slides may drop restated numbers: recheck the person's figures
    deck_service.restate_missing_facts(slides, request)
    # a figure the old deck marked keeps its mark; no guard blank or dangling unit carried
    slides = revise.carry_marks(slides, old_slides)
    deck_service.settle_blanks(slides)
    work.document.sections = slides
    # what the old deck showed that the new one shows nowhere: read at save, said in the reply
    work.document.before = old_slides
    if carried:
        yield {"type": "step", "id": "carry", "label": f"그대로 둔 장 {carried}개",
               "status": "done"}
        for slide in slides:
            yield {"type": "slide", "slide": slide, "done": True}


def _restructured_said(before: list[dict], slides: list[dict], *, english: bool) -> str:
    """「6장짜리 슬라이드를 만들었습니다. 고치면서 4일, 3곳과 「…」 차트가 빠졌습니다. …」: the
    deck made and what of the old deck its slides no longer show (notes do not count), in the
    deck's or the request's language."""
    count = len(deck_service.filled(slides))
    plotted = deck_service.charted_only(before, slides)
    lost = [f for f in deck_service.lost_across(before, slides, plotted) if f not in plotted]
    blocks = deck_service.lost_blocks(before, slides, english=english) if before else []
    if english:
        made = f"Made a {count}-slide deck."
        if not lost and not blocks:
            return made
        what = "; ".join(x for x in (", ".join(lost), ", ".join(blocks)) if x)
        return (f"{made} No slide shows these any more: {what}. Ask again to put them back "
                "if they belong there.")
    made = f"{count}장짜리 슬라이드를 만들었습니다."
    if not lost and not blocks:
        return made
    what = ", ".join(lost)
    if blocks:
        what = f"{what}{revise._particle(what, '과', '와')} " if what else ""
        what += ", ".join(blocks)
    return (f"{made} 고치면서 {what}{revise._particle(what, '이', '가')} 슬라이드에서 "
            "빠졌습니다. 필요하면 다시 넣어 달라고 해 주세요.")


async def _deck_save(work: PipelineBundle) -> AsyncIterator[dict]:
    """Stores the deck (version if one exists), message and bill; empty deck records the failure."""
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
        # planner tokens at planner price, if one ran
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
                # restructure keeps the deck name unless rename asked
                kept = await db.get(Artifact, carry_from)
                if kept is not None and kept.title:
                    title = kept.title
            if written:
                artifact_design = design_tokens
                if template is not None and template.look:
                    # template look beats project default and request
                    artifact_design = design_service.normalise_tokens(
                        {**(design_tokens or {}), "visualStyle": template.look}
                    )
                elif not artifact_design:
                    requested_style = str(
                        (approved_plan or {}).get("visualStyle")
                        or design_service.visual_style_for(request)
                    )
                    # deck's own accent travels with it (not product purple)
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
                        # export uses the template's PowerPoint half
                        **({"templateId": template.id} if template else {}),
                        # snapshot: later project changes mustn't repaint
                        **({"design": artifact_design} if artifact_design else {}),
                        "lint": lint.wire(lint.check(lint.from_slides(slides), slides=True)),
                        # every slide, unwritten included: gaps stay visible
                        "slides": slides,
                    },
                )
                session.artifact_id = artifact_id
                # proposal became the document
                session.pending = None
                message = Message(
                    session_id=session_id,
                    role=Role.assistant,
                    content=rerun_note(turn) or _restructured_said(
                        work.document.before if carry_from else [], slides,
                        english=bool(carry_from) and (
                            deck_service.deck_language(slides) == "en"
                            or output_language.typed_in_english(carry_note))),
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
                # nothing written: record failure for reloads
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


#: Citation style per format; APA otherwise.
_CITATION_STYLES = {"paper": "IEEE", "lab": "IEEE", "review": "IEEE"}


def _skeleton(artifact: Artifact) -> list[str]:
    """Document part names in order: slide titles or section headings."""
    data = artifact.data or {}
    if artifact.kind is ArtifactKind.deck:
        return [str(p.get("title") or "") for p in data.get("slides") or []]
    return [str(p.get("heading") or "") for p in data.get("sections") or []]


def _part_texts(artifact: Artifact) -> list[str]:
    """Each part's own words in order — what a restructure must not lose."""
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
    """Where an instruction lands, plus the skeleton; `(None, [])` without a document."""
    artifact = await db.get(Artifact, session.artifact_id) if session.artifact_id else None
    if artifact is None or not artifact.data:
        return None, []
    skeleton = _skeleton(artifact)
    if not skeleton:
        return None, []
    if revise.asks_question(instruction):
        # a question is no edit: not planned (nor re-planned as a new outline)
        return revise.Plan(scope="parts", targets=[], note=instruction), skeleton
    rename = (revise.title_rename(instruction, skeleton)
              if getattr(artifact, "kind", None) != ArtifactKind.deck else None)
    if rename is not None and rename[1] is None:
        # the document's title is renamed by hand; the rest is planned without it
        instruction = revise.without_title_clause(instruction)
        if not instruction:
            return revise.Plan(scope="parts", targets=[], note=f"제목 → {rename[0]}"), skeleton
    if (swap := revise.order_swap(instruction, skeleton)) is not None:
        # swap done in place: planner would read 「순서」 as restructure and redraw
        first, second = swap
        return revise.Plan(
            scope="parts", targets=[], note=f"{skeleton[first]} ↔ {skeleton[second]}"
        ), skeleton
    if (added := revise.explicit_insert(instruction, skeleton)) is not None:
        # 「위험 장을 하나 추가해 줘」 is an insert; a new-document reading would refuse it
        return added, skeleton
    deck = getattr(artifact, "kind", None) == ArtifactKind.deck
    by_code = (revise.only_removes_picture(instruction) or revise.only_removes_chart(instruction)
               if deck else revise.settled_by_code(instruction, skeleton, _part_texts(artifact)))
    if by_code:
        # taken away by hand in the revise route, the rest asked of the parts it names: nothing
        # for the planner to read (nor to read as a new outline)
        return revise.Plan(scope="parts", targets=[], note=instruction), skeleton
    if (named := revise.numbered_parts(instruction, skeleton)) is not None:
        # 「4장을 번호 목록으로」: the part named by number, as asked; no outline reading
        return revise.Plan(scope="parts", targets=named, note=instruction), skeleton
    plan = await revise.plan(
        message=instruction,
        title=artifact.title or "",
        parts=skeleton,
        model=model["id"],
        api_key=api_key,
    )
    if plan.restructures and not revise.asks_restructure(instruction):
        # an outline reading of words asking no new structure: never the document written
        # anew; every part edited in place only for a concrete edit, else asked what to change
        plan = (revise.Plan(scope="whole", targets=list(range(len(skeleton))), note=plan.note,
                            usage=plan.usage) if revise.asks_edit(instruction)
                else revise.Plan(scope="ask", note=plan.note, usage=plan.usage))
    return plan, skeleton


async def _original_request(db: AsyncSession, session: ChatSession) -> str:
    """Conversation's first user message: the document's original ask."""
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
    #: formats asked in the same turn (「견적 고치고 PDF로」), handed over after the edit
    export_after: list[str] | None = None,
) -> AsyncIterator[str]:
    """Applies one instruction to the document via its `revise` pipeline; no regeneration."""
    usage = {"inputTokens": 0, "outputTokens": 0}
    if routing:
        yield chat_service.sse({"type": "privacy_route", **routing})

    turn = {"user_id": user_id, "model": model, "routing": routing,
            "export_after": export_after}
    # `revise-load` first: the revise pipeline depends on what's on screen
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

    # revise flow (`*.toml` `revise`); early-ending stages send their own close
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
    """Pipeline's revise flow minus `revise-load` (run beforehand)."""
    return [stage for stage in pipe.stages("revise") if stage.name != "revise-load"]


async def _revise_pass(work: PipelineBundle, pipe: Any, recorder: pipeline_runs.Recorder, *,
                       session_id: str, user_id: str, start: str | None = None,
                       carried: dict | None = None, attempts: dict | None = None,
                       parent: Any = None) -> AsyncIterator[str]:
    """Revise flow over `work` (from `start` on rerun), then the stored revision's events."""
    runner = PipelineRunner(_revise_stages(pipe), name=f"{pipe.name}.revise", recorder=recorder)
    runner.attempts = dict(attempts or {})
    recorder.begin(work, carried=carried)
    said, errors = [], []
    try:
        async for event in runner.run(work, PipelineContext(options=dict(pipe.options),
                                                            run_id=recorder.run_id),
                                      start=start):
            if event.get("type") == "delta":
                said.append(str(event.get("text") or ""))
            elif event.get("type") == "error":
                errors.append(str(event.get("message") or ""))
            yield chat_service.sse(event)
    finally:
        if parent is not None:
            _carry_records(work, parent, start)
    saved = work.document.saved or {}
    if not saved.get("messageId"):
        # a turn that ended before the save (nothing to change, a failed rewrite) still
        # leaves its reply, so a reload shows it and the composer is free
        message_id = await _store_unsaved_revision(
            work, session_id=session_id, text="".join(said).strip(),
            error=" ".join(e for e in errors if e).strip())
        if message_id:
            saved = {**saved, "messageId": message_id}
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
        # rerun reply = stored changed count
        yield chat_service.sse({"type": "delta", "text": saved.get("reply") or ""})
    yield chat_service.sse({"type": "usage", **work.usage,
                            "credits": int(saved.get("credits") or 0)})
    yield chat_service.sse({"type": "done"})


async def _store_unsaved_revision(work: PipelineBundle, *, session_id: str, text: str,
                                  error: str) -> str | None:
    """The reply of a revise turn that stopped before `revise-save`: what it said, marked as
    no answer when it failed or said nothing; best effort."""
    turn = work.request.turn
    model = turn.get("model") or {}
    try:
        async with SessionLocal() as db:
            session = await db.get(ChatSession, session_id)
            if session is None:
                return None
            message = Message(
                session_id=session_id, role=Role.assistant, content=text or error,
                failure=TurnFailure.no_answer if error or not text else None,
                usage={**work.usage, "credits": 0}, model=model.get("id"),
                routing=turn.get("routing"),
            )
            db.add(message)
            session.updated_at = utcnow()
            db.add(session)
            await db.commit()
            return message.id
    except Exception:  # noqa: BLE001 — the live reply already reached the person
        log.exception("could not keep the reply of a stopped revision")
        return None


def _carry_records(work: PipelineBundle, parent: Any, start: str | None) -> None:
    """Rerun's records for skipped stages: the parent run's own."""
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
    """Exports the document to asked formats as chat files; `(step or None, reply)`."""
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
    """Answers a file-only turn; never revises."""
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
    #: agent's bound `doc_formats` id; 서식's own format wins
    format_id: str | None = None,
    outline_model: dict | None = None,
    #: `None` plans and offers; otherwise written as approved
    approved_plan: dict | None = None,
    #: False after "있는 자료로 진행": writer won't ask again
    may_ask: bool = True,
    #: so a stored proposal is written against the same files
    attachments: list[str] | None = None,
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
    #: composer search toggle; off on strict-local
    web_search: bool = True,
    #: figure-card pictures: `None` when planning, `[]` declined; report only
    figures_plan: list[dict] | None = None,
    #: draws figures (image default, not the writer)
    image_model: dict | None = None,
    #: self-hosted image model: the document draws its own pictures
    auto_image_model: dict | None = None,
    project_sources: list[dict[str, Any]] | None = None,
    #: restructure source; untouched parts carried over
    carry_from: str | None = None,
    carry_note: str = "",
    #: check skills from agent and 서식; None = 서식's
    check_keys: tuple[str, ...] | None = None,
    #: agent's `check_options`: trace strictness
    check_options: dict | None = None,
    #: server specs for a person's check skill in `check_keys`
    skill_specs: dict | None = None,
    #: agent's 진행 과정 설정 (`Agent.process_settings`), over its 서식's
    process: dict | None = None,
    #: run this pass follows (the waiting card)
    parent_run_id: str | None = None,
) -> AsyncIterator[str]:
    """Drives one report to completion (`write` → `report-save`); run kept for reruns."""
    #: this call's arguments, for writing a one-part plan straight away
    given = dict(locals())
    proposal: dict | None = None
    questions: list[dict] | None = None
    usage = {"inputTokens": 0, "outputTokens": 0}
    writing = approved_plan is not None
    #: a one-part plan (공문 「본문」) with nothing to choose is written, not offered
    straight = False
    recorder = pipeline_runs.Recorder(run_id=uuid4().hex) if writing else None
    built: list = []
    announced = False
    turn = _report_turn(
        user_id=user_id, model=model, outline_model=outline_model, project_id=project_id,
        routing=routing, skills_event=skills_event, context_steps=context_steps,
        carry_from=carry_from, carry_note=carry_note,
        run_id=recorder.run_id if recorder else "",
    )
    #: stream output, for writers that don't store their own result
    told: dict[str, Any] = {}

    if routing:
        # first: model badge updates
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
            auto_image_model=auto_image_model,
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
                # stopped on purpose: stored below, no artifact
                if event["type"] == "proposal":
                    proposal = event["plan"]
                    straight = writing is False and outline_rules.nothing_to_approve(
                        proposal, request,
                        has_material=any(t.strip() for t in untrusted_context or ()),
                    )
                    if straight:
                        continue
                    # the carried findings stay with the stored plan
                    event = {**event, "plan": report_stages.public_plan(proposal)}
                else:
                    questions = event["questions"]
                yield chat_service.sse(event)
                continue
            if straight and event["type"] == "usage":
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
    if straight and proposal is not None and questions is None:
        # nothing to decide in the outline (one part, or a specific request): written as planned
        planned = {k: v for k, v in proposal.items() if k != "figures"}
        async for chunk in _run_report(**{**given, "approved_plan": planned, "figures_plan": [],
                                          "routing": None, "skills_event": None,
                                          "context_steps": None}):
            yield chunk
        return
    # planned or asked: stored on session, no artifact
    if proposal is not None or questions is not None:
        # figure question held until outline approval
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
        # writer didn't store (stopped early, replaced): `report-save` on what it sent
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
    """Bundle of a writer's events, for `report-save` alone."""
    work = PipelineBundle(request=PipelineRequest(
        text=request, model=model["id"], api_key="", approved_plan=approved_plan,
        template_id=template.id if template else None, design_tokens=design_tokens,
        session_id=session_id, turn=turn))
    work.document.sections = list((told.get("report") or {}).get("sections") or [])
    work.wire = {k: v for k, v in told.items() if k != "report"}
    work.usage = {"inputTokens": 0, "outputTokens": 0, **usage}
    return work


# ── pipeline runs ──


#: Above any real run's snapshots; rejects runaway ones.
_MAX_SNAPSHOTS = 24_000_000


def _run_event(run_id: str, pipeline: str, flow: str, *, parent_id: str | None = None,
               from_stage: str | None = None) -> dict | None:
    """`run` event a recorded pass opens with (records on only)."""
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
    """Compact run kept on the message (`routing.run`): records without evidence."""
    if not pipeline_records_on() or not work.steps:
        return None
    return {"id": run_id, "pipeline": pipeline, "flow": flow,
            "status": _run_status(work.steps), "parentId": parent_id, "fromStage": from_stage,
            "stages": [r.stage_wire(evidence=False) for r in work.steps]}


async def _persist_run(work: PipelineBundle, recorder: pipeline_runs.Recorder, *,
                       session_id: str, user_id: str, saved: dict | None = None,
                       parent: Any = None, from_stage: str | None = None,
                       pipeline: str = "", flow: str = "") -> str | None:
    """Keeps the run (`PipelineRun`) and its compact copy on the message; best effort."""
    saved = saved or {}
    pipeline, flow = pipeline or recorder.pipeline, flow or recorder.flow
    snapshots = recorder.snapshots()
    if len(json.dumps(snapshots, ensure_ascii=False, default=str)) > _MAX_SNAPSHOTS:
        # kept without snapshots: records stand, not rerunnable
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
    """`Request.turn` of a report's writing pass, for `report-save`."""
    return {
        "user_id": user_id, "model": model, "outline_model": outline_model,
        "project_id": project_id, "routing": routing, "skills_event": skills_event,
        "context_steps": list(context_steps or []), "carry_from": carry_from,
        "carry_note": carry_note, "run_id": run_id,
        **({"rerun_from": rerun_from} if rerun_from else {}),
    }


async def _mark_unanswered(session_id: str) -> None:
    """Marks the latest unanswered question failed so a reload shows the failure and retry."""
    try:
        async with SessionLocal() as db:
            last = (await db.exec(
                select(Message).where(Message.session_id == session_id)
                .order_by(col(Message.created_at).desc()).limit(1)
            )).first()
            if last is not None and last.role is Role.user and last.failure is None:
                last.failure = TurnFailure.no_answer
                db.add(last)
                await db.commit()
    except Exception:  # noqa: BLE001 — the stream has already said it failed
        log.exception("could not mark the unanswered report turn in session %s", session_id)


async def _report_tail(work: PipelineBundle | None, *, recorder: pipeline_runs.Recorder | None,
                       session_id: str, user_id: str, usage: dict, parent: Any = None,
                       from_stage: str | None = None) -> AsyncIterator[str]:
    """After a report's writing pass: run kept, artifact named, usage, done."""
    saved = (work.document.saved if work is not None else None) or {}
    written = [s for s in (work.document.sections if work is not None else [])
               if (s.get("content") or "").strip()]
    if work is not None and recorder is not None:
        await _persist_run(work, recorder, session_id=session_id, user_id=user_id,
                           saved=saved, parent=parent, from_stage=from_stage)
    if saved.get("artifactId"):
        yield chat_service.sse({"type": "artifact", "artifactId": saved["artifactId"]})
    elif not written:
        # empty body (thinking-only, empty answer): said, never a blank page that looks finished
        log.warning("report for session %s came back with no section text", session_id)
        yield chat_service.sse(_error_event(
            "보고서 본문을 쓰지 못했습니다. 다시 시도하거나 다른 모델을 골라 주세요.",
            code="empty_report",
        ))
        await _mark_unanswered(session_id)
    credits = int(saved.get("credits") or 0)
    yield chat_service.sse({"type": "usage", **usage, "credits": credits})
    yield chat_service.sse({"type": "done"})
