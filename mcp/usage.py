#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mcp>=1.2.0",
#   "httpx>=0.27.0",
# ]
# ///
"""LiteLLM usage MCP server (stdio).

Exposes the current LibreChat user's spend / budget on KloudChat's LiteLLM
proxy. Identifies the user by `LIBRECHAT_USER_EMAIL` env (substituted by
LibreChat per-session from `{{LIBRECHAT_USER_EMAIL}}` placeholder).

Tools:
- my_usage(months_back=0) — spend / token totals + model breakdown per month
- budget_status()         — per-key budget, remaining, reset date

Trust / boundary
----------------
LiteLLM admin endpoints (/spend/logs, /user/info) require master_key auth —
user-scoped virtual keys can't read these, so we have no choice but to call
them with the master key. Two layers protect against cross-user leakage:

  1. Every call passes `user_id=USER_EMAIL` so LiteLLM applies its own filter.
  2. `_verify_self_only` post-walks the response and raises if any user_id
     field differs from USER_EMAIL — belt-and-suspenders against LiteLLM filter
     bugs or unexpected fields.

The remaining trust point is `LIBRECHAT_USER_EMAIL` placeholder substitution
itself: if LibreChat passes the wrong email to a child MCP process, both
layers above will happily return *that* email's data. This is intrinsic to
how MCP env substitution works in LibreChat — there is no MCP-side way to
independently verify which user is calling. Mitigation lives at the LibreChat
process boundary (per-session stdio spawn with `startup: false`).
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

LITELLM_URL = os.environ.get("LITELLM_URL", "http://litellm:8000").rstrip("/")
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
USER_EMAIL = os.environ.get("LIBRECHAT_USER_EMAIL", "").strip()

mcp = FastMCP("litellm-usage")


class CrossUserDataError(Exception):
    """Defensive: response contains a user_id != caller's USER_EMAIL."""


def _verify_self_only(data: Any, expected_email: str, path: str = "$") -> None:
    """Walk the response; raise CrossUserDataError if any user_id field differs
    from expected_email. LiteLLM's /spend/logs and /user/info are admin endpoints
    and we trust their `user_id=` filter — this is belt-and-suspenders against
    accidental cross-user leakage (filter bugs, unexpected fields). Allows
    `null`/missing user_id since some sub-records don't carry it."""
    if isinstance(data, dict):
        for k, v in data.items():
            if k in ("user_id", "user_email") and isinstance(v, str) and v and v != expected_email:
                raise CrossUserDataError(
                    f"{path}.{k}={v!r} differs from caller {expected_email!r}"
                )
            _verify_self_only(v, expected_email, f"{path}.{k}")
    elif isinstance(data, list):
        for i, item in enumerate(data):
            _verify_self_only(item, expected_email, f"{path}[{i}]")


async def _get(path: str, **params: Any) -> Any:
    """Admin-auth call into LiteLLM; admin endpoints (/spend/logs, /user/info)
    are unavoidable here since user-scoped LiteLLM keys can't read these.
    Boundary is enforced via `user_id=USER_EMAIL` filter + response post-check."""
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            f"{LITELLM_URL}{path}",
            params={k: v for k, v in params.items() if v is not None},
            headers={"Authorization": f"Bearer {MASTER_KEY}"},
        )
        r.raise_for_status()
        data = r.json()
    _verify_self_only(data, USER_EMAIL)
    return data


def _fmt_money(v: float) -> str:
    return f"${v:.4f}" if v < 1 else f"${v:,.2f}"


def _month_range(months_back: int) -> tuple[Any, Any, str]:
    """(start_date, end_exclusive_date, label) for a calendar month offset.
    months_back=0 → current month, 1 → last month, etc."""
    today = datetime.now(timezone.utc).date()
    year, month = today.year, today.month - months_back
    while month <= 0:
        month += 12
        year -= 1
    start = today.replace(year=year, month=month, day=1)
    # next month's first day
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    end_excl = today.replace(year=ny, month=nm, day=1)
    return start, end_excl, f"{year}-{month:02d}"


