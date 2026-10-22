"""Report verify-and-repair; a rewrite kept only if its check clears."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: rewrites per report, worst first
MAX_REPAIRS = 16

_CITE = re.compile(r"\[\d{1,3}\]")

_REPAIR_PROMPT = """Some sentences in the report have problems. Fix only that sentence for each
item, and answer only with the fixed sentences as a JSON array of strings in the same
order. Write nothing else.

Rules:
- Fix only the part pointed out as the problem. Keep the rest of the content, tone and
  length as they are.
- Keep citation numbers [n] as they are. An item given evidence may add that evidence's
  number.
- Answer a table row (starting with 「|」) with a row of the same number of cells.
- Do not make up names, dates, amounts or figures that are not in the material; rewrite the
  sentence so it holds without them (a table cell: 「미정」). Keep existing bracket blanks
  as they are.
- If you judge the finding wrong (e.g. the sentence is about a different case), answer
  with the original sentence unchanged.

Material (values set in the request and the earlier conversation):
{material}

Sources (numbers are the document's citation numbers):
{sources}

Items:
{items}"""


@dataclass(slots=True)
class Finding:
    """Sentence or table row a check found wrong, verbatim."""

    section: int
    sentence: str
    code: str
    why: str
    #: settling source excerpt for the repair
    evidence: str = ""
    #: citations the repair may add (evidence's source)
    may_cite: list[int] = field(default_factory=list)
    #: True while still wrong; None accepts any careful rewrite
    still: Callable[[str], bool] | None = None
    #: lower first under a short budget
    rank: int = 5
    #: False: unconfirmable (web claim, judgement); shown, never rewritten
    auto: bool = True


@dataclass(slots=True)
class Doc:
    sections: list[dict]
    material: str = ""
    request: str = ""
    sources: list[dict] = field(default_factory=list)
    settled: list = field(default_factory=list)
    #: `key_figures.Conflict`s from the material; never merged
    conflicts: list = field(default_factory=list)
    #: what the person gave (request, files, answers, agent instructions), without the
    #: pipeline's own blocks (settled figures, notes, search pages); "" = `material`
    given: str = ""
    #: the meeting itself (request, files, pasted text, answers), without instructions or
    #: skills that speak of meetings in general; "" = `given`
    memo: str = ""


Check = Callable[[Doc], Awaitable[tuple[list[Finding], dict]]]
#: `(model, messages, api_key, max_tokens) -> (text, usage)`
Complete = Callable[..., Awaitable[tuple[str, dict]]]


def _add(usage: dict, spent: dict | None) -> None:
    for key in ("inputTokens", "outputTokens"):
        usage[key] = usage.get(key, 0) + int((spent or {}).get(key, 0))


async def find(doc: Doc, checks: list[Check]) -> tuple[list[Finding], dict]:
    """Findings merged per sentence, worst first."""
    usage: dict[str, int] = {"inputTokens": 0, "outputTokens": 0}
    results = await asyncio.gather(*(check(doc) for check in checks), return_exceptions=True)
    merged: dict[tuple[int, str, bool], Finding] = {}
    for check, result in zip(checks, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("check %s failed: %r", getattr(check, "__name__", check), result)
            continue
        findings, spent = result
        _add(usage, spent)
        for f in findings:
            # judged findings never folded into auto-rewrite ones
            key = (f.section, f.sentence, f.auto)
            if key not in merged:
                merged[key] = f
            elif f.why not in merged[key].why:
                kept = merged[key]
                kept.why = f"{kept.why} 또한 {f.why}"
                kept.evidence = "\n".join(e for e in (kept.evidence, f.evidence) if e)
                kept.may_cite = sorted({*kept.may_cite, *f.may_cite})
                kept.rank = min(kept.rank, f.rank)
                if f.still is not None:
                    first = kept.still
                    kept.still = (lambda s, a=first, b=f.still: bool((a and a(s)) or b(s)))
    return sorted(merged.values(), key=lambda f: f.rank), usage


#: 합니다체 sentence end, and plain 한다체 ends read in it
_POLITE_END = re.compile(r"(?:니다|세요|십시오|까요)[.?!]?[」\")]*\s*$")
_PLAIN_END = re.compile(r"(?<!니)다(?P<tail>[.!]?[」\")]*\s*)$")
_TO_POLITE = tuple((plain, polite) for plain, polite in (
    ("이다", "입니다"), ("한다", "합니다"), ("된다", "됩니다"), ("있다", "있습니다"),
    ("없다", "없습니다"), ("않다", "않습니다"), ("같다", "같습니다"), ("했다", "했습니다"),
    ("였다", "였습니다"), ("었다", "었습니다"), ("았다", "았습니다"), ("겠다", "겠습니다"),
))


def keep_register(old: str, new: str) -> str | None:
    """`new` in `old`'s 합니다체 when the repair ended it in 한다체; None when it cannot be."""
    if not _POLITE_END.search(old) or not (end := _PLAIN_END.search(new)):
        return new
    body = new[: end.start()] + "다"
    for plain, polite in _TO_POLITE:
        if body.endswith(plain):
            return body[: -len(plain)] + polite + end.group("tail")
    return None


def _acceptable(old: str, new: str, finding: Finding) -> bool:
    # multi-line items (question with options) may stay multi-line
    if not new or ("\n" in new and "\n" not in old) or len(new) > 2 * len(old) + 120:
        return False
    row = old.lstrip().startswith("|")
    if row and (not new.startswith("|") or new.count("|") != old.count("|")):
        return False
    had = set(_CITE.findall(old))
    now = set(_CITE.findall(new))
    allowed = had | {f"[{n}]" for n in finding.may_cite}
    if not had <= now or not now <= allowed:
        return False
    return not (finding.still and finding.still(new))


#: a reply that is a marker alone: 「확인 필요」, 「(미정)」
_MARK_ONLY = re.compile(r"\(?[ \t]{0,2}(?:확인[ \t]?필요|미정|해당[ \t]?없음)[ \t]{0,2}\)?[.:]?")


def pasted(old: str, new: str, content: str, memo: str = "") -> bool:
    """A prose rewrite that pastes instead of writing: a marker alone where a sentence was
    (「확인 필요」), a sentence the section already holds or one keeping nothing of the old that
    only says the section's words again (`restates`), a word or two left of a sentence, or
    the memo's own line in its shorthand (`meeting_memo.copied`) where the sentence was prose."""
    from app.services import meeting_memo

    if old.lstrip().startswith("|"):
        return False
    if _MARK_ONLY.fullmatch(new.strip()) and not _MARK_ONLY.fullmatch(old.strip()):
        return True
    flat = re.sub(r"[\s,.:;·!?()「」\"']", "", new)
    rest = re.sub(r"[\s,.:;·!?()「」\"']", "", content.replace(old, "", 1))
    if len(flat) >= 16 and flat in rest:
        return True
    if restates(new, old, content.replace(old, "", 1)):
        return True
    # a word or two left of a whole sentence (「함.」)
    if len(flat) < 8 and len(flat) * 3 < len(re.sub(r"[\s,.:;·!?()「」\"']", "", old)):
        return True
    pieces = [p for p in re.split(r"(?<=[.!?])\s+", new.strip()) if p.strip()]
    return bool(memo) and not meeting_memo.copied(old, memo) \
        and any(meeting_memo.copied(p, memo) for p in pieces)


#: a blank for the person to fill: 「[회의 일자]」, 「[담당자]」
_BLANK = re.compile(r"\[[^\[\]\n\d]{1,20}\]")


def restates(new: str, old: str, rest: str) -> bool:
    """A repair that keeps no word of the sentence it fixes and says only what the rest of the
    section says (「자료에 명시된 예정 안건은 없고, 일정·장소만 기재합니다」 → 「다음 회의는 10/21
    화 10시 3층 회의실입니다」 beside 「10/21 10시, 3층 회의실에서 열립니다」)."""
    said = {w[:2] for w in _stems(new)}
    return len(said) >= 3 and not said & {w[:2] for w in _stems(old)} \
        and said <= {w[:2] for w in _stems(rest)}


_PARTICLE_HEAD = re.compile(r"^(?:에서|에는|에|은|는|이|가|을|를|의|으로|로|와|과)")


def filled_twin(new: str, content: str) -> str | None:
    """The sentence of `content` that `new` restates with a blank filled: 「[회의 일자]에 3층
    회의실에서 … 열었습니다.」 for 「2026년 10월 14일 3층 회의실에서 … 열었습니다.」; the
    words around each blank the same, a value of at most 30 letters in its place."""
    from app.services import units

    target = re.sub(r"\s+", "", new or "").rstrip(".!?")
    if not target or _BLANK.search(new):
        return None
    for sentence in units.SENTENCE_BREAK.split(content or ""):
        sentence = sentence.strip()
        if sentence.startswith("|") or not _BLANK.search(sentence):
            continue
        parts = [re.sub(r"\s+", "", p) for p in _BLANK.split(sentence.rstrip(".!? "))]
        # a blank's particle may go with it: 「[회의 일자]에 3층 …」 → 「… 14일 3층 …」
        parts = parts[:1] + [p if target.find(p) > 0 else _PARTICLE_HEAD.sub("", p, count=1)
                             for p in parts[1:]]
        if sum(len(p) for p in parts) < 12 or not target.startswith(parts[0]) \
                or not target.endswith(parts[-1]):
            continue
        at, end, ok = len(parts[0]), len(target) - len(parts[-1]), True
        for k, part in enumerate(parts[1:], start=1):
            found = end if k == len(parts) - 1 else target.find(part, at + 1)
            if found < 0 or found > end or not 0 < found - at <= 30:
                ok = False
                break
            at = found + len(part)
        if ok:
            return sentence
    return None


#: the writer reporting its own act of writing: 「그 값을 넣지 않은 문장으로 쓰도록 했습니다」,
#: 「회의록에 기록하지 않았습니다」, 「해당 내용을 뺐습니다」
WROTE = re.compile(
    r"(?:쓰|적|기록하|기재하|넣|표기하|표시하|반영하|작성하|싣|담|남기|제외하|삭제하|수정하|고치|바꾸)"
    r"(?:도록\s?(?:했|하였)|지\s?(?:않았|않도록\s?했)|었|았|였)습니다"
    r"|(?:썼|적었|뺐|넣었|고쳤|바꿨|실었|담았|남겼)습니다")
#: what a writer's note names of the document itself, which ties it to no sentence
_DOC_WORDS = frozenset(("회의록", "보고서", "문서", "문장", "내용", "자료", "항목", "부분",
                        "참고로", "해당"))
_SENTENCE_END = re.compile(r"(?<=[다요][.!?])\s+")


def _stems(text: str) -> set[str]:
    out = set()
    for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or ""):
        word = re.sub(r"(?:으로|에서|에게|까지|부터|은|는|이|가|을|를|에|의|과|와|로|도|만)$", "",
                      word)
        if len(word) >= 2:
            out.add(word.lower())
    return out


