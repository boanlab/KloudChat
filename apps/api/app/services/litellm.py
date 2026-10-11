"""Sole LiteLLM client; best-effort; master key stays here, no key reaches a browser."""

from __future__ import annotations

import logging
import math
import uuid

import httpx

from app.core.config import settings
from app.models.user import User, utcnow
from app.services import settings_store

log = logging.getLogger(__name__)

#: KST monthly cycle; LiteLLM reset differs (one reason for budget headroom)
_BUDGET_DURATION = "1mo"


class LiteLLMError(RuntimeError):
    """Failure the caller must surface (model listing)."""


async def _configured() -> bool:
    base, key = await settings_store.litellm_config()
    return bool(base and key)


async def _client() -> httpx.AsyncClient:
    """Built per call so admin changes apply without restart."""
    base, key = await settings_store.litellm_config()
    return httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {key}"},
        timeout=settings.litellm_timeout_sec,
    )


async def ensure_user(user: User) -> str | None:
    """Create the LiteLLM user record (no key)."""
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
                    "auto_create_key": False,
                },
            )
            # already provisioned = success; adopt proxy's id (one person, one proxy user)
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
    """Create LiteLLM user and key; mutates user; None if unconfigured/unreachable."""
    litellm_id = await ensure_user(user)
    if litellm_id is None:
        return None
    if user_key(user):
        # no second key on re-provision
        await sync_budget(user)
    else:
        await issue_key(user)
    return litellm_id


def user_key(user: User) -> str | None:
    """User's plaintext virtual key, or None."""
    if not user.litellm_key:
        return None
    return settings_store.decrypt_secret(user.litellm_key) or None


async def credentials_for(user: User) -> tuple[str, str]:
    """(base_url, key) for model calls; master key fallback."""
    base, master = await settings_store.litellm_config()
    key = user_key(user)
    if key:
        return base, key
    log.info("no virtual key for %s — falling back to the master key", user.email)
    return base, master


async def issue_named_key(user: User, name: str) -> tuple[str, str] | None:
    """Mint an extra key: (plaintext, alias); proxy default per-key budget cleared."""
    if not await _configured():
        return None
    alias = f"KloudChat:user:{user.email}:{uuid.uuid4().hex[:8]}"
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
    """Retire one key by value."""
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
    """Push the model allowlist onto every key."""
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
    """Mint and store (encrypted) a key; plaintext or None."""
    if not await _configured():
        return None

    alias = f"KloudChat:{user.email}"
    for attempt in range(2):
        try:
            async with await _client() as client:
                response = await client.post(
                    "/key/generate",
                    json={
                        "user_id": user.litellm_user_id or user.id,
                        "key_alias": alias,
                        # empty = everything the proxy serves
                        "models": list(user.allowed_models or []),
                        "metadata": {"kchat_user_id": user.id, "email": user.email},
                    },
                )
                # leftover alias from a failed rotation would block the account
                if response.status_code >= 400 and _is_duplicate(response.text):
                    if attempt == 0:
                        alias = f"KloudChat:{user.email}#{uuid.uuid4().hex[:6]}"
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
    # else inherits the proxy default budget
    await sync_budget(user)
    return key


def budget_usd(monthly_credits: int) -> float:
    """Proxy ceiling: headroom applied, rounded up to the cent."""
    usd = monthly_credits / settings.credits_per_usd * (1 + settings.litellm_budget_headroom)
    return math.ceil(usd * 100) / 100


async def sync_budget(user: User) -> bool:
    """Mirror the allowance as one user ceiling; key budgets cleared (both enforced)."""
    if not await _configured():
        return False

    limit = budget_usd(user.monthly_credits)
    ok = True
    try:
        async with await _client() as client:
            if user.litellm_user_id:
                # ceiling first: no unlimited window
                response = await client.post(
                    "/user/update",
                    json={
                        "user_id": user.litellm_user_id,
                        "max_budget": limit,
                        "budget_duration": _BUDGET_DURATION,
                    },
                )
                response.raise_for_status()
            # from the proxy: forgotten keys still spend
            for token in await _user_key_tokens(client, user):
                response = await client.post("/key/update", json={"key": token, "max_budget": None})
                response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("litellm budget sync failed for %s: %s", user.email, exc)
        ok = False
    return ok


async def revoke_key(user: User) -> bool:
    """Delete the key on the proxy; cleared locally regardless."""
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
    """Suspend or restore the key."""
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
    """Key for user, provisioning first (unknown user_id → unattributed spend)."""
    if not user.litellm_user_id:
        await ensure_user(user)
    return user_key(user) or await issue_key(user)


async def _find_user_id(client: httpx.AsyncClient, email: str) -> str | None:
    """Proxy id for email, if known."""
    try:
        response = await client.get("/user/list", params={"user_email": email, "page_size": 2})
        response.raise_for_status()
        users = response.json().get("users") or []
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        log.warning("litellm user lookup failed for %s: %s", email, exc)
        return None
    return users[0].get("user_id") if users else None


async def _user_key_tokens(client: httpx.AsyncClient, user: User) -> list[str]:
    """Hashed tokens of every account key (/key/update accepts hashes)."""
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
    """Spend and budget for one key, or None."""
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
    """Liveness probe; quick = shorter timeout."""
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
    """Raw /model/info rows (shaped in services/models.py)."""
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
