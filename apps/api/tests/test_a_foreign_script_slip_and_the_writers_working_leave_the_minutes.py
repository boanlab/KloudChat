"""A run of a script neither Korean nor the material's (「결정이 نشده 미결된」) goes back to the
writer with its paragraph and is taken out by code wherever it stands (reports, chat, decks);
minutes flag a weekday the calendar disagrees with once, drop a 「확인 필요:」 line a row
already marks, and no action row the memo gives no one. Shapes from the round 16 re-verify
(reverify-z tc, tc_b, tc2)."""

from __future__ import annotations

import time

from app.services import hangul, meeting_memo, report, report_checks, weekdays

MEMO = (
    "10/14 교육혁신팀 주간회의. 참석 김지훈(팀장), 이수연, 박민호, 최다은. 불참 정우성(출장). "
    "안건1 2학기 튜터링 모집: 이수연이 포스터 시안을 다음 주 금요일까지 준비하기로 함, 모집 "
    "기간은 11/3~11/14로 확정. 안건2 예산: 박민호가 간식비 80만원을 요청했으나 팀장이 "
    "50만원으로 줄이자고 했고 합의. 안건3 설문 도구: 구글폼 vs 네이버폼 논의, 결정 못 함, "
    "다음 회의에서 최다은이 비교표 가져오기. 다음 회의 10/21 화 10시 3층 회의실."
)
GARBLED = "확정된 결정에 따라 해야 할 작업과 아직 결정이 نشده 미결된 과제를 아래에 정리합니다."
FLAG = "(화, 확인 필요: 달력상 수요일)"


def _sec(heading: str, content: str) -> dict:
    return {"heading": heading, "content": content}


def test_a_run_of_a_script_neither_korean_nor_the_materials_is_taken_out():
    assert hangul.foreign_slips(GARBLED) == ["نشده"]
    plain = "확정된 결정에 따라 해야 할 작업과 아직 결정이 미결된 과제를 아래에 정리합니다."
    assert hangul.drop_foreign_runs(GARBLED) == (plain, ["نشده"])
    # every writer's output passes the mixed-script repair: report, chat, deck
    assert hangul.repair_mixed_script(GARBLED)[0] == plain
    assert hangul.repair_mixed_script(GARBLED, keep_sentences=True)[0] == plain
    # Thai, Devanagari: a run in brackets takes its brackets with it
    assert (
        hangul.drop_foreign_runs("일정은 다음 주로 정했습니다(ไม่ได้). 장소는 미정 आज 입니다.")[0]
        == "일정은 다음 주로 정했습니다. 장소는 미정 입니다."
    )


def test_a_script_the_material_writes_or_a_text_in_that_language_stays():
    assert hangul.foreign_slips(GARBLED, "자료: نشده 는 페르시아어") == []
    persian = "این جلسه تصمیم نشده است و بعدا بررسی می شود 회의"
    assert hangul.foreign_slips(persian) == []
    assert (
        hangul.drop_foreign_runs("`نشده` 코드는 그대로 둡니다. 나머지는 한국어 문장입니다.")[1]
        == []
    )


def test_a_paragraph_with_such_a_run_goes_back_to_the_writer():
    body = f"첫 문단은 한국어로만 씁니다.\n\n{GARBLED}\n\n| 항목 | 값 |\n|---|---|\n| نشده | 1 |"
    assert report.chinese_paragraphs(body) == [(1, ["نشده"])]
    assert not report.korean_again(GARBLED, GARBLED)
    assert report.korean_again(GARBLED, GARBLED.replace("결정이 نشده 미결된", "결정되지 않은"))
    assert "slipped out of Korean" in report._KOREAN_AGAIN_PROMPT


