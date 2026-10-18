"""Skill post-checks on chat answers: code fixes, optional judge, one rewrite, final code pass."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from app.services import (
    internal_words,
    skill_packages,
    skill_scripts,
    skill_specs,
    units,
    value_guard,
)

log = logging.getLogger(__name__)

Complete = Callable[[str, list[dict], str, int], Awaitable[tuple[str, dict]]]

_THINK = re.compile(r"<think>[\s\S]{0,60000}?</think>", re.I)
_MATERIAL_CHARS = 16000
_SKILL_CHARS = 6000
_JUDGED = 12

_JUDGE = """You check an answer against the material it was written from. The material is \
the person's request and the files and text they gave, and what tools returned this turn \
(code and scripts, web search results, fetched pages); nothing else counts as a source.

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
    "material does not give: take out the unsupported clause or replace it with what the "
    "material states; when the sentence needs the value, keep it and write 「(확인 필요)」 "
    "right after it. Never replace a value with a bracket blank such as [날짜] or [수치]; "
    "brackets are only for a field the person fills in (a signature, a contact). Never empty "
    "a paragraph or leave a table with only its header. For an original "
    "value or wording that was changed: restore the original exactly. For a plan or judgment "
    "written as settled: write it as the material hedges it. Keep everything else exactly as "
    "written — structure, order, headings, tables, the other wording and the language. Add no "
    "new fact, number, date, name, feature or procedure. Reply with the whole corrected answer "
    "only, no preface and no word about the problems or the check — the reader never sees "
    "them.\n\n# Material\n{material}\n\n# Problems\n{problems}\n\n# Answer\n{text}"
)

#: A strong writer gets the findings as evidence to weigh, not orders (told to 「take out the
#: unsupported clause」, it would cut a feature's obvious use).
_REVISE = (
    "Below are the material and your answer. A check read the answer against the material and "
    "noted the items listed under Notes; it may be wrong. Weigh each note yourself against the "
    "material. Keep a sentence exactly as it is when the material states it or it follows from "
    "the material: a listed feature's plain use or benefit, a calculation from the material's "
    "values, your own recommendation, caveat or suggestion, a step a loaded skill asked for. "
    "When a note is right, revise only that sentence: replace the claim with what the material "
    "says, state it as an assumption or a suggestion, or write 「(확인 필요)」 right after the "
    "value; take a sentence out only when it asserts something invented and the answer reads "
    "complete without it. Never use a bracket blank such as [수치]; never empty a paragraph or "
    "a table. Keep everything else exactly as written — structure, order, headings, tables, "
    "the other wording and the language. Add no new fact. Reply with the whole answer only, "
    "no preface and no word about the notes or the check — the reader never sees them."
    "\n\n# Material\n{material}\n\n# Notes\n{problems}\n\n# Answer\n{text}"
)


Specs = Mapping[str, skill_specs.Spec] | None


def entries(key: str, specs: Specs = None) -> list[tuple[str, str]]:
    """(package, script) pairs from post_check, in order; shipped scripts only."""
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
    """Post-checked skills returned by use_skill this turn."""
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
    """Turn skills naming post checks, stable order."""
    named = {k for k in keys or () if entries(k, specs)} | picked(list(outputs or []), specs)
    return sorted(named)


@dataclass(slots=True)
class Outcome:
    text: str
    skills: list[str] = field(default_factory=list)
    #: code fixes, first and final pass
    fixed: list[dict] = field(default_factory=list)
    #: sent to the writer
    sent: list[dict] = field(default_factory=list)
    #: unsettled after final pass (shown only)
    left: list[dict] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)
    rewritten: bool = False
    #: sentences taken out on the judge's confirmed finding: not to be brought back
    dropped: list[str] = field(default_factory=list)

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


#: string left open at line end
_OPEN_STRING = re.compile(r'^(\s{0,40}"\w{1,20}"\s{0,4}:\s{0,4}"[^"\n]{0,600})$', re.M)
_PAIR = re.compile(r'"quote"\s{0,4}:\s{0,4}"([^"\n]{1,400})"'
                   r'(?:[\s,]{0,8}"why"\s{0,4}:\s{0,4}"([^"\n]{0,400}))?')
#: string broken over a line
_SPLIT_CLOSE = re.compile(r'\\?\n\s{0,40}"\s{0,4}\n')


