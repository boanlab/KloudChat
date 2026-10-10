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
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import ModuleType

from app.services import (
    gap_questions,
    grounding,
    skill_packages,
    skill_scripts,
    skill_specs,
    starter,
)

log = logging.getLogger(__name__)

#: `post` checks run by a stage of their own (`check:copy`), not by `ground` (`own_stage`
#: in the skill's fields). An attached check that also names `post_check` scripts
#: (generic-language-killer) runs here as its entry only — the safe rewrites; switched on by
#: the person, `check:skills` runs the rest.
#: The question ids the gap card uses, so answers are told from the file questions.
GAP_PREFIX = "gap:"


Specs = Mapping[str, skill_specs.Spec] | None


def attached(*, agent_checks: list | tuple | None = None, agent_key: str | None = None,
             template_checks: tuple[str, ...] = (), specs: Specs = None) -> tuple[str, ...]:
    """The check skills an agent and a 서식 attach, agent first, each once.

    `agent_checks` is the agent row's own list (`Agent.check_skills`), what every agent —
    catalogue or a person's — runs with; `agent_key` reads a shipped spec instead, for a
    replay or a test that has no row. A person's check skill (`skill:<id>`) counts when
    `specs` holds it."""
    own = list(agent_checks or ()) if agent_checks is not None else list(
        starter.agent_checks(agent_key))
    keys = [*own, *template_checks]
    return tuple(k for k in dict.fromkeys(keys)
                 if (p := skill_specs.resolve(k, specs)) is not None and p.stage)


def of_stage(keys: tuple[str, ...], stage: str, specs: Specs = None) -> list[str]:
    return [k for k in keys if (p := skill_specs.resolve(k, specs)) is not None
            and p.stage == stage]


def entry(key: str, specs: Specs = None) -> ModuleType:
    """The shipped script a check skill runs, whoever's skill names it (never a person's
    own file: `skill_specs.validate` lets an entry name shipped checks only)."""
    spec = skill_specs.resolve(key, specs)
    ref = spec.entry_ref() if spec is not None and spec.stage else None
    if ref is None or ref[1] not in skill_packages.script_names(ref[0]):
        raise LookupError(f"{key} is not a check skill")
    return skill_scripts.module(*ref)


def own_stage(key: str, specs: Specs = None) -> bool:
    spec = skill_specs.resolve(key, specs)
    return bool(spec and spec.own_stage)


# ── before writing ───────────────────────────────────────────────────────


def gaps(keys: tuple[str, ...], *, questions: list[dict], request: str, material: str,
         today: dt.date | None = None, specs: Specs = None,
         judged: dict[str, bool] | None = None) -> list[dict]:
    """The attached `pre` checks' findings: the 빠진 정보 질문 (`services/gap_questions.py`)
    the request and material do not answer. `judged` holds the judge model's reading of the
    questions it decides."""
    if not questions:
        return []
    computed = {**gap_questions.readings(questions, request, material, today),
                **({"judged": dict(judged)} if judged else {})}
    found: list[dict] = []
    for key in of_stage(keys, "pre", specs):
        try:
            module = entry(key, specs)
            found += module.check(request, material, computed, {"questions": list(questions)})
        except Exception:  # noqa: BLE001 — a broken check asks nothing; the turn goes on
            log.exception("check skill %s failed before writing", key)
    return found


def questions(found: list[dict]) -> list[grounding.Question]:
    """One card's questions, each saying what is missing and why; answered in free text."""
    return [
        grounding.Question(id=f"{GAP_PREFIX}{f['fact']}", question=f["question"], detail=f["why"])
        for f in found
    ]


def rule(keys: tuple[str, ...], found: list[dict], specs: Specs = None) -> str:
    """The writer's line when the person went on without the facts: each one a blank."""
    for key in of_stage(keys, "pre", specs):
        try:
            if text := entry(key, specs).rule(found):
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
    """The asked question's label, as the card carried it."""
    return str(question.get("label") or "")


#: A reply that only says to go on: 「더 있는 정보는 없어. 자료대로 진행해 줘.」 — not an answer.
_GO_ON = re.compile(
    r"자료대로|있는\s{0,2}(?:자료|것|대로)|그대로\s{0,2}(?:진행|써)|더\s{0,2}(?:있는\s{0,2})?(?:정보|자료)\s{0,2}(?:는|가)?\s{0,2}없|"
    r"모르겠|몰라|없어요|없어\.?$|진행해\s{0,2}(?:줘|주세요)")


