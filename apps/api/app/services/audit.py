"""Audit trail helpers: the action codes the API writes and value diffs for them.

A change event carries `metadata.changes = {field: [old, new]}`. Secrets are masked
and long values clipped, so the trail says what changed without storing what it is.
"""

from __future__ import annotations

import re
from typing import Any

#: Every action code written to `audit_events`. The admin screen labels each one;
#: `tests/test_audit_log.py` checks both directions.
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
        "pipeline.reject",
        "issue.update",
        "issue.snapshot_view",
        # skills
        "skill.import",
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
    }
)

#: Shown in place of a secret, set or not.
MASK = "••••"
_SECRET = re.compile(r"(key|password|secret|token)", re.IGNORECASE)
_MAX_TEXT = 160
_MAX_ITEMS = 20


def is_secret(field: str) -> bool:
    return bool(_SECRET.search(field))


def clip(value: Any) -> Any:
    """`value` made small and JSON-safe: long text cut, long lists shortened."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out = [clip(v) for v in items[:_MAX_ITEMS]]
        if len(items) > _MAX_ITEMS:
            out.append(f"…(+{len(items) - _MAX_ITEMS})")
        return out
    if hasattr(value, "value") and not isinstance(value, dict):
        # Enums record their wire value.
        value = value.value
    text = str(value)
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "…"


def masked(value: Any) -> str:
    return MASK if value not in (None, "", [], False) else ""


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[Any]]:
    """`{field: [old, new]}` for the fields of `after` whose value differs from `before`.

    Secret-looking fields are compared but recorded masked; a secret that was
    resubmitted is recorded as changed, since its old value cannot be shown either way.
    """
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
    # A cleared value and an unset one are the same thing on screen.
    if a in (None, "") and b in (None, ""):
        return True
    return a == b
