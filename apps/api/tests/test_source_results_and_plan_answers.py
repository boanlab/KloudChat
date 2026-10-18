"""Missing source tool results, and work-plan-only answers written once more."""

from __future__ import annotations

import json

import pytest

from app.routers import sessions
from app.services import agent, internal_words
from app.services.tools.base import Tool, ToolContext, ToolResult
from app.services.workspace_context import AppliedSkill

# ── answers that are only a work plan ────────────────────────────────────────────────

#: the whole answer, after the opening 「The user wants …」 note went
WEEKLY_REPORT_PLAN = (
    'Key points from the skeleton:\n'
    '- 보고 기간·보고 대상·결론 3줄로 시작\n'
    '- 4개 과제별 추진 내용·현황 표\n'
    '- 견적 이슈: A사·B사 금액·납기 표 + 결정 요청\n'
    '- 다음 주 일정 표\n'
    '- 수치 원문 그대로 유지\n'
    '- 완료/진행 상태 구분\n'
    '- 담당 의견은 「제안」으로 표기\n'
    '- 「납기 1주 빠름」은 비교 표현이므로 원문대로만 기재\n'
    '\n'
    'No computed figures — I must not compute differences. Per '
    'the skill rules: "write each figure as the material gives '
    'it, and compute a difference, rate or total only through a '
    'calculation tool on this turn; otherwise state the two '
    'values side by side and do not write a computed one."\n'
    '\n'
    'I could compute the difference with a tool, but the '
    'skeleton says 원문대로만 기재. Better to just state both values '
    'side by side without computing. Actually, a difference '
    'might be useful for decision-making, but the rule says '
    "don't add what the material doesn't state. The material "
    "doesn't mention a difference. So I'll present both amounts "
    'as given.\n'
    '\n'
    'Let me write this in the weekly report format per rule 5 of '
    'the 개조식 skill:\n'
    '- 보고 기간 and 보고 대상 in head lines\n'
    '- 실적 as one table (사업 | 추진 내용 | 상태)\n'
    '- 결정 요청 with option table + one line on what each choice '
    'favours\n'
    '- 차주 계획 as table\n'
    '\n'
    'Let me structure this properly.\n'
    '\n'
    'Format: 개조식, end in noun form (…함, …임, …예정, …필요), items at '
    'most 2 lines, use □ ○ - ㆍ marks.\n'
    '\n'
    'Let me draft:\n'
    '\n'
    '**핵심**\n'
    '- LMS 개편·워크숍·사례집 3건 정상 진행, 디자인 업체 견적(A사/B사) 결정 필요\n'
    '- 결정 요청: 디자인 업체 선정 (비용 vs 납기 우선 판단)\n'
    '\n'
    'Then sections. Let me write it out.\n'
    '\n'
    "For the table format in chat, I'll use markdown tables as "
    'the skill says 실적 as one table.\n'
    '\n'
    'Let me be careful about:\n'
    '- 「납기 1주 빠름」 is a comparative expression, not an absolute '
    'period → write as-is in the table\n'
    "- Don't compute 420-380=40만원 difference\n"
    '- 담당 의견 marked as 「제안」\n'
    '- The material says 10/15 LMS 시안 검토 회의 and also 10/15 시안 도착 '
    '예정 — these are from the same source so both stay\n'
    '- 11월 예산 집행계획 10/23 팀장 제출\n'
    '\n'
    'Now let me also think about whether I need to run any '
    'tools. The skill says to run grounding-check after writing. '
    'Let me do that. But first let me compose the answer.\n'
    '\n'
    'Actually, the grounding-check skill says: "A chat answer on '
    "the person's own material (attached or earlier files, "
    'pasted material) gets the same pass with no agent '
    'attached." So I should run grounding-check on my answer.\n'
    '\n'
    'Let me compose:\n'
    '\n'
    '보고 기간: 2026. 10. 5. ~ 10. 9.\n'
    '보고 대상: 팀장님\n'
    '\n'
    '실적 table:\n'
    '| 사업 | 추진 내용 | 상태 |\n'
    '| LMS 개편 | 업체 1차 시안 일정 확인(10/15 도착 예정) / 모바일 검수 체크리스트 초안 작성 '
    '| 시안 일정 확인 / 초안 70% |\n'
    '| AI 교수법 워크숍(10/22) | 신청 42명 마감, 대기 2명 온라인 송출 수용 결정, 강사 계약서 '
    '서명 | 신청 마감·수용 결정·계약 완료 |\n'
    '| 우수 강의 사례집 | 원고 12편 중 9편 접수, 미제출 3편 재요청 메일 발송(10/16 기한) | '
    '9편 접수 / 재요청 발송 |\n'
    '| 디자인 업체 선정 | 견적 A사 380만원 / B사 420만원(납기 1주 빠름) | 결정 필요 |\n'
    '\n'
    '결정 요청 table:\n'
    '| 안 | 견적 | 비고 |\n'
    '| A사 | 380만원 | — |\n'
    '| B사 | 420만원 | 납기 1주 빠름 |\n'
    '\n'
    '담당 의견(제안): 비용 vs 납기 우선 판단 필요\n'
    '\n'
    '다음 주:\n'
    '| 일정 | 내용 | 비고 |\n'
    '| 10/15 | LMS 시안 검토 회의 | 시안 도착 예정일 |\n'
    '| 10/23 | 11월 예산 집행계획 팀장 제출 | 작성 필요 |\n'
    '\n'
    'Now let me write the full 개조식 report and then run '
    'grounding-check.\n'
    '\n'
    'Let me write the final answer first mentally then run the '
    'check.'
)