def _json(text: str) -> dict:
    """Reply JSON object, mending raw line breaks and open strings; quote-by-quote fallback."""
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
#: a recommendation or caveat of the answer's own, which the judge's prompt leaves out but a
#: small judge lists anyway (「게시 전에 그 수치의 산정 기준을 확인하세요」)
_SUGGESTS = re.compile(
    r"(?:확인하|검토하|고려하|참고하|살펴보|점검하)(?:세요|십시오|시기\s?바랍니다)"
    r"|(?:권합니다|권장합니다|권고합니다|추천합니다|제안합니다|바람직합니다)[.!]?\s*$"
    r"|(?:하는|두는|쓰는|확인하는)\s?(?:것이|편이)\s?(?:좋|낫|안전)")


_OPEN = re.compile(
    r"확인\s?필요|출처\s?미확인|자료\s?없음|판단\s?보류|가정|미정|\[[^\]\n]{1,20}\]")
#: statement = predicate or figure
_STATES = re.compile(r"\d|(?:다|요|음|됨|함|임|니다)[.!)」]?\s*$|(?:다|니다)[.,]\s")
_CHECKLIST = re.compile(
    r"확인할\s?것|확인\s?사항|확인이\s?필요|보내기\s?전|채울\s?것|질문|물어볼|남은\s?일")
_HEAD_LINE = re.compile(r"^\s{0,3}(?:#{1,6}\s|\*\*[^*\n]{1,40}\*\*\s*:?\s*$)")


def _under_checklist(quote: str, text: str) -> bool:
    """Whether the quote sits under a to-check/to-ask heading."""
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
        # questions, requests, open items, bare labels, checklist lines state nothing;
        # the answer's own suggestions are its judgment, not a fact to source
        if _ASKS.search(quote) or _OPEN.search(quote) or not _STATES.search(quote) \
                or (_SUGGESTS.search(quote) and not re.search(r"\d", quote)) \
                or "자료만으로" in quote \
                or _under_checklist(quote, text):
            continue
        if len(quote) >= 4 and present(quote, text):
            out.append({"kind": "unsupported", "text": quote,
                        "why": str(row.get("why") or "자료에 없는 내용").strip()[:160]})
    return out[:_JUDGED]


async def _confirm(rows: list[dict], material: str, *, complete: Complete, judge: str,
                   api_key: str, usage: dict | None) -> list[dict]:
    """Judged statements a narrower second reading confirms unstated."""
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
    # stated only if the quoted evidence is the material's
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
#: words carrying no claim
_PLAIN = {"있습", "있다", "합니", "됩니", "하는", "되는", "이후", "이전", "경우", "모든", "기존",
          "해당", "대한", "위해", "통해", "따라", "니다", "그리", "또는", "이용", "사용"}


def _covers(evidence: str, statement: str) -> bool:
    """Whether evidence covers ≥40% of the statement's words (first two syllables)."""
    words = {w[:2] if w[0] >= "가" else w.lower() for w in _WORD.findall(statement)} - _PLAIN
    if not words:
        return True
    low = evidence.lower()
    return sum(1 for w in words if w in low) >= 0.4 * len(words)


def _bigrams(text: str) -> set[str]:
    flat = re.sub(r"[^0-9A-Za-z가-힣]", "", text or "").lower()
    return {flat[i:i + 2] for i in range(len(flat) - 1)}


def _quoted_from(evidence: str, material: str) -> bool:
    """Whether evidence quotes the material (near-exact)."""
    if _flat(evidence) in _flat(material):
        return True
    want = _bigrams(evidence)
    return bool(want) and len(want & _bigrams(material)) >= 0.85 * len(want)


