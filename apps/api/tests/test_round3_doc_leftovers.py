"""Document and chat guards read against plain answers, with evaluation strings."""

from __future__ import annotations

import datetime as dt

import pytest

from app.routers import sessions
from app.services import (
    agent,
    calc_check,
    doc_formats,
    internal_words,
    report,
    report_checks,
    skill_scripts,
    value_guard,
    verify,
)

# ── the weekly meeting memo ──────────────────────────────────────────────────────

A01_MEMO = (
    "아래 메모로 회의록 작성해 줘. 결정 사항과 할 일(담당·기한)이 한눈에 보이게.\n\n"
    "[메모] 10/8(목) 14시 본관 3층 소회의실, 교육혁신팀 주간회의\n"
    "참석: 박정민 팀장, 이수진 차장, 김도윤 대리, 최하늘 주임 (정현우 과장 출장으로 불참)\n"
    "1. 2학기 AI 활용 교수법 워크숍 — 신청 42명(정원 40). 이수진: 대기자 2명은 온라인 동시 송출로 "
    "수용하자고 제안. 팀장 OK. 강사 섭외 완료(외부 강사 1명, 강사료 50만원). 장소 미래관 201호, "
    "10/22(목) 15:00.\n"
    "2. LMS 개편 — 업체(한결소프트) 1차 시안 10/15 도착 예정. 김도윤: 모바일 화면 검수 체크리스트 "
    "만들어서 다음 회의 때 공유하겠다. 학생 테스트 그룹 모집은 최하늘이 학생회 통해 진행, 10명 목표.\n"  # noqa: E501 — verbatim data
    "3. 우수 강의 사례집 — 원고 12편 중 9편 접수. 미제출 3편은 교수님들께 10/16까지 재요청(최하늘). "  # noqa: E501 — verbatim data
    "디자인 업체 견적 2곳: A사 380만원, B사 420만원. B사가 납기 1주 빠름. 결론 못 냄 → 정현우 과장 "
    "복귀 후 다음 회의에서 결정.\n"
    "4. 기타: 재무팀에서 10/30까지 11월 예산 집행계획 제출 요청 옴. 팀장이 각자 담당 사업 "
    "집행계획을 10/23까지 본인에게 달라고 함.\n"
    "다음 회의 10/15(목) 14시 같은 장소"
)

#: one writer's action table: 「미정」 with the reason in 비고
A01_27B = (
    "| 번호 | 할 일 | 담당 | 기한 | 비고 |\n|------|-------|------|------|------|\n"
    "| 1 | 모바일 화면 검수 체크리스트 작성 및 공유 | 김도윤 | 2026-10-15(목) | 다음 회의 때 공유 |\n"  # noqa: E501 — verbatim data
    "| 2 | 학생 테스트 그룹 모집(학생회 통해, 10명 목표) | 최하늘 | 미정 | 회의에서 기한을 정하지 않음 |\n"  # noqa: E501 — verbatim data
    "| 3 | 미제출 원고 3편 재요청(교수님들께) | 최하늘 | 2026-10-16(금) | 원고 12편 중 9편 접수 상태 |\n"  # noqa: E501 — verbatim data
)
#: another writer's, with 「출처: 회의」 in every 비고 and no reason on the 미정 row
A01_FLASH = (
    "| 할 일 | 담당 | 기한 | 비고 |\n|------|------|------|------|\n"
    "| 모바일 화면 검수 체크리스트 작성 및 공유 | 김도윤 대리 | 2026-10-15(목) | 출처: 회의 |\n"
    "| 학생 테스트 그룹 모집(학생회 경유) | 최하늘 주임 | 미정 | 10명 목표, 출처: 회의 |\n"
    "| 11월 예산 집행계획 제출 | 미정 | 2026-10-23(금) | 각자 담당 사업, 팀장 제출 |\n"
)


