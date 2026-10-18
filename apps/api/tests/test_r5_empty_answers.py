"""Empty or cut-off answers rewritten by the other local model; checks that keep the answer."""

from __future__ import annotations

import datetime as dt
from urllib.parse import urlsplit

import pytest

from app.services import (
    agent,
    calc_check,
    copy_check,
    deck,
    grounding,
    internal_words,
    report_checks,
    skill_scripts,
)
from app.services.tools.base import Tool, ToolContext, ToolResult

length = skill_scripts.module("copy-check", "length")
dates = skill_scripts.module("date-calc", "dates")

# ── 1. empty and announced answers ───────────────────────────────────────────

P05_REQUEST = ("혈당 관리 중인데 저녁 메뉴로 GI 낮고 단백질 많은 요리 하나 추천해 줘. "
               "1인분 열량이랑 탄단지도 알려 줘.")
P05_ANSWER = ("I have enough verified data. Let me compute the recipe's calorie and "
              "macronutrient totals precisely.")
A12_CUT = ("### 페이지 카피\n\n**메일 제목**\n"
           "한빛워크 회의록 요약, 11월 한 달 기존 고객 무료 (29자)\n"
           "*미리보기 문구: 화상회의 후 5분 안에 요약본을 보내드*")


def test_a_one_line_english_announcement_is_no_answer() -> None:
    assert internal_words.is_work_plan(P05_ANSWER, request=P05_REQUEST)
    # English asked for: still only an announcement
    assert internal_words.is_work_plan("Let me check the latest rates first.",
                                       request="What is the current base rate?")
    # an English answer that closes with an offer is an answer
    assert not internal_words.is_work_plan(
        "The base rate is 2.5%. Let me know if you want the history.",
        request="What is the current base rate?")
    assert not internal_words.is_announcement("저녁 메뉴로 렌틸콩 커리를 추천합니다.")


def test_a_rewrite_cut_off_short_beside_rich_results_is_not_shown() -> None:
    results = "헤드라인 매트릭스\n" + "회의 끝나고 5분이면 정리까지 끝나는 이유\n" * 120
    assert not agent._usable_rewrite(A12_CUT, request="헤드라인 여러 안", results=results)
    assert agent._usable_rewrite("기준금리는 연 2.50%입니다.", request="기준금리?", results=results)
    assert not agent._usable_rewrite(P05_ANSWER, request=P05_REQUEST, results=results)


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
_EMPTY = ['data: {"choices":[{"delta":{},"finish_reason":"stop"}]}', "data: [DONE]"]


def _said(text: str) -> list[str]:
    import json
    return [f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}", "data: [DONE]"]


class _Client:
    """The writer calls the tool once, then stays empty; the other model answers."""

    def __init__(self, seen: list[dict], writer_says: list[str] | None = None) -> None:
        self._seen, self._writer_says = seen, writer_says

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def stream(self, _method: str, _path: str, *, json: dict) -> _Response:
        self._seen.append(json)
        if json["model"] == "local/qwen3.8-27b":
            return _Response(_said(
                "렌틸콩 시금치 커리와 통보리밥을 추천합니다. 1인분 약 520kcal입니다."))
        if len(self._seen) == 1:
            return _Response([_CALL, "data: [DONE]"])
        return _Response(self._writer_says or _EMPTY)


async def _turn(monkeypatch, *, rescue: str | None, writer_says: list[str] | None = None):
    seen: list[dict] = []

    async def client(*_args, **_kwargs):
        return _Client(seen, writer_says)

    monkeypatch.setattr(agent, "_client", client)

    async def lookup(_args):
        return ToolResult(content="렌틸콩 GI 32, 100g당 단백질 9g. 통보리 GI 28.")

    tool = Tool(name="lookup", description="찾는다", parameters={"type": "object"}, run=lookup,
                label="찾기")
    events = [
        event
        async for event in agent.run_turn(
            "local/qwen3.8-flash",
            [{"role": "system", "content": "시스템"}, {"role": "user", "content": P05_REQUEST}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key",
                        request=P05_REQUEST),
            rescue_model=rescue,
        )
    ]
    return events, seen


@pytest.mark.asyncio
async def test_an_empty_answer_is_written_by_the_other_local_model(monkeypatch) -> None:
    events, seen = await _turn(monkeypatch, rescue="local/qwen3.8-27b")
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert text.startswith("렌틸콩 시금치 커리")
    # the empty writer is not asked a second time (a stall costs 180 s)
    assert [s["model"] for s in seen].count("local/qwen3.8-27b") == 1
    assert seen[-1]["model"] == "local/qwen3.8-27b" and "tools" not in seen[-1]
    usage = next(e for e in events if e["type"] == "usage")
    assert usage.get("model:local/qwen3.8-27b:inputTokens", 0) == 0  # no usage chunk sent
    assert any(e.get("id") == "answer-rescue" for e in events)


