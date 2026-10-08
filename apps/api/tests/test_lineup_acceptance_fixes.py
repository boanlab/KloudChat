"""Fixes from the lineup v1 acceptance run, each pinned with the run's own text."""

from __future__ import annotations

import json

from app.services import report_checks

# ── B11: values a pasted table gives are not 「조건 미확인」 ─────────────────

OFFICE_REQUEST = (
    "사무실 이전 후보 세 곳 비교해서 대표님께 올릴 보고서 써 줘. 3년 계약 기준 총비용으로 "
    "비교하는데 "
    "보증금은 돌려받으니까 비용에 넣지 말고 따로 적어 줘. 직원 평균 통근 시간이 50분 넘는 곳은 안 "
    "돼.")
OFFICE_TABLE = (
    "| 후보 | 월 임대료 | 월 관리비 | 일회성(인테리어·이전) | 보증금 | 직원 평균 통근(분) |\n"
    "| 판교 A타워 | 8,400,000원 | 1,350,000원 | 62,000,000원 | 1억 원 | 47 |\n"
    "| 성수 B빌딩 | 7,200,000원 | 1,900,000원 | 48,000,000원 | 1억 5천만 원 | 52 |\n"
    "| 문정 C센터 | 6,300,000원 | 1,100,000원 | 75,000,000원 | 8천만 원 | 58 |\n"
    "(통근 시간: 9월 직원 설문 41명 응답 평균)")
#: comparison-report-2 r2: the reading named the condition but left every value null.
OFFICE_READING = {
    "criteria": [],
    "musts": [{"name": "직원 평균 통근 시간", "kind": "max", "limit": 50, "unit": "분"}],
    "options": [{"name": "판교 A타워", "musts": {"직원 평균 통근 시간": None}},
                {"name": "성수 B빌딩", "musts": {}},
                {"name": "문정 C센터", "musts": {"직원 평균 통근 시간": None}}],
}


def test_a_pasted_table_without_a_rule_line_is_read_per_option():
    [table] = report_checks.material_tables(OFFICE_TABLE)
    assert table["판교 A타워"]["직원 평균 통근(분)"] == "47"
    assert table["문정 C센터"]["보증금"] == "8천만 원"


def test_commute_minutes_in_the_table_settle_the_must_have_by_code():
    given = f"{OFFICE_REQUEST}\n{OFFICE_TABLE}"
    numbers = report_checks._material_numbers(given)
    assert report_checks.must_status(OFFICE_READING, numbers)["options"]["판교 A타워"][
        "status"] == "조건 미확인"
    result = report_checks.must_status(OFFICE_READING, numbers, given)
    assert {o: r["status"] for o, r in result["options"].items()} == {
        "판교 A타워": "충족", "성수 B빌딩": "탈락", "문정 C센터": "탈락"}
    assert "52분 > 조건 50분" in result["options"]["성수 B빌딩"]["failed"][0]


def test_a_reading_with_no_options_takes_them_from_the_table():
    reading = {"musts": OFFICE_READING["musts"], "options": []}
    given = f"{OFFICE_REQUEST}\n{OFFICE_TABLE}"
    result = report_checks.must_status(reading, report_checks._material_numbers(given), given)
    assert result["options"]["판교 A타워"]["status"] == "충족"


# ── internal wording never reaches the page ─────────────────────────────

from app.services import internal_words, report  # noqa: E402

#: minutes-4 r2's action table and closing paragraph.
MINUTES_4_TABLE = (
    "| 할 일 | 담당 | 기한 | 출처 | 비고 |\n|---|---|---|---|---|\n"
    "| 안전교육 자료 제작 | 유주임 | 미정 | 회의 | 1월 중순(1월 15일까지)으로 합의, 정확한 날짜는 "
    "코드 미해석 |\n"
    "| 회의록 작성 및 회람 | 남주무관 | 미정 | 회의 | 원문 '월요일까지', 코드 미해석 |")
MINUTES_4_PROSE = (
    "유주임은 동선 계획과 안전교육 자료를 준비하고, 탁주무관은 유학생 지원 단가 자료를 이번 달 "
    "말까지 "
    "제공해야 합니다. 안전교육 자료 기한은 1월 15일까지로 정해졌으나, 확정 날짜 블록에서 이 표현이 "
    "코드 해석되지 않아 표의 기한 칸은 미정으로 유지합니다(표 4).")
