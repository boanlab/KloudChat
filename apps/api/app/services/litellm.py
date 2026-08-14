"""The only module that talks to LiteLLM.

Best-effort throughout: a proxy outage must still leave sign-in, history and
the workspace working, so provisioning failures are logged and swallowed and
the caller decides whether the missing capability matters.

The master key is used here and nowhere else, for provisioning only. Model
calls go out on the caller's virtual key, which is what makes proxy-side spend,
budgets and rate limits per person. Neither key reaches a browser.
"""

from __future__ import annotations

import logging
import math
import uuid

import httpx

from app.core.config import settings
from app.models.user import User, utcnow
from app.services import settings_store

log = logging.getLogger(__name__)

#: kchat's cycle turns over on the 1st (KST); LiteLLM's reset is not on the same
#: instant, which is part of why the budget carries headroom.
_BUDGET_DURATION = "1mo"


class LiteLLMError(RuntimeError):
    """Raised by calls whose failure the caller must surface (model listing)."""


async def _configured() -> bool:
    base, key = await settings_store.litellm_config()
    return bool(base and key)


async def _client() -> httpx.AsyncClient:
    """Built per call so an administrator's change takes effect without a restart."""
    base, key = await settings_store.litellm_config()
    return httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {key}"},
        timeout=settings.litellm_timeout_sec,
    )


async def ensure_user(user: User) -> str | None:
    """Creates the matching LiteLLM user record. No key — see `issue_key`.

    Separate from key issuance: a key whose `user_id` points at a record that
    was never created leaves proxy-side spend belonging to nobody.
    """
    if not await _configured():
        return None
    try:
        async with await _client() as client:
            response = await client.post(
                "/user/new",
                json={
                    "user_id": user.id,
                    "user_email": user.email,
                    "user_alias": user.name,
                    "user_role": "internal_user",
                    # Key minted separately, so a re-run cannot leave a second
                    # unreferenced key on the proxy.
                    "auto_create_key": False,
                },
            )
            # Already provisioned counts as success. The proxy rejects on email
            # and its existing record may carry a different id, so adopt that id
            # rather than ours — otherwise one person becomes two proxy users.
            if response.status_code in (400, 409) and _is_duplicate(response.text):
                user.litellm_user_id = await _find_user_id(client, user.email) or user.id
                return user.litellm_user_id
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("litellm user provisioning failed for %s: %s", user.email, exc)
        return None

    litellm_id = data.get("user_id") or user.id
    user.litellm_user_id = litellm_id
    return litellm_id


async def provision_user(user: User) -> str | None:
    """Creates the LiteLLM user and mints their virtual key.

    Mutates `user` on success; the caller adds it to the session. None when
    LiteLLM is unconfigured or unreachable — the account stays usable and falls
    back to the master key until `ensure_key` succeeds on a later turn.
    """
    litellm_id = await ensure_user(user)
    if litellm_id is None:
        return None
    if user_key(user):
        # Re-provisioning must not mint a second key: the first would stay live
        # on the proxy, referenced by nothing.
        await sync_budget(user)
    else:
        await issue_key(user)
    return litellm_id


def user_key(user: User) -> str | None:
    """The user's plaintext virtual key, or None if they have not got one."""
    if not user.litellm_key:
        return None
    return settings_store.decrypt_secret(user.litellm_key) or None


async def credentials_for(user: User) -> tuple[str, str]:
    """`(base_url, key)` to make this user's model calls with.

    Falls back to the master key when they have none, and logs it: availability
    beats attribution while somebody is waiting for an answer.
    """
    base, master = await settings_store.litellm_config()
    key = user_key(user)
    if key:
        return base, key
    log.info("no virtual key for %s — falling back to the master key", user.email)
    return base, master


async def issue_named_key(user: User, name: str) -> tuple[str, str] | None:
    """Mints a second key the user holds. Returns `(plaintext, alias)`.

    Same account, budget and attribution; it inherits `allowed_models`, so a
    key someone takes away reaches no further than they do in the app.

    The budget is cleared straight after minting: the proxy stamps its default
    on every new key and ignores a null at generate time, and a per-key budget
    would bind before the account's limit.
    """
    if not await _configured():
        return None
    alias = f"kchat:user:{user.email}:{uuid.uuid4().hex[:8]}"
    try:
        async with await _client() as client:
            response = await client.post(
                "/key/generate",
                json={
                    "user_id": user.litellm_user_id or user.id,
                    "key_alias": alias,
                    "models": list(user.allowed_models or []),
                    "metadata": {"kchat_user_id": user.id, "email": user.email, "kind": "user"},
                },
            )
            response.raise_for_status()
            key = response.json().get("key")
            if key:
                response = await client.post("/key/update", json={"key": key, "max_budget": None})
                response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("user key issuance failed for %s: %s", user.email, exc)
        return None
    return (key, alias) if key else None


