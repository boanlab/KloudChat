"""Org-wide masking (`pii_masking`) is for what leaves the boundary: a turn that stays on
strict-local models reads the person's words, files and history as given, and its answer
keeps the values the person gave. Anything that goes out (an outside model, a web search
query) is still masked."""
from __future__ import annotations

import json

import pytest
from test_privacy import (
    _block,
    _external_model,
    _NoWriteDb,
    _patch_guard_dependencies,
    _request,
)

from app.models.chat import ChatSession, RoutingMode
from app.models.governance import Governance
from app.models.user import User
from app.routers import sessions as sessions_router
from app.schemas.chat import SendMessage
from app.services import litellm as litellm_service
from app.services import search_consent

EMAIL = "sky.kim@example.com"
FILE_EMAIL = "yujin@example.org"


def _strict(model_id: str = "strict-local/qwen") -> dict:
    return {**_external_model(model_id), "dataBoundary": "self_hosted", "strictLocal": True}


class _AcceptedDb(_NoWriteDb):
    def is_modified(self, _value):
        return False


async def _send(monkeypatch, *, session, models, policy, content, blocks=()):
    """One chat turn through `send_message`: what `_run_turn` was handed, and the db."""
    await _patch_guard_dependencies(monkeypatch, session=session, models=models,
                                    blocks=list(blocks))

    async def current(*_args, **_kwargs):
        return policy

    async def ensure_key(*_args, **_kwargs):
        return None

    async def credentials(*_args, **_kwargs):
        return "http://litellm.test", "key"

    async def build_tools(*_args, **_kwargs):
        return []

    captured: dict = {}

    async def no_events():
        return
        yield b""  # pragma: no cover - shape only

    def run_turn(**kwargs):
        captured.update(kwargs)
        return no_events()

    monkeypatch.setattr(sessions_router.governance, "current_for_egress", current)
    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions_router, "build_tools", build_tools)
    monkeypatch.setattr(sessions_router, "_run_turn", run_turn)
    db = _AcceptedDb()
    response = await sessions_router.send_message(
        session.id,
        SendMessage(content=content),
        _request(),
        User(id=session.user_id, email="person@example.test", password_hash="hash",
             name="Person"),
        db,
    )
    assert response.status_code == 200, getattr(response, "body", b"")
    return captured, db


def _sent(captured: dict) -> str:
    return json.dumps(captured["messages"], ensure_ascii=False)


def _stored_question(db) -> str:
    rows = [row for row in db.added if isinstance(row, sessions_router.Message)
            and row.role is sessions_router.Role.user]
    return rows[-1].content


@pytest.mark.asyncio
async def test_a_strict_local_model_the_person_picked_reads_their_email_unmasked(monkeypatch):
    session = ChatSession(user_id="u1", model="strict-local/qwen")
    captured, db = await _send(
        monkeypatch, session=session, models=[_strict()],
        policy=Governance(pii_masking=True, external_data_guard=True),
        content=f"{EMAIL} 형식이 맞는지 봐 줘",
        blocks=[_block("attachment", f"이름,이메일\n한유진,{FILE_EMAIL}")],
    )
    sent = _sent(captured)
    assert EMAIL in sent and FILE_EMAIL in sent
    assert "[이메일]" not in sent
    # stored as typed, so the next turn's history reads it too
    assert EMAIL in _stored_question(db)
    # nothing was masked going in: the answer at rest masks no value the person gave
    assert captured["protected_values"] == frozenset()
    assert captured["routing"]["action"] == "strict_local"


@pytest.mark.asyncio
async def test_internal_material_forced_to_strict_local_is_read_unmasked(monkeypatch):
    hybrid = {**_external_model("local/qwen"), "dataBoundary": "hybrid"}
    session = ChatSession(user_id="u2", model="local/qwen", project_id="p1")
    captured, db = await _send(
        monkeypatch, session=session, models=[hybrid, _strict()],
        policy=Governance(pii_masking=True, internal_data_strict_local=True),
        content=f"{EMAIL} 회원 정리해 줘",
        blocks=[_block("attachment", f"한유진,{FILE_EMAIL}")],
    )
    assert captured["model"]["id"] == "strict-local/qwen"
    assert captured["routing"]["action"] == "internal_strict_local"
    sent = _sent(captured)
    assert EMAIL in sent and FILE_EMAIL in sent and "[이메일]" not in sent
    assert EMAIL in _stored_question(db)
    assert captured["protected_values"] == frozenset()


@pytest.mark.asyncio
async def test_an_external_model_still_gets_the_email_masked(monkeypatch):
    session = ChatSession(user_id="u3", model="external/model")
    captured, db = await _send(
        monkeypatch, session=session, models=[_external_model("external/model")],
        policy=Governance(pii_masking=True, external_data_guard=True),
        content=f"{EMAIL} 형식이 맞는지 봐 줘",
        blocks=[_block("user_instructions", f"내 주소는 {FILE_EMAIL}")],
    )
    sent = _sent(captured)
    assert EMAIL not in sent and FILE_EMAIL not in sent
    assert "[이메일]" in sent
    assert captured["routing"]["action"] == "mask_external"
    assert EMAIL in captured["protected_values"]
    assert EMAIL not in _stored_question(db)


@pytest.mark.asyncio
async def test_a_super_agent_strict_conversation_is_not_masked():
    """`super_strict` picks from the strict column only: the privacy step sees a strict-local
    model and masks nothing; its reports and decks count as inside too."""
    user = User(id="u4", email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(id="s4", user_id=user.id, routing_mode=RoutingMode.super_strict)
    policy = Governance(pii_masking=True, external_data_guard=True)
    strict = _strict()
    resolved = await sessions_router._resolve_privacy(
        user=user, session=session, policy=policy, catalogue=[strict], requested=[strict],
        sources={"current_input": f"{EMAIL} 확인"}, explicit_action=None,
        decision_token=None,
    )
    assert resolved.action == "strict_local" and resolved.mask_outbound is False
    assert resolved.findings  # still counted, never hidden
    assert sessions_router._document_stays_inside(
        session, _external_model("external/model"), policy, [], None, [])
    # plain Super Agent: its writer is the table's pick, so the picked model alone is no proof
    plain = ChatSession(id="s5", user_id=user.id, routing_mode=RoutingMode.super,
                        model=strict["id"])
    assert not sessions_router._document_stays_inside(plain, strict, policy, [], None, [])
    manual = ChatSession(id="s6", user_id=user.id, model=strict["id"])
    assert sessions_router._document_stays_inside(manual, strict, policy, [], None, [])
    assert not sessions_router._document_stays_inside(
        manual, _external_model("external/model"), policy, [], None, [])


def test_a_web_search_query_from_a_strict_local_turn_is_still_masked():
    """Only the query leaves a strict-local turn, and it is vetted on the way out."""
    vetted = search_consent.vet([f"{EMAIL} 가입 확인 방법"], material=[], legacy=True)
    assert EMAIL not in vetted[0].text
    assert "[이메일]" in vetted[0].text