MINUTES_1_R1 = ("이 두 작업의 기한은 코드가 계산해 준 확정 날짜를 그대로 적용하며, 교무처 제출 "
                "서류 취합은 "
                "외부 마감일과 내부 기한이 겹쳐 별도 표기로 관리합니다(표 4).")
MINUTES_1_R2 = ("원문 표현은 「다음 주 금요일」입니다. 교무처 마감은 외부 마감이며, 내부 취합 완료 "
                "시점은 확정 "
                "수치 블록에 해당 날짜 해석값이 없어 [기안 내부 취합 기한]으로 표시합니다.")
MINUTES_5_R1 = (
    "2차 회의에서 류○○는 포스터 공고문 초안은 작성했으나 게시는 하지 않았다고 보고했고, 회의에서는 "
    "다음 주 "
    "월요일까지 게시하기로 했습니다. 확정 날짜 기준 2026년 11월 16일(월)로 풀렸으나, 이는 2차 회의 "
    "기준(11월 13일)이 아니라 1차 회의 기준(9월 23일)의 '다음 주 월요일' 해석값이므로 본문에서는 "
    "'다음 "
    "주 월요일' 표현을 그대로 두고 실제 게시 시점은 메일 2(10/12)로 잡습니다.")


def test_the_runs_internal_wording_is_found():
    for text in (MINUTES_4_TABLE, MINUTES_4_PROSE, MINUTES_1_R1, MINUTES_1_R2, MINUTES_5_R1):
        assert internal_words.found(text), text
    for said in ("견적 확정 블록의 7,727,273원과 다릅니다", "사전 계산 결과", "date-calc가 풀었다",
                 "확정 수치에 따르면", "총비용 계산(코드)"):
        assert internal_words.found(said), said
    # The subject's own words are not internal.
    assert not internal_words.found("회의에서 확정된 날짜는 10월 16일이다. 코드 리뷰는 남주무관.")


def test_a_table_cell_keeps_its_subject_and_loses_the_internal_clause():
    cleaned = internal_words.scrub(MINUTES_4_TABLE)
    assert "코드" not in cleaned
    assert "| 1월 중순(1월 15일까지)으로 합의 |" in cleaned
    assert "| 원문 '월요일까지' |" in cleaned
    assert cleaned.count("\n") == MINUTES_4_TABLE.count("\n")


def test_prose_loses_the_sentence_that_names_a_block_and_keeps_the_rest():
    cleaned = internal_words.scrub(MINUTES_4_PROSE)
    assert cleaned.startswith("유주임은 동선 계획과") and "블록" not in cleaned
    assert internal_words.scrub(MINUTES_1_R2) == "원문 표현은 「다음 주 금요일」입니다."
    kept = internal_words.scrub(MINUTES_5_R1)
    assert "게시하기로 했습니다." in kept and "확정 날짜" not in kept


def test_final_sections_and_answers_are_scrubbed():
    sections = report.final_tidy([{"heading": "실행 항목", "content": MINUTES_4_TABLE + "\n\n"
                                   + MINUTES_1_R1}])
    assert not internal_words.found(sections[0]["content"])
    # An answer may say it ran code; it may not name a block or a step.
    answer = "코드로 계산한 결과 평균은 3.47입니다. 확정 수치 블록에 없는 값은 쓰지 않았습니다."
    assert internal_words.scrub(answer, document=False) == "코드로 계산한 결과 평균은 3.47입니다."


def test_the_writer_is_told_the_blocks_are_internal():
    assert "내부 메모" in internal_words.WRITER_NOTE and "코드 미해석" in internal_words.WRITER_NOTE


# ── head fields the material gives are filled by code ───────────────────

from app.services import doc_formats  # noqa: E402

MINUTES_1 = (
    "어제 교육과정위원회 메모야. 정식 회의록으로 만들어 줘. 작성자는 나(조교 정다은)로 해 줘.\n"
    "[메모 — 컴퓨터공학과 교육과정위원회, 2026-10-12(월) 15:00~16:10, 공학관 302호]\n"
    "참석: 김교수(위원장), 박교수, 이교수, 학생대표 최○○, 조교 정다은 / 불참: 윤교수(출장)\n"
    "- 2027학년도 교육과정: 전공선택 ‘생성형 AI 활용’(3학점) 신설")
