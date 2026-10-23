"""A new document is written in the language the person asked in, not the material's: an
English memo sent alone is English, a Korean ask over an English memo Korean. Shapes from the
round 17 checks (audit-x2 H2: GLM and 27B wrote an English memo's minutes in Korean)."""

from __future__ import annotations

import time

from app.pipeline import save_stages
from app.services import output_language

ENGLISH_MEMO = (
    "Oct 19 Product Design weekly sync. Attendees: Maya Chen (lead), Arjun Patel, Lena "
    "Fischer, Tom Okafor. 1) Onboarding: we moved the launch to Nov 11. Arjun will finish the "
    "event tracking spec by Oct 28.")
KOREAN_MEMO = ("- 월: 신규 거래처 1차 미팅. 월 발주 예상 3,200만원.\n"
               "- 화: 재고 클레임 2건 처리 완료.")


def test_the_asked_language_is_the_persons_own_words_not_the_material():
    assert output_language.asked(ENGLISH_MEMO) == "en"
    assert output_language.asked("아래 메모로 회의록을 만들어줘.\n\n" + ENGLISH_MEMO) == "ko"
    assert output_language.asked(ENGLISH_MEMO + "\n\n위 메모로 회의록 정리해줘") == "ko"
    assert output_language.asked("회의록 만들어줘: " + ENGLISH_MEMO) == "ko"
    asked = output_language.asked("Write the weekly report from this memo.\n\n" + KOREAN_MEMO)
    assert asked == "en"
    assert output_language.asked("Q3 OKR 보고서 만들어줘") == "ko"
    # conditions the server adds are no words of the person's
    assert output_language.asked("Make a deck on Q3 results.\n\n덧붙인 조건:\n- 8장") == "en"
    assert output_language.asked("") == ""
    assert "English" in output_language.asked_condition("en")
    assert "Korean" in output_language.asked_condition("ko")
    assert output_language.asked_condition("") == ""


def test_the_finish_line_is_in_the_documents_language():
    minutes = {"format": "minutes"}
    said = save_stages._written_note(5, minutes, english=True)
    assert said == "Wrote the minutes in 5 sections."
    assert save_stages._written_note(1, None, english=True) == "Wrote the report in 1 section."
    assert save_stages._written_note(5, minutes) == "5개 절로 회의록 작성을 마쳤습니다."


def test_a_fresh_english_deck_is_answered_in_english():
    from app.routers import sessions

    slides = [{"layout": "title", "title": "Can small on-premise models replace cloud APIs"},
              {"layout": "bullets", "title": "Cost and latency benchmark results",
               "bullets": ["Cost per 1,000 docs is $4 against $38 for the cloud model"]}]
    assert sessions._restructured_said([], slides, english=True).startswith("Made a 2-slide")


def test_the_asked_language_reads_linearly_on_long_spaces():
    started = time.monotonic()
    output_language.asked(" " * 50_000 + "a." + " " * 50_000 + "b")
    assert time.monotonic() - started < 0.5
