"""What every integration tool shares: the person's account opened for one run, provider
failures said plainly, and times read and written in the instance's time zone."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.integrations import IntegrationAccount
from app.services.integrations import accounts, config
from app.services.integrations.http import AuthExpired, ProviderError
from app.services.tools.base import ToolContext, ToolResult

T = TypeVar("T")

#: what the model is told when a call failed, by `ProviderError.code`
_FAILURES = {
    "reconnect_required": "연결이 끊겼습니다. 설정 › 연결에서 다시 연결해 달라고 안내하세요.",
    "not_connected": "이 계정은 연결되어 있지 않습니다. 설정 › 연결에서 연결해 달라고 안내하세요.",
    "scope_missing": (
        "연결할 때 이 권한을 허용하지 않았습니다. 설정 › 연결에서 다시 연결해 달라고 안내하세요."
    ),
    "feature_off": "관리자가 이 기능을 켜지 않았습니다.",
    "api_disabled": "관리자의 Google Cloud 프로젝트에서 이 API가 켜져 있지 않습니다.",
    "rate_limited": "요청이 많아 잠시 거절되었습니다. 잠시 뒤 다시 시도하세요.",
    "not_found": "찾을 수 없습니다. 주소나 ID를 확인하세요.",
    "forbidden": "이 항목에 접근할 권한이 없습니다.",
    "unreachable": "서비스에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요.",
    "too_large": "파일이 너무 큽니다.",
}


def failure(code: str, detail: str = "") -> ToolResult:
    said = _FAILURES.get(code) or f"요청을 처리하지 못했습니다 ({code})."
    return ToolResult(content=f"오류: {said}" + (f" {detail}" if detail else ""), failed=True,
                      detail=said)


async def with_account(ctx: ToolContext, provider: str, feature: str,
                       work: Callable[[AsyncSession, IntegrationAccount], Awaitable[ToolResult]]
                       ) -> ToolResult:
    """`work` with the person's connected account, or the failure the model passes on."""
    cfg = await config.get(provider)
    if not cfg.allows(feature):
        return failure("feature_off")
    async with SessionLocal() as db:
        row = await accounts.account(db, ctx.user_id, provider)
        if row is None or row.status != "connected":
            return failure("reconnect_required" if row is not None else "not_connected")
        if not accounts.granted(row, feature):
            return failure("scope_missing")
        try:
            return await work(db, row)
        except AuthExpired:
            return failure("reconnect_required")
        except ProviderError as exc:
            return failure(exc.code)


# ── times ────────────────────────────────────────────────────────────────

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def zone() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def moment(text: Any, *, end_of_day: bool = False) -> datetime:
    """An ISO date or date-time as an aware time; no offset means the instance's zone.
    `ValueError("bad_time")` otherwise."""
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("bad_time")
    try:
        if _DAY.match(raw):
            day = date.fromisoformat(raw)
            at = datetime.combine(day + timedelta(days=1) if end_of_day else day, time(0),
                                  tzinfo=zone())
            return at
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("bad_time") from None
    return at if at.tzinfo else at.replace(tzinfo=zone())


_WEEKDAYS = "월화수목금토일"


def local(at: datetime) -> datetime:
    return at.astimezone(zone())


def say(at: datetime, *, with_day: bool = True) -> str:
    """`11/16(월) 10:00` in the instance's zone."""
    at = local(at)
    clock = at.strftime("%H:%M")
    return f"{at.month}/{at.day}({_WEEKDAYS[at.weekday()]}) {clock}" if with_day else clock


_EMAIL = re.compile(r"^[^@\s,;<>]{1,64}@[^@\s,;<>]{1,255}\.[A-Za-z]{2,}$")


def emails(raw: Any, *, limit: int = 50) -> list[str]:
    """Addresses from a list (or a comma list); `ValueError("bad_email")` on one that is not."""
    items = raw if isinstance(raw, list) else str(raw or "").replace(";", ",").split(",")
    out: list[str] = []
    for item in items:
        value = str(item or "").strip().strip("<>").lower()
        if not value:
            continue
        if not _EMAIL.match(value):
            raise ValueError("bad_email")
        if value not in out:
            out.append(value)
    return out[:limit]


def clip(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]
