"""Unretrieved current claims never stream or persist through comparison columns."""

import json
from copy import deepcopy

import pytest
from test_grounded_answer_runtime import _persistence, _visible_text
from test_privacy import _external_model

from app.models.chat import Message
from app.routers import sessions
from app.services import current_evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    "현재 책임자는 UNSUPPORTED_NAME입니다.",
    "- 과거 사실: 2020년에 가상 조직은 설립되었습니다.\n"
    "- 현재 상태: UNSUPPORTED_NAME입니다.",
    "",
])
async def test_current_comparison_displays_only_constrained_background(monkeypatch, raw):
    user, session, _, rows, _, _ = _persistence(monkeypatch)
    model = _external_model("synthetic/local")
    request = "현재 Acme의 CEO는 누구야?"
    messages = [{"role": "user", "content": request}]
    before = deepcopy(messages)
    captured = []

    async def complete(_model, envelope, *_args, **_kwargs):
        captured.append(envelope)
        yield {"type": "delta", "text": raw}
        yield {"type": "usage", "inputTokens": 10, "outputTokens": 20}

    monkeypatch.setattr(sessions.chat_service, "stream_completion", complete)
    events = [
        json.loads(chunk.removeprefix("data: ").strip())
        async for chunk in sessions._run_comparison(
            user_id=user.id, session_id=session.id, api_key="synthetic",
            models=[model], messages=messages, routing={}, current_fact_request=request,
        )
    ]
    expected = current_evidence.render(raw, request)
    assert _visible_text(events, model=model["id"]) == expected
    assert "UNSUPPORTED_NAME" not in json.dumps(events)
    stored = next(row for row in rows if isinstance(row, Message))
    assert stored.content == expected
    assert stored.variants[0]["usage"] == {"inputTokens": 10, "outputTokens": 20}
    assert messages == before
    assert len(captured) == 1
    assert all(message["role"] != "system" for message in captured[0][1:])


@pytest.mark.asyncio
async def test_failed_current_column_does_not_persist_a_hidden_raw_claim(monkeypatch):
    user, session, _, rows, _, _ = _persistence(monkeypatch)
    model = _external_model("synthetic/local")

    async def complete(*_args, **_kwargs):
        yield {"type": "delta", "text": "UNSUPPORTED_PARTIAL"}
        raise sessions.chat_service.ChatStreamError("synthetic_error")

    monkeypatch.setattr(sessions.chat_service, "stream_completion", complete)
    events = [
        json.loads(chunk.removeprefix("data: ").strip())
        async for chunk in sessions._run_comparison(
            user_id=user.id, session_id=session.id, api_key="synthetic",
            models=[model], messages=[{"role": "user", "content": "현재 대통령"}],
            routing={}, current_fact_request="현재 대통령",
        )
    ]
    assert "UNSUPPORTED_PARTIAL" not in json.dumps(events)
    for row in rows:
        if isinstance(row, Message):
            assert "UNSUPPORTED_PARTIAL" not in row.content
            assert "UNSUPPORTED_PARTIAL" not in json.dumps(row.variants)
