"""Usage, storage, audit and governance routes.

Usage is one stream of billable events: assistant turns carry the request and
the model, credit-ledger rows carry the cost. Every breakdown is that stream
grouped a different way, so totals and bars always agree.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import DateTime, String, case, cast, func, literal, or_, union_all
from sqlmodel import col, select

from app.core.config import settings
from app.core.deps import AdminUser, CurrentUser, DbSession, client_ip
from app.models.chat import ChatSession, Message, Role
from app.models.governance import Governance
from app.models.user import ApiKey, AuditEvent, CreditLedger, User, UserStatus, utcnow
from app.schemas.admin import GovernanceIn
from app.services import adaptive_routing, geoip, governance, settings_store
from app.services import credits as credits_service
from app.services import files as file_service
from app.services import litellm as litellm_service
from app.services import models as model_service
from app.services import storage as storage_service

router = APIRouter(prefix="/admin", tags=["admin"])
#: Caller-scoped usage. Separate router so no admin-only route can land here by mistake.
me_router = APIRouter(prefix="/me", tags=["usage"])

#: Ledger reasons with no assistant turn behind them; each row counts as one request.
MEDIA_REASONS = ("image.generate", "image.chart", "audio.generate", "video.generate")

#: Free work measured in `units` (seconds, chunks); each row counts as one request.
UNIT_REASONS = ("speech.transcribe", "index.embed", "index.search")


#: Cycle boundary is local midnight, not a UTC month boundary.
_cycle_start = credits_service.cycle_start


def _since(days: int) -> datetime:
    """Midnight `days-1` days ago, so a 7-day window is seven whole days."""
    start = datetime.now(UTC) - timedelta(days=days - 1)
    return start.replace(hour=0, minute=0, second=0, microsecond=0)


def _every_day(since: datetime, days: int, rows) -> list[dict]:
    """One entry per day of the window, in order, zeros where nothing happened."""
    by_date = {day.date().isoformat(): (int(c), int(n)) for day, c, n in rows}
    return [
        {
            "date": date,
            "credits": by_date.get(date, (0, 0))[0],
            "requests": by_date.get(date, (0, 0))[1],
        }
        for date in ((since + timedelta(days=i)).date().isoformat() for i in range(days))
    ]


#: Model a ledger row paid. Rows without one fall back to the session model
#: for media reasons only; a conversation's stored model is just the current one.
_SPEND_MODEL = func.nullif(
    func.coalesce(
        col(CreditLedger.model),
        case(
            (col(CreditLedger.reason).in_(MEDIA_REASONS), col(ChatSession.model)),
            else_=None,
        ),
        "",
    ),
    "",
)

#: Surface a ledger row came from; rows without one fall back to the session.
#: `sessions.kind` is a Postgres enum, so it is cast to match the text column.
_SPEND_SURFACE = func.coalesce(col(CreditLedger.surface), cast(col(ChatSession.kind), String))


def _events(since: datetime, user_id: str | None = None):
    """Union of assistant turns and ledger spend, both shaped as
    (day, model, kind, user_id, credits, requests, units).
    """
    turns = (
        select(
            func.date_trunc("day", col(Message.created_at), type_=DateTime(timezone=True)).label(
                "day"
            ),
            col(Message.model).label("model"),
            # Enum cast to text so both halves of the union agree.
            cast(col(ChatSession.kind), String).label("kind"),
            col(ChatSession.user_id).label("user_id"),
            # Credits come only from the ledger; counting the turn's copy would double it.
            literal(0).label("credits"),
            literal(1).label("requests"),
            literal(0).label("units"),
        )
        .select_from(Message)
        .join(ChatSession, col(Message.session_id) == col(ChatSession.id))
        .where(Message.role == Role.assistant, Message.created_at >= since)
    )
    spend = (
        select(
            func.date_trunc(
                "day", col(CreditLedger.created_at), type_=DateTime(timezone=True)
            ).label("day"),
            _SPEND_MODEL.label("model"),
            _SPEND_SURFACE.label("kind"),
            col(CreditLedger.user_id).label("user_id"),
            (-col(CreditLedger.delta)).label("credits"),
            case((col(CreditLedger.reason).in_((*MEDIA_REASONS, *UNIT_REASONS)), 1), else_=0).label(
                "requests"
            ),
            func.coalesce(col(CreditLedger.units), 0).label("units"),
        )
        .select_from(CreditLedger)
        # Outer join: some charges belong to no conversation.
        .join(ChatSession, col(CreditLedger.session_id) == col(ChatSession.id), isouter=True)
        # Spend or measured free work; never a refill.
        .where(
            or_(CreditLedger.delta < 0, col(CreditLedger.units).is_not(None)),
            CreditLedger.created_at >= since,
        )
    )
    if user_id is not None:
        turns = turns.where(ChatSession.user_id == user_id)
        spend = spend.where(CreditLedger.user_id == user_id)
    return union_all(turns, spend).subquery()


def _unit_of(model: str | None) -> str:
    """Unit name for a model's `units` column: seconds for STT, chunks for embeddings."""
    name = (model or "").lower()
    if "whisper" in name or "stt" in name:
        return "seconds"
    if "bge" in name or "embed" in name:
        return "chunks"
    return ""


