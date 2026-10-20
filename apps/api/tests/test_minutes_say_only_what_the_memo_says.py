"""Minutes say only what the memo says: no meeting length, transition month or open question
it never gives, no owner-less action row it never asks for, no empty column it never names;
quoted tables print as tables, Chinese slips read as Korean words, a blank never sits beside
its own value."""

from __future__ import annotations

import time

from app.services import (
    doc_formats,
    hangul,
    placeholders,
    report,
    report_checks,
    richtext,
    stated_meeting,
)
from app.services.verify import Doc

#: the round-11 false-positive memo (reverify-d fpm)
SALES = ("2026년 10월 21일(수) 오후 2시 영업1팀 "
         "주간회의, 장소 본사 5층 대회의실. 참석 오세훈(팀장), "
         "한지민, 류태호, 서은우. 불참 없음. 안건1 4분기 "
         "신규 거래처 목표: 한지민이 후보 리스트 10곳을 "
         "10월 23일(금)까지 정리하기로 함. 안건2 견적 양식 "
         "개편: 류태호가 새 양식 초안을 10월 28일(수)까지 "
         "공유하기로 함, 팀장 승인 후 11월 1주차부터 적용. "
         "안건3 출장비 정산: 서은우가 영수증 누락 3건을 "
         "이번 주 안에 제출하기로 함. 다음 회의는 10월 "
         "28일(수) 오후 2시, 같은 장소(본사 5층 대회의실).")
#: the round-12 agent memo (reverify-d/e/f tc)
TUTOR = ("10/14 교육혁신팀 주간회의. 참석 김지훈(팀장), 이수연, 박민호, 최다은. 불참 정우성(출장). "
         "안건1 2학기 튜터링 모집: 이수연이 포스터 시안을 "
         "다음 주 금요일까지 준비하기로 함, 모집 기간은 "
         "11/3~11/14로 확정. 안건2 예산: 박민호가 "
         "간식비 80만원을 요청했으나 팀장이 50만원으로 줄이자고 "
         "했고 합의. 안건3 설문 도구: 구글폼 vs 네이버폼 "
         "논의, 결정 못 함, 다음 회의에서 최다은이 비교표 "
         "가져오기. 점심 메뉴 얘기와 주차 이야기 잠깐 있었음. 다음 회의 10/21 화 10시 3층 회의실.")


def test_a_table_written_behind_a_quote_comes_out_as_a_table():
    body = ("첫째, 후보 리스트를 먼저 확보하기로 했습니다.\n\n> 표: 안건별 추진 현황\n\n"
            "> | 사업 | 추진 내용 | 상태 |\n\n> |---|---|---|\n\n> "
            "| 견적 양식 개편 | 초안 공유 | 진행 중 |\n\n"
            "둘째, 견적 양식 개편입니다.")
    out = richtext.unquote_tables(body)
    assert "> |" not in out
    assert ("| 사업 | 추진 내용 | 상태 |\n|---|---|---|\n"
            "| 견적 양식 개편 | 초안 공유 | 진행 중 |") in out
    assert "표: 안건별 추진 현황\n\n| 사업" in out
    # the same through the document mends and the structural export
    tidied = report.final_tidy([{"heading": "안건별 논의", "content": body}])[0]["content"]
    assert "> |" not in tidied
    kinds = [k for k, *_ in report_export_lines(body)]
    assert "table" in kinds


def report_export_lines(text: str) -> list[tuple]:
    from app.services import report_export

    return report_export._markdown_to_lines(text)


def test_a_quote_of_prose_stays_a_quote():
    body = "> 팀장: 이번 주 안에 마무리합시다.\n> | 이 줄은 표가 아니다 |\n\n본문."
    assert richtext.unquote_tables(body) == body


def test_a_meeting_length_and_a_month_part_the_memo_never_gives_are_found():
    said = stated_meeting.untraced(
        "회의는 약 20분 만에 주요 업무 일정을 배분하여 끝났습니다.", SALES)
    assert said == ["약 20분"]
    assert stated_meeting.untraced(
        "기존 양식 사용은 10월 말까지 병행되며, 11월 1주차에는 전환됩니다.", SALES) == ["10월 말"]
    assert stated_meeting.untraced("회의는 11시에 종료되었습니다.", TUTOR) == ["11시"]
    # one another check already marked stands
    assert not stated_meeting.untraced("기존 양식은 10월 말(확인 필요)까지 씁니다.", SALES)
    # what the memo gives stands
    assert not stated_meeting.untraced("다음 회의는 10월 28일(수) 오후 2시에 열립니다.", SALES)
    assert not stated_meeting.untraced("회의는 30분 동안 진행했습니다.",
                                       "회의 30분 동안 진행. " + SALES)


