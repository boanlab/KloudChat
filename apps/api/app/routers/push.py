"""Web push devices: subscribe, list, remove; the VAPID public key; a test notice."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field
from sqlalchemy import delete, func
from sqlmodel import select

from app.core.deps import CurrentUser, DbSession
from app.models.push import PushSubscription
from app.models.user import User
from app.schemas.auth import Wire
from app.services import push as push_service

router = APIRouter(prefix="/push", tags=["push"])

#: devices per account; the oldest goes when a new one joins
MAX_DEVICES = 20


class SubscriptionKeys(Wire):
    p256dh: str = Field(min_length=80, max_length=120)
    auth: str = Field(min_length=16, max_length=64)


class SubscriptionIn(Wire):
    endpoint: str = Field(min_length=12, max_length=1000)
    keys: SubscriptionKeys
    #: the screen's language on this device; notices to the account are worded in it
    language: Literal["ko", "en"] | None = None


class EndpointIn(Wire):
    endpoint: str = Field(min_length=12, max_length=1000)


class DeviceOut(Wire):
    id: str
    device: str
    created_at: datetime
    last_sent_at: datetime | None = None


class PushConfigOut(Wire):
    public_key: str
    #: event → on by default
    events: dict[str, bool]


def device_label(user_agent: str) -> str:
    """「Chrome · Windows」 from a User-Agent; coarse on purpose."""
    ua = user_agent or ""
    if "Edg/" in ua:
        browser = "Edge"
    elif "SamsungBrowser" in ua:
        browser = "Samsung Internet"
    elif "Firefox/" in ua or "FxiOS" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua or "CriOS" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = "브라우저"
    if "iPhone" in ua or "iPad" in ua:
        system = "iOS"
    elif "Android" in ua:
        system = "Android"
    elif "Windows" in ua:
        system = "Windows"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        system = "macOS"
    elif "CrOS" in ua:
        system = "ChromeOS"
    elif "Linux" in ua:
        system = "Linux"
    else:
        return browser
    return f"{browser} · {system}"


@router.get("/config", response_model=PushConfigOut)
async def push_config(user: CurrentUser, db: DbSession):
    _, public = await push_service.vapid_keys(db)
    return PushConfigOut(public_key=public, events=dict(push_service.EVENTS))


@router.get("/subscriptions", response_model=list[DeviceOut])
async def list_devices(user: CurrentUser, db: DbSession):
    rows = (
        await db.exec(
            select(PushSubscription)
            .where(PushSubscription.user_id == user.id)
            .order_by(PushSubscription.created_at)
        )
    ).all()
    return [DeviceOut.model_validate(row, from_attributes=True) for row in rows]


@router.post("/subscriptions", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def subscribe(payload: SubscriptionIn, request: Request, user: CurrentUser, db: DbSession):
    endpoint = payload.endpoint.strip()
    if not push_service.endpoint_allowed(endpoint):
        raise HTTPException(status_code=422, detail="push_endpoint_not_allowed")
    try:
        push_service.webpush_key_check(payload.keys.p256dh, payload.keys.auth)
    except ValueError:
        raise HTTPException(status_code=422, detail="push_keys_invalid") from None
    label = device_label(request.headers.get("User-Agent", ""))
    row = (
        await db.exec(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    ).first()
    if row is None:
        row = PushSubscription(
            user_id=user.id,
            endpoint=endpoint,
            p256dh=payload.keys.p256dh,
            auth=payload.keys.auth,
            device=label,
        )
    else:
        # same browser, maybe another account signed in now: it follows the current one
        row.user_id = user.id
        row.p256dh = payload.keys.p256dh
        row.auth = payload.keys.auth
        row.device = label
    db.add(row)
    if payload.language:
        owner = await db.get(User, user.id)
        if owner is not None:
            owner.preferences = {**(owner.preferences or {}), "ui_language": payload.language}
            db.add(owner)
    await db.flush()
    count = (
        await db.exec(
            select(func.count())
            .select_from(PushSubscription)
            .where(PushSubscription.user_id == user.id)
        )
    ).one()
    if count > MAX_DEVICES:
        oldest = (
            await db.exec(
                select(PushSubscription.id)
                .where(PushSubscription.user_id == user.id, PushSubscription.id != row.id)
                .order_by(PushSubscription.created_at)
                .limit(count - MAX_DEVICES)
            )
        ).all()
        await db.exec(delete(PushSubscription).where(PushSubscription.id.in_(list(oldest))))
    await db.commit()
    await db.refresh(row)
    return DeviceOut.model_validate(row, from_attributes=True)


@router.delete("/subscriptions/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_device(device_id: str, user: CurrentUser, db: DbSession):
    row = await db.get(PushSubscription, device_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="device_not_found")
    await db.delete(row)
    await db.commit()


@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(payload: EndpointIn, user: CurrentUser, db: DbSession):
    """This browser stops; by endpoint, as the page knows it."""
    await db.exec(
        delete(PushSubscription).where(
            PushSubscription.endpoint == payload.endpoint.strip(),
            PushSubscription.user_id == user.id,
        )
    )
    await db.commit()


@router.post("/test")
async def send_test(user: CurrentUser):
    """A test notice to every device of the account; counts devices that took it."""
    sent = await push_service.deliver(
        user.id,
        push_service.Notice(
            event="test", title="알림이 켜졌습니다", label="이 기기로 알림을 받습니다", tag="test"
        ),
    )
    return {"sent": sent}
