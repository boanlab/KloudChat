"""A comparison turn's `done` event carries the stored message id."""

from __future__ import annotations

import json

import pytest

from app.models.chat import Message, Role
from app.models.user import User
from app.routers import sessions as sessions_router


class _Db:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0
        self.user = User(id="user-1", email="p@example.test", password_hash="h", name="P")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def add(self, row):
        self.added.append(row)

    async def get(self, model, key):
        if model is User and key == self.user.id:
            return self.user
        return None

    async def commit(self):
        self.commits += 1


def _model(model_id: str) -> dict:
    return {
        "id": model_id,
        "label": model_id,
        "kinds": ["chat"],
        "dataBoundary": "external",
        "strictLocal": False,
        "inputCreditCost": 1,
        "creditCost": 1,
    }


@pytest.mark.asyncio
async def test_done_carries_the_stored_message_id(monkeypatch) -> None:
    db = _Db()

    async def stream_completion(model_id, *_args, **_kwargs):
        yield {"type": "delta", "text": f"{model_id}의 답"}
        yield {"type": "usage", "inputTokens": 10, "outputTokens": 5}

    monkeypatch.setattr(sessions_router, "SessionLocal", lambda: db)
    monkeypatch.setattr(sessions_router.chat_service, "stream_completion", stream_completion)

    chunks = [
        chunk
        async for chunk in sessions_router._run_comparison(
            user_id=db.user.id,
            api_key="virtual-key",
            session_id="session-1",
            models=[_model("m/one"), _model("m/two")],
            messages=[{"role": "user", "content": "질문"}],
            routing={},
        )
    ]

    events = [
        json.loads(line[len("data: ") :])
        for chunk in chunks
        for line in chunk.splitlines()
        if line.startswith("data: ")
    ]
    done = events[-1]
    assert done["type"] == "done"

    stored = next(r for r in db.added if isinstance(r, Message) and r.role is Role.assistant)
    assert done["messageId"] == stored.id
    # Default until somebody chooses: the first successful column.
    assert [v["chosen"] for v in stored.variants] == [True, False]
    assert db.commits == 1
