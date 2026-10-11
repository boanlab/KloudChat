"""A browser with no refresh cookie is told it is signed out with 204, not 401.

Every first visit asks `/auth/refresh`; the 401 was logged as an error in the console
(research lane R-25). A presented cookie is still checked as before.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_session
from app.routers import auth


def test_no_cookie_is_a_quiet_signed_out():
    app = FastAPI()
    app.include_router(auth.router)

    async def no_db():
        # Nothing is read for a browser with no cookie.
        yield None

    app.dependency_overrides[get_session] = no_db
    response = TestClient(app).post(f"{auth.router.prefix}/refresh")
    assert response.status_code == 204
    assert response.content == b""