def _kind(kind) -> str:
    """Surface name; some drivers hand the enum back raw from a union."""
    return getattr(kind, "value", kind)


@router.get("/usage")
async def usage(admin: AdminUser, db: DbSession, days: int = Query(7, ge=1, le=90)):
    since = _since(days)
    events = _events(since)
    credits = func.coalesce(func.sum(events.c.credits), 0)
    requests = func.coalesce(func.sum(events.c.requests), 0)
    units = func.coalesce(func.sum(events.c.units), 0)
    people = func.count(func.distinct(events.c.user_id))

    spent, request_count, active_users = (await db.exec(select(credits, requests, people))).one()

    daily = (
        await db.exec(
            select(events.c.day, credits, requests).group_by(events.c.day).order_by(events.c.day)
        )
    ).all()

    by_model = (
        await db.exec(
            select(events.c.model, credits, requests, people, units)
            .where(events.c.model.is_not(None))
            .group_by(events.c.model)
            .order_by(credits.desc(), requests.desc())
        )
    ).all()

    by_surface = (
        await db.exec(
            select(events.c.kind, credits, requests)
            .where(events.c.kind.is_not(None))
            .group_by(events.c.kind)
            .order_by(credits.desc())
        )
    ).all()

    # Residues: spend with no single model, and spend outside any conversation.
    other_credits, loose_credits, loose_requests = (
        await db.exec(
            select(
                func.coalesce(
                    func.sum(case((events.c.model.is_(None), events.c.credits), else_=0)), 0
                ),
                func.coalesce(
                    func.sum(case((events.c.kind.is_(None), events.c.credits), else_=0)), 0
                ),
                func.coalesce(
                    func.sum(case((events.c.kind.is_(None), events.c.requests), else_=0)), 0
                ),
            )
        )
    ).one()

    top_users = (
        await db.exec(
            select(
                User.id,
                User.name,
                User.email,
                User.monthly_credits,
                credits.label("spent"),
                requests.label("asked"),
            )
            .select_from(events)
            .join(User, events.c.user_id == col(User.id))
            .group_by(col(User.id), col(User.name), col(User.email), col(User.monthly_credits))
            .order_by(credits.desc(), requests.desc())
        )
    ).all()

    # Active accounts only.
    allocated = (
        await db.exec(
            select(func.coalesce(func.sum(User.monthly_credits), 0)).where(
                User.status == UserStatus.active
            )
        )
    ).one()

    surfaces = [{"kind": _kind(k), "credits": int(c), "requests": int(n)} for k, c, n in by_surface]
    if loose_credits or loose_requests:
        surfaces.append(
            {"kind": "other", "credits": int(loose_credits), "requests": int(loose_requests)}
        )

    return {
        "days": days,
        "since": since,
        "totals": {
            "credits": int(spent),
            "requests": int(request_count),
            "activeUsers": int(active_users),
            "allocatedCredits": int(allocated),
            # Spend no single model can be named for.
            "otherCredits": int(other_credits),
        },
        "daily": _every_day(since, days, daily),
        "byModel": [
            {
                "model": m,
                "credits": int(c),
                "requests": int(n),
                "users": int(u),
                "units": int(x),
                "unit": _unit_of(m),
            }
            for m, c, n, u, x in by_model
        ],
        "bySurface": surfaces,
        # Every account with activity in the window, most spent first.
        "topUsers": [
            {
                "id": uid,
                "name": name,
                "email": email,
                "credits": int(spent_u),
                "requests": int(asked),
                "allowance": int(allowance),
            }
            for uid, name, email, allowance, spent_u, asked in top_users
        ],
    }


