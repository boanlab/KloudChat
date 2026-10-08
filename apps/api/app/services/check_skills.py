"""Check skills attached to an agent or a 서식, run by the server around writing.

A check skill is a package under `app/skills/` whose SKILL.md names a `stage` and an `entry`
script (app/skills/README.md). Before writing (`pre`), what the format needs and the material
does not give becomes one question card; after writing (`post`), every value no source holds
becomes a bracket blank — fixed once, checked again, and both counts recorded.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field

from app.services import grounding, report_checks, skill_packages, skill_scripts, starter

log = logging.getLogger(__name__)

#: Fact sets keyed by agent rather than by format: the chat agents write no `doc_format`.
_AGENT_FACTS = {"career-docs": "career-docs", "data-analysis": "data-analysis"}
#: The question ids the gap card uses, so answers are told from the file questions.
GAP_PREFIX = "gap:"


def attached(*, agent_key: str | None = None,
             template_checks: tuple[str, ...] = ()) -> tuple[str, ...]:
    """The check skills an agent and a 서식 attach, agent first, each once."""
    keys = [*starter.agent_checks(agent_key), *template_checks]
    return tuple(k for k in dict.fromkeys(keys) if (p := skill_packages.package_for(k)) and p.stage)


def of_stage(keys: tuple[str, ...], stage: str) -> list[str]:
    return [k for k in keys if (p := skill_packages.package_for(k)) and p.stage == stage]


def fact_set(doc_format: str | None, agent_key: str | None) -> str:
    """Which declared fact list applies: the report's format, else the chat agent's own."""
    if doc_format:
        return doc_format
    return _AGENT_FACTS.get(agent_key or "", "")


# ── before writing ───────────────────────────────────────────────────────


def readings(facts: str, request: str, material: str, today: dt.date | None) -> dict:
    """What the server's extractors already read, so the script trusts them over its regexes."""
    out: dict = {}
    if facts == "minutes":
        base, _ = report_checks.meeting_base(request, material, today)
        out["meeting_date"] = base.isoformat() if base else ""
    elif facts == "client_proposal":
        terms = report_checks.rfp_terms(material, request) or {}
        if terms.get("clients"):
            out["clients"] = terms["clients"]
        out["period"] = bool(terms.get("end") or terms.get("months"))
        out["staff"] = bool(report_checks.staffing(material))
        out["priced"] = any(report_checks.price_line(line)
                            for line in f"{request}\n{material}".splitlines())
        out["vat"] = report_checks.price_basis(material)
    elif facts == "career_history":
        periods = report_checks.career_periods(material or request, request, today)
        if periods:
            out["periods"] = True
    return out


def gaps(keys: tuple[str, ...], *, facts: str, request: str, material: str,
         today: dt.date | None = None) -> list[dict]:
    """The attached `pre` checks' findings: facts the request and material do not hold."""
    if not facts:
        return []
    found: list[dict] = []
    for key in of_stage(keys, "pre"):
        try:
            module = skill_scripts.entry(key)
            found += module.check(request, material, readings(facts, request, material, today),
                                  {"facts": facts})
        except Exception:  # noqa: BLE001 — a broken check asks nothing; the turn goes on
            log.exception("check skill %s failed before writing", key)
    return found


def questions(found: list[dict]) -> list[grounding.Question]:
    """One card's questions, each saying what is missing and why; answered in free text."""
    return [
        grounding.Question(id=f"{GAP_PREFIX}{f['fact']}", question=f["question"], detail=f["why"])
        for f in found
    ]


def rule(keys: tuple[str, ...], found: list[dict]) -> str:
    """The writer's line when the person went on without the facts: each one a blank."""
    for key in of_stage(keys, "pre"):
        try:
            if text := skill_scripts.entry(key).rule(found):
                return text
        except Exception:  # noqa: BLE001
            log.exception("check skill %s rule failed", key)
    return ""