OT_TRANSCRIPT = (
    "[자동 전사본 — 2027학년도 신입생 오리엔테이션 준비 회의]\n"
    "[일시: 2026-10-08(목) 14:00~15:40, 장소: 학생회관 2층 회의실]\n"
    "[참석: 학생지원팀 백팀장(진행), 강주무관, 남주무관, 안전관리팀 유주임, 국제교류팀 탁주무관, "
    "총학생회 OT기획단장 염○○]\n[전사 메모: 화자 구분은 자동, 일부 오인식 있음]\n\n"
    "백팀장: 시작하겠습니다.")
CAREER_2 = (
    "해솔핀테크 시니어 백엔드 지원할 경력기술서 만들어 줘. 결제 쪽 경력을 앞에 세워 줘.\n"
    "[경력 정리 — 박준영]\n"
    "지원: (주)해솔핀테크 시니어 백엔드 개발자 (공고 자격: 백엔드 개발 경력 6년 이상)")


def test_minutes_head_is_read_from_the_memo_header():
    read = doc_formats.head_values(doc_formats.BY_ID["minutes"], MINUTES_1)
    assert read == {
        "회의명": "컴퓨터공학과 교육과정위원회",
        "일시": "2026-10-12(월) 15:00~16:10",
        "장소": "공학관 302호",
        "참석자": "김교수(위원장), 박교수, 이교수, 학생대표 최○○, 조교 정다은",
        "작성자": "조교 정다은",
    }


def test_minutes_head_is_read_from_the_transcript_header():
    read = doc_formats.head_values(doc_formats.BY_ID["minutes"], OT_TRANSCRIPT)
    assert read["회의명"] == "2027학년도 신입생 오리엔테이션 준비 회의"
    assert read["일시"] == "2026-10-08(목) 14:00~15:40"
    assert read["장소"] == "학생회관 2층 회의실"
    assert read["참석자"].startswith("학생지원팀 백팀장(진행), 강주무관")
    # Nothing names who writes it: left for the person, not guessed.
    assert "작성자" not in read


def test_career_name_and_gaejo_reporter_are_read():
    career = doc_formats.BY_ID["career_history"]
    assert doc_formats.head_values(career, CAREER_2)["이름"] == "박준영"
    gaejo = doc_formats.BY_ID["gaejo"]
    said = doc_formats.head_values(gaejo, "아래 메모를 1쪽 개조식 보고로 바꿔 줘. 보고자는 시설팀 "
                                          "김도윤 주무관.")
    assert said == {"보고자(부서)": "시설팀 김도윤 주무관"}
    pack = ("- 발주(계약) 예정일: 2026. 10. 19.(월)\n- 담당: 교무팀 최하늘 주무관, 보고 대상: "
            "교무처장")
    assert doc_formats.head_values(gaejo, pack) == {
        "보고자(부서)": "교무팀 최하늘 주무관", "보고 대상": "교무처장"}


async def test_title_block_takes_code_values_and_asks_the_model_only_for_the_rest(monkeypatch):
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        asked.append(messages[0]["content"])
        # The model makes up a writer; it is not in the material, so it stays blank.
        return '{"fields": {"작성자": "김철수", "회의명": "엉뚱한 회의"}}', {}

    monkeypatch.setattr(report, "_complete", complete)
    fmt = doc_formats.BY_ID["minutes"]
    block, _ = await report.title_block_for(fmt, "회의록으로 만들어 "
                                                 "줘.", "OT 준비 회의록", "m", "k",
                                            OT_TRANSCRIPT)
    fields = dict(block["fields"])
    assert fields["회의명"] == "2027학년도 신입생 오리엔테이션 준비 회의"
    assert fields["장소"] == "학생회관 2층 회의실" and fields["작성자"] == ""
    assert asked and "학생회관 2층 회의실" in asked[0]
    asked.clear()
    block, _ = await report.title_block_for(fmt, MINUTES_1, "회의록", "m", "k")
    assert asked == [] and dict(block["fields"])["작성자"] == "조교 정다은"


# ── a blank the material can fill is found and repaired once ────────────

from app.services import verify  # noqa: E402

OT_LINES = (
    "남주무관: 회당 900명 중에 지하철 이용이 절반쯤이면 450명, 45인승으로 10대 정도요.\n"
    "유주임: 저 확인됐습니다. 체육관 소방 기준 수용 인원이 750명이에요. 900명은 안 됩니다.\n"
    "유주임: 대강당이 600석이고 체육관 750이니까, 분산하면 됩니다. 대강당에서 본 행사 하고 "
    "체육관은 "
    "동아리 박람회로.\n"
    "염○○: 회당 900명인데 대강당이 600석이면 300명은요?")
