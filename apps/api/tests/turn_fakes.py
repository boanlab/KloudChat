"""In-memory fakes for driving one turn through the session routes."""

from __future__ import annotations

from fastapi import Request

from app.models.chat import ChatSession
from app.models.governance import Governance
from app.models.user import User, UserStatus
from app.models.workspace import Memory, Skill, SkillSource
from app.routers import sessions as sessions_router


class _Result:
    def __init__(self, rows: list | None = None):
        self._rows = rows or []

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    """In-memory db for one context and one turn."""

    def __init__(
        self,
        session: ChatSession,
        *,
        skills: list[Skill] | None = None,
        memories: list[Memory] | None = None,
    ):
        self.session = session
        self.skills = skills or []
        self.memories = memories or []
        self.added: list[object] = []
        self.commits = 0

    async def get(self, model, row_id):
        if model is ChatSession and row_id == self.session.id:
            return self.session
        return None

    async def exec(self, query):
        table = query.get_final_froms()[0].name
        if table == "skills":
            return _Result(self.skills)
        if table == "memories":
            return _Result(self.memories)
        if table in {"messages", "files"}:
            return _Result()
        raise AssertionError(f"unexpected query: {query}")

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1

    def is_modified(self, _row):
        return False


def _user() -> User:
    return User(
        id="user-1",
        email="user@example.com",
        password_hash="hash",
        name="User",
        status=UserStatus.active,
        monthly_credits=100,
    )


def _skill() -> Skill:
    return Skill(
        id="skill-1",
        owner_id="user-1",
        name="검증",
        slug="검증",
        body="검증한다.",
        source=SkillSource.personal,
        kinds=["chat"],
        required_tools=[],
        estimated_tokens=12,
        enabled=True,
    )


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "query_string": b"",
        }
    )


def _patch_route_services(monkeypatch, upstream: dict[str, int]) -> None:
    async def policy():
        return Governance()

    async def models():
        return {
            "models": [
                {
                    "id": model_id,
                    "kinds": ["chat", "report", "slides"],
                    "supportsTools": False,
                    "creditCost": 1,
                    "inputCreditCost": 1,
                }
                for model_id in ("model-a", "model-b")
            ]
        }

    async def ensure_key(_user):
        upstream["calls"] += 1

    async def credentials(_user):
        upstream["calls"] += 1
        return "http://unused", "unused"

    monkeypatch.setattr(sessions_router.governance, "current_for_egress", policy)
    monkeypatch.setattr(sessions_router.model_service, "list_models_for_egress", models)
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(sessions_router.litellm_service, "credentials_for", credentials)
