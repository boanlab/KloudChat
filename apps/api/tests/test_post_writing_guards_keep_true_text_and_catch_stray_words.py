"""Post-writing guards that keep correct text and catch foreign words, tool words and markup."""

from __future__ import annotations

import asyncio
import datetime as dt

from app.services import (
    check_skills,
    doc_formats,
    internal_words,
    report,
    skill_scripts,
    stray_han,
    units,
)
from app.services.tools import builtin
from app.services.value_guard import MARK, Evidence, settle, supported

TODAY = dt.date(2026, 10, 12)

# ── 회의록: trims that cut the decision and broke the 「첫째·둘째·셋째」 run ──────────────

MEETING_MEMO = (
    "[메모] 10/8(목) 14시 본관 3층 소회의실, 교육혁신팀 주간회의\n"
    "참석: 박정민 팀장, 이수진 차장, 김도윤 대리, 최하늘 주임 (정현우 과장 출장으로 불참)\n"
    "1. 2학기 AI 활용 교수법 워크숍 — 신청 42명(정원 40). 이수진: 대기자 2명은 온라인 동시 송출로 "
    "수용하자고 제안. 팀장 OK. 강사 섭외 완료(외부 강사 1명, 강사료 50만원). 장소 미래관 201호, "
    "10/22(목) 15:00.\n"
    "3. 우수 강의 사례집 — 디자인 업체 견적 2곳: A사 380만원, B사 420만원. B사가 납기 1주 빠름.\n"
    "4. 기타: 재무팀에서 10/30까지 11월 예산 집행계획 제출 요청 옴. 팀장이 각자 담당 사업 "
    "집행계획을 10/23까지 본인에게 달라고 함.\n"
    "다음 회의 10/15(목) 14시 같은 장소"
)

MEETING_DECISIONS = (
    "회의에서 최종적으로 확정된 사안은 AI 활용 교수법 워크숍의 대기자 수용 방식입니다. 정원 40명을 "
    "초과한 신청 42명 가운데 대기자 2명을 온라인 동시 송출로 참여를 허용하기로 했으며, 이 건은 "
    "이수진 차장의 제안과 박정민 팀장의 승인으로 결정되었습니다.\n\n"
    "| 항목 | 내용 | 비고 |\n| --- | --- | --- |\n"
    "| 워크숍 대기자 수용 | 대기자 2명 온라인 동시 송출로 참여 허용 | 정원 40명 초과(신청 42명) |\n"
    "| 워크숍 일시·장소 | 10/22(목) 15:00, 미래관 201호 | - |\n"
)

MEETING_NEXT_AGENDA = (
    "다음 회의에서 다룰 예정인 안건은 크게 세 가지입니다. 첫째, 한결소프트의 LMS 개편 1차 "
    "시안(10/15 도착 예정)을 확인하고, 김도윤 대리가 가져온 모바일 화면 검수 체크리스트를 기준으로 "
    "검수 방향을 논의합니다. 둘째, 정현우 과장 복귀 후 사례집 디자인 업체(A사, B사) 선정 기준을 "
    "확정합니다. 셋째, 11월 예산 집행계획과 관련해서 각 담당 사업별 내역이 10/23(금)까지 정리되어 "
    "박정민 팀장에게 제출되는지가 10/15(목) 회의의 시점에서는 아직 미정이며, 사업별 담당자 배분은 "
    "확인이 필요합니다.\n\n"
    "| 준비 사항 | 관련 안건 | 참고 |\n| --- | --- | --- |\n"
    "| LMS 1차 시안 | 한결소프트 | 10/15(목) 도착 예정 |\n"
    "| 각 담당 사업별 집행계획 | 11월 예산 집행계획 | 10/23(금)까지 박정민 팀장에게 제출 예정 |\n"
)


def test_a_decision_sentence_naming_who_decided_is_no_table_echo():
    out = report.trim_table_echo(MEETING_DECISIONS)
    assert "이수진 차장의 제안과 박정민 팀장의 승인으로 결정되었습니다" in out
    # a pure read-back of the row still goes
    echo = ("확정된 사안은 대기자 수용 방식입니다. 워크숍은 10/22(목) 15:00, 미래관 "
            "201호입니다.\n\n"
            + MEETING_DECISIONS.split("\n\n")[1])
    assert "미래관 201호입니다" not in report.trim_table_echo(echo)