#: minutes-4 r2's open-items paragraph.
MINUTES_4_OPEN = (
    "기획단 운영비와 티셔츠 제작 여부는 본부 예산 배정이 확정된 후에 다시 상정하며, 현재 단계에서 "
    "금액을 "
    "정하지 않습니다. OT 일정을 확인한 결과 회당 [인원] 규모를 대강당 [석수]와 체육관 [석수]로 "
    "분산해야 "
    "하는데, 남은 인원을 어떤 순서로 배치할지는 유주임이 동선 계획을 재작성한 뒤 다음 회의에서 "
    "확정합니다.")
FIRST_MEETING = ("[1차 회의(2026-09-23) 실행 항목]\n| 할 일 | 담당 | 기한 |\n| 기조연사 섭외 | "
                 "배교수 | 10/2 |\n"
                 "| 세미나실 예약 | 신조교 | 9/30 |\n| 포스터 세션 공고 | 류○○(대학원생) | 10/9 |\n"
                 "- 기조연사: 배교수 섭외 완료(10/1 수락), 1명")
#: minutes-5 r1's follow-up table.
MINUTES_5_ROWS = (
    "| 할 일 | 담당 | 원 기한 | 완료일 | 상태 | 비고 | 출처 |\n|---|---|---|---|---|---|---|\n"
    "| 기조연사 섭외 | 배교수 | [기한] | 10/1 | 완료 | 10/1 수락 | 회의 |\n"
    "| 세미나실 예약 | 신조교 | [기한] | 완료(날짜 미기재) | 완료 | 11/13(금) 13:00~18:00, 인문관 "
    "대회의실 | 회의 |")


def test_people_and_seats_the_transcript_gives_are_not_blanks():
    found = report_checks.answerable_blanks(
        [{"heading": "미결", "content": MINUTES_4_OPEN}], OT_LINES)
    blanks = {blank for _, _, blank, _ in found}
    assert blanks == {"[인원]", "[석수]"}
    piece = found[0][1]
    assert piece.startswith("OT 일정을 확인한 결과") and "기획단" not in piece
    evidence = "\n".join(e for *_, ev in found for e in ev)
    assert "600석" in evidence and "900명" in evidence


def test_first_meeting_deadlines_in_the_table_are_not_blanks():
    found = report_checks.answerable_blanks([{"heading": "1차 실행", "content": MINUTES_5_ROWS}],
                                            FIRST_MEETING)
    rows = {piece.split("|")[1].strip(): ev for _, piece, _, ev in found}
    assert rows["기조연사 섭외"] == ["| 기조연사 섭외 | 배교수 | 10/2 |"]
    assert rows["세미나실 예약"] == ["| 세미나실 예약 | 신조교 | 9/30 |"]


def test_a_blank_the_material_does_not_fill_stays():
    # 교무처 마감 11/2 is the office's deadline, already in 비고; the internal one is not given.
    row = "| 교무처 제출 서류 취합 | 정조교 | [기한] | 회의 | 교무처 마감 11/2 |"
    memo = "- 교무처 제출 서류 취합 → 정조교 (교무처 마감 11/2)"
    assert report_checks.answerable_blanks([{"heading": "실행", "content": row}], memo) == []
    assert report_checks.answerable_blanks(
        [{"heading": "결정", "content": "다과 단가는 [견적 금액]으로 "
                                      "남깁니다."}], "다과: 120명분") == []


async def test_the_blank_is_repaired_once_and_checked_again():
    sections = [{"heading": "1차 실행", "content": MINUTES_5_ROWS}]
    doc = verify.Doc(sections, FIRST_MEETING)
    pre = report_checks.Prepass(format_id="minutes")
    found, _ = await verify.find(doc, [report_checks.material_blanks])
    assert [f.code for f in found] == ["placeholder_answerable"] * 2

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        return ('["| 기조연사 섭외 | 배교수 | 10/2 | 10/1 | 완료 | 10/1 수락 | 회의 |", '
                '"| 세미나실 예약 | 신조교 | 9/30 | 완료(날짜 미기재) | 완료 | '
                '11/13(금) 13:00~18:00, '
                '인문관 대회의실 | 회의 |"]'), {"inputTokens": 1, "outputTokens": 1}

    fixed, left, _ = await verify.verify_and_repair(
        doc, report_checks.checks(pre), complete=complete, model="m", api_key="k")
    assert left == [] and "[기한]" not in fixed[0]["content"]
    assert report_checks.LABELS["placeholder_answerable"]


