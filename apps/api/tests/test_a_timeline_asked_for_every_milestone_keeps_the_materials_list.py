"""A deck asked for its whole schedule (「일정(마일스톤 전부)」) keeps every dated item the attached
memo lists on its timeline, in order — not the few the writer picked.

Rounds 2-3 r2d04, r3d04 (KC-SA): kickoff decks from a memo listing 8 milestones showed 2–5 of
them on the timeline slide (「11/26, 12/18, 01/22, 03/19」 of eight)."""

from __future__ import annotations

from app.services import deck

MEMO = ("[사옥 이전 킥오프 메모]\n마일스톤\n"
        "1. 12/02(수) 이전 TF 구성\n2. 12/16(수) 인테리어 업체 선정\n3. 2027-01-06(수) 설계 확정\n"
        "4. 2027-02-03(수) 공사 착공\n5. 2027-03-17(수) 가구·IT 설치\n6. 2027-03-27(토) 이사\n"
        "미정: 기존 사무실 원상 복구 범위")
ASK = ("첨부 메모로 사옥 이전 킥오프 발표 자료를 8장으로 만들어 줘. "
       "목표, 일정(마일스톤 전부), 역할, 리스크.")


def test_every_listed_milestone_is_on_the_timeline_in_order():
    slide = {"layout": "timeline", "title": "주요 일정",
             "timeline": [["12/02", "이전 TF 구성"], ["2027-01-06", "설계 확정"],
                          ["03/27", "이사"]]}
    out = deck.keep_listed_dates(slide, ASK, MEMO)
    keys = [p[0] for p in out["timeline"]]
    assert len(keys) == 6
    assert keys[0] == "12/02" and keys[-1] == "03/27"
    assert any("인테리어" in p[1] for p in out["timeline"])
    whats = [p[1] for p in out["timeline"]]
    assert whats.index("설계 확정") < whats.index("공사 착공")


def test_a_timeline_with_its_dates_on_the_right_keeps_that_side():
    slide = {"layout": "timeline", "title": "주요 일정",
             "timeline": [["TF 구성", "12/02(수)"], ["설계 확정", "2027-01-06(수)"],
                          ["이사", "2027-03-27(토)"]]}
    out = deck.keep_listed_dates(slide, ASK, MEMO)
    assert len(out["timeline"]) == 6
    assert all(deck._DATE_KEY.search(p[1]) for p in out["timeline"])
    assert out["timeline"][1] == ["인테리어 업체 선정", "12/16"]


def test_without_a_whole_schedule_ask_the_writers_pick_stands():
    slide = {"layout": "timeline", "title": "주요 일정",
             "timeline": [["12/02", "이전 TF 구성"], ["03/27", "이사"]]}
    ask = "첨부 메모로 사옥 이전 킥오프 발표 자료를 8장으로 만들어 줘. 핵심 일정만 간단히."
    assert deck.keep_listed_dates(slide, ask, MEMO) is slide
