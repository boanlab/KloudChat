"""Creates the first administrator on an empty instance.

Runs only when the database holds no accounts at all: it must never overwrite
an account on an instance already in use, or roll a password back.

Left unconfigured it does nothing, and the original path — the first person to
sign up becomes administrator — stays in place. This exists for unattended
deployments, where nobody can walk through the signup screen.
"""

from __future__ import annotations

import logging

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlmodel import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.user import User, UserRole, UserStatus
from app.services import starter
from app.services.credits import grant_initial_allowance
from app.services.litellm import provision_user

log = logging.getLogger(__name__)

#: The sign-in schema's own rule, reached directly rather than restated.
#: `LoginRequest.email` is an `EmailStr` and this is the validator behind it,
#: which is what keeps the two ends from drifting apart again.
_EMAIL = TypeAdapter(EmailStr)


def usable_email(value: str) -> bool:
    """Whether `POST /auth/login` would accept this address.

    The bootstrap used to take any string while sign-in refused the special-use
    domains a closed network reaches for first, so `admin@kchat.local` produced
    an administrator that could never sign in. By then the instance had an
    account, which is also what stops `signup` promoting the next person and
    makes `seed_admin` return early on every later boot — no administrator, and
    no way back that does not go through psql.

    Asked of the validator rather than of a list of domains, because the list
    belongs to `email-validator` and moves: `.internal` looks exactly as
    private as `.local` and is accepted today.
    """
    try:
        _EMAIL.validate_python((value or "").strip())
    except ValidationError:
        return False
    return True


async def seed_admin() -> None:
    email = (settings.bootstrap_admin_email or "").lower().strip()
    password = settings.bootstrap_admin_password or ""
    if not email or not password:
        return

    async with SessionLocal() as db:
        if (await db.exec(select(User).limit(1))).first() is not None:
            # The instance already has accounts — leave it alone.
            return

        if not usable_email(email):
            # Refused rather than created. An administrator sign-in cannot
            # accept is worse than none at all, because writing the row is also
            # what closes the path that would have produced a working one — the
            # first person to sign up. Error level because nothing else reports
            # it: the instance comes up, answers /health, and looks well.
            log.error(
                "BOOTSTRAP_ADMIN_EMAIL=%r is not an address this instance can sign in "
                "with, so no administrator was created. Correct it, or leave it blank "
                "and let the first account to sign up become the administrator.",
                email,
            )
            return

        user = User(
            email=email,
            password_hash=hash_password(password),
            name=settings.bootstrap_admin_name,
            role=UserRole.admin,
            status=UserStatus.active,
        )
        db.add(user)
        await db.flush()

        grant_initial_allowance(db, user, settings.default_monthly_credits)
        await provision_user(user)
        await starter.seed_designs(db, user.id)
        # This account is the instance's administrator, so it is where the
        # shared catalogue of agents and skills lives.
        await starter.seed_catalog(db, user.id)
        db.add(user)
        await db.commit()

    log.info("bootstrap admin created: %s", email)