def labelled(answers: dict[str, str], asked: list[dict]) -> dict[str, str]:
    """Gap answers as 「회의 일시: 10월 7일」, so the request reads them as that fact."""
    labels = {str(q.get("id") or ""): _label(q) for q in asked or []}
    out = {}
    for qid, text in (answers or {}).items():
        label = labels.get(qid, "")
        text = str(text or "").strip()
        out[qid] = f"{label}: {text}" if label and text and qid.startswith(GAP_PREFIX) else text
    return out


def _label(question: dict) -> str:
    fact = str(question.get("id") or "").removeprefix(GAP_PREFIX)
    for module_key in of_stage(tuple(skill_packages.packages()), "pre"):
        try:
            for facts in skill_scripts.entry(module_key).FACTS.values():
                for row in facts:
                    if row.id == fact:
                        return row.label
        except Exception:  # noqa: BLE001
            continue
    return ""


#: A reply that only says to go on: 「더 있는 정보는 없어. 자료대로 진행해 줘.」 — not an answer.
_GO_ON = re.compile(
    r"자료대로|있는\s{0,2}(?:자료|것|대로)|그대로\s{0,2}(?:진행|써)|더\s{0,2}(?:있는\s{0,2})?(?:정보|자료)\s{0,2}(?:는|가)?\s{0,2}없|"
    r"모르겠|몰라|없어요|없어\.?$|진행해\s{0,2}(?:줘|주세요)")


def goes_on(text: str) -> bool:
    return bool(_GO_ON.search(text or ""))


# ── after writing ────────────────────────────────────────────────────────


def options_for(facts: str, today: dt.date | None = None) -> dict:
    """How strict the trace is for the format: an analysis's numbers come from its tool output
    only; a career document's titles and tools are checked too."""
    options: dict = {"today": today.isoformat() if today else ""}
    if facts == "data-analysis":
        options.update(bare=True, numbers="computed")
    if facts in ("career-docs", "career_history"):
        options.update(titles=True, products=True)
    return options


@dataclass(slots=True)
class Outcome:
    text: str
    #: What the first check found (and the fix blanked).
    found: list[dict] = field(default_factory=list)
    #: What the check found again after the fix.
    left: list[dict] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.found:
            return ""
        try:
            shown = skill_scripts.entry("grounding-check").summary(self.found)
        except Exception:  # noqa: BLE001
            shown = f"근거 없는 값 {len(self.found)}개 → 빈칸"
        return shown + (f" · 남은 것 {len(self.left)}개" if self.left else "")


def ground(keys: tuple[str, ...], text: str, *, material: str, computed: list[str],
           options: dict) -> Outcome:
    """The attached `post` checks over `text`: check, fix once, check again."""
    outcome = Outcome(text)
    for key in of_stage(keys, "post"):
        try:
            module = skill_scripts.entry(key)
            found = module.check(outcome.text, material, computed, options)
            if not found:
                continue
            fixed = module.fix(outcome.text, found)
            outcome.found += found
            outcome.left += module.check(fixed, material, computed, options)
            outcome.text = fixed
        except Exception:  # noqa: BLE001 — the text stays as written; the step says nothing
            log.exception("check skill %s failed after writing", key)
    return outcome


_REFERENCES = re.compile(r"참고\s{0,2}문헌|references|출처\s{0,2}목록", re.I)


def ground_sections(keys: tuple[str, ...], sections: list[dict], *, material: str,
                    computed: list[str], options: dict) -> tuple[list[dict], Outcome]:
    """Each section's content traced; a reference list is the sources themselves."""
    total = Outcome("")
    out = []
    for row in sections:
        content = str(row.get("content") or "")
        if not content or _REFERENCES.search(str(row.get("heading") or "")):
            out.append(row)
            continue
        one = ground(keys, content, material=material, computed=computed, options=options)
        total.found += one.found
        total.left += one.left
        out.append({**row, "content": one.text} if one.text != content else row)
    return out, total


__all__ = [
    "GAP_PREFIX", "Outcome", "attached", "fact_set", "gaps", "goes_on", "ground", "ground_sections",
    "labelled", "of_stage", "options_for", "questions", "readings", "rule",
]
