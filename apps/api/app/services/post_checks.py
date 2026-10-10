"""Skill post-checks on a chat answer: the scripts a skill names in `post_check`.

A package whose SKILL.md names `post_check` (app/skills/README.md) has its scripts run by the
server on the finished answer whenever the skill is on the turn — switched on, picked through
`use_skill`, or attached by the agent — the copy check's pattern made general:

1. each script's `post(text, args)` fixes by code what is safe (a value no source holds
   becomes a bracket blank, a changed original value goes back) and lists the rest;
2. with `post_judge`, the judge model quotes the statements the material does not give, and a
   second reading quotes the material's words for each, and a statement whose quoted
   evidence is the material's own counts as stated (code checks the quote);
3. the writer rewrites once with only those problems;
4. the scripts run again with `final` set, fixing by code what the rewrite left, and a judged
   quote still there word for word is taken out.

`post` returns `{"text", "fixed", "rewrite", "flags"}`, each finding `{"kind", "text", "why"}`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from app.services import skill_packages, skill_scripts, skill_specs, units

log = logging.getLogger(__name__)

Complete = Callable[[str, list[dict], str, int], Awaitable[tuple[str, dict]]]

_THINK = re.compile(r"<think>[\s\S]{0,60000}?</think>", re.I)
_MATERIAL_CHARS = 16000
_SKILL_CHARS = 6000
_JUDGED = 12

_JUDGE = """You check an answer against the material it was written from. The material is \
the person's request and the files and text they gave, and what code and scripts printed \
this turn; nothing else counts as a source.