@pytest.mark.asyncio
async def test_an_announcement_is_rewritten_by_the_other_local_model(monkeypatch) -> None:
    events, seen = await _turn(monkeypatch, rescue="local/qwen3.8-27b",
                               writer_says=_said(P05_ANSWER))
    retracted = [e["text"] for e in events if e["type"] == "retract"]
    assert P05_ANSWER in retracted
    assert seen[-1]["model"] == "local/qwen3.8-27b"


@pytest.mark.asyncio
async def test_no_other_model_and_an_external_one_is_never_the_rescuer(monkeypatch) -> None:
    _, seen = await _turn(monkeypatch, rescue="vendor/big")
    assert {s["model"] for s in seen} == {"local/qwen3.8-flash"}


# ── 2. code values: a group's value never overwrites the overall one ─────────

A06_PRINTED = [
    "(1) 문항별 전체 통계 (평균 오름차순)\n"
    "   문항   평균  중위값  표준편차  1~2점비율(%)  4~5점비율(%)  낮은점(1~2)건수\n"
    "온라인학습 3.56  4.0  0.83       10.8       55.8          13\n"
    "   시설 3.68  4.0  0.87        5.0       56.7           6\n"
    "  교수법 3.78  4.0  0.73        2.5       65.0           3\n"
    " 전반만족 3.78  4.0  0.62        0.0       67.5           0\n"
    " 교과내용 3.88  4.0  0.84        4.2       66.7           5\n",
    "(3) 예술대학 응답 수: 18\n문항별 평균:\n"
    "  교과내용: 평균=3.22, 1~2점 건수=2\n  교수법: 평균=3.61, 1~2점 건수=1\n"
    "  시설: 평균=2.89, 1~2점 건수=4\n  온라인학습: 평균=3.06, 1~2점 건수=2\n"
    "  전반만족: 평균=3.39, 1~2점 건수=0\n",
]
A06_ANSWER = (
    "**1. 문항별 결과 (n=120, 5점 리커트, 평균 오름차순)**\n\n"
    "| 문항 | 평균 | 중위값 | 표준편차 | 1~2점 비율 | 4~5점 비율 |\n"
    "|---|---|---|---|---|---|\n"
    "| 온라인학습 | 3.56 | 4.0 | 0.83 | 10.8% | 55.8% |\n"
    "| 시설 | 3.68 | 4.0 | 0.87 | 5.0% | 56.7% |\n"
    "| 교과내용 | 3.88 | 4.0 | 0.84 | 4.2% | 66.7% |\n\n"
    "둘째, 예술대학 시설입니다. 평균 2.89점으로 유일하게 3점 미만이고, 5점이 한 건도 없으며 "
    "1~2점이 4건(전체 시설 1~2점 6건 중 4건)입니다."
)


def test_one_colleges_values_never_overwrite_the_overall_table() -> None:
    out, fixes = agent.code_checked(A06_ANSWER, A06_PRINTED)
    assert fixes == [] and out == A06_ANSWER


def test_a_value_printed_once_is_still_set() -> None:
    printed = ["SRTF: 평균 대기시간=3.00"]
    out, fixes = calc_check.check_markdown("SRTF의 평균 대기시간은 3.50입니다.",
                                           calc_check.printed_values(printed), printed)
    assert fixes and "3.00" in out


# ── 3. copy check: each table measured by its own channel ────────────────────

A13_REQUEST = ("한빛워크 체험 신청 광고 소재 만들어 줘. 네이버 검색광고, 메타 광고, 링크드인 "
               "광고 채널별로 3안씩, 채널별 글자 수 제한을 지켜서.")
A13_ANSWER = (
    "## 네이버 검색광고 (파워링크)\n\n"
    "| # | 각도 | 제목 | 자 | 설명 | 자 |\n|---|---|---|---|---|---|\n"
    "| 1 | 기능·혜택 | 망분리·설치·클라우드 | 13/15 | "
    "11월 신청 시 체험 기간 45일 연장 | 24/45 |\n\n"
    "## 메타 (페이스북·인스타그램 피드)\n\n"
    "| # | 각도 | 기본 문구 | 자 | 제목 | 자 | 설명 | 자 |\n|---|---|---|---|---|---|---|---|\n"
    "| 1 | 문제 공감 | 망분리 환경에서도 전자결재와 붙여 쓰는 업무시스템. | 27/125 | "
    "망분리 지원, 설치형·클라우드형 선택 | 20/40 | 11월 중 신청 시 체험 기간 45일 | 21/30 |\n"
)


