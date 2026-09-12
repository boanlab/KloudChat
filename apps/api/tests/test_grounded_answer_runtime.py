"""Model-backed answers keep their body, accounting and final accuracy notice."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from test_privacy import _external_model

from app.models.chat import ChatSession, Message, Role, TurnFailure
from app.models.user import User
from app.routers import sessions
from app.services import freshness

QUESTIONS = [
    "현재 대한민국 대통령은 누구야?",
    "오늘 서울 날씨는 어때?",
    "파이썬 최신 버전은 무엇이야?",
    "이 과학 이론의 근거를 설명해줘.",
    "제공된 자료에서 확인할 수 있는 사실만 설명해줘.",
    "경제 상황의 변화를 알려줘.",
    "Explain how gravity works.",
    "Explain the history of printing.",
]
BODY = "확인할 수 있는 배경을 설명하고, 알 수 없는 세부 사항은 구분합니다."


def _persistence(monkeypatch):
    user = User(id="accuracy-user", email="accuracy@example.test", password_hash="synthetic")
    session = ChatSession(id="accuracy-session", user_id=user.id)
    question = Message(id="accuracy-question", session_id=session.id, role=Role.user, content="Q")
    added, settled, artifacts = [], [], []

    class Db:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, key):
            if model is User and key == user.id:
                return user
            if model is ChatSession and key == session.id:
                return session
            if model is Message and key == question.id:
                return question
            return None

        def add(self, row):
            added.append(row)

        async def commit(self):
            pass

    async def store(**kwargs):
        artifacts.append(kwargs["content"])
        return None

    async def no_memory(**_kwargs):
        return None

    def settle(*_args, **kwargs):
        settled.append(kwargs)

    monkeypatch.setattr(sessions, "SessionLocal", Db)
    monkeypatch.setattr(sessions, "_store_artifacts", store)
    monkeypatch.setattr(sessions, "_enrich_memory", no_memory)
    monkeypatch.setattr(sessions, "settle", settle)
    monkeypatch.setattr(sessions, "record_searches", lambda *_args, **_kwargs: None)
    return user, session, question, added, settled, artifacts


async def _turn(monkeypatch, *, request, model, events, routing=None):
    user, session, question, added, settled, artifacts = _persistence(monkeypatch)
    original = [
        {"role": "system", "content": "Existing project instructions."},
        {"role": "user", "content": request},
    ]
    untouched = deepcopy(original)
    calls = []

    async def run(model_id, messages, tools, ctx, **kwargs):
        calls.append({"model": model_id, "messages": messages, "tools": tools, **kwargs})
        for event in events:
            if event == "error":
                raise sessions.chat_service.ChatStreamError("synthetic_failure")
            if event == "stop":
                for stop in sessions._STOPPING[ctx.session_id]:
                    stop.set()
                return
            yield event

    monkeypatch.setattr(sessions.agent_service, "run_turn", run)
    chunks = [
        json.loads(chunk.removeprefix("data: ").strip())
        async for chunk in sessions._run_turn(
            user_id=user.id,
            api_key="synthetic-noncredential",
            auto_memory=False,
            session_id=session.id,
            model=model,
            messages=original,
            tools=[],
            first_user_message=request,
            user_message_id=question.id,
            is_first_turn=False,
            routing=routing,
        )
    ]
    assert original == untouched
    assert calls and "Grounded best-effort answers" in calls[0]["messages"][0]["content"]
    assert calls[0]["tools"] == []
    assert session.id not in sessions._STOPPING
    return chunks, added, settled, artifacts, question


@pytest.mark.asyncio
@pytest.mark.parametrize("question", QUESTIONS)
@pytest.mark.parametrize("cutoff", [None, "2024-06"])
async def test_every_domain_keeps_model_body_and_appends_identical_stored_streamed_notice(
    monkeypatch, question, cutoff,
):
    model = {**_external_model("synthetic/qwen"), "knowledgeCutoff": cutoff}
    events, rows, settled, artifacts, _ = await _turn(
        monkeypatch,
        request=question,
        model=model,
        events=[
            {"type": "delta", "text": BODY},
            {"type": "usage", "inputTokens": 5, "outputTokens": 8},
        ],
    )
    answer = next(row for row in rows if isinstance(row, Message) and row.role == Role.assistant)
    caveat = freshness.accuracy_caveat(question, model, model["id"])
    streamed = "".join(event["text"] for event in events if event["type"] == "delta")
    assert streamed == answer.content == BODY + "\n\n" + caveat
    assert streamed.count(caveat) == 1
    assert answer.model == model["id"] and answer.failure is None
    assert answer.routing["accuracy"] == {
        "policy": "grounded-best-effort-v1",
        "knowledgeCutoff": cutoff,
        "cutoffSource": "model_catalogue" if cutoff else "unknown",
    }
    assert answer.usage["inputTokens"] == 5 and answer.usage["outputTokens"] == 8
    assert next(event for event in events if event["type"] == "usage")["outputTokens"] == 8
    assert artifacts == [answer.content]
    assert any(record["reason"] == "chat.completion" for record in settled)
    assert not any(event["type"] == "freshness_abstention" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("actual", ["synthetic/qwen", "synthetic/fallback"])
@pytest.mark.parametrize("mode", ["auto", "auto_quality"])
async def test_auto_actual_model_identity_controls_cutoff_without_overwriting_routing(
    monkeypatch, actual, mode,
):
    model = {**_external_model("synthetic/qwen"), "knowledgeCutoff": "2024-06"}
    route = {
        "actualModel": model["id"],
        "routedModels": [model["id"]],
        "costRouting": {
            "mode": mode, "decision": "routed", "reasonCode": "synthetic_route",
            "routedModel": model["id"], "executedModel": model["id"],
        },
    }
    events, rows, _, _, _ = await _turn(
        monkeypatch, request=QUESTIONS[0], model=model, routing=route,
        events=[
            {"type": "model_route", "routedModel": model["id"], "actualModel": actual},
            {"type": "delta", "text": BODY},
            {"type": "usage", "inputTokens": 5, "outputTokens": 8},
        ],
    )
    answer = next(row for row in rows if isinstance(row, Message))
    assert answer.model == actual
    assert answer.routing["actualModel"] == actual
    assert answer.routing["costRouting"]["executedModel"] == actual
    assert answer.routing["costRouting"]["mode"] == mode
    assert answer.routing["accuracy"]["knowledgeCutoff"] == (
        "2024-06" if actual == model["id"] else None
    )
    assert answer.content.endswith(freshness.accuracy_caveat(QUESTIONS[0], model, actual))
    assert "".join(event["text"] for event in events if event["type"] == "delta") == answer.content


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["empty", "error", "stop", "partial_error", "partial_stop"])
async def test_empty_failed_or_stopped_turns_do_not_gain_a_fabricated_footer_answer(
    monkeypatch, outcome,
):
    model = {**_external_model("synthetic/qwen"), "knowledgeCutoff": "2024-06"}
    stream = [{"type": "delta", "text": BODY}] if outcome.startswith("partial") else []
    if "error" in outcome:
        stream.append("error")
    elif "stop" in outcome:
        stream.append("stop")
    events, rows, _, artifacts, question = await _turn(
        monkeypatch, request=QUESTIONS[0], model=model, events=stream,
    )
    answers = [row for row in rows if isinstance(row, Message) and row.role == Role.assistant]
    content = "".join(event["text"] for event in events if event["type"] == "delta")
    assert artifacts == []
    assert "학습 기준" not in content
    if outcome.startswith("partial"):
        assert len(answers) == 1 and answers[0].content == BODY
        assert answers[0].failure == (
            TurnFailure.stopped if outcome.endswith("stop") else TurnFailure.interrupted
        )
    else:
        assert answers == [] and content == ""
        assert question.failure == (
            TurnFailure.stopped if outcome == "stop" else TurnFailure.no_answer
        )


@pytest.mark.asyncio
async def test_an_exact_model_emitted_notice_is_not_appended_twice(monkeypatch):
    model = {**_external_model("synthetic/qwen"), "knowledgeCutoff": "2024-06"}
    caveat = freshness.accuracy_caveat(QUESTIONS[0], model, model["id"])
    content = BODY + "\n\n" + caveat
    events, rows, _, _, _ = await _turn(
        monkeypatch, request=QUESTIONS[0], model=model,
        events=[
            {"type": "delta", "text": content},
            {"type": "usage", "inputTokens": 5, "outputTokens": 8},
        ],
    )
    answer = next(row for row in rows if isinstance(row, Message))
    assert answer.content == content
    assert "".join(event["text"] for event in events if event["type"] == "delta") == content


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_comparison_columns_use_their_own_metadata_and_keep_envelopes_independent(
    monkeypatch, fallback,
):
    user, session, _, rows, _, _ = _persistence(monkeypatch)
    models = [
        {**_external_model("synthetic/one"), "knowledgeCutoff": "2024-06"},
        {**_external_model("synthetic/two"), "knowledgeCutoff": "2023-11"},
    ]
    original = [{"role": "user", "content": QUESTIONS[0]}]
    untouched = deepcopy(original)
    captured = {}

    async def complete(model_id, messages, *_args, **_kwargs):
        captured[model_id] = deepcopy(messages)
        actual = "synthetic/fallback" if fallback and model_id == "synthetic/one" else model_id
        yield {"type": "model_route", "routedModel": model_id, "actualModel": actual}
        yield {"type": "delta", "text": BODY}
        yield {"type": "usage", "inputTokens": 5, "outputTokens": 8}

    monkeypatch.setattr(sessions.chat_service, "stream_completion", complete)
    events = [
        json.loads(chunk.removeprefix("data: ").strip())
        async for chunk in sessions._run_comparison(
            user_id=user.id, api_key="synthetic-noncredential", session_id=session.id,
            models=models, messages=original, routing={},
        )
    ]
    answer = next(row for row in rows if isinstance(row, Message))
    assert original == untouched
    for model in models:
        policy = captured[model["id"]][0]["content"]
        assert model["knowledgeCutoff"] in policy
        other = models[1] if model is models[0] else models[0]
        assert other["knowledgeCutoff"] not in policy
        variant = next(item for item in answer.variants if item["model"] == model["id"])
        actual = "synthetic/fallback" if fallback and model is models[0] else model["id"]
        caveat = freshness.accuracy_caveat(QUESTIONS[0], model, actual)
        streamed = "".join(
            event["text"] for event in events
            if event["type"] == "variant" and event["model"] == model["id"]
        )
        assert variant["actualModel"] == actual
        assert streamed == variant["content"] == BODY + "\n\n" + caveat
        assert variant["usage"] == {"inputTokens": 5, "outputTokens": 8}
    assert answer.content == answer.variants[0]["content"]