List each statement in the answer that presents as fact something the material does not \
state or directly imply: a date, deadline, time, number, amount, name, contact, version, \
procedure, menu or screen, policy, refund or contract term, feature, capability, customer, \
interview finding, market figure, or what a cited source says (a statement citing [n] is \
unsupported when that source's text in the material does not say it). A plan or judgment \
the material hedges (예정, 검토 중, 가능할 것으로 판단) written as settled fact is unsupported.

Do not list: a sentence that restates or paraphrases what the material says (a listed \
feature described in plain words, its obvious use), a heading or a line that only introduces \
what follows, wording changes that keep the meaning, a value written as a bracket blank \
([날짜], [수치]), anything marked 가정, 출처 미확인, 확인 필요 or 자료 없음 (also a value \
the answer lists elsewhere as an assumption with its basis), a question to the \
person, a recommendation or option the answer offers as its own suggestion, the answer's own \
ranking, rating, priority or conclusion drawn from the material's facts, or arithmetic on \
the material's own values.

<<TODAY>><<FOCUS>>Reply with JSON only:
{"unsupported": [{"quote": "<exact text copied from the answer, one sentence or table cell, \
at most 160 characters>", "why": "<what the material lacks, one short sentence in Korean>"}]}
At most 12 items; an empty list when everything is supported.

# Material
<<MATERIAL>>

# Answer
<<ANSWER>>
"""

_CONFIRM = """Below are the material an answer was written from and statements quoted from \
the answer. For each statement, copy from the material the words that state it or directly \
imply it (a paraphrase of what it says counts), exactly as the material writes them, or leave \
"evidence" empty when the material does not say it. Reply with JSON only:
{"verdicts": [{"n": <number>, "evidence": "<exact words from the material, or empty>"}]}

# Material
<<MATERIAL>>

# Statements
<<STATEMENTS>>
"""

_REWRITE = (
    "Below are the material and an answer written from it. Fix only the problems listed; an "
    "item the material does state after all stays as it is. For a value or statement the "
    "material does not give: take it out or replace it with what the material states; use a "
    "bracket blank such as [날짜] or [수치] only for a value the person must fill in, and "
    "delete a sentence or list item that has nothing true left to say. For an original "
    "value or wording that was changed: restore the original exactly. For a plan or judgment "
    "written as settled: write it as the material hedges it. Keep everything else exactly as "
    "written — structure, order, headings, tables, the other wording and the language. Add no "
    "new fact, number, date, name, feature or procedure. Reply with the whole corrected answer "
    "only, no preface.\n\n# Material\n{material}\n\n# Problems\n{problems}\n\n# Answer\n{text}"
)


Specs = Mapping[str, skill_specs.Spec] | None


def entries(key: str, specs: Specs = None) -> list[tuple[str, str]]:
    """`(package key, script)` pairs a skill's `post_check` names, in order: shipped
    scripts only, whoever's skill names them."""
    spec = skill_specs.resolve(key, specs)
    if spec is None:
        return []
    out = []
    for item in spec.post_checks:
        owner, _, script = item.partition("/")
        if (found := skill_packages.package_for(owner)) and script in found.scripts:
            out.append((found.key, script))
    return out


def picked(outputs: list[str], specs: Specs = None) -> set[str]:
    """Post-checked skills a `use_skill` call returned this turn: the output carries the body."""
    found = set()
    candidates = {p.key: skill_specs.of_package(p) for p in skill_packages.post_checked()}
    candidates.update({k: s for k, s in (specs or {}).items() if s.post_checks})
    for key, spec in candidates.items():
        head = spec.body.strip()[:160] if spec is not None and spec.post_checks else ""
        if head and any(head in (out or "") for out in outputs):
            found.add(key)
    return found


def on_turn(keys: tuple[str, ...] | list[str], outputs: list[str] | None = None,
            specs: Specs = None) -> list[str]:
    """The skills on the turn that name post checks, in a stable order."""
    named = {k for k in keys or () if entries(k, specs)} | picked(list(outputs or []), specs)
    return sorted(named)


@dataclass(slots=True)
class Outcome:
    text: str
    skills: list[str] = field(default_factory=list)
    #: What code fixed, first pass and final.
    fixed: list[dict] = field(default_factory=list)
    #: What went to the writer.
    sent: list[dict] = field(default_factory=list)
    #: What the final pass could not settle (shown, not changed).
    left: list[dict] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    rewritten: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.fixed or self.sent)

    @property
    def label(self) -> str:
        if not self.changed and not self.flags and not self.left:
            return "스킬 점검: 고칠 것 없음"
        parts = []
        if self.fixed:
            parts.append(f"코드로 고침 {len(self.fixed)}개")
        if self.sent:
            parts.append(f"다시 쓰게 함 {len(self.sent)}개")
        if self.left or self.flags:
            parts.append(f"확인할 것 {len(self.left) + len(self.flags)}개")
        return "스킬 점검: " + " · ".join(parts)

    @property
    def detail(self) -> str:
        def shown(rows: list[dict]) -> str:
            return ", ".join(f"「{str(r.get('text') or '')[:30]}」 {r.get('why') or ''}".strip()
                             for r in rows[:8])

        parts = []
        if self.fixed:
            parts.append("고침: " + shown(self.fixed))
        if self.sent:
            parts.append(("고쳐 쓰기 1회: " if self.rewritten else "고쳐 쓰기 못함: ")
                         + shown(self.sent))
        if self.left:
            parts.append("남은 것: " + shown(self.left))
        if self.flags:
            parts.append("확인: " + shown(self.flags))
        return " · ".join(parts)


def _spend(usage: dict | None, spent: dict | None) -> None:
    if usage is None:
        return
    for key in ("inputTokens", "outputTokens"):
        usage[key] = usage.get(key, 0) + int((spent or {}).get(key, 0))


#: A string value a model left open at the end of its line: 「"why": "자료에 없음.」.
_OPEN_STRING = re.compile(r'^(\s{0,40}"\w{1,20}"\s{0,4}:\s{0,4}"[^"\n]{0,600})$', re.M)
_PAIR = re.compile(r'"quote"\s{0,4}:\s{0,4}"([^"\n]{1,400})"'
                   r'(?:[\s,]{0,8}"why"\s{0,4}:\s{0,4}"([^"\n]{0,400}))?')
#: A string broken over a line: 「…없음.\n"」 (the closing quote on a line of its own).
_SPLIT_CLOSE = re.compile(r'\\?\n\s{0,40}"\s{0,4}\n')


def _json(text: str) -> dict:
    """The reply's JSON object; a model's raw line breaks inside strings and a string left
    open at its line's end are mended first, and quotes are read one by one as a last try."""
    text = _THINK.sub("", text or "")
    block = text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""
    for attempt in (block, _OPEN_STRING.sub(r'\1"', _SPLIT_CLOSE.sub('"\n', block))):
        try:
            data = json.loads(attempt, strict=False)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    pairs = [{"quote": q, "why": w or ""} for q, w in _PAIR.findall(block)]
    return {"unsupported": pairs} if pairs else {}


