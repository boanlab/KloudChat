"""Grounding checks that keep short dates, time ranges, shared values and worked totals."""

from __future__ import annotations

import datetime as dt

from app.services import agent, skill_scripts

TRACE = skill_scripts.entry("grounding-check")

#: the attached calendar's lines
CALENDAR = """한빛대학교 2027학년도 1학기 학사일정
2027-02-03 ~ 2027-02-05  신입생 수강신청 (09:00 시작, 마지막 날 17:00 마감)
2027-02-15 ~ 2027-02-19  1학기 등록(등록금 납부)
2027-02-22               신입생 오리엔테이션 (10:00, 대강당)"""


def _blanked(answer: str, material: str, **options) -> str:
    found = TRACE.check(answer, material, [], {"person": material, **options})
    return TRACE.fix(answer, found)


def test_a_day_the_person_gave_is_kept_without_its_year():
    answer = ("**1. 수강신청 — 2. 3.(수) 09:00 ~ 2. 5.(금) 17:00**\n"
              "**2. 등록금 납부 — 2. 15.(월) ~ 2. 19.(금)**\n오리엔테이션은 2. 22.(월)입니다.")
    assert _blanked(answer, CALENDAR) == answer


def test_a_bold_title_whose_dates_are_blanked_keeps_its_words():
    answer = "**2. 등록금 납부 — 2. 16.(화) ~ 2. 18.(목)**\n기간 내 미등록은 제적 사유입니다."
    assert _blanked(answer, CALENDAR) == (
        "**2. 등록금 납부 — [일자](화) ~ [일자](목)**\n기간 내 미등록은 제적 사유입니다.")


def test_a_speakers_line_does_not_move_the_meeting_date():
    transcript = (
        "[자동 전사] 2026-10-08(목) 15:02 시작 · 본관 4층 소회의실 · 4차 회의\n"
        "김민석: 네. 연수는 원래 11월 5일 수요일로 잡았는데요, 그날 교무회의가 잡혀서 어렵습니다.\n"
        "정하윤: 그럼 이달 말까지 정리해 주세요.\n")
    rows = skill_scripts.dates().scan(transcript, dt.date(2026, 10, 12))
    month_end = next(r for r in rows if r["text"] == "이달 말")
    assert month_end["base_date"] == "2026-10-08" and month_end["date"] == "2026-10-31"
    # a header field still sets it
    rows = skill_scripts.dates().scan("일시: 2026-11-05(목) 교무회의\n내일까지 회신", None)
    assert next(r for r in rows if r["text"] == "내일")["date"] == "2026-11-06"


def test_a_sentence_whose_values_two_rows_share_is_left_as_written():
    printed = ("stdout:\n=== 자연과학 ===\n좌석 부족: 22\n와이파이: 18\n"
               "=== 공학 ===\n좌석 부족: 41\n와이파이: 31")
    answer = ("자연과학·공학은 좌석 부족(각 22·41건)이 1순위입니다.\n\n## 공학\n\n"
              "41건이 좌석 부족을, 31건이 와이파이를 꼽았습니다.")
    checked, fixes = agent.code_checked(answer, [printed])
    assert checked == answer and fixes == []
    # one row and its one value is still set
    checked, _ = agent.code_checked("공학은 좌석 부족 40건이 1순위입니다.", [printed])
    assert checked == "공학은 좌석 부족 41건이 1순위입니다."


def test_a_span_of_hours_and_its_other_time_zone_are_given_times():
    request = ("다음 단계: 11월 중 30분 화상 회의 제안. 우리는 한국 시간 평일 오후 5~8시 가능 "
               "(영국과 시차 9시간)")
    answer = ("We are available 8:00–11:00 a.m. UK time (5:00–8:00 p.m. KST). "
              "한국 시간 오후 5시부터 가능합니다.")
    assert _blanked(answer, request) == answer


def test_a_verb_before_a_title_is_not_a_name():
    material = "분반 A와 분반 B는 담당 교수와 영상 길이가 모두 다르다."
    answer = "분반별 담당 교수가 달라 교수 효과와 영상 길이 효과가 분리되지 않는다."
    assert _blanked(answer, material) == answer


def test_totals_and_gaps_worked_from_the_documents_own_table_stand():
    material = ("국내여비 내역: 1차 320,000원, 2차 410,000원, 3차 220,000원\n"
                "만족도: 실험반 7.6점, 대조반 7.1점")
    answer = ("| 구분 | 금액(원) |\n|---|---:|\n| 1차 | 320,000 |\n| 2차 | 410,000 |\n"
              "| 3차 | 220,000 |\n| 소계 | 950,000 |\n\n"
              "국내여비는 950,000원을 집행했습니다. 실험반이 대조반보다 0.5점 높았습니다.")
    assert _blanked(answer, material) == answer


def test_an_amounts_words_follow_its_figure():
    material = "강사료 400만 원 20명, 보조 200만 원 10명"
    blanked = _blanked("총 소요 예산: 금 2,120,000원(금이백십이만원)", material)
    assert blanked == "총 소요 예산: 금 [수치]"
    assert TRACE.ko_spelled("이백십이만") == 2_120_000 and TRACE.spell_ko(3_200_000) == "삼백이십만"
    # the figure kept and its words wrong: the words are the figure's
    assert TRACE._spelled_amounts("금 3,200,000원(금이백십이만원)") \
        == "금 3,200,000원(금삼백이십만원)"