# ── a chat answer: numeric blanks computed once; internal names taken out ──

from app.core.config import settings  # noqa: E402
from app.services import agent  # noqa: E402
from app.services.tools.base import Tool, ToolContext, ToolResult  # noqa: E402


class _Lines:
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


def _said(text: str) -> list[str]:
    return ['data: ' + json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False),
            "data: [DONE]"]


_RUN = ['data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
        '"function":{"name":"run_code","arguments":"{}"}}]}}]}', "data: [DONE]"]


def _scripted(replies: list[list[str]], seen: list[dict]):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        def stream(self, _method: str, _path: str, *, json: dict) -> _Lines:
            seen.append(json)
            return _Lines(replies[len(seen) - 1])

    async def client(*_args, **_kwargs):
        return _Client()

    return client


async def _answer(monkeypatch, replies: list[list[str]]) -> tuple[str, list[dict]]:
    seen: list[dict] = []
    monkeypatch.setattr(agent, "_client", _scripted(replies, seen))
    monkeypatch.setattr(settings, "max_tool_hops", 6)

    async def run(_args):
        return ToolResult(content="mean_without_outlier 9.09")

    tool = Tool(name="run_code", description="코드", parameters={"type": "object"}, run=run,
                label="파일 작업")
    shown = ""
    async for event in agent.run_turn(
        "vendor/model", [{"role": "user", "content": "작년과 올해 처리일수를 비교해 줘."}], [tool],
        ToolContext(user_id="u", session_id="s", api_key="k"),
    ):
        if event["type"] == "delta":
            shown += event["text"]
        elif event["type"] == "retract":
            at = shown.rfind(event["text"])
            shown = shown[:at] + shown[at + len(event["text"]):] if at >= 0 else shown
    return shown, seen


async def test_a_numeric_blank_after_run_code_is_computed_once(monkeypatch):
    # data-analysis-4 r2: 「2025-0012(187일)를 제외하면 2025년 평균 처리일수는 [수치]일」.
    blank = "2025-0012(187일)를 제외하면 2025년 평균 처리일수는 [수치]일로 더 낮아집니다."
    shown, seen = await _answer(monkeypatch, [
        _RUN, _said(blank), _RUN, _said("2025-0012(187일)를 제외하면 평균은 9.09일입니다.")])
    assert shown == "2025-0012(187일)를 제외하면 평균은 9.09일입니다."
    assert any(agent._COMPUTE_BLANKS == m.get("content") for m in seen[2]["messages"])
    assert seen[2].get("tools")


async def test_a_blank_left_after_the_one_retry_stays(monkeypatch):
    blank = "직전 학기 평점은 [수치]점입니다."
    shown, seen = await _answer(monkeypatch, [_RUN, _said(blank), _said(blank)])
    assert shown == blank and len(seen) == 3


async def test_an_answer_naming_a_block_is_resent_without_it(monkeypatch):
    shown, _ = await _answer(monkeypatch, [_RUN, _said(
        "참여 평균은 3.47입니다. 확정 수치 블록에 없는 값은 쓰지 않았습니다.")])
    assert shown == "참여 평균은 3.47입니다."


# ── a follow-up is revised everywhere; the document is read for one fact two ways ──

from app.services import consistency  # noqa: E402

LMS_REQUEST = (
    "LMS 세 개 중에 가성비 좋은 걸로 골라 줘. 보고서로.\n\n"
    "- 런큐브: 연 18,000,000원(300명까지), 모바일 앱 있음, SCORM 지원, 도입 사례 공공기관 12곳\n"
    "- 에듀노트: 1인 월 4,800원, 모바일 웹만, SCORM 지원, 무료 체험 30일\n"
    "- 클래스온: 견적 요청 중(다음 주 회신), 모바일 앱 있음, SCORM 미지원, 사내 HR 시스템 연동 "
    "기본 제공\n"
    "우리 직원 300명.")
LMS_FOLLOW_UP = "SCORM은 필수야. 그리고 비용 50, 모바일 앱 30, HR 연동 20으로 다시 봐 줘."
#: comparison-report-4 r2's headings; the planner rewrote 2 and 4 only.
LMS_NAMES = ["요약", "결정 기준과 필수 조건", "대안별 기본 정보와 비용 전제", "솔루션별 종합 비교",
             "출처별 가격과 기능 차이", "조건 미확인 사항과 리스크", "권고안과 다음 단계"]