async def _minutes_dates() -> list[dict]:
    async def offline(*_a, **_k):
        raise RuntimeError("offline")

    pre = await report_checks.prepare("minutes", A01_MEMO, A01_MEMO, complete=offline,
                                      model="m", api_key="", today=dt.date(2026, 10, 7))
    return pre.dates


def _repaired(table: str, dates: list[dict]) -> str:
    sections = [{"heading": "할 일", "content": table}]
    sections, _ = report_checks.minutes_columns(sections, dates)
    sections, _ = report_checks.minutes_one_source(sections, dates)
    sections, _ = report_checks.minutes_deferred(sections)
    sections, _ = report_checks.minutes_due_fill(sections, dates)
    return sections[0]["content"]


@pytest.mark.asyncio
async def test_a_due_the_meeting_left_open_stays_open_and_no_row_borrows_another_rows_date():
    dates = await _minutes_dates()
    said = next(d for d in dates if d["text"].startswith("다음 회의 때"))
    assert said["date"] == "2026-10-15"
    out = _repaired(A01_27B, dates)
    # the writer's 「미정」 with its reason: not the checklist's 「다음 회의 때」
    assert "| 최하늘 | 미정 | 회의에서 기한을 정하지 않음 |" in out
    assert "원문 「다음 회의 때」" not in out
    flash = _repaired(A01_FLASH, dates)
    # no reason in 비고, yet the date's sentence is the checklist's, not the test group's
    assert "| 최하늘 주임 | 미정 |" in flash and "다음 회의 때" not in flash


@pytest.mark.asyncio
async def test_a_one_meeting_action_table_has_no_source_column_saying_meeting_on_every_row():
    dates = await _minutes_dates()
    out = _repaired(A01_27B, dates)
    assert out.splitlines()[0] == "| 번호 | 할 일 | 담당 | 기한 | 비고 |"
    flash = _repaired(A01_FLASH, dates)
    assert "출처" not in flash
    assert "| 김도윤 대리 | 2026-10-15(목) | - |" in flash
    assert "| 10명 목표 |" in flash
    # a 출처 column of 「회의」 the writer drew itself goes too
    drawn = ("| 할 일 | 담당 | 기한 | 출처 | 비고 |\n|---|---|---|---|---|\n"
             "| 미제출 원고 3편 재요청 | 최하늘 주임 | 2026-10-16(금) | 회의 | - |\n"
             "| 학생 테스트 그룹 모집 | 최하늘 주임 | 미정 | 회의 | 10명 목표 |\n")
    gone, dropped = report_checks.minutes_one_source([{"heading": "할 일", "content": drawn}],
                                                     dates)
    assert dropped == 1 and gone[0]["content"].splitlines()[0] == "| 할 일 | 담당 | 기한 | 비고 |"
    assert "| 최하늘 주임 | 미정 | 10명 목표 |" in gone[0]["content"]


def test_a_group_owner_is_no_name_to_blank():
    table = ("| 할 일 | 담당 | 기한 |\n|---|---|---|\n"
             "| 담당 사업별 11월 예산 집행계획 작성·팀장 제출 | 각 담당자 | 2026-10-23(금) |\n")
    out, codes = report_checks.minutes_grounding([{"heading": "할 일", "content": table}], A01_MEMO)
    assert "| 각 담당자 |" in out[0]["content"] and "minutes_owner" not in codes


# ── the 공문 and the result report ───────────────────────────────────────────────────

A03_T1 = (
    "각 학과에 보낼 협조 요청 공문을 써 줘.\n\n"
    "- 제목 취지: 2027학년도 실험실습 기자재 수요조사 협조 요청\n"
    "- 발신: 한빛대학교 기획처장 / 수신: 각 학과(부)장\n"
    "- 제출 기한: 2026. 11. 6.(금) 18:00까지\n"
    "- 붙임: 실험실습 기자재 수요조사서 1부"
)
A03_T2 = (
    "아래 자료로 행사 결과보고서 써 줘. 총장님까지 올라가는 내부 보고야.\n\n"
    "- 만족도 조사(응답 141명): 전반 만족 4.5/5, 멘토링 4.6, 장소·시설 4.1, 식음료 3.8. 주관식에서 "
    "'밤샘 중 휴식 공간 부족' 23건, '와이파이 끊김' 17건 지적\n"
    "- 문제: 2일차 새벽 2~4시 미래관 2층 와이파이 장애(AP 2대 과부하), 식음료 야식 부족"
)


