"""Checks flag, never damage (r5 over-constraint review): a check script, the copy check, the
value guard, the template and the pre-pass do not blank correct values, delete supported lines,
rewrite right facts, force fields that do not fit or leak their own process into the answer.
Strings are the round-5 outputs' own."""

import asyncio
import datetime as dt
import json

from app.services import (
    copy_check,
    deck,
    doc_formats,
    internal_words,
    post_checks,
    report,
    report_checks,
    skill_scripts,
    value_guard,
)

scan = skill_scripts.module("generic-language-killer", "scan")
comms = skill_scripts.module("internal-comms", "check")
claims = skill_scripts.module("copy-check", "claims")
trace = skill_scripts.module("grounding-check", "trace")
dates = skill_scripts.module("date-calc", "dates")
quotes = skill_scripts.module("quote-calc", "quote")

LANDING = (
    "「한빛워크」 랜딩 페이지 문구 써 줘. 히어로(헤드라인·서브), 기능 3개 블록, "
    "고객 사례 한 줄, CTA까지.\n"
    "제품 정보(한빛소프트, 가상 — 아래 자료가 전부임):\n"
    "- 공공기관·중견기업용 협업툴: 메신저, 화상회의(최대 50명), 문서 공동편집, 결재 알림 연동\n"
    "- 설치형·클라우드형 둘 다 제공, 설치형은 망분리 환경 지원\n"
    "- 무료 체험 30일, 도입 상담 신청 가능")


# ── 1. skill post-check scripts ──────────────────────────────────────────


def test_new_copy_keeps_its_everyday_numbers():
    # r5 A12-T1: 「1명부터」 and 「2개 버전」 were blanked and came back as 「(1)명까지」
    answer = ("**화상회의 — 최대 50명까지**\n한빛워크 안에서 1명부터 50명까지 바로 열립니다.\n\n"
              "같은 문서가 2개 버전으로 흩어지고, 어디가 최종본인지 확인하는 데 시간이 듭니다.")
    for final in (False, True):
        out = scan.post(answer, {"request": LANDING, "final": final})
        assert out["text"] == answer and not out["fixed"] and not out["rewrite"]


def test_an_edit_of_the_person_s_text_asks_once_about_a_new_figure_and_never_blanks_it():
    request = "상투어 빼 줘\n\n신규 기능은 4분기 출시 예정입니다."
    answer = "## 고친 글\n신규 기능은 4분기에 출시할 예정이며 처리 속도가 40% 빨라집니다."
    first = scan.post(answer, {"request": request, "final": False})
    assert [f["text"] for f in first["rewrite"] if f["kind"] == "number"] == ["40%"]
    final = scan.post(answer, {"request": request, "final": True})
    assert "40%" in final["text"] and "[구체적 수치]" not in final["text"]
    assert [f["text"] for f in final["flags"] if f["kind"] == "number"] == ["40%"]


def test_a_dash_between_an_item_and_its_note_becomes_a_colon_and_a_header_s_stays():
    # r5 A08-T1: 「…생각함, HTML은 …」; A08-T2: 「(…, 2026년 게시) 자막 기반 전사」
    text = ("- HTML을 구문 언어처럼 생각함 — HTML은 구조를 '표시(markup)'하는 언어입니다.\n"
            "(경제 이야기, 길이 미상, 2026년 게시) — 자막 기반 전사\n"
            "**기능 블록 1 — 결재 알림 연동**")
    out = scan.apply(text, scan.check(text, ""))
    assert "- HTML을 구문 언어처럼 생각함: HTML은" in out
    assert "2026년 게시) — 자막 기반 전사" in out
    assert "**기능 블록 1 — 결재 알림 연동**" in out


SHUTDOWN = (
    "「한빛메모」 서비스 종료 안내문 써 줘.\n"
    "- 일정: 2026년 11월 2일 신규 가입 중단 → 12월 31일 메모 작성·수정 중단(읽기 전용) → "
    "2027년 3월 31일 서비스 완전 종료 및 데이터 삭제\n"
    "- 내보내기: 앱 설정 > 데이터 내보내기, 2027년 3월 31일까지\n날짜마다 요일도 맞게 넣어 줘.")


