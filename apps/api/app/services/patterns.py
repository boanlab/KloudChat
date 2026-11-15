"""Structural patterns: what a document of a kind is made of, chosen from the request, never
picked from a gallery.

A report's pattern is its purpose format (`doc_formats`, 22 of them): the format's head and
numbering, its genre rule, its pre-writing questions (빠진 정보 질문), its process settings and
its check skills (확인하는 것) all come with the format detected from the request, whether or
not a template was picked. The shipped format templates still hold those settings
(`design_templates.for_format`); this module is where the writers ask for them by format.

A deck's pattern is its purpose (`app/patterns/deck/*.toml`): 사내 브리핑, 케이스 발표, 심사 발표,
제안 발표, 강의 자료. Its rules reach the deck planner as trusted context; its checks are what
the deck is reviewed against. The 55 slide shapes (`slide_patterns`, `deck._LAYOUTS`) stay the
planner's own choice per slide.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.services import doc_formats
from app.services.context import instruction_part, lead_instruction

_DECKS = Path(__file__).resolve().parent.parent / "patterns" / "deck"
#: first match wins: the narrower purposes before the broader ones
_DECK_ORDER = ("defense", "lecture", "proposal", "case", "briefing")


@dataclass(frozen=True)
class DeckPattern:
    id: str
    name: str
    words: re.Pattern[str]
    rules: str
    checks: tuple[str, ...]

    def note(self) -> str:
        """What the deck planner is told about the pattern."""
        return f"[패턴: {self.name}]\n{self.rules.strip()}"


@lru_cache(maxsize=1)
def deck_patterns() -> tuple[DeckPattern, ...]:
    out: list[DeckPattern] = []
    for key in _DECK_ORDER:
        data = tomllib.loads((_DECKS / f"{key}.toml").read_text(encoding="utf-8"))
        out.append(DeckPattern(
            id=key, name=str(data["name"]), words=re.compile(str(data["words"]), re.I),
            rules=str(data["rules"]), checks=tuple(str(c) for c in data.get("checks") or ())))
    return tuple(out)


def deck_purpose(request: str) -> DeckPattern | None:
    """The deck pattern the request's own words name (pasted material aside), or None."""
    words = lead_instruction(request) or instruction_part(request)
    for pattern in deck_patterns():
        if pattern.words.search(words):
            return pattern
    words = instruction_part(request)
    return next((p for p in deck_patterns() if p.words.search(words)), None)


def report_format(request: str, template_id: str | None = None,
                  bound: str | None = None) -> str | None:
    """The report pattern (`doc_formats` id) a request takes: the template's, the agent's bound
    one, else the one the request's words name (as `report_stages.bundle_for` decides)."""
    found = doc_formats.detect(lead_instruction(request), template_id, bound) or \
        doc_formats.detect(instruction_part(request), template_id, bound)
    return found.id if found is not None else None


def check_skills(format_id: str | None) -> tuple[str, ...]:
    """The check skills (확인하는 것) a report pattern brings."""
    from app.services import design_templates

    shipped = design_templates.for_format(format_id) if format_id else None
    return tuple(shipped.check_skills) if shipped is not None else ()


def checklist(format_id: str | None) -> tuple[str, ...]:
    """The review checklist of a report pattern (the critique's rubric)."""
    from app.services import design_templates

    shipped = design_templates.for_format(format_id) if format_id else None
    return tuple(shipped.checks) if shipped is not None else ()