WEEKLY_REPORT_REQUEST = (
    "아래 메모로 이번 주 주간 업무 보고서 써 줘. 팀장님께 올릴 거야.\n\n"
    "- 기간: 10/5~10/9\n"
    "- 이슈: 디자인 업체 견적 A사 380만원 / B사 420만원(납기 1주 빠름) — 결정 필요"
)

WEEKLY_REPORT = (
    "# 주간 업무 보고(10/5~10/9)\n\n"
    "□ 핵심\n ○ LMS 개편·워크숍·사례집 정상 진행, 디자인 업체 선정 결정 필요\n\n"
    "| 안 | 견적 | 비고 |\n|---|---|---|\n| A사 | 380만원 | — |\n"
    "| B사 | 420만원 | 납기 1주 빠름 |"
)

#: written with no transcript, claiming one
VIDEO_NOTE_INVENTED = (
    "전사 추출로 영상의 실제 내용을 확인했습니다.\n\n## 3줄 요약\n\n"
    "- 이 영상은 금리가 무엇인지, 명목금리와 실질금리를 어떻게 구분하는지, 그리고 기준금리가 "
    "시중 금리에 어떻게 전파되는지를 다루는 경제 기초 강의입니다.\n\n"
    "## 영상 예시·수치 (원문 인용)\n\n"
    "- 「명목금리에서 물가 상승률을 빼면 실질금리가 됩니다」"
)

VIDEO_NOTE_REQUEST = (
    "이 영상 보고 금리 개념 학습 노트 정리해 줘. 신입 직원 경제 기초 스터디에서 같이 볼 거야.\n"
    "https://www.youtube.com/watch?v=EAs8qDciTak\n"
    "3줄 요약, 영상에서 설명한 핵심 개념, 스터디 토론 질문 2개로."
)

TRANSCRIPT = "금리는 돈을 빌린 대가입니다. 300만 원을 3%로 맡기면 1년에 9만 원을 받습니다."
SOURCE = "mcp__youtube__transcript"


# ── the plan detector ────────────────────────────────────────────────────────


def test_the_weekly_report_plan_is_a_plan() -> None:
    assert internal_words.is_work_plan(WEEKLY_REPORT_PLAN, request=WEEKLY_REPORT_REQUEST)
    opened = ("The user wants a weekly report in bullet-point format for their team leader. "
              "Let me organize the memo content following the skills.\n\n" + WEEKLY_REPORT_PLAN)
    assert internal_words.is_work_plan(opened, request=WEEKLY_REPORT_REQUEST)


