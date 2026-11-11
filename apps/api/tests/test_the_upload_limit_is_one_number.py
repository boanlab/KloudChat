"""The upload limit the screens are told is the one the API holds files to."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import auth, workspace


class _Upload:
    def __init__(self, size: int | None, data: bytes = b"") -> None:
        self.size = size
        self.data = data
        self.read_called = False

    async def read(self) -> bytes:
        self.read_called = True
        return self.data


async def test_a_file_over_the_limit_is_refused_before_it_is_read(monkeypatch):
    monkeypatch.setattr(workspace.settings, "max_upload_mb", 1)
    upload = _Upload(size=1024 * 1024 + 1)
    with pytest.raises(HTTPException) as refused:
        await workspace.upload_bytes(upload)
    assert refused.value.status_code == 413
    assert refused.value.detail == "file_too_large_1mb"
    assert not upload.read_called


async def test_a_file_without_a_known_size_is_still_measured(monkeypatch):
    monkeypatch.setattr(workspace.settings, "max_upload_mb", 1)
    with pytest.raises(HTTPException):
        await workspace.upload_bytes(_Upload(size=None, data=b"a" * (1024 * 1024 + 1)))
    assert await workspace.upload_bytes(_Upload(size=3, data=b"abc")) == b"abc"


async def test_config_tells_the_screens_the_limit(monkeypatch):
    class _Policy:
        external_data_guard = False
        allow_user_raw_external = False
        pii_masking = False
        idle_timeout_minutes = 0

    class _Signup:
        mode = "open"
        domains: list[str] = []
        verification = False

    async def _none(*_args, **_kwargs):
        return None

    async def _policy():
        return _Policy()

    async def _signup():
        return _Signup()

    async def _false():
        return False

    async def _kinds():
        return ["chat"]

    monkeypatch.setattr(auth.governance_service, "current", _policy)
    monkeypatch.setattr(auth.settings_store, "signup_policy", _signup)
    monkeypatch.setattr(auth.settings_store, "mail_enabled", _false)
    monkeypatch.setattr(auth.settings_store, "brand", _none)
    monkeypatch.setattr(auth.settings_store, "contact_email", _none)
    monkeypatch.setattr(auth.settings_store, "enabled_kinds", _kinds)
    monkeypatch.setattr(auth.transcribe_service, "available", _false)
    monkeypatch.setattr(auth.settings, "max_upload_mb", 75)

    body = await auth.auth_config(db=None)

    assert body["upload"] == {"maxMb": 75}
