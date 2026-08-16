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
from app.models.chat import ChatSession, Message, Role, SessionKind
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
    SessionOut,
    SessionPatch,
    snippet,
)
from app.schemas.workspace import ArtifactOut
from app.services import agent as agent_service
from app.services import artifact_extract, audiogen, governance, imagegen, settings_store
from app.services import auto_memory as auto_memory_service
from app.services import chat as chat_service
from app.services import deck as deck_service
from app.services import litellm as litellm_service
from app.services import models as model_service
from app.services import report as report_service
from app.services.context import build_messages
from app.services.credits import charge_for_tokens, has_headroom, settle
from app.services.tools.base import Tool, ToolContext, openai_snapshot
from app.services.tools.registry import build_tools
from app.services.workspace_context import (
    ContextBlock,
    WorkspaceContextError,
    agent_settings,
    assemble,
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
    missing = code in {"agent_not_found", "project_not_found", "attachment_not_found"}
    raise HTTPException(
        status_code=(
            status.HTTP_404_NOT_FOUND
            if missing
            else status.HTTP_422_UNPROCESSABLE_CONTENT
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
    previews = await _previews(db, [s.id for s in rows])
    return [
        SessionOut.of(
            s,
            preview=previews.get(s.id, (None, 0))[0],
            message_count=previews.get(s.id, (None, 0))[1],
        )
        for s in rows
    ]


async def _previews(db: DbSession, session_ids: list[str]) -> dict[str, tuple[str | None, int]]:
    """`{session_id: (latest message snippet, message count)}`."""
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


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: str, user: CurrentUser, db: DbSession):
    session = await _owned(db, user, session_id)
    return SessionOut.of(session, await _history(db, session_id))


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
    session = ChatSession(
        user_id=user.id,
        kind=payload.kind,
        project_id=payload.project_id,
        agent_id=payload.agent_id,
        model=payload.model or "",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionOut.of(session, [])


@router.patch("/{session_id}", response_model=SessionOut)
async def patch_session(session_id: str, payload: SessionPatch, user: CurrentUser, db: DbSession):
    session = await _owned(db, user, session_id)
    changes = payload.model_dump(exclude_unset=True)
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
    composed = imagegen.compose_prompt(payload.prompt, aspect=payload.aspect, style=payload.style)

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
    # default, not a lock.
    try:
        agent_model, agent_tools = await agent_settings(db, user, session)
    except WorkspaceContextError as exc:
        _raise_workspace_error(exc)
    model_id = payload.model or session.model or agent_model
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
    if policy.pii_masking and skills_event:
        # The document runners persist this event directly as a timeline step.
        # Treat the selected skill's display name as user-controlled metadata,
        # just like an attachment filename.
        skills_event = _mask_text_tree(skills_event, governance.mask_legacy)

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
        routing=privacy_resolution.routing if privacy_resolution else None,
    )
    db.add(user_message)
    # A strict privacy route is a turn-only override. The conversation remains
    # on the model the user requested for its next (possibly clean) turn.
    session.model = requested_model["id"]
    session.updated_at = utcnow()
    if not session.title:
        # Provisional title, replaced once the first turn completes.
        session.title = stored_content.strip()[:40]
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
    if agent_row is not None:
        agent_row.runs += 1
        db.add(agent_row)
    await db.commit()

    strict_local = bool(privacy_resolution and privacy_resolution.strict_local)
    effective_web_search = payload.web_search and not strict_local

    wire_history = [
        {"role": message.role.value, "content": body}
        for message, body in zip(history, outbound_history, strict=True)
    ]
    wire_history.append({"role": "user", "content": content})
    messages = build_messages(
        session.kind,
        wire_history,
        with_tools=bool(tools),
        web_search=effective_web_search,
        # An agent allowlist may have removed the tool the toggle enabled.
        web_search_available=any(t.name == "web_search" for t in tools),
        extra=trusted_context,
        untrusted_context=untrusted_context,
    )

    # Resolved per turn while a DB session is open. Also issues the key to an
    # account provisioned during a proxy outage.
    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)

    is_first_turn = len(history) == 0
    if session.kind is SessionKind.report:
        return StreamingResponse(
            _run_report(
                user_id=user.id,
                api_key=api_key,
                session_id=session.id,
                model=model,
                request=content,
                project_id=session.project_id,
                # The same blocks the chat surface gets. Without this a report
                # or a deck saw the request sentence alone — no project
                # instructions, no memories, no attached form.
                trusted_context=trusted_context,
                untrusted_context=untrusted_context,
                skills_event=skills_event,
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
                # The same blocks the chat surface gets. Without this a report
                # or a deck saw the request sentence alone — no project
                # instructions, no memories, no attached form.
                trusted_context=trusted_context,
                untrusted_context=untrusted_context,
                skills_event=skills_event,
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
            first_user_message=stored_content,
            is_first_turn=is_first_turn,
            skills_event=skills_event,
            routing=privacy_resolution.routing if privacy_resolution else None,
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
    first_user_message: str,
    is_first_turn: bool,
    skills_event: dict | None = None,
    routing: dict | None = None,
    mask_at_rest: bool = False,
    sanitize_tool_output: bool = False,
    legacy_masking: bool = False,
    protect_enrichment: bool = False,
    privacy_audit_id: str | None = None,
) -> AsyncIterator[str]:
    """Drives one assistant turn to completion and settles it.

    Own DB session: the request-scoped one closes when the route returns the
    StreamingResponse.
    """
    text_parts: list[str] = []
    initial_skill_step = _skill_step(skills_event)
    steps: list[dict] = [initial_skill_step] if initial_skill_step else []
    usage = {"inputTokens": 0, "outputTokens": 0}
    failed: str | None = None
    tool_output_masked = 0
    tool_output_findings: dict[tuple[str, str], int] = {}
    actual_model = model["id"]

    ctx = ToolContext(user_id=user_id, session_id=session_id, api_key=api_key)
    masker = governance.mask_legacy if legacy_masking else governance.mask
    strict_local = _strict_model(model)

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
    if skills_event:
        yield chat_service.sse(skills_event)
    try:
        async for event in agent_service.run_turn(
            model["id"],
            messages,
            tools,
            ctx,
            tool_definitions=tool_definitions,
            sanitize_tool_output=(masker if sanitize_tool_output else None),
            sanitize_step_detail=masker if protect_enrichment else None,
            classify_tool_output=classify_tool_output if protect_enrichment else None,
            strict_local=strict_local,
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
    stored_steps = _mask_text_tree(steps, masker) if protect_persistence else steps
    stored_routing = _mask_text_tree(routing, masker) if protect_persistence else routing
    stored_actual_model = masker(actual_model)[0] if protect_persistence else actual_model
    credits = (
        0 if not content else charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
    )

    new_artifact: str | None = None
    title: str | None = None
    if is_first_turn and stored_content and not failed:
        enrichment_model = model["id"] if strict_local else settings.title_model or model["id"]
        title = await chat_service.generate_title(
            enrichment_model,
            first_user_message,
            stored_content,
            api_key,
            masker=masker if protect_enrichment else None,
            strict_local=strict_local,
            redact_logging=mask_at_rest or bool(tool_output_findings),
        )

    # One transaction: assistant message, deduction, title.
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        user = await db.get(User, user_id)
        if session is not None and user is not None:
            if content:
                db.add(
                    Message(
                        session_id=session_id,
                        role=Role.assistant,
                        content=stored_content,
                        steps=stored_steps or None,
                        usage={**usage, "credits": credits},
                        model=stored_actual_model,
                        routing=stored_routing,
                    )
                )
                settle(db, user, credits, reason="chat.completion", session_id=session_id)
            if title:
                session.title = title
            if privacy_audit_id:
                privacy_audit = await db.get(AuditEvent, privacy_audit_id)
                if privacy_audit is not None:
                    privacy_audit.event_metadata = {
                        **(privacy_audit.event_metadata or {}),
                        **(stored_routing or {}),
                    }
                    db.add(privacy_audit)
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
    if stored_content and not failed:
        new_artifact = await _enrich(
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
            redact_logging=mask_at_rest or bool(tool_output_findings),
            legacy_masking=legacy_masking,
        )

    if new_artifact:
        yield chat_service.sse({"type": "artifact", "artifactId": new_artifact})
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
        )
    )
    session.updated_at = utcnow()
    if not session.title:
        session.title = stored_content.strip()[:40]
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
    stored_skill_step = _skill_step(skills_event)
    if stored_skill_step and mask_at_rest:
        stored_skill_step = _mask_text_tree(stored_skill_step, masker)

    async with SessionLocal() as db:
        db.add(
            Message(
                session_id=session_id,
                role=Role.assistant,
                content=chosen["content"] if chosen else "",
                variants=variants,
                usage={"credits": total},
                steps=[stored_skill_step] if stored_skill_step else None,
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
    redact_logging: bool = False,
    legacy_masking: bool = False,
) -> str | None:
    """Artifacts and memories derived from a finished turn.

    All optional, and nothing here may raise — the turn is already stored.
    """
    artifact_id: str | None = None
    async with SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        user = await db.get(User, user_id)
        if session is None or user is None:
            return None
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
                enrichment_model = (
                    model["id"] if strict_local else settings.title_model or model["id"]
                )
                written = await auto_memory_service.extract(
                    db,
                    user,
                    user_message=first_user_message,
                    assistant_message=content,
                    api_key=api_key,
                    model=enrichment_model,
                    masker=privacy_masker,
                    strict_local=strict_local,
                    redact_logging=redact_logging,
                )
                if written:
                    log.info("auto-memory wrote %d fact(s) for user %s", written, user.id)
            except Exception:  # noqa: BLE001
                log.exception("auto-memory failed for session %s", session_id)

        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            log.exception("enrichment commit failed for session %s", session_id)
            return None
    return artifact_id


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


async def _run_deck(
    *,
    user_id: str,
    api_key: str,
    session_id: str,
    model: dict,
    request: str,
    project_id: str | None,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    skills_event: dict | None = None,
) -> AsyncIterator[str]:
    """Drives one deck to completion and settles it.

    Same contract as `_run_report`: the deck is an artifact, not a chat message.
    """
    slides: list[dict] = []
    usage = {"inputTokens": 0, "outputTokens": 0}
    doc_title = ""

    if skills_event:
        yield chat_service.sse(skills_event)
    try:
        stream = deck_service.write(
            request=request,
            model=model["id"],
            api_key=api_key,
            trusted_context=trusted_context,
            untrusted_context=untrusted_context,
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
        0 if not written else charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
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
                        steps=[_skill_step(skills_event)] if skills_event else None,
                    )
                )
                settle(db, user, credits, reason="deck.generate", session_id=session_id)
            session.updated_at = utcnow()
            db.add(session)
            await db.commit()

    if artifact_id:
        yield chat_service.sse({"type": "artifact", "artifactId": artifact_id})
    yield chat_service.sse({"type": "usage", **usage, "credits": credits})
    yield chat_service.sse({"type": "done"})


async def _run_report(
    *,
    user_id: str,
    api_key: str,
    session_id: str,
    model: dict,
    request: str,
    project_id: str | None,
    trusted_context: list[str] | None = None,
    untrusted_context: list[str] | None = None,
    skills_event: dict | None = None,
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

    if skills_event:
        yield chat_service.sse(skills_event)
    try:
        stream = report_service.write(
            request=request,
            model=model["id"],
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
        0 if not written else charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
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
                        steps=[_skill_step(skills_event)] if skills_event else None,
                    )
                )
                settle(db, user, credits, reason="report.generate", session_id=session_id)
            session.updated_at = utcnow()
            db.add(session)
            await db.commit()

    if artifact_id:
        yield chat_service.sse({"type": "artifact", "artifactId": artifact_id})
    yield chat_service.sse({"type": "usage", **usage, "credits": credits})
    yield chat_service.sse({"type": "done"})
    if failed and not written:
        return