LMS_SUMMARY = (
    "런큐브, 에듀노트, 클래스온 세 LMS 가운데 300명 기준 연 총비용을 비교하면 "
    "에듀노트(17,280,000원)가 "
    "최저비용입니다. SCORM 지원과 모바일 사용 가능이라는 필수 조건을 모두 충족하는 대안 중 가장 싼 "
    "것이므로 "
    "에듀노트를 권고합니다.")


def test_a_changed_criterion_widens_the_rewrite_to_summary_and_conclusion():
    texts = [LMS_SUMMARY, "LMS 선택의 필수 조건은 첫째, SCORM 표준 콘텐츠 호환 "
                          "여부", "", "| 연 총비용 (원) |",
             "", "클래스온의 가격 미확정은", "에듀노트를 권고합니다."]
    widened = consistency.dependents(LMS_FOLLOW_UP, LMS_NAMES, texts, [2, 4])
    # 리스크 (5) names nothing the follow-up changed; everything that restates is widened,
    # the summary at the top last.
    assert widened == [1, 3, 6, 0]
    # A wording request widens nothing.
    assert consistency.dependents("문장을 더 간결하게 다듬어 줘", LMS_NAMES, texts, [2]) == []


def test_career_start_month_follow_up_reaches_the_summary():
    names = ["경력 요약", "경력 사항", "주요 프로젝트", "보유 기술", "자격 및 교육"]
    widened = consistency.dependents("입사는 2019년 4월이었어. 총 경력 맞는지 다시 봐 줘.", names,
                                     ["", "", "", "", ""], [1])
    assert 0 in widened