@router.get("/storage")
async def storage(admin: AdminUser, db: DbSession):
    """Disk use per user under `file_storage_dir/<user id>/`, walked on request."""
    root = file_service.storage_root()
    per_user: dict[str, tuple[int, int]] = {}
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        size = files = 0
        for path in directory.rglob("*"):
            if path.is_file():
                files += 1
                size += path.stat().st_size
        per_user[directory.name] = (size, files)
    names = {
        row.id: row
        for row in (
            await db.exec(select(User).where(col(User.id).in_(list(per_user) or ["-"])))
        ).all()
    }
    disk = shutil.disk_usage(root)
    orphans = await storage_service.reclaim(db, dry_run=True)
    return {
        "path": str(root),
        "usedBytes": sum(size for size, _ in per_user.values()),
        "files": sum(files for _, files in per_user.values()),
        "diskTotalBytes": disk.total,
        "diskFreeBytes": disk.free,
        "reclaimAt": settings.storage_reclaim_at,
        "orphanBytes": orphans.orphan_bytes,
        "orphanFiles": orphans.orphan_files,
        "byUser": sorted(
            (
                {
                    "id": user_id,
                    "name": names[user_id].name if user_id in names else "(삭제된 계정)",
                    "email": names[user_id].email if user_id in names else "",
                    "bytes": size,
                    "files": files,
                }
                for user_id, (size, files) in per_user.items()
            ),
            key=lambda row: -row["bytes"],
        ),
    }


@router.post("/storage/reclaim")
async def reclaim_storage(admin: AdminUser, db: DbSession, request: Request):
    """Removes the files of deleted accounts now, regardless of the fill mark."""
    result = await storage_service.reclaim(db, threshold=1e-9)
    db.add(
        AuditEvent(
            actor_id=admin.id,
            action="storage.reclaim.manual",
            target="파일 저장소",
            detail=f"{result.freed_files}개, {result.freed_bytes:,} B",
            ip=client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:400],
        )
    )
    await db.commit()
    return {
        "freedBytes": result.freed_bytes,
        "freedFiles": result.freed_files,
        "fillBefore": result.fill_before,
        "fillAfter": result.fill_after,
    }


async def _api_key_spend(db: DbSession, user: User) -> list[dict]:
    """Spend per key the user issued. Empty when the proxy does not answer."""
    rows = (
        await db.exec(
            select(ApiKey)
            .where(ApiKey.user_id == user.id, col(ApiKey.revoked_at).is_(None))
            .order_by(col(ApiKey.created_at))
        )
    ).all()
    if not rows:
        return []

    spends = await asyncio.gather(
        *(litellm_service.key_spend(settings_store.decrypt_secret(r.secret)) for r in rows),
        return_exceptions=True,
    )
    out = []
    for row, spend in zip(rows, spends, strict=True):
        if not isinstance(spend, dict):
            continue
        out.append(
            {
                "id": row.id,
                "name": row.name,
                "preview": row.preview,
                "spendUsd": round(spend["spend"], 4),
                "credits": int(spend["spend"] * settings.credits_per_usd),
                "budgetUsd": spend["maxBudget"],
            }
        )
    return out


