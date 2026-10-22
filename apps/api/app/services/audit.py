"""Audit action codes and value diffs; secrets masked, long values clipped."""

from __future__ import annotations

import re
from typing import Any

from fastapi import Request

from app.core.deps import client_ip
from app.models.user import AuditEvent, User

#: all action codes in `audit_events`; admin labels checked by `tests/test_audit_log.py`
ACTIONS: frozenset[str] = frozenset(
    {
        # auth
        "signup",
        "signup.verified",
        "signup.verification_resent",
        "login",
        "password.change",
        "password.reset_requested",
        "password.reset",
        "session.revoke",
        # admin: accounts
        "user.approve",
        "user.reject",
        "user.suspend",
        "user.reinstate",
        "user.role",
        "user.update",
        "user.password_reset",
        "user.litellm_key",
        "user.delete",
        "user.models",
        "credits.set",
        # admin: keys, settings, policy, branding
        "key.create",
        "key.revoke",
        "settings.update",
        "governance.update",
        "branding.logo",
        "branding.logo.clear",
        "pipeline.approve",
        "connector.catalog.save",
        "connector.catalog.delete",
        "connector.stdio_allow.save",
        "pipeline.reject",
        "issue.update",
        "issue.snapshot_view",
        # skills
        "skill.import",
        "skill.approve",
        "skill.reject",
        # storage
        "storage.reclaim",
        "storage.reclaim.manual",
        # chat policy
        "filter.blocked",
        "pii.masked",
        "routing.auto",
        "privacy.none",
        "privacy.decision_required",
        "privacy.internal_strict_local",
        "privacy.internal_data_requires_strict_local",
        "privacy.strict_local",
        "privacy.route_strict_local",
        "privacy.mask_external",
        "privacy.send_raw_external",
        "privacy.mask_tool_output",
        "privacy.strict_tool_output",
        # a model call LiteLLM's fallback answered (`services/fallback_watch`)
        "model.fallback",
        # published chatbots (`routers/publications`, `services/publishing`)
        "publication.published",
        "publication.requested",
        "publication.request_withdrawn",
        "publication.reviewed",
        "publication.narrowed",
        "publication.withdrawn",
        "publication.version",
        "publication.rollback",
        "publication.settings",
        "publication.paused",
        "publication.resumed",
        "publication.admin_status",
        "publication.deleted",
        "publication.channels_requested",
        "publication.channels_closed",
        "publication.key_created",
        "publication.key_revoked",
        "publication.consent",
        "publication.shared",
        "publication.conversation_read",
        "publication.admin_list",
        "publication.admin_read",
        "publication.visitor_blocked",
        "publication.visitor_unblocked",
        # a person deleting their own content (`deletion`)
        "conversation.delete",
        "conversation.bulk_delete",
        "project.delete",
        "project.bulk_delete",
        "file.delete",
        "knowledge.delete",
        "artifact.delete",
        "artifact.bulk_delete",
        "memory.delete",
        "agent.delete",
        "agent.bulk_delete",
        "skill.delete",
        "skill.bulk_delete",
        "design.delete",
        "design.bulk_delete",
        "template.delete",
        "pipeline.delete",
    }
)

#: a bulk delete this large (or of everything) is flagged `warn`
BULK_WARN = 50
#: ids kept per deletion event; the counts carry the rest
_MAX_IDS = 50

#: count key → Korean noun for the detail line, in display order
_COUNT_WORDS = (
    ("sessions", "대화"),
    ("messages", "메시지"),
    ("artifacts", "아티팩트"),
    ("projects", "프로젝트"),
    ("files", "파일"),
    ("memories", "메모리"),
    ("agents", "에이전트"),
    ("skills", "스킬"),
    ("designs", "테마"),
    ("templates", "템플릿"),
    ("pipelines", "진행 과정"),
)

#: secret placeholder, set or not
MASK = "••••"
_SECRET = re.compile(r"(key|password|secret|token)", re.IGNORECASE)
_MAX_TEXT = 160
_MAX_ITEMS = 20


def is_secret(field: str) -> bool:
    return bool(_SECRET.search(field))


def clip(value: Any) -> Any:
    """`value` made small and JSON-safe."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out = [clip(v) for v in items[:_MAX_ITEMS]]
        if len(items) > _MAX_ITEMS:
            out.append(f"…(+{len(items) - _MAX_ITEMS})")
        return out
    if hasattr(value, "value") and not isinstance(value, dict):
        value = value.value
    text = str(value)
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "…"


def masked(value: Any) -> str:
    return MASK if value not in (None, "", [], False) else ""


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[Any]]:
    """`{field: [old, new]}` for changed fields; secrets masked, resubmitted ones count."""
    out: dict[str, list[Any]] = {}
    for field, new in after.items():
        old = before.get(field)
        if is_secret(field):
            if new is None:
                continue
            out[field] = [masked(old), masked(new)]
            continue
        if _same(old, new):
            continue
        out[field] = [clip(old), clip(new)]
    return out


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return list(a) == list(b)
    # cleared and unset look the same
    if a in (None, "") and b in (None, ""):
        return True
    return a == b


def deletion(
    user: User,
    request: Request | None,
    *,
    action: str,
    ids: list[str],
    counts: dict[str, int],
    everything: bool = False,
    note: str = "",
    extra: dict[str, Any] | None = None,
) -> AuditEvent:
    """One event for one delete request by its owner: counts and ids, never content."""
    words = [f"{noun} {counts[key]:,}개" for key, noun in _COUNT_WORDS if key in counts]
    detail = " · ".join((["전체 삭제"] if everything else []) + words + ([note] if note else []))
    meta: dict[str, Any] = {**counts, **(extra or {})}
    if everything:
        meta["all"] = True
    meta["ids"] = ids[:_MAX_IDS]
    if len(ids) > _MAX_IDS:
        meta["idsOmitted"] = len(ids) - _MAX_IDS
    bulk = action.endswith(".bulk_delete")
    return AuditEvent(
        actor_id=user.id,
        action=action,
        target=user.email,
        detail=detail,
        event_metadata=meta,
        ip=client_ip(request) if request is not None else "",
        user_agent=request.headers.get("User-Agent", "")[:400] if request is not None else "",
        severity="warn" if bulk and (everything or len(ids) >= BULK_WARN) else "info",
    )