def test_a_dates_weekday_flag_is_said_once_with_nothing_stacked_beside_it():
    sections = [
        _sec("회의 개요", f"다음 회의는 2026-10-21{FLAG}입니다(표 1) (확인 필요)."),
        _sec(
            "할 일",
            f"| 비교표 준비 | 최다은 | 2026-10-21{FLAG} | - |\n\n"
            f"- 비교표를 가져옵니다. (기한 2026-10-21{FLAG}, 확인 필요: 요일 불명) (확인 필요)",
        ),
        _sec(
            "다음 회의",
            f"다음 회의는 2026-10-21{FLAG} 10시에 열립니다 (확인 필요). "
            "장소는 미정(확인 필요)입니다.",
        ),
    ]
    out, changed = weekdays.flag_once(sections)
    assert changed == 4
    assert out[0]["content"] == f"다음 회의는 2026-10-21{FLAG}입니다(표 1)."
    assert out[1]["content"].split("\n")[0] == "| 비교표 준비 | 최다은 | 2026-10-21 | - |"
    assert out[1]["content"].endswith("- 비교표를 가져옵니다. (기한 2026-10-21)")
    assert (
        out[2]["content"] == "다음 회의는 2026-10-21 10시에 열립니다. 장소는 미정(확인 필요)입니다."
    )
    # another date keeps its own flag
    other, _ = weekdays.flag_once(
        [_sec("a", f"10월 21일{FLAG}"), _sec("b", "11월 4일(화, 확인 필요: 달력상 수요일)")]
    )
    assert other[1]["content"] == "11월 4일(화, 확인 필요: 달력상 수요일)"


def test_a_check_line_a_row_already_marks_goes():
    content = (
        "| 항목 | 내용 |\n| --- | --- |\n| 장소 | 자료에 없음 (미정) |\n"
        "확인 필요: 회의 장소는 자료에 나타나지 않아 확인이 필요합니다.\n\n"
        "확인 필요: 예산 집행 계획은 정하지 않았습니다."
    )
    out, gone = meeting_memo.restated_checks([_sec("회의 개요", content)])
    assert gone == 1
    assert "회의 장소는" not in out[0]["content"] and "예산 집행 계획" in out[0]["content"]
    flagged = (
        "| 할 일 | 담당 | 기한 |\n|---|---|---|\n"
        f"| 설문 도구 비교표 준비 | 최다은 | 2026-10-21{FLAG} |"
        "\n\n확인 필요: 설문 도구 비교표 준비(2026-10-21)"
    )
    assert meeting_memo.restated_checks([_sec("할 일", flagged)])[1] == 1


def test_an_action_row_under_a_number_column_the_memo_gives_no_one_goes_with_its_note():
    table = (
        "**표 2. 실행 항목**\n\n| 항목 | 할 일 | 담당 | 기한 | 비고 |\n|---|---|---|---|---|\n"
        "| 1 | 2학기 튜터링 포스터 시안 준비 | 이수연 | 2026-10-23(금) | - |\n"
        "| 2 | 설문 도구 비교표 준비 | 최다은 | 2026-10-21 | - |\n"
        "| 3 | 간식비 예산 집행 | 미정 | 미정 | 회의에서 지정하지 않음 |\n\n"
        "※ 3번의 담당자와 기한은 회의에서 정하지 않아 확인이 필요합니다."
    )
    sections = [_sec("실행 항목", table)]
    assert [task for _, _, task in report_checks.unassigned_rows(sections, MEMO)] == [
        ["간식비 예산 집행"]
    ]
    out = report_checks.settle_unsourced(sections, MEMO, strong=False)[0]["content"]
    assert "간식비 예산 집행" not in out and "※ 3번" not in out
    assert "| 2 | 설문 도구 비교표 준비 | 최다은 | 2026-10-21 | - |" in out


def test_the_new_patterns_take_linear_time_on_long_input():
    spaces = " " * 50_000
    started = time.monotonic()
    hangul.drop_foreign_runs(spaces + "ب" + spaces + "가" * 10)
    hangul.drop_foreign_runs("가" * 100 + "ب " * 25_000)
    meeting_memo.restated_checks(
        [_sec("a", "| a | 미정 |\n|---|---|\n| 장소 | 미정 |\n확인 필요:" + spaces)]
    )
    weekdays.flag_once(
        [_sec("a", "2026-10-21(화, 확인 필요: 달력상 수요일)," + spaces + "확인 필요:" + spaces)]
    )
    assert time.monotonic() - started < 0.5
