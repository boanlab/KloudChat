"""Pipeline-internal names (blocks, steps) kept out of reader-facing text."""

from __future__ import annotations

import json
import re

from app.services import units

WRITER_NOTE = (
    "# 작성 메모(내부)\nBlocks named like 「확정 수치」「확정 날짜」「견적 확정 블록」「…(코드)」, "
    "and the pre-calculation and step names, are internal notes (내부 메모) for writing. Put only "
    "their values in the document — not the blocks or their notes as they stand, and never the "
    "names of blocks, calculation steps, code or instructions, nor words like 「코드가 계산했다」 "
    "「코드 미해석」. If a date or value could not be settled, write only 「확인 필요」."
)

#: block, step, prompt-section names
TERMS = re.compile(
    r"코드\s{0,2}(?:미해석|해석되지|해석하지\s{0,2}못)"
    r"|(?:확정|계산)\s{0,2}(?:수치\s{0,2}|날짜\s{0,2}|금액\s{0,2})?블록"
    r"|(?:견적|날짜|수치|점수|총비용)\s{0,2}확정\s{0,2}블록"
    r"|확정\s{0,2}수치"
    r"|확정\s{0,2}날짜\s{0,2}(?:기준|목록|블록)"
    r"|사전\s{0,2}계산"
    r"|(?<![A-Za-z-])(?:date-calc|quote-calc|compare-cost|compare-check|proposal-check|pre-?pass)"
    r"(?![A-Za-z-])"
    r"|쓸 수 있는 수치|수치 목록|자료끼리 다른 값|작성\s{0,2}(?:지침|메모)"
)
#: how-it-was-written terms in documents
DOCUMENT_TERMS = re.compile(
    TERMS.pattern
    + r"|코드(?:가|로)\s{0,2}(?:계산|풀|해석)|\(\s{0,2}코드\s{0,2}계산[^()\n]{0,24}\)|\(코드\)"
    r"|코드\s{0,2}계산"
)
#: clause within a table cell
_CLAUSE = re.compile(r"\s{0,4}(?:…|·{2,3}|[;,，]|—|–|\s/\s|\.\s)\s{0,4}")


def found(text: str, *, document: bool = True) -> list[str]:
    """Internal names in `text`, in order, once each."""
    rx = DOCUMENT_TERMS if document else TERMS
    return list(dict.fromkeys(m.group(0) for m in rx.finditer(text or "")))


def _cell(cell: str, rx: re.Pattern[str]) -> str:
    if not rx.search(cell):
        return cell
    parts = [p for p in _CLAUSE.split(cell.strip()) if p.strip() and not rx.search(p)]
    return " ".join(parts) if parts else "확인 필요"


def _row(line: str, rx: re.Pattern[str]) -> str:
    lead = line[: len(line) - len(line.lstrip())]
    inner = line.strip()
    closed = inner.endswith("|")
    cells = inner.strip("|").split("|")
    return lead + "| " + " | ".join(_cell(c.strip(), rx) for c in cells) + (" |" if closed else "")


