"""Minutes tell the memo in their own sentences: a repair that pastes the memo's lines, a
marker alone, a sentence already there or a word left of one is not taken; memo lines and a
phrase said over and over left by the writer go back once and then go when the rest holds their
facts; a cell's note on its working goes, its value and the memo words it quotes kept. Shapes
from the round 16 re-verify (reverify-y tc, tc2)."""

from __future__ import annotations

import asyncio
import json
import time

from app.services import meeting_memo, report_checks, verify

MEMO = ("10/7 기획팀 주간회의. 참석 이민수(팀장), 정하늘, 오세진. 불참 한지우(휴가). "
        "안건1 워크숍 장소: 강릉 vs 속초 논의, 결정 못 함, 다음 회의에서 정하늘이 견적 가져오기. "
        "안건2 예산: 오세진이 다과비 120만원을 요청했으나 팀장이 90만원으로 줄이자고 했고 합의. "
        "커피 머신 얘기 잠깐 있었음. 다음 회의 10/14 화 10시 2층 회의실.")
#: a memo line as it stands
COPIED = "안건2 예산: 오세진이 다과비 120만원을 요청했으나 팀장이 90만원으로 줄이자고 했고 합의."


def _sec(heading: str, content: str) -> dict:
    return {"heading": heading, "content": content}


def test_a_memo_line_in_its_shorthand_reads_as_copied_and_its_sentence_told_does_not():
    for line in (COPIED,
                 "강릉 vs 속초 논의 결과, 결정 못 함.",
                 "다음 회의에서 정하늘이 견적 가져오기.",
                 "커피 머신 얘기 잠깐 있었음.",
                 # a noun-form end inside a finished sentence is a paste's seam
                 "견적은 정하늘이 가져오기로 함 회의에서 더 정할 것은 없습니다."):
        assert meeting_memo.copied(line, MEMO), line
    for told in ("오세진이 다과비 120만원을 요청했으나 팀장이 90만원으로 줄이자고 해 합의했습니다.",
                 "워크숍 장소는 강릉과 속초 가운데 정하지 못했습니다.",
                 "정하늘이 다음 회의에 견적을 가져오기로 했습니다.",
                 # a list item may be shorthand, a table row is judged elsewhere
                 "- 강릉 vs 속초 논의, 결정 못 함",
                 "| 워크숍 장소 | 강릉 vs 속초 논의, 결정 못 함 |"):
        assert not meeting_memo.copied(told, MEMO), told


def _repair(sentence: str, reply: str, content: str) -> str:
    async def complete(model, messages, api_key, *args, **kwargs):
        return json.dumps([reply], ensure_ascii=False), {}

    doc = verify.Doc([_sec("토의 내용", content)], material=MEMO, memo=MEMO)
    finding = verify.Finding(0, sentence, "minutes_unsourced", "자료에 없는 내용이다.")
    sections, left, _ = asyncio.run(verify.repair(doc, [finding], complete=complete,
                                                  model="m", api_key=""))
    return sections[0]["content"]


def test_a_repair_that_pastes_instead_of_writing_is_not_taken():
    said = "워크숍 장소는 강릉과 속초 가운데 정하지 못했습니다."
    made = "두 곳의 숙박 여건을 비교해 본 결과가 없어 판단을 미뤘습니다."
    content = f"{said} {made}"
    for reply in ("강릉 vs 속초 논의 결과, 결정 못 함.",       # the memo's line
                  "확인 필요",                               # a marker alone
                  said,                                      # a sentence already there
                  "함."):                                    # a word left of a sentence
        assert _repair(made, reply, content) == content, reply
    # a sentence of its own is taken
    plain = "장소는 다음 회의에서 정하기로 했습니다."
    assert _repair(made, plain, content) == f"{said} {plain}"


