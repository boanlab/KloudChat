"""Work notes kept out of answers, and empty answers written from gathered material."""

from __future__ import annotations

import pytest

from app.pipeline import chat_stages
from app.pipeline.stage import Context
from app.services import agent, internal_words, report
from app.services.tools.base import Tool, ToolContext, ToolResult

# ── work notes ────────────────────────────


def test_a_check_verdict_above_the_report_is_dropped() -> None:
    """The grounding check's verdict opened the answer, a rule under it."""
    answer = (
        "근거 대조 결과 근거 없는 값 0개.\n\n---\n\n# 주간 업무 보고서\n\n"
        "**보고 기간:** 10/5 ~ 10/9\n\n## 1. 결정 요청 사항\n\n"
        "디자인 업체 선정을 팀장님께 요청드립니다."
    )
    out = internal_words.strip_work_notes(
        answer, request="아래 메모로 이번 주 주간 업무 보고서 써 줘. 팀장님께 올릴 거야.\n"
        "- 다음 주: LMS 시안 검토 회의(10/15)")
    assert out.startswith("# 주간 업무 보고서")
    assert "근거 대조" not in out
    assert "디자인 업체 선정을 팀장님께 요청드립니다." in out


def test_a_verdict_the_person_asked_for_stays() -> None:
    answer = "근거 대조 결과 근거 없는 값 2개입니다. 「11월 초」와 「3억 원」이 자료에 없습니다."
    assert internal_words.strip_work_notes(answer, request="이 보고서 근거 대조해 줘") == answer


def test_section_narration_question_stubs_and_lone_check_go() -> None:
    """Narration about sections, a 「[질문]」 stub, and a section that is just 「check」."""
    overview = (
        "결과 요약 섹션의 초안 확인을 위해 누락된 정보를 먼저 점검합니다.\n\n"
        "개요 섹션은 행사 및 사업의 기본 현황을 표로 정리하는 영역입니다. "
        "결과 요약이 아직 비어 있어 "
        "개요 섹션의 범위 내인 기본 정보(일시, 장소, 주관, 참여 규모, 예산 규모)만 "
        "표로 작성합니다. "
        "추진 경과, 세부 실적, 성과 및 문제점, 향후 계획은 각 해당 섹션의 영역이므로 여기서는 "
        "다루지 않습니다(표 1).\n\n**표 1. 2026 한빛 AI 해커톤 기본 현황**\n\n"
        "| 구분 | 내용 |\n| --- | --- |\n| 주관 | SW중심대학사업단 |"
    )
    request = "해커톤 결과보고서 써 줘"
    out = internal_words.strip_work_notes(overview, request=request)
    assert out.startswith("**표 1. 2026 한빛 AI 해커톤 기본 현황**")
    assert "| 주관 | SW중심대학사업단 |" in out
    stub = (
        "[질문] 운영 인력 규모(교직원·서포터즈·멘토 포함)가 미기재되어 「인력 규모」 항목은 "
        "[운영 인력 25명(교직원 9명·학생 서포터즈 16명)]로 남겼습니다. 인원과 구성을 알 수 있으면 "
        "알려 주십시오."
    )
    assert internal_words.strip_work_notes(stub, request=request) == ""
    assert internal_words.strip_work_notes("check", request=request) == ""


def test_an_english_note_in_place_of_a_section_leaves_it_empty() -> None:
    """A 「주요 사례」 section that is one English line about the work."""
    body = ("I'll analyze the available sources to write this section accurately, then verify "
            "against the material.")
    out = internal_words.strip_work_notes(
        body, request="국내 대학의 생성형 AI 활용 가이드라인 동향을 조사해서 보고서로 정리해 줘")
    assert out == ""


def test_a_worksheet_section_keeps_only_its_final_text() -> None:
    """Analysis, plan, draft, review and 「최종 텍스트 생성」 all in the section."""
    body = (
        "먼저 제공된 자료를 분석하여 \"가이드라인 공통 쟁점 분석\"에 해당되는 내용만 추출하고, "
        "요청된 형식(수치 제한, 문체, 출처)에 맞게 작성하겠습니다.\n\n"
        "**자료 분석 및 추출 내용:**\n1. **허용 범위 분류:** 아주대학교는 '전면 금지', "
        "'제한적 허용', '자유 사용 허용'으로 분류 [1].\n\n"
        "**수치 제한 확인:**\n-   사용 가능한 수치: 2023년, 05, 09, 00, 20%, 2023, 2024, 1085\n\n"
        "**작성 계획 (2~4단락):**\n-   **1단락:** 허용 범위와 사용 기준의 상이함.\n\n"
        "**초안 작성 및 수정:**\n(문체: ~합니다/입니다, 출처 표기, 빈칸 사용 주의)\n\n"
        "국내 대학 가이드라인은 생성형 AI의 허용 범위와 사용 기준에 있어 상이한 접근을 보이고 "
        "있습니다. 아주대학교는 활용 범위를 3단계로 분류합니다[1].\n\n"
        "**검토:**\n-   [5]는 2023 정책자문 자료집 목차만 있어 본문 내용 추출에 부적합하므로 "
        "생략합니다.\n\n"
        "**최종 텍스트 생성:**\n\n"
        "국내 대학 가이드라인은 생성형 AI의 허용 범위와 사용 기준에 있어 상이한 접근을 보이고 "
        "있습니다.\n\n데이터 보안과 부정행위 대응에 대한 기준도 명확해지고 있습니다(그림 1)."
    )
    out = internal_words.strip_work_notes(body, request="생성형 AI 가이드라인 동향 보고서")
    assert out == (
        "국내 대학 가이드라인은 생성형 AI의 허용 범위와 사용 기준에 있어 상이한 접근을 보이고 "
        "있습니다.\n\n데이터 보안과 부정행위 대응에 대한 기준도 명확해지고 있습니다(그림 1)."
    )


