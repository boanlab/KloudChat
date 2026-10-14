"""Copy check on copy-skill chat answers: claims and channel lengths."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from app.services import (
    internal_words,
    output_language,
    post_checks,
    skill_packages,
    skill_scripts,
    skill_specs,
    value_guard,
)

log = logging.getLogger(__name__)

#: shipped skills with `copy_check: true`
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
    """Copy skills returned by `use_skill` this turn."""
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
    """Copy skills on the turn: switched on, attached, or via `use_skill`."""
    return ({k for k in keys or () if _copy(k, specs)}
            | used_skills(list(outputs or []), specs))


def active(keys: tuple[str, ...] | list[str], outputs: list[str] | None = None,
           specs: Specs = None) -> bool:
    """Whether the turn used a copy skill."""
    return bool(on_turn(keys, outputs, specs))


@dataclass(slots=True)
class Outcome:
    text: str
    found: list[dict] = field(default_factory=list)
    #: findings after the rewrite (then fixed by code)
    left: list[dict] = field(default_factory=list)
    #: pieces vs channel limits, final
    rows: list[dict] = field(default_factory=list)
    #: pieces over a hard limit before the rewrite
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
            parts.append(f"고쳐 쓴 뒤 남은 것 {len(self.left)}개 → 확인 표시: "
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
    """Pieces to measure: table cells under a field header, then judge-lifted pieces, once each."""
    found = _length().table_items(text, rows or []) if text else []
    seen = {i["text"] for i in found}
    for item in reply.get("items") or []:
        piece = str(item.get("text") or "").strip() if isinstance(item, dict) else ""
        # a clause of a cell already measured whole is not a piece of its own (r5 A13-T1:
        # 「11월 신청 시 체험 45일」 out of a 36-character description, measured as one 15/45)
        if piece and not any(piece in cell for cell in seen):
            found.append(item)
    return found


#: a site named by its place: 「이천 공장」 「오송공장」 「판교 연구소」
_SITE = re.compile(r"([가-힣]{2,8})\s?(?:공장|사업장|본사|지사|캠퍼스|연구소|연구원|센터|지점|법인"
                   r"|공단|산업단지|단지|물류센터)")
_LINK = re.compile(r"https?://[^\s)\]>\"'」]+")


def added_facts(new: str, old: str, material: str) -> list[str]:
    """Sites and links a rewrite brought that neither the answer before it nor the material
    has (r5 A15-T1: asked to fix one claim, the rewrite wrote 「본사와 이천 공장」 and a home
    page no search had shown); such a rewrite is not kept."""
    known = f"{old}\n{material}"
    sites = [m.group(0) for m in _SITE.finditer(new or "") if m.group(1) not in known]
    links = [u.rstrip(".,;:") for u in _LINK.findall(new or "")
             if u.rstrip(".,;:") not in known]
    return list(dict.fromkeys([*sites, *links]))


def _rewrite_prompt(text: str, material: str, findings: list[dict], over: list[dict],
                    *, strong: bool = False) -> str:
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
    if strong:
        # a strong writer weighs the notes itself (r7 A12-T1: a feature's everyday use was cut
        # to 「같은 문서를 여러 명이 함께 고칩니다.」 on a judge's note)
        return (
            "Below are the material and your copy. A check noted the items listed under Notes; "
            "a claim note may be wrong. Weigh each claim note against the material: keep the "
            "words when the material states them or they follow plainly from it (a listed "
            "feature's everyday use or benefit, your own suggestion or caveat, a date or count "
            "worked out from the material). When a note is right, reword only that phrase with "
            "what the material gives — never a name, place, site, number or link it does not "
            "give — or write 「(확인 필요)」 right after it; take a sentence out only when it "
            "asserts something invented and the copy reads complete without it. A length note "
            "is a fact: shorten or lengthen that piece only. Never use a bracket blank such as "
            "[수치]; never empty a paragraph or a table. Keep everything else exactly as "
            "written — structure, order, numbering, tables, headings, the other wording and "
            "the language. Add no new fact, number, feature or offer. Reply with the whole "
            "copy only, no preface and no word about the notes or the check.\n\n"
            f"# Material\n{material[:_MATERIAL_CHARS] or '(none)'}\n\n"
            "# Notes\n" + "\n".join(lines) + f"\n\n# Answer\n{text}"
        )
    return (
        "Below are the material and an answer written from it. Fix only the problems listed; "
        "an item the material does state after all stays as it is. "
        "For a claim: take out the unsupported clause or replace it with what the material "
        "states — never with a name, place, site, number or link the material does not give; "
        "when the sentence needs the value, keep it and write 「(확인 필요)」 right "
        "after it. Never replace a value with a bracket blank such as [수치]; brackets are "
        "only for a field the person fills in (a signature, a contact). Never empty a "
        "paragraph or leave a table with only its header. For a length: shorten or lengthen that "
        "piece only. Keep "
        "everything else exactly as written — structure, order, numbering, tables, headings, "
        "the other wording, and the language. Add no new fact, number, feature or offer. Reply "
        "with the whole corrected answer only, no preface and no word about the problems or "
        "the check — the reader never sees them.\n\n"
        f"# Material\n{material[:_MATERIAL_CHARS] or '(none)'}\n\n"
        "# Problems\n" + "\n".join(lines) + f"\n\n# Answer\n{text}"
    )


def _cleaned(reply: str) -> str:
    text = _THINK.sub("", reply or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```[a-z]{0,12}\n?|```$", "", text).strip()
    return internal_words.strip_echoed_heading(text)


#: what is left after the rewrite is marked, never dropped: a sentence or a row taken out
#: whole emptied blocks and took facts with it (r5 A12-T1 feature blocks, A15-T1 the mail's
#: one reason for the company)
_MARKED = ("drop", "rewrite")


def mark_left(text: str, found: list[dict]) -> str:
    """`text` with 「(확인 필요)」 after each finding's phrase (once each, right to left)."""
    lines = (text or "").split("\n")
    for f in sorted(found, key=lambda f: (f["line"], f["end"]), reverse=True):
        k = int(f["line"])
        if not 0 <= k < len(lines):
            continue
        line, phrase = lines[k], str(f.get("text") or "")
        at = line.find(phrase) if phrase else -1
        if at < 0:
            continue
        # never inside a value: 「3.5」 of 「3.5.0」 marked after the whole 「3.5.0」
        end = value_guard.token_end(line, at + len(phrase.rstrip()))
        if _MARK.match(line[end:end + 9]):
            continue
        lines[k] = f"{line[:end]} {value_guard.MARK}{line[end:]}"
    return "\n".join(lines)