def edit_note(sentence: str, near: str = "") -> bool:
    """A sentence reporting how the text was written (「그 값을 넣지 않은 문장으로 쓰도록
    했습니다」): an act of writing, no figure, and no word of `near` (the sentence it stands
    for) but the document's own (「회의록」, 「문장」)."""
    body = (sentence or "").strip()
    # the act is the sentence's predicate, at its end
    if not body or body.startswith("|") or not WROTE.search(body[-40:]) \
            or re.search(r"\d", body):
        return False
    return not (_stems(body) & _stems(near)) - _DOC_WORDS


def without_edit_notes(old: str, new: str) -> str:
    """A repair reply less the sentences that only report the edit; "" when nothing else is
    left (the reply then repairs nothing)."""
    if not new or new.lstrip().startswith("|"):
        return new
    parts = [p for p in _SENTENCE_END.split(new.strip()) if p.strip()]
    kept = [p for p in parts if not edit_note(p, old)]
    return new if len(kept) == len(parts) else " ".join(kept)


#: keys a repair reply puts its fixed sentence under when it answers objects, not strings
_SENTENCE_KEYS = ("sentence", "fixed", "revised", "new", "text", "corrected")


def _sentence_of(item: object) -> str | None:
    """The fixed sentence of one repair reply item: a string, or the one sentence of an object
    that echoes the request's shape (`{"sentence": …}`); None for anything else."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in _SENTENCE_KEYS:
            if isinstance(item.get(key), str):
                return item[key]
    return None


async def repair(
    doc: Doc, findings: list[Finding], *, complete: Complete, model: str, api_key: str,
    clean: Callable[[str], str] = lambda s: s,
) -> tuple[list[dict], list[Finding], dict]:
    """One model call per section; `(sections, unrepaired findings, usage)`."""
    usage: dict[str, int] = {"inputTokens": 0, "outputTokens": 0}
    out = [dict(sec) for sec in doc.sections]
    fixable = [f for f in findings if f.auto]
    todo = fixable[:MAX_REPAIRS]
    left = fixable[MAX_REPAIRS:] + [f for f in findings if not f.auto]
    by_section: dict[int, list[Finding]] = {}
    for f in todo:
        by_section.setdefault(f.section, []).append(f)

    async def one(index: int, items: list[Finding]):
        listed = [
            {"sentence": f.sentence, "problem": f.why,
             **({"evidence": f.evidence[:1200]} if f.evidence else {})}
            for f in items
        ]
        return await complete(
            model,
            [{"role": "user", "content": _REPAIR_PROMPT.format(
                material=(doc.material or "(없음)")[:5000],
                sources="\n".join(
                    f"[{src.get('ordinal', n + 1)}] {src.get('title') or ''} "
                    f"{str(src.get('quote') or '')[:240]}"
                    for n, src in enumerate(doc.sources or [])
                )[:4000] or "(없음)",
                items=json.dumps(listed, ensure_ascii=False),
            )}],
            api_key,
            min(4000, 320 * len(items) + 300),
        )

    replies = await asyncio.gather(
        *(one(i, items) for i, items in by_section.items()), return_exceptions=True
    )
    for (index, items), reply in zip(by_section.items(), replies, strict=True):
        if isinstance(reply, BaseException):
            log.info("repair of section %d failed: %r", index, reply)
            left.extend(items)
            continue
        text, spent = reply
        _add(usage, spent)
        match = re.search(r"\[.*\]", text or "", re.S)
        try:
            rewritten = json.loads(match.group(0)) if match else []
        except json.JSONDecodeError:
            rewritten = []
        if not isinstance(rewritten, list) or len(rewritten) != len(items):
            left.extend(items)
            continue
        content = str(out[index].get("content") or "")
        for finding, new in zip(items, rewritten, strict=True):
            new = _sentence_of(new)
            if new is None:
                # an unparsable reply item (e.g. `{'sentence': …}`) never goes into the text
                left.append(finding)
                continue
            # the writer's account of the fix (「…쓰도록 했습니다」) is no sentence of the text
            new = without_edit_notes(finding.sentence, clean(new.strip().strip("「」")))
            # a fix keeps the document's 합니다체 (no 「…대상으로 한다.」 in a notice)
            new = keep_register(finding.sentence, new) or ""
            if new and _acceptable(finding.sentence, new, finding) \
                    and (twin := filled_twin(new, content.replace(finding.sentence, "", 1))):
                # the fix fills another sentence's blank (「[회의 일자]에 …」 → 「2026년 10월
                # 14일 …」): that sentence takes it, and this one stands unrepaired
                content = content.replace(twin, new, 1)
                left.append(finding)
                log.info("repair of [%s] filled the blank it restates in %r", finding.code,
                         out[index].get("heading"))
            elif new != finding.sentence and _acceptable(finding.sentence, new, finding) \
                    and not pasted(finding.sentence, new, content, doc.memo):
                content = content.replace(finding.sentence, new, 1)
                log.info("repaired [%s] in %r", finding.code, out[index].get("heading"))
            else:
                left.append(finding)
        out[index]["content"] = content
    return out, left, usage


async def verify_and_repair(
    doc: Doc, checks: list[Check], *, complete: Complete, model: str, api_key: str,
    clean: Callable[[str], str] = lambda s: s,
    normalize: Callable[[list[dict]], list[dict]] = lambda sections: sections,
) -> tuple[list[dict], list[Finding], dict]:
    """Find, repair, normalize, find again; `(sections, remaining findings, usage)`."""
    usage: dict[str, int] = {"inputTokens": 0, "outputTokens": 0}
    findings, spent = await find(doc, checks)
    _add(usage, spent)
    if not findings:
        return doc.sections, [], usage
    log.info("verify found %d: %s", len(findings),
             sorted({f.code for f in findings}))
    sections, left, spent = await repair(
        doc, findings, complete=complete, model=model, api_key=api_key, clean=clean
    )
    _add(usage, spent)
    sections = normalize(sections)
    # unconfirmable model findings reported, not fixed
    shown = [f for f in left if not f.auto and f.sentence in "\n".join(
        str(s.get("content") or "") for s in sections)]
    # recheck with free code checks only
    again = Doc(sections, doc.material, doc.request, doc.sources, doc.settled, doc.conflicts,
                doc.given)
    remaining, spent = await find(again, [c for c in checks if not getattr(c, "uses_model", False)])
    _add(usage, spent)
    return sections, remaining + shown, usage


def model_check(fn: Callable[[Doc], Awaitable[tuple[list[Finding], dict]]]) -> Check:
    """Mark a model-calling check (runs once, before repair)."""
    fn.uses_model = True  # type: ignore[attr-defined]
    return fn


__all__ = [
    "Check", "Doc", "Finding", "MAX_REPAIRS", "find", "model_check", "repair",
    "verify_and_repair",
]