def _cleaned(reply: str) -> str:
    text = _THINK.sub("", reply or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```[a-z]{0,12}\n?|```$", "", text).strip()
    return internal_words.strip_echoed_heading(text)


#: a name or path a sentence can only have from somewhere: 「v2」, 「/messages」, 「iOS 18」
_IDENT = re.compile(r"(?<![A-Za-z0-9_/])(?:/[A-Za-z][A-Za-z0-9_./-]*|[A-Za-z][A-Za-z0-9_.-]*\d"
                    r"[A-Za-z0-9_.-]*|[A-Z]{2,}[A-Za-z0-9_-]*)")


def names_the_material(statement: str, material: str) -> bool:
    """Whether every name, version and path the statement writes is the material's own
    (「… v2의 /messages로 전환해 주세요」 from 「(v2 /messages 사용)」 was taken out)."""
    names = {m.group(0).rstrip(".") for m in _IDENT.finditer(statement or "")}
    names = {n for n in names if len(n) >= 2}
    low = (material or "").lower()
    return bool(names) and any(re.search(r"\d|/", n) for n in names) \
        and all(n.lower() in low for n in names)


def drop(text: str, quote: str) -> str:
    """text without the sentence/cell/item holding quote."""
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


#: what code can look up in the evidence: a figure (「8,000명」 → 8000), a Latin name or
#: version (「Free」, 「ISO 27001」, 「v2」), a quoted phrase
_KEY_NUMBER = re.compile(r"(?<![\d.])\d[\d,]{0,15}(?:\.\d{1,6})?")
_KEY_LATIN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9+-]{1,30}")
_KEY_QUOTED = re.compile(r"「([^」\n]{2,40})」|“([^”\n]{2,40})”")
#: a counted figure is looked up with its counter: in a long search page a bare 「9」 or
#: 「306」 turns up by chance (「306명」, 「9개 투자 라운드」); so are 억·조 (「89억」 not
#: matched on a bare 89). Dates and 만 stay bare (「2026. 4.」, 「1.2만」 against 「12,000」).
_COUNTED = re.compile(
    r"(?<![\d.])(\d[\d,]{0,15}(?:\.\d{1,6})?)\s?(%|개사|라운드|[명개곳건회원배위대종차석억조])")


def key_tokens(quote: str) -> list[str]:
    """The figures, Latin names and quoted phrases of a statement, in the form the evidence
    is searched in (no thousands commas, lower case)."""
    out = [m.group(0).replace(",", "") for m in _KEY_NUMBER.finditer(quote or "")]
    out += [m.group(0).lower() for m in _KEY_LATIN.finditer(quote or "")]
    out += [_flat(next(g for g in m.groups() if g)).lower()
            for m in _KEY_QUOTED.finditer(quote or "")]
    return list(dict.fromkeys(t for t in out if t and t not in ("0", "1")))


def _searchable(text: str) -> str:
    return re.sub(r"(?<=\d),(?=\d)", "", _flat(text)).lower()


def code_reading(quote: str, evidence: str) -> str:
    """What code can say about a judged statement before any of it is rewritten or taken
    out: 「absent」 when one of its key tokens is in none of the evidence, 「present」 when all
    are, 「unchecked」 when it has none."""
    tokens = key_tokens(quote)
    if not tokens:
        return "unchecked"
    flat = _searchable(evidence)
    for number, counter in _COUNTED.findall(quote or ""):
        figure = re.escape(number.replace(",", ""))
        if not re.search(rf"(?<![\d.]){figure}\s?{re.escape(counter[0])}", flat):
            return "absent"
    for token in tokens:
        if token[0].isdigit():
            if not re.search(rf"(?<![\d.]){re.escape(token)}(?!\d|\.\d)", flat):
                return "absent"
        elif token not in flat:
            return "absent"
    return "present"


#: the citations right after a statement: 「… 인수했다 [[1]](https://…)[[3]](https://…)」
_CITE_RUN = re.compile(r"[\s.,。]{0,3}((?:\[\[\d{1,3}\]\]\(https?://[^)\s]{1,500}\)[\s,]*)+)")
_CITED_URL = re.compile(r"\[\[\d{1,3}\]\]\((https?://[^)\s]{1,500})\)")
def cited_pages(quote: str, text: str, computed: list[str]) -> str | None:
    """What the pages a statement cites say: the search entries and fetched pages under the
    addresses of the `[[n]](url)` run after it, or None when it cites none or one of them
    was not read this turn (a name found on some other page does not back a statement its own
    source does not make)."""
    phrase = (quote or "").strip().rstrip(".!?。 ")
    at = text.find(phrase) if len(phrase) >= 4 else -1
    if at < 0:
        return None
    end = at + len(phrase)
    line_end = text.find("\n", end)
    rest = text[end:line_end if line_end >= 0 else len(text)]
    first = _CITED_URL.search(rest)
    if first is None or re.search(r"[.!?。]\s+\S", rest[:first.start()]):
        return None
    run = _CITE_RUN.match(rest, max(0, first.start() - 3)) or _CITE_RUN.search(rest)
    return value_guard.pages_at(_CITED_URL.findall(run.group(1) if run else first.group(0)),
                                computed)