def test_a_counted_run_keeps_every_item():
    out = report.trim_table_echo(MEETING_NEXT_AGENDA)
    for item in ("첫째, 한결소프트", "둘째, 정현우", "셋째, 11월 예산"):
        assert item in out
    trimmed, cut = report.trim_restatements(MEETING_NEXT_AGENDA,
                                            [MEETING_MEMO.replace("[메모]", "")])
    assert not [c for c in cut if c.startswith(("첫째", "둘째", "셋째"))]
    assert "셋째, 11월 예산" in trimmed


def test_a_sentence_the_next_one_leans_on_stays():
    body = ("AI 활용 교수법 워크숍은 정원 40명을 넘긴 신청 42명 중 대기자 2명을 온라인 동시 송출로 "
            "참여시키기로 한 것이 진행의 핵심입니다. 나머지 40명은 미래관 201호에서 오프라인으로, "
            "대기자 "
            "2명은 온라인으로 각각 참여하며, 10/22(목) 15:00에 동시에 진행하는 방식입니다.\n")
    earlier = ["정원 40명을 초과한 신청 42명 가운데 대기자 2명을 온라인 동시 송출로 참여를 "
               "허용하기로 했습니다. 장소는 미래관 201호, 10/22(목) 15:00입니다."]
    trimmed, _ = report.trim_restatements(body, earlier)
    assert trimmed.startswith("AI 활용 교수법 워크숍은")


def test_a_process_told_in_the_passive_is_no_requirement_to_bring():
    trace = skill_scripts.module("grounding-check", "trace")
    text = ("예산 집행계획은 제출 기한(10/23)은 정해졌지만, 각 담당자가 제출할 사업별 내역은 아직 "
            "특정되지 "
            "않았습니다. 재무팀 마감(10/30) 전 팀 내부에서 1주일 앞서(10/23) 통합을 마쳐야 하므로, "
            "사업별 분할 내역이 먼저 정리되어야 통합 제출이 가능합니다.\n"
            "※ 10/23(금) 팀장 제출이 10/30(금) 재무팀 마감의 전제입니다: 11월 예산 집행계획은 각 "
            "담당 "
            "사업 내역이 10/23(금)까지 박정민 팀장에게 모인 뒤 재무팀 10/30(금) 마감에 맞춰 "
            "제출됩니다.")
    found = trace.check(text, MEETING_MEMO, [], {"requirements": True})
    assert not [f for f in found if f["kind"] == "requirement"]
    # an ask of the reader still is one
    asked = trace.check("당일 신분증을 꼭 지참해 주세요.", MEETING_MEMO, [], {"requirements": True})
    assert [f for f in asked if f["kind"] == "requirement"]


def test_a_due_word_about_a_due_is_not_blanked():
    table = ("| 구분 | 비용 | 납기 |\n|------|------|------|\n"
             "| A사 | 380만원 | 기준 |\n| B사 | 420만원 | A사 대비 1주 빠름 |")
    request = "우수 강의 사례집 디자인 업체 결정(A사 380만원 vs B사 420만원·납기 1주 빠름)"
    out = report._unsourced_owner_dates(table, request)
    assert "| 기준 |" in out and "[기한]" not in out


def test_a_reply_that_gives_no_date_never_fills_the_date_field():
    reply = ("요청문에 적은 자료가 제가 가진 사실의 전부입니다. 자료에 없는 것은 모르니 빈칸으로 "
             "두세요.")
    assert check_skills.goes_on(reply)
    shown = check_skills.labelled({"gap:meeting_date": reply},
                                  [{"id": "gap:meeting_date", "label": "회의 일시"}])
    assert not shown["gap:meeting_date"].startswith("회의 일시:")
    fmt = next(f for f in doc_formats.FORMATS if f.id == "minutes")
    read = doc_formats.head_values(fmt, f"회의 일시: {reply}")
    assert not read.get("일시")
    assert not doc_formats.field_value("일시", reply)
    assert doc_formats.field_value("일시", "10/15(목) 14시")


def test_the_year_of_today_backs_a_month_the_memo_names():
    ev = Evidence.of([MEETING_MEMO], today=TODAY)
    line = "2026년 11월분 예산 집행계획을 2026년 10월 30일까지 재무팀에 제출해야 합니다."
    assert supported("2026년 11월", line, 0, ev, 0)


