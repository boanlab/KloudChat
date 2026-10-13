"""Round 3 third-pass leftovers, with the evaluation's own strings: settled comparisons in the
cost block, no needless gap questions, and the fixes that keep work text out of deliverables."""

from __future__ import annotations

import datetime as dt

from app.services import (
    copy_check,
    deck,
    internal_words,
    report,
    report_checks,
    skill_scripts,
    units,
)

# ── A04 T2: three laptop quotes, VAT-exclusive and VAT-inclusive side by side ──────────

A04_T2 = """교직원 업무용 노트북 30대 교체 건, 견적 세 곳 받은 걸로 비교 검토 보고서 써 줘.
어디로 할지 추천까지.
[견적 1] 가온정보통신
- 단가 1,320,000원(부가세 별도) × 30대
- 무상 보증 3년(방문 수리), 납기 계약 후 3주
[견적 2] 다솜시스템
- 단가 1,390,000원(부가세 포함) × 30대
- 무상 보증 2년(센터 입고), 3년 연장 시 대당 45,000원(부가세 포함)
- 납기 2주, 기존 기기 수거는 대당 10,000원(부가세 포함)
[견적 3] 누리컴
- 단가 1,480,000원(부가세 별도) × 30대, 30대 이상 구매 시 총액 5% 할인
- 기존 기기 수거 무료, 데이터 삭제 확인서는 별도 대당 5,000원(부가세 별도)
조건: 예산 4,800만원(부가세 포함), 보증 3년 필수, 기존 기기 데이터 삭제 증빙 필요."""

#: a reading of the quotes as the cost prompt asks for it
A04_READING = {
    "period": "구매", "years": 1,
    "budget": {"amount": 48000000, "vat": "incl"},
    "options": [
        {"name": "가온정보통신", "items": [
            {"name": "노트북", "unit_price": 1320000, "count": 30, "per": "once", "vat": "excl"}]},
        {"name": "다솜시스템", "items": [
            {"name": "노트북", "unit_price": 1390000, "count": 30, "per": "once", "vat": "incl"},
            {"name": "보증 3년 연장", "unit_price": 45000, "count": 30, "per": "once",
             "vat": "incl"},
            {"name": "기존 기기 수거", "unit_price": 10000, "count": 30, "per": "once",
             "vat": "incl"}]},
        {"name": "누리컴", "items": [
            {"name": "노트북", "unit_price": 1480000, "count": 30, "per": "once", "vat": "excl"},
            {"name": "5% 할인", "unit_price": 1480000, "count": 30, "per": "once", "rate": 0.05,
             "sign": -1, "vat": "excl"},
            {"name": "데이터 삭제 확인서", "unit_price": 5000, "count": 30, "per": "once",
             "vat": "excl"}]},
    ],
}


def _a04_costs() -> dict:
    numbers = report_checks._material_numbers(A04_T2)
    costs = report_checks.option_costs(A04_READING, numbers)
    assert costs is not None
    return costs


def test_mixed_vat_quotes_are_compared_on_one_basis() -> None:
    costs = _a04_costs()
    totals = {name: entry["total"] for name, entry in costs["options"].items()}
    # 27B wrote 39,600,000 and 42,330,000 (VAT-exclusive) beside 다솜's inclusive 43,350,000
    assert totals == {"가온정보통신": 43_560_000, "다솜시스템": 43_350_000, "누리컴": 46_563_000}
    assert costs["vat_basis"] == "incl" and costs["lowest"] == "다솜시스템"


def test_the_budget_is_judged_by_code() -> None:
    costs = _a04_costs()
    # flash judged 누리컴 「예산 대비 1,465,000원을 초과」; it is within, 1,437,000원 left
    assert costs["budget"]["room"] == {
        "가온정보통신": 4_440_000, "다솜시스템": 4_650_000, "누리컴": 1_437_000}
    block = report_checks._cost_block(costs)
    assert "부가세 포함" in block and "가장 적은 대안: 다솜시스템 (부가세 포함 기준)" in block
    assert "누리컴 46,563,000원 → 예산 안(잔액 1,437,000원)" in block
    assert "예산 안: 가온정보통신, 다솜시스템, 누리컴" in block
    assert "다솜시스템 43,350,000원 < 가온정보통신 43,560,000원 < 누리컴 46,563,000원" in block
    assert "가온정보통신와 다솜시스템의 합계 차이: 210,000원" in block
    assert "30 × 1,320,000 + 부가세 10%" in block