def test_a_year_less_day_keeps_a_weekday_right_in_a_year_the_schedule_names():
    # r5 A14-T2: 「12월 31일(목)」 read as 2027 and changed to (금)
    text = "12월 31일(목)부터는 읽기만 가능해집니다. 2027년 3월 31일(수)에 종료됩니다."
    out = comms.post(text, {"material": SHUTDOWN, "today": "2026-10-13", "final": True})
    assert out["text"] == text and not out["fixed"]


def test_a_weekday_wrong_in_the_only_year_is_still_fixed_and_an_unsure_one_only_shown():
    memo = "[인사팀 메모] 2026-11-02(월)부터 새 HR 시스템으로 신청."
    sure = comms.post("11월 2일(화)부터 씁니다.", {"material": memo, "today": "2026-10-08"})
    assert "11월 2일(월)" in sure["text"]
    # 12월 31일 is a Wednesday in no year the notice could mean: shown, not rewritten
    unsure = comms.post("12월 31일(화)부터 읽기 전용입니다.",
                        {"material": SHUTDOWN, "today": "2026-10-13"})
    assert "12월 31일(화)" in unsure["text"] and unsure["flags"][0]["kind"] == "weekday"


CHANGES = ("(개발팀 변경 목록 — 한빛소프트, 가상, 출시일 2026-10-28)\n"
           "- deprecate: 구 API v1의 /messages/list 엔드포인트 2027-03-31 종료 예정 "
           "(v2 /messages 사용)")


def test_a_judged_line_made_of_the_material_s_names_is_kept_and_shown():
    # r5 A16-T1: the v2 switch line, backed by 「(v2 /messages 사용)」, was taken out
    line = "해당 API를 쓰는 서비스는 그 전에 v2의 /messages로 전환해 주세요."
    assert post_checks.names_the_material(line, CHANGES)
    assert not post_checks.names_the_material("결제가 3배 빨라졌습니다.", CHANGES)
    answer = ("## 주의\n- 구 API v1의 /messages/list 엔드포인트가 2027년 3월 31일에 종료됩니다. "
              + line)

    async def complete(model, messages, api_key, max_tokens):
        prompt = messages[0]["content"]
        if prompt.startswith("You check an answer"):
            return json.dumps({"unsupported": [{"quote": line, "why": "전환 안내 없음"}]},
                              ensure_ascii=False), {}
        if prompt.startswith("Below are the material an answer was written from and statements"):
            return json.dumps({"verdicts": []}), {}
        return answer, {}

    out = asyncio.run(post_checks.run(
        answer, keys=["release-notes"], request="릴리스 노트 써 줘", material=CHANGES,
        attached="", computed=[], today="2026-10-13", complete=complete, model="w",
        judge="j", api_key="k"))
    assert line in out.text and not [f for f in out.fixed if "뺌" in f["why"]]
    assert any(f["text"] == line for f in out.flags)


def test_a_rewrite_s_reply_to_the_check_is_cut_from_the_top():
    before = ("### 제목\n모바일 앱 2.0 소식과 시범 사용 제안\n\n### 본문\n\n"
              "다음 주 중 편하신 시간에 15~20분 정도 통화로 안내드려도 될까요?")
    reply = ("점검이 끝났습니다. 「15~20분」은 통화 시간 제안이라 자료에 없어도 무방하지만, "
             "스크립트가 15를 수치로 짚었습니다. 통화 시간 제안은 허용되는 항목이라 그대로 두고, "
             "그 외 실적·도입 효과는 쓰지 않았습니다.\n\n" + before)
    assert internal_words.strip_rewrite_preface(reply, before) == before
    table = "| 각도 | # | 헤드라인 |\n|---|---|---|\n| 호기심 | 17 | 정리부터 자동화 |"
    reply = ("#17의 「자동화」는 기능 설명에 있는 「자동으로 만들어」에서 나온 말이라 "
             "자료에 근거가 있습니다. 다만 스크립트가 확인 표시를 달았으니, "
             "더 안전하게 쓰려면 「정리부터 "
             "자동으로」로 바꿀 수 있습니다.\n\n" + table)
    assert internal_words.strip_rewrite_preface(reply, table) == table
    # the answer's own opening stays
    opening = "한빛워크 랜딩 페이지 카피입니다.\n\n### 히어로"
    assert internal_words.strip_rewrite_preface(opening, opening) == opening