def test_a_deadline_the_letter_gives_is_not_its_issue_day():
    fmt = doc_formats.BY_ID["official"]
    block = doc_formats.title_block(fmt, {"fields": {
        "수신": "각 학과(부)장", "발신": "한빛대학교 기획처장", "시행일": "2026. 11. 6."}}, A03_T1)
    assert dict(block["fields"])["시행일"] == ""
    assert dict(block["fields"])["발신"] == "한빛대학교 기획처장"
    # a 시행일 the request names as such is kept
    named = doc_formats.title_block(fmt, {"fields": {"시행일": "2026. 11. 2."}},
                                    A03_T1 + "\n- 시행일: 2026. 11. 2.")
    assert dict(named["fields"])["시행일"] == "2026. 11. 2."


def test_an_overview_table_written_as_labelled_items_is_a_table_again():
    written = ("- 구분: 일시\n  - 내용: 2026. 10. 2.~10. 3.(무박 2일)\n"
               "- 구분: 장소\n  - 내용: 미래관 1~2층\n"
               "- 구분: 주관\n  - 내용: SW중심대학사업단")
    out = doc_formats.gaejo_markdown(written)
    assert out.splitlines()[:3] == ["| 구분 | 내용 |", "|---|---|",
                                    "| 일시 | 2026. 10. 2.~10. 3.(무박 2일) |"]
    assert "| 주관 | SW중심대학사업단 |" in out and "- 구분" not in out
    # a nested list whose labels differ stays a list
    listed = "- 신청: 61개 팀\n  - 서류 심사: 48개 팀 172명\n- 멘토: 12명\n  - 교수: 5명"
    assert "|" not in doc_formats.gaejo_markdown(listed)


@pytest.mark.asyncio
async def test_a_remark_count_stays_with_its_remark():
    flash = "- 문제: 야간 식음료 부족으로 인한 불만 23건 접수"
    nested = ("- 문제점 2: 식음료 야식 부족\n"
              "  - 근거: 식음료 만족도 3.8/5, 주관식 답변에서 '밤샘 중 휴식 공간 부족' 23건, "
              "'와이파이 끊김' 17건 지적")
    right = "- 휴식 공간 부족: 밤샘 중 쉴 곳 부족(주관식 23건)\n- 와이파이 장애 17건"
    doc = verify.Doc([{"heading": "성과 및 문제점", "content": f"{flash}\n\n{nested}\n\n{right}"}],
                     A03_T2)
    found, _ = await report_checks.count_labels(doc)
    by_line = {f.sentence: f.why for f in found}
    assert "밤샘 중 휴식 공간 부족" in by_line[flash.strip()]
    nested_why = next(w for s, w in by_line.items() if s.startswith("- 근거"))
    assert "23건" in nested_why and "17건" in nested_why
    assert not any("쉴 곳" in s or "와이파이 장애 17건" in s for s in by_line)


def test_a_one_part_plan_goes_straight_to_writing():
    straight = sessions.outline_rules.nothing_to_approve
    ask = "협조 요청 공문 써 줘"
    assert straight({"title": "협조 요청", "sections": ["본문"]}, ask)
    assert not straight({"sections": ["개요", "결과"]}, ask)
    assert not straight({"sections": ["본문"], "figures": [{"caption": "x"}]}, ask)
    assert not straight({"sections": ["본문"], "frame": True}, ask)


# ── a budget limit judged on the computed VAT-inclusive totals ──────────────────────

