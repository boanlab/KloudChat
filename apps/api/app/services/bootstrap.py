"""Creates the first administrator on an instance with no accounts.

Unconfigured, it does nothing and the first account to sign up becomes
administrator. It never touches an instance that already has accounts.
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

#: The same validator behind `LoginRequest.email`, so bootstrap and sign-in agree.
_EMAIL = TypeAdapter(EmailStr)


def usable_email(value: str) -> bool:
    """Whether `POST /auth/login` would accept this address."""
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
            return

        if not usable_email(email):
            # An administrator that cannot sign in would also block the
            # first-signup-becomes-admin path, so refuse instead.
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
        # The administrator owns the shared catalogue of agents and skills.
        await starter.seed_catalog(db, user.id)
        db.add(user)
        await db.commit()

    log.info("bootstrap admin created: %s", email)
