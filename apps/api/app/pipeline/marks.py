"""Highlight spans per section: placeholders, grounding-blanked values, verify findings."""

from __future__ import annotations

from typing import Any

from app.services import placeholders

_MAX_SPANS = 60
_GROUNDING = ("check:grounding", "check:grounding-again", "check:answer-grounding")


def _gap_reasons(bundle: Any) -> dict[str, str]:
    out = {}
    for gap in getattr(bundle.findings, "gaps", None) or []:
        if isinstance(gap, dict) and gap.get("placeholder"):
            name = gap.get("label") or gap.get("fact")
            out[str(gap["placeholder"])] = f"자료에 없는 정보: {name}"
    return out


def _replaced(bundle: Any) -> dict[str, list[tuple[str, str, str]]]:
    """Per section id: (blank, original value, stage) from grounding checks."""
    out: dict[str, list[tuple[str, str, str]]] = {}
    for record in getattr(bundle, "steps", None) or []:
        if record.stage not in _GROUNDING:
            continue
        for change in (record.evidence or {}).get("changes") or []:
            new, old = str(change.get("new") or ""), str(change.get("old") or "")
            for m in placeholders.PLACEHOLDER.finditer(new):
                out.setdefault(str(change.get("sectionId") or ""), []).append(
                    (m.group(0), old, record.stage))
    return out


def for_sections(bundle: Any, sections: list[dict]) -> list[dict[str, Any]]:
    """Spans per stored section; sections without spans omitted."""
    gaps, replaced = _gap_reasons(bundle), _replaced(bundle)
    still = list(getattr(bundle.findings, "still", None) or [])
    out, total = [], 0
    for index, section in enumerate(sections or []):
        content = str(section.get("content") or "")
        key = str(section.get("id") or index)
        spans: list[dict[str, Any]] = []
        pending = list(replaced.get(key, []))
        for m in placeholders.PLACEHOLDER.finditer(content):
            blank = m.group(0)
            hit = next((r for r in pending if r[0] == blank), None)
            if hit is not None:
                pending.remove(hit)
                spans.append({"kind": "ungrounded-replaced", "start": m.start(), "end": m.end(),
                              "text": blank, "original": hit[1][:200],
                              "reason": "자료에서 찾지 못한 값을 빈칸으로 바꿈", "stage": hit[2],
                              "code": "ungrounded_value"})
            else:
                spans.append({"kind": "placeholder", "start": m.start(), "end": m.end(),
                              "text": blank, "original": "",
                              "reason": gaps.get(blank, "채울 빈칸"), "stage": "", "code": ""})
        for finding in still:
            if getattr(finding, "section", None) != index:
                continue
            sentence = str(getattr(finding, "sentence", "") or "").strip()
            at = content.find(sentence) if sentence else -1
            if at < 0:
                continue
            spans.append({"kind": "check-finding", "start": at, "end": at + len(sentence),
                          "text": sentence, "original": "",
                          "reason": str(getattr(finding, "why", "") or "")[:200],
                          "stage": "verify", "code": str(getattr(finding, "code", "") or "")})
        if spans:
            spans.sort(key=lambda s: (s["start"], s["end"]))
            spans = spans[: max(0, _MAX_SPANS - total)]
            total += len(spans)
            out.append({"sectionId": key, "heading": str(section.get("heading") or ""),
                        "spans": spans})
    return out


def relocate(stored: list[dict] | None, sections: list[dict]) -> list[dict[str, Any]]:
    """Stored spans re-found by text; vanished ones dropped, new blanks added."""
    by_id = {str(s.get("id") or i): s for i, s in enumerate(sections or [])}
    kept: dict[str, list[dict]] = {}
    for row in stored or []:
        key = str(row.get("sectionId") or "")
        section = by_id.get(key)
        if section is None:
            continue
        content = str(section.get("content") or "")
        cursor: dict[str, int] = {}
        for span in row.get("spans") or []:
            text = str(span.get("text") or "")
            if not text:
                continue
            at = content.find(text, cursor.get(text, 0))
            if at < 0:
                continue
            cursor[text] = at + len(text)
            kept.setdefault(key, []).append({**span, "start": at, "end": at + len(text)})
    out = []
    for index, section in enumerate(sections or []):
        key = str(section.get("id") or index)
        content = str(section.get("content") or "")
        spans = kept.get(key, [])
        taken = {(s["start"], s["end"]) for s in spans}
        for m in placeholders.PLACEHOLDER.finditer(content):
            if not any(a <= m.start() and m.end() <= b for a, b in taken):
                spans.append({"kind": "placeholder", "start": m.start(), "end": m.end(),
                              "text": m.group(0), "original": "", "reason": "채울 빈칸",
                              "stage": "", "code": ""})
        if spans:
            spans.sort(key=lambda s: (s["start"], s["end"]))
            out.append({"sectionId": key, "heading": str(section.get("heading") or ""),
                        "spans": spans[:_MAX_SPANS]})
    return out


__all__ = ["for_sections", "relocate"]
