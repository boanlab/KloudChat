"""Every password form holds the same rule, and the sign-in screens read it from `/auth/config`
instead of repeating it: a person told 「10자 이상」 is never refused for something else."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.routers import auth
from app.schemas.admin import ResetPasswordRequest
from app.schemas.auth import (
    PASSWORD_MIN_LENGTH,
    PasswordChange,
    PasswordReset,
    SignupRequest,
)

_FORMS = [
    lambda pw: SignupRequest(email="a@example.com", password=pw, name="가"),
    lambda pw: PasswordChange(current_password="old-password", new_password=pw),
    lambda pw: PasswordReset(token="t" * 32, new_password=pw),
    lambda pw: ResetPasswordRequest(password=pw),
]


def test_the_rule_is_ten_characters():
    assert PASSWORD_MIN_LENGTH == 10


@pytest.mark.parametrize("form", _FORMS)
def test_every_form_refuses_one_short_and_takes_the_minimum(form):
    with pytest.raises(ValidationError):
        form("a" * (PASSWORD_MIN_LENGTH - 1))
    # Letters only: no digit or symbol is asked for, so none is checked.
    form("a" * PASSWORD_MIN_LENGTH)


async def test_config_hands_the_rule_and_the_lockout_to_the_screens(monkeypatch):
    class _Policy:
        external_data_guard = False
        allow_user_raw_external = False
        pii_masking = False
        idle_timeout_minutes = 0

    class _Signup:
        mode = "open"
        domains: list[str] = []
        verification = False

    async def _value(*_args, **_kwargs):
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
    monkeypatch.setattr(auth.settings_store, "brand", _value)
    monkeypatch.setattr(auth.settings_store, "contact_email", _value)
    monkeypatch.setattr(auth.settings_store, "enabled_kinds", _kinds)
    monkeypatch.setattr(auth.transcribe_service, "available", _false)
    monkeypatch.setattr(auth.settings, "login_max_failures", 5)
    monkeypatch.setattr(auth.settings, "login_lockout_min", 15)

    body = await auth.auth_config(db=None)

    assert body["password"] == {"minLength": PASSWORD_MIN_LENGTH}
    assert body["login"] == {"maxFailures": 5, "lockoutMinutes": 15}
    assert body["signup"]["mode"] == "open"


@pytest.mark.parametrize(("users", "available"), [(3, False), (0, True)])
async def test_a_closed_signup_is_offered_only_for_the_first_account(monkeypatch, users, available):
    class _Policy:
        external_data_guard = False
        allow_user_raw_external = False
        pii_masking = False
        idle_timeout_minutes = 0

    class _Signup:
        mode = "closed"
        domains: list[str] = []
        verification = False

    async def _value(*_args, **_kwargs):
        return None

    async def _policy():
        return _Policy()

    async def _signup():
        return _Signup()

    async def _false():
        return False

    async def _kinds():
        return ["chat"]

    async def _count(_db):
        return users

    monkeypatch.setattr(auth.governance_service, "current", _policy)
    monkeypatch.setattr(auth.settings_store, "signup_policy", _signup)
    monkeypatch.setattr(auth.settings_store, "mail_enabled", _false)
    monkeypatch.setattr(auth.settings_store, "brand", _value)
    monkeypatch.setattr(auth.settings_store, "contact_email", _value)
    monkeypatch.setattr(auth.settings_store, "enabled_kinds", _kinds)
    monkeypatch.setattr(auth.transcribe_service, "available", _false)
    monkeypatch.setattr(auth, "user_count", _count)

    body = await auth.auth_config(db=None)

    assert body["signup"]["available"] is available