# ── 견적 비교: totals worked from the quotes, and formulas the fixers broke ─────────────

LAPTOP_QUOTES = (
    "[견적 1] 가온정보통신\n- 단가 1,320,000원(부가세 별도) × 30대\n"
    "[견적 2] 다솜시스템\n- 단가 1,390,000원(부가세 포함) × 30대\n"
    "- 무상 보증 2년(센터 입고), 3년 연장 시 대당 45,000원(부가세 포함)\n"
    "- 납기 2주, 기존 기기 수거는 대당 10,000원(부가세 포함)\n"
    "[견적 3] 누리컴\n- 단가 1,480,000원(부가세 별도) × 30대, 30대 이상 구매 시 총액 5% 할인\n"
    "- 기존 기기 수거 무료, 데이터 삭제 확인서는 별도 대당 5,000원(부가세 별도)\n"
    "조건: 예산 4,800만원(부가세 포함)"
)


def test_a_formula_the_fixers_read_in_part_is_left_whole():
    for line in ("가온: 1,320,000원 × 30 × 1.1 = 43,560,000원",
                 "다솜: 확정 42,000,000원(부가세 포함) + 보증 연장 45,000원 × 30대 = 43,350,000원"):
        assert report.fix_products(line) == line
        assert units.fix_written_arithmetic(line) == line
    # a wrong product still gets its right result
    assert "39,600,000" in report.fix_products("1,320,000원 × 30 = 36,000,000원")


def test_totals_worked_from_the_quotes_are_supported():
    answer = ("누리컴은 1,480,000원×30대=44,400,000원(공급가액)에 5% 할인(2,220,000원)을 적용해 "
              "42,180,000원(공급가액), 여기에 부가세(4,218,000원)를 더하면 46,398,000원(부가세 "
              "포함)이고, "
              "데이터 삭제 확인서 5,000원(부가세 별도)×30대=150,000원(공급가액, 부가세 포함 "
              "165,000원)을 "
              "더하면 총 46,563,000원(부가세 포함)입니다.\n"
              "누리: (1,480,000 × 30 × 0.95 + 5,000 × 30) × 1.1 = 46,563,000원\n"
              "가온 총 비용은 43,560,000원(부가세 포함)입니다.")
    ev = Evidence.of([LAPTOP_QUOTES], answer=answer)
    for value in ("4,218,000원", "46,398,000원", "46,563,000원", "43,560,000원", "165,000원"):
        line = next(x for x in answer.split("\n") if value in x)
        assert supported(value, line, line.index(value), ev, 0), value
    assert not supported("47,000,000원", "총 47,000,000원입니다.", 2, ev, 0)


def test_a_total_one_section_works_out_backs_the_others():
    worked = "누리: (1,480,000 × 30 × 0.95 + 5,000 × 30) × 1.1 = 46,563,000원"
    row = "| 예산 4,800만원(부가세 포함) | 탈락(총 비용 46,563,000원(확인 필요) > 4,800만원) |"
    out = report.unmark_worked([{"heading": "a", "content": row},
                                {"heading": "b", "content": worked}], [LAPTOP_QUOTES])
    assert MARK not in out[0]["content"]
    still = report.unmark_worked([{"heading": "a", "content": "총 47,000,000원(확인 필요)"}],
                                 [LAPTOP_QUOTES])
    assert MARK in still[0]["content"]


# ── names the person listed, plans of the reader's own, dates in result links ───────────

MARKET_SIZE_REQUEST = (
    "알고 싶은 것: 국내 시장 규모와 성장 전망(공개 자료 기준, 추정이면 가정 명시), 고객군 나누기, "
    "주요 경쟁사(네이버웍스, 카카오워크, 잔디, 플로우, 두레이, 마이크로소프트 팀즈, 슬랙 등)의 "
    "위치, "
    "산업 환경(규제·공공 클라우드 등), 진입 여부 의견.\n"
    "- 고객사 설문(2026년 8월, 응답 112곳)"
)