def test_a_worksheet_without_a_final_text_keeps_its_draft() -> None:
    body = (
        "**작성 계획:**\n- 1단락: 현황\n- 2단락: 과제\n\n"
        "**초안:**\n(문체: ~합니다)\n\n현황은 다음과 같습니다.\n\n과제는 두 가지입니다.\n\n"
        "**검토:**\n- 문체 통일 확인"
    )
    out = internal_words.strip_work_notes(body, request="현황 보고서")
    assert out == "현황은 다음과 같습니다.\n\n과제는 두 가지입니다."


def test_an_english_process_memo_before_the_answer_goes() -> None:
    """About forty lines of English notes, then the Korean briefing."""
    answer = (
        "The user is asking me to write the customer research briefing using only what I've "
        "already found, without further tools. Let me carefully review what I actually "
        "confirmed from search results.\n\n"
        "From [[3]](https://info.funbox.co.kr/kwater-jobs/) (info.funbox.co.kr, a blog — not "
        "authoritative): 설립 1967년, 환경부 산하 시장형 공기업.\n\n"
        "예상 이슈: 공공 클라우드 규제(설치형 선호), 기존 협업툴 대체 장벽.\n\n"
        "No Hanja, no English mixing, natural Korean. No horizontal rules.\n\n"
        "Write it.\n\n"
        "한국수자원공사(K-water)는 1967년 설립된 환경부 소관 공기업으로 물관리 전반을 담당하는 "
        "기관이고, 정보화 관련 신호는 「경영부문 정보관리처」라는 전담 조직입니다.\n\n"
        "## 한국수자원공사(K-water) 한눈에\n\n| 항목 | 내용 |\n|---|---|\n| 설립 | 1967년 |"
    )
    out = internal_words.strip_work_notes(
        answer, request="한국수자원공사 미팅 전에 고객 조사 브리핑 써 줘")
    assert out.startswith("한국수자원공사(K-water)는 1967년 설립된")
    assert "The user" not in out and "Write it." not in out
    assert "| 설립 | 1967년 |" in out


def test_a_document_that_is_itself_a_plan_is_left_alone() -> None:
    """Plan headings, one 「검토:」 line, a 「검토 결과」 heading and its verdict are content."""
    document = (
        "# 신규 서비스 출시 작업 계획\n\n## 작성 계획\n\n"
        "1단계: 시장 조사(10월), 2단계: 시안 작성(11월).\n\n"
        "**검토:**\n- 팀장 검토 후 확정\n\n## 검토 결과\n\n검토 결과, 계약서 3조의 해지 조항은 "
        "수정이 필요합니다.\n\n## 점검 일정\n\n점검 결과 이상 없음.\n\n"
        "[질문] 지원 동기는 무엇입니까?"
    )
    assert internal_words.strip_work_notes(
        document, request="신규 서비스 출시 작업 계획서 써 줘") == document


def test_an_english_deliverable_is_left_alone() -> None:
    email = ("I'll be in Seoul next week and would be glad to show you how the material flows "
             "through the tools your team already uses.\n\nBest regards,\nHyunsoo Kim")
    assert internal_words.strip_work_notes(email, request="영문 영업 메일 써 줘") == email


async def test_a_chat_answer_ships_without_the_verdict_line() -> None:
    request = "아래 메모로 이번 주 주간 업무 보고서 써 줘."
    bundle = chat_stages.bundle_for(
        answer="근거 대조 결과 근거 없는 값 0개.\n\n---\n\n# 주간 업무 보고서\n\n본문입니다.",
        request=request, model={"id": "m"}, actual_model=None, api_key="k", session_id="s",
        today=None, first_message=request, messages=[], material="", check_keys=(),
        check_material="", tool_outputs=[], judge="", kept_steps=[], usage={},
    )
    await chat_stages.Normalize().apply(bundle, Context())
    assert bundle.document.answer == "# 주간 업무 보고서\n\n본문입니다."