def scrub(text: str, *, document: bool = True) -> str:
    """`text` minus internal names (cell clause or sentence); fences, headings kept."""
    rx = DOCUMENT_TERMS if document else TERMS
    if not rx.search(text or ""):
        return text
    out: list[str] = []
    fenced = False
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        if fenced or stripped.startswith("#") or not rx.search(line):
            out.append(line)
            continue
        if stripped.startswith("|"):
            out.append(_row(line, rx))
            continue
        lead = re.match(r"\s{0,24}(?:(?:[-*+]|\d{1,3}[.)]|[□■○◦ㅇㆍ·∙※])\s{1,4})?", line).group(0)
        body = line[len(lead):]
        kept = [s for s in units.SENTENCE_BREAK.split(body) if s.strip() and not rx.search(s)]
        if kept:
            out.append(lead + " ".join(kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


#: tool/script reports copied into the answer (skills g2–g4)
TOOL_REPORT = re.compile(
    r"`(?:block|check|info|pass|lost|added|risky|format_changed)`"
    r"|(?<![A-Za-z_])(?:block|check|info)\s{0,2}\d{1,4}\s{0,2}건"
    r"|(?<![A-Za-z_])(?:calculate|run_code|run_skill_script|use_skill|web_search)\s{0,2}[:(（]"
    r"|스크립트(?:가|로|를|의|는|에서|로는)?\s{0,3}(?:[^\s.]{1,12}\s{1,2}){0,4}"
    r"(?:오류|실행|결과|한도|돌려|돌린|확인했|점검했|냈|검증|남긴|계산)"
    r"|(?:점검|검사|검증|검산)\s{0,2}(?:스크립트\s{0,2})?(?:최종\s{0,2})?결과\s{0,2}[:：][^\n]{0,200}?"
    r"\d{1,4}\s{0,2}(?:건|개)"
    r"|(?:점검|검사|검증)\s{0,2}(?:스크립트\s{0,2})?(?:최종\s{0,2})?결과\s{0,2}[:：][^\n]{0,16}?문제\s{0,2}없"
    r"|`(?:claims|sources|frame|preserve|proof|scan|timeline|notes|size|check|length|matrix|plan)`"
    r"|(?<![A-Za-z_])calculate\s{0,2}도구로"
    r"|(?<![\w/])[a-z_]{2,20}\.py(?![\w])"
    r"|(?<![A-Za-z])(?:preserve|proof|scan|timeline|sources|claims|frame|notes)\s{0,2}"
    r"(?:검사|점검|결과|스크립트)"
)
#: raw JSON lines or 「[실행 결과: …]」 headers
JSON_LINE = re.compile(
    r'^\s{0,8}[\[{]\s{0,2}"[A-Za-z_]{1,30}"\s{0,2}:'
    r'|(?:"[A-Za-z_]{1,30}"\s{0,2}:\s{0,2}[^,\n]{1,80},\s{0,2}){2}'
    r'|^\s{0,16}"[a-z_]{1,30}"\s{0,2}:\s{0,2}(?:["\[{\d]|true|false|null)'
    r'|^\s{0,16}[\[\]{}],?\s{0,2}$'
    r'|^\s{0,4}\[실행 결과\s{0,2}[:：][^\]\n]{0,60}\]\s{0,2}$',
    re.M,
)
#: table cell saying a script fixed a phrase
_SCRIPT_CELL = re.compile(r"스크립트\s{0,2}(?:auto\s{0,2})?(?:자동\s{0,2})?(교체|고침|수정)")


def strip_reports(text: str) -> str:
    """`text` minus tool/script reports; cells say 「자동 교체」; fences and headings kept."""
    if not text or not (TOOL_REPORT.search(text) or JSON_LINE.search(text)
                        or _SCRIPT_CELL.search(text)):
        return text
    out: list[str] = []
    fenced = False
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        if fenced or stripped.startswith("#"):
            out.append(line)
            continue
        if stripped.startswith("|"):
            out.append(_SCRIPT_CELL.sub(lambda m: f"자동 {m.group(1)}", line))
            continue
        if JSON_LINE.search(line):
            continue
        if not TOOL_REPORT.search(line):
            out.append(line)
            continue
        lead = re.match(r"\s{0,24}(?:(?:[-*+]|\d{1,3}[.)]|[□■○◦ㅇㆍ·∙※])\s{1,4})?", line).group(0)
        kept = [s for s in units.SENTENCE_BREAK.split(line[len(lead):])
                if s.strip() and not TOOL_REPORT.search(s)]
        if kept:
            out.append(lead + " ".join(kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


#: writer apologising for its own tool call
TOOL_APOLOGY = re.compile(
    r"(?:도구|툴|tool|함수)(?:를|을)?\s{0,2}잘못\s{0,2}(?:호출|사용|실행|불러)"
    # a code tool's run told by its name: 「직전 `run_code`는 … syntax error로 실패」
    r"|`?(?<![A-Za-z_])(?:run_code|execute_code|run_skill_script)(?![A-Za-z_])`?", re.I)
_SORRY = re.compile(r"^\s{0,4}(?:죄송합니다|죄송해요|미안합니다|사과드립니다)[.!]?\s{0,4}$")


def strip_tool_apologies(text: str) -> str:
    """`text` minus tool-call apologies; fences, headings, tables kept."""
    if not text or not TOOL_APOLOGY.search(text):
        return text
    out: list[str] = []
    fenced = False
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            fenced = not fenced
        if fenced or stripped.startswith(("```", "#", "|")) or not TOOL_APOLOGY.search(line):
            out.append(line)
            continue
        sentences = [s for s in units.SENTENCE_BREAK.split(line) if s.strip()]
        kept: list[str] = []
        for sentence in sentences:
            if TOOL_APOLOGY.search(sentence):
                if kept and _SORRY.match(kept[-1]):
                    kept.pop()
                continue
            kept.append(sentence)
        if kept:
            out.append(" ".join(s.strip() for s in kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).lstrip("\n")


#: tool names a written-out call names
_TOOL_NAME = re.compile(
    r"(?<![A-Za-z_])(?:run_skill_script|run_code|execute_code|use_skill|calculate|web_search"
    r"|fetch_url|mcp__[A-Za-z0-9_-]{1,80})(?![A-Za-z_])")
#: fenced block, any language
_FENCE = re.compile(r"^[ \t]{0,8}```[^\n`]{0,20}\n([\s\S]*?)\n[ \t]{0,8}```[ \t]{0,8}$", re.M)
_DECODER = json.JSONDecoder()


def _call_shaped(data: object) -> bool:
    """A call to one of this app's tools written as JSON (`action_type`, `tool_calls`,
    skill+script, or a named tool with its arguments)."""
    if isinstance(data, list):
        return bool(data) and all(_call_shaped(d) for d in data)
    if not isinstance(data, dict):
        return False
    keys = set(data)
    script = {"skill", "script"} <= keys
    shaped = script or bool(keys & {"action_type", "tool_calls"}) or (
        bool(keys & {"name", "tool", "function", "action"})
        and bool(keys & {"arguments", "args", "parameters", "input"}))
    return shaped and (script or bool(_TOOL_NAME.search(json.dumps(data, ensure_ascii=False))))


def _json_at(text: str, at: int) -> tuple[object, int] | None:
    try:
        return _DECODER.raw_decode(text, at)
    except (json.JSONDecodeError, ValueError):
        return None


def has_tool_call(text: str) -> bool:
    """Whether `text` holds a tool call written out as JSON (bare or fenced)."""
    return strip_tool_calls(text) != (text or "")


def strip_tool_calls(text: str) -> str:
    """`text` minus tool calls written out as JSON: the fenced block or the bare object."""
    if not text or "{" not in text or not (
            _TOOL_NAME.search(text) or re.search(r'"(?:action_type|tool_calls|script)"', text)):
        return text
    out = _FENCE.sub(lambda m: "" if (got := _json_at(m.group(1).strip(), 0)) is not None
                     and not m.group(1).strip()[got[1]:].strip() and _call_shaped(got[0])
                     else m.group(0), text)
    # bare objects starting a line
    pieces: list[str] = []
    at = 0
    for m in re.finditer(r"^[ \t]{0,8}(?=[{\[])", out, re.M):
        start = m.end()
        if start < at:
            continue
        got = _json_at(out, start)
        if got is None or not _call_shaped(got[0]):
            continue
        pieces.append(out[at:m.start()])
        at = got[1]
    if pieces or at:
        pieces.append(out[at:])
        out = "".join(pieces)
    if out == text:
        return text
    return re.sub(r"\n{3,}", "\n\n", out).strip("\n")


__all__ = ["DOCUMENT_TERMS", "JSON_LINE", "TERMS", "TOOL_APOLOGY", "TOOL_REPORT", "WRITER_NOTE",
           "found", "has_tool_call", "scrub", "strip_reports", "strip_tool_apologies",
           "strip_tool_calls"]
