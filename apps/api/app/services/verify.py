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
- Do not make up names, dates, amounts or figures that are not in the material; put the
  slot's name in brackets, like 「[담당자]」 「[기한]」 「[금액]」. Keep existing bracket blanks
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
            new = clean(str(new).strip().strip("「」"))
            # a fix keeps the document's 합니다체 (no 「…대상으로 한다.」 in a notice)
            new = keep_register(finding.sentence, new) or ""
            if new != finding.sentence and _acceptable(finding.sentence, new, finding):
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
    again = Doc(sections, doc.material, doc.request, doc.sources, doc.settled, doc.conflicts)
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