@me_router.get("/usage")
async def my_usage(user: CurrentUser, db: DbSession, days: int = Query(30, ge=1, le=90)):
    """The caller's own spending, from the same event stream as the admin view."""
    since = _since(days)
    events = _events(since, user_id=user.id)
    credits = func.coalesce(func.sum(events.c.credits), 0)
    requests = func.coalesce(func.sum(events.c.requests), 0)
    units = func.coalesce(func.sum(events.c.units), 0)

    spent, request_count = (await db.exec(select(credits, requests))).one()

    daily = (
        await db.exec(
            select(events.c.day, credits, requests).group_by(events.c.day).order_by(events.c.day)
        )
    ).all()

    by_model = (
        await db.exec(
            select(events.c.model, credits, requests, units)
            .where(events.c.model.is_not(None))
            .group_by(events.c.model)
            .order_by(credits.desc(), requests.desc())
        )
    ).all()

    by_surface = (
        await db.exec(
            select(events.c.kind, credits, requests)
            .where(events.c.kind.is_not(None))
            .group_by(events.c.kind)
            .order_by(credits.desc())
        )
    ).all()

    other_credits, loose_credits, loose_requests = (
        await db.exec(
            select(
                func.coalesce(
                    func.sum(case((events.c.model.is_(None), events.c.credits), else_=0)), 0
                ),
                func.coalesce(
                    func.sum(case((events.c.kind.is_(None), events.c.credits), else_=0)), 0
                ),
                func.coalesce(
                    func.sum(case((events.c.kind.is_(None), events.c.requests), else_=0)), 0
                ),
            )
        )
    ).one()

    # Cycle figure is independent of the window.
    cycle_used = (
        await db.exec(
            select(-func.coalesce(func.sum(CreditLedger.delta), 0))
            .where(CreditLedger.user_id == user.id)
            .where(CreditLedger.delta < 0)
            .where(CreditLedger.created_at >= _cycle_start())
        )
    ).one()
    # Scalar on some drivers, one-element row on others.
    cycle_used = int(cycle_used if not hasattr(cycle_used, "__len__") else cycle_used[0])

    surfaces = [{"kind": _kind(k), "credits": int(c), "requests": int(n)} for k, c, n in by_surface]
    if loose_credits or loose_requests:
        surfaces.append(
            {"kind": "other", "credits": int(loose_credits), "requests": int(loose_requests)}
        )

    return {
        "days": days,
        "since": since,
        # Proxy-side spend through issued keys; shown beside, never added to, the ledger figures.
        "apiKeys": await _api_key_spend(db, user),
        "totals": {
            "credits": int(spent),
            "requests": int(request_count),
            # Spend no single model can be named for.
            "otherCredits": int(other_credits),
        },
        "cycle": {
            "allowance": int(user.monthly_credits or 0),
            "used": int(cycle_used),
            "remaining": max(0, int(user.monthly_credits or 0) - int(cycle_used)),
        },
        "daily": _every_day(since, days, daily),
        "byModel": [
            {
                "model": m,
                "credits": int(c),
                "requests": int(n),
                "units": int(x),
                "unit": _unit_of(m),
            }
            for m, c, n, x in by_model
        ],
        "bySurface": surfaces,
    }


@router.get("/audit")
async def audit_log(
    admin: AdminUser,
    db: DbSession,
    limit: int = Query(100, ge=1, le=500),
    severity: str | None = None,
):
    """Audit events, newest first."""
    query = select(AuditEvent).order_by(col(AuditEvent.at).desc()).limit(limit)
    if severity:
        query = query.where(AuditEvent.severity == severity)
    rows = (await db.exec(query)).all()

    actor_ids = {r.actor_id for r in rows if r.actor_id}
    actors: dict[str, str] = {}
    if actor_ids:
        actors = {
            u.id: u.email
            for u in (await db.exec(select(User).where(col(User.id).in_(actor_ids)))).all()
        }

    return [
        {
            "id": r.id,
            "at": r.at,
            "actor": actors.get(r.actor_id or "", r.actor_id or "시스템"),
            "action": r.action,
            "target": r.target,
            "detail": r.detail,
            "metadata": r.event_metadata,
            "ip": r.ip,
            # Empty unless a GeoLite2 database is configured.
            "region": geoip.lookup(r.ip),
            "userAgent": r.user_agent,
            "severity": r.severity,
        }
        for r in rows
    ]


