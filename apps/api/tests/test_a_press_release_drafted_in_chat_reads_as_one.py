"""A document drafted in chat reaches its reader without a doubled title or notes on its writing."""

from __future__ import annotations

from app.services.chat_format import draft_shape

ASK = "보도자료 초안 써줘. 회사: 노바랩스, 대표 이름은 이서연. 문의처는 pr@novalabs.example."

DRAFT = """# 노바랩스, 농협과 손잡고 딸기 농가 30곳에 토양 센서 시범 설치

## 보도자료

**노바랩스, 농협과 협력해 토양 센서 시범 사업 착수**

노바랩스(대표 이서연)가 농협과 협력해 전남 딸기 농가 30곳에 토양 센서를 시범 설치합니다.

## 문의처

노바랩스 대표 이서연
이메일: pr@novalabs.example

---

**작성 시 반영한 점**
- 제목·본문·문의처·회사 소개를 구분해 배치했습니다.
- 대표 이서연, 문의처 pr@novalabs.example를 그대로 기재했습니다.

보내실 매체나 분량에 맞춰 제목이나 본문 톤을 조정해 드릴 수 있습니다."""


def test_the_genre_heading_moves_above_the_title_and_the_writing_notes_go():
    shaped = draft_shape(DRAFT, ASK)
    assert shaped.startswith("**보도자료**\n\n# 노바랩스, 농협과 손잡고")
    assert "## 보도자료" not in shaped
    assert "작성 시 반영한 점" not in shaped and "그대로 기재했습니다" not in shaped
    assert "---" not in shaped
    # the draft's own parts and the closing offer stay
    assert ("## 문의처\n\n노바랩스 대표 이서연\n이메일: pr@novalabs.example\n\n보내실 매체나"
            in shaped)
    assert draft_shape(shaped, ASK) == shaped


def test_an_answer_that_is_no_drafted_document_is_left_as_written():
    assert draft_shape(DRAFT, "농협 협력 소식 어떻게 생각해?") == DRAFT
    # a list in the draft's body named like notes, but before its end, stays
    body = "# 안내\n\n**반영 사항**\n- 12월 시작\n- 6개월\n\n" + "본문 문장입니다.\n\n" * 6
    assert draft_shape(body, ASK) == body
