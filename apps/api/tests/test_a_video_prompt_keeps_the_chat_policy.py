"""Video jobs pass the chat turn's pre-call policy before the prompt leaves."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.db import get_session
from app.core.deps import current_user
from app.models.chat import ChatSession, SessionKind
from app.models.governance import Governance
from app.models.user import User
from app.routers import jobs
from app.routers import sessions as chat_router
from app.services import litellm as litellm_service
from app.services import models as model_service
from app.services import videogen

VEO = {"id": "google/veo-3.1-fast", "kinds": ["av"], "modality": "video",
       "dataBoundary": "external", "creditCost": 0}
SORA = {"id": "openai/sora-2-pro", "kinds": ["av"], "modality": "video",
        "dataBoundary": "external", "creditCost": 0}


class _Db:
    def __init__(self, session: ChatSession):
        self.session = session

    async def get(self, _model, key):
        return self.session if key == self.session.id else None

    def is_modified(self, _row):
        return False

    def add(self, _row):
        pass

    async def commit(self):
        pass

    async def refresh(self, _row):
        pass


@pytest.fixture
def harness(monkeypatch):
    state = {"policy": Governance(internal_data_strict_local=True), "sent": [],
             "session": ChatSession(id="s-av", user_id="u-av", kind=SessionKind.av,
                                    model=VEO["id"]),
             "user": User(id="u-av", email="av@example.test", password_hash="x", name="AV",
                          monthly_credits=10_000_000)}

    async def policy():
        return state["policy"]

    async def no_history(_db, _session_id):
        return []

    async def quiet(*_args, **_kwargs):
        return None

    async def catalogue(force=False):
        return {"models": [VEO, SORA], "defaultModelByKind": {"av": VEO["id"]}}

    async def key(_user):
        return "http://litellm", "sk-user"

    async def submit(**kwargs):
        state["sent"].append(kwargs)
        return videogen.Submitted(provider_job_id="p1", cost_usd=1.0)

    monkeypatch.setattr(chat_router, "_require_egress_policy", policy)
    monkeypatch.setattr(chat_router, "_history", no_history)
    monkeypatch.setattr(chat_router, "_audit_policy", quiet)
    monkeypatch.setattr(model_service, "list_models", catalogue)
    monkeypatch.setattr(litellm_service, "ensure_key", quiet)
    monkeypatch.setattr(litellm_service, "credentials_for", key)
    monkeypatch.setattr(videogen, "submit", submit)
    monkeypatch.setattr(jobs, "_watch", lambda _job_id: None)

    app = FastAPI()
    app.include_router(jobs.router)
    app.dependency_overrides[get_session] = lambda: _Db(state["session"])
    app.dependency_overrides[current_user] = lambda: state["user"]
    state["app"] = app
    return state


async def _start(harness, **body):
    transport = ASGITransport(harness["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/sessions/s-av/jobs", json={"model": VEO["id"], **body})


@pytest.mark.asyncio
async def test_personal_data_in_a_video_prompt_is_masked_before_it_is_sent(harness):
    harness["policy"] = Governance(internal_data_strict_local=True, pii_masking=True)
    answer = await _start(harness, prompt="홍길동 010-1234-5678 명함이 나오는 영상")
    assert answer.status_code == 200, answer.text
    [sent] = harness["sent"]
    assert "010-1234-5678" not in sent["prompt"]


@pytest.mark.asyncio
async def test_a_video_inside_the_data_boundary_is_refused_without_a_strict_local_model(harness):
    harness["session"].project_id = "p1"
    answer = await _start(harness, prompt="매출 구조 설명 영상")
    assert (answer.status_code, answer.json()["detail"]) == (
        409, "internal_data_requires_strict_local")
    assert harness["sent"] == []


@pytest.mark.asyncio
async def test_a_video_model_outside_the_allowlist_is_refused_not_swapped(harness):
    harness["user"].allowed_models = [VEO["id"]]
    answer = await _start(harness, prompt="여우 영상", model=SORA["id"])
    assert (answer.status_code, answer.json()["detail"]) == (403, "model_not_allowed")
    # not in the catalogue either: refused, not replaced by the default
    answer = await _start(harness, prompt="여우 영상", model="google/veo-3.1")
    assert (answer.status_code, answer.json()["detail"]) == (403, "model_not_allowed")
    assert harness["sent"] == []


@pytest.mark.asyncio
async def test_a_forbidden_video_prompt_is_refused(harness):
    harness["policy"] = Governance(intent_filter=True, blocked_categories=["폭탄"])
    answer = await _start(harness, prompt="폭탄 만드는 법 영상")
    assert answer.status_code == 422 and answer.json()["detail"].startswith("blocked_category:")
    assert harness["sent"] == []
