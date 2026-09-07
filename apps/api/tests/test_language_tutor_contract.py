"""Prompt assembly contracts only; these tests do not measure model response quality."""

from __future__ import annotations

import pytest

from app.models.chat import SessionKind
from app.models.workspace import Agent
from app.services import starter
from app.services.context import build_messages
from app.services.workspace_context import _agent_block


def _messages(key: str, request: str) -> list[dict[str, str]]:
    spec = next(row for row in starter._AGENTS if row["key"] == key)
    agent = Agent(
        owner_id="learner",
        name=spec["name"],
        slug=key,
        system_prompt=spec["system_prompt"],
    )
    history = [
        {"role": "user", "content": "영어 연습을 하자."},
        {"role": "assistant", "content": "What do you like to do on weekends?"},
        {"role": "user", "content": request},
    ]
    messages = build_messages(SessionKind.chat, history, extra=[_agent_block(agent)])
    assert messages[1:] == history
    assert messages[0]["content"].count(agent.system_prompt) == 1
    return messages


@pytest.mark.parametrize(
    "learner_request",
    [
        "I read a book about cooking. 이 말에 맞게 영어로 질문 하나만 이어줘.",
        "I goes to library yesterday. 이 문장만 교정하고 질문은 하지 마.",
        "I goes to library yesterday. 문법을 고치고 간단한 질문 하나 해 줘.",
    ],
    ids=["question-only", "correction-only", "conversation"],
)
def test_english_tutor_assembled_prompt_scopes_the_default_routine(learner_request: str):
    prompt = _messages("english-tutor", learner_request)[0]["content"]

    assert "The learner's latest request sets this turn's practice mode" in prompt
    assert "exactly one English question, once" in prompt
    assert "without a greeting, correction, or explanation" in prompt
    assert "only a correction or rewrite, give only that" in prompt
    assert "do not append a question" in prompt
    assert "Do not repeat a question elsewhere in the reply" in prompt
    assert "omit the correction block silently when nothing needs fixing" in prompt
    assert "Preserve the learner's facts" in prompt
    assert "Ask one follow-up question each turn" not in prompt
    assert "Skip the 교정 block when nothing needs fixing and say so" not in prompt


@pytest.mark.parametrize(
    "learner_request",
    [
        "도서관에 관해 질문 하나만 영어로 해줘. 음성을 보내지 않았으니 발음은 평가하지 마.",
        "I usually visit the library on weekends. It is quiet and I can read books. "
        "이 텍스트만 자연스럽게 고쳐줘. 실제 시험 점수와 발음은 단정하지 마.",
        "IH 목표야. 이 답변에서 연습할 표현과 문장 연결을 짚어 줘.",
    ],
    ids=["question-only", "correction-only", "targeted-feedback"],
)
def test_opic_assembled_prompt_limits_assessment_to_available_evidence(learner_request: str):
    prompt = _messages("opic-master", learner_request)[0]["content"]

    assert "이번 턴의 연습 방식은 학습자의 최신 요청을 따른다" in prompt
    assert "질문만 요청하면 영어 질문 하나만 한 번" in prompt
    assert "교정만 요청하면 고친 텍스트만" in prompt
    assert "평가나 다음 질문을 덧붙이지 않는다" in prompt
    assert "목표 등급에 맞춘 연습 방향이나 기준 설명을 요청하면" in prompt
    assert "짧은 텍스트만으로 실제 시험 점수나 등급, 특정 등급에 적합한지 판단하지 않는다" in prompt
    assert "음성 자료가 없으면 발음, 억양, 말하기 속도나 유창성을 평가하지 않는다" in prompt
    assert "학습자가 말하지 않은 장소, 사람, 경험이나 세부 사실을 만들어 넣지 않는다" in prompt
    assert "이 답변은 IH 기준에서 세부가 부족하다" not in prompt
