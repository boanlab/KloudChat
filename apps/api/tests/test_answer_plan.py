"""A substantial chat request is planned before it is answered."""

import pytest

from app.services import answer_plan


def test_which_requests_get_a_plan():
    assert answer_plan.wanted("중학교 2학년 과학 3차시 수업 흐름을 짜 줘. 목표와 활동을 표로.")
    assert answer_plan.wanted("전세 계약이 끝났는데 집주인이 보증금을 안 돌려줘. 어떻게 해야 해?")
    assert not answer_plan.wanted("고마워, 덕분에 잘 됐어. 다음에 또 물어볼게!")
    assert not answer_plan.wanted("오늘 날씨 어때?")


@pytest.mark.asyncio
async def test_the_plan_sits_beside_the_question_and_a_failure_changes_nothing():
    question = {"role": "user", "content": "보증금 어떻게 받아?"}
    messages = [{"role": "system", "content": "s"}, question]

    async def complete(model, msgs, key, max_tokens):
        assert msgs[-1]["content"].startswith("(이 메시지는 답을 쓰기 전의 준비다")
        return "- 내용증명\n- 임차권등기명령 후 이사\n- 분쟁조정위원회\n- 지급명령", {}

    outline = await answer_plan.plan(messages, complete=complete, model="m", api_key="k")
    out = answer_plan.attach(messages, outline)
    assert out[-1]["content"].startswith("보증금 어떻게 받아?")
    assert "- 임차권등기명령 후 이사" in out[-1]["content"]
    assert messages[-1]["content"] == "보증금 어떻게 받아?"

    async def broken(*a, **k):
        raise RuntimeError("down")

    assert await answer_plan.plan(messages, complete=broken, model="m", api_key="k") == ""
    assert answer_plan.attach(messages, "") == messages
