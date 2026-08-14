"""Service name and logo.

The logo is kept as a file and only its name is stored in settings. Putting the
image itself in the settings table would mix a value needed once with the values
read on every request, in the same cache.

Reading the logo requires no authentication — the sign-in screen has to render
it before anyone is authenticated.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status

from app.core.config import settings
from app.core.deps import AdminUser, DbSession, client_ip
from app.models.user import AuditEvent
from app.services import settings_store

log = logging.getLogger(__name__)

router = APIRouter(tags=["branding"])

#: Only formats a browser renders inline and cannot execute script from.
#: SVG is excluded because it can carry script — even uploaded by an
#: administrator, that file loads on every visitor's sign-in screen.
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
    """The current logo, or 404 — on which the UI draws the default mark."""
    values = await settings_store.all_values()
    filename = values.get(settings_store.BRAND_LOGO, "")
    mime = values.get(settings_store.BRAND_LOGO_MIME, "")
    if not filename or not mime:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no_logo")
    try:
        data = _logo_path(filename).read_bytes()
    except OSError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no_logo") from None
    # Changing the file changes the filename and so the URL, which is what
    # makes a long cache lifetime safe here.
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "public, max-age=86400"})


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
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"{MAX_BYTES // (1024 * 1024)}MB 이하만 올릴 수 있습니다.",
        )

    # The filename carries a hash of the contents, so replacing the logo
    # changes the URL and no browser cache holds on to the old one.
    previous = (await settings_store.all_values()).get(settings_store.BRAND_LOGO, "")
    digest = hashlib.sha256(data).hexdigest()[:12]
    filename = f"logo-{digest}{ALLOWED[mime]}"
    _logo_dir().mkdir(parents=True, exist_ok=True)
    _logo_path(filename).write_bytes(data)

    await settings_store.put(db, settings_store.BRAND_LOGO, filename, admin.id)
    await settings_store.put(db, settings_store.BRAND_LOGO_MIME, mime, admin.id)
    db.add(AuditEvent(actor_id=admin.id, action="branding.logo", target=filename,
                      detail=f"{len(data)}B", ip=client_ip(request)))
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
    db.add(AuditEvent(actor_id=admin.id, action="branding.logo.clear", target=filename or "-",
                      ip=client_ip(request)))
    await db.commit()
    settings_store.invalidate()
    if filename:
        _logo_path(filename).unlink(missing_ok=True)