def test_a_count_of_the_names_the_person_listed_is_theirs():
    ev = Evidence.of([MARKET_SIZE_REQUEST], today=TODAY)
    line = ("첫째, 경쟁사 7곳(네이버웍스, 카카오워크, 잔디, 플로우, 두레이, 마이크로소프트 팀즈, "
            "슬랙)의 "
            "가격·기능·고객사 수가 아직 확인되지 않아")
    assert supported("7곳", line, line.index("7곳"), ev, 0)
    assert not supported("9곳", "경쟁사 9곳", 4, ev, 0)


def test_a_month_ahead_in_a_step_the_reader_takes_is_a_plan():
    ev = Evidence.of([MARKET_SIZE_REQUEST], today=TODAY)
    line = "2. 2026년 11월, 직원 85명 내 개발·보안·운영 인력 계획을 확정합니다."
    assert supported("2026년 11월", line, 3, ev, 0)
    assert supported("2026년 10월", "1. 2026년 10월∼11월 경쟁사 가격을 확인합니다.", 3, ev, 0)
    # a month told as someone else's fact is not
    told = "정부는 2026년 12월 공공 클라우드 지침을 개정 발표합니다."
    assert not supported("2026년 12월", told, told.index("2026"), ev, 0)


def test_a_day_in_a_search_results_link_is_evidence():
    searched = ("'한국수자원공사' 검색 결과:\n"
                "[1] 수자원공사, 美 물산업 기관과 협력 국내 기업 실증 기반 확대\n"
                "https://www.fnnews.com/news/202610021506176780\n"
                "[2] 한국수자원공사 테스트베드 협약\nhttps://example.kr/2026/03/15/kwater\n")
    ev = Evidence.of([searched], today=TODAY)
    assert ev.supports_day(2026, 10, 2)
    assert ev.supports_day(2026, 3, 15)
    assert not ev.supports_day(2026, 10, 9)


def test_settle_gives_the_link_dated_day_back_whole():
    searched = "[3] 협약 체결 기사\nhttps://www.fnnews.com/news/202610021506176780\n"
    before = "- 2026-10-02 WEFTEC 2026에서 글로벌 테스트베드 협약 체결 [3]"
    after = "- [일자] WEFTEC 2026에서 글로벌 테스트베드 협약 체결 [3]"
    out = settle(before, after, Evidence.of([searched], answer=before, today=TODAY)).text
    assert out == before


# ── marks the checks put on true values, and marks inside a date ───────────────────────

CAMPAIGN_REQUEST = (
    "캠페인 정보(한빛소프트, 가상 — 아래가 전부임):\n- 기간 11/1~11/30, 목표: 30일 무료 체험 신청\n"
    "- 메시지 포인트: 설치형·클라우드형 선택, 망분리 지원, 전자결재 연동, 한빛시청(가상) 1,200명 "
    "사용 중\n"
    "- 혜택: 11월 중 신청하면 체험 기간 30일 → 45일"
)


def test_schedule_days_inside_the_given_period_keep_no_mark():
    before = ("| 주 | 날짜 | 채널 |\n| --- | --- | --- |\n"
              "| 1주 | 11/2(월), 11/4(수) | 파워링크 |\n| 4주 | 11/23(월), 11/25(수) | 전체 |")
    after = ("| 주 | 날짜 | 채널 |\n| --- | --- | --- |\n"
             f"| 1주 | 11/2(월){MARK}, 11/4(수){MARK} | 파워링크 |\n"
             f"| 4주{MARK} | 11/23(월){MARK}, 11/25(수){MARK} | 전체 |")
    out = settle(before, after, Evidence.of([CAMPAIGN_REQUEST], answer=before, today=TODAY)).text
    assert MARK not in out
    # a day outside it still gets the check's mark
    before2 = "본 캠페인은 12/5(토)까지 연장됩니다."
    after2 = f"본 캠페인은 12/5(토){MARK}까지 연장됩니다."
    out2 = settle(before2, after2,
                  Evidence.of([CAMPAIGN_REQUEST], answer=before2, today=TODAY)).text
    assert MARK in out2


def test_a_rewrite_that_drops_a_given_value_is_not_taken():
    before = "11월 중 신청하면 무료 체험이 45일로 늘어납니다. 설치형·클라우드형 모두 됩니다."
    after = f"11월 중 신청하면 무료 체험 캠페인이 시작됩니다{MARK}. 설치형·클라우드형 모두 됩니다."
    out = settle(before, after, Evidence.of([CAMPAIGN_REQUEST], answer=before, today=TODAY)).text
    assert "45일" in out


