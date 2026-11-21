"""The agenda slide a deck gets added reads in the deck's language: 「Agenda」 in an English deck.

An English all-hands deck showed 「발표 순서」 as its second slide's title."""

from __future__ import annotations

from app.services import deck


def _plan(titles: list[str]) -> list[dict]:
    return [{"title": titles[0], "layout": "title"},
            *({"title": t, "layout": "bullets"} for t in titles[1:-1]),
            {"title": titles[-1], "layout": "closing"}]


def test_an_english_deck_gets_an_agenda_titled_agenda():
    plan = deck.ensure_agenda(_plan([
        "Security Awareness Month Kickoff", "Why It Matters", "Top 3 Risks We Face",
        "What We Ask of You", "Phishing Simulation", "Team Prizes", "Reporting Steps", "Quiz",
        "Who to Contact"]))
    assert plan[1] == {"title": "Agenda", "layout": "agenda"}


def test_a_korean_deck_keeps_its_korean_agenda():
    plan = deck.ensure_agenda(_plan([
        "신규 물류센터 오픈 킥오프", "프로젝트 목표", "주요 일정", "역할 분담", "미정 사항",
        "위험과 대응", "예산", "협업 방식", "다음 단계"]))
    assert plan[1] == {"title": "발표 순서", "layout": "agenda"}