def goes_on(text: str) -> bool:
    return bool(_GO_ON.search(text or ""))


# ── after writing ────────────────────────────────────────────────────────


#: An agent's `check_options` the form offers (G3), with what each sets on the trace.
CHECK_OPTIONS = {
    #: 수치는 도구 계산 결과에서만 인정: a number counts only when a tool printed it.
    "tool_numbers": {"bare": True, "numbers": "computed"},
    #: 직함·제품명까지 근거 확인: titles and product names are traced too.
    "titles": {"titles": True, "products": True},
}


def clean_options(raw: dict | None) -> dict[str, bool]:
    return {k: True for k in CHECK_OPTIONS if isinstance(raw, dict) and raw.get(k)}


def options_for(today: dt.date | None = None, settings: dict | None = None,
                surface: str = "") -> dict:
    """How strict the trace is: what the agent asked for (`Agent.check_options`) and its
    서식 sets (`check_options` in a `template.toml`: a career history traces titles). Also what
    every check may read: where it runs (`surface`) and the agent's 담당 부서·연락처
    (`contact`), which the checks that point to it copy as written."""
    options: dict = {"today": today.isoformat() if today else ""}
    for key in clean_options(settings):
        options.update(CHECK_OPTIONS[key])
    if surface:
        options["surface"] = surface
    contact = settings.get("contact") if isinstance(settings, dict) else None
    if isinstance(contact, str) and contact.strip():
        options["contact"] = contact.strip()[:500]
    return options


@dataclass(slots=True)
class Outcome:
    text: str
    #: What the first check found (and the fix blanked).
    found: list[dict] = field(default_factory=list)
    #: What the check found again after the fix.
    left: list[dict] = field(default_factory=list)
    #: The findings by the check that made them, in the order they ran.
    by_key: dict[str, list[dict]] = field(default_factory=dict)

    @property
    def traced_only(self) -> bool:
        """Whether every finding is a value the trace (`grounding-check`) blanked."""
        return set(self.by_key) <= {"grounding-check"}

    @property
    def summary(self) -> str:
        if not self.found:
            return ""
        parts = []
        for key, found in (self.by_key or {"grounding-check": self.found}).items():
            try:
                parts.append(skill_scripts.entry(key).summary(found))
            except Exception:  # noqa: BLE001
                parts.append(f"근거 없는 값 {len(found)}개 → 빈칸"
                             if key == "grounding-check" else f"{key} {len(found)}개")
        shown = " · ".join(p for p in parts if p)
        return shown + (f" · 남은 것 {len(self.left)}개" if self.left else "")


def ground(keys: tuple[str, ...], text: str, *, material: str, computed: list[str],
           options: dict, specs: Specs = None) -> Outcome:
    """The attached `post` checks over `text`: check, fix once, check again."""
    outcome = Outcome(text)
    for key in of_stage(keys, "post", specs):
        if own_stage(key, specs):
            continue
        try:
            module = entry(key, specs)
            found = module.check(outcome.text, material, computed, options)
            if not found:
                continue
            fixed = module.fix(outcome.text, found)
            outcome.found += found
            outcome.by_key.setdefault(key, []).extend(found)
            outcome.left += module.check(fixed, material, computed, options)
            outcome.text = fixed
        except Exception:  # noqa: BLE001 — the text stays as written; the step says nothing
            log.exception("check skill %s failed after writing", key)
    return outcome


_REFERENCES = re.compile(r"참고\s{0,2}문헌|references|출처\s{0,2}목록", re.I)


def ground_sections(keys: tuple[str, ...], sections: list[dict], *, material: str,
                    computed: list[str], options: dict,
                    specs: Specs = None) -> tuple[list[dict], Outcome]:
    """Each section's content traced; a reference list is the sources themselves."""
    total = Outcome("")
    out = []
    for row in sections:
        content = str(row.get("content") or "")
        if not content or _REFERENCES.search(str(row.get("heading") or "")):
            out.append(row)
            continue
        one = ground(keys, content, material=material, computed=computed, options=options,
                     specs=specs)
        total.found += one.found
        total.left += one.left
        out.append({**row, "content": one.text} if one.text != content else row)
    return out, total


__all__ = [
    "CHECK_OPTIONS", "GAP_PREFIX", "Outcome", "attached", "clean_options", "entry",
    "gaps", "own_stage",
    "goes_on", "ground", "ground_sections", "labelled", "of_stage", "options_for", "questions",
    "rule",
]