_ASKS = re.compile(r"\?\s*$|(?:알려|말씀해|확인해|정해|보내)\s?주(?:세요|십시오)|할까요|인가요")


_OPEN = re.compile(
    r"확인\s?필요|출처\s?미확인|자료\s?없음|판단\s?보류|가정|미정|\[[^\]\n]{1,20}\]")
#: A statement says something: a predicate (「…다」「…니다」「…함」) or a figure.
_STATES = re.compile(r"\d|(?:다|요|음|됨|함|임|니다)[.!)」]?\s*$|(?:다|니다)[.,]\s")
_CHECKLIST = re.compile(
    r"확인할\s?것|확인\s?사항|확인이\s?필요|보내기\s?전|채울\s?것|질문|물어볼|남은\s?일")
_HEAD_LINE = re.compile(r"^\s{0,3}(?:#{1,6}\s|\*\*[^*\n]{1,40}\*\*\s*:?\s*$)")


def _under_checklist(quote: str, text: str) -> bool:
    """Whether the quote stands under a heading of things to check or ask."""
    flat = _flat(quote)
    heading = ""
    for line in (text or "").split("\n"):
        if _HEAD_LINE.match(line):
            heading = line
        elif flat in _flat(line):
            return bool(_CHECKLIST.search(heading))
    return False


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def present(quote: str, text: str) -> bool:
    return bool(quote.strip()) and _flat(quote) in _flat(text)


async def _judge(text: str, material: str, *, complete: Complete, judge: str, api_key: str,
                 usage: dict | None, focus: str = "", today: str = "") -> list[dict]:
    prompt = (_JUDGE.replace("<<MATERIAL>>", material[:_MATERIAL_CHARS * 2] or "(none)")
              .replace("<<TODAY>>", f"Today is {today}; a date the request gives relative to "
                                    "today (「다음 달 말」) worked out from it is supported.\n\n"
                       if today else "")
              .replace("<<FOCUS>>", f"Look in particular at: {focus}\n\n" if focus else "")
              .replace("<<ANSWER>>", text))
    try:
        reply, spent = await complete(judge, [{"role": "user", "content": prompt}], api_key, 3000)
    except Exception as exc:  # noqa: BLE001 — the scripts still run without the judge
        log.info("post check judge failed: %r", exc)
        return []
    _spend(usage, spent)
    out = []
    for row in _json(reply).get("unsupported") or []:
        if not isinstance(row, dict):
            continue
        quote = str(row.get("quote") or "").strip().strip("「」\"'")[:200]
        if present(quote, material):
            continue
        # A question or a request to the person states nothing; nor does a marked open item,
        # a bare label, or a line of a list of things to check.
        if _ASKS.search(quote) or _OPEN.search(quote) or not _STATES.search(quote) \
                or "자료만으로" in quote \
                or _under_checklist(quote, text):
            continue
        if len(quote) >= 4 and present(quote, text):
            out.append({"kind": "unsupported", "text": quote,
                        "why": str(row.get("why") or "자료에 없는 내용").strip()[:160]})
    return out[:_JUDGED]


async def _confirm(rows: list[dict], material: str, *, complete: Complete, judge: str,
                   api_key: str, usage: dict | None) -> list[dict]:
    """The judged statements a second, narrower reading also finds unstated: only these are
    rewritten and, left word for word, taken out."""
    if not rows:
        return rows
    listed = "\n".join(f"{i}. 「{row['text']}」" for i, row in enumerate(rows, 1))
    prompt = (_CONFIRM.replace("<<MATERIAL>>", material[:_MATERIAL_CHARS * 2] or "(none)")
              .replace("<<STATEMENTS>>", listed))
    try:
        reply, spent = await complete(judge, [{"role": "user", "content": prompt}], api_key, 800)
    except Exception as exc:  # noqa: BLE001 — unconfirmed findings are kept as findings only
        log.info("post check confirm failed: %r", exc)
        return []
    _spend(usage, spent)
    # A statement counts as stated only when the words the reader quotes are the material's.
    stated = set()
    for verdict in _json(reply).get("verdicts") or []:
        if not isinstance(verdict, dict):
            continue
        evidence = str(verdict.get("evidence") or "").strip().strip("「」\"'")
        try:
            row = rows[int(verdict.get("n", 0)) - 1]
        except (TypeError, ValueError, IndexError):
            continue
        if len(evidence) >= 4 and _quoted_from(evidence, material) \
                and _covers(evidence, row["text"]):
            try:
                stated.add(int(verdict.get("n", 0)))
            except (TypeError, ValueError):
                continue
    return [row for i, row in enumerate(rows, 1) if i not in stated]