def test_a_meta_title_is_measured_against_metas_limit() -> None:
    rows = length.targets(A13_REQUEST)
    items = {i["text"]: i["key"] for i in length.table_items(A13_ANSWER, rows)}
    assert items["망분리 지원, 설치형·클라우드형 선택"] == "meta.headline"
    assert items["망분리·설치·클라우드"] == "naver_powerlink.title"
    assert items["11월 중 신청 시 체험 기간 45일"] == "meta.description"


def test_counts_written_over_their_limit_are_measured_again() -> None:
    rows = length.targets(A13_REQUEST)
    measured = length.measured(length.table_items(A13_ANSWER, rows), rows)
    out = length.restate_counts(A13_ANSWER, measured)
    assert "| 망분리·설치·클라우드 | 11/15 |" in out
    assert "| 11월 신청 시 체험 기간 45일 연장 | 21/45 |" in out


def test_a_clause_of_a_measured_cell_is_not_measured_alone() -> None:
    rows = length.targets(A13_REQUEST)
    judged = {"items": [{"key": "naver_powerlink.description", "text": "11월 신청 시 체험 기간"}]}
    texts = [i["text"] for i in copy_check._items(judged, A13_ANSWER, rows)]
    assert "11월 신청 시 체험 기간" not in texts


def test_a_rewrite_that_brings_a_site_or_a_link_is_not_kept() -> None:
    before = ("대웅제약이 충북 청주 오송공장과 경기 화성 향남공장에서 의약품을 생산하며, "
              "스마트공장을 운영 중이라는 보도를 보았습니다.")
    after = ("대웅제약은 본사와 이천 공장 등 다수의 사업장을 운영하는 제약·바이오 기업으로 "
             "파악됩니다. [[1]](https://www.daewoongpharm.com/)")
    added = copy_check.added_facts(after, before, "대웅제약 협업툴 제안 메일")
    assert {"이천 공장", "https://www.daewoongpharm.com/"} <= set(added)
    assert copy_check.added_facts(before, before, "") == []


# ── 4. a deck's quiz is the request's, not the material's ────────────────────

A09_REQUEST = (
    "다음 주 학회(한국정보과학회 추계) 구두 발표용 슬라이드 만들어 줘. 발표 15분, 10장 안팎. "
    "아래 초록과 실험 결과로.\n\n"
    "제목: 소형 언어모델을 위한 검색 증강 생성에서의 문서 재순위화 경량화\n"
    "저자: 이서준, 김나래, 최영훈(한빛대학교 컴퓨터공학과)\n\n"
    "초록: 검색 증강 생성(RAG)은 소형 언어모델(sLM)의 사실성을 높이지만, 교차 인코더 기반 "
    "재순위화가 전체 지연 시간의 상당 부분을 차지한다. 한국어 질의응답 데이터셋 KorQuAD-RAG와 "
    "사내 문서 QA 세트(1,200문항)에서 평가했다.\n"
    "- 조기 종료 제거 시: EM 72.1, 지연 71ms"
)


def test_items_counted_in_the_material_ask_for_no_quiz() -> None:
    slides = [{"title": "연구 배경"}, {"title": "평가 데이터셋과 설정",
                                      "bullets": ["KorQuAD-RAG", "사내 QA 1,200문항"]}]
    assert deck.requested_contents(slides, A09_REQUEST) == {}
    asked = ("운영체제 50분 강의용 슬라이드 만들어 줘. 마지막에 비교 표와 확인 퀴즈 2문제를 "
             "넣어 줘.\n\n예제 (도착 시간, 실행 시간)\nP1 0 8")
    quiz = [{"title": "개요"}, {"title": "확인 퀴즈", "bullets": ["정리"]}]
    assert 1 in deck.requested_contents(quiz, asked)


# ── 5. citations across turns ────────────────────────────────────────────────

TURN_1 = (
    "위기 상담은 109로 전화하세요 [[2]](https://129.go.kr/109).\n\n### 출처\n"
    "- [1] [정신건강 정보 · mentalhealth.go.kr](https://www.mentalhealth.go.kr/a)\n"
    "- [2] [자살예방상담전화 | 보건복지상담센터 · 129.go.kr](https://129.go.kr/109)\n"
    "- [6] [Liverpool's fatal 1911 riots remembered - BBC News · bbc.com]"
    "(https://www.bbc.com/news/uk-england-merseyside-14532345)"
)