def test_the_writer_s_settled_comparison_is_grounded_in_the_block() -> None:
    trace = skill_scripts.entry("grounding-check")
    block = report_checks._cost_block(_a04_costs())
    text = ("누리컴은 부가세 포함 46,563,000원으로 예산 안이며 잔액은 1,437,000원입니다. "
            "가온정보통신은 다솜시스템보다 210,000원 많습니다.")
    assert trace.check(text, A04_T2, [block]) == []


def test_one_vat_basis_or_none_leaves_totals_as_they_were() -> None:
    plain = {"period": "1년", "years": 1, "options": [
        {"name": "A안", "items": [{"name": "구입", "unit_price": 1320000, "count": 30,
                                  "per": "once"}]},
        {"name": "B안", "items": [{"name": "구입", "unit_price": 1390000, "count": 30,
                                  "per": "once"}]}]}
    costs = report_checks.option_costs(plain, report_checks._material_numbers(A04_T2))
    assert costs["options"]["A안"]["total"] == 39_600_000
    assert "vat_basis" not in costs and "budget" not in costs
    # a budget the material does not state is not judged
    made_up = {**plain, "budget": {"amount": 50000000, "vat": "incl"}}
    assert "budget" not in report_checks.option_costs(
        made_up, report_checks._material_numbers(A04_T2))


def test_quote_calc_block_ranks_the_vendors() -> None:
    result = {"quotes": [
        {"vendor": "가온정보통신", "amount": 39600000, "excl": 39600000, "incl": 43560000},
        {"vendor": "다솜시스템", "amount": 43350000, "excl": 39409091, "incl": 43350000},
    ]}
    block = report_checks._quote_block(result, material=A04_T2)
    assert "다솜시스템 43,350,000원 < 가온정보통신 43,560,000원" in block
    assert "가장 낮은 견적: 다솜시스템; 가온정보통신 +210,000원" in block


# ── A01 T2 / A07 T1: no question the request already answers ─────────────────────────

A01_T2 = ("다음 주 회의 안건 초안을 같이 잡아 줘.\n\n다음 주 목요일(10/15) 14시 교육혁신팀 "
          "주간회의야. 1시간이고, 다룰 것: LMS 1차 시안 검토(업체 담당자 30분 참석 예정), "
          "워크숍(10/22) 준비 점검.")


def test_a_meeting_still_to_come_has_its_date() -> None:
    today = dt.date(2026, 10, 12)
    assert report_checks.meeting_date(A01_T2, "", today) == dt.date(2026, 10, 15)
    # no weekday: a meeting the request says is coming is the next such day
    assert report_checks.meeting_date("다음 주 10/15 주간회의 안건 잡아 줘", "", today) == (
        dt.date(2026, 10, 15))
    # December's meeting asked about in late year, January's one next year
    assert report_checks.meeting_date("다음 주 1/7 회의 안건", "", dt.date(2026, 12, 30)) == (
        dt.date(2027, 1, 7))
    # minutes of a past meeting with a yearless day and no weekday: still asked
    assert report_checks.meeting_date("10/8 회의 메모로 회의록 써 줘", "", today) is None


A07_T1 = ("「국내 대학의 생성형 AI 활용 가이드라인 동향」을 조사해서 보고서로 정리해 줘. 우리 "
          "대학(한빛대학교, 가상) 교무처에서 자체 가이드라인을 만들기 전에 참고하려는 거야.\n"
          "- 공통적으로 다루는 쟁점(과제·시험에서의 허용 범위, 출처 표기, 표절 판단)\n"
          "출처를 꼭 달아 줘.")


def test_a_web_research_request_is_not_asked_for_material() -> None:
    assert report._results_without_data(A07_T1, [])  # 「시험」 reads as results
    assert not report._results_without_data(A07_T1, [], web=True)
    # an upload the request points at is still asked for
    assert report._results_without_data("첨부 파일의 실험 결과 동향을 조사해서 정리해 줘", [],
                                        web=True)


# ── work text in deliverables (A09·A10·A12·A13·A16) ──────────────────────────────────

A09_T1_NOTES = (
    "1단계에서는 조기 종료로 질의-문서 쌍의 토큰 처리를 줄입니다. 수정: 근거 없는 수치를 "
    "「[수치]」 등으로 비워 두고, 요청의 값을 채워 넣어 주세요. \"교차 인코더가 560M이던 걸 "
    "22M으로 줄여도 정확도는 72.8에서 71.9로만 떨어집니다.\"")
