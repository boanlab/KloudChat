"""Minutes state the value a meeting decided, not the one it changed (「80만원을 요청했으나 50만원
으로 줄이자고 했고 합의」 → 50만원), print a paragraph the writer fenced as their own text, fill a
blank where it stands instead of saying the sentence twice, and keep the memo's bare clock when
the writer put 오전 before it. Shapes from the round 16 re-verify (reverify-za tc, tc_b)."""

from __future__ import annotations

import asyncio
import json
import time

from test_minutes_say_only_what_the_memo_says import TUTOR

from app.services import key_figures, meeting_memo, report, stated_meeting, verify


def _sec(heading: str, content: str) -> dict:
    return {"heading": heading, "content": content}


GIVEN = "안건2 예산: 박민호가 간식비 80만원을 요청했으나 팀장이 50만원으로 줄이자고 했고 합의."


def test_an_item_the_meeting_gave_a_new_value_holds_its_old_one_only_under_its_full_name():
    reply = json.dumps([{"name": "간식비 요청액", "value": "80만원",
                         "aliases": ["요청예산", "간식비"]}], ensure_ascii=False)
    figures, _ = key_figures.parse_all(reply, GIVEN)
    assert [f.name for f in figures] == ["간식비 요청액"] and figures[0].aliases == []
    # the agreed 50만원 is no settled figure changed
    assert not key_figures.conflicts("간식비를 50만원으로 합의했습니다.", figures)
    assert key_figures.conflicts("간식비 요청액은 60만원입니다.", figures)
    assert key_figures.changes(GIVEN)[0][1:3] == (800000.0, 500000.0)
    # an item no change touches keeps its aliases
    plain = json.dumps([{"name": "모집 인원", "value": "30명", "aliases": ["인원"]}],
                       ensure_ascii=False)
    assert key_figures.parse_all(plain, "모집 인원 30명으로 확정.")[0][0].aliases == ["인원"]


def test_a_decision_given_the_value_the_meeting_changed_takes_the_new_one():
    content = ("1. 모집 기간을 11/3~11/14로 확정했습니다.\n"
               "2. 간식비를 80만원으로 합의했습니다.\n\n"
               "| 번호 | 결정 사항 |\n|---|---|\n| 1 | 간식비 80만원 |\n\n"
               "박민호가 간식비 80만원을 요청했습니다.")
    out, fixed = meeting_memo.decided_values([_sec("결정 사항", content)], TUTOR)
    text = out[0]["content"]
    assert fixed == 2
    assert "간식비를 50만원으로 합의했습니다." in text and "| 1 | 간식비 50만원 |" in text
    # the request itself stays as said
    assert "박민호가 간식비 80만원을 요청했습니다." in text
    right = [_sec("결정 사항", "간식비를 50만원으로 합의했습니다.")]
    assert meeting_memo.decided_values(right, TUTOR) == (right, 0)


def test_a_paragraph_fenced_bare_prints_as_the_documents_own_text():
    body = ("회의 개요입니다.\n\n```\n김지훈(팀장), 이수연이 참석했습니다. "
            "안건은 세 가지였습니다.\n```\n\n참석 인원은 4명입니다.")
    assert report.unfence_pictures(body) == ("회의 개요입니다.\n\n김지훈(팀장), 이수연이 "
                                             "참석했습니다. 안건은 세 가지였습니다.\n\n참석 "
                                             "인원은 4명입니다.")
    code = "```\nresult = run(config)\nprint('완료되었습니다')\n```"
    assert report.unfence_pictures(code) == code


def _repair(sentence: str, reply: str, content: str) -> tuple[str, list]:
    async def complete(model, messages, api_key, *args, **kwargs):
        return json.dumps([reply], ensure_ascii=False), {}

    doc = verify.Doc([_sec("회의 개요", content)], material=TUTOR, memo=TUTOR)
    finding = verify.Finding(0, sentence, "minutes_unsourced", "자료에 없는 내용이다.")
    sections, left, _ = asyncio.run(verify.repair(doc, [finding], complete=complete,
                                                  model="m", api_key=""))
    return sections[0]["content"], left


def test_a_repair_that_fills_another_sentences_blank_fills_it_there_and_says_it_once():
    blank = "[회의 일자]에 교육혁신팀 주간회의를 열었습니다."
    note = "날짜 기준으로 2026-10-14를 사용했습니다."
    text, left = _repair(note, "2026년 10월 14일 교육혁신팀 주간회의를 열었습니다.",
                         f"{blank} 김지훈(팀장)이 참석했습니다. {note}")
    assert text.count("주간회의를 열었습니다") == 1
    assert text.startswith("2026년 10월 14일 교육혁신팀 주간회의를 열었습니다.")
    # the note itself stands unrepaired, for what settles the rest
    assert [f.sentence for f in left] == [note]


def test_a_repair_that_only_says_the_sections_words_again_is_not_taken():
    said = "10/21 10시, 3층 회의실에서 열립니다."
    note = "자료에 명시된 예정 안건은 없고, 일정·장소만 기재합니다."
    content = f"다음 회의입니다. {said} {note}"
    text, left = _repair(note, "다음 회의는 10/21 화 10시 3층 회의실입니다.", content)
    assert text == content and len(left) == 1


def test_the_memos_bare_clock_loses_the_writers_am_before_any_repair():
    sections = [_sec("다음 회의", "다음 회의는 2026-10-21 오전 10시에 3층 회의실에서 열립니다.")]
    out, fixed = stated_meeting.bare_clocks(sections, TUTOR)
    assert fixed == 1
    assert out[0]["content"] == "다음 회의는 2026-10-21 10시에 3층 회의실에서 열립니다."
    # a memo that says 오전 keeps it
    assert stated_meeting.bare_clocks(sections, TUTOR.replace("화 10시", "화 오전 10시"))[1] == 0


def test_the_new_checks_finish_on_a_long_run_of_spaces():
    spaces = " " * 50_000
    start = time.perf_counter()
    key_figures.changes(f"80만원{spaces}50만원{spaces}으로 줄이자고")
    meeting_memo.decided_values([_sec("결정", f"간식비를{spaces}80만원으로{spaces}합의")], TUTOR)
    report.unfence_pictures(f"```\n{spaces}다.\n```")
    verify.filled_twin(f"회의를{spaces}열었습니다", f"[회의 일자]에{spaces}회의를 열었습니다.")
    stated_meeting.bare_clocks([_sec("다음", f"오전{spaces}10시")], TUTOR)
    assert time.perf_counter() - start < 0.5
