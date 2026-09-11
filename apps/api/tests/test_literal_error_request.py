"""The user-facing request path, not an injected calculation_expression parameter."""

import json

import pytest
from test_privacy import _external_model, _NoWriteDb, _patch_guard_dependencies, _request
from test_tool_result_answer import ORIGIN, _database

from app.models.chat import Message
from app.routers import sessions
from app.schemas.chat import SendMessage
from app.services import agent
from app.services.tools.arithmetic import CALCULATE


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("expression", ["12 / 0", "12 / (3 - 3)"])
async def test_literal_failure_reaches_tool_answer_from_the_public_request(
    monkeypatch, strict, expression
):
    user, session, _, _, added = _database(monkeypatch)
    model = {
        **_external_model("synthetic/model"),
        "supportsTools": True,
        "inputCreditCost": 10,
        "creditCost": 20,
        "contextWindow": 64000,
        "strictLocal": strict,
        "dataBoundary": "self_hosted" if strict else "external",
    }
    session.model = model["id"]
    await _patch_guard_dependencies(monkeypatch, session=session, models=[model], blocks=[])
    unexpected = []

    async def tools(*_args, **_kwargs):
        return [CALCULATE]

    async def key(*_args, **_kwargs):
        return "synthetic-unused-key"

    async def credentials(*_args, **_kwargs):
        return "http://unused.test", "synthetic-unused-key"

    async def forbidden_stream(*_args, **_kwargs):
        unexpected.append("model")
        raise RuntimeError("unexpected model attempt")
        yield

    def forbidden(*_args, **_kwargs):
        unexpected.append("accounting")
        return 0

    async def forbidden_enrichment(*_args, **_kwargs):
        unexpected.append("enrichment")
        return None

    class Db(_NoWriteDb):
        def is_modified(self, _row):
            return False

    monkeypatch.setattr(sessions, "build_tools", tools)
    monkeypatch.setattr(sessions, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", key)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions.litellm_service, "user_key", lambda _user: "synthetic-unused-key")
    monkeypatch.setattr(agent, "_stream_once", forbidden_stream)
    monkeypatch.setattr(sessions, "record_searches", lambda *_args, **_kwargs: None)
    for name in ["settle", "charge_for_tokens"]:
        monkeypatch.setattr(sessions, name, forbidden)
    for name in ["_store_artifacts", "_enrich_memory", "_enrichment_model", "_store_notes"]:
        monkeypatch.setattr(sessions, name, forbidden_enrichment)
    response = await sessions.send_message(
        session.id,
        SendMessage(
            content=f"{expression}은 얼마야? 짧게 답해줘. 파일은 만들지 마.", web_search=False
        ),
        _request(),
        user,
        Db(),
    )
    chunks = [chunk async for chunk in response.body_iterator]
    events = [json.loads(chunk.removeprefix("data: ").strip()) for chunk in chunks]
    assert unexpected == []
    answer = next(row for row in added if isinstance(row, Message))
    assert {"type": "tool_result_answer", **ORIGIN} in events
    assert answer.model is None and answer.routing["answerOrigin"] == "tool_result"
    assert "0으로 나누는 계산은 정의되지" in answer.content
    assert answer.usage == {"inputTokens": 0, "outputTokens": 0, "credits": 0}
    assert answer.artifact_ids is None and answer.failure is None
