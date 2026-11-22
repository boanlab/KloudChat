"""A deck asked for 「OX 퀴즈 3문제」 shows three questions, and no speaker note stops mid-way."""

from __future__ import annotations

from app.services import deck

ASK = ("신입사원 입문 교육용 \"개인정보보호 기본\" 발표 자료를 10장 안팎으로 만들어 줘. "
       "마지막에 OX 퀴즈 3문제를 넣어 줘.")


def _deck(bullets: list[str]) -> list[dict]:
    return [{"id": "a", "layout": "title", "title": "개인정보보호 기본"},
            {"id": "b", "layout": "bullets", "title": "개인정보란", "bullets": ["정의"]},
            {"id": "c", "layout": "bullets", "title": "OX 퀴즈로 확인하기", "bullets": bullets}]


def test_the_request_names_how_many_questions() -> None:
    assert deck.asked_questions(ASK) == 3
    assert deck.asked_questions("마지막에 퀴즈 다섯 문항을 넣어 줘") == 5
    assert deck.asked_questions("퀴즈 한 장 넣어 줘") == 0


def test_a_quiz_short_of_its_questions_is_sent_back() -> None:
    slides = _deck(["Q1. 사내 메신저로 고객 연락처를 보내도 되는가?",
                    "Q3. 사고 발생 시 혼자 해결해도 되는가?"])
    needs = deck.requested_contents(slides, ASK)
    assert 2 in needs and "3 questions" in needs[2] and "shows 2" in needs[2]


def test_a_quiz_with_every_question_passes() -> None:
    slides = _deck(["① 이름만 있어도 개인정보다 O X", "② 메신저로 주민번호 전송 O X",
                    "③ 외부 AI에 원본 업로드 O X"])
    assert deck.requested_contents(slides, ASK) == {}


def test_questions_spread_over_quiz_slides_count_together() -> None:
    slides = [*_deck(["Q1. 출력물은 바로 가져가야 한다?"]),
              {"id": "d", "layout": "bullets", "title": "OX 퀴즈 2", "bullets": ["Q2. …인가?"]},
              {"id": "e", "layout": "bullets", "title": "OX 퀴즈 3", "bullets": ["Q3. …인가?"]}]
    assert deck.requested_contents(slides, ASK) == {}


def test_a_note_broken_off_before_its_verb_is_no_speech() -> None:
    assert deck.finished_notes("특히 무게 감소는 고객 불만 1위를 정면으로 돌파한 결과임을") == ""
    cut = "무게를 줄였습니다. 특히 배터리는 고객 불만 2위를"
    assert deck.finished_notes(cut) == "무게를 줄였습니다."


def test_a_phrase_after_the_last_sentence_is_cut_back() -> None:
    note = "안녕하세요. 오늘은 「클린윙 S2」에 대해 발표하겠습니다. 경영진 보고용 출시 전략 개요"
    assert deck.finished_notes(note) == "안녕하세요. 오늘은 「클린윙 S2」에 대해 발표하겠습니다."
    assert deck.finished_notes("이 장은 꼭 기억해 주세요") == "이 장은 꼭 기억해 주세요"
    assert deck.finished_notes("이 점이 중요합니다") == "이 점이 중요합니다"


def test_the_cover_note_is_a_greeting_without_the_subtitle_phrase() -> None:
    slides = [{"id": "a", "layout": "title", "title": "클린윙 S2 출시 전략"},
              *[{"id": str(i), "layout": "bullets", "title": f"장 {i}", "bullets": ["항목"],
                 "notes": "설명합니다. 다음 내용입니다."} for i in range(4)]]
    deck.fill_missing_notes(slides, "발표 자료 만들어 줘", title="클린윙 S2 출시 전략",
                            subtitle="경영진 보고용 출시 전략 개요")
    assert slides[0]["notes"] == "안녕하세요. 오늘은 「클린윙 S2 출시 전략」에 대해 발표하겠습니다."


def test_a_note_that_is_the_writers_json_is_no_speech() -> None:
    slides = [{"id": "a", "layout": "bullets", "title": "신고 절차", "bullets": ["즉시 보고"],
               "notes": '[ { "type": "title'}]
    deck.strip_internal_text(slides)
    assert slides[0]["notes"] == ""