def _gate(reply: dict, material: str, *, strong: bool) -> tuple[dict, list[dict]]:
    """The judge's claim notes code may act on (`post_checks.code_reading`, every model
    group): a note whose figures and names the material all holds is no note; for a small
    writer one code cannot check (no figure, no name) is only marked, never rewritten or cut
    (r8 L27L A12-T1: the noted clause cut left the empathy sentence hanging; LFLF A15-T1: the
    mail's reason for the company gone). Returns the reply left and the notes to mark."""
    notes = [u for u in reply.get("unsupported") or []
             if isinstance(u, dict) and str(u.get("quote") or "").strip()]
    if not notes:
        return reply, []
    kept, marked = [], []
    for note in notes:
        reading = post_checks.code_reading(str(note["quote"]), material)
        if reading == "present":
            continue
        (marked if reading == "unchecked" and not strong else kept).append(note)
    return {**reply, "unsupported": kept}, marked


async def run(answer: str, *, request: str, material: str, complete: Complete, model: str,
              judge: str, api_key: str, usage: dict | None = None,
              skills: frozenset[str] | set[str] = frozenset(), specs: Specs = None,
              strong: bool = False) -> Outcome:
    """Check, code-fix, rewrite once, recheck.

    `strong`: the writer is a strong model. It reads its own copy for claims (no weaker judge),
    every finding goes back to it as a note for one revision (code edits nothing but counts),
    and what is left after it is marked, never cut."""
    claims, length = _claims(), _length()
    material = "\n".join(x for x in (request, material) if x and x.strip())
    rows = length.targets(request)
    options: dict = {"category": claims.category_of(material)}
    on = {k: spec for k in set(skills) - {KEY} if (spec := _copy(k, specs)) is not None}
    if on and all(spec.copy_kinds for spec in on.values()):
        options["kinds"] = list(dict.fromkeys(
            kind for key in sorted(on) for kind in on[key].copy_kinds))
    outcome = Outcome(answer)

    reader = model if strong and model else judge or model
    reply = await _judged(answer, material, rows, complete=complete, judge=reader,
                          api_key=api_key, usage=usage)
    reply, unchecked = _gate(reply, material, strong=strong)
    found = claims.check(answer, material, [reply], options)
    measured = length.measured(_items(reply, answer, rows), rows)
    outcome.found = found
    outcome.over = [r for r in measured if r.get("hard") and (r["over"] or r["under"])]
    outcome.rows = measured
    text = answer if strong else claims.fix(answer, [f for f in found
                                                     if f["action"] != "rewrite"])
    hand = found if strong else [f for f in found if f["action"] == "rewrite"]
    if (hand or outcome.over) and model:
        try:
            reply_text, spent = await complete(
                model, [{"role": "user", "content": _rewrite_prompt(
                    text, material, hand, outcome.over, strong=strong)}],
                api_key, min(12000, 2 * len(text) + 1000))
            _spend(usage, spent)
            new = internal_words.strip_rewrite_preface(_cleaned(reply_text), text)
            # reject rewrites losing half a long answer
            if (new and len(new) >= min(0.5 * len(text), len(text) - 300)
                    and not added_facts(new, text, material)):
                text, outcome.rewritten = new, True
            else:
                log.warning("copy check rewrite not kept: %d of %d characters", len(new), len(text))
        except Exception as exc:  # noqa: BLE001 — code fixes what is left
            log.warning("copy check rewrite failed: %r", exc)
        # second reading never blanks text the first didn't question
        outcome.left = claims.check(text, material, [reply], options)
        if strong:
            # what the writer kept on the notes is marked where it stands
            text = mark_left(text, outcome.left)
        else:
            text = mark_left(claims.fix(text, [f for f in outcome.left
                                               if f["action"] not in _MARKED]),
                             [f for f in outcome.left if f["action"] in _MARKED])
        if outcome.rewritten and outcome.over:
            # re-measure; this reading only lifts pieces
            reply = await _judged(text, material, rows, complete=complete,
                                  judge=reader, api_key=api_key, usage=usage)
    elif strong and found:
        text = mark_left(text, found)
    for row in unchecked:
        marked = post_checks.mark(text, row["quote"])
        if marked != text:
            text = marked
            why = f"{row.get('why') or ''} (코드로 확인할 값이 없어 표시만)"
            outcome.found.append({"kind": "claim", "text": row["quote"], "action": "mark",
                                  "why": why})
    outcome.rows = [r for r in length.measured(_items(reply, text, rows), rows)
                    if r["text"] in text]
    outcome.text = length.restate_counts(text, outcome.rows)
    return outcome


