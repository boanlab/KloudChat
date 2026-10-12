"""Round-2 R2W-05: a date the request's own rule settles is the one that ships. A date a few
days off the rule's, written with the rule day's weekday (「2. 1.(일)」 for 1/31(일)), is the
slip — its date is set, its weekday kept; a 요일 column is read against its row's date; a row
named like a rule's line gets the rule's period; and the writer is given those dates by code,
with 「오늘부터 며칠」 counted from the server's date (sc/n01_scholar, sc/n02_haksa)."""

from __future__ import annotations

import asyncio
import datetime as dt

from app.pipeline import registry
from app.pipeline.bundle import Bundle, Material, Request
from app.pipeline.stage import Context
from app.services import date_rules, skill_scripts, weekdays

TODAY = dt.date(2026, 10, 12)

_SCHOLAR_REQUEST = (
    "첨부한 장학금 지급 규정을 근거로 「2027학년도 1학기 교내 장학금(성적우수·가계곤란) 신청 안내」 "
    "공고문을 학생 대상으로 써 줘. 2027학년도 1학기 개강일은 2027. 3. 2.(화)이야. 신청 기간은 "
    "규정대로 계산해서 날짜와 요일까지 넣어 주고, 자격·지급액·중복 지급 제한·유의사항(허위 서류)을 "
    "빠짐없이."
)
_RULE = "제7조(신청 기간) 장학금 신청은 매 학기 개시 30일 전부터 14일 전까지 학생포털로 한다."

_HAKSA_REQUEST = """학생들한테 보낼 2027학년도 1학기 주요 학사일정 안내문 써 줘. 날짜마다 요일도 붙여서.
- 수강신청: 2027. 2. 15. ~ 2. 18.
- 개강: 2027. 3. 2.
- 수강신청 정정: 개강일부터 5일간(주말 포함 아님, 수업일 기준)
- 수강포기 신청: 개강 후 8주차 월요일부터 3일간
- 중간고사: 2027. 4. 20. ~ 4. 26.
- 종강: 2027. 6. 21.
그리고 마지막 줄에 '정정 마감일까지 오늘부터 며칠 남았는지'도 써 줘."""

#: the shipped table (sc/n02_haksa/artifact_c12f97.md)
_HAKSA_TABLE = """| 구분 | 날짜 | 요일 | 비고 |
| --- | --- | --- | --- |
| 수강신청 | 2027. 2. 15. ~ 2. 18. | 월~목 | [2] |
| 개강 | 2027. 3. 2. | 월 | [1] |
| 수강신청 정정 | 2027. 2. 28. ~ 3. 5. | 토~금(수업일 기준 5일) | [1] |
| 수강포기 신청 | [8주차 월요일]부터 3일간 | [요일 미상] | |
| 중간고사 | 2027. 4. 20. ~ 4. 26. | 화~월 | |"""


def test_a_date_off_the_rule_with_the_rule_days_weekday_takes_the_rule_date():
    # the writer's text before normalize bent (일) to (월) (runs.json normalize)
    text = ("신청 기간은 2027. 2. 1.(일)부터 2027. 2. 16.(월)까지이며, 접수 기간은 2027년 2월 "
            "1일(일)부터 2027년 2월 16일(월)까지입니다.")
    fixed, fixes = weekdays.fix_weekdays(text, context=f"{_SCHOLAR_REQUEST}\n{_RULE}",
                                         today=TODAY)
    assert fixed == ("신청 기간은 2027. 1. 31.(일)부터 2027. 2. 16.(화)까지이며, 접수 기간은 "
                     "2027년 1월 31일(일)부터 2027년 2월 16일(화)까지입니다.")
    assert len(fixes) == 4
    # with no rule the date stands and the weekday follows it, as before
    assert weekdays.fix_weekdays("2027. 2. 1.(일)", context=_SCHOLAR_REQUEST, today=TODAY)[0] \
        == "2027. 2. 1.(월)"


def test_a_weekday_column_and_rule_rows_are_set_from_the_dates():
    fixed, _ = weekdays.fix_weekdays(_HAKSA_TABLE, context=_HAKSA_REQUEST, today=TODAY)
    rows = {line.split("|")[1].strip(): [c.strip() for c in line.split("|")[2:4]]
            for line in fixed.splitlines()[2:]}
    assert rows["수강신청"] == ["2027. 2. 15. ~ 2. 18.", "월~목"]
    assert rows["개강"] == ["2027. 3. 2.", "화"]
    # 3/1 is 삼일절: five class days from 3/2(화) end on 3/8(월)
    assert rows["수강신청 정정"] == ["2027. 3. 2. ~ 3. 8.", "화~월(수업일 기준 5일)"]
    # the week of 개강 is week 1: week 8's Monday is 4/19
    assert rows["수강포기 신청"] == ["2027. 4. 19. ~ 4. 21.", "월~수"]
    assert rows["중간고사"] == ["2027. 4. 20. ~ 4. 26.", "화~월"]


def test_the_writer_is_given_the_rule_dates_and_the_days_left_from_today():
    block = date_rules.block(_HAKSA_REQUEST, "", TODAY)
    assert "- 수강신청 정정: 2027. 3. 2.(화) ~ 2027. 3. 8.(월)" in block
    assert "- 수강포기 신청: 2027. 4. 19.(월) ~ 2027. 4. 21.(수)" in block
    assert "수강신청 정정 마지막 날 2027. 3. 8.(월)까지: 147일" in block
    scholar = date_rules.block(_SCHOLAR_REQUEST, _RULE, TODAY)
    assert "2027. 1. 31.(일)" in scholar and "2027. 2. 16.(화)" in scholar
    assert "며칠" not in scholar and "오늘" not in scholar
    assert date_rules.block("회의록 양식 알려 줘", "", TODAY) == ""


def test_the_report_writer_notes_carry_the_rule_dates():
    bundle = Bundle(request=Request(text=_SCHOLAR_REQUEST, model="w", api_key="k", today=TODAY),
                    material=Material(given=_RULE))
    asyncio.run(registry.stage("writer-notes").apply(bundle, Context()))
    texts = [p.text for p in bundle.material.parts]
    assert any("학기 개시 30일 전: 2027. 1. 31.(일)" in t for t in texts)
    assert any("작성 메모" in t for t in texts)


def test_a_chat_answers_run_end_is_set_to_the_class_day_count():
    out = skill_scripts.dates().post(
        "정정 기간은 2027. 3. 2.(화)부터 2027. 3. 5.(금)까지입니다.",
        {"request": _HAKSA_REQUEST, "today": TODAY.isoformat(), "anchored_only": True})
    assert out["text"] == "정정 기간은 2027. 3. 2.(화)부터 2027. 3. 8.(월)까지입니다."
