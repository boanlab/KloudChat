"""The bootstrap administrator has to be an account that can then sign in.

`BOOTSTRAP_ADMIN_EMAIL` is the unattended path: fill it in and the first boot
creates an administrator rather than waiting for somebody to walk through the
signup screen. It accepted any string. `/auth/login` does not — `LoginRequest`
carries an `EmailStr`, which refuses the special-use domains a closed network
reaches for first.

The two ends disagreeing is not cosmetic. Once the row exists the instance has
accounts, so `signup` stops promoting the first one to administrator and
`seed_admin` returns early on every later boot. What is left is an
administrator nobody can sign in as and no way back that does not go through
psql.

So what is pinned here is an **equality**, not a list of bad domains: whatever
sign-in accepts the bootstrap accepts, and nothing else. The list belongs to
`email-validator` and moves with it — `.internal`, which looks exactly as
private as `.local`, is accepted today. A test that hardcoded the set would
pass while the two ends drifted apart again.
"""

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


#: Refused by the sign-in schema, and so unusable as a bootstrap address.
#: Every one of these is a plausible thing to write in `.env` on a closed
#: network, which is what makes the silent acceptance expensive.
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
    # Not every private-looking domain is refused, and the point of the
    # equality below is that this file does not have to know which.
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
    """`seed_admin` strips before it stores, so the check has to strip too.

    Otherwise a trailing newline out of a `.env` file — which is how most of
    them end — reads as a malformed address and refuses a perfectly good one.
    """
    assert bootstrap.usable_email("  admin@example.com\n")