async def test_the_old_recommendation_left_in_summary_and_conclusion_is_repaired(monkeypatch):
    """r2: re-weighted in two parts, the summary and 권고안 still recommend the cheapest."""
    reading = {
        "criteria": [{"name": "비용", "weight": 50, "kind": "cost"},
                     {"name": "모바일 앱", "weight": 30, "kind": "yesno"},
                     {"name": "HR 연동", "weight": 20, "kind": "yesno"}],
        "musts": [{"name": "SCORM", "kind": "yesno"}],
        "options": [
            {"name": "런큐브", "values": {"비용": 18000000, "모바일 앱": "있음", "HR 연동": "없음"},
             "musts": {"SCORM": "있음"}},
            {"name": "에듀노트",
             "values": {"비용": 17280000, "모바일 앱": "없음", "HR 연동": "없음"},
             "musts": {"SCORM": "있음"}},
            {"name": "클래스온", "values": {"비용": None, "모바일 앱": "있음", "HR 연동": "있음"},
             "musts": {"SCORM": "없음"}}]}
    given = f"{LMS_REQUEST}\n추가 요청: {LMS_FOLLOW_UP}"
    pre = report_checks.Prepass(format_id="comparison",
                                scores=report_checks.scores(reading),
                                musts=report_checks.must_status(
                                    reading, report_checks._material_numbers(given)))
    assert consistency._leader(pre) == "런큐브"
    parts = [{"heading": "요약", "content": LMS_SUMMARY},
             {"heading": "대안별 기본 정보와 비용 전제",
              "content": "비용 점수는 가중치 50%이며, 모바일 앱과 HR 연동은 각각 30%, 20%다."},
             {"heading": "권고안과 다음 단계", "content": "에듀노트를 권고합니다."}]
    replies = {
        "에듀노트를 권고합니다.": "가중 점수 1위인 런큐브를 권고합니다.",
    }

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        items = json.loads(messages[0]["content"].rsplit("항목:\n", 1)[1])
        out = []
        for item in items:
            sentence = item["sentence"]
            out.append(replies.get(sentence) or sentence.replace(
                "에듀노트를 권고합니다", "가중 점수 1위인 런큐브를 권고합니다"))
        return json.dumps(out, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    fixed, step, _ = await consistency.align(
        parts, changed={1}, instruction=LMS_FOLLOW_UP, request=LMS_REQUEST, material="",
        pre=pre, model="m", api_key="k")
    assert step["label"].startswith("앞뒤 맞춤 검사: ") and step["label"].endswith("→ 0건")
    assert "권고가 필수 조건·점수와 어긋남" in step["detail"]
    assert "런큐브를 권고" in fixed[0]["content"] and "런큐브를 권고" in fixed[2]["content"]


def test_a_total_written_two_ways_takes_the_changed_part_s_value():
    """client-proposal-4 r2: the rewritten 견적 says one 공급가액, 요청 사항 still another."""
    parts = [
        {"heading": "견적: 범위 조정 반영", "content": (
            "〔표〕와 같이 조정된 공급가액은 45,000,000원으로, 고객 측에서 명시한 VAT 별도 예산 "
            "상한(4,500만원)과 정확히 일치합니다.")},
        {"heading": "요청 사항", "content": (
            "본 제안서의 범위 조정(25개 매장 연동, 모바일 앱 제외, 2027-02-12 오픈)과 예산 초과 "
            "7,200,000원(공급가액 52,200,000원 vs 예산 45,000,000원)에 대해, 예산 상한 상향 중 "
            "하나를 "
            "결정해 주시기 바랍니다.")},
    ]
    [c] = consistency.conflicts(parts, changed={0})
    assert c.key == "·공급가액·원" and c.want == "45,000,000원"
    assert c.off == [(1, parts[1]["content"], "52,200,000원")]


async def test_the_total_conflict_is_repaired_once_and_read_again():
    parts = [
        {"heading": "경력 사항", "content": "총 경력은 6년 10개월입니다."},
        {"heading": "경력 요약", "content": "총 경력 7년 5개월 동안 콘텐츠와 그로스 마케팅을 "
                                        "맡았습니다."},
    ]
    check = consistency.check(changed={0}, instruction="입사는 2019년 4월이었어.")
    found, _ = await check(verify.Doc(parts))
    assert [f.code for f in found] == ["cross_section"] and found[0].section == 1
    assert found[0].still("총 경력 7년 5개월 동안") and not found[0].still("총 경력 6년 10개월 "
                                                                   "동안")


def test_the_start_month_the_follow_up_gave_is_not_a_blank():
    summary = ("총 경력을 계산하기 위해서는 '19년 봄'의 정확한 시작 월이 필요합니다. 아래 [경력 "
               "시작 월]을 "
               "확인해 주시면 해당 값을 계산해 넣겠습니다.")
    found = report_checks.answerable_blanks(
        [{"heading": "경력 요약", "content": summary}],
        "추가 요청: 입사는 2019년 4월이었어. 총 경력 맞는지 다시 봐 줘.")
    assert [b for _, _, b, _ in found] == ["[경력 시작 월]"]


async def test_the_pre_pass_runs_again_with_the_follow_up_and_its_blocks_reach_the_rewrite(
        monkeypatch):
    prompts: list[str] = []
    no_hr = {"HR 연동": "없음"}
    weighted = {"criteria": [{"name": "비용", "weight": 50, "kind": "cost"},
                             {"name": "모바일 앱", "weight": 30, "kind": "yesno"},
                             {"name": "HR 연동", "weight": 20, "kind": "yesno"}],
                "musts": [{"name": "SCORM", "kind": "yesno"}],
                "options": [{"name": "런큐브", "musts": {"SCORM": "있음"},
                             "values": {"비용": 18000000, "모바일 앱": "있음", **no_hr}},
                            {"name": "에듀노트", "musts": {"SCORM": "있음"},
                             "values": {"비용": 17280000, "모바일 앱": "없음", **no_hr}}]}

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        prompts.append(messages[0]["content"])
        if "점수 계산과 필수 조건" in messages[0]["content"]:
            return json.dumps(weighted, ensure_ascii=False), {}
        return "{}", {}

    monkeypatch.setattr(report, "_complete", complete)
    material = report._request_material(LMS_REQUEST)
    pre = await consistency.settle_again("comparison", LMS_REQUEST, LMS_FOLLOW_UP, material,
                                         model="m", api_key="k")
    assert any("추가 요청: " + LMS_FOLLOW_UP in p for p in prompts if "점수 계산과" in p)
    blocks = consistency.blocks_for_rewrite(pre)
    assert any(b.startswith("# 점수 계산(코드)") and "런큐브" in b for b in blocks)
    assert blocks[-1] == internal_words.WRITER_NOTE
    assert consistency._leader(pre) == "런큐브"


def test_the_revision_widens_settles_and_aligns():
    import inspect

    from app.routers import sessions

    source = inspect.getsource(sessions._revise_document)
    assert source.find("consistency.dependents(") < source.find("consistency.settle_again(")
    assert source.find("consistency.settle_again(") < source.find("report_service.rewrite_section(")
    assert source.find("report_service.rewrite_section(") < source.find("consistency.align(")
    assert "internal_words.scrub(body)" in source


# ── client proposal: MM is never written as headcount ───────────────────

STAFF_PLAN = ("[투입 계획 — 내부 메모]\n- PM 1명 1.0MM, AI 엔지니어 1명 3.0MM, 데이터 엔지니어 1명 "
              "1.5MM\n"
              "- GPU 서버 임차: 월 1,200,000원 × 3개월")
#: client-proposal-1 r1 and r2.
STAFF_R1 = ("본 사업에는 PM 1명(1.0MM), AI 엔지니어 3명(3.0MM), 데이터 엔지니어 1.5명(1.5MM)이 "
            "투입되며, "
            "GPU 서버 임차를 수행사가 제공합니다(표 3).")
STAFF_R1_TABLE = ("| 역할 | 인원 | 투입 기간(MM) | 담당 업무 |\n| --- | --- | --- | --- |\n"
                  "| AI 엔지니어 | 3명 | 3.0 | 요약·분류 모델 개발 |\n"
                  "| 데이터 엔지니어 | 1.5명 | 1.5 | 데이터 정제 |")
STAFF_R2 = "본 사업에는 프로젝트 매니저 1명(AI 엔지니어 3명)과 데이터 엔지니어 1.5명이 참여합니다."


def test_the_plan_s_people_and_mm_are_read_apart():
    assert report_checks.staffing(STAFF_PLAN) == {
        "PM": {"people": 1.0, "mm": 1.0}, "AI 엔지니어": {"people": 1.0, "mm": 3.0},
        "데이터 엔지니어": {"people": 1.0, "mm": 1.5}}
    # quote-calc's reading fills a role the text does not pair.
    more = report_checks.staffing("", {"items": [{"name": "퍼블리셔", "mm": 4, "people": 2}]})
    assert more == {"퍼블리셔": {"people": 2.0, "mm": 4.0}}


async def test_mm_written_as_headcount_is_found_and_repaired_once():
    roles = report_checks.staffing(STAFF_PLAN)
    sections = [{"heading": "수행 조직", "content": STAFF_R1 + "\n\n" + STAFF_R1_TABLE},
                {"heading": "투입 인력", "content": STAFF_R2}]
    pre = report_checks.Prepass(format_id="client_proposal", staff=roles)
    doc = verify.Doc(sections, STAFF_PLAN)
    found, _ = await verify.find(doc, report_checks.checks(pre))
    staff = [f for f in found if f.code == "staff_headcount"]
    assert len(staff) == 4
    assert any("자료는 AI 엔지니어 1명 × 3MM" in f.why for f in staff)
    fixes = {
        STAFF_R1: STAFF_R1.replace("AI 엔지니어 3명", "AI 엔지니어 1명").replace(
            "데이터 엔지니어 1.5명", "데이터 엔지니어 1명"),
        "| AI 엔지니어 | 3명 | 3.0 | 요약·분류 모델 개발 "
        "|": "| AI 엔지니어 | 1명 | 3.0 | 요약·분류 모델 개발 |",
        "| 데이터 엔지니어 | 1.5명 | 1.5 | 데이터 정제 "
        "|": "| 데이터 엔지니어 | 1명 | 1.5 | 데이터 정제 |",
        STAFF_R2: "본 사업에는 프로젝트 매니저 1명, AI 엔지니어 1명(3.0MM)과 데이터 엔지니어 "
                  "1명(1.5MM)이 참여합니다.",
    }

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        items = json.loads(messages[0]["content"].rsplit("항목:\n", 1)[1])
        return json.dumps([fixes.get(i["sentence"], i["sentence"]) for i in items],
                          ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}

    fixed, left, _ = await verify.verify_and_repair(
        doc, report_checks.checks(pre), complete=complete, model="m", api_key="k")
    assert [f for f in left if f.code == "staff_headcount"] == []
    assert "AI 엔지니어 3명" not in fixed[0]["content"] and "1.5명" not in fixed[1]["content"]
    # The quote table's 「3 | MM」 is effort, not people, and is left alone.
    quote_row = "| AI 엔지니어 | 3 | MM | 8,000,000 | 24,000,000 | |"
    quote = ("| 항목 | 수량 | 단위 | 단가 | 금액 | 비고 |\n|---|---|---|---|---|---|\n"
             + quote_row)
    found, _ = await report_checks.staff_counts(
        verify.Doc([{"heading": "견적", "content": quote}]), roles)
    assert found == []