async def delete_key(secret: str) -> bool:
    """Retires one key by value. Used for the keys a user manages themselves."""
    if not secret or not await _configured():
        return False
    try:
        async with await _client() as client:
            response = await client.post("/key/delete", json={"keys": [secret]})
            response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("key deletion failed: %s", exc)
        return False
    return True


async def sync_allowed_models(user: User, secrets: list[str]) -> bool:
    """Pushes the account's model allowlist onto every key it holds.

    Enforced app-side only, the allowlist is a suggestion — a user's own API key
    would still reach everything.
    """
    if not await _configured():
        return False
    models = list(user.allowed_models or [])
    keys = [k for k in [user_key(user), *secrets] if k]
    ok = True
    try:
        async with await _client() as client:
            for key in keys:
                response = await client.post("/key/update", json={"key": key, "models": models})
                response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("model allowlist sync failed for %s: %s", user.email, exc)
        ok = False
    return ok


async def issue_key(user: User) -> str | None:
    """Mints a virtual key for `user` and records it, encrypted, on the row.

    Plaintext on success, None on any failure — a proxy outage must not block a
    signup.
    """
    if not await _configured():
        return None

    alias = f"kchat:{user.email}"
    for attempt in range(2):
        try:
            async with await _client() as client:
                response = await client.post(
                    "/key/generate",
                    json={
                        # The proxy's id for them, which is not always ours.
                        "user_id": user.litellm_user_id or user.id,
                        "key_alias": alias,
                        # Empty list means "everything the proxy serves".
                        "models": list(user.allowed_models or []),
                        # On every spend row, so a log line traces back to a
                        # kchat account without a join.
                        "metadata": {"kchat_user_id": user.id, "email": user.email},
                    },
                )
                # Aliases are unique on the proxy; a leftover from a failed
                # rotation delete would block the account permanently.
                if response.status_code >= 400 and _is_duplicate(response.text):
                    if attempt == 0:
                        alias = f"kchat:{user.email}#{uuid.uuid4().hex[:6]}"
                        continue
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("litellm key issuance failed for %s: %s", user.email, exc)
            return None
        break
    else:
        return None

    key = data.get("key")
    if not key:
        log.warning("litellm returned no key for %s", user.email)
        return None

    user.litellm_key = settings_store.encrypt_secret(key)
    user.litellm_key_preview = settings_store.preview(key)
    user.litellm_key_issued_at = utcnow()
    # A new key otherwise inherits the proxy's default budget, unrelated to the
    # allowance kchat just granted.
    await sync_budget(user)
    return key


def budget_usd(monthly_credits: int) -> float:
    """The proxy-side ceiling for a kchat allowance.

    Deliberately above it (`litellm_budget_headroom`). Rounded up to the cent: a
    budget of zero reads as "no calls at all", not "no limit".
    """
    usd = monthly_credits / settings.credits_per_usd * (1 + settings.litellm_budget_headroom)
    return math.ceil(usd * 100) / 100


async def sync_budget(user: User) -> bool:
    """Mirrors the kchat allowance onto the proxy as one user-level ceiling.

    User-level only. LiteLLM enforces key and user budgets independently and
    stops at whichever binds first, so every key is cleared of its own budget
    and draws on the one pool.

    A failure leaves the backstop stale, not anyone blocked: kchat's own check
    runs before every turn.
    """
    if not await _configured():
        return False

    limit = budget_usd(user.monthly_credits)
    ok = True
    try:
        async with await _client() as client:
            if user.litellm_user_id:
                # Ceiling first: clearing keys before it is in place leaves a
                # window with no limit anywhere.
                response = await client.post(
                    "/user/update",
                    json={
                        "user_id": user.litellm_user_id,
                        "max_budget": limit,
                        "budget_duration": _BUDGET_DURATION,
                    },
                )
                response.raise_for_status()
            # Read from the proxy, not kchat's rows: a key kchat lost track of
            # still spends and still carries its own budget.
            for token in await _user_key_tokens(client, user):
                response = await client.post("/key/update", json={"key": token, "max_budget": None})
                response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("litellm budget sync failed for %s: %s", user.email, exc)
        ok = False
    return ok