def test_deliverables_are_not_plans() -> None:
    assert not internal_words.is_work_plan(WEEKLY_REPORT, request=WEEKLY_REPORT_REQUEST)
    # notes before the briefing: stripped, not regenerated
    memo = (
        "The user is asking me to write the customer research briefing using only what I've "
        "already found. Let me carefully review what I actually confirmed.\n\n"
        "Write it.\n\n"
        "한국수자원공사(K-water)는 1967년 설립된 환경부 소관 공기업으로 물관리 전반을 담당하는 "
        "기관이고, 정보화 관련 신호는 「경영부문 정보관리처」라는 전담 조직입니다."
    )
    assert not internal_words.is_work_plan(memo, request="고객 조사 브리핑 써 줘")
    # an English letter asked for in Korean keeps its 「I'll …」 and 「Let me know …」
    letter = (
        "I'll be in Seoul next week and would be glad to show you how the material flows.\n\n"
        "I can bring the draft schedule and the pricing structure for your team to review.\n\n"
        "I will also share the rollout format we used with two other universities.\n\n"
        "Let me know which day suits you best."
    )
    assert not internal_words.is_work_plan(letter, request="영문 영업 메일 써 줘")
    # an English conversation
    assert not internal_words.is_work_plan(WEEKLY_REPORT_PLAN, request="Write the weekly report.")


# ── run_turn with a scripted model ───────────────────────────────────────────


class _Response:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _says(text: str) -> list[str]:
    return [f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}", "data: [DONE]"]


class _Client:
    """Each model call answers with the next scripted text."""

    def __init__(self, seen: list[dict], replies: list[str]) -> None:
        self._seen, self._replies = seen, replies

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
        self._seen.append(json)
        return _Response(_says(self._replies[min(len(self._seen), len(self._replies)) - 1]))


async def _turn(monkeypatch, replies: list[str], *, request: str, tools=(), **kwargs):
    seen: list[dict] = []

    async def client(*_args, **_kwargs):
        return _Client(seen, replies)

    monkeypatch.setattr(agent, "_client", client)
    events = [
        event
        async for event in agent.run_turn(
            "local/qwen3.8-27b",
            [{"role": "system", "content": "시스템"}, {"role": "user", "content": request}],
            list(tools),
            ToolContext(user_id="user", session_id="session", api_key="key", request=request),
            **kwargs,
        )
    ]
    return events, seen


def _shown(events: list[dict]) -> str:
    """What the person ends up with: deltas, each retract taking its text back once."""
    text = ""
    for event in events:
        if event["type"] == "delta":
            text += event["text"]
        elif event["type"] == "retract":
            text = text.replace(event["text"], "", 1)
    return text


@pytest.mark.asyncio
async def test_a_plan_only_answer_is_written_once_more_as_the_report(monkeypatch) -> None:
    events, seen = await _turn(monkeypatch, [WEEKLY_REPORT_PLAN, WEEKLY_REPORT],
                               request=WEEKLY_REPORT_REQUEST)

    assert _shown(events) == WEEKLY_REPORT
    assert len(seen) == 2
    again = seen[-1]
    assert "tools" not in again
    assert "plan of the work" in again["messages"][-1]["content"]
    assert WEEKLY_REPORT_PLAN not in json.dumps(again, ensure_ascii=False)
    assert len([e for e in events if e["type"] == "usage"]) == 1


@pytest.mark.asyncio
async def test_a_plan_twice_is_never_shipped(monkeypatch) -> None:
    events, seen = await _turn(monkeypatch, [WEEKLY_REPORT_PLAN, WEEKLY_REPORT_PLAN],
                               request=WEEKLY_REPORT_REQUEST)

    assert _shown(events).strip() == ""
    assert len(seen) == 2  # once more, not more