A09_T1_NOTES_2 = (
    "KorQuAD-RAG EM은 72.8에서 71.9로 소폭 하락했고, 사내 QA 정확도는 69.1에서 68.3으로 비슷하게 "
    "유지됐습니다. 96퍼센트와 33퍼센트는 자료에 근거가 없어 비율로 쓰지 않습니다. 지연 절감 "
    "효과가 명확하며 성능 손실은 작습니다.")
A09_T2_NOTES = ("여러 수치와 수식은 계산 도구로 구한 결과입니다. "
                "같은 조건에서 SRTF의 3, SJF의 4보다 "
                "높게 나온 이 결과가 왜 나오는지 확인해 보겠습니다.")


def test_speaker_notes_lose_the_writer_s_instructions() -> None:
    slides = [{"id": "s1", "title": "지식 증류 기반 2단계 구조", "bullets": ["증류"],
               "notes": A09_T1_NOTES},
              {"id": "s2", "title": "지연은 줄고 정확도는 유지", "bullets": ["지연"],
               "notes": A09_T1_NOTES_2},
              {"id": "s3", "title": "한계", "bullets": ["9 평균 반환시간"], "notes": A09_T2_NOTES}]
    assert deck.strip_internal_text(slides) == [0, 1, 2]
    assert slides[0]["notes"] == (
        "1단계에서는 조기 종료로 질의-문서 쌍의 토큰 처리를 줄입니다. \"교차 인코더가 560M이던 걸 "
        "22M으로 줄여도 정확도는 72.8에서 71.9로만 떨어집니다.\"")
    assert "자료에 근거가 없어" not in slides[1]["notes"]
    assert slides[1]["notes"].endswith("성능 손실은 작습니다.")
    assert slides[2]["notes"].startswith("같은 조건에서 SRTF의 3")


A10_T1_LINE = ("수치 제시 전에는 통계청·과기정통부 등 공공 또는 협회 발표 자료를 "
               "교차 확인해야 하며, "
               "확인 전까지는 「확인 필요」 상태로 남겨둡니다.")
A10_T2_LINE = ("학원 구매는 학원 내 디지털 교육 도구 도입 관점에서 접근합니다. "
               "국내 논문 검색 결과도 "
               "「AI 학습 튜터 에듀테크」로 KCI·DBpia·RISS 등을 찾았으나 "
               "국내 논문을 찾지 못했습니다.")


def test_report_sections_lose_the_writer_s_account() -> None:
    assert internal_words.strip_work_notes(A10_T1_LINE, document=True) == ""
    assert internal_words.strip_work_notes(A10_T2_LINE, document=True) == (
        "학원 구매는 학원 내 디지털 교육 도구 도입 관점에서 접근합니다.")
    # a document's own 「확인이 필요함」 stays (A02 T1 27B)
    kept = "- B사 납기 1주 차이가 실제 일정에 미치는 영향은 자료에 근거가 없어 확인이 필요함"
    assert internal_words.strip_work_notes(kept, document=True) == kept
    # a chat answer may tell the person what to fill in
    asked = "[담당자] 칸은 직접 채워 넣어 주세요."
    assert internal_words.strip_work_notes(asked) == asked


A12_T1_FLASH = (
    "두 check는 자료에 명시된 혜택(무료 체험 30일, 도입 상담 신청)이라 그대로 둡니다.\n\n"
    "### 페이지 카피\n\n**헤드라인**\n메일 첨부 파일 주고받기, 월 4,100건에서 1,300건으로\n\n"
    "### 카피 점검 결과\n\n| 항목 | 판정 |\n|---|---|\n"
    "| 헤드라인 3안, 서브, 기능 3개, 사례 | 문제 표현 없음 |\n"
    "| CTA 2건 | check 2건 (자료에 명시된 혜택이라 유지) |")


def test_copy_check_verdicts_leave_the_copy() -> None:
    out = internal_words.strip_reports(A12_T1_FLASH)
    assert "check" not in out
    assert out.lstrip().startswith("### 페이지 카피")
    assert "| 헤드라인 3안, 서브, 기능 3개, 사례 | 문제 표현 없음 |" in out
    flash = "최종 검수에서 차단(block) 표현은 0건(확인 필요)입니다.\n\n본문"
    assert internal_words.strip_reports(flash).strip() == "본문"


