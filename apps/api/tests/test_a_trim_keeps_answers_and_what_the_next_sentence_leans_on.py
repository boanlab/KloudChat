"""Restatement trim keeping FAQ lead sentences and what the next sentence leans on."""

from __future__ import annotations

from app.services import report

EARLIER = [
    "성적우수장학금은 직전 학기 12학점 이상 이수 및 평점평균 3.8 이상을 충족한 재학생을 "
    "대상으로 합니다. 신청은 매 학기 개시 30일 전부터 14일 전까지 학생포털을 통해 진행합니다."
]


def test_the_answer_to_a_question_heading_keeps_its_lead_sentence():
    body = ("장학금 신청은 매 학기 개시 30일 전부터 14일 전까지 학생포털을 통해 진행합니다. "
            "신청 기간을 놓치면 그 학기 장학금을 받을 수 없습니다.\n")
    assert report.QUESTION_HEADING.search("Q7. 장학금 신청 기간과 방법은 무엇인가요?")
    kept, cut = report.trim_restatements(body, EARLIER, question=True)
    assert cut == [] and "30일 전부터 14일 전까지" in kept
    # The same body under a plain heading still loses the restatement.
    _, cut = report.trim_restatements(body, EARLIER)
    assert cut and "30일 전부터" in cut[0]


def test_a_question_line_inside_a_section_keeps_its_answer():
    body = ("**Q2. 성적우수장학금의 선발 기준은?**\n\n"
            "성적우수장학금은 직전 학기 12학점 이상, 평점평균 3.8 이상인 재학생입니다. "
            "F학점이 있으면 제외됩니다.\n")
    kept, cut = report.trim_restatements(body, EARLIER)
    assert cut == [] and "12학점 이상, 평점평균 3.8 이상" in kept


def test_a_sentence_the_next_one_leans_on_is_not_cut():
    body = ("자격은 직전 학기 12학점 이상 이수와 평점평균 3.8 이상입니다. "
            "이 기본 자격 요건은 제3조 제1항에 따라 적용됩니다.\n")
    kept, cut = report.trim_restatements(body, EARLIER)
    assert cut == [] and kept == body
    listed = ("공통 축은 세 가지다. 첫째, 직전 학기 12학점 이상 이수와 평점평균 3.8 이상이다. "
              "둘째, 소득구간이다.\n")
    kept, cut = report.trim_restatements(listed, EARLIER)
    assert "첫째," in kept and cut == []
    # A restatement nothing leans on still goes.
    alone = ("자격은 직전 학기 12학점 이상 이수와 평점평균 3.8 이상입니다. "
             "소득 증빙은 별도로 냅니다.\n")
    kept, cut = report.trim_restatements(alone, EARLIER)
    assert len(cut) == 1 and kept.strip() == "소득 증빙은 별도로 냅니다."