def test_a_mark_never_lands_inside_a_date():
    before = "- 사용한 사실: 종이팩 재활용 업무협약 체결 (뉴스핌, 2026-09-22)."
    after = "- 사용한 사실: 종이팩 재활용 업무협약 체결 (뉴스핌, 2026년 [수치]월 [수치]일)."
    out = settle(before, after, Evidence.of(["요청"], answer=before, today=TODAY)).text
    assert "(확인 필요)월" not in out and "(확인 필요)일" not in out
    assert "2026-09-22" in out


def test_a_value_the_check_swapped_for_a_mark_comes_back():
    request = "(개발팀 변경 목록 — 한빛소프트, 가상, 출시일 2026-10-28)\n" + "\n".join(
        f"- feat(x{k}): 변경 {k}" for k in range(1, 10))
    before = "변경 목록 9개 항목 가운데 고객에게 보이는 7개만 썼습니다."
    after = f"변경 목록 {MARK} 항목 가운데 고객에게 보이는 {MARK}만 썼습니다."
    out = settle(before, after, Evidence.of([request], answer=before)).text
    assert out == before


def test_an_echoed_problem_list_above_the_answer_heading_goes():
    reply = ("「9」 — 자료에 없는 수치입니다. 자료의 값으로 바꾸거나 값 뒤에 「(확인 필요)」를 "
             "붙이세요.\n"
             "- 「1」 — 자료에 없는 수치입니다. 자료의 값으로 바꾸거나 값 뒤에 「(확인 필요)」를 "
             "붙이세요.\n\n"
             "# Answer\n# 한빛워크 3.4.0 (2026. 10. 28.)\n\n## 웹 공지용")
    out = internal_words.strip_echoed_heading(reply)
    assert out.startswith("# 한빛워크 3.4.0")
    assert "자료에 없는 수치입니다" not in out


# ── text the steps garbled: dashes, chart captions ─────────────────────────────────────


def test_a_dash_becomes_what_it_joins():
    scan = skill_scripts.module("generic-language-killer", "scan")
    text = ("1. HyperText의 'Hyper'가 가리키는 기능은 무엇인지 설명하세요. — 답안 방향: 링크.\n"
            "- **금리의 기본 정의** — 금리는 돈을 빌리는 값입니다.\n"
            "금리가 오르면 대출이 줄고 — 소비도 줄어듭니다.\n"
            "| 항목 | 값 |\n| --- | --- |\n| 비고 | — |")
    out = scan.apply(text, scan.check(text, "", None, {}))
    assert "설명하세요. 답안 방향:" in out and "., " not in out
    assert "**금리의 기본 정의**: 금리는" in out
    assert "대출이 줄고, 소비도" in out
    assert "| 비고 | — |" in out


def test_tool_call_markup_never_reaches_a_chart_caption():
    caption = ('연말까지 4개월 안에 소진하기 어려운 사업입니다.</caption>\n'
               '<parameter name="xLabel">사업')
    assert builtin._plain_arg(caption) == "연말까지 4개월 안에 소진하기 어려운 사업입니다."
    assert builtin._plain_arg(" 부서별 집행률 ") == "부서별 집행률"


# ── check vocabulary and test labels in the answer ─────────────────────────────────────


def test_check_tool_verdicts_and_names_leave_the_answer():
    text = ("공지 본문입니다.\n\n"
            "점검 결과: block 0 · check 0 · info 1 (제목의 [공지] 태그만 빈칸으로 인식)\n"
            "점검 결과: 일정 순서·요일 확인 완료 (block 0) · 제작된 수치·연락처 없음 (block 0)\n"
            "초안에서 CTA가 자료에 없는 세부 서비스로 block 판정을 받아 바꿨습니다.\n"
            "- 글자 수는 초안 기준이고, 실제 발행 전 `copy-check` 로 한도 재검사가 필요합니다.\n"
            "「iOS」는 그대로 두고, 스크립트 지적 9개는 예외 처리했습니다.")
    out = internal_words.strip_reports(text)
    assert out.strip() == "공지 본문입니다."


