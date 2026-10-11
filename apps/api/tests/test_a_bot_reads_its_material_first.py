"""An agent's material is searched by the server before the model answers (ops OPS-01): a bot
left to choose whether to call `search_knowledge` answered from memory — 연체료 하루 100원,
일요일 09~18시 — where its FAQ said there was none and it was closed. With the passages in
hand the 「공개 전 점검」 hold keeps answers the material gives, and a search that finds
nothing answers that the material does not cover the question."""

from __future__ import annotations

import copy

import pytest

from app.services import agent, check_skills, knowledge, publishing, skill_scripts
from app.services.tools.base import ToolContext
from app.services.tools.builtin import knowledge_tool

_FAQ = """# 한빛대학교 도서관 이용 안내 (가상 자료)

## 운영 시간
- 학기 중: 평일 09:00~22:00, 토요일 10:00~17:00, 일요일·공휴일 휴관
- 방학 중: 평일 09:00~18:00, 주말 휴관

## 대출
- 학부생: 10권, 14일. 대학원생: 20권, 30일. 교직원: 30권, 60일.
- 연장은 1회, 7일. 연체 중에는 연장할 수 없습니다.
- 연체료는 없고, 연체한 날수만큼 대출이 정지됩니다.

## 열람실 좌석
- 좌석 예약은 키오스크 또는 모바일 앱에서 합니다. 최대 4시간, 1회 연장(2시간).
- 30분 이상 자리를 비우면 자동 반납됩니다.

## 문의
- 대출·반납: 학술정보팀 (내선 1234)
- 열람실: 시설관리팀 (내선 5678)
"""

_CONTACT = "학술정보팀 (내선 1234)"

#: The lane's questions, and an answer that says what the FAQ says.
_CASES = [
    ("연체료 있나요?", "연체료는 없습니다. 대신 연체한 날수만큼 대출이 정지됩니다 [1]."),
    ("일요일에도 열어요?", "아니요, 일요일과 공휴일에는 휴관합니다 [1]."),
    ("대학원생은 몇 권 빌릴 수 있어요?", "대학원생은 20권까지 30일 동안 빌릴 수 있습니다 [1]."),
    ("평일 몇 시까지 해요?", "학기 중에는 평일 오후 10시(22:00)까지 운영합니다 [1]. "
                         "방학 중에는 평일 18:00까지입니다 [1]."),
]


def _model(monkeypatch, answers, seen):
    async def completion(_model, messages, tools, *_args, **kwargs):
        seen.append((copy.deepcopy(messages), [tool.name for tool in tools]))
        acc = agent._Accumulator()
        acc.content = [answers[len(seen) - 1]]
        acc.usage = {"inputTokens": 3, "outputTokens": 5}
        yield "delta", acc.content[0]
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", completion)


async def _turn(question, documents, **kwargs):
    ctx = ToolContext(user_id="owner", session_id="bot")
    events = [
        event
        async for event in agent.run_turn(
            "strict-local/m",
            [{"role": "system", "content": publishing.MATERIAL_ONLY_RULE},
             {"role": "user", "content": question}],
            [knowledge_tool(documents)],
            ctx,
            strict_local=True,
            preset_call=("search_knowledge", {"query": question}),
            **kwargs,
        )
    ]
    return events, ctx


@pytest.mark.asyncio
@pytest.mark.parametrize(("question", "answer"), _CASES)
async def test_the_model_answers_with_the_material_already_in_hand(monkeypatch, question, answer):
    seen = []
    _model(monkeypatch, [answer], seen)
    events, ctx = await _turn(question, [("faq.md", _FAQ, None)],
                              preset_empty_answer=publishing.not_covered_answer(_CONTACT))
    (first,) = seen
    tool_turns = [m for m in first[0] if m["role"] == "tool"]
    # Searched before the model was asked: the whole small FAQ, numbered for citing.
    assert tool_turns and tool_turns[0]["content"].startswith("[1] 자료: faq.md")
    assert "연체료는 없고" in tool_turns[0]["content"]
    steps = [e for e in events if e["type"] == "step" and e["status"] == "done"]
    assert steps and steps[0]["label"] == "자료 찾기"
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "### 출처" in text and "faq.md" in text
    # The 「공개 전 점검」 hold reads the same passages and keeps the answer.
    hold = skill_scripts.entry("answer-hold")
    options = check_skills.options_for(None, {"contact": _CONTACT}, surface="chat")
    assert hold.check(answer, "", list(ctx.outputs), options) == []


@pytest.mark.asyncio
async def test_a_search_that_finds_nothing_answers_that_the_material_does_not_cover_it(
        monkeypatch):
    seen = []
    _model(monkeypatch, ["주차 요금은 한 시간에 1,000원입니다."], seen)
    # Past the whole-shelf size, so the search ranks passages and can come back empty.
    big = "\n\n".join(f"## 제{i}조(대출)\n학부생 대출 권수와 기간을 정한다. " + "대출 " * 40
                      for i in range(1, 80))
    assert len(big) > knowledge._WHOLE_SHELF
    events, _ = await _turn("헬스장 회원권 환불 규정", [("규정.md", big, None)],
                            preset_empty_answer=publishing.not_covered_answer(_CONTACT))
    assert seen == []
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert text.startswith(publishing.NOT_COVERED) and _CONTACT in text
    assert publishing.unanswered(text)


def test_the_bot_and_the_hold_say_not_found_in_the_same_words():
    hold = skill_scripts.entry("answer-hold")
    assert hold.NOT_FOUND == publishing.NOT_COVERED
    assert hold.message(_CONTACT) == publishing.not_covered_answer(_CONTACT)
