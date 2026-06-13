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
LiteLLM admin endpoints (/user/daily/activity, /customer/daily/activity,
/user/info, /model/info) require master_key auth — user-scoped virtual keys
can't read these, so we have no choice but to call them with the master key.
**조회는 master key, 결과는 caller 본인 데이터만** — 세 겹 강제:

  1. Every per-user query passes `user_id=USER_EMAIL` (or `end_user_ids=`)
     so LiteLLM applies its own filter. These endpoints return *global* data
     when the filter is omitted, so we refuse to call them without a non-empty
     USER_EMAIL.
  2. `_get` 가 응답에서 `teams` 로스터(타 멤버 user_id/키 — 미사용) 제거.
     이후 `_verify_self_only` 가 잔여 응답을 post-walk → 어떤 user
     identifier(`user_id` / `user_email` / `end_user` 필드값, `users` rollup 의
     dict 키)든 USER_EMAIL 과 불일치 시 raise — 필터 버그 시 누수 대신 거부.
  3. 렌더 직전 본인 소유로 재필터: budget_status 는 `user_id == USER_EMAIL`
     인 키만 표시, my_usage 는 caller 의 api_key 기준으로만 집계.

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
    """Walk the response; raise CrossUserDataError on any cross-user data.

    Catches two shapes of user identifier:
      - field values: `user_id` / `user_email` whose string value differs from
        expected_email
      - dict-key-as-user: a `users` dict whose keys are themselves user
        identifiers (incl. `default_user_id`) — LiteLLM uses this shape in
        rollup-style responses

    Allows null/missing user_id since some sub-records don't carry it. Skips
    `teams` (caller's team rosters; naturally multi-user, not rendered)."""
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "users" and isinstance(v, dict):
                # Rollup shape: {"users": {"<email>": <spend>, ...}} — keys are user
                # identifiers. Verify each; don't recurse into values (spend numbers).
                for uk in v:
                    if uk and uk != expected_email:
                        raise CrossUserDataError(
                            f"{path}.{k}: contains user {uk!r} (caller is {expected_email!r})"
                        )
                continue
            if k in ("user_id", "user_email", "end_user") and isinstance(v, str) and v and v != expected_email:
                raise CrossUserDataError(
                    f"{path}.{k}={v!r} differs from caller {expected_email!r}"
                )
            _verify_self_only(v, expected_email, f"{path}.{k}")
    elif isinstance(data, list):
        for i, item in enumerate(data):
            _verify_self_only(item, expected_email, f"{path}[{i}]")


async def _model_name_map() -> dict[str, str]:
    """Reverse map: internal `litellm_params.model` → user-facing `model_name`.

    /user/daily/activity logs request models with mixed naming — the internal
    routed name (`hosted_vllm/local/gemma-4-26b`, `openai/super-agent`) for
    successful calls, the user-facing name (`local/gemma-4-26b`) for failures
    before routing. Without this map, the same model would show up under
    multiple rows. Failures keep their user-facing name via the dict-get
    fallback, so they merge with successes correctly.

    Built from /model/info (admin endpoint, full catalog). Returned dict may
    have duplicate values (e.g., two deployments of the same model_name)."""
    data = await _get("/model/info")
    entries = data.get("data") if isinstance(data, dict) else data
    out: dict[str, str] = {}
    for m in entries or []:
        if not isinstance(m, dict):
            continue
        user_facing = m.get("model_name")
        internal = (m.get("litellm_params") or {}).get("model")
        if internal and user_facing:
            out[internal] = user_facing
    return out