@router.get("/governance")
async def get_governance(admin: AdminUser, db: DbSession):
    policy = await db.get(Governance, "default")
    policy = policy or Governance()
    catalogue = await model_service.list_models()
    configured_safe_ids = set(policy.privacy_safe_model_ids or [])
    ordered_safe_ids = [
        model["id"]
        for model in catalogue["models"]
        if model["id"] in configured_safe_ids
        and model.get("dataBoundary") == "self_hosted"
        and model.get("strictLocal") is True
        and "chat" in model.get("kinds", [])
    ]
    return {
        "piiMasking": policy.pii_masking,
        "externalDataGuard": policy.external_data_guard,
        "allowUserRawExternal": policy.allow_user_raw_external,
        "privacySafeModelIds": ordered_safe_ids,
        "adaptiveRoutingEnabled": policy.adaptive_routing_enabled,
        "adaptiveClassifierModelId": policy.adaptive_classifier_model_id,
        "adaptiveEconomyModelIds": list(policy.adaptive_economy_model_ids or []),
        "adaptiveQualityEnabled": policy.adaptive_quality_enabled,
        "adaptiveQualityModelIds": list(policy.adaptive_quality_model_ids or []),
        "outlineModelId": policy.outline_model_id,
        "intentFilter": policy.intent_filter,
        "blockedCategories": list(policy.blocked_categories or []),
        "retentionDays": policy.retention_days,
        "idleTimeoutMinutes": policy.idle_timeout_minutes,
    }