A04_T2 = (
    "[견적 1] 가온정보통신 단가 1,320,000원(부가세 별도) × 30대, 무상 보증 3년\n"
    "[견적 2] 다솜시스템 단가 1,390,000원(부가세 포함) × 30대, 무상 보증 2년\n"
    "[견적 3] 누리컴 단가 1,480,000원(부가세 별도) × 30대, 무상 보증 3년\n"
    "조건: 예산 4,800만원(부가세 포함), 보증 3년 필수"
)


def test_a_budget_limit_with_no_reading_is_judged_on_the_computed_total():
    data = {"musts": [{"name": "총액(부가세 포함)", "kind": "max", "limit": 48000000, "unit": "원"},
                      {"name": "무상 보증", "kind": "min", "limit": 3, "unit": "년"}],
            "options": [{"name": "가온정보통신", "musts": {"무상 보증": 3}},
                        {"name": "다솜시스템", "musts": {"무상 보증": 2}},
                        {"name": "누리컴", "musts": {"무상 보증": 3}}]}
    numbers = report_checks._material_numbers(A04_T2)
    unread = report_checks.must_status(data, numbers)
    assert unread["options"]["가온정보통신"]["status"] == "조건 미확인"
    costs = {"budget": {"amount": 48000000, "vat": "incl", "compared": {
        "가온정보통신": 43560000, "다솜시스템": 43350000, "누리컴": 46563000}}}
    judged = report_checks.must_status(data, numbers, costs=costs)["options"]
    assert judged["가온정보통신"]["status"] == "충족" and judged["누리컴"]["status"] == "충족"
    assert judged["다솜시스템"]["status"] == "탈락"
    over = {"budget": {**costs["budget"], "compared": {**costs["budget"]["compared"],
                                                       "누리컴": 49000000}}}
    over_status = report_checks.must_status(data, numbers, costs=over)["options"]["누리컴"]
    assert over_status["status"] == "탈락"


# ── values a code turn printed for a group, and a column name ──────────────────────────

PRINTED = (
    "=== 전체 ===\n전반만족 평균: 3.78\n시설 평균: 3.68\n"
    "=== 단과대학별 전반만족·시설 평균 ===\n"
    "         n  전반만족  시설\n예술대학   18  3.39  2.888889\n공과대학   32  3.59  3.69\n"
)


def test_a_group_value_the_code_printed_is_not_swapped_for_the_overall_one():
    answer = ("전체 중 예술대학의 전반만족이 3.39점으로 가장 낮고, 예술대학 시설 평균 2.89점은 "
              "전체 시설 평균 3.68점보다 0.79 낮습니다.")
    stated = calc_check.printed_values([PRINTED])
    # read without the printed lines, the group's 3.39 becomes the overall 3.78
    swapped, _ = calc_check.check_markdown(answer, stated)
    assert "전반만족이 3.78점으로" in swapped
    kept, fixes = agent.code_checked(answer, [f"stdout:\n{PRINTED}"])
    assert kept == answer and not fixes
    # a gap beside the overall value is no slip for it
    gap = "전체 시설 평균 대비 차이는 −0.79입니다."
    assert calc_check.check_markdown(gap, stated)[0] == gap
    # the overall value written wrong is still set right
    wrong, fixes = agent.code_checked("전체 전반만족 평균은 3.87점입니다.", [f"stdout:\n{PRINTED}"])
    assert "3.78점" in wrong and fixes


def test_a_column_name_gets_no_check_mark():
    before = ("| 부서 | 12월말 예상 집행액(원) |\n|---|---:|\n| 교육혁신팀 | 12,800,000 |\n\n"
              "12월말까지 남은 집행이 걱정됩니다.")
    after = before.replace("12월말 예상", "[시기] 예상", 1).replace("12월말까지", "[시기]까지")
    settled = value_guard.settle(before, after, value_guard.Evidence.of(["9월말 집행 현황"]))
    assert "| 12월말 예상 집행액(원) |" in settled.text
    assert "12월말(확인 필요)" not in settled.text.split("\n")[0]


# ── a source's own names, a clause before 「교수」, a section that ran out ───────────────

