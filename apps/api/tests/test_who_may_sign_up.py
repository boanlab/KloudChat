"""Sign-up domain allow-list and email verification are administrator settings."""

from __future__ import annotations

import pytest

from app.services import settings_store
from app.services.settings_store import SignupPolicy, parse_domains


def test_domains_are_read_loosely_and_kept_tidy():
    assert parse_domains("@dankook.ac.kr, Example.COM;  kloud.zone\nexample.com") == [
        "dankook.ac.kr",
        "example.com",
        "kloud.zone",
    ]
    assert parse_domains("") == []


def test_an_empty_list_lets_anyone_in_and_a_list_is_exact():
    anyone = SignupPolicy("approval", "environment", [], False, False)
    assert anyone.allows("someone@anywhere.org")

    campus = SignupPolicy("approval", "database", ["dankook.ac.kr"], False, False)
    assert campus.allows("student@DANKOOK.ac.kr")
    assert not campus.allows("student@mail.dankook.ac.kr")
    assert not campus.allows("student@gmail.com")
    assert not campus.allows("no-at-sign")


@pytest.mark.anyio
async def test_verification_needs_a_mail_server(monkeypatch):
    async def values(force: bool = False):
        return {
            settings_store.SIGNUP_MODE: "open",
            settings_store.SIGNUP_DOMAINS: "kloud.zone",
            settings_store.SIGNUP_VERIFY_EMAIL: "on",
        }

    monkeypatch.setattr(settings_store, "all_values", values)

    async def no_mail():
        return False

    monkeypatch.setattr(settings_store, "mail_enabled", no_mail)
    policy = await settings_store.signup_policy()
    assert policy.mode == "open" and policy.mode_source == "database"
    assert policy.domains == ["kloud.zone"]
    assert policy.verify_email and not policy.verification

    async def mail():
        return True

    monkeypatch.setattr(settings_store, "mail_enabled", mail)
    assert (await settings_store.signup_policy()).verification


@pytest.mark.anyio
async def test_the_environment_mode_stands_until_the_database_says_otherwise(monkeypatch):
    async def values(force: bool = False):
        return {}

    monkeypatch.setattr(settings_store, "all_values", values)
    monkeypatch.setattr(settings_store.env_settings, "signup_mode", "closed")
    policy = await settings_store.signup_policy()
    assert policy.mode == "closed" and policy.mode_source == "environment"
    assert policy.domains == [] and not policy.verify_email
