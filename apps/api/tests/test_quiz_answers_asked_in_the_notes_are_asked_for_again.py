"""A quiz whose answers the request puts in the speaker notes is rewritten once when its notes
give fewer answers than it has questions.

Rounds 2-3 r2d02, r3d02 (lesson decks): 「마지막 장은 퀴즈 3문제, 퀴즈 정답과 해설은 발표자 노트에」
— the quiz slide's notes only led into the quiz (「첫 번째는 …입니다. 답을 생각해 보세요.」)."""

from __future__ import annotations

from app.services import deck

ASK = ("초등학교 5학년 과학 수업용 '물의 순환' 발표 자료를 7장으로 만들어 줘. 마지막 장은 퀴즈 3문제, "
       "퀴즈 정답과 해설은 발표자 노트에 넣어 줘.")


def _deck(notes: str) -> list[dict]:
    return [
        {"title": "물의 순환", "layout": "title"},
        {"title": "증발과 응결", "layout": "bullets", "bullets": ["햇볕에 물이 수증기가 된다"]},
        {"title": "오늘의 퀴즈", "layout": "bullets", "notes": notes,
         "bullets": ["1. 물이 수증기로 바뀌는 것은? ① 증발 ② 응결", "2. 구름은 무엇이 모인 것일까요?",
                     "3. 비가 땅에 스며드는 것을 무엇이라 하나요?"]},
    ]


def test_notes_without_the_answers_get_the_quiz_rewritten():
    needs = deck.requested_contents(_deck("세 문제를 풀어 보겠습니다. 답을 생각해 보세요."), ASK)
    assert 2 in needs and "answer" in needs[2]


def test_notes_with_every_answer_stand():
    notes = "1번 정답: ① 증발 — 열을 받아 기체가 됩니다. 2번 정답: 작은 물방울. 3번 정답: 침투."
    assert 2 not in deck.requested_contents(_deck(notes), ASK)


def test_a_quiz_without_that_ask_is_left_alone():
    ask = "초등학교 5학년 과학 수업용 '물의 순환' 발표 자료를 7장으로 만들어 줘. 마지막 장은 퀴즈 3문제."
    assert 2 not in deck.requested_contents(_deck("세 문제를 풀어 보겠습니다."), ask)
