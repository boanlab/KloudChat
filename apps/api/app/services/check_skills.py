"""Agent/서식 check skills: `pre` gap question card, `post` unsourced values blanked."""

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
    value_guard,
)

log = logging.getLogger(__name__)

#: own_stage `post` checks (`check:copy`) run in their own stage, not `ground`
#: gap card question ids, told apart from file questions
GAP_PREFIX = "gap:"


Specs = Mapping[str, skill_specs.Spec] | None


def attached(*, agent_checks: list | tuple | None = None, agent_key: str | None = None,
             template_checks: tuple[str, ...] = (), specs: Specs = None) -> tuple[str, ...]:
    """Check skills from agent then 서식, each once; `agent_key` for replay/tests."""
    own = list(agent_checks or ()) if agent_checks is not None else list(
        starter.agent_checks(agent_key))
    keys = [*own, *template_checks]
    return tuple(k for k in dict.fromkeys(keys)
                 if (p := skill_specs.resolve(k, specs)) is not None and p.stage)


def of_stage(keys: tuple[str, ...], stage: str, specs: Specs = None) -> list[str]:
    return [k for k in keys if (p := skill_specs.resolve(k, specs)) is not None
            and p.stage == stage]


def entry(key: str, specs: Specs = None) -> ModuleType:
    """Shipped script a check skill runs (never a person's own file)."""
    spec = skill_specs.resolve(key, specs)
    ref = spec.entry_ref() if spec is not None and spec.stage else None
    if ref is None or ref[1] not in skill_packages.script_names(ref[0]):
        raise LookupError(f"{key} is not a check skill")
    return skill_scripts.module(*ref)


def own_stage(key: str, specs: Specs = None) -> bool:
    spec = skill_specs.resolve(key, specs)
    return bool(spec and spec.own_stage)


def gaps(keys: tuple[str, ...], *, questions: list[dict], request: str, material: str,
         today: dt.date | None = None, specs: Specs = None,
         judged: dict[str, bool] | None = None) -> list[dict]:
    """Attached `pre` checks' unanswered 빠진 정보 질문; `judged` = judge's reading."""
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
    """One card's questions with what's missing and why."""
    return [
        grounding.Question(id=f"{GAP_PREFIX}{f['fact']}", question=f["question"], detail=f["why"])
        for f in found
    ]


def rule(keys: tuple[str, ...], found: list[dict], specs: Specs = None) -> str:
    """Writer's line when the person proceeds without facts: each a blank."""
    for key in of_stage(keys, "pre", specs):
        try:
            if text := entry(key, specs).rule(found):
                return text
        except Exception:  # noqa: BLE001
            log.exception("check skill %s rule failed", key)
    return ""


def labelled(answers: dict[str, str], asked: list[dict]) -> dict[str, str]:
    """Gap answers as 「회의 일시: 10월 7일」."""
    labels = {str(q.get("id") or ""): _label(q) for q in asked or []}
    out = {}
    for qid, text in (answers or {}).items():
        label = labels.get(qid, "")
        text = str(text or "").strip()
        # 「모르니 빈칸으로」 says nothing about the fact: never 「회의 일시: 모르니 …」
        fact = qid.startswith(GAP_PREFIX) and not goes_on(text)
        out[qid] = f"{label}: {text}" if label and text and fact else text
    return out


def _label(question: dict) -> str:
    """Question label as carried on the card."""
    return str(question.get("label") or "")


#: "just proceed" reply, not an answer
_GO_ON = re.compile(
    r"자료대로|있는\s{0,2}(?:자료|것|대로)|그대로\s{0,2}(?:진행|써)|더\s{0,2}(?:있는\s{0,2})?(?:정보|자료)\s{0,2}(?:는|가)?\s{0,2}없|"
    r"모르겠|몰라|모르니|모릅니다|없어요|없어\.?$|진행해\s{0,2}(?:줘|주세요)"
    r"|빈칸으로|비워\s{0,2}(?:두|둬)|자료에\s{0,2}없는\s{0,2}것은|가진\s{0,2}사실의\s{0,2}전부")


def goes_on(text: str) -> bool:
    return bool(_GO_ON.search(text or ""))


#: form-offered `check_options` (G3) → trace settings
CHECK_OPTIONS = {
    #: 수치는 도구 계산 결과에서만 인정
    "tool_numbers": {"bare": True, "numbers": "computed"},
    #: 직함·제품명까지 근거 확인
    "titles": {"titles": True, "products": True},
}


def clean_options(raw: dict | None) -> dict[str, bool]:
    return {k: True for k in CHECK_OPTIONS if isinstance(raw, dict) and raw.get(k)}


def options_for(today: dt.date | None = None, settings: dict | None = None,
                surface: str = "") -> dict:
    """Trace strictness from agent and 서식 `check_options`, plus `surface`, `contact`."""
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
    #: first check's findings (fix blanked)
    found: list[dict] = field(default_factory=list)
    #: refound after the fix
    left: list[dict] = field(default_factory=list)
    #: findings by check, in run order
    by_key: dict[str, list[dict]] = field(default_factory=dict)
    #: what the value guard gave back of the fix (supported values, emptied blocks)
    settled: value_guard.Settled | None = None

    @property
    def marked(self) -> int:
        """Values and blocks left marked 「(확인 필요)」."""
        if self.settled is None:
            return len(self.found)
        return len(self.settled.marked) + len(self.settled.kept)

    @property
    def traced_only(self) -> bool:
        """Whether every finding is a `grounding-check` blank."""
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
                parts.append(f"근거 없는 값 {len(found)}개 → 확인 필요"
                             if key == "grounding-check" else f"{key} {len(found)}개")
        if self.settled is not None and self.settled.changed:
            parts.append(self.settled.note())
        shown = " · ".join(p for p in parts if p)
        return shown + (f" · 남은 것 {len(self.left)}개" if self.left else "")


def ground(keys: tuple[str, ...], text: str, *, material: str, computed: list[str],
           options: dict, specs: Specs = None, evidence: list[str] | None = None,
           worked: str = "") -> Outcome:
    """Attached `post` checks over `text`: check, fix once, recheck."""
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
    if outcome.text != text:
        # supported values back as written, the rest marked; nothing emptied
        today = None
        try:
            today = dt.date.fromisoformat(str(options.get("today") or "")[:10])
        except ValueError:
            pass
        outcome.settled = value_guard.settle(text, outcome.text, value_guard.Evidence.of(
            [material, *computed, str(options.get("person") or ""), *(evidence or [])],
            answer=text, today=today, worked=worked))
        outcome.text = outcome.settled.text
    return outcome


_REFERENCES = re.compile(r"참고\s{0,2}문헌|references|출처\s{0,2}목록", re.I)


def ground_sections(keys: tuple[str, ...], sections: list[dict], *, material: str,
                    computed: list[str], options: dict,
                    specs: Specs = None) -> tuple[list[dict], Outcome]:
    """Each section traced; reference list = the sources."""
    total = Outcome("")
    out = []
    # a total one section works out (「(…) × 1.1 = 46,563,000원」) holds in the others too
    whole = "\n".join(str(row.get("content") or "") for row in sections)
    for row in sections:
        content = str(row.get("content") or "")
        if not content or _REFERENCES.search(str(row.get("heading") or "")):
            out.append(row)
            continue
        one = ground(keys, content, material=material, computed=computed, options=options,
                     specs=specs, worked=whole)
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
