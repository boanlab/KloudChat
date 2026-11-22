"""Google Drive calls with a person's own `drive.file` grant: a 401 refreshes once, rate
limits back off (`http.request`), lists follow `nextPageToken` up to a cap, downloads stop at
the upload limit."""

from __future__ import annotations

from typing import Any

import httpx
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.integrations import IntegrationAccount
from app.services.integrations import accounts
from app.services.integrations.http import AuthExpired, ProviderError, request

DRIVE = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
FIELDS = "id,name,mimeType,modifiedTime,size,webViewLink,trashed"
#: files one search returns at most (several pages)
SEARCH_MAX = 50

#: Google's own formats → what they are exported as, and the stored file's suffix
EXPORTS: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
}
#: Google's formats that cannot be brought in as a file
UNREADABLE = frozenset({"application/vnd.google-apps.folder",
                        "application/vnd.google-apps.form", "application/vnd.google-apps.map",
                        "application/vnd.google-apps.site", "application/vnd.google-apps.shortcut"})


class Google:
    """One person's Google calls for one tool run."""

    def __init__(self, db: AsyncSession, account: IntegrationAccount) -> None:
        self.db = db
        self.account = account

    async def call(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        token = await accounts.access_token(self.db, self.account)
        response = await request(method, url, token=token, **kwargs)
        if response.status_code == 401:
            # a token revoked early: one refresh, then the person reconnects
            await accounts.expire(self.db, self.account)
            token = await accounts.access_token(self.db, self.account)
            response = await request(method, url, token=token, **kwargs)
            if response.status_code == 401:
                raise AuthExpired("unauthorized")
        if response.status_code == 403:
            reason = _reason(response)
            if reason in ("accessNotConfigured", "SERVICE_DISABLED"):
                # the API is not switched on in the administrator's Google Cloud project
                raise ProviderError("api_disabled", 503, reason)
            if reason in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"):
                raise ProviderError("scope_missing", 403, reason)
            raise ProviderError("forbidden", 403, reason)
        if response.status_code == 404:
            raise ProviderError("not_found", 404)
        if response.status_code == 429:
            raise ProviderError("rate_limited", 429)
        if response.status_code >= 400:
            raise ProviderError("google_error", 502, _reason(response))
        await accounts.touch(self.db, self.account)
        return response

    async def json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = await self.call(method, url, **kwargs)
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    # ── drive ────────────────────────────────────────────────────────────

    async def file(self, file_id: str) -> dict[str, Any]:
        return await self.json("GET", f"{DRIVE}/files/{_path(file_id)}",
                               params={"fields": FIELDS, "supportsAllDrives": "true"})

    async def search(self, query: str = "", limit: int = SEARCH_MAX) -> list[dict[str, Any]]:
        """Files the app may open whose name has `query` (newest first)."""
        q = "trashed = false and mimeType != 'application/vnd.google-apps.folder'"
        if query:
            q += " and name contains '" + query.replace("\\", "").replace("'", "\\'") + "'"
        params: dict[str, Any] = {"q": q, "fields": f"nextPageToken,files({FIELDS})",
                                  "orderBy": "modifiedTime desc", "pageSize": min(100, limit),
                                  "supportsAllDrives": "true",
                                  "includeItemsFromAllDrives": "true"}
        found: list[dict[str, Any]] = []
        while True:
            body = await self.json("GET", f"{DRIVE}/files", params=params)
            found += [f for f in body.get("files") or [] if isinstance(f, dict)]
            token = body.get("nextPageToken")
            if not token or len(found) >= limit:
                return found[:limit]
            params["pageToken"] = token

    async def content(self, meta: dict[str, Any], limit: int) -> tuple[bytes, str]:
        """The file's bytes and the name to store it under: Google's own formats exported
        (Docs as .docx, Sheets as .xlsx, Slides as .pptx), anything else downloaded.
        `ProviderError("too_large")` past `limit` bytes."""
        mime = str(meta.get("mimeType") or "")
        name = str(meta.get("name") or "drive-file")
        if mime in UNREADABLE:
            raise ProviderError("unsupported_type", 415)
        if mime in EXPORTS:
            target, suffix = EXPORTS[mime]
            response = await self.call(
                "GET", f"{DRIVE}/files/{_path(str(meta['id']))}/export",
                params={"mimeType": target})
            if not name.lower().endswith(suffix):
                name += suffix
        else:
            if int(meta.get("size") or 0) > limit:
                raise ProviderError("too_large", 413)
            response = await self.call(
                "GET", f"{DRIVE}/files/{_path(str(meta['id']))}",
                params={"alt": "media", "supportsAllDrives": "true"})
        if len(response.content) > limit:
            raise ProviderError("too_large", 413)
        return response.content, name

    async def upload(self, name: str, mime: str, data: bytes) -> dict[str, Any]:
        """A new file in the person's Drive (`drive.file`: the app may open it later)."""
        import json as _json
        import secrets

        boundary = "kloudchat" + secrets.token_hex(8)
        meta = _json.dumps({"name": name}, ensure_ascii=False).encode()
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                .encode() + meta
                + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode() + data
                + f"\r\n--{boundary}--".encode())
        return await self.json(
            "POST", f"{UPLOAD}/files",
            params={"uploadType": "multipart", "fields": FIELDS, "supportsAllDrives": "true"},
            content=body, headers={"Content-Type": f"multipart/related; boundary={boundary}"})


def _path(part: str) -> str:
    from urllib.parse import quote

    return quote(part or "", safe="@.")


def _reason(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
    except (ValueError, AttributeError):
        return str(response.status_code)
    if isinstance(error, dict):
        errors = error.get("errors") or []
        if errors and isinstance(errors[0], dict) and errors[0].get("reason"):
            return str(errors[0]["reason"])[:80]
        details = error.get("details") or []
        for item in details if isinstance(details, list) else []:
            if isinstance(item, dict) and item.get("reason"):
                return str(item["reason"])[:80]
        return str(error.get("status") or response.status_code)[:80]
    return str(error)[:80]