A16_T1_TAIL = (
    "대화 검색이 빨라졌습니다(사내 측정 평균 2.1초 → 0.8초).\n\n---\n"
    "뺀 항목: L7(로깅 라이브러리 버전 업 — 사용자 영향 없는 내부 작업), L8(토큰 갱신 로직 정리 — "
    "사용자 영향 없는 내부 리팩터링)\n항목 9개: 노트 7 · 뺌 2 · 빠짐 0")


def test_release_notes_coverage_lines_stay_with_the_script() -> None:
    assert internal_words.strip_work_notes(A16_T1_TAIL) == (
        "대화 검색이 빨라졌습니다(사내 측정 평균 2.1초 → 0.8초).")
    bold = "본문입니다.\n\n**뺀 항목**: 없음\n\n**항목 7개: 노트 7 · 뺌 0 · 빠짐 0**"
    assert internal_words.strip_work_notes(bold) == "본문입니다."


# ── A09 T1 flash: template blanks and a value in brackets ─────────────────────────────

A09_ABSTRACT = ("LiteRerank는 조기 종료와 상위 20개 후보 제한으로 지연을 412ms에서 38ms로 줄였다. "
                "다만 2,000토큰 이상의 긴 문서에서는 EM이 3.1포인트 하락했다.")


def test_a_bracket_around_a_given_value_is_taken_off() -> None:
    slides = [{"id": "s1", "title": "연구 한계 및 향후 과제",
               "bullets": ["긴 문서(2,000토큰 이상)에서 EM 하락", "[구체적 하락 수치: 3.1포인트]",
                           "[다국어 및 긴 문서 지원 과제]", "[예상 효과: 5배]"]}]
    assert deck.unwrap_known_values(slides, A09_ABSTRACT) == [0]
    assert slides[0]["bullets"][1] == "구체적 하락 수치: 3.1포인트"
    # not a value, or a value the material does not give: left
    assert slides[0]["bullets"][2:] == ["[다국어 및 긴 문서 지원 과제]", "[예상 효과: 5배]"]


def test_a_comparison_left_as_its_template_is_rewritten() -> None:
    table = {"id": "s1", "title": "기존 교차 인코더와 경량화 기법의 조건 비교",
             "table": [["기준", "교차 인코더", "경량화 기법"],
                       ["구조", "[구조 설명]", "[구조 설명]"],
                       ["장점", "[장점]", "[장점]"], ["단점", "[단점]", "[단점]"]]}
    assert len(deck.template_blanks(table)) == 6
    # fields the person fills in are not template blanks
    fields = {"id": "s2", "title": "실행 계획",
              "table": [["할 일", "담당", "기한"], ["발주", "[담당자]", "[기한]"],
                        ["계약", "[담당자]", "[금액]"]]}
    assert deck.template_blanks(fields) == []


# ── A05 T1: a 「수정:」 line beside the wrong total ─────────────────────────────────────

A05_T1 = (
    "부산 출장 2박 3일 여비는 총 409,600원이며, 숙박비 1박당 상한 80,000원을 초과한 15,000원은 "
    "본인 부담입니다.\n\n"
    "| 항목 | 근거 조항 | 식 | 금액 |\n|---|---|---|---|\n"
    "| 숙박비 | 제9조①, [별표 1] | min(95,000원, 80,000원) × 2박 | 160,000원 |\n\n"
    "합계: 119,600 + 75,000 + 75,000 + 160,000 = 429,600원\n\n"
    "수정: 합계는 429,600원입니다.\n\n"
    "- 다만, 사유서로 소명하면 상한액의 30%(24,000원/박)까지 추가 지급될 수 있어, 이 경우 총액은 "
    "453,600원까지 가능합니다 [제9조② 단서].")


def test_a_self_correction_settles_the_total() -> None:
    out = units.settle_corrections(A05_T1)
    assert out.startswith("부산 출장 2박 3일 여비는 총 429,600원이며")
    assert "수정:" not in out and "409,600" not in out
    assert "= 429,600원\n\n- 다만" in out
    # a conditional total is not the one corrected
    assert "이 경우 총액은 453,600원까지" in out
    # a release note's 「수정:」 is content
    notes = "수정: iOS 18 알림 중복 문제를 고쳤습니다."
    assert units.settle_corrections(notes) == notes


# ── A12·A13·A15 T1: 「(확인 필요)」 inside copy ──────────────────────────────────────

