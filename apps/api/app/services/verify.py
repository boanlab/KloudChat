"""Verify-and-repair for a written report: checks find, one repair rewrites, checks rerun.

A rewrite is kept only when the check that raised it no longer does.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

#: Sentences rewritten per report, worst first.
MAX_REPAIRS = 16

_CITE = re.compile(r"\[\d{1,3}\]")

_REPAIR_PROMPT = """보고서의 문장들에 문제가 있다. 항목마다 그 문장만 고쳐서, 고친 문장들을 같은
순서의 JSON 문자열 배열로만 답하라. 다른 말은 쓰지 마라.

규칙:
- 문제로 지적된 부분만 고친다. 나머지 내용·어조·길이는 그대로 둔다.
- 인용 번호 [n]은 그대로 둔다. 근거 자료가 주어진 항목은 그 자료의 번호를 붙여도 된다.
- 표의 행(「|」로 시작)은 같은 칸 수의 행으로 답한다.
- 지적이 틀렸다고 판단되면(다른 경우를 말하는 문장 등) 원문을 그대로 답한다.

자료(요청과 앞 대화에서 정한 값):
{material}

참고 자료 목록(번호는 문서의 인용 번호):
{sources}

항목:
{items}"""


@dataclass(slots=True)
class Finding:
    """A sentence (or a table row) a check found wrong, exactly as it stands in a section."""

    section: int
    sentence: str
    code: str
    why: str
    #: A source excerpt that settles it, shown to the repair.
    evidence: str = ""
    #: Citation numbers the repair may add because the evidence is that source.
    may_cite: list[int] = field(default_factory=list)
    #: True while a rewrite is still wrong by this check; None keeps any careful rewrite.
    still: Callable[[str], bool] | None = None
    #: Lower comes first when the repair budget is short.
    rank: int = 5
    #: False for a finding no check can confirm a rewrite of (a web claim, a teaching
    #: judgement): shown to the reader, never rewritten on a judge's word alone.
    auto: bool = True


@dataclass(slots=True)
class Doc:
    sections: list[dict]
    material: str = ""
    request: str = ""
    sources: list[dict] = field(default_factory=list)
    settled: list = field(default_factory=list)


#: A check reads the document and names what it finds; a model check may spend tokens.
Check = Callable[[Doc], Awaitable[tuple[list[Finding], dict]]]
#: `(model, messages, api_key, max_tokens) -> (text, usage)`.
Complete = Callable[..., Awaitable[tuple[str, dict]]]


def _add(usage: dict, spent: dict | None) -> None:
    for key in ("inputTokens", "outputTokens"):
        usage[key] = usage.get(key, 0) + int((spent or {}).get(key, 0))


async def find(doc: Doc, checks: list[Check]) -> tuple[list[Finding], dict]:
    """Every check's findings merged to one per sentence, worst first."""
    usage: dict[str, int] = {"inputTokens": 0, "outputTokens": 0}
    results = await asyncio.gather(*(check(doc) for check in checks), return_exceptions=True)
    merged: dict[tuple[int, str], Finding] = {}
    for check, result in zip(checks, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("check %s failed: %r", getattr(check, "__name__", check), result)
            continue
        findings, spent = result
        _add(usage, spent)
        for f in findings:
            key = (f.section, f.sentence)
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


def _acceptable(old: str, new: str, finding: Finding) -> bool:
    # A multi-line item (an assessment question with its options) may come back multi-line.
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
    """One model call per section; a rewrite is kept only when it passes `_acceptable`.

    Returns (sections, unrepaired findings, usage)."""
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
    """Find, repair, normalize, find again.

    Returns (sections, findings still standing after the repair, usage)."""
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
    # Model-only findings no check can confirm are reported, not fixed.
    shown = [f for f in left if not f.auto and f.sentence in "\n".join(
        str(s.get("content") or "") for s in sections)]
    # Re-check with the free code checks only.
    again = Doc(sections, doc.material, doc.request, doc.sources, doc.settled)
    remaining, spent = await find(again, [c for c in checks if not getattr(c, "uses_model", False)])
    _add(usage, spent)
    return sections, remaining + shown, usage


def model_check(fn: Callable[[Doc], Awaitable[tuple[list[Finding], dict]]]) -> Check:
    """Marks a check that calls a model: it runs once, before the repair."""
    fn.uses_model = True  # type: ignore[attr-defined]
    return fn


__all__ = [
    "Check", "Doc", "Finding", "MAX_REPAIRS", "find", "model_check", "repair",
    "verify_and_repair",
]