def test_a_school_a_source_names_by_its_short_name_is_kept():
    trace = skill_scripts.module("grounding-check", "trace")
    source = ("[AI돋보기] 'AI로 시험보다가 정학' — 고려대는 계열별 차등 적용을 검토하며 AI 사용 시 "
              "출처를 표기하면... 아주대학교에서 ‘아주대학교 생성형 AI 교수·학습 활용 가이드라인’을 "  # noqa: E501 — verbatim data
              "신설했다.")
    row = "| 고려대학교 | 확인 필요 | 계열별 차등 적용 검토 [2] | AI 사용 시 출처 표기 허용 방향 모색 [2] |"  # noqa: E501 — verbatim data
    clause = ("아주대학교는 ‘아주대학교 생성형 AI 교수·학습 활용 가이드라인’을 신설하여 교수·학습 "
              "영역에서의 AI 활용 기준을 공식화했습니다 [1].")
    found = trace.check(f"{row}\n{clause}", source)
    assert not [f for f in found if f["kind"] in ("org", "person")]
    # a school no source names is still blanked
    made = trace.check("연세대와 서울대학교 등 주요 대학도 발표했습니다.", source)
    assert [f for f in made if f["kind"] == "org"]


def test_a_section_cut_off_mid_word_ends_at_its_last_sentence():
    cut = ("행정안전부 주도가 부처별 추진으로 전환되면서 개별 기관의 자체 재원 부족이 시스템 전환 "
           "지연으로 이어진 바 있으므로, SaaS 기업은 특정 부처의 사업 중단 여부가 자사 매출에 즉각 "
           "반영되는 리스크를 관리해야 합니다[17]. 이러한 부처별 추")
    assert report.cut_unfinished_tail(cut).endswith("관리해야 합니다[17].")
    for whole in ("두 축으로 요약됩니다.", "| 시기 | 주체 |\n|---|---|\n| 2023년 | 부처별 |",
                  "- 휴식 공간 별도 확보", "세부 항목은 다음과 같다(표 2)"):
        assert report.cut_unfinished_tail(whole) == whole


# ── a market-entry review searches even with the company's memo ──────────────────────

A10_T1 = (
    "「국내 클라우드 협업툴 시장」에 진입할 만한지 검토 보고서 써 줘. 경영회의 안건 자료야.\n\n"
    "우리 회사 자료(한빛소프트, 가상 회사 — 아래는 사내 자료임):\n"
    "- 업력 12년, 직원 85명, 주력은 중견기업용 전자결재·그룹웨어(설치형), 고객사 약 340곳\n"
    "- 작년 매출 162억원, 그중 유지보수 매출 비중 58%\n"
    "- 고객사 설문(2026년 8월, 응답 112곳): \"클라우드 메신저·공동편집 도입 계획 있음\" 47곳, "
    "\"이미 다른 협업툴 사용 중\" 38곳\n"
    "- 검토안: 기존 그룹웨어에 붙는 SaaS형 협업툴(메신저·화상·공동편집) 2027년 하반기 출시\n\n"
    "알고 싶은 것: 국내 시장 규모와 성장 전망(공개 자료 기준, 추정이면 가정 명시), 고객군 나누기, "
    "주요 경쟁사(네이버웍스, 카카오워크, 잔디, 플로우, 두레이, 마이크로소프트 팀즈, 슬랙 등)의 위치, "  # noqa: E501 — verbatim data
    "산업 환경(규제·공공 클라우드 등), 진입 여부 의견."
)


def test_a_market_entry_review_with_a_company_memo_still_searches():
    assert not report.material_complete(A10_T1, "biz")
    # the company memo alone, nothing asked from outside: written from it
    memo_only = A10_T1.split("\n\n알고 싶은 것")[0] + "\n\n알고 싶은 것: 진입 여부 의견."
    assert report.material_complete(memo_only, "biz")


# ── a notice carries no check report ───────────────────────────────────────────────────

