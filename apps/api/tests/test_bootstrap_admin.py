"""`bootstrap.usable_email` accepts exactly the addresses `LoginRequest` accepts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.auth import LoginRequest
from app.services import bootstrap


def _sign_in_accepts(address: str) -> bool:
    """Whether `POST /auth/login` would get past its own schema."""
    try:
        LoginRequest(email=address, password="long-enough-password")
    except ValidationError:
        return False
    return True


#: Refused by the sign-in schema.
REFUSED = [
    "admin@kchat.local",
    "admin@kchat.test",
    "admin@localhost",
    "admin@kchat.invalid",
    "관리자",
    "admin@",
    "",
    "   ",
]

ACCEPTED = [
    "admin@example.com",
    "admin@dankook.ac.kr",
    "first.last+tag@sub.example.org",
    # email-validator accepts `.internal`; the equality test does not hardcode the set.
    "admin@kchat.internal",
]


# ── the invariant ──────────────────────────────────────────────────────


@pytest.mark.parametrize("address", REFUSED + ACCEPTED)
def test_the_bootstrap_takes_exactly_what_sign_in_takes(address):
    assert bootstrap.usable_email(address) is _sign_in_accepts(address)


# ── each end, stated on its own ────────────────────────────────────────


@pytest.mark.parametrize("address", REFUSED)
def test_an_address_sign_in_would_refuse_never_becomes_an_account(address):
    assert not bootstrap.usable_email(address)


@pytest.mark.parametrize("address", ACCEPTED)
def test_an_ordinary_address_still_bootstraps(address):
    assert bootstrap.usable_email(address)


def test_surrounding_space_is_not_what_makes_an_address_unusable():
    """Surrounding whitespace is stripped before validation, as `seed_admin` does."""
    assert bootstrap.usable_email("  admin@example.com\n")
