"""Service name and logo.

The logo is a file on disk; settings hold only its name. Reading it needs no
authentication: the sign-in screen renders it.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status

from app.core.config import settings
from app.core.deps import AdminUser, DbSession, client_ip
from app.models.user import AuditEvent
from app.services import files, settings_store

log = logging.getLogger(__name__)

router = APIRouter(tags=["branding"])

#: No SVG: it can carry script, and the logo loads on every visitor's sign-in screen.
ALLOWED = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}

MAX_BYTES = 2 * 1024 * 1024


def _logo_dir() -> Path:
    return Path(settings.file_storage_dir) / "branding"


def _logo_path(filename: str) -> Path:
    return _logo_dir() / filename


@router.get("/branding/logo")
async def get_logo():
    """The current logo, or 404 (the UI then draws the default mark)."""
    values = await settings_store.all_values()
    filename = values.get(settings_store.BRAND_LOGO, "")
    mime = values.get(settings_store.BRAND_LOGO_MIME, "")
    if not filename or not mime:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no_logo")
    try:
        data = _logo_path(filename).read_bytes()
    except OSError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no_logo") from None
    # The filename carries a content hash, so a long cache lifetime is safe.
    return Response(
        content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400"}
    )


@router.post("/admin/branding/logo")
async def upload_logo(
    request: Request, admin: AdminUser, db: DbSession, file: UploadFile = File(...)
):
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in ALLOWED:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="PNG, JPG, WebP 만 올릴 수 있습니다.",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="빈 파일입니다.")
    # The declared type is the client's word; the bytes must agree before the logo is
    # served to every visitor.
    if files.sniff(file.filename or "", data) != mime:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="파일 내용이 PNG, JPG, WebP 가 아닙니다.",
        )
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"{MAX_BYTES // (1024 * 1024)}MB 이하만 올릴 수 있습니다.",
        )

    # Content-hashed filename: replacing the logo changes the URL.
    previous = (await settings_store.all_values()).get(settings_store.BRAND_LOGO, "")
    digest = hashlib.sha256(data).hexdigest()[:12]
    filename = f"logo-{digest}{ALLOWED[mime]}"
    _logo_dir().mkdir(parents=True, exist_ok=True)
    _logo_path(filename).write_bytes(data)

    await settings_store.put(db, settings_store.BRAND_LOGO, filename, admin.id)
    await settings_store.put(db, settings_store.BRAND_LOGO_MIME, mime, admin.id)
    db.add(
        AuditEvent(
            actor_id=admin.id,
            action="branding.logo",
            target=filename,
            detail=f"{len(data)}B",
            ip=client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:400],
        )
    )
    await db.commit()
    settings_store.invalidate()

    if previous and previous != filename:
        _logo_path(previous).unlink(missing_ok=True)
    return {"logo": f"/api/branding/logo?v={filename}"}


@router.delete("/admin/branding/logo", status_code=status.HTTP_204_NO_CONTENT)
async def delete_logo(request: Request, admin: AdminUser, db: DbSession):
    """Reverts to the default mark."""
    filename = (await settings_store.all_values()).get(settings_store.BRAND_LOGO, "")
    await settings_store.put(db, settings_store.BRAND_LOGO, "", admin.id)
    await settings_store.put(db, settings_store.BRAND_LOGO_MIME, "", admin.id)
    db.add(
        AuditEvent(
            actor_id=admin.id,
            action="branding.logo.clear",
            target=filename or "-",
            ip=client_ip(request),
            user_agent=request.headers.get("User-Agent", "")[:400],
        )
    )
    await db.commit()
    settings_store.invalidate()
    if filename:
        _logo_path(filename).unlink(missing_ok=True)