@mcp.tool()
async def my_usage(months_back: int | str = 0) -> str:
    """Show the current user's KloudChat / LiteLLM spend for a calendar month.

    LiteLLM key budgets reset monthly (`budget_duration: 1mo`) so usage is
    aggregated by calendar month to match. Returns total cost + per-model
    breakdown + per-day cumulative within the chosen month.

    Args:
        months_back: 0 = current month (default), 1 = last month, 2 = two months ago, ... (max 12).
                     Accepts int or numeric string — some models pass JSON ints as strings.
    """
    if not MASTER_KEY:
        return "Error: LITELLM_MASTER_KEY not set on MCP server."
    if not USER_EMAIL:
        return ("Error: LIBRECHAT_USER_EMAIL not passed to MCP server — "
                "check librechat.yaml mcpServers env mapping.")

    try:
        mb = int(months_back)
    except (TypeError, ValueError):
        mb = 0
    months_back = max(0, min(mb, 12))
    start, end_excl, label = _month_range(months_back)

    try:
        # LiteLLM /spend/logs end_date is EXCLUSIVE.
        rolls = await _get(
            "/spend/logs",
            user_id=USER_EMAIL,
            start_date=start.isoformat(),
            end_date=end_excl.isoformat(),
        )
    except httpx.HTTPStatusError as e:
        return f"Error fetching /spend/logs (HTTP {e.response.status_code}): {e.response.text[:200]}"
    except Exception as e:
        return f"Error fetching /spend/logs: {e}"

    if not isinstance(rolls, list):
        return f"Unexpected /spend/logs response shape: {type(rolls).__name__}"

    by_model: dict[str, float] = defaultdict(float)
    by_day: dict[str, float] = defaultdict(float)
    total_spend = 0.0
    for roll in rolls:
        if not isinstance(roll, dict):
            continue
        day_total = float(roll.get("spend") or 0)
        total_spend += day_total
        day = (roll.get("startTime") or "")[:10]
        if day:
            by_day[day] += day_total
        for model_id, spend in (roll.get("models") or {}).items():
            try:
                by_model[model_id] += float(spend)
            except (TypeError, ValueError):
                pass

    last_day_incl = end_excl - timedelta(days=1)
    lines = [
        f"User:        {USER_EMAIL}",
        f"Month:       {label}  ({start} → {last_day_incl})",
        f"Total spend: {_fmt_money(total_spend)}",
    ]
    if by_model:
        lines.append("")
        lines.append("By model (sorted by spend):")
        for m in sorted(by_model.keys(), key=lambda k: -by_model[k]):
            sp = by_model[m]
            if sp <= 0:
                continue
            lines.append(f"  {m:<46}  {_fmt_money(sp):>12}")
    elif total_spend == 0:
        lines.append("")
        lines.append("(no usage in this month)")
    nonzero_days = {d: s for d, s in by_day.items() if s > 0}
    if nonzero_days:
        lines.append("")
        lines.append("Daily:")
        for d in sorted(nonzero_days.keys()):
            lines.append(f"  {d}  {_fmt_money(nonzero_days[d]):>12}")

    return "\n".join(lines)


@mcp.tool()
async def budget_status() -> str:
    """Show the current user's LiteLLM key budget — total spend, max budget,
    remaining, and reset schedule (per key)."""
    if not MASTER_KEY:
        return "Error: LITELLM_MASTER_KEY not set on MCP server."
    if not USER_EMAIL:
        return ("Error: LIBRECHAT_USER_EMAIL not passed to MCP server — "
                "check librechat.yaml mcpServers env mapping.")

    try:
        info = await _get("/user/info", user_id=USER_EMAIL)
    except httpx.HTTPStatusError as e:
        return f"Error fetching /user/info (HTTP {e.response.status_code}): {e.response.text[:200]}"
    except Exception as e:
        return f"Error fetching /user/info: {e}"

    user_info = info.get("user_info") or info
    lifetime_spend = float(user_info.get("spend") or info.get("total_spend") or 0)
    user_max_budget = user_info.get("max_budget")
    user_budget_duration = user_info.get("budget_duration")
    user_budget_reset_at = user_info.get("budget_reset_at")

    keys = info.get("keys") or info.get("info") or []

    lines = [
        f"User:           {USER_EMAIL}",
        f"Lifetime spend: {_fmt_money(lifetime_spend)}",
    ]
    if user_max_budget is not None:
        rem = float(user_max_budget) - lifetime_spend
        pct = (lifetime_spend / float(user_max_budget) * 100) if float(user_max_budget) else 0
        lines.append(
            f"User budget:    {_fmt_money(lifetime_spend)} / {_fmt_money(float(user_max_budget))} "
            f"({pct:.1f}% used, remaining {_fmt_money(rem)})"
        )
        if user_budget_duration:
            lines.append(f"  resets every {user_budget_duration}" +
                         (f", next at {user_budget_reset_at}" if user_budget_reset_at else ""))

    if not keys:
        lines.append("")
        lines.append("(no LiteLLM keys for this user)")
        return "\n".join(lines)

    lines.append("")
    lines.append("Per-key budgets:")
    for k in keys:
        alias = k.get("key_alias") or k.get("key_name") or k.get("token") or "?"
        spend = float(k.get("spend") or 0)
        max_b = k.get("max_budget")
        dur = k.get("budget_duration")
        reset = k.get("budget_reset_at")
        tpm = k.get("tpm_limit")
        rpm = k.get("rpm_limit")
        if max_b is not None:
            rem = float(max_b) - spend
            pct = (spend / float(max_b) * 100) if float(max_b) else 0
            lines.append(
                f"  {alias}: {_fmt_money(spend)} / {_fmt_money(float(max_b))} "
                f"({pct:.1f}%, remaining {_fmt_money(rem)})"
            )
        else:
            lines.append(f"  {alias}: {_fmt_money(spend)} (no budget cap)")
        if dur:
            lines.append(f"    resets every {dur}" + (f", next at {reset}" if reset else ""))
        if tpm or rpm:
            lines.append(f"    rate limit: tpm={tpm or '∞'}, rpm={rpm or '∞'}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