# ── 2. copy check: copy only, marked, never emptied ──────────────────────

HEADLINES = (
    "| 각도 | # | 헤드라인 | 글자 수 | 추천 채널 | 활용 이유 |\n|---|---|---|---|---|---|\n"
    "| 호기심 | 1 | 회의 끝나고 5분이면 정리까지 끝나는 이유 | 23 | SNS 첫 줄 | "
    "숫자가 들어가면서도 답을 미뤄 클릭을 유도합니다 |")
MEETING_AI = ("- 화상회의가 끝나면 5분 안에 요약·결정 사항·할 일 목록을 자동으로 만들어 "
              "회의 참석자에게 보냄\n- 베타 기간(11월 한 달) 기존 고객 무료")


def test_the_copy_check_leaves_the_notes_columns_alone():
    # r5 A12-T2: 「추천 채널·활용 이유」 emptied in all 25 rows
    judged = {"unsupported": [{"quote": "숫자가 들어가면서도 답을 미뤄 클릭을 유도합니다",
                               "why": "자료에 없음"}]}
    found = claims.check(HEADLINES, MEETING_AI, [judged], {})
    assert not [f for f in found if f["kind"] == "claim"]


def test_a_feature_said_in_plain_words_and_a_guess_are_not_claims():
    # r5 A12-T1: 「결재가 진행되면 알림이 따라옵니다」; A15-T1: the mail's one reason
    copy = ("결재가 진행되면 알림이 따라옵니다.\n"
            "오송생명과학단지 제조시설과 향남공단 나보타 공장이 떨어져 있어, 현장 결재와 본사 간 "
            "문서 공유가 수작업으로 이어지는 경우가 많을 것 같습니다.")
    judged = {"unsupported": [
        {"quote": "결재가 진행되면 알림이 따라옵니다", "why": "x"},
        {"quote": "현장 결재와 본사 간 문서 공유가 수작업으로 이어지는", "why": "x"}]}
    assert not [f for f in claims.check(copy, LANDING, [judged], {}) if f["kind"] == "claim"]


def test_a_claim_left_after_the_rewrite_is_marked_not_dropped():
    text = "**화상회의**\n팀 단위 회의부터 부서 전체 회의까지 한 번에 엽니다."
    left = [{"kind": "claim", "text": "팀 단위 회의부터 부서 전체 회의까지", "line": 1,
             "start": 0, "end": 19, "action": "rewrite"}]
    out = copy_check.mark_left(text, left)
    assert out == "**화상회의**\n팀 단위 회의부터 부서 전체 회의까지 (확인 필요) 한 번에 엽니다."
    assert "팀 단위 회의부터" in copy_check.lift_marks(out).split("보내기 전 확인")[0]


def test_a_cell_a_guard_emptied_comes_back():
    emptied = HEADLINES.replace("| SNS 첫 줄 | 숫자가 들어가면서도 답을 미뤄 클릭을 유도합니다 |",
                                "| | |")
    settled = value_guard.settle(HEADLINES, emptied, value_guard.Evidence.of([MEETING_AI]))
    assert settled.text == HEADLINES and settled.kept
    judged = HEADLINES.replace("숫자가 들어가면서도 답을 미뤄 클릭을 유도합니다", " 확인 필요 ")
    back = value_guard.settle(HEADLINES, judged, value_guard.Evidence.of([MEETING_AI])).text
    assert "숫자가 들어가면서도 답을 미뤄 클릭을 유도합니다 (확인 필요)" in back


# ── 3. no 「(확인 필요)」 on right values ────────────────────────────────


def _marked(text: str, **options) -> list[str]:
    return [f["text"] for f in trace.check(text, "", [], {"today": "2026-10-13", **options})]