#: a check's mark beside copy (「…줄이세요 (확인 필요)」)
_MARK = re.compile(r"[ \t]?\(\s?확인\s?필요\s?\)")
_SENTENCE_END = re.compile(r"[.!?。]\s*$")
_SHOWN = 40
_LISTED = 6


def _clause_before(head: str) -> str:
    """The clause that ends `head`, a closed 「(월)」 kept with it: scanned back to a comma,
    colon, bar, dash or an unclosed 「(」."""
    depth = 0
    for k in range(len(head) - 1, -1, -1):
        ch = head[k]
        if ch in ")）":
            depth += 1
        elif ch in "(（":
            if not depth:
                return head[k + 1:]
            depth -= 1
        elif not depth and (ch in ",，:：|" or (ch in "-—" and head[k - 1:k] == " ")):
            if ch == "," and head[k - 1:k].isdigit() and head[k + 1:k + 2].isdigit():
                continue  # 「99,999명」: a thousands comma, not a clause break
            return head[k + 1:]
    return head


def _marked_phrase(before: str, after: str, row: str = "") -> str:
    """What a mark sits on: the clause before it, the sentence it closes, or (a mark that
    opens a line) the clause after it; in a table, with the row's first cell."""
    head = re.sub(r"\*\*|__|`", "", before).rstrip()
    if _SENTENCE_END.search(head):
        sentences = [s for s in re.split(r"(?<=[.!?。])\s+", head) if s.strip()]
        phrase = sentences[-1] if sentences else ""
    else:
        phrase = _clause_before(head)
    phrase = phrase.strip(" \t-*•>#").rstrip(".")
    if not phrase:
        tail = re.sub(r"\*\*|__|`", "", after).lstrip()
        phrase = re.split(r"[.,:|。]", tail, maxsplit=1)[0].strip()
    if row and phrase and row not in phrase:
        phrase = f"{row} · {phrase}"
    return phrase if len(phrase) <= _SHOWN else phrase[: _SHOWN - 1].rstrip() + "…"