def test_memo_lines_and_a_phrase_said_over_go_back_once_then_go_when_their_facts_stand():
    sections = [
        _sec("결정 사항", "다과비는 오세진의 120만원 요청에서 90만원으로 줄여 합의했습니다."),
        _sec("토의 내용", "워크숍 장소는 강릉과 속초를 두고 논의했습니다. "
                       "강릉 vs 속초 논의 결과, 결정 못 함. "
                       + COPIED),
        _sec("남은 쟁점", "워크숍 장소는 정하지 못했으며, 정하늘이 견적을 가져옵니다. "
                       "워크숍 장소는 정하지 못했으며, 다음 회의에서 다시 봅니다. "
                       "워크숍 장소는 정하지 못했으며, 견적이 열쇠입니다."),
    ]
    doc = verify.Doc(sections, material=MEMO, memo=MEMO)
    found = report_checks.minutes_pasted(doc)
    assert {f.code for f in found} == {"minutes_copy"}
    assert any(f.sentence.startswith("안건2 예산") for f in found)
    copy = next(f for f in found if f.sentence.startswith("안건2 예산"))
    assert copy.still(copy.sentence) and not copy.still("다과비는 90만원으로 합의했습니다.")
    out, gone = meeting_memo.settle_copies(sections, MEMO, strong=False)
    body = "\n".join(s["content"] for s in out)
    assert "안건2 예산" not in body and "결과, 결정 못 함." not in body
    assert body.count("워크숍 장소는 정하지 못했으며") == 1
    assert "정하늘이 견적을 가져옵니다." in body
    assert "워크숍 장소는 강릉과 속초를 두고 논의했습니다." in body
    assert "120만원" in body and "90만원" in body
    # a copy holding a number nowhere else stays
    alone = [_sec("토의 내용", COPIED)]
    assert meeting_memo.settle_copies(alone, MEMO, strong=False)[0] == alone
    # a strong writer's sentences stand but the one said twice word for word
    twice = "장소는 다음 회의에서 정합니다."
    kept, _ = meeting_memo.settle_copies(
        [*sections, _sec("다음 회의", f"{twice} 견적을 봅니다. {twice}")], MEMO, strong=True)
    kept_body = "\n".join(s["content"] for s in kept)
    assert "안건2 예산" in kept_body and kept_body.count(twice) == 1
    assert kept_body.count("워크숍 장소는 정하지 못했으며") == 3


def test_a_cell_repeating_an_open_items_phrase_reads_undecided():
    table = ("| 안건 | 결과 | 비고 |\n| --- | --- | --- |\n"
             "| 워크숍 장소 | 강릉 vs 속초 논의, 결정 못 함 | - |\n"
             "| 숙소 | 강릉 vs 속초 논의, 결정 못 함 | - |\n"
             "| 교통편 | 강릉 vs 속초 논의, 결정 못 함 | - |")
    out, _ = meeting_memo.settle_copies([_sec("안건별 결과", table)], MEMO, strong=False)
    rows = out[0]["content"].split("\n")
    assert rows[2].endswith("강릉 vs 속초 논의, 결정 못 함 | - |")
    assert rows[3] == "| 숙소 | 미정 | - |" and rows[4] == "| 교통편 | 미정 | - |"


def test_a_cells_note_on_its_working_goes_and_its_value_and_quote_stay():
    table = ("| 할 일 | 담당 | 기한 | 비고 |\n| --- | --- | --- | --- |\n"
             "| 견적 가져오기 | 정하늘 | 2026-10-14(화) | 「다음 회의에서」 기준 날짜 해석 |\n"
             "\n| 항목 | 내용 |\n| --- | --- |\n"
             "| 일시 | 2026-10-07 (기준일: 대화 날짜에 가장 가까운 연도로 해석) |\n"
             "| 다음 일시 | 2026-10-14 (원문 「화」 기재 — 기준일 10-07은 화요일이라 확인 필요) |\n"
             "| 장소 | 2층 회의실 |")
    out, fixed = meeting_memo.cell_notes([_sec("회의 개요", table)], MEMO)
    rows = out[0]["content"].split("\n")
    assert fixed == 3
    assert rows[2] == "| 견적 가져오기 | 정하늘 | 2026-10-14(화) | 원문 「다음 회의에서」 |"
    assert "| 일시 | 2026-10-07 |" in rows
    assert "| 다음 일시 | 2026-10-14 (확인 필요) |" in rows
    assert "| 장소 | 2층 회의실 |" in rows


def test_a_marker_left_alone_on_its_line_goes():
    content = "장소는 정하지 못했습니다.\n\n확인 필요\n\n(확인 필요)\n\n견적은 확인 필요합니다."
    out, gone = meeting_memo.bare_marks([_sec("남은 쟁점", content)])
    assert gone == 2
    assert out[0]["content"] == "장소는 정하지 못했습니다.\n\n견적은 확인 필요합니다."


def test_the_paste_checks_finish_fast_on_a_long_run_of_spaces():
    gap = " " * 50_000
    start = time.perf_counter()
    meeting_memo.copied(f"강릉{gap}함.", MEMO)
    cell = f"| 항목 | 내용 |\n| --- | --- |\n| 일시 | 10/7 ({gap}) |"
    meeting_memo.cell_notes([_sec("표", cell)], MEMO)
    meeting_memo.bare_marks([_sec("끝", f"확인{gap}필요")])
    meeting_memo.settle_copies([_sec("끝", f"강릉 vs 속초{gap}결정 못 함.")], MEMO, strong=False)
    verify.pasted("원래 문장입니다.", f"확인{gap}필요", "원래 문장입니다.", MEMO)
    assert time.perf_counter() - start < 0.5
