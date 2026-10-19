"""A report edit whose rewrite is the writer's plan, not the text (round 11, DOCS-01): the plan
taken off by code, a P0 it brought in sent back once, then the part kept and the edit said to
have failed."""

from __future__ import annotations

import asyncio

import pytest

from app.pipeline import revise_stages
from app.pipeline.stage import Context, Stop
from app.services import internal_words, lint, report, revise

INSTRUCTION = ("2절의 표 2와 그림 1은 빼고, 맨 앞에 3줄 요약을 넣어줘. 내부 허용 한도가 4%라는 "
               "표현은 메모에 없으니 결재 요청으로 고쳐줘.")

BEFORE = ("3분기 팀 매출은 41.2억 원으로 목표 40억 원 대비 103%를 기록했습니다. 신규 거래처 "
          "대성유통과 1차 미팅을 진행해 월 발주 예상액 3,200만 원 규모를 확인했습니다. 한솔마트 "
          "재고 클레임 2건은 반품 420만 원으로 처리를 마쳤습니다.")

#: what the writer gave back on the follow-up edit: its plan, a rule, then the text
PLAN = """1. **3줄 요약 (본문 앞부분)**: 핵심 성과(매출 달성률), 주요 진행 상황(신규 거래처 협상 및 클레임 처리), 결정 필요 사항(단가 인하 결재)을 한 문장씩 압축합니다.
2. **주요 현황 서술**: 3분기 매출 실적과 신규 거래처 미팅, 기존 거래처 클레임 처리 내역을 연결된 서술로 정리합니다.
3. **결정 필요 사항 반영**: 내부 허용 한도라는 표현 대신 메모에 명시된 대로 '단가 인하 폭 결재 요청'으로 수정하여 서술합니다.

*주의사항*: 지시된 대로 표와 그림은 배제하고, 본문은 '합니다/입니다'체로만 작성합니다. 수치만 정확히 인용하며, 원인이 없는 추측은 배제합니다.

***

"""  # noqa: E501
TEXT = ("10월 3주차에는 3분기 팀 매출이 목표 대비 103%인 41.2억 원을 기록했습니다. 신규 거래처 "
        "단가 협상과 기존 거래처 클레임 처리를 병행했습니다. 다음 주에는 대성유통과의 단가 협상 "
        "마무리가 핵심 과제로 남아 있습니다.\n\n" + BEFORE)
#: a plan sentence inside the text
PLAN_INSIDE = ("3분기 팀 매출은 41.2억 원입니다. 지시된 대로 표와 그림은 배제하고 본문만 "
               "작성합니다. 대성유통과 1차 미팅을 진행했습니다. " * 2)
#: a draft leftover code cannot settle
LEFTOVER = "3분기 팀 매출은 41.2억 원입니다. 대성유통 2차 미팅 일정은 TBD 입니다. " * 2


def test_the_writers_plan_above_the_text_is_taken_off():
    assert internal_words.strip_plan_preface(PLAN + TEXT) == TEXT
    assert internal_words.strip_work_notes(PLAN + TEXT, document=True) == TEXT
    # text that only reports the work done, no plan: as written
    assert internal_words.strip_plan_preface(TEXT) == TEXT
    # a plan with nothing after it is left for the check to send back, never emptied
    assert internal_words.strip_plan_preface(PLAN) == PLAN


def test_a_plan_left_in_the_text_is_a_p0_lint_finding():
    found = lint.check(lint.from_sections([{"heading": "핵심 성과", "content": PLAN_INSIDE}]))
    assert [f.rule for f in found if f.severity == "P0"] == ["plan"]
    assert lint.check(lint.from_sections([{"heading": "핵심 성과", "content": TEXT}])) == []