_WORD = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")
#: Words that carry no claim of their own.
_PLAIN = {"있습", "있다", "합니", "됩니", "하는", "되는", "이후", "이전", "경우", "모든", "기존",
          "해당", "대한", "위해", "통해", "따라", "니다", "그리", "또는", "이용", "사용"}


def _covers(evidence: str, statement: str) -> bool:
    """Whether the evidence holds at least 40% of the statement's words (first two syllables):
    「서비스 종료: 2026-12-31」 does not state 「자동 결제가 중단됩니다」."""
    words = {w[:2] if w[0] >= "가" else w.lower() for w in _WORD.findall(statement)} - _PLAIN
    if not words:
        return True
    low = evidence.lower()
    return sum(1 for w in words if w in low) >= 0.4 * len(words)


def _bigrams(text: str) -> set[str]:
    flat = re.sub(r"[^0-9A-Za-z가-힣]", "", text or "").lower()
    return {flat[i:i + 2] for i in range(len(flat) - 1)}


def _quoted_from(evidence: str, material: str) -> bool:
    """Whether `evidence` is the material's own words (a near-exact quote does)."""
    if _flat(evidence) in _flat(material):
        return True
    want = _bigrams(evidence)
    return bool(want) and len(want & _bigrams(material)) >= 0.85 * len(want)


