"""Structural patterns: what a document of a kind is made of, chosen from the request, never
picked from a gallery.

A report's pattern is its purpose format (`doc_formats`, 22 of them): the format's head and
numbering, its genre rule, its pre-writing questions (빠진 정보 질문), its process settings and
its check skills (확인하는 것) all come with the format detected from the request, whether or
not a template was picked. The shipped format templates still hold those settings
(`design_templates.for_format`); this module is where the writers ask for them by format.

A deck's pattern is its purpose (`app/patterns/deck/*.toml`, 13 of them: 회의·브리핑,
실적·성과 보고, 연구·논문 발표, 심사 발표, 교육·온보딩, 강의, 수업 과제 발표, 제품·서비스 소개,
투자 IR, 제안 발표, 프로젝트 결과 보고, 정책·제도 안내, 케이스 발표). Its rules and usual flow
reach the deck planner as trusted context; its questions are asked before writing; its checks
are what the deck is reviewed against. When two purposes match, the narrower one (lower
`order`) is taken and the deck records both. The 55 slide shapes (`slide_patterns`,
`deck._LAYOUTS`) stay the planner's own choice per slide.
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


@dataclass(frozen=True)
class DeckPattern:
    id: str
    name: str
    name_en: str
    #: lower first: when two purposes match, the lower `order` is taken
    order: int
    words: re.Pattern[str]
    rules: str
    #: `[(part, slides, layouts)]`: the deck's usual flow
    flow: tuple[tuple[str, str, tuple[str, ...]], ...]
    checks: tuple[str, ...]
    #: 빠진 정보 질문, as `gap_questions` stores them
    questions: tuple[dict, ...]
    #: process settings: `search` (False: no web search unless asked)
    process: dict

    def note(self) -> str:
        """What the deck planner is told about the pattern: its rules and its usual flow."""
        flow = "\n".join(
            f"- {part} ({slides}장): " + ", ".join(layouts) for part, slides, layouts in self.flow)
        return (f"[패턴: {self.name}]\n{self.rules.strip()}\n\n"
                "Usual flow of this kind of deck (adapt the count to the request; the layouts "
                f"are the ones that fit each part):\n{flow}")

    def wire(self, others: tuple[DeckPattern, ...] = ()) -> dict:
        """What the deck records of its pattern, for the line under the answer."""
        out: dict = {"id": self.id, "name": self.name, "nameEn": self.name_en}
        if others:
            out["also"] = [p.name for p in others]
        return out


@lru_cache(maxsize=1)
def deck_patterns() -> tuple[DeckPattern, ...]:
    out: list[DeckPattern] = []
    for path in sorted(_DECKS.glob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        out.append(DeckPattern(
            id=path.stem, name=str(data["name"]), name_en=str(data.get("name_en") or ""),
            order=int(data.get("order") or 999), words=re.compile(str(data["words"]), re.I),
            rules=str(data["rules"]),
            flow=tuple((str(f["part"]), str(f["slides"]), tuple(str(x) for x in f["layouts"]))
                       for f in data.get("flow") or ()),
            checks=tuple(str(c) for c in data.get("checks") or ()),
            questions=tuple(dict(q) for q in data.get("questions") or ()),
            process=dict(data.get("process") or {})))
    return tuple(sorted(out, key=lambda p: (p.order, p.id)))


def deck_purposes(request: str) -> tuple[DeckPattern, ...]:
    """Every deck pattern the request's own words name (pasted material aside), the one taken
    first: the lead sentence decides before the rest of the instruction, then `order`."""
    for words in (lead_instruction(request), instruction_part(request)):
        found = tuple(p for p in deck_patterns() if words and p.words.search(words))
        if found:
            return found
    return ()


def deck_purpose(request: str) -> DeckPattern | None:
    """The deck pattern a request takes, or None."""
    found = deck_purposes(request)
    return found[0] if found else None


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


def step(request: str, kind: str, report_format: str | None = None) -> dict | None:
    """The timeline line naming the structure pattern a document turn took (and any other the
    request also named), or None."""
    if kind == "slides":
        found = deck_purposes(request)
        if not found:
            return None
        detail = f"함께 맞은 패턴: {', '.join(p.name for p in found[1:])} — 앞의 것을 따름" \
            if len(found) > 1 else ""
        return {"id": "pattern", "type": "thinking", "status": "done",
                "label": f"구조: {found[0].name} 패턴", **({"detail": detail} if detail else {})}
    if kind == "report" and report_format and report_format in doc_formats.BY_ID:
        return {"id": "pattern", "type": "thinking", "status": "done",
                "label": f"구조: {doc_formats.BY_ID[report_format].label} 서식"}
    return None