def test_only_a_p0_the_rewrite_brought_in_counts_against_it():
    assert revise.new_faults(BEFORE, PLAN_INSIDE, heading="핵심 성과", instruction=INSTRUCTION)
    assert revise.new_faults(BEFORE, TEXT, heading="핵심 성과", instruction=INSTRUCTION) == []
    # bold labels across the lines of a markdown part are its own markup, not a fault
    labels = "- **매출**: 41.2억 원을 기록했습니다.\n- **거래처**: 대성유통과 미팅했습니다.\n" * 2
    assert revise.new_faults(BEFORE, labels, heading="핵심 성과", instruction=INSTRUCTION) == []
    # a figure the person asked for is theirs
    asked = BEFORE + " 가용성 99.9%를 목표로 합니다."
    assert revise.new_faults(BEFORE, asked, heading="핵심 성과", instruction=INSTRUCTION)
    assert revise.new_faults(BEFORE, asked, heading="핵심 성과",
                             instruction="가용성 99.9% 목표를 넣어 줘") == []


def _bundle(sections):
    return revise_stages.bundle_for(
        request="이번 주 메모로 팀장님께 올릴 주간 업무 보고서를 만들어줘.",
        instruction=INSTRUCTION,
        model={"id": "m"}, planner=None, api_key="k", is_deck=False, title="주간 업무 현황",
        data={}, parts=sections, material=[], plan=None, format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})


def _sections():
    return [{"id": "s0", "heading": "이번 주 핵심 성과 및 현황", "content": BEFORE},
            {"id": "s1", "heading": "다음 주 추진 계획", "content": "다음 주에는 단가 협상을 "
             "마무리하고 한솔마트를 방문해 재고 점검을 수행합니다. CRM 병합을 마칩니다."}]


async def _run(bundle):
    events = []
    for stage in (revise_stages.Route(), revise_stages.Widen(), revise_stages.Rewrite()):
        async for event in stage.run(bundle, Context()):
            events.append(event)
    return events


@pytest.fixture
def one_part(monkeypatch):
    async def plan(**_kwargs):
        return revise.Plan(scope="parts", targets=[0], note="맨 앞에 3줄 요약, 결재 요청으로")

    monkeypatch.setattr(revise, "plan", plan)


def test_a_plan_written_into_the_rewrite_is_taken_out_by_code(monkeypatch, one_part):
    async def complete(model, messages, api_key, max_tokens, **_kwargs):
        return PLAN + PLAN_INSIDE, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    bundle = _bundle(_sections())
    asyncio.run(_run(bundle))
    body = bundle.document.sections[0]["content"]
    assert "지시된 대로" not in body and "압축합니다" not in body and "***" not in body
    assert body.startswith("3분기 팀 매출은 41.2억 원입니다. 대성유통과 1차 미팅을 진행했습니다.")


def test_a_rewrite_with_a_new_p0_is_sent_back_once_with_the_reason(monkeypatch, one_part):
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens, **_kwargs):
        asked.append(messages[-1]["content"])
        return (LEFTOVER if len(asked) == 1 else TEXT), {"inputTokens": 3, "outputTokens": 2}

    monkeypatch.setattr(report, "_complete", complete)
    bundle = _bundle(_sections())
    asyncio.run(_run(bundle))
    assert len(asked) == 2
    assert "rejected" in asked[1] and "채우지 않은 자리" in asked[1]
    assert bundle.document.sections[0]["content"].startswith("10월 3주차에는")
    assert bundle.document.changed == 1 and bundle.document.failed == []
    # both attempts are billed
    assert bundle.usage == {"inputTokens": 6, "outputTokens": 4}


def test_a_rewrite_that_fails_twice_keeps_the_part_and_says_so(monkeypatch, one_part):
    async def complete(model, messages, api_key, max_tokens, **_kwargs):
        return LEFTOVER, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    bundle = _bundle(_sections())
    events: list[dict] = []

    async def run():
        for stage in (revise_stages.Route(), revise_stages.Widen(), revise_stages.Rewrite()):
            async for event in stage.run(bundle, Context()):
                events.append(event)

    with pytest.raises(Stop):
        asyncio.run(run())
    assert bundle.document.sections[0]["content"] == BEFORE
    assert bundle.document.failed == ["이번 주 핵심 성과 및 현황"]
    errors = [e["message"] for e in events if e["type"] == "error"]
    assert errors and "이전 버전을 그대로" in errors[0]