def test_emergency_numbers_statutory_rates_examples_and_worked_lines_are_not_marked():
    lines = [
        # r5 P14, P48
        "1. 지금 당장 119에 전화하거나 보호자에게 도움을 요청해 응급실로 이동.",
        "과실 비율에 이의가 있으면 112에 신고하여 사고 보고서를 받는 것이 유리합니다.",
        # r5 P19
        "금융소득에 대한 이자소득세(기본 15.4%, 금융소득 2,000만 원 초과 시 종합과세)를 빼세요.",
        "수익률 0.5%포인트 차이는 3천만 원 기준 연 15만 원 수준입니다.",
        # r5 P46
        "실제 마지막 행(예: 4,872행)까지 범위를 줄이는 것이 좋습니다.",
    ]
    for line in lines[:2]:
        assert _marked(line, advice=True) == [], line
    assert _marked(lines[2], advice=True) == []
    assert "15만 원" not in " ".join(_marked(lines[3], advice=True))
    assert _marked(lines[4], bare=True) == []
    # an unknown hotline still is
    assert _marked("상담은 1234에 전화하세요.", advice=True) == ["1234"]


# ── 4. frames that fit, nothing injected ─────────────────────────────────

QUOTES = (
    "[견적 2] 다솜시스템\n- 단가 1,390,000원(부가세 포함) × 30대\n"
    "- 무상 보증 2년(센터 입고), 3년 연장 시 대당 45,000원(부가세 포함)\n"
    "[견적 1] 가온정보통신\n- 무상 보증 3년(방문 수리)\n"
    "조건: 보증 3년 필수, 기존 기기 데이터 삭제 증빙 필요.")


def test_a_must_the_material_never_says_is_missing_is_unconfirmed_not_failed():
    # r5 A04-T2: 「다솜시스템: 탈락 — 보증기간 없음」 for a two-year warranty
    reading = {"musts": [{"name": "보증기간", "kind": "yesno"}],
               "options": [{"name": "다솜시스템", "musts": {"보증기간": "없음"}},
                           {"name": "가온정보통신", "musts": {"보증기간": "있음"}}]}
    status = report_checks.must_status(reading, report_checks._material_numbers(QUOTES), QUOTES)
    assert status["options"]["다솜시스템"]["status"] == "조건 미확인"
    stated = QUOTES.replace("무상 보증 2년(센터 입고), 3년 연장 시 대당 45,000원(부가세 포함)",
                            "무상 보증 없음")
    status = report_checks.must_status(reading, report_checks._material_numbers(stated), stated)
    assert status["options"]["다솜시스템"]["status"] == "탈락"


def test_a_name_over_the_band_width_is_not_cut_through():
    # r5 A09-T1: 「KorQuAD-RAG」 became 「KorQuAD-RA」
    pairs = deck._clean_pairs([["KorQuAD-RAG", "한국어 질의응답 공개 벤치마크"]], "bands")
    assert pairs == [["KorQuAD-RAG", "한국어 질의응답 공개 벤치마크"]]
    assert deck._cut("아주 긴 데이터셋 이름입니다", 10) == "아주 긴 데이터셋"


def test_a_report_for_a_meeting_is_not_an_agenda():
    # r5 A10-T1: agenda head fields, its sections and no web search
    request = ("「국내 클라우드 협업툴 시장」에 진입할 만한지 검토 보고서 써 줘. "
               "경영회의 안건 자료야.")
    assert doc_formats.detect(request).id != "agenda"
    assert doc_formats.detect("다음 주 회의 안건 초안을 같이 잡아 줘.").id == "agenda"


# ── 5. pre-pass: nothing uncertain handed over as settled ────────────────


def test_a_quote_with_every_line_left_out_settles_no_total():
    # r5 A04-T2: 「공급가액(VAT 별도)은 0원입니다」 six times
    result = quotes.run({"items": [
        {"name": "노트북(견적1)", "unit_price": 1320000, "qty": 30, "excluded": "예산 초과"},
        {"name": "노트북(견적2)", "unit_price": 1390000, "qty": 30, "excluded": "보증 미달"}]})
    assert result["ok"] is False and "0원" in result["warning"]
    assert report_checks._quote_empty(result)


PLANS = ("A안: 지금 쓰는 사내 메신저(무료) 유지 + 화상회의 라이선스만 추가 구매\n"
         "- 화상회의 라이선스 1인당 월 8,000원 × 40명\n"
         "B안: 통합 협업툴(메신저·화상회의·공동편집·일정·할 일) 전환\n"
         "- 1인당 월 14,000원 × 40명, 연 단위 계약 시 15% 할인\n"
         "- 초기 전환 컨설팅·데이터 이전 1회 300만원")