def test_an_earlier_number_keeps_its_source_and_new_results_follow_it() -> None:
    sources, titles = agent._earlier_sources([{"role": "user", "content": "q"},
                                              {"role": "assistant", "content": TURN_1}])
    assert sources[1] == "https://129.go.kr/109" and urlsplit(sources[5]).hostname == "www.bbc.com"
    assert sources[2] == ""
    assert titles["https://129.go.kr/109"] == "자살예방상담전화 | 보건복지상담센터"
    numbered = agent._number_sources(
        "[1] Interpol Review Papers\nhttps://www.interpol.int/review.pdf", sources,
        {"name": "web_search", "arguments": "{}"})
    assert numbered.startswith("[7] ")
    linked, cited = agent._link_citations("1911년 리버풀 폭동 [6], 새 자료 [7], 모름 [3].", sources)
    assert "[[6]](https://www.bbc.com/news/uk-england-merseyside-14532345)" in linked
    assert "[[7]](https://www.interpol.int/review.pdf)" in linked and "모름 [3]" in linked
    assert cited == [6, 7]


def test_the_writers_own_closing_list_is_found() -> None:
    answer = ("본문 [1].\n\n### 출처\n- [1] [A](https://a.example/x)\n- [2] [B](https://b.example/y)")
    at, end, numbers = agent._own_source_list(answer)
    assert answer[at:].startswith("### 출처") and numbers == [1, 2] and end == len(answer)
    assert agent._own_source_list("본문.\n\n### 출처\n그 밖의 말") is None
    # a paragraph after the list does not hide it
    tail = answer + "\n\n보완이 필요한 항목: 예산 [2]."
    at, end, numbers = agent._own_source_list(tail)
    assert numbers == [1, 2] and tail[end:].strip() == "보완이 필요한 항목: 예산 [2]."


# ── 6. question cards ────────────────────────────────────────────────────────


def test_needless_questions_are_dropped() -> None:
    a03 = ('{"needs": [{"question": "이 협조 요청 공문의 시행(발송)일이 언제입니까?", '
           '"options": ["2026. 10. 13.(화)", "다른 날짜 (입력)"]}]}')
    assert grounding.parse_needs(a03, "각 학과에 보낼 협조 요청 공문을 써 줘.") is None
    a09 = ('{"needs": [{"question": "간트 차트와 대기시간을 실제로 계산해 넣을지, 자리만 '
           '틀어둘까요.", "options": ["코드로 계산해 숫자를 채우겠습니다", '
           '"[수치]로 빈칸 두고 발표자가 채웁니다"]}]}')
    assert grounding.parse_needs(
        a09, "모든 알고리즘의 간트 차트와 평균 대기시간을 계산해서") is None
    a04 = ('{"needs": [{"question": "어떤 시점을 계약일로 봐서 납기를 비교할까요?", '
           '"options": ["오늘(10월 12일) 계약 가정", "다른 날짜로 잡아 주세요"]}]}')
    assert grounding.parse_needs(a04, "납기는 오늘 10월 12일 계약 가정으로 비교해 줘") is None
    real = ('{"needs": [{"question": "무엇을 결정하는 보고서입니까?", "options": []}]}')
    assert grounding.parse_needs(real, "처장님 결재용 한 장 보고") is not None


# ── 7. a rule's offset counts from its event, not today ──────────────────────

A05_RULE = "제12조(정산) 출장자는 출장 종료 후 7일 이내에 영수증을 첨부하여 여비를 정산하여야 한다."


def test_an_offset_from_an_event_does_not_move_a_right_date() -> None:
    answer = "예를 들어 다음 주 월요일은 2026-10-19(월)이므로 정산 기한도 그에 맞춰 계산됩니다."
    out = dates.post(answer, {"today": "2026-10-13", "request": "다음 주 부산 출장",
                              "material": A05_RULE})
    assert out["text"] == answer and not out["fixed"]
    # an offset from today still sets its date
    out = dates.post("마감은 2026-10-21입니다.", {"today": "2026-10-13",
                                                 "request": "7일 이내에 제출해 줘"})
    assert "2026-10-20" in out["text"]


# ── 8. minutes: a blanked due comes back from the memo ───────────────────────

A01_MEMO = (
    "[메모] 10/8(목) 14시 본관 3층 소회의실, 교육혁신팀 주간회의\n"
    "참석: 박정민 팀장, 이수진 차장, 김도윤 대리, 최하늘 주임\n"
    "3. 우수 강의 사례집 — 원고 12편 중 9편 접수. 미제출 3편은 교수님들께 10/16까지 "
    "재요청(최하늘)."
)


def test_a_due_blanked_by_a_check_gets_the_memos_date() -> None:
    sections = [{"heading": "할 일(담당·기한)", "content": (
        "| 할 일 | 담당 | 기한 | 비고 |\n|---|---|---|---|\n"
        "| 미제출 원고 3편 재요청 | 최하늘 | [기한] | 교수님들께 재요청 |")}]
    found = dates.scan(A01_MEMO, dt.date(2026, 10, 8))
    out, filled = report_checks.minutes_due_fill(sections, found, blanked=True)
    assert filled == 1 and "[기한]" not in out[0]["content"]
    assert "10" in out[0]["content"].split("| 최하늘 |")[1].split("|")[0]