#: the mark a judged statement gets where it stands (`value_guard.MARK`)
MARK = "(확인 필요)"


def mark(text: str, quote: str) -> str:
    """`text` with 「(확인 필요)」 right after the quoted statement, before its full stop; once."""
    phrase = (quote or "").strip().rstrip(".!?。 ")
    at = text.find(phrase) if len(phrase) >= 4 else -1
    if at < 0:
        return text
    end = at + len(phrase)
    if text[end:end + len(MARK) + 1].lstrip().startswith(MARK):
        return text
    return f"{text[:end]}{MARK}{text[end:]}"


def _problems(rows: list[dict]) -> str:
    out = []
    for row in rows:
        hint = f" ({row['fix']})" if row.get("fix") else ""
        out.append(f"- 「{row.get('text') or ''}」 — {row.get('why') or ''}{hint}")
    return "\n".join(out)


async def run(answer: str, *, keys: list[str], request: str, material: str, attached: str,
              computed: list[str], today: str, complete: Complete, model: str, judge: str,
              api_key: str, usage: dict | None = None, specs: Specs = None,
              strong: bool = False) -> Outcome:
    """Check, code-fix, rewrite once, recheck and code-fix.

    `strong`: the writer is a strong model. It judges its own answer (no weaker judge reads
    it), gets the findings as evidence for one revision of its own, and nothing it kept is
    taken out afterwards; the findings it kept are shown in the step only. For any writer a
    judged statement is rewritten or taken out only on code's reading (`code_reading`)."""
    outcome = Outcome(answer, skills=list(keys))
    scripts = list(dict.fromkeys(e for key in keys for e in entries(key, specs)))
    on = [p for k in keys if (p := skill_specs.resolve(k, specs)) is not None]
    judged_on = any(p.post_judge for p in on)
    # judge/writer sources: material, code output, skill-shipped facts
    printed = "\n".join(c for c in computed if c and c.strip())[:_MATERIAL_CHARS // 2]
    shipped = "\n\n".join(p.body[:_SKILL_CHARS] for p in on)
    # skill text excluded from judge material (instructions); confirmer may quote its facts
    sources = "\n\n".join(x for x in (
        material, f"# 도구 출력(코드·스크립트·웹 검색)\n{printed}" if printed else "") if x)
    reference = "\n\n".join(x for x in (sources, f"# 스킬 참고\n{shipped}" if shipped else "")
                             if x)
    args = {"request": request, "material": material, "attached": attached,
            "computed": list(computed), "today": today, "skills": list(keys),
            # comparison-table skills (tables: true)
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
    # a weaker model never judges a strong writer's answer: the writer reads it itself
    reader = model if strong and model else judge or model
    if judged_on and reader:
        focus = "; ".join(p.judge_focus for p in on if p.judge_focus)
        judged = await _judge(text, sources, complete=complete, judge=reader,
                              api_key=api_key, usage=usage, focus=focus, today=today)
        judged = await _confirm(judged, reference, complete=complete, judge=reader,
                                api_key=api_key, usage=usage)
    # Marking apart from deleting (docs/architecture.md, "Mechanisms by model group"): code
    # reads each finding against all the evidence, the search pages whole. One whose figures
    # and names are all there is dropped as a finding (every group). One code cannot check (no
    # figure, no name) is marked where it stands, never rewritten or cut, for a small writer;
    # a strong writer gets it as a note to weigh. Only one with a figure or name in none of the
    # evidence (or a script's own finding) goes to a small writer's rewrite and may come out.
    everything = "\n".join([request, material, attached, *computed, shipped])
    scripted = [_flat(str(r.get("text") or "")) for r in rewrite]
    held, marked, absent = [], [], []
    for row in judged:
        quote = _flat(row["text"])
        # a statement citing pages read this turn is read against those pages only
        cited = cited_pages(row["text"], text, list(computed))
        if cited is not None and not value_guard.same_script(row["text"], cited):
            # an English page for a Korean sentence: its figures are written another way
            cited = None
        reading = "absent" if any(s and (s in quote or quote in s) for s in scripted) \
            else code_reading(row["text"], everything if cited is None else "\n".join(
                [request, material, attached, cited]))
        if reading == "present":
            held.append({**row, "why": f"{row['why']} (근거에 있는 값이라 남김)"})
        elif reading == "absent" or strong:
            absent.append(row)
        else:
            marked.append(row)
    judged = absent
    for row in marked:
        text = mark(text, row["text"])
        held.append({**row, "why": f"{row['why']} (코드로 확인할 값이 없어 표시만)"})
    send = rewrite + judged
    outcome.sent = send
    if send and model:
        prompt = _REVISE if strong else _REWRITE
        try:
            reply, spent = await complete(model, [{"role": "user", "content": prompt.format(
                material=(reference if strong else sources)[:_MATERIAL_CHARS * 2] or "(none)",
                problems=_problems(send), text=text)}], api_key, min(12000, 2 * len(text) + 1000))
            _spend(usage, spent)
            new = internal_words.strip_rewrite_preface(_cleaned(reply), text)
            # rewrite that lost half a long answer discarded
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
    flags = held + flags
    # judged statement left verbatim: a strong writer kept it on the evidence (shown only); a
    # small writer's, confirmed unstated, is removed
    for row in judged:
        # user-written sentences never removed
        if present(row["text"], text) and not present(row["text"], material):
            if strong:
                flags.append({**row, "why": f"{row['why']} (작성 모델이 근거를 보고 남김)"})
                continue
            if names_the_material(row["text"], sources):
                # its names and paths are the material's: kept and shown, never removed
                flags.append({**row, "why": f"{row['why']} (자료의 항목을 풀어 쓴 문장이라 남김)"})
                continue
            text = drop(text, row["text"])
            outcome.dropped.append(row["text"])
            outcome.fixed.append({**row, "why": f"{row['why']} → 뺌"})
    outcome.flags = flags
    outcome.text = particles(text)
    return outcome


#: the list the answer step put under the answer: 「### 출처」 then 「- [3] [제목](url)」
_LIST_HEADING = re.compile(r"^#{2,4}\s*(?:출처|Sources)\s*$", re.M)
_LIST_LINE = re.compile(r"^\s{0,3}[-*]\s{1,3}\[(\d{1,3})\]\s")
_LINKED = re.compile(r"[ \t]?\[\[(\d{1,3})\]\]\([^)\s]{1,500}\)")


def _split_list(text: str) -> tuple[str, str, dict[int, str], str]:
    """(body, heading, {n: list line}, what follows the list) of `text`; no heading → the
    body is all of it."""
    found = list(_LIST_HEADING.finditer(text or ""))
    if not found:
        return text or "", "", {}, ""
    at = found[-1]
    listed: dict[int, str] = {}
    lines = text[at.end():].split("\n")
    last = 0
    for i, line in enumerate(lines):
        if m := _LIST_LINE.match(line):
            listed.setdefault(int(m.group(1)), line.rstrip())
            last = i
        elif line.strip() and listed:
            break
    tail = "\n".join(lines[last + 1:]) if listed else "\n".join(lines)
    return text[:at.start()].rstrip(), at.group(0), listed, tail


def cited_sources_listed(before: str, text: str) -> str:
    """`text` with each linked 「[[n]](url)」 of its body listed under it.

    A rewrite can take list lines out with the sentences it fixed, or a guard can give a cell
    back after its line had gone: a line the list had before comes back; a citation no list
    ever had comes out."""
    body, heading, listed, tail = _split_list(text)
    _, old_heading, was, _ = _split_list(before)
    if not was:
        return text
    cited = list(dict.fromkeys(int(n) for n in _LINKED.findall(body)))
    missing = [n for n in cited if n not in listed]
    if not missing:
        return text
    body = _LINKED.sub(lambda m: "" if int(m.group(1)) not in listed | was else m.group(0), body)
    listed |= {n: was[n] for n in missing if n in was}
    if not listed:
        return body + (f"\n\n{tail.strip()}" if tail.strip() else "")
    return f"{body}\n\n{heading or old_heading}\n" + "\n".join(
        listed[n] for n in sorted(listed)) + (f"\n{tail.rstrip()}" if tail.strip() else "")


_PARTICLE = re.compile(r"(\[[^\[\]\n]{1,20}\])(으로|을|이|은|과|이나|이라)(?=[\s.,)」]|$)")
_AFTER_VOWEL = {"으로": "로", "을": "를", "이": "가", "은": "는", "과": "와", "이나": "나",
                "이라": "라"}


def particles(text: str) -> str:
    """Particle after a blank agrees with its last syllable (「[날짜]으로」 → 「[날짜]로」)."""
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