async def revoke_key(user: User) -> bool:
    """Deletes the user's key on the proxy and clears it locally.

    Local columns are cleared even when the remote delete fails — `ensure_key`
    mints a fresh one.
    """
    key = user_key(user)
    user.litellm_key = None
    user.litellm_key_preview = None
    user.litellm_key_issued_at = None
    if not key or not await _configured():
        return False
    try:
        async with await _client() as client:
            response = await client.post("/key/delete", json={"keys": [key]})
            response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("litellm key deletion failed for %s: %s", user.email, exc)
        return False
    return True


async def set_key_blocked(user: User, blocked: bool) -> bool:
    """Suspends or restores the key without destroying it.

    Defence in depth: the key never leaves this process, so this matters only if
    one leaks. It also keeps the proxy's view of who is active in step.
    """
    key = user_key(user)
    if not key or not await _configured():
        return False
    try:
        async with await _client() as client:
            response = await client.post(
                "/key/block" if blocked else "/key/unblock", json={"key": key}
            )
            response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        verb = "block" if blocked else "unblock"
        log.warning("litellm key %s failed for %s: %s", verb, user.email, exc)
        return False
    return True


async def ensure_key(user: User) -> str | None:
    """Key for `user`, provisioning them first if that never happened.

    Signup succeeds whether or not the proxy is reachable, so this is where a
    missing user record and key appear.

    The user record has to come first: `/key/generate` accepts a `user_id` that
    does not exist and links the key anyway, leaving spend attributed to nobody.
    """
    if not user.litellm_user_id:
        await ensure_user(user)
    return user_key(user) or await issue_key(user)


async def _find_user_id(client: httpx.AsyncClient, email: str) -> str | None:
    """The proxy's id for `email`, if it already knows one."""
    try:
        response = await client.get("/user/list", params={"user_email": email, "page_size": 2})
        response.raise_for_status()
        users = response.json().get("users") or []
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        log.warning("litellm user lookup failed for %s: %s", email, exc)
        return None
    return users[0].get("user_id") if users else None


async def _user_key_tokens(client: httpx.AsyncClient, user: User) -> list[str]:
    """Hashed tokens of every key the proxy holds for this account.

    The proxy never returns a secret twice. `/key/update` accepts a hash, which
    is enough to edit a key kchat cannot read.
    """
    if not user.litellm_user_id:
        return []
    try:
        response = await client.get("/user/info", params={"user_id": user.litellm_user_id})
        response.raise_for_status()
        keys = response.json().get("keys") or []
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        log.warning("litellm key listing failed for %s: %s", user.email, exc)
        return []
    return [t for k in keys if (t := k.get("token"))]


def _is_duplicate(body: str) -> bool:
    lowered = body.lower()
    return any(s in lowered for s in ("already exists", "duplicate", "unique"))


async def key_spend(secret: str) -> dict[str, float] | None:
    """Spend to date and budget for one virtual key, or None if the proxy is silent.

    kchat's ledger covers only turns taken inside the app; calls made through
    `/llm` are recorded on the proxy alone.
    """
    if not await _configured():
        return None
    try:
        async with await _client() as client:
            response = await client.get("/key/info", params={"key": secret})
            response.raise_for_status()
            info = (response.json() or {}).get("info") or {}
    except (httpx.HTTPError, ValueError) as exc:
        log.info("key spend unreadable: %s", exc)
        return None
    return {
        "spend": float(info.get("spend") or 0.0),
        "maxBudget": float(info.get("max_budget") or 0.0),
    }


async def health(quick: bool = False) -> bool:
    """Liveness probe.

    `quick` shortens the timeout for the admin screen, where a bad host would
    otherwise hold the settings request open for the full client timeout.
    """
    if not await _configured():
        return False
    base, key = await settings_store.litellm_config()
    timeout = settings.litellm_probe_timeout_sec if quick else settings.litellm_timeout_sec
    try:
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        ) as client:
            response = await client.get("/health/liveliness")
            return response.is_success
    except httpx.HTTPError:
        return False


async def model_info() -> list[dict]:
    """Raw `/model/info` payload. Shaping into kchat's `ModelInfo` happens in
    `services/models.py` so this stays a thin transport wrapper.
    """
    if not await _configured():
        raise LiteLLMError("litellm_not_configured")
    try:
        async with await _client() as client:
            response = await client.get("/model/info")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LiteLLMError(f"model_info_failed: {exc}") from exc
    return payload.get("data", [])
