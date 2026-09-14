"""Copying supplied values is not a request to run their arithmetic."""

import json

import pytest
from test_privacy import _external_model, _NoWriteDb, _patch_guard_dependencies, _request

from app.models.chat import ChatSession, Message, Role
from app.models.user import User
from app.routers import sessions
from app.schemas.chat import SendMessage
from app.services import agent
from app.services.calculation_policy import requires_calculation
from app.services.tools.arithmetic import CALCULATE

DATE_EXTRACTION = (
    "문장에 있는 날짜 표현을 추출해줘. 날짜 뒤 조사인 '까지'와 '에'는 제거하고, "
    "상대 날짜를 실제 날짜로 계산하지는 마. "
    "'초안은 내일까지, 최종본은 10월 5일까지, 발표는 다음 주 월요일에 진행한다.' "
    "JSON 문자열 배열만 출력해줘."
)
EXTRACTED_DATES = '["내일", "10월 5일", "다음 주 월요일"]'


@pytest.mark.parametrize(
    "prompt",
    [
        DATE_EXTRACTION,
        "12 + 3을 계산하지는 마. 그대로 적어줘.",
        "12와 3은 계산하지도 마. 숫자만 보여줘.",
        "12와 3은 계산을 하지는 마. 문자로 보여줘.",
        "12 + 3을 검산하지는 마. 그대로 적어줘.",
        "12와 3은 계산은 하지 마. 원문을 유지해줘.",
        "12와 3은 검산도 하지 마. 원문을 유지해줘.",
    ],
)
def test_negated_calculation_words_are_not_positive_intent(prompt):
    assert not requires_calculation(prompt)


@pytest.mark.parametrize("target", ["JSON", "JSON 문자열 배열", "CSV", "YAML", "표", "텍스트"])
def test_output_representation_conversion_does_not_require_unit_arithmetic(target):
    assert not requires_calculation(f"3시간과 40분이라는 값을 그대로 {target}로 변환해줘.")


@pytest.mark.parametrize(
    "prompt",
    [
        "12 + 3은 계산하지는 마. 그리고 17 * 23은 계산해줘.",
        "12와 3은 검산하지도 마. 또한 7과 11의 합계는 구해줘.",
        "12와 18을 계산해서 JSON으로 변환해줘.",
        "12와 18의 합계를 계산해서 CSV로 바꿔줘.",
        "3시간을 분으로 환산해서 JSON으로 변환해줘.",
        "0.125를 백분율로 변환해서 표로 바꿔줘.",
        "12와 18을 JSON으로 변환하고, 평균도 계산해줘.",
        "3시간을 분으로 변환해줘.",
        "12.5%를 소수로 바꿔줘.",
        "12와 18의 평균을 계산해줘. JSON으로 출력해줘.",
    ],
)
def test_separate_or_explicit_arithmetic_remains_required(prompt):
    assert requires_calculation(prompt)


@pytest.mark.asyncio
async def test_date_extraction_survives_real_turn_and_storage_without_a_tool(monkeypatch):
    user = User(
        email="learner@example.test", password_hash="synthetic", monthly_credits=1000,
    )
    model = {**_external_model("synthetic/learner"), "supportsTools": True, "creditCost": 0}
    session = ChatSession(user_id=user.id, model=model["id"])
    await _patch_guard_dependencies(monkeypatch, session=session, models=[model], blocks=[])
    model_calls = []

    class Db(_NoWriteDb):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def is_modified(self, _row):
            return False

        async def get(self, kind, key):
            if kind is ChatSession and key == session.id:
                return session
            if kind is User and key == user.id:
                return user
            return next(
                (row for row in self.added if isinstance(row, kind) and row.id == key), None,
            )

    async def tools(*_args, **_kwargs):
        return [CALCULATE]

    async def key(*_args, **_kwargs):
        return "synthetic-unused-key"

    async def credentials(*_args, **_kwargs):
        return "http://unused.invalid", "synthetic-unused-key"

    async def stream(_model, messages, *_args, **_kwargs):
        model_calls.append(messages)
        acc = agent._Accumulator()
        acc.content = [EXTRACTED_DATES]
        acc.actual_model = model["id"]
        acc.finish_reason = "stop"
        acc.usage = {"inputTokens": 10, "outputTokens": 8}
        yield "delta", EXTRACTED_DATES
        yield "done", acc

    async def title(*_args, **_kwargs):
        return None, {"inputTokens": 0, "outputTokens": 0}

    async def enrichment(writer, **_kwargs):
        return writer

    async def no_enrichment(**_kwargs):
        return None

    db = Db()
    monkeypatch.setattr(sessions, "SessionLocal", lambda: db)
    monkeypatch.setattr(sessions, "build_tools", tools)
    monkeypatch.setattr(sessions, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", key)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(agent, "_stream_once", stream)
    monkeypatch.setattr(sessions, "_enrichment_model", enrichment)
    monkeypatch.setattr(sessions.chat_service, "generate_title", title)
    monkeypatch.setattr(sessions, "_store_artifacts", no_enrichment)
    monkeypatch.setattr(sessions, "_enrich_memory", no_enrichment)
    response = await sessions.send_message(
        session.id, SendMessage(content=DATE_EXTRACTION, web_search=False), _request(), user, db,
    )
    events = [json.loads(chunk.removeprefix("data: ")) async for chunk in response.body_iterator]
    visible = "".join(event["text"] for event in events if event["type"] == "delta")
    assert visible == EXTRACTED_DATES
    assert len(model_calls) == 1
    assert events[-1]["type"] == "done"
    assert not any(event.get("id") == "preflight" for event in events)
    stored = [row for row in db.added if isinstance(row, Message) and row.role == Role.assistant]
    assert len(stored) == 1 and stored[0].content == EXTRACTED_DATES
    assert stored[0].model == model["id"]
    assert stored[0].usage["inputTokens"] == 10
    assert stored[0].usage["outputTokens"] == 8