def _cleaned(reply: str) -> str:
    text = _THINK.sub("", reply or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```[a-z]{0,12}\n?|```$", "", text).strip()
    return text


def drop(text: str, quote: str) -> str:
    """`text` without the sentence (or table cell, or list item) holding `quote`."""
    flat = _flat(quote)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if flat not in _flat(line):
            continue
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = line.split("|")
            for j, cell in enumerate(cells):
                if flat in _flat(cell):
                    cells[j] = " 확인 필요 "
                    break
            else:
                continue
            lines[i] = "|".join(cells)
            return "\n".join(lines)
        if stripped.startswith("#"):
            return text
        lead = re.match(r"\s{0,12}(?:(?:[-*+•·]|\d{1,3}[.)])\s{1,4})?", line).group(0)
        body = line[len(lead):]
        kept = [s for s in units.SENTENCE_BREAK.split(body) if s.strip() and flat not in _flat(s)]
        if kept and len(_flat("".join(kept))) < len(_flat(body)):
            lines[i] = lead + " ".join(s.strip() for s in kept)
        else:
            del lines[i]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return text


def _problems(rows: list[dict]) -> str:
    out = []
    for row in rows:
        hint = f" ({row['fix']})" if row.get("fix") else ""
        out.append(f"- 「{row.get('text') or ''}」 — {row.get('why') or ''}{hint}")
    return "\n".join(out)


async def run(answer: str, *, keys: list[str], request: str, material: str, attached: str,
              computed: list[str], today: str, complete: Complete, model: str, judge: str,
              api_key: str, usage: dict | None = None, specs: Specs = None) -> Outcome:
    """Check, fix by code, rewrite once with the rest, check again and fix by code."""
    outcome = Outcome(answer, skills=list(keys))
    scripts = list(dict.fromkeys(e for key in keys for e in entries(key, specs)))
    on = [p for k in keys if (p := skill_specs.resolve(k, specs)) is not None]
    judged_on = any(p.post_judge for p in on)
    # What the judge and the writer may read as a source: the material, what code printed,
    # and the facts the skills ship (a legal article a skill cites with its source).
    printed = "\n".join(c for c in computed if c and c.strip())[:_MATERIAL_CHARS // 2]
    shipped = "\n\n".join(p.body[:_SKILL_CHARS] for p in on)
    # The judge reads the material and the output; the skill's text (mostly instructions)
    # would read as material to it. The confirming reading may quote the skill's facts too.
    sources = "\n\n".join(x for x in (
        material, f"# 코드·스크립트 출력\n{printed}" if printed else "") if x)
    reference = "\n\n".join(x for x in (sources, f"# 스킬 참고\n{shipped}" if shipped else "")
                             if x)
    args = {"request": request, "material": material, "attached": attached,
            "computed": list(computed), "today": today, "skills": list(keys),
            # A skill whose answers are comparison tables (`tables: true` in its SKILL.md).
            "tables": any(p.tables for p in on)}
    text = answer

    def one_pass(text: str, final: bool) -> tuple[str, list[dict], list[dict], list[dict]]:
        fixed, rewrite, flags = [], [], []
        for owner, script in scripts:
            try:
                module = skill_scripts.module(owner, script)
                result = module.post(text, {**args, "final": final})
            except Exception:  # noqa: BLE001 — a broken check changes nothing
                log.exception("post check %s/%s failed", owner, script)
                continue
            text = str(result.get("text") or text)
            fixed += list(result.get("fixed") or [])
            rewrite += list(result.get("rewrite") or [])
            flags += list(result.get("flags") or [])
        return text, fixed, rewrite, flags

    text, fixed, rewrite, flags = one_pass(text, final=False)
    outcome.fixed += fixed
    judged = []
    if judged_on and (judge or model):
        focus = "; ".join(p.judge_focus for p in on if p.judge_focus)
        judged = await _judge(text, sources, complete=complete, judge=judge or model,
                              api_key=api_key, usage=usage, focus=focus, today=today)
        judged = await _confirm(judged, reference, complete=complete, judge=judge or model,
                                api_key=api_key, usage=usage)
    send = rewrite + judged
    outcome.sent = send
    if send and model:
        try:
            reply, spent = await complete(model, [{"role": "user", "content": _REWRITE.format(
                material=sources[:_MATERIAL_CHARS * 2] or "(none)", problems=_problems(send),
                text=text)}], api_key, min(12000, 2 * len(text) + 1000))
            _spend(usage, spent)
            new = _cleaned(reply)
            # A rewrite that lost half a long answer is not kept.
            if new and len(new) >= min(0.5 * len(text), len(text) - 300):
                text, outcome.rewritten = new, True
            else:
                log.warning("post check rewrite not kept: %d of %d characters", len(new), len(text))
        except Exception as exc:  # noqa: BLE001 — code fixes what is left
            log.warning("post check rewrite failed: %r", exc)
    if send or fixed:
        text, fixed, rewrite, more = one_pass(text, final=True)
        outcome.fixed += fixed
        outcome.left += rewrite
        flags = more
    # A judged statement the writer left word for word is taken out by code.
    for row in judged:
        # Words the person wrote themselves (an edited text's own sentence) never go.
        if present(row["text"], text) and not present(row["text"], material):
            text = drop(text, row["text"])
            outcome.fixed.append({**row, "why": f"{row['why']} → 뺌"})
    outcome.flags = flags
    outcome.text = particles(text)
    return outcome


_PARTICLE = re.compile(r"(\[[^\[\]\n]{1,20}\])(으로|을|이|은|과|이나|이라)(?=[\s.,)」]|$)")
_AFTER_VOWEL = {"으로": "로", "을": "를", "이": "가", "은": "는", "과": "와", "이나": "나",
                "이라": "라"}


def particles(text: str) -> str:
    """A particle after a bracket blank agrees with the blank's last syllable: 「[날짜]으로」 →
    「[날짜]로」, 「[연락처]를」 stays."""
    def agree(m: re.Match) -> str:
        last = next((c for c in reversed(m.group(1)) if "가" <= c <= "힣"), None)
        if last is None:
            return m.group(0)
        final = (ord(last) - 0xAC00) % 28
        if final == 0 or (final == 8 and m.group(2) == "으로"):
            return m.group(1) + _AFTER_VOWEL[m.group(2)]
        return m.group(0)

    return _PARTICLE.sub(agree, text or "")


__all__ = ["Outcome", "drop", "entries", "on_turn", "particles", "picked", "present", "run"]
