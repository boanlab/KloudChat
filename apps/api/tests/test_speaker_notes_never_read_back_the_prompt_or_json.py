"""Speaker notes hold what the presenter says: never the prompt's own lines, the request's ask
pasted back, talk about the output, a JSON list run into the note, or a stray closing quote.

Round 3 (11-21) base decks: 「(발표자 노트) 두 문장만 출력합니다.」 (r3d05 SA); a note that was the
request itself, 「공공기관 설계 공모 … 만들어 줘. 덧붙인 조건: 있는 자료로 진행해 주세요.」 (r3d01 SA);
「Do not repeat the screen's sentences; choose among why it matters …」 (r3d10 SL); 「… 발굴
방법", "3. 중간 점검 및 멘토링", … ], "notes": "…」 (r3d08 SL); 「… 잡아줍니다."」 (r3d08 SA)."""

from __future__ import annotations

from app.services import deck

REQUEST = ("구청 민원실 직원 대상 친절 응대 교육 발표 자료를 8장으로 만들어 줘.\n"
           "- 대상: 민원실 직원 24명\n- 시간: 50분\n발표자 노트도 넣어 줘.\n\n"
           "덧붙인 조건: 있는 자료로 진행해 주세요. 사용자가 더 줄 정보가 없다고 했다.")


def test_a_note_that_reads_back_the_ask_or_the_prompt_is_dropped():
    prompt = deck._NOTES_PROMPT.format(heading="", shown="", neighbours="", language="",
                                      request=deck.ask_lines(REQUEST))
    said = ("구청 민원실 직원 대상 친절 응대 교육 발표 자료를 8장으로 만들어 줘. 덧붙인 조건: 있는 자료로 진행해 주세요. "
            "민원인의 첫마디를 끝까지 듣는 것이 응대의 시작입니다.")
    assert deck.notes_without_prompt(said, prompt) == "민원인의 첫마디를 끝까지 듣는 것이 응대의 시작입니다."
    leaked = ("Do not repeat the screen's sentences; choose among why it matters, the one thing the "
              "audience should remember. Keep eye contact with each visitor.")
    assert deck.notes_without_prompt(leaked, prompt) == "Keep eye contact with each visitor."
    assert deck.notes_without_prompt("두 문장만 출력합니다.", prompt) == ""


def test_a_note_keeps_the_requests_facts():
    prompt = deck.ask_lines(REQUEST)
    said = "오늘 교육은 민원실 직원 24명이 함께 50분 동안 진행합니다."
    assert deck.notes_without_prompt(said, prompt) == said


def test_a_json_list_run_into_a_note_and_a_stray_quote_are_cut():
    slides = [
        {"title": "일정", "notes": '오전과 오후 운영 시간을 비교합니다. 팀 규칙과 발굴 방법", "3. 중간 점검", '
                                  '"4. 최종 발표" ], "notes": "전체 순서를 안내합니다.'},
        {"title": "점검", "notes": '점심 이후에는 중간 점검으로 방향을 잡아 줍니다."'},
    ]
    deck.strip_json_residue(slides)
    assert slides[0]["notes"] == "오전과 오후 운영 시간을 비교합니다."
    assert slides[1]["notes"] == "점심 이후에는 중간 점검으로 방향을 잡아 줍니다."