def test_the_materials_test_label_stays_out_of_the_copy():
    answer = ("한빛시청(가상) 1,200명이 쓰고 있는 한빛워크. 11월 중 신청하면 체험이 45일까지 "
              "늘어납니다.")
    out = internal_words.strip_test_label(answer, CAMPAIGN_REQUEST)
    assert "(가상)" not in out and "한빛시청 1,200명" in out
    asked = CAMPAIGN_REQUEST + "\n광고에는 가상 기관임을 표시해 줘."
    assert internal_words.strip_test_label(answer, asked) == answer
    assert internal_words.strip_test_label(answer, "한빛시청 광고 써 줘") == answer


# ── Chinese and Japanese words in a Korean answer ──────────────────────────────────────

TRAVEL_SETTLEMENT_ANSWER = (
    "정산은 제12조에 따라 종료일 2026년 10월 29일(목)에서 7일을 더한 2026년 11월 "
    "5일(목)까지입니다. "
    "공휴일顺延 규정은 발췌문에 없어서 확인이 필요합니다. 이 내용은 협의(協議) "
    "결과입니다.\n"
    "```python\nprint('讨论')\n```\n"
    "- 물가 상승을 뺀 실질금리가 돈을 실제로 얼마나增值시키는지를 결정합니다.\n"
    "- 팀별로 입장이 달라질 수 있는지讨论해 봅시다.\n"
    "- 지역 拠点 운영 방식은 자료에 없습니다.")


def test_stray_chinese_is_found_outside_glosses_code_and_the_persons_words():
    runs = [run for _, _, run in stray_han.strays(TRAVEL_SETTLEMENT_ANSWER)]
    assert runs == ["顺延", "增值", "讨论", "拠点"]
    # the person's own words stay theirs
    assert not stray_han.strays("「贵司」라는 표현을 쓰셨는데, 이 말은 귀사라는 뜻입니다. 그대로 "
                                "두겠습니다.",
                                given="고객이 贵司 라고 썼어")
    # a Chinese text is no slip
    assert not stray_han.strays("讨论这个问题的时候我们需要注意几点。")


def test_stray_chinese_is_said_again_in_korean_once():
    calls = []

    async def complete(model, messages, api_key, max_tokens):
        calls.append(messages[0]["content"])
        return ('["공휴일 순연 규정은 발췌문에 없어서 확인이 필요합니다.", '
                '"- 물가 상승을 뺀 실질금리가 돈의 실제 가치를 얼마나 늘리는지를 결정합니다.", '
                '"- 팀별로 입장이 달라질 수 있는지 토론해 봅시다.", '
                '"- 지역 拠点 운영 방식은 자료에 없습니다."]'), {"inputTokens": 10,
                                                          "outputTokens": 5}

    out, changed = asyncio.run(stray_han.mend(TRAVEL_SETTLEMENT_ANSWER, complete=complete,
                                              model="m"))
    assert len(calls) == 1
    assert ("공휴일 순연 "
            "규정은") in out and "가치를 얼마나 늘리는지" in out and "토론해 봅시다" in out
    # still Chinese after the rewrite: read in Hangul
    assert "지역 거점 운영 방식" in out
    assert "협의(協議)" in out and "print('讨论')" in out
    assert len(changed) == 4


def test_stray_chinese_without_a_writer_is_read_in_hangul():
    out, _ = asyncio.run(stray_han.mend(TRAVEL_SETTLEMENT_ANSWER))
    assert "공휴일순연 규정" in out and "토론해 봅시다" in out
    assert not stray_han.strays(out)


def test_a_character_count_keeps_its_mark_only_when_it_is_wrong():
    request = ("새 기능 「AI 회의록 요약」 헤드라인 여러 안 뽑아 줘.\n- 베타 기간(11월 한 달) "
               "기존 고객 무료")
    before = ("| 각도 | 안 | 헤드라인 | 글자 수 |\n| --- | --- | --- | --- |\n"
              "| 역발상 | 12 | 회의록이 5분 뒤 도착합니다 | 15 |\n"
              "| 긴급성 | 8 | 11월 한 달만, 기존 고객 무료 | 30 |")
    after = before.replace("| 15 |", f"| 15{MARK} |").replace("| 30 |", f"| 30{MARK} |")
    out = settle(before, after, Evidence.of([request], answer=before, today=TODAY)).text
    assert f"15{MARK}" not in out  # 「회의록이 5분 뒤 도착합니다」 is 15 characters
    assert f"30{MARK}" in out  # 「11월 한 달만, 기존 고객 무료」 is not 30
