"""The copy check on a chat answer written with a copy skill: claims and channel lengths.

`copy-check` (app/skills/copy-check, stage `post`) runs here when one of the copy skills is
on the turn — switched on, picked through `use_skill`, or attached by the agent. A judge
reads the answer against the material once (its prompt ships in the package); the claims
script turns that reply and its own scan into findings:

- ungrounded numbers, offers, testimonials and qualifiers are fixed by code (a bracket blank,
  the word taken out, the sentence dropped);
- ungrounded superlatives, comparisons, guarantees, urgency, disease wording and the
  statements the judge found no source for go back to the writer once, with any piece over
  its channel limit; the result is scanned again, and what the scan finds and what the writer
  left word for word is fixed by code.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from app.services import skill_packages, skill_scripts, skill_specs

log = logging.getLogger(__name__)

#: The shipped skills whose answers are copy: those whose SKILL.md says `copy_check: true`.
#: A turn's skills are read through their specs (`services/skill_specs.py`), a person's too.
COPY_SKILLS = frozenset(k for k, p in skill_packages.packages().items() if p.copy_check)
KEY = "copy-check"

Complete = Callable[[str, list[dict], str, int], Awaitable[tuple[str, dict]]]

_THINK = re.compile(r"<think>[\s\S]{0,60000}?</think>", re.I)
_MATERIAL_CHARS = 12000

Specs = Mapping[str, skill_specs.Spec] | None


def _copy(key: str, specs: Specs) -> skill_specs.Spec | None:
    spec = skill_specs.resolve(key, specs)
    return spec if spec is not None and spec.copy_check else None


def used_skills(outputs: list[str], specs: Specs = None) -> set[str]:
    """Copy skills a `use_skill` call returned this turn: the output carries the body."""
    found = set()
    candidates = {k: skill_specs.resolve(k, specs) for k in COPY_SKILLS}
    candidates.update({k: s for k, s in (specs or {}).items() if s.copy_check})
    for key, spec in candidates.items():
        head = spec.body.strip()[:160] if spec is not None and spec.copy_check else ""
        if head and any(head in (out or "") for out in outputs):
            found.add(key)
    return found


def on_turn(keys: tuple[str, ...] | list[str], outputs: list[str] | None = None,
            specs: Specs = None) -> set[str]:
    """The copy skills the turn wrote with: switched on, attached, or picked by `use_skill`."""
    return ({k for k in keys or () if _copy(k, specs)}
            | used_skills(list(outputs or []), specs))


def active(keys: tuple[str, ...] | list[str], outputs: list[str] | None = None,
           specs: Specs = None) -> bool:
    """Whether the turn wrote with a copy skill."""
    return bool(on_turn(keys, outputs, specs))


@dataclass(slots=True)
class Outcome:
    text: str
    #: The first check's findings.
    found: list[dict] = field(default_factory=list)
    #: Findings still there after the writer's rewrite (then fixed by code).
    left: list[dict] = field(default_factory=list)
    #: Pieces measured against the channel limits, as they stand at the end.
    rows: list[dict] = field(default_factory=list)
    #: Pieces over a hard limit before the rewrite.
    over: list[dict] = field(default_factory=list)
    rewritten: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.found or self.over)

    @property
    def detail(self) -> str:
        claims = _claims()
        parts = []
        if self.found:
            parts.append(claims.summary(self.found))
        if self.over:
            parts.append("한도 넘은 문구 " + ", ".join(
                f"「{r['text'][:20]}」 {r['count']}/{r['limit']}" for r in self.over[:6]))
        if self.rewritten:
            parts.append("고쳐 쓰기 1회")
        if self.left:
            parts.append(f"고쳐 쓴 뒤 남은 것 {len(self.left)}개 → 빈칸·삭제: "
                         + ", ".join(f"「{f['text'][:20]}」" for f in self.left[:6]))
        still = [r for r in self.rows if r.get("hard") and (r["over"] or r["under"])]
        if still:
            parts.append(f"아직 한도 밖 {len(still)}개")
        return " · ".join(parts)


def _claims():
    return skill_scripts.module(KEY, "claims")


def _length():
    return skill_scripts.module(KEY, "length")


def _judge_prompt(answer: str, material: str, rows: list[dict]) -> str:
    package = skill_packages.package_for(KEY)
    template = (package.root / "resources" / "judge.md").read_text(encoding="utf-8")
    if rows:
        fields = (
            "Copy out every piece of ad or headline copy written for these fields, exactly as "
            "written (without its numbering, label, quotes or the count beside it), one item per "
            "piece, `key` being the field it is for:\n"
            + "\n".join(f"- `{r['key']}` — {r['name']} {r['word']}" for r in rows)
        )
    else:
        fields = 'No fields to list: reply `"items": []`.'
    return (template.replace("<<MATERIAL>>", material[:_MATERIAL_CHARS] or "(none)")
            .replace("<<COPY>>", answer).replace("<<FIELDS>>", fields))


def _json(text: str) -> dict:
    text = _THINK.sub("", text or "")
    block = text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""
    try:
        data = json.loads(block)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _spend(usage: dict | None, spent: dict | None) -> None:
    if usage is None:
        return
    for key in ("inputTokens", "outputTokens"):
        usage[key] = usage.get(key, 0) + int((spent or {}).get(key, 0))


async def _judged(answer: str, material: str, rows: list[dict], *, complete: Complete,
                  judge: str, api_key: str, usage: dict | None) -> dict:
    try:
        text, spent = await complete(
            judge, [{"role": "user", "content": _judge_prompt(answer, material, rows)}],
            api_key, 3000)
    except Exception as exc:  # noqa: BLE001 — the scan still runs without the judge
        log.info("copy check judge failed: %r", exc)
        return {}
    _spend(usage, spent)
    return _json(text)


def _items(reply: dict, text: str = "", rows: list[dict] | None = None) -> list[dict]:
    """The pieces to measure: table cells under a field's header (read by code), then what the
    judge lifted out of anything else, each piece once."""
    found = _length().table_items(text, rows or []) if text else []
    seen = {i["text"] for i in found}
    for item in reply.get("items") or []:
        if isinstance(item, dict) and str(item.get("text") or "").strip() not in seen:
            found.append(item)
    return found


def _rewrite_prompt(text: str, material: str, findings: list[dict], over: list[dict]) -> str:
    lines = []
    for f in findings:
        why = f" — {f['why']}" if f.get("why") else ""
        lines.append(f"- 「{f['text']}」{why}")
    for r in over:
        bound = (f"at most {r['limit']} (cut at least {r['over']})" if r["over"]
                 else f"at least {r.get('min', 0)} (add at least {r['under']})")
        unit = "characters counting Hangul as 2" if r["unit"] == "chars_dw" else "characters"
        lines.append(f"- 「{r['text']}」 ({r['name']} {r['word']}) is {r['count']} {unit}, "
                     f"spaces included; make it {bound} {unit}, keeping its meaning — drop a "
                     "word rather than squeeze, and update any count written beside it.")
    return (
        "Below are the material and an answer written from it. Fix only the problems listed; "
        "an item the material does state after all stays as it is. "
        "For a claim: take it out or replace it with what the material states. Use a bracket "
        "blank such as [수치] or [영업시간] only for a value the person must fill in; when a "
        "sentence, list item or section has nothing true left to say, delete it (and its "
        "heading) instead of leaving a blank or a note. For a length: shorten or lengthen that "
        "piece only. Keep "
        "everything else exactly as written — structure, order, numbering, tables, headings, "
        "the other wording, and the language. Add no new fact, number, feature or offer. Reply "
        "with the whole corrected answer only, no preface.\n\n"
        f"# Material\n{material[:_MATERIAL_CHARS] or '(none)'}\n\n"
        "# Problems\n" + "\n".join(lines) + f"\n\n# Answer\n{text}"
    )


def _cleaned(reply: str) -> str:
    text = _THINK.sub("", reply or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```[a-z]{0,12}\n?|```$", "", text).strip()
    return text


async def run(answer: str, *, request: str, material: str, complete: Complete, model: str,
              judge: str, api_key: str, usage: dict | None = None,
              skills: frozenset[str] | set[str] = frozenset(), specs: Specs = None) -> Outcome:
    """Check, fix by code, rewrite once with what code cannot fix, check again."""
    claims, length = _claims(), _length()
    material = "\n".join(x for x in (request, material) if x and x.strip())
    rows = length.targets(request)
    options: dict = {"category": claims.category_of(material)}
    on = {k: spec for k in set(skills) - {KEY} if (spec := _copy(k, specs)) is not None}
    if on and all(spec.copy_kinds for spec in on.values()):
        options["kinds"] = list(dict.fromkeys(
            kind for key in sorted(on) for kind in on[key].copy_kinds))
    outcome = Outcome(answer)

    reply = await _judged(answer, material, rows, complete=complete, judge=judge or model,
                          api_key=api_key, usage=usage)
    found = claims.check(answer, material, [reply], options)
    measured = length.measured(_items(reply, answer, rows), rows)
    outcome.found = found
    outcome.over = [r for r in measured if r.get("hard") and (r["over"] or r["under"])]
    outcome.rows = measured
    text = claims.fix(answer, [f for f in found if f["action"] != "rewrite"])
    hand = [f for f in found if f["action"] == "rewrite"]
    if (hand or outcome.over) and model:
        try:
            reply_text, spent = await complete(
                model, [{"role": "user", "content": _rewrite_prompt(
                    text, material, hand, outcome.over)}],
                api_key, min(12000, 2 * len(text) + 1000))
            _spend(usage, spent)
            new = _cleaned(reply_text)
            # A rewrite that lost half a long answer is not kept.
            if new and len(new) >= min(0.5 * len(text), len(text) - 300):
                text, outcome.rewritten = new, True
            else:
                log.warning("copy check rewrite not kept: %d of %d characters", len(new), len(text))
        except Exception as exc:  # noqa: BLE001 — code fixes what is left
            log.warning("copy check rewrite failed: %r", exc)
        # Left: what the scan finds in the new text, and the judge's quotes still there word
        # for word. A second reading is not trusted to blank text the first did not question.
        outcome.left = claims.check(text, material, [reply], options)
        text = claims.fix(text, outcome.left)
        if outcome.rewritten and outcome.over:
            # The pieces as they now stand, measured again; this reading only lifts them out.
            reply = await _judged(text, material, rows, complete=complete,
                                  judge=judge or model, api_key=api_key, usage=usage)
    # The final text's pieces: its tables read again by code, and what the judge lifted out.
    outcome.rows = [r for r in length.measured(_items(reply, text, rows), rows)
                    if r["text"] in text]
    outcome.text = length.restate_counts(text, outcome.rows)
    return outcome


__all__ = ["COPY_SKILLS", "Outcome", "active", "on_turn", "run", "used_skills"]
