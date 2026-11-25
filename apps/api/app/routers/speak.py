"""Voice conversation: one block of an answer in, its speech out. Self-hosted models only."""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Response, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.models.user import User
from app.routers.sessions import _allowed_models, _strict_model
from app.schemas.chat import SpeakRequest
from app.services import audiogen, spoken_rewrite
from app.services import litellm as litellm_service
from app.services import models as model_service
from app.services.credits import charge_for_tokens, has_headroom, settle

log = logging.getLogger(__name__)

router = APIRouter(prefix="/audio", tags=["audio"])

#: sentences per minute per account across the deployment: each API replica counts on its own,
#: so each takes its share
_PER_MINUTE = max(1, math.ceil(90 / max(1, settings.api_replicas)))
#: warm-up clips per minute per account (one is enough; a second covers a reload)
_WARM_PER_MINUTE = 2
#: clips being made at once per account (this replica): one person cannot hold the speech
#: server for everyone
_MAX_INFLIGHT = 3
#: the throwaway clip that loads the model: the server's own phrase, never the caller's text
_WARM_PHRASE = "네."
_recent: dict[str, deque[float]] = defaultdict(deque)
_warm_recent: dict[str, deque[float]] = defaultdict(deque)
_inflight: dict[str, int] = defaultdict(int)

#: the rewrite step is skipped for a while after this many failures in a row, so a helper model
#: that is down costs one wait, not one per sentence
_REWRITE_STRIKES = 3
_REWRITE_PAUSE_SEC = 60.0
_rewrite_failures = 0
_rewrite_paused_until = 0.0


def _within(book: dict[str, deque[float]], user_id: str, cap: int) -> bool:
    """True if the account may take one more now (and takes it)."""
    now = time.monotonic()
    window = book[user_id]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= cap:
        return False
    window.append(now)
    if len(book) > 1000:  # accounts that went quiet leave nothing behind
        for key in [k for k, w in book.items() if not w or now - w[-1] > 60]:
            book.pop(key, None)
    return True


def _limited(user_id: str) -> bool:
    return not _within(_recent, user_id, _PER_MINUTE)


async def _spoken(payload: SpeakRequest, catalogue: dict, api_key: str) -> tuple[str, int, str]:
    """`(text to read, credits for the rewrite, the helper model)`: the block put into spoken
    words by a self-hosted helper model, else the cleaned text the client sent. Only
    `strict-local/` helper models are asked, so the text stays inside the deployment."""
    global _rewrite_failures, _rewrite_paused_until
    if time.monotonic() < _rewrite_paused_until:
        return payload.text, 0, ""
    try:
        for wanted in await spoken_rewrite.models_for(strict=True):
            helper = model_service.find(catalogue["models"], wanted)
            if helper is None or "chat" not in (helper.get("kinds") or []):
                continue
            spoken, usage = await spoken_rewrite.rewrite(payload.source or "", wanted, api_key)
            charged = charge_for_tokens(helper, usage["inputTokens"], usage["outputTokens"])
            if spoken:
                _rewrite_failures = 0
            else:
                _rewrite_failures += 1
                if _rewrite_failures >= _REWRITE_STRIKES:
                    _rewrite_paused_until = time.monotonic() + _REWRITE_PAUSE_SEC
                    _rewrite_failures = 0
            return (spoken or payload.text), charged, wanted
    except Exception:  # noqa: BLE001 - the voice never stops for a rewrite
        log.info("spoken rewrite failed", exc_info=True)
    return payload.text, 0, ""


@router.post("/speak")
async def speak(payload: SpeakRequest, user: CurrentUser, db: DbSession) -> Response:
    """Reads text aloud as WAV, not stored. Only a self-hosted speech model is used, so no text
    leaves the deployment whatever the conversation's model was."""
    if payload.warm:
        if not _within(_warm_recent, user.id, _WARM_PER_MINUTE):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="speak_rate_limited"
            )
    elif _limited(user.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="speak_rate_limited"
        )
    if _inflight[user.id] >= _MAX_INFLIGHT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="speak_rate_limited"
        )
    _inflight[user.id] += 1
    try:
        return await _speak(payload, user, db)
    finally:
        _inflight[user.id] -= 1
        if _inflight[user.id] <= 0:
            _inflight.pop(user.id, None)


async def _speak(payload: SpeakRequest, user: User, db: AsyncSession) -> Response:
    catalogue = await model_service.list_models()
    usable = [
        m
        for m in _allowed_models(user, catalogue["models"], kind="av")
        if m.get("speechEndpoint") and _strict_model(m)
    ]
    if not usable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no_speech_model"
        )
    default = (catalogue.get("defaultAvModelByMode") or {}).get("audio") or ""
    model = model_service.find(usable, payload.model or default) or sorted(
        usable, key=model_service.fallback_order
    )[0]
    voices = model.get("voices") or []
    voice = payload.voice if payload.voice in voices else audiogen.default_voice(model["id"])

    if not payload.warm and not has_headroom(user, model):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )
    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    base_url, api_key = await litellm_service.credentials_for(user)
    text = _WARM_PHRASE if payload.warm else payload.text
    if payload.spoken and payload.source and not payload.warm:
        text, rewrite_credits, rewrite_model = await _spoken(payload, catalogue, api_key)
    else:
        rewrite_credits, rewrite_model = 0, ""
    try:
        audio = await audiogen.generate(
            base_url=base_url,
            api_key=api_key,
            model=model["id"],
            prompt=text,
            speech=True,
            voice=voice,
            speech_endpoint=True,
        )
    except audiogen.AudioError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    if not payload.warm:
        charged = max(
            charge_for_tokens(model, audio.input_tokens, audio.output_tokens),
            int(model.get("creditPerCall") or 0),
        )
        if charged:
            settle(db, user, charged, reason="audio.speak", model=model["id"], surface="av")
        if rewrite_credits:
            settle(
                db, user, rewrite_credits, reason="audio.speak.rewrite", model=rewrite_model,
                surface="av",
            )
        if charged or rewrite_credits:
            await db.commit()
    return Response(
        content=audio.data, media_type=audio.mime, headers={"Cache-Control": "no-store"}
    )