async def _get(path: str, **params: Any) -> Any:
    """Admin-auth GET into LiteLLM. We have to use master_key here because the
    activity / info endpoints we depend on (/user/daily/activity,
    /customer/daily/activity, /user/info, /model/info) require admin auth —
    user-scoped virtual keys can't read them. Boundary is enforced via the
    `user_id=` / `end_user_ids=` filter the caller passes + the response
    post-check in `_verify_self_only`."""
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            f"{LITELLM_URL}{path}",
            params={k: v for k, v in params.items() if v is not None},
            headers={"Authorization": f"Bearer {MASTER_KEY}"},
        )
        r.raise_for_status()
        data = r.json()
    # team 로스터(/user/info.teams) = caller 무관 멤버의 user_id / 키 보유 —
    # 미사용이므로 검증·렌더 이전 제거 → 타 사용자 데이터 원천 배제.
    if isinstance(data, dict):
        data.pop("teams", None)
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
    """현재 사용자의 KloudChat / LiteLLM 토큰 사용량과 비용을 월 단위로 조회.

    한국어 트리거: "내 사용량", "이번 달 사용량", "토큰 사용량", "LLM 비용", "API 비용",
    "지난달 얼마 썼지", "한달 누적", "모델별 사용량", "qwen/claude/gpt 사용량".
    Show the current user's KloudChat / LiteLLM spend for a calendar month.

    LiteLLM key budgets reset monthly (`budget_duration: 1mo`) so usage is
    aggregated by calendar month to match. Returns total cost + per-model
    breakdown + per-day cumulative within the chosen month.

    Args:
        months_back: 0 = current month (default, "이번 달"), 1 = last month ("지난 달"), 2 = two months ago ("두 달 전"), ... (max 12).
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
    last_day_incl = end_excl - timedelta(days=1)

    try:
        # /user/daily/activity = activity authed via user's own keys.
        # /customer/daily/activity = activity tagged with end_user=USER_EMAIL —
        # covers super-agent downstream calls (shim auths as master, tags caller
        # via body.user injected by litellm-callbacks/inject_super_agent_user.py).
        # Merging both gives a complete picture; api_key_breakdown lets us skip
        # the wrapper rows in end_user data that are already in user_id data.
        activity_user = await _get(
            "/user/daily/activity",
            user_id=USER_EMAIL,
            start_date=start.isoformat(),
            end_date=last_day_incl.isoformat(),
        )
        activity_eu = await _get(
            "/customer/daily/activity",
            end_user_ids=USER_EMAIL,
            start_date=start.isoformat(),
            end_date=last_day_incl.isoformat(),
        )
        name_map = await _model_name_map()
    except httpx.HTTPStatusError as e:
        return f"Error fetching activity (HTTP {e.response.status_code}): {e.response.text[:200]}"
    except CrossUserDataError as e:
        return f"Error: cross-user data detected, refusing to render. ({e})"
    except Exception as e:
        return f"Error fetching activity: {e}"

    # Build the deduplication set from what's actually in user_id activity, not
    # from /user/info. /user/info excludes revoked keys, so a key that was used
    # then revoked would slip past the filter and get counted twice (once via
    # user_id, once via end_user). The activity response is authoritative — any
    # api_key listed under user_id activity belongs to this user.
    user_api_keys: set[str] = set()
    for r in (activity_user.get("results") or []):
        for m in ((r.get("breakdown") or {}).get("models") or {}).values():
            user_api_keys.update((m.get("api_key_breakdown") or {}).keys())

    by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"spend": 0.0, "tokens": 0, "requests": 0}
    )
    by_day: dict[str, float] = defaultdict(float)
    totals = {"spend": 0.0, "tokens": 0, "prompt": 0, "completion": 0, "requests": 0}

    def _accumulate(activity: Any, *, skip_user_keys: bool) -> None:
        """Walk activity response at the (date × model × api_key) level. When
        skip_user_keys is True (end_user pass), drop entries whose api_key is one
        of the caller's keys — those calls are the wrapper, already counted in
        the user_id pass. Aggregating at the api_key level (not metadata totals)
        is what lets us deduplicate without double-counting."""
        if not isinstance(activity, dict):
            return
        for r in activity.get("results") or []:
            if not isinstance(r, dict):
                continue
            date = r.get("date") or ""
            for model_name, model_data in ((r.get("breakdown") or {}).get("models") or {}).items():
                # Normalize internal litellm_params.model (e.g., hosted_vllm/local/gemma-4-26b,
                # openai/super-agent) back to user-facing model_name. Pre-routing failures
                # already use the user-facing name and merge via dict-get fallback.
                display = name_map.get(model_name, model_name)
                akb = (model_data or {}).get("api_key_breakdown") or {}
                for api_key, key_data in akb.items():
                    if skip_user_keys and api_key in user_api_keys:
                        continue
                    mm = (key_data or {}).get("metrics") or {}
                    spend = float(mm.get("spend") or 0)
                    tokens = int(mm.get("total_tokens") or 0)
                    reqs = int(mm.get("api_requests") or 0)
                    by_model[display]["spend"] += spend
                    by_model[display]["tokens"] += tokens
                    by_model[display]["requests"] += reqs
                    if date:
                        by_day[date] += spend
                    totals["spend"] += spend
                    totals["tokens"] += tokens
                    totals["prompt"] += int(mm.get("prompt_tokens") or 0)
                    totals["completion"] += int(mm.get("completion_tokens") or 0)
                    totals["requests"] += reqs

    _accumulate(activity_user, skip_user_keys=False)
    _accumulate(activity_eu,   skip_user_keys=True)

    total_spend = totals["spend"]
    total_prompt = totals["prompt"]
    total_completion = totals["completion"]
    total_tokens = totals["tokens"]
    total_requests = totals["requests"]

    lines = [
        f"User:         {USER_EMAIL}",
        f"Month:        {label}  ({start} → {last_day_incl})",
        f"Total spend:  {_fmt_money(total_spend)}",
        f"Total tokens: {total_tokens:,} (prompt {total_prompt:,} / completion {total_completion:,})",
        f"Requests:     {total_requests:,}",
    ]
    if by_model:
        lines.append("")
        lines.append("By model (sorted by spend):")
        for m in sorted(by_model.keys(), key=lambda k: -by_model[k]["spend"]):
            e = by_model[m]
            if e["spend"] <= 0 and e["tokens"] <= 0:
                continue
            lines.append(
                f"  {m:<46}  {_fmt_money(e['spend']):>12}  "
                f"{e['tokens']:>10,} tok  {e['requests']:>4} req"
            )
    elif total_spend == 0 and total_tokens == 0:
        lines.append("")
        lines.append("(no usage in this month)")
    nonzero_days = {d: s for d, s in by_day.items() if s > 0}
    if nonzero_days:
        lines.append("")
        lines.append("Daily spend:")
        for d in sorted(nonzero_days.keys()):
            lines.append(f"  {d}  {_fmt_money(nonzero_days[d]):>12}")

    return "\n".join(lines)


@mcp.tool()
async def budget_status() -> str:
    """현재 사용자의 LiteLLM 예산/잔액/한도 조회.

    한국어 트리거: "예산", "남은 잔액", "한도", "예산 얼마 남았어", "잔여 비용",
    "예산 상태", "한도 얼마야", "리셋 언제", "다음 예산 리셋", "키별 예산".
    Show the current user's LiteLLM key budget — total spend, max budget,
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

    # 본인 소유 키만 렌더 — user_id 가 caller 와 다른 키 제외 (user_id 없는 레코드는
    # 허용: 일부 키 레코드가 소유자 미포함). master key 조회라도 결과는 사용자만.
    keys = [k for k in (info.get("keys") or info.get("info") or [])
            if isinstance(k, dict) and (not k.get("user_id") or k.get("user_id") == USER_EMAIL)]

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