@pytest.mark.asyncio
async def test_a_report_is_not_written_twice(monkeypatch) -> None:
    events, seen = await _turn(monkeypatch, [WEEKLY_REPORT], request=WEEKLY_REPORT_REQUEST)

    assert _shown(events) == WEEKLY_REPORT
    assert len(seen) == 1


def _transcript_tool(result: str | Exception, calls: list[dict]) -> Tool:
    async def run(arguments: dict) -> ToolResult:
        calls.append(arguments)
        if isinstance(result, Exception):
            raise result
        return ToolResult(content=result, detail=f"{len(result):,}자")

    return Tool(
        name=SOURCE, description="YouTube 전사",
        parameters={"type": "object", "required": ["url"],
                    "properties": {"url": {"type": "string"}, "language": {"type": "string"}}},
        run=run, label="YouTube 전사 · transcript", source="mcp")


@pytest.mark.asyncio
async def test_no_transcript_called_means_no_summary(monkeypatch) -> None:
    """A note claiming a transcript tool that never ran, and quoting the video."""
    calls: list[dict] = []
    events, _ = await _turn(
        monkeypatch, [VIDEO_NOTE_INVENTED], request=VIDEO_NOTE_REQUEST,
        tools=[_transcript_tool(TRANSCRIPT, calls)], source_tools=frozenset({SOURCE}))

    shown = _shown(events)
    assert not calls
    assert "받아 오지 못했습니다" in shown and "YouTube 전사" in shown
    assert "전사 추출로" not in shown and "명목금리" not in shown


@pytest.mark.asyncio
async def test_a_failed_server_fetch_ends_the_turn_without_writing(monkeypatch) -> None:
    calls: list[dict] = []
    tool = _transcript_tool("Error: transcription failed: Whisper transcription failed or "
                            "returned empty.", calls)
    events, seen = await _turn(
        monkeypatch, [VIDEO_NOTE_INVENTED], request=VIDEO_NOTE_REQUEST, tools=[tool],
        source_tools=frozenset({SOURCE}),
        preset_call=(SOURCE, {"url": "https://www.youtube.com/watch?v=EAs8qDciTak"}))

    shown = _shown(events)
    assert calls == [{"url": "https://www.youtube.com/watch?v=EAs8qDciTak"}]
    assert not seen  # the model never wrote from nothing
    assert "받아 오지 못했습니다" in shown and "도구가 오류를 돌려줘" in shown
    assert "Whisper" not in shown


@pytest.mark.asyncio
async def test_a_fetched_transcript_is_what_the_note_is_written_from(monkeypatch) -> None:
    calls: list[dict] = []
    note = "## 3줄 요약\n\n- 금리는 돈을 빌린 대가로, 300만 원을 3%로 맡기면 9만 원입니다."
    events, seen = await _turn(
        monkeypatch, [note], request=VIDEO_NOTE_REQUEST,
        tools=[_transcript_tool(TRANSCRIPT, calls)],
        source_tools=frozenset({SOURCE}),
        preset_call=(SOURCE, {"url": "https://www.youtube.com/watch?v=EAs8qDciTak"}))

    assert _shown(events) == note
    assert len(calls) == 1 and len(seen) == 1
    assert any(m.get("role") == "tool" and TRANSCRIPT in m["content"]
               for m in seen[0]["messages"])


# ── which turns fetch first ──────────────────────────────────────────────────


def _skill(*tools: str) -> AppliedSkill:
    return AppliedSkill("s", "YouTube 영상 전사·요약", None, 405, required_tools=tools)


def test_a_declared_connector_given_its_link_is_fetched_first() -> None:
    tool = _transcript_tool(TRANSCRIPT, [])
    assert sessions._source_call([_skill(SOURCE)], [tool], VIDEO_NOTE_REQUEST) == (
        SOURCE, {"url": "https://www.youtube.com/watch?v=EAs8qDciTak"})
    # no link in the message (a follow-up on the note): nothing fetched
    assert sessions._source_call([_skill(SOURCE)], [tool], "토론 질문 하나 더 만들어 줘") is None
    # a skill that does not declare it, or a connector that is not connected
    assert sessions._source_call([_skill()], [tool], VIDEO_NOTE_REQUEST) is None
    assert sessions._source_call([_skill(SOURCE)], [], VIDEO_NOTE_REQUEST) is None