_REPORT_REQUEST = "해커톤 결과보고서 써 줘. 개요와 향후 계획을 나눠서."
_DRAFT = "## 개요\n행사는 10월 2일부터 3일까지 미래관에서 열렸다.\n## 향후 계획\ncheck\n"


async def test_a_section_left_as_check_is_written_once_more(monkeypatch) -> None:
    """「향후 계획」 written as the single word 「check」."""
    prompts: list[str] = []

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        prompt = "\n".join(str(m.get("content") or "") for m in messages)
        prompts.append(prompt)
        if len(prompts) == 1 and "향후 계획" in prompt and "개요" in prompt:
            return _DRAFT, {"inputTokens": 1, "outputTokens": 1}
        return "와이파이 AP를 두 대 더 두고, 휴식 공간을 1층 라운지에 마련한다.", {
            "inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    events = [e async for e in report.write(
        request=_REPORT_REQUEST, model="m", api_key="k", web_search=False,
        approved_plan={"title": "해커톤 결과보고서", "sections": ["개요", "향후 계획"]},
    )]
    sections = next(e["sections"] for e in events if e["type"] == "report")
    plan = next(s for s in sections if s["heading"] == "향후 계획")
    assert "휴식 공간" in plan["content"]
    assert "check" not in "\n".join(str(s["content"]) for s in sections)


# ── a turn that gathered material but wrote nothing ──────────────────────────


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


_CALL = ('data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
         '"function":{"name":"lookup","arguments":"{}"}}]}}]}')


class _Client:
    """A tool call, then empty hops (with and without tools), then whatever `last` says."""

    def __init__(self, seen: list[dict], last: list[str]) -> None:
        self._seen, self._last = seen, last

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
        self._seen.append(json)
        if len(self._seen) == 1:
            return _Response([_CALL, "data: [DONE]"])
        if len(self._seen) <= 3:
            return _Response([
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}', "data: [DONE]"])
        return _Response([*self._last, "data: [DONE]"])


async def _turn(monkeypatch, last: list[str]) -> tuple[list[dict], list[dict]]:
    seen: list[dict] = []

    async def client(*_args, **_kwargs):
        return _Client(seen, last)

    monkeypatch.setattr(agent, "_client", client)

    async def lookup(_args):
        return ToolResult(content="대웅제약 지속가능경영보고서: 정보보안을 핵심 정책 영역으로 다룸")

    tool = Tool(name="lookup", description="찾는다", parameters={"type": "object"}, run=lookup,
                label="찾기")
    events = [
        event
        async for event in agent.run_turn(
            "local/qwen3.8-flash",
            [{"role": "system", "content": "시스템"},
             {"role": "user", "content": "대웅제약 담당자에게 보낼 영업 메일 써 줘"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    return events, seen


@pytest.mark.asyncio
async def test_an_empty_turn_after_tools_is_written_once_more_from_the_results(
    monkeypatch,
) -> None:
    events, seen = await _turn(monkeypatch, [
        'data: {"choices":[{"delta":{"content":'
        '"대웅제약은 정보보안을 핵심 정책으로 다룹니다."}}]}'])

    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert text == "대웅제약은 정보보안을 핵심 정책으로 다룹니다."
    # tool hop, empty answer, empty retry, then the one write from the results
    assert len(seen) == 4
    last = seen[-1]
    assert "tools" not in last
    assert not any(m["role"] == "tool" or m.get("tool_calls") for m in last["messages"])
    assert last["messages"][0] == {"role": "system", "content": "시스템"}
    assert "정보보안을 핵심 정책 영역으로" in last["messages"][-1]["content"]
    usage = [e for e in events if e["type"] == "usage"]
    assert len(usage) == 1


@pytest.mark.asyncio
async def test_a_last_write_that_is_only_a_tool_call_is_no_answer(monkeypatch) -> None:
    events, seen = await _turn(monkeypatch, [
        'data: {"choices":[{"delta":{"content":"{\\"name\\": \\"web_search\\", '
        '\\"arguments\\": {\\"query\\": \\"대웅제약\\"}}"}}]}'])

    assert not [e for e in events if e["type"] in ("delta", "retract")]  # never shown
    assert len(seen) == 4  # tried once, not more


@pytest.mark.asyncio
async def test_no_last_write_without_tool_results(monkeypatch) -> None:
    """An empty answer with no tool use is not retried from 「results」 that do not exist."""
    seen: list[dict] = []

    class _Silent(_Client):
        def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
            self._seen.append(json)
            return _Response(["data: [DONE]"])

    async def client(*_args, **_kwargs):
        return _Silent(seen, [])

    monkeypatch.setattr(agent, "_client", client)
    events = [
        event
        async for event in agent.run_turn(
            "vendor/model", [{"role": "user", "content": "안녕"}], [],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]
    assert not [e for e in events if e["type"] == "delta"]
    assert len(seen) == 1