@router.put("/governance")
async def put_governance(payload: GovernanceIn, request: Request, admin: AdminUser, db: DbSession):
    """Writes the policy and applies retention retroactively."""
    policy = await db.get(Governance, "default") or Governance(id="default")
    patch = payload.model_dump(exclude_unset=True)
    if "outline_model_id" in patch:
        # Empty clears it; a model that cannot write documents is refused here.
        wanted = (patch["outline_model_id"] or "").strip()
        patch["outline_model_id"] = wanted or None
        if wanted:
            catalogue = await model_service.list_models()
            usable = {
                model["id"]
                for model in catalogue["models"]
                if {"slides", "report"} & set(model.get("kinds") or [])
            }
            if wanted not in usable:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="outline_model_cannot_write_documents",
                )
    if "privacy_safe_model_ids" in patch:
        catalogue = await model_service.list_models()
        strict_order = [
            model["id"]
            for model in catalogue["models"]
            if model.get("dataBoundary") == "self_hosted"
            and model.get("strictLocal") is True
            and "chat" in model.get("kinds", [])
        ]
        strict_ids = set(strict_order)
        requested = list(dict.fromkeys(patch["privacy_safe_model_ids"] or []))
        invalid = [model_id for model_id in requested if model_id not in strict_ids]
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="privacy_safe_models_must_be_strict_local",
            )
        requested_set = set(requested)
        patch["privacy_safe_model_ids"] = [
            model_id for model_id in strict_order if model_id in requested_set
        ]
    if {
        "adaptive_routing_enabled",
        "adaptive_classifier_model_id",
        "adaptive_economy_model_ids",
        "adaptive_quality_enabled",
        "adaptive_quality_model_ids",
    } & patch.keys():
        catalogue = await model_service.list_models_for_egress()
        models = catalogue["models"]
        by_id = {model["id"]: model for model in models}

        if patch.get("adaptive_classifier_model_id") == "":
            patch["adaptive_classifier_model_id"] = None
        if "adaptive_economy_model_ids" in patch:
            economy_ids = list(patch["adaptive_economy_model_ids"] or [])
            patch["adaptive_economy_model_ids"] = economy_ids
        if "adaptive_quality_model_ids" in patch:
            patch["adaptive_quality_model_ids"] = list(patch["adaptive_quality_model_ids"] or [])

        enabled = patch.get("adaptive_routing_enabled", policy.adaptive_routing_enabled)
        classifier_id = patch.get(
            "adaptive_classifier_model_id", policy.adaptive_classifier_model_id
        )
        economy_ids = list(
            patch.get("adaptive_economy_model_ids", policy.adaptive_economy_model_ids) or []
        )
        quality_ids = list(
            patch.get("adaptive_quality_model_ids", policy.adaptive_quality_model_ids) or []
        )
        quality_on = bool(patch.get("adaptive_quality_enabled", policy.adaptive_quality_enabled))
        if enabled and not classifier_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="adaptive_classifier_required",
            )
        if enabled and not economy_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="adaptive_economy_models_required",
            )
        if enabled and len(economy_ids) > 3:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="adaptive_economy_models_max_three",
            )
        if enabled and len(economy_ids) != len(set(economy_ids)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="adaptive_economy_models_must_be_distinct",
            )
        if enabled and not adaptive_routing.classifier_is_usable(
            by_id.get(classifier_id or ""), allowed_model_ids=set()
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="adaptive_classifier_must_be_zero_cost_strict_local",
            )
        if enabled and any(
            not adaptive_routing.economy_is_baseline_usable(
                by_id.get(model_id), allowed_model_ids=set()
            )
            for model_id in economy_ids
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="adaptive_economy_models_invalid",
            )
        # The quality lane is validated on its own.
        if quality_on and not classifier_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="adaptive_classifier_required",
            )
        if quality_on and not quality_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="adaptive_quality_models_required",
            )
        if quality_on and not adaptive_routing.classifier_is_usable(
            by_id.get(classifier_id or ""), allowed_model_ids=set()
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="adaptive_classifier_must_be_zero_cost_strict_local",
            )
        if len(quality_ids) > 3:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="adaptive_quality_models_max_three",
            )
        if len(quality_ids) != len(set(quality_ids)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="adaptive_quality_models_must_be_distinct",
            )
        if any(model_id not in by_id for model_id in quality_ids):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="adaptive_quality_models_invalid",
            )
    if patch.get("pii_masking", policy.pii_masking):
        # Masking admits no raw-delivery exception; persist that so turning
        # masking off later cannot resurrect a stale allowance.
        patch["allow_user_raw_external"] = False
    for field, value in patch.items():
        setattr(policy, field, value)

    invalidated_raw_preferences = 0
    if ("pii_masking" in patch or "allow_user_raw_external" in patch) and (
        policy.pii_masking or not policy.allow_user_raw_external
    ):
        preference_users = (
            await db.exec(
                select(User).where(
                    User.preferences["privacy_default_action"].astext == "send_raw_external"
                )
            )
        ).all()
        for preference_user in preference_users:
            preference_user.preferences = {
                **(preference_user.preferences or {}),
                "privacy_default_action": "ask",
            }
            db.add(preference_user)
        invalidated_raw_preferences = len(preference_users)
    policy.updated_at = utcnow()
    policy.updated_by = admin.id
    db.add(policy)
    db.add(
        AuditEvent(
            actor_id=admin.id,
            action="governance.update",
            target="정책",
            detail=", ".join(sorted(patch)),
            event_metadata={
                "rawPreferencesInvalidated": invalidated_raw_preferences,
                "policyVersion": governance.POLICY_VERSION,
            },
            ip=client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:400],
        )
    )
    await db.commit()
    governance.invalidate()

    cleared = await governance.sweep_expired(db)
    await db.commit()
    return {
        "ok": True,
        "clearedMessages": cleared,
        "invalidatedPrivacyPreferences": invalidated_raw_preferences,
    }