def test_a_notice_ends_without_its_check_report():
    flash = ("**문의**\n- 경영지원팀 박소연 매니저 (내선 2050)\n\n감사합니다.\n\n---\n"
             "날짜 네 개(10. 21.(수)·10. 23.(금)·10. 25.(일)·10. 26.(월))는 드린 자료의 요일과 실제 "  # noqa: E501 — verbatim data
             "달력이 일치합니다. 점검 결과 자료에 없는 값은 없고 채울 빈칸도 없습니다.")
    out = internal_words.strip_work_notes(flash, request="네트워크 공사 공지 써 줘")
    assert out.endswith("감사합니다.")
    eol = ("버튼: 데이터 내보내기 / 한빛워크로 옮기기\n\n---\n\n"
           "일정 점검: 순서와 요일 모두 맞고 차단 항목 0건입니다. 다만 종료일과 데이터 삭제일이 같은 "  # noqa: E501 — verbatim data
           "날(2027. 3. 31.)이라 종료 뒤에 내보낼 기간이 없습니다.")
    kept = internal_words.strip_work_notes(eol, request="서비스 종료 안내 써 줘")
    assert "차단 항목" not in kept and "종료 뒤에 내보낼 기간이 없습니다" in kept
    heading = ("[자세히 보기] 버튼 텍스트: 「종료 일정과 내보내기 방법」\n\n---\n\n**점검 결과**\n\n"  # noqa: E501 — verbatim data
               "문의처: support@hanbitsoft.example (평일 10~17시)")
    stripped, block = internal_words.strip_check_block(heading)
    assert "점검 결과" not in stripped and block == "**점검 결과**"
    assert stripped.endswith("문의처: support@hanbitsoft.example (평일 10~17시)")
    # a report's own 「점검 결과」 section is content
    own = "## 점검 결과\n\n- 소화기 3개 교체 필요\n- 비상구 표지 1곳 파손"
    assert internal_words.strip_check_block(own)[0] == own


# ── a source link keeps its article id ──────────────────────────────────────────────

def test_a_source_listed_without_its_article_id_takes_the_whole_address():
    bare = "https://www.ftoday.co.kr/news/articleView.html"
    whole = bare + "?idxno=355223"
    sources = ["https://example.org/a", bare]
    assert agent._source_number(whole, sources) == 2 and sources[1] == whole
    sources = ["https://example.org/a", bare]
    linked, cited = agent._link_citations(f"소재지 [[2]]({whole}). 또 [2].", sources)
    assert sources[1] == whole and f"[[2]]({whole})" in linked and cited == [2]
    # a page that is no article viewer keeps both
    board = ["https://example.org/list"]
    assert agent._source_number("https://example.org/list?page=2", board) == 2


# ── a copy answer carries no account of the writer's own edits and checks ───────────

def test_copy_carries_no_report_of_the_writers_own_edits_or_checks():
    request = "한빛워크 무료 체험 캠페인 광고 소재를 채널별로 써 줘."
    top = ("링크드인3 도입만 권장 150자를 12자 초과해서 줄였습니다.\n\n---\n\n"
           "# 한빛워크 무료 체험 캠페인 — 채널별 광고 소재")
    out = internal_words.strip_work_notes(top, request=request)
    assert out.startswith("# 한빛워크 무료 체험 캠페인")
    tail = ("- 기준: CTR 10% 이상 우위면 승자.\n\n"
            "최종 검수에서 차단(block) 표현은 0건(확인 필요)입니다.")
    out = internal_words.strip_work_notes(tail, request=request)
    assert out == "- 기준: CTR 10% 이상 우위면 승자."
    # content that cut something, and an answer to an edit request, stay
    kept = "예산을 1,200만 원으로 줄였습니다."
    assert internal_words.strip_work_notes(kept, request=request) == kept
    asked = "제목을 30자 안으로 줄였습니다."
    assert internal_words.strip_work_notes(asked, request="제목 30자로 줄여 줘") == asked