def lift_marks(text: str, *, answer: bool = False) -> str:
    """Copy keeps no 「(확인 필요)」: each mark comes out of the text it sits in, and what it
    was on is listed once below as 「보내기 전 확인」 (r3 A12·A13·A15 T1: marks inside the
    headline, the CTA and the mail body). `answer`: any answer's end box, 「확인 필요: …」 (a
    small external writer's, docs/architecture.md "Mechanisms by model group")."""
    if not text or not _MARK.search(text):
        return text
    listed: list[str] = []
    lines: list[str] = []
    fenced = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if fenced or not _MARK.search(line):
            lines.append(line)
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        row = cells[0] if line.lstrip().startswith("|") and len(cells) > 1 else ""
        kept, at = [], 0
        for m in _MARK.finditer(line):
            if line[m.start() - 1:m.start()] == "「" or line[m.end():m.end() + 1] == "」":
                continue  # the mark quoted, not placed (「값 뒤에 「(확인 필요)」를 붙이세요」)
            phrase = _marked_phrase(line[at:m.start()], line[m.end():],
                                    "" if row and line[at:m.start()].rstrip().endswith(row)
                                    else row)
            if phrase and phrase not in listed:
                listed.append(phrase)
            piece = line[at:m.start()]
            # 「…6개월 안」, 「…입니다.」: no space left before the punctuation that follows
            kept.append(piece.rstrip(" \t") if line[m.end():m.end() + 1] in (".", ",", ")")
                        else piece)
            at = m.end()
        kept.append(line[at:])
        lines.append("".join(kept).rstrip())
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip()
    if not listed:
        return body
    if output_language.reads_english(body):
        # copy in English gets its note in English
        shown = ", ".join(f"“{p}”" for p in listed[:_LISTED])
        more = f" and {len(listed) - _LISTED} more" if len(listed) > _LISTED else ""
        lead = "Needs checking" if answer else "Check before sending"
        return f"{body}\n\n{lead}: {shown}{more} — the material does not confirm these."
    shown = ", ".join(f"「{p}」" for p in listed[:_LISTED])
    more = f" 외 {len(listed) - _LISTED}곳" if len(listed) > _LISTED else ""
    if answer:
        return (f"{body}\n\n확인 필요: {shown}{more} — 주신 자료나 검색 결과로 확인하지 못한 "
                "내용이니 쓰기 전에 확인하세요.")
    return (f"{body}\n\n보내기 전 확인: {shown}{more} — 자료로 확인되지 않은 표현이니 보내기 전에 "
            "확인하세요.")


__all__ = ["COPY_SKILLS", "Outcome", "active", "lift_marks", "on_turn", "run", "used_skills"]