A12_T1 = (
    "**헤드라인**\n메일 첨부 파일을 주고받을 때마다 사라지는 시간, 줄이세요 (확인 필요)\n\n"
    "**기능 1: 메신저**\n직원 간 대화가 메신저 안에서 끝납니다. 메일 첨부 파일을 주고받느라 오가는 "
    "시간이 줄어듭니다. (확인 필요)\n\n**기능 2(확인 필요): 화상회의**\n최대 50명까지 참여할 수 "
    "있는 화상회의를 제공합니다.")
A15_T1 = ("오송 스마트공장이 ICT 기반으로 전 공정을 자동화하고 있다는 점을 확인했습니다. "
          "(확인 필요)\n\n"
          "보내기 전에: 수신 동의(또는 6개월(확인 필요) 안 거래관계)를 확인하세요.")


def test_copy_marks_move_below_the_copy() -> None:
    out = copy_check.lift_marks(A12_T1)
    body, _, tail = out.rpartition("\n\n")
    assert "확인 필요)" not in body
    assert "메일 첨부 파일을 주고받을 때마다 사라지는 시간, 줄이세요\n" in body
    assert "**기능 2: 화상회의**" in body
    assert tail.startswith("보내기 전 확인: 「줄이세요」, 「메일 첨부 파일을 주고받느라 "
                           "오가는 시간이 "
                           "줄어듭니다」, 「기능 2」")
    mail = copy_check.lift_marks(A15_T1)
    assert "점을 확인했습니다.\n\n" in mail and "6개월 안 거래관계" in mail
    assert mail.count("확인 필요") == 0 and mail.count("보내기 전 확인:") == 1
    assert copy_check.lift_marks("표시 없는 카피") == "표시 없는 카피"


# ── A10 T2 27B: a table 100× the prose's worked SOM ─────────────────────────────────

A10_T2_DOWN = (
    "**표 2. 하향식 TAM·SAM·SOM**\n\n| 구분 | 수치 | 산출 방식 |\n|---|---|---|\n"
    "| TAM | 29조 2,000억 원 | 2024년 사교육비 총액 [1] |\n"
    "| SOM (연) | 11,286억 원 | 475만 명 × 2% × 118,800원 |\n\n"
    "SOM 산출: 4,750,000명 × 0.02 = 95,000명, 95,000명 × 118,800원 = 11,286,000,000원 ≈ 113억 원"
    "(연간, 유료 이용 학생 전체 기준)(그림 2).")
A10_T2_UP = (
    "**표 3. 상향식 고객군별 SOM(연 매출)**\n\n"
    "| 고객군 | 학생 수 (가정) | 유료 이용 (×95%) | 목표 점유 (×2%) | 연 매출 (×118,800원) |\n"
    "|---|---|---|---|---|\n"
    "| 학부모 직접 구매 | 250만 명 (500만 × 50%) | 237만 5,000명 | 4만 7,500명 | 5,643억 원 |\n"
    "| 학교·교육청 구매 | 150만 명 (500만 × 30%) | 142만 5,000명 | 2만 8,500명 | 3,387억 원 |\n"
    "| 학원 구매 | 100만 명 (500만 × 20%) | 95만 명 | 1만 9,000명 | 2,257억 원 |\n"
    "| 합계 | 500만 명 | 475만 명 | 9만 5,000명 | 11,287억 원 |")


def test_a_rate_in_a_table_formula_is_a_hundredth() -> None:
    out = report.fix_table_formulas(A10_T2_DOWN)
    assert "| SOM (연) | 112.86억 원 | 475만 명 × 2% × 118,800원 |" in out
    assert "| TAM | 29조 2,000억 원 |" in out


def test_table_amounts_follow_the_prose_s_working() -> None:
    sections = [{"heading": "하향식", "content": report.fix_table_formulas(A10_T2_DOWN)},
                {"heading": "상향식", "content": A10_T2_UP}]
    out = report.fix_table_magnitudes(sections)
    up = out[1]["content"]
    assert "| 4만 7,500명 | 56.43억 원 |" in up
    assert "| 2만 8,500명 | 33.87억 원 |" in up
    assert "| 1만 9,000명 | 22.57억 원 |" in up
    assert "| 9만 5,000명 | 112.87억 원 |" in up
    assert out[0] == sections[0]
    # a table the prose agrees with, or no working in the prose: untouched
    plain = [{"heading": "x", "content": A10_T2_UP}]
    assert report.fix_table_magnitudes(plain) == plain
