"""Creates the first administrator on an empty instance.

Runs only when the database holds no accounts at all: it must never overwrite
an account on an instance already in use, or roll a password back.

Left unconfigured it does nothing, and the original path — the first person to
sign up becomes administrator — stays in place. This exists for unattended
deployments, where nobody can walk through the signup screen.
"""

from __future__ import annotations

import logging

from sqlmodel import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.user import User, UserRole, UserStatus
from app.services import starter
from app.services.credits import grant_initial_allowance
from app.services.litellm import provision_user

log = logging.getLogger(__name__)


async def seed_admin() -> None:
    email = (settings.bootstrap_admin_email or "").lower().strip()
    password = settings.bootstrap_admin_password or ""
    if not email or not password:
        return

    async with SessionLocal() as db:
        if (await db.exec(select(User).limit(1))).first() is not None:
            # The instance already has accounts — leave it alone.
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