def test_option_wordings_meet_on_their_label_and_an_unpriced_discount_is_not_settled():
    # r5 A04-T1: two wordings of A안 were two options; B안 lost its 15% discount
    one = report_checks._option_key("A안: 사내 메신저 유지 + 화상회의 라이선스 추가")
    other = report_checks._option_key(
        "A안: 지금 쓰는 사내 메신저(무료) 유지 + 화상회의 라이선스만 추가")
    assert one == other == "A안"
    assert report_checks._option_key("팀링크 (엔터프라이즈)") == "팀링크"
    options = {
        "A안: 사내 메신저 유지": {"total": 3840000, "rows": [
            {"name": "화상회의", "amount": 3840000, "factors": [8000, 40, 12]}]},
        "B안: 통합 협업툴 전환": {"total": 9720000, "rows": [
            {"name": "구독", "amount": 6720000, "factors": [14000, 40, 12]},
            {"name": "컨설팅", "amount": 3000000, "factors": [3000000]}]}}
    assert report_checks._missing_discount(options, [], PLANS) == ["B안: 통합 협업툴 전환"]
    options["B안: 통합 협업툴 전환"]["rows"][0]["factors"].append(0.85)
    assert report_checks._missing_discount(options, [], PLANS) == []


def test_a_relative_day_with_its_date_written_beside_it_is_that_date():
    # r5 A01-T2: 「다음 주 목요일(10/15)」 resolved from the meeting day to 10/22
    text = "다음 주 목요일(10/15) 14시 교육혁신팀 주간회의야."
    row = next(r for r in dates.scan(text, dt.date(2026, 10, 15)) if r["text"] == "다음 주 목요일")
    assert row["date"] == "2026-10-15"


# ── 6. report rendering ──────────────────────────────────────────────────


def test_a_figure_mark_with_brackets_inside_never_reaches_the_reader():
    # r5 A01-T1
    mark = ("[[그림: flow | LMS 개편 검수·테스트 흐름 | 한결소프트 1차 시안(10/15) → "
            "[김도윤: 모바일 화면 검수 체크리스트] ∥ [최하늘: 학생 테스트 그룹 10명 모집] → "
            "검수·테스트 결과 반영]]")
    assert report.figure_marks(mark)[0]["kind"] == "flow"
    out = report.caption_figures_and_tables([{"heading": "후속 조치",
                                              "content": "검수를 진행합니다.\n\n" + mark}])
    assert "[[그림" not in out[0]["content"] and "김도윤" not in out[0]["content"]


def test_a_table_without_a_caption_is_not_titled_with_its_columns():
    content = ("| 항목 | 내용 | 비고 |\n|---|---|---|\n| 가 | 나 | 다 |\n\n"
               "| 항목 | 내용 | 비고 |\n|---|---|---|\n| 라 | 마 | 바 |")
    out = report.caption_figures_and_tables([{"heading": "2. 결정 사항", "content": content}])
    text = out[0]["content"]
    assert "**표 1. 결정 사항**" in text and "**표 2**" in text and "항목·내용·비고" not in text


def test_a_formula_line_is_never_trimmed_as_a_restatement():
    # r5 A10-T2
    earlier = ["시장 규모는 유료 이용 학생 수 N(명) × 9,900원 × 12개월 × c로 계산한다."]
    body = ("연간 시장 규모 계산은 아래 식으로 구성합니다.\n"
            "- 연간 시장 규모(원) = N(명) × 9,900원(월 구독료) × 12개월 × c\n"
            "- 2단계(2주): 9,900원 요금으로 12개월 이용을 가정해 N을 다시 셉니다.")
    trimmed, _ = report.trim_restatements(body, earlier)
    assert "= N(명) × 9,900원(월 구독료) × 12개월 × c" in trimmed and "2단계(2주)" in trimmed


def test_internal_wording_in_a_callout_is_scrubbed():
    # r5 A04-T1
    text = ("```callout\nB안 비용은 코드에서 확정된 금액을 사용합니다. 연 단위 계약 시 15% 할인이 "
            "있습니다.\n```\n\n본문입니다.")
    out = internal_words.scrub(text)
    assert "코드에서 확정된" not in out and "15% 할인" in out and "본문입니다." in out
    alone = internal_words.scrub("```callout\n확정 수치 블록에 따르면 그렇습니다.\n```\n\n본문.")
    assert "```" not in alone and alone.strip() == "본문."