def test_built_in_tools_and_tools_needing_more_than_a_link_are_left_to_the_model() -> None:
    async def run(_arguments: dict) -> str:
        return ""

    fetch = Tool(name="fetch_url", description="", run=run, label="열기",
                 parameters={"type": "object", "required": ["url"]})
    assert sessions._source_call([_skill("fetch_url")], [fetch], VIDEO_NOTE_REQUEST) is None
    two = Tool(name="mcp__x__clip", description="", run=run, label="자르기", source="mcp",
               parameters={"type": "object", "required": ["url", "start"]})
    assert sessions._source_call([_skill("mcp__x__clip")], [two], VIDEO_NOTE_REQUEST) is None


# ── an answer that only says what it is about to do ──

CLIENT_BRIEF_REQUEST = (
    "다음 주에 한국수자원공사(K-water) 정보화 담당 부서와 첫 미팅이 있어. 미팅 전에 읽을 "
    "고객사 조사 자료 만들어 줘.\n- 기관 개요(규모, 주요 사업, 최근 이슈) — 공개 자료로, "
    "출처 달기\n- 우리가 미팅에서 물어볼 질문 5개, 예상 이슈")
CLIENT_BRIEF_INTENT = ("K-water 개요·조직·재무, 최근 정보화·DX 사업, 최근 12개월 이슈를 각각 공개 "
                       "자료로 확인하겠습니다.\n\n\n\n")
CLIENT_BRIEF = (
    "## 기관 개요\n한국수자원공사(K-water)는 1967년 설립된 물관리 공기업입니다.\n\n"
    "## 미팅 질문\n1. 현재 쓰는 협업 도구는 무엇입니까?")


def test_an_intent_line_is_no_deliverable() -> None:
    assert internal_words.is_intent_only(CLIENT_BRIEF_INTENT, request=CLIENT_BRIEF_REQUEST)
    assert internal_words.is_work_plan(CLIENT_BRIEF_INTENT, request=CLIENT_BRIEF_REQUEST)
    assert internal_words.is_intent_only(
        "관련 자료를 찾아보겠습니다. 잠시만 기다려 주세요.", request=CLIENT_BRIEF_REQUEST)
    # a deliverable, or a short answer that is not a promise of work
    assert not internal_words.is_intent_only(CLIENT_BRIEF, request=CLIENT_BRIEF_REQUEST)
    assert not internal_words.is_intent_only(
        "네, 다음부터는 존댓말로 답하겠습니다.", request="존댓말로 해 줘")
    assert not internal_words.is_intent_only(
        "K-water는 1967년에 설립됐습니다. 최근 이슈는 공개 자료로 더 확인하겠습니다.",
        request=CLIENT_BRIEF_REQUEST)
    assert not internal_words.is_intent_only("I'll check the sources.", request="Check it")


@pytest.mark.asyncio
async def test_an_intent_line_is_written_once_more_as_the_brief(monkeypatch) -> None:
    events, seen = await _turn(monkeypatch, [CLIENT_BRIEF_INTENT, CLIENT_BRIEF],
                               request=CLIENT_BRIEF_REQUEST)

    assert _shown(events) == CLIENT_BRIEF
    assert len(seen) == 2
    assert "tools" not in seen[-1]


@pytest.mark.asyncio
async def test_an_intent_line_twice_is_never_shipped(monkeypatch) -> None:
    events, seen = await _turn(monkeypatch, [CLIENT_BRIEF_INTENT, CLIENT_BRIEF_INTENT],
                               request=CLIENT_BRIEF_REQUEST)

    assert _shown(events).strip() == ""
    assert len(seen) == 2
