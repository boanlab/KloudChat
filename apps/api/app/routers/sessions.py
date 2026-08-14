"""Sessions, messages, and the chat stream.

Ordering rules for a streaming turn:

* user message committed before the upstream call
* assistant message and credit deduction committed together
* no charge for a turn that produced no output
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, update
from sqlmodel import col, delete, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.deps import CurrentUser, DbSession, client_ip
from app.models.chat import ChatSession, Message, Role, SessionKind
from app.models.user import AuditEvent, User, utcnow
from app.models.workspace import Agent as WorkspaceAgent
from app.models.workspace import Artifact, ArtifactKind, StoredFile
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
from app.services.tools.base import Tool, ToolContext
from app.services.tools.registry import build_tools
from app.services.workspace_context import agent_settings, assemble

log = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])


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
        SessionOut.of(s, preview=previews.get(s.id, (None, 0))[0],
                      message_count=previews.get(s.id, (None, 0))[1])
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
async def patch_session(
    session_id: str, payload: SessionPatch, user: CurrentUser, db: DbSession
):
    session = await _owned(db, user, session_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
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
async def generate_images(
    session_id: str, payload: ImageRequest, user: CurrentUser, db: DbSession
):
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
    composed = imagegen.compose_prompt(
        payload.prompt, aspect=payload.aspect, style=payload.style
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
async def generate_audio(
    session_id: str, payload: AudioRequest, user: CurrentUser, db: DbSession
):
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
    await db.exec(
        update(Artifact).where(col(Artifact.session_id).in_(ids)).values(session_id=None)
    )
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

    # Policy before the model call; masking before the write.
    policy = await governance.current()
    content = payload.content
    masked = 0
    if policy.intent_filter and (hit := governance.blocked_by(content, policy.blocked_categories)):
        await _audit_policy(user, request, "filter.blocked", hit)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"blocked_category:{hit}",
        )
    if policy.pii_masking:
        content, masked = governance.mask(content)
        if masked:
            await _audit_policy(user, request, "pii.masked", f"{masked}건")

    catalogue = await model_service.list_models()
    # Model precedence: turn override → session → agent. The agent supplies a
    # default, not a lock.
    agent_model, agent_tools = await agent_settings(db, session)
    model_id = payload.model or session.model or agent_model
    model = model_service.find(catalogue["models"], model_id) if model_id else None
    if model is None:
        # Cheapest chat model as fallback; a stale id must not brick a session.
        usable = sorted(
            (m for m in catalogue["models"] if "chat" in m["kinds"]),
            key=lambda m: m["creditCost"],
        )
        if not usable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_models_available"
            )
        model = usable[0]

    if not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )

    history = await _history(db, session_id)

    # Stored as id + name + readability: what the transcript renders later,
    # without a join per message.
    attachment_meta: list[dict] | None = None
    if payload.attachments:
        rows = (
            await db.exec(
                select(StoredFile).where(
                    col(StoredFile.id).in_(payload.attachments),
                    StoredFile.user_id == user.id,
                )
            )
        ).all()
        attachment_meta = [
            {"id": f.id, "name": f.name, "size": f.size, "type": f.mime, "error": f.error}
            for f in rows
        ]
        for stored in rows:
            stored.session_id = session.id
            db.add(stored)

    user_message = Message(
        session_id=session.id,
        role=Role.user,
        content=content,
        attachments=attachment_meta,
    )
    db.add(user_message)
    session.model = model["id"]
    session.updated_at = utcnow()
    if not session.title:
        # Provisional title, replaced once the first turn completes.
        session.title = content.strip()[:40]
    db.add(session)
    await db.commit()

    # No tools for models without function calling: upstream 400 or invented calls.
    tools: list[Tool] = []
    if model.get("supportsTools"):
        tools = await build_tools(
            db, user, web_search=payload.web_search, allowed=agent_tools or None
        )

    extra = await assemble(db, user, session, attachment_ids=payload.attachments)
    wire_history = [{"role": m.role.value, "content": m.content} for m in history]
    wire_history.append({"role": "user", "content": content})
    messages = build_messages(
        session.kind,
        wire_history,
        with_tools=bool(tools),
        web_search=payload.web_search,
        # An agent allowlist may have removed the tool the toggle enabled.
        web_search_available=any(t.name == "web_search" for t in tools),
        extra=extra,
    )

    if session.agent_id:
        agent_row = await db.get(WorkspaceAgent, session.agent_id)
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
    if session.kind is SessionKind.report:
        return StreamingResponse(
            _run_report(
                user_id=user.id,
                api_key=api_key,
                session_id=session.id,
                model=model,
                request=content,
                project_id=session.project_id,
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
            first_user_message=content,
            is_first_turn=is_first_turn,
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
    first_user_message: str,
    is_first_turn: bool,
) -> AsyncIterator[str]:
    """Drives one assistant turn to completion and settles it.

    Own DB session: the request-scoped one closes when the route returns the
    StreamingResponse.
    """
    text_parts: list[str] = []
    steps: list[dict] = []
    usage = {"inputTokens": 0, "outputTokens": 0}
    failed: str | None = None

    ctx = ToolContext(user_id=user_id, session_id=session_id, api_key=api_key)
    try:
        async for event in agent_service.run_turn(model["id"], messages, tools, ctx):
            if event["type"] == "delta":
                text_parts.append(event["text"])
            elif event["type"] == "step":
                # Stored without the SSE envelope key: `Step.type` in the UI is
                # a display category, not the event name.
                steps.append({k: v for k, v in event.items() if k != "type"})
            elif event["type"] == "usage":
                usage = {k: v for k, v in event.items() if k != "type"}
                continue  # re-emitted below with the credit figure
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
    credits = (
        0 if not content else charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
    )

    new_artifact: str | None = None
    title: str | None = None
    if is_first_turn and content and not failed:
        title = await chat_service.generate_title(
            settings.title_model or model["id"], first_user_message, content, api_key
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
                        content=content,
                        steps=steps or None,
                        usage={**usage, "credits": credits},
                        model=model["id"],
                    )
                )
                settle(db, user, credits, reason="chat.completion", session_id=session_id)
            if title:
                session.title = title
            session.updated_at = utcnow()
            db.add(session)
            await db.commit()

    # Enrichment after the answer is durable, in its own transaction. Sharing
    # the turn's would hold it open for an extra query and a model call, and a
    # failure would roll the reply back.
    if content and not failed:
        new_artifact = await _enrich(
            user_id=user_id,
            session_id=session_id,
            content=content,
            first_user_message=first_user_message,
            api_key=api_key,
            model=model,
            auto_memory=auto_memory,
            requested_artifacts=ctx.pending_artifacts,
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

    catalogue = await model_service.list_models()
    chosen = [model_service.find(catalogue["models"], m) for m in payload.models]
    chosen = [m for m in chosen if m is not None]
    if len(chosen) < 2:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="models_unavailable")

    # Headroom checked against the most expensive column.
    if not has_headroom(user, max(chosen, key=lambda m: m["creditCost"])):
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="no_credits")

    # Same policy as a single turn.
    policy = await governance.current()
    content = payload.content
    if policy.intent_filter and (hit := governance.blocked_by(content, policy.blocked_categories)):
        await _audit_policy(user, request, "filter.blocked", hit)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"blocked_category:{hit}"
        )
    if policy.pii_masking:
        content, masked = governance.mask(content)
        if masked:
            await _audit_policy(user, request, "pii.masked", f"{masked}건")

    history = await _history(db, session.id)
    db.add(Message(session_id=session.id, role=Role.user, content=content))
    session.updated_at = utcnow()
    if not session.title:
        session.title = content.strip()[:40]
    db.add(session)
    await db.commit()

    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)

    wire = [{"role": m.role.value, "content": m.content} for m in history]
    wire.append({"role": "user", "content": content})
    messages = build_messages(session.kind, wire, with_tools=False, web_search=False)

    return StreamingResponse(
        _run_comparison(
            user_id=user.id,
            api_key=api_key,
            session_id=session.id,
            models=chosen,
            messages=messages,
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
) -> AsyncIterator[str]:
    """Fans out, merges the streams, then settles every column in one transaction.

    Interleaved on one connection: the turn is stored and billed as one thing
    even when a column fails.
    """
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    results: dict[str, dict] = {
        m["id"]: {"model": m["id"], "content": "", "usage": None, "error": None} for m in models
    }

    async def run(model: dict) -> None:
        slot = results[model["id"]]
        try:
            async for event in chat_service.stream_completion(
                model["id"], messages, user_id, api_key
            ):
                if event["type"] == "delta":
                    slot["content"] += event["text"]
                    await queue.put(
                        {"type": "variant", "model": model["id"], "text": event["text"]}
                    )
                elif event["type"] == "usage":
                    slot["usage"] = {k: v for k, v in event.items() if k != "type"}
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
        while (event := await queue.get()) is not None:
            yield chat_service.sse(event)
    finally:
        await task

    variants = [
        {
            "model": r["model"],
            "content": r["content"],
            "credits": r.get("credits", 0),
            "usage": r["usage"],
            "error": r["error"],
        }
        for r in results.values()
    ]
    total = sum(v["credits"] for v in variants)

    # The chosen column is the turn's answer for the next turn; empty content
    # would leave a silent assistant message in the history. First successful
    # column is the default, and the stored answer follows a later choice.
    chosen = next((v for v in variants if v["content"] and not v["error"]), None)
    for variant in variants:
        variant["chosen"] = variant is chosen

    async with SessionLocal() as db:
        db.add(
            Message(
                session_id=session_id,
                role=Role.assistant,
                content=chosen["content"] if chosen else "",
                variants=variants,
                usage={"credits": total},
            )
        )
        settled = await db.get(User, user_id)
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
    message.model = payload.model
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
        try:
            # A `create_artifact` call wins over extraction from the transcript.
            # Both run: a turn can do each once.
            requested_id = await artifact_extract.store_requested(
                db,
                user_id=user_id,
                session_id=session_id,
                project_id=session.project_id,
                requests=requested_artifacts or [],
            )
            extracted_id = await artifact_extract.extract(
                db,
                user_id=user_id,
                session_id=session_id,
                project_id=session.project_id,
                content=content,
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
                written = await auto_memory_service.extract(
                    db,
                    user,
                    user_message=first_user_message,
                    assistant_message=content,
                    api_key=api_key,
                    model=settings.title_model or model["id"],
                )
                if written:
                    log.info("auto-memory wrote %d fact(s) for %s", written, user.email)
            except Exception:  # noqa: BLE001
                log.exception("auto-memory failed for session %s", session_id)

        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            log.exception("enrichment commit failed for session %s", session_id)
            return None
    return artifact_id


async def _audit_policy(user: User, request: Request, action: str, detail: str) -> None:
    """Writes a policy event on its own connection.

    Separate from the turn's transaction: the record of a refusal must survive
    the request.
    """
    async with SessionLocal() as db:
        db.add(
            AuditEvent(
                actor_id=user.id,
                action=action,
                target=user.email,
                detail=detail,
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
) -> AsyncIterator[str]:
    """Drives one deck to completion and settles it.

    Same contract as `_run_report`: the deck is an artifact, not a chat message.
    """
    slides: list[dict] = []
    usage = {"inputTokens": 0, "outputTokens": 0}
    doc_title = ""

    try:
        stream = deck_service.write(request=request, model=model["id"], api_key=api_key)
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

    try:
        stream = report_service.write(request=request, model=model["id"], api_key=api_key)
        async for event in stream:
            if event["type"] == "report":
                sections = event["sections"]
                continue
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
                        "sources": [],
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
