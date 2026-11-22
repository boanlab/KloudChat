"""An answer from an outside-model turn keeps what was sent, masked (`routing.egress`); never an
original value; nothing for strict-local; owner only."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from test_privacy import _block, _external_model, _NoWriteDb, _patch_guard_dependencies
from test_privacy import _request as _http_request
from test_strict_local_reads_the_persons_words_unmasked import _send, _strict

from app.models.chat import ChatSession, SessionKind
from app.models.governance import Governance
from app.models.user import User
from app.routers import sessions as sessions_router
from app.schemas.chat import SendMessage
from app.services import governance
from app.services import litellm as litellm_service

EMAIL = "sky.kim@example.com"
PHONE = "010-2345-6789"
FILE_EMAIL = "yujin@example.org"
GUARD = Governance(pii_masking=True, external_data_guard=True)


def _user_message(db) -> sessions_router.Message:
    return next(row for row in db.added if isinstance(row, sessions_router.Message)
                and row.role is sessions_router.Role.user)


@pytest.mark.asyncio
async def test_an_external_turn_keeps_the_masked_prompt_and_what_was_masked(monkeypatch):
    session = ChatSession(user_id="u1", model="external/model")
    captured, db = await _send(
        monkeypatch, session=session, models=[_external_model("external/model")],
        policy=GUARD, content=f"{EMAIL}, {PHONE} 로 연락해 줘",
        blocks=[_block("attachment", f"이름,이메일\n한유진,{FILE_EMAIL}")],
    )
    egress = captured["routing"]["egress"]
    counts = {row["category"]: row["count"] for row in egress["masked"]}
    assert counts == {"email": 2, "phone": 1}
    assert {row["label"] for row in egress["masked"]} == {"[이메일]", "[전화번호]"}
    parts = {part["source"]: part["text"] for part in egress["parts"]}
    assert "[이메일]" in parts["prompt"] and "[전화번호]" in parts["prompt"]
    assert "[이메일]" in parts["attachments"]
    record = json.dumps(egress, ensure_ascii=False)
    for raw in (EMAIL, PHONE, FILE_EMAIL):
        assert raw not in record
    # the question row carries the route, not a second copy of what was sent
    assert "egress" not in (_user_message(db).routing or {})


@pytest.mark.asyncio
async def test_a_strict_local_turn_records_nothing_sent_out(monkeypatch):
    session = ChatSession(user_id="u2", model="strict-local/qwen")
    captured, _db = await _send(
        monkeypatch, session=session, models=[_strict()], policy=GUARD,
        content=f"{EMAIL} 확인해 줘",
    )
    assert "egress" not in captured["routing"]


def test_the_record_is_cut_and_shows_only_parts_the_mask_touched():
    findings = [governance.Finding(category="email", source="current_input", count=1),
                governance.Finding(category="api_key", source="tool_output", count=4)]
    record = sessions_router._egress_record(findings, [
        ("prompt", ["[이메일] 확인"]),
        ("material", ["깨끗한 참고 자료"]),
        ("conversation_history", ["x" * 30_000 + "[이메일]"]),
        ("instructions", ["[이메일]" * 10_000]),
    ])
    assert record["masked"] == [{"category": "email", "label": "[이메일]", "count": 1}]
    parts = {part["source"]: part for part in record["parts"]}
    assert "material" not in parts
    assert len(parts["conversation_history"]["text"]) == sessions_router._EGRESS_PART_CHARS
    assert parts["conversation_history"]["truncated"] is True
    assert sum(len(p["text"]) for p in record["parts"]) <= sessions_router._EGRESS_TOTAL_CHARS
    assert sessions_router._egress_record([], [("prompt", ["x"])]) is None


@pytest.mark.parametrize("model", [_external_model("external/report"), _strict()],
                         ids=["external", "strict-local"])
@pytest.mark.asyncio
async def test_a_report_keeps_what_it_sent_out_and_nothing_when_it_stayed_inside(
        monkeypatch, model):
    model = {**model, "kinds": ["report"]}
    user = User(id="u3", email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(user_id=user.id, kind=SessionKind.report, model=model["id"])
    await _patch_guard_dependencies(monkeypatch, session=session, models=[model], blocks=[])

    async def current(*_args, **_kwargs):
        return GUARD

    async def ensure_key(*_args, **_kwargs):
        return None

    async def credentials(*_args, **_kwargs):
        return "http://litellm.test", "key"

    captured: dict = {}

    async def run_document(**kwargs):
        captured.update(kwargs)
        yield 'data: {"type":"done"}\n\n'

    class AcceptedDb(_NoWriteDb):
        def is_modified(self, _value):
            return False

    monkeypatch.setattr(sessions_router.governance, "current_for_egress", current)
    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions_router, "_run_report", run_document)
    response = await sessions_router.send_message(
        session.id, SendMessage(content=f"{EMAIL} 고객 응대 보고서"), _http_request(), user,
        AcceptedDb())
    async for _chunk in response.body_iterator:
        pass

    routing = captured["routing"] or {}
    if model["id"].startswith("strict-local/"):
        assert EMAIL in captured["request"]
        assert "egress" not in routing
    else:
        assert EMAIL not in captured["request"]
        egress = routing["egress"]
        assert egress["masked"] == [{"category": "email", "label": "[이메일]", "count": 1}]
        assert "[이메일]" in egress["parts"][0]["text"]
        assert EMAIL not in json.dumps(egress, ensure_ascii=False)


@pytest.mark.asyncio
async def test_only_the_conversations_owner_reads_its_messages():
    owner_session = ChatSession(id="s9", user_id="owner")

    class Db:
        async def get(self, _model, _id):
            return owner_session

    stranger = User(id="stranger", email="x@example.test", password_hash="h", name="X")
    with pytest.raises(HTTPException) as refused:
        await sessions_router.list_messages("s9", stranger, Db())
    assert refused.value.status_code == 404
