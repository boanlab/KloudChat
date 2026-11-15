"""The grounding check blanks an unsourced person's name, not an ordinary word that happens to
stand before a title-like word: 「8강이 짚은 대표 오답」 is no 「[이름] 대표」."""

from __future__ import annotations

from app.services import skill_scripts

trace = skill_scripts.module("grounding-check", "trace")
OPTIONS = {"today": "2026-11-15"}
MATERIAL = "8강 중간고사 대비 정리: 네임스페이스와 cgroup 혼동이 가장 많이 틀린 문제다."


def _people(text: str) -> list[str]:
    found = trace.check(text, MATERIAL, [], OPTIONS)
    return [f["text"] for f in found if f.get("kind") == "person"]


def test_a_word_with_no_surname_before_a_title_word_stays():
    for word in ("짚은", "꼽은", "흔한", "가장", "이번"):
        text = f"마지막으로 8강이 {word} 대표 오답이 이 구분에서 나옵니다."
        assert _people(text) == [], word
        assert "[이름]" not in trace.fix(text, trace.check(text, MATERIAL, [], OPTIONS))


def test_an_unsourced_full_name_with_a_title_is_still_blanked():
    assert _people("회의는 김민수 대표 주재로 열렸습니다.") == ["김민수"]
    assert _people("정해원 대표 발언으로 시작했습니다.") == ["정해원"]


def test_a_given_name_with_nim_is_still_a_name():
    assert _people("발표는 지민 님, 정리는 다음 주에 합니다.") == ["지민"]