def test_an_open_question_the_memo_never_raises_is_found_and_its_own_is_not():
    invented = ("50만원이라는 총액에는 합의했지만, 이 돈을 매달 얼마씩 사용할지, 특정 행사에 몰아 "
                "쓸지, 카드 결제 후 현금 정산할지 등 운영 메커니즘은 논의되지 않았습니다.")
    absent = ("그의 부재로 인한 결석 사유(출장)가 해소되었는지 확인하지 않으면 안건2, 3의 후속 "
              "진행에 차질이 생길 수 있습니다(표 3).")
    assert stated_meeting.untraced(invented, TUTOR)
    assert stated_meeting.untraced(absent, TUTOR)
    for own in ("구글폼과 네이버폼 중 어느 것을 사용할지 결정하지 못했습니다.",
                "설문 도구를 무엇으로 할지는 다음 회의에서 최다은이 비교표를 가져온 뒤 정합니다."):
        assert not stated_meeting.untraced(own, TUTOR)


def test_the_meeting_checks_finish_on_a_long_run_of_spaces():
    text = "회의는 약 2" + " " * 50_000 + "분"
    started = time.perf_counter()
    stated_meeting.untraced(text, SALES)
    placeholders.drop_beside_values("2026-10-14(수)" + " " * 50_000 + "[회의 일자]")
    richtext.unquote_tables(">" + " " * 50_000 + "|")
    report_checks.minutes_head_place(
        [{"heading": "개요", "content": "- 장소:" + " " * 50_000}], {"fields": [["장소", ""]]})
    assert time.perf_counter() - started < 0.5


ACTIONS = ("**표 3. 액션 아이템**\n\n| 할 일 | 담당 | 기한 | 비고 |\n|---|---|---|---|\n"
           "| 튜터링 포스터 시안 준비 | 이수연 | 2026-10-23(금) | 다음 주 금요일 |\n"
           "| 구글폼·네이버폼 비교표 작성 | 최다은 | 2026-10-21(수) | 다음 회의 때 |\n"
           "| 2학기 튜터링 모집 시행 | 미정 | "
           "2026-11-03(화)~2026-11-14(토) | 담당자 회의에서 지정하지 않음 |\n"
           "\n※ 3번(튜터링 모집 시행)의 담당자는 회의에서 지정하지 않아 확인이 필요합니다.")


def test_an_action_row_nobody_was_given_is_found_and_given_ones_are_not():
    rows = report_checks.unassigned_rows([{"heading": "액션 아이템", "content": ACTIONS}], TUTOR)
    assert [r[2] for r in rows] == [["2학기 튜터링 모집 시행"]]
    # a task the memo leaves open (「검토」) is an action with no owner yet
    open_task = ACTIONS.replace("2학기 튜터링 모집 시행", "설문 도구 검토")
    assert not report_checks.unassigned_rows([{"heading": "", "content": open_task}],
                                             TUTOR + " 설문 도구는 추후 검토.")


async def test_the_minutes_check_sends_them_back_and_what_stands_is_marked_or_goes():
    question = ("카드 결제 후 현금 정산할지, 매달 얼마씩 사용할지 등 운영 메커니즘은 논의되지 "
                "않았습니다.")
    sections = [
        {"heading": "회의 개요", "content": "회의는 약 20분 만에 끝났습니다. 참석자는 4명입니다."},
        {"heading": "액션 아이템", "content": ACTIONS},
        {"heading": "미결 사항", "content": f"설문 도구는 미결입니다. {question}\n\n"
                                         "새 도구는 10월 말까지 시범 운영합니다."},
    ]
    found = report_checks.minutes_unsourced(Doc(sections, material=TUTOR))
    assert {f.code for f in found} == {"minutes_unsourced"}
    assert len(found) == 4
    assert all(f.still(f.sentence) for f in found)
    # self-hosted or small writer: the meeting's length, the question and the row go (with
    # the 「※」 line naming it); a value elsewhere is marked where it stands
    dropped = report_checks.settle_unsourced(sections, TUTOR, strong=False)
    assert dropped[0]["content"] == "참석자는 4명입니다."
    assert "튜터링 모집 시행" not in dropped[1]["content"]
    assert "이수연" in dropped[1]["content"]
    assert dropped[2]["content"] == ("설문 도구는 미결입니다.\n\n"
                                     "새 도구는 10월 말(확인 필요)까지 시범 운영합니다.")
    # strong writer: marked where it stands
    marked = report_checks.settle_unsourced(sections, TUTOR, strong=True)
    assert "회의는 약 20분(확인 필요) 만에 끝났습니다." in marked[0]["content"]
    assert "2학기 튜터링 모집 시행 (확인 필요)" in marked[1]["content"]
    assert "논의되지 않았습니다 (확인 필요)." in marked[2]["content"]


