"""A professor's research plan is not headed 「지도교수」.

In the 연구·조사 계획서 head, 지도교수 and 연구 기간 are shown only when the
request or its material gives them; a student's plan that names an advisor keeps the row.
"""

from __future__ import annotations

from app.services import doc_formats

PROFESSOR = (
    "한국연구재단 개인기초연구(중견연구) 연구계획서 초안을 써 줘. 주제는 \"대형 언어모델 기반 "
    "학술 문헌 요약의 사실 일관성 평가 방법 연구\"이고, 연구기간은 3년."
)


def _fields(request: str, answer: dict) -> dict[str, str]:
    fmt = doc_formats.detect(request)
    assert fmt is not None and fmt.id == "plan"
    return dict(doc_formats.title_block(fmt, answer, request)["fields"])


def test_a_professors_plan_has_no_advisor_row():
    fields = _fields(PROFESSOR, {"fields": {"연구 기간": "3년", "지도교수": "김교수"}})
    assert "지도교수" not in fields
    assert fields["연구 기간"] == "3년"
    # The fields every plan carries stay, blank when not given.
    assert fields["연구자"] == "" and fields["작성일"] == ""


def test_a_students_plan_that_names_an_advisor_keeps_it():
    request = "졸업 작품 계획서 써 줘. 지도교수는 박지훈 교수님이야."
    fields = _fields(request, {"fields": {"지도교수": "박지훈"}})
    assert fields["지도교수"] == "박지훈"
    assert "연구 기간" not in fields
