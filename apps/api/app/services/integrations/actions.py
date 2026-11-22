"""Write tools wait for the person: a write stores what it would send and shows a card; the
provider is called only on 확인 (`confirm`). The model is told the write is waiting, never that
it happened. Every confirmed or failed write is in the audit log."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import Any

from sqlmodel import col, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import SessionLocal
from app.models.integrations import ActionStatus, IntegrationAccount, IntegrationAction
from app.models.user import User, utcnow
from app.services.integrations import accounts, config
from app.services.integrations.http import ProviderError
from app.services.tools.base import ToolContext, ToolResult

log = logging.getLogger(__name__)

#: a card not answered in this long can no longer be confirmed
EXPIRES = timedelta(hours=24)

#: what the model is told after proposing; the card says the rest
WAITING = (
    "Nothing was written yet. A confirmation card with these details is now shown to the "
    "person; the write happens only if they press 확인 there. Tell them briefly what the card "
    "will do and that it waits for their confirmation. Do not say it is done, and do not "
    "call this tool again for the same request."
)


@dataclass(frozen=True, slots=True)
class Executor:
    provider: str
    #: the feature the write belongs to (`config.FEATURES`), checked again at confirm time
    feature: str
    #: fields the person may change on the card before confirming
    editable: frozenset[str]
    #: `(db, user, account, payload) -> {"id", "url", "label"}`
    run: Callable[[AsyncSession, User, IntegrationAccount, dict[str, Any]],
                  Awaitable[dict[str, Any]]]
    #: the payload with the person's edits applied and checked; raises `ValueError(code)`
    edit: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None


EXECUTORS: dict[str, Executor] = {}


def register(kind: str, executor: Executor) -> None:
    EXECUTORS[kind] = executor


async def propose(ctx: ToolContext, provider: str, kind: str, payload: dict[str, Any],
                  summary: str) -> ToolResult:
    """A waiting write and its card; `summary` is the one line the model gets back."""
    async with SessionLocal() as db:
        row = IntegrationAction(user_id=ctx.user_id, session_id=ctx.session_id or None,
                                provider=provider, kind=kind, payload=payload)
        db.add(row)
        await db.commit()
        action_id = row.id
    return ToolResult(
        content=f"{summary}\n\n{WAITING}",
        detail="확인을 기다립니다",
        trace={"confirm": {"actionId": action_id, "kind": kind, "provider": provider}},
    )


def view(row: IntegrationAction) -> dict[str, Any]:
    executor = EXECUTORS.get(row.kind)
    return {
        "id": row.id,
        "provider": row.provider,
        "kind": row.kind,
        "status": _status(row),
        "payload": row.payload or {},
        "editable": sorted(executor.editable) if executor else [],
        "result": row.result,
        "error": row.error,
        "createdAt": row.created_at,
        "decidedAt": row.decided_at,
    }


def _status(row: IntegrationAction) -> str:
    if row.status == ActionStatus.pending.value and _expired(row):
        return "expired"
    return row.status


def _expired(row: IntegrationAction) -> bool:
    created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)
    return utcnow() - created > EXPIRES


class ActionRefused(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


async def owned(db: AsyncSession, user: User, action_id: str) -> IntegrationAction:
    row = await db.get(IntegrationAction, action_id)
    if row is None or row.user_id != user.id:
        raise ActionRefused("not_found", 404)
    return row


async def confirm(db: AsyncSession, user: User, row: IntegrationAction,
                  edits: dict[str, Any] | None = None, *, ip: str = "",
                  user_agent: str = "") -> IntegrationAction:
    """Runs the write the card shows (with the person's edits), once."""
    if row.status != ActionStatus.pending.value:
        raise ActionRefused("action_decided")
    if _expired(row):
        raise ActionRefused("action_expired")
    executor = EXECUTORS.get(row.kind)
    if executor is None:
        raise ActionRefused("unknown_action", 422)
    payload = dict(row.payload or {})
    edits = edits or {}
    if extra := sorted(set(edits) - executor.editable):
        raise ActionRefused(f"not_editable:{','.join(extra)}", 422)
    try:
        # checked even with no edits: what the card would send must still be sendable
        payload = executor.edit(payload, edits) if executor.edit else {**payload, **edits}
    except ValueError as exc:
        raise ActionRefused(str(exc), 422) from None
    account = await accounts.account(db, user.id, row.provider)
    if account is None or account.status != "connected":
        raise ActionRefused("not_connected")
    cfg = await config.get(row.provider)
    if not cfg.allows(executor.feature) or not accounts.granted(account, executor.feature):
        # the administrator turned the feature off (or the grant lacks it) since the card
        raise ActionRefused("feature_off")
    # claimed in one conditional update before the call: a second press, tab or replica
    # finds it decided and sends nothing
    claimed = await db.exec(
        update(IntegrationAction)
        .where(col(IntegrationAction.id) == row.id,
               col(IntegrationAction.status) == ActionStatus.pending.value)
        .values(status=ActionStatus.done.value, payload=payload, decided_at=utcnow()))
    await db.commit()
    if claimed.rowcount != 1:
        raise ActionRefused("action_decided")
    await db.refresh(row)
    try:
        result = await executor.run(db, user, account, payload)
    except Exception as exc:  # noqa: BLE001 — a failed write is a state on the card
        code = (exc.code if isinstance(exc, ProviderError)
                else str(exc) if isinstance(exc, ValueError) else "write_failed")
        if not isinstance(exc, (ProviderError, ValueError)):
            log.warning("integration write %s failed (%s)", row.kind, type(exc).__name__)
        row.status = ActionStatus.failed.value
        row.error = code[:300]
        db.add(row)
        accounts.audit(db, user.id, "integration.write_failed", row.provider,
                       detail=row.kind, metadata={"kind": row.kind, "action": row.id,
                                                  "error": code[:80]},
                       ip=ip, user_agent=user_agent)
        await db.commit()
        await db.refresh(row)
        return row
    row.result = result
    db.add(row)
    accounts.audit(db, user.id, "integration.write", row.provider, detail=row.kind,
                   metadata={"kind": row.kind, "action": row.id,
                             "session": row.session_id or "", "id": str(result.get("id") or "")},
                   ip=ip, user_agent=user_agent)
    await db.commit()
    await db.refresh(row)
    return row


async def cancel(db: AsyncSession, row: IntegrationAction) -> IntegrationAction:
    if row.status != ActionStatus.pending.value:
        raise ActionRefused("action_decided")
    row.status = ActionStatus.cancelled.value
    row.decided_at = utcnow()
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row