def test_a_column_the_memo_never_names_and_no_cell_fills_goes():
    body = ("**표 2. 이슈**\n\n| 안 | 금액 | 납기 | 비고 |\n|---|---|---|---|\n"
            "| 박민호 초안 | 80만원 | 미정 | 미합의 |\n| 팀장 제안(합의) | 50만원 | 미정 | 확정 |")
    out, dropped = report_checks.minutes_blank_columns([{"heading": "", "content": body}], TUTOR)
    assert dropped == 1
    assert "| 안 | 금액 | 비고 |" in out[0]["content"]
    assert "납기" not in out[0]["content"]
    # an owner column of 「미정」 is the minutes' own
    owners = ("| 할 일 | 담당 | 기한 |\n|---|---|---|\n| 비교표 | 미정 | 미정 |")
    kept, none = report_checks.minutes_blank_columns([{"heading": "", "content": owners}], TUTOR)
    assert none == 0 and kept[0]["content"] == owners


def test_a_chinese_slip_reads_as_the_korean_word_with_its_space():
    assert hangul.read_back("의견이 갈리면서本次会议 내 결론에")[0] == \
        "의견이 갈리면서 이번 회의 내 결론에"
    assert hangul.read_back("아래 표에本次会议에서 지정한")[0] == "아래 표에 이번 회의에서 지정한"
    assert hangul.read_back("이번 주 내로补提하여")[0] == "이번 주 내로 보완 제출하여"
    # Korean Hanja reads as before
    assert hangul.read_back("公式적")[0] == "공식적"


def test_a_blank_beside_the_value_it_stands_for_goes():
    assert placeholders.drop_beside_values("- 일시: 2026-10-14(수) [회의 일자]") == \
        "- 일시: 2026-10-14(수)"
    assert placeholders.drop_beside_values("일시: [회의 일자] 2026년 10월 14일") == \
        "일시: 2026년 10월 14일"
    for alone in ("- 일시: [회의 일자]", "| 비교표 | 최다은 | [기한] |", "[연도] 10월 14일"):
        assert placeholders.drop_beside_values(alone) == alone
    tidied = report.final_tidy([{"heading": "회의 개요",
                                 "content": "- 일시: 2026-10-14(수) [회의 일자]"}])
    assert tidied[0]["content"] == "- 일시: 2026-10-14(수)"


def test_the_overview_place_agrees_with_the_head():
    overview = {"heading": "회의 개요",
                "content": "- 일시: 2026-10-14(수)\n- 장소: 미정\n- 참석자: 4명"}
    nxt = {"heading": "다음 회의 계획", "content": "- 장소: 미정"}
    blank = report_checks.minutes_head_place([overview, nxt], {"fields": [["장소", ""]]})
    assert blank[0]["content"] == "- 일시: 2026-10-14(수)\n- 참석자: 4명"
    assert blank[1] == nxt
    given = report_checks.minutes_head_place([overview],
                                             {"fields": [["장소", "본사 5층 대회의실"]]})
    assert "- 장소: 본사 5층 대회의실" in given[0]["content"]


def test_minutes_of_a_weekly_meeting_take_the_minutes_genre():
    rule = report._genre_rule(SALES, doc_formats.BY_ID["minutes"])
    assert rule.startswith("Genre: 회의록")
    assert "사업 | 추진 내용 | 상태" not in rule
    # with no 서식, a 「주간회의」 memo is no weekly report either
    assert not report._genre_rule(SALES).startswith("Genre: 현황·주간 보고")
    assert report._genre_rule("주간 업무 보고를 써 줘").startswith("Genre: 현황·주간 보고")
