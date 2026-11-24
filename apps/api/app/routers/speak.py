"""Voice conversation: one block of an answer in, its speech out. Self-hosted models only."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Response, status

from app.core.deps import CurrentUser, DbSession
from app.routers.sessions import _allowed_models, _strict_model
from app.schemas.chat import SpeakRequest
from app.services import audiogen, spoken_rewrite
from app.services import litellm as litellm_service
from app.services import models as model_service
from app.services.credits import charge_for_tokens, has_headroom, settle

log = logging.getLogger(__name__)

router = APIRouter(prefix="/audio", tags=["audio"])

#: sentences per minute per account (this replica); a spoken answer is a few a minute
_PER_MINUTE = 90
_recent: dict[str, deque[float]] = defaultdict(deque)


def _limited(user_id: str) -> bool:
    now = time.monotonic()
    window = _recent[user_id]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= _PER_MINUTE:
        return True
    window.append(now)
    return False


async def _spoken(payload: SpeakRequest, catalogue: dict, api_key: str) -> tuple[str, int, str]:
    """`(text to read, credits for the rewrite, the helper model)`: the block put into spoken
    words by a self-hosted helper model, else the cleaned text the client sent. Only
    `strict-local/` helper models are asked, so the text stays inside the deployment."""
    try:
        for wanted in await spoken_rewrite.models_for(strict=True):
            helper = model_service.find(catalogue["models"], wanted)
            if helper is None or "chat" not in (helper.get("kinds") or []):
                continue
            spoken, usage = await spoken_rewrite.rewrite(payload.source or "", wanted, api_key)
            charged = charge_for_tokens(helper, usage["inputTokens"], usage["outputTokens"])
            return (spoken or payload.text), charged, wanted
    except Exception:  # noqa: BLE001 - the voice never stops for a rewrite
        log.info("spoken rewrite failed", exc_info=True)
    return payload.text, 0, ""


@router.post("/speak")
async def speak(payload: SpeakRequest, user: CurrentUser, db: DbSession) -> Response:
    """Reads text aloud as WAV, not stored. Only a self-hosted speech model is used, so no text
    leaves the deployment whatever the conversation's model was."""
    if _limited(user.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="speak_rate_limited"
        )
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
    text = payload.text
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
            await db.commit()
        if rewrite_credits:
            settle(
                db, user, rewrite_credits, reason="audio.speak.rewrite", model=rewrite_model,
                surface="av",
            )
            await db.commit()
    return Response(
        content=audio.data, media_type=audio.mime, headers={"Cache-Control": "no-store"}
    )
