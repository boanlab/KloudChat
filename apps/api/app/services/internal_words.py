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
    # 「코드에서 확정된 금액」
    r"|코드(?:에서|가|로|를\s{0,2}통해)\s{0,2}(?:확정|산출|정해|정한|고정)"
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
    fence = ""  # "" outside, "code", or "prose" for a 「```callout」·「```kpi」 box
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            # a callout or kpi box is prose the reader sees
            fence = "" if fence else (
                "prose" if stripped[3:].strip().lower() in ("callout", "kpi") else "code")
            out.append(line)
            continue
        if fence == "code" or stripped.startswith("#") or not rx.search(line):
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
    # a box left with nothing in it goes too
    empty_box = r"(?m)^[ \t]{0,3}```(?:callout|kpi)[^\n]*\n(?:[ \t]*\n)*[ \t]{0,3}```[ \t]*(?:\n|$)"
    text = re.sub(empty_box, "", "\n".join(out))
    return re.sub(r"\n{3,}", "\n\n", text)


#: tool/script reports copied into the answer
TOOL_REPORT = re.compile(
    r"`(?:block|check|info|pass|lost|added|risky|format_changed)`"
    r"|(?<![A-Za-z_])(?:block|check|info)\s{0,2}\d{1,4}\s{0,2}건"
    # a check script's verdict counts: 「block 0 · check 0 · info 1」, 「(block 0)」
    r"|(?<![A-Za-z_])(?:block|check|info)\s{0,2}\d{1,4}\s{0,2}(?:[·,)]|$)"
    r"|\(\s{0,2}block\s{0,2}\d{1,4}\s{0,2}\)"
    r"|(?<![A-Za-z_])block\s{0,2}\)?\s{0,2}판정"
    r"|(?<![A-Za-z_])(?:calculate|run_code|run_skill_script|use_skill|web_search)\s{0,2}[:(（]"
    r"|스크립트(?:가|로|를|의|는|에서|로는)?\s{0,3}(?:[^\s.]{1,12}\s{1,2}){0,4}"
    r"(?:오류|실행|결과|한도|돌려|돌린|확인했|점검했|냈|검증|남긴|계산|지적|항목|판정)"
    # a check skill named in the answer: 「`copy-check` 로 한도 재검사」
    r"|`?(?<![A-Za-z_-])(?:copy-check|grounding-check|gap-check|calc-check|source-check|date-calc"
    r"|internal-comms|generic-language-killer|pii-mask|text-fit)(?![A-Za-z_-])`?"
    r"|(?:점검|검사|검증|검산)\s{0,2}(?:스크립트\s{0,2})?(?:최종\s{0,2})?결과\s{0,2}[:：][^\n]{0,200}?"
    r"\d{1,4}\s{0,2}(?:건|개)"
    r"|(?:점검|검사|검증)\s{0,2}(?:스크립트\s{0,2})?(?:최종\s{0,2})?결과\s{0,2}[:：][^\n]{0,16}?문제\s{0,2}없"
    r"|`(?:claims|sources|frame|preserve|proof|scan|check|length)`"
    r"|(?<![A-Za-z_])calculate\s{0,2}도구로"
    r"|(?<![\w/])[a-z_]{2,20}\.py(?![\w])"
    r"|(?<![A-Za-z])(?:preserve|proof|scan|sources|claims|frame)\s{0,2}"
    r"(?:검사|점검|결과|스크립트)"
    # a check's findings named by its verdict word (「두 check는 …」, 「차단(block) 표현은 0건」)
    r"|(?<![A-Za-z_])(?:두|세|네|이|그|이번)\s{1,2}(?:check|block)(?:는|은|가|를|도|의)?(?![A-Za-z])"
    r"|\(\s{0,2}(?:block|check)\s{0,2}\)\s{0,2}표현"
    # how the values were made (「코드로 계산한 출력값」 「코드 출력 그대로」)
    r"|코드로\s?계산한\s?출력값|코드\s?출력\s?그대로|계산\s?도구로\s?(?:구한|계산한)"
)
#: a table row reporting a check's verdict count (「| CTA 2건 | check 2건 (…) |」)
_REPORT_CELL = re.compile(r"\|\s{0,2}(?:block|check|info)\s{0,2}\d{1,4}\s{0,2}건")
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
            if not _REPORT_CELL.search(line):
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
    # any particle after the tool (「날씨 도구는 잘못 호출됐네요.」)
    r"(?:도구|툴|tool|함수)(?:를|을|는|은|가|이|도)?\s{0,2}잘못\s{0,2}(?:호출|사용|실행|불러)"
    # a code tool's run told by its name: 「직전 `run_code`는 … syntax error로 실패」
    r"|`?(?<![A-Za-z_])(?:run_code|execute_code|run_skill_script)(?![A-Za-z_])`?", re.I)
_SORRY = re.compile(r"^\s{0,4}(?:죄송합니다|죄송해요|미안합니다|사과드립니다)[.!]?\s{0,4}$")
#: the writer going on after a tool apology (「…필요 없으니, 바로 계획을 짜 드리겠습니다.」)
_CARRY_ON = re.compile(
    r"(?:필요\s?없으니|필요하지\s?않으니|대신|바로)[^\n.]{0,60}?(?:드리겠습니다|하겠습니다|합니다)"
    r"[.!]?\s*$")


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
        apologised = False
        for sentence in sentences:
            if TOOL_APOLOGY.search(sentence):
                if kept and _SORRY.match(kept[-1]):
                    kept.pop()
                apologised = True
                continue
            if apologised and not kept and _CARRY_ON.search(sentence):
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
    """Whether `text` holds a tool call written out as JSON (bare or fenced) or in a model's
    call template (「<tool_call>…」)."""
    return strip_tool_calls(text) != (text or "")


#: a tool call in a model's own call template, written into the text instead of sent as a
#: call (GLM's 「<tool_call>web_search<arg_key>query</arg_key><arg_value>…」): Qwen·GLM
#: 「<tool_call>…</tool_call>」, 「<|tool_call|>…」, 「<function_call>…」, DeepSeek's
#: 「<｜tool▁calls▁begin｜>…<｜tool▁calls▁end｜>」 and 「<function=name>…</function>」; an
#: unclosed one runs to the end of the text
_TAGGED_CALL = re.compile(
    r"<(?P<tag>tool_calls?|function_calls?)>(?P<body>[\s\S]*?)(?:</(?P=tag)>|\Z)"
    r"|<\|(?P<ptag>tool_calls?|function_calls?)(?:_begin)?\|>(?P<pbody>[\s\S]*?)"
    r"(?:<\|/?(?P=ptag)(?:_end)?\|>|\Z)"
    r"|<｜tool▁calls?▁begin｜>(?P<ds>[\s\S]*?)(?:<｜tool▁calls?▁end｜>|\Z)"
    r"|<function=(?P<fn>[A-Za-z0-9_.:-]{1,80})>(?P<fbody>[\s\S]*?)(?:</function>|\Z)",
    re.I)
#: a template's tag left on its own: 「</tool_call>」, 「<|tool_call_end|>」, 「<｜tool▁sep｜>」
_STRAY_TAG = re.compile(
    r"</?(?:tool_calls?|function_calls?|arg_key|arg_value)>|<\|/?(?:tool_calls?|function_calls?)"
    r"(?:_begin|_end)?\|>|<｜tool▁[a-z▁]{1,20}｜>", re.I)
_ARG_PAIR = re.compile(r"<arg_key>\s*([\s\S]*?)\s*</arg_key>\s*<arg_value>([\s\S]*?)</arg_value>")
_PARAMETER = re.compile(r"<parameter=([A-Za-z0-9_.:-]{1,80})>([\s\S]*?)</parameter>")
_CALL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,79}")


#: code the answer shows on purpose (an explanation of a call format): never taken as a call
_CODE_SPAN = re.compile(r"```[\s\S]*?(?:```|\Z)|`[^`\n]{1,400}`")
#: DeepSeek's template tokens are never prose, even with its own ```json inside
_DEEPSEEK = re.compile(r"<｜tool▁calls?▁begin｜>")


def _template_sub(text: str, repl) -> str:
    """`text` with every call template replaced by `repl(match)`, code spans left alone."""
    def sub(piece: str) -> str:
        return _STRAY_TAG.sub("", _TAGGED_CALL.sub(repl, piece))

    if _DEEPSEEK.search(text):
        # its markup wraps a ```json fence: taken before code spans are set apart
        text = _TAGGED_CALL.sub(lambda m: repl(m) if m.group("ds") is not None else m.group(0),
                                text)
    out, at = [], 0
    for code in _CODE_SPAN.finditer(text):
        out += [sub(text[at:code.start()]), code.group(0)]
        at = code.end()
    out.append(sub(text[at:]))
    return "".join(out)


def _loose(value: str) -> object:
    """A template's argument value: JSON when it reads as JSON, else the text."""
    value = value.strip()
    try:
        return json.loads(value)
    except ValueError:
        return value


def _json_calls(body: str) -> list[tuple[str, dict]]:
    """`(name, arguments)` of JSON calls in `body`: {"name", "arguments"}, OpenAI's
    {"function": {…}}, or a list of them."""
    body = re.sub(r"```[A-Za-z]*", "", body).strip()
    out: list[tuple[str, dict]] = []
    at = 0
    while at < len(body):
        start = min((i for i in (body.find("{", at), body.find("[", at)) if i >= 0), default=-1)
        if start < 0:
            break
        got = _json_at(body, start)
        if got is None:
            at = start + 1
            continue
        data, at = got
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                continue
            fn = item.get("function") if isinstance(item.get("function"), dict) else {}
            name = item.get("name") or item.get("tool") or fn.get("name") or (
                item.get("function") if isinstance(item.get("function"), str) else "")
            args = next((v for v in (item.get("arguments"), item.get("parameters"),
                                     item.get("args"), item.get("input"), fn.get("arguments"))
                         if v is not None), {})
            if isinstance(args, str):
                args = _loose(args)
            if isinstance(name, str) and _CALL_NAME.fullmatch(name.strip()):
                out.append((name.strip(), args if isinstance(args, dict) else {}))
    return out


def _template_calls(m: re.Match) -> list[tuple[str, dict]]:
    """`(name, arguments)` of the calls one template match holds."""
    if m.group("fn"):
        return [(m.group("fn"), {k: _loose(v) for k, v in _PARAMETER.findall(m.group("fbody"))}
                 or (_json_calls(m.group("fbody"))[:1] or [("", {})])[0][1])]
    if m.group("ds") is not None:
        out = []
        for part in re.split(r"<｜tool▁call▁begin｜>", m.group("ds")):
            if "<｜tool▁sep｜>" not in part:
                continue
            left, right = part.split("<｜tool▁sep｜>", 1)
            right = re.sub(r"<｜tool▁call▁end｜>[\s\S]*", "", right)
            if left.strip() == "function":
                # 「function<｜tool▁sep｜>name\n```json\n{…}\n```」
                name, _, right = right.strip().partition("\n")
            else:
                name = left
            brace = right.find("{")
            got = _json_at(right, brace) if brace >= 0 else None
            if _CALL_NAME.fullmatch(name.strip()):
                out.append((name.strip(), got[0] if got and isinstance(got[0], dict) else {}))
        return out
    body = m.group("body") if m.group("body") is not None else m.group("pbody") or ""
    if "<arg_key>" in body or (_CALL_NAME.fullmatch(body.strip()) is not None):
        # GLM: 「name<arg_key>k</arg_key><arg_value>v</arg_value>…」
        name = body.split("<arg_key>", 1)[0].strip()
        if _CALL_NAME.fullmatch(name):
            return [(name, {k: _loose(v) for k, v in _ARG_PAIR.findall(body)})]
        return []
    if "<function=" in body:
        return [c for f in _TAGGED_CALL.finditer(body) for c in _template_calls(f)]
    return _json_calls(body)


def _named_call(data: object, known: set[str]) -> bool:
    """A JSON call (or list of them) naming only tools in `known`."""
    calls = _json_calls(json.dumps(data, ensure_ascii=False))
    shaped = _call_shaped(data) or (isinstance(data, (dict, list)) and bool(calls))
    return bool(known) and shaped and bool(calls) and all(name in known for name, _ in calls)


#: what may sit between two calls written in a row: 「{…}, {…}」
_BETWEEN_CALLS = re.compile(r"[ \t]{0,4}[,;]?[ \t]{0,4}\n?[ \t]{0,8}(?=[{\[])")


def _run_of_calls(text: str, at: int, is_call) -> tuple[int, list[str]]:
    """End of the calls written right after `at` (「, {…}」 again and again) and their texts."""
    bodies: list[str] = []
    while (m := _BETWEEN_CALLS.match(text, at)) is not None:
        got = _json_at(text, m.end())
        if got is None or not is_call(got[0]):
            break
        bodies.append(text[m.end():got[1]])
        at = got[1]
    return at, bodies


def written_tool_calls(text: str, known: set[str] | frozenset[str] = frozenset()
                       ) -> tuple[str, list[tuple[str, dict]]]:
    """`(text without written-out tool calls, the calls naming a tool in `known`)`: a model's
    call template (any tool name; never prose) and JSON calls naming a known tool or one of
    this app's. A call naming a tool not in `known` is dropped, never shown."""
    if not text:
        return text or "", []
    calls: list[tuple[str, dict]] = []
    def take(m: re.Match) -> str:
        calls.extend(_template_calls(m))
        return ""

    out = _template_sub(text, take)
    if known and "{" in out:
        def fenced(m: re.Match) -> str:
            got = _json_at(m.group(1).strip(), 0)
            if got is None or m.group(1).strip()[got[1]:].strip() or not _named_call(
                    got[0], set(known)):
                return m.group(0)
            calls.extend(_json_calls(m.group(1)))
            return ""
        out = _FENCE.sub(fenced, out)
        pieces: list[str] = []
        at = 0
        for m in re.finditer(r"^[ \t]{0,8}(?=[{\[])", out, re.M):
            if m.end() < at:
                continue
            got = _json_at(out, m.end())
            if got is None or not _named_call(got[0], set(known)):
                continue
            calls.extend(_json_calls(out[m.end():got[1]]))
            pieces.append(out[at:m.start()])
            # a second call after 「, 」 on the same run goes too, not shown
            at, more = _run_of_calls(out, got[1], lambda d: _named_call(d, set(known)))
            for body in more:
                calls.extend(_json_calls(body))
        if pieces:
            pieces.append(out[at:])
            out = "".join(pieces)
    out = _strip_json_calls(out)
    if out == text:
        return text, []
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, [(name, args) for name, args in calls if name in known]


def strip_tool_calls(text: str) -> str:
    """`text` minus tool calls written out: a model's call template (「<tool_call>…」), and a
    JSON call to this app's tools, fenced or bare."""
    if text and (_TAGGED_CALL.search(text) or _STRAY_TAG.search(text)):
        out = _template_sub(text, lambda m: "")
        if out != text:
            out = _strip_json_calls(out)
            return re.sub(r"\n{3,}", "\n\n", out).strip()
    return _strip_json_calls(text)


def _strip_json_calls(text: str) -> str:
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
        at, _ = _run_of_calls(out, got[1], _call_shaped)
    if pieces or at:
        pieces.append(out[at:])
        out = "".join(pieces)
    if out == text:
        return text
    return re.sub(r"\n{3,}", "\n\n", out).strip("\n")


#: the material's own test label: 「한빛시청(가상)」, 「(한빛소프트, 가상 — …)」
_TEST_LABEL = re.compile(r"\(\s{0,2}가상\s{0,2}\)|[(,]\s{0,2}가상\s{0,2}(?:—|-|,|\))")
#: the person asking for the label in the copy: 「가상임을 표시」, 「(가상) 표기」
_LABEL_WANTED = re.compile(
    r"가상[^\n.]{0,10}?(?:임을|이라고|이라는|표시|표기|명시|라고\s{0,2}밝)")
_LABEL_IN_COPY = re.compile(r"[ \t]?\(\s{0,2}가상\s{0,2}\)")


def strip_test_label(text: str, request: str) -> str:
    """`text` without the 「(가상)」 the material tags its made-up names with for a test; kept
    when the person asks for the label; fences kept."""
    if not text or "가상" not in text or not _TEST_LABEL.search(request or "") \
            or _LABEL_WANTED.search(request or ""):
        return text
    out, fenced = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        out.append(line if fenced else _LABEL_IN_COPY.sub("", line))
    return "\n".join(out)


#: a prompt section heading echoed back at the top of a rewrite: 「# Answer」
_ECHOED_HEADING = re.compile(
    r"\A\s{0,3}#{1,6}\s{0,3}(?:Answer|Corrected\s+answer|Answer\s+\(corrected\)|Final\s+answer|"
    r"Rewritten\s+answer)\s{0,3}:?\s*(?:\n|\Z)", re.I)


#: the same heading on a line of its own further down, the prompt's problems echoed above it
_ECHOED_LINE = re.compile(
    r"^\s{0,3}#{1,6}\s{0,3}(?:Answer|Corrected\s+answer|Answer\s+\(corrected\)|Final\s+answer|"
    r"Rewritten\s+answer)\s{0,3}:?\s*$", re.I | re.M)


#: Reasoning written into the answer: 「<think>…</think>」, a closing tag whose opening the
#: template ate, or a bracketed 「[Thinking: …]」 / 「(Reasoning: …)」 block starting a line.
_THINK_TAGGED = re.compile(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>\s*", re.I)
_THINK_CLOSE = re.compile(r"\A[\s\S]*?</think(?:ing)?>\s*", re.I)
_THINK_BRACKET = re.compile(
    r"^[ \t]*([\[(])\s?(?:thinking|thoughts?|reasoning|internal\s+(?:notes?|reasoning)|"
    r"생각|추론)\s?:", re.I | re.M)
_CLOSER = {"[": "]", "(": ")"}
#: Shorter brackets are content (「(생각: 30초)」), not a reasoning block.
_THINK_MIN = 80


def strip_reasoning(text: str) -> str:
    """`text` minus reasoning blocks the model wrote into the answer; never down to nothing."""
    if not text or not re.search(r"think|reason|thought|생각|추론", text, re.I):
        return text
    out = _THINK_TAGGED.sub("", text)
    if "</think" in out.lower():
        out = _THINK_CLOSE.sub("", out, count=1)
    pieces: list[str] = []
    at = 0
    for m in _THINK_BRACKET.finditer(out):
        if m.start() < at:
            continue
        opener, closer = m.group(1), _CLOSER[m.group(1)]
        depth, i = 0, m.start(1)
        while i < len(out):
            if out[i] == opener:
                depth += 1
            elif out[i] == closer:
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if i >= len(out) or i - m.start() < _THINK_MIN:
            continue
        pieces.append(out[at:m.start()])
        at = i + 1
    if pieces:
        pieces.append(out[at:])
        out = "".join(pieces)
    if out == text:
        return text
    out = re.sub(r"\n{3,}", "\n\n", out).strip("\n")
    return out if out.strip() else text


def strip_echoed_heading(text: str) -> str:
    """`text` without a prompt heading the model copied (「# Answer」), and without the
    prompt's problem list it echoed above one."""
    if not text:
        return text
    text = _ECHOED_HEADING.sub("", text, count=1).lstrip("\n")
    if m := _ECHOED_LINE.search(text):
        text = text[m.end():].lstrip("\n")
    return text


#: a check rewrite's reply to the check (「점검이 끝났습니다. … 스크립트가 15를 수치로 짚었습니다」,
#: 「#17의 「자동화」는 … 근거가 있습니다. 다만 스크립트가 확인 표시를 달았으니 …」)
_CHECK_REPLY = re.compile(
    r"스크립트|점검(?:이|을|은|에서|\s?결과|\s?지적)|확인\s?표시|지적(?:하신|된|받은|에)|짚었|짚은"
    r"|근거가\s?(?:있|없)습니다|자료에\s?(?:근거|없어도|있는\s?표현)|(?:고쳐|다시)\s?썼|수정했습니다"
    r"|그대로\s?두(?:고|었)|허용되는|무방")


def strip_rewrite_preface(new: str, before: str) -> str:
    """A check rewrite without the paragraphs it put above the answer to answer the check:
    paragraphs before the first one the answer had, that the answer
    did not have and that talk about the check. Never down to nothing."""
    if not new or not before:
        return new
    paragraphs = re.split(r"\n[ \t]*\n", new.strip("\n"))
    old = re.sub(r"\s+", " ", before)
    cut = 0
    for paragraph in paragraphs[:3]:
        flat = re.sub(r"\s+", " ", paragraph).strip()
        if not flat or re.match(r"(?:#{1,6}\s|\||```|!\[)", paragraph.lstrip()) \
                or flat in old or not _CHECK_REPLY.search(flat):
            break
        cut += 1
    rest = "\n\n".join(paragraphs[cut:]).strip("\n")
    return rest if cut and rest.strip() else new


#: a verification block the writer appended: 「**검증 결과**」 then its items
_CHECK_HEAD = re.compile(
    r"^\s{0,3}(?:#{1,6}\s{0,3}|\*\*)\s{0,2}(?:검증|점검|대조)\s{0,2}결과\s{0,2}(?:\*\*)?\s{0,2}[:：]?\s*$")
#: an item of that block: what was checked, never content for the reader
_CHECK_ITEM = re.compile(
    r"^\s{0,8}(?:[-*•]|\d{1,2}[.)])\s.*(?:빠짐|뺌|빈칸|반영|누락|스크립트|검사|대조|근거|통과|일치)")


def strip_check_block(text: str) -> tuple[str, str]:
    """`text` without a closing 「검증 결과」 block of check notes, and the block (for a step)."""
    lines = (text or "").rstrip().split("\n")
    for k in range(len(lines) - 1, -1, -1):
        if not _CHECK_HEAD.match(lines[k]):
            continue
        rest = lines[k + 1:]
        # the block runs to the end, or to a blank line (a caveat line may follow)
        end = next((i for i, line in enumerate(rest) if not line.strip()), len(rest))
        items = [line for line in rest[:end] if line.strip()]
        tail = [line for line in rest[end:] if line.strip()]
        if not items or not all(_CHECK_ITEM.match(line) for line in items) or (
                tail and not all(line.strip().startswith("_") for line in tail)):
            # a heading with nothing of the check under it: the heading goes, with the rule
            # set above it to part it from the answer
            first = next((i for i, line in enumerate(rest) if line.strip()), None)
            under = rest[first:] if first is not None else []
            stop = next((i for i, line in enumerate(under) if not line.strip()), len(under))
            top = k
            while top and not lines[top - 1].strip():
                top -= 1
            # only past a rule that parts it from the answer (a report's own 「점검 결과」 stays)
            ruled = bool(top) and bool(
                re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*", lines[top - 1]))
            if ruled and not any(_CHECK_ITEM.match(line) for line in under[:stop]):
                kept = [*lines[:top - 1], *rest]
                return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n"), lines[k].strip()
            return text, ""
        block = "\n".join([lines[k], *items]).strip()
        kept = [*lines[:k], *rest[end:]]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n"), block
    return text, ""


_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")
#: the writer's English note to itself opens like this
_EN_NOTE_OPEN = re.compile(
    r"^\s{0,4}(?:[-*>]\s{1,3})?(?:\*\*)?(?:The user(?:'s)?\b|I(?:'ll|'m| will| need| should| must"
    r"| have| can| am going)\b|Let me\b|Let's\b|Now(?:,|\s(?:I|let|the|format|write))\b"
    r"|OK(?:ay)?[,.]|Alright\b|First(?:,|\sI\b)|So(?:,|\sI\b)|Wait\b|Hmm\b|Actually\b"
    r"|Write it\b|Key discipline\b)")
#: …and is about the work, not the reader's subject
_EN_NOTE_TOPIC = re.compile(
    r"\b(?:the user|this section|the section|the material|sources|search results?|tools?"
    r"|verify|the draft|instructions|cite)\b", re.I)
#: …unmistakably about the writing task, even inside English text
_EN_NOTE_TASK = re.compile(
    r"\b(?:the user(?:'s)?|this section|the section|against the material|the draft"
    r"|the instructions)\b", re.I)
#: Korean narration of the writing itself (「개요 섹션은 … 다루지 않습니다」)
_KO_NOTE = re.compile(
    r"섹션의\s?(?:초안|범위|영역)|섹션은\s[^\n]{0,80}?영역입니다"
    r"|(?:제공된|주어진)\s?자료를\s?(?:먼저\s?)?(?:분석|검토|확인)하여[^\n]{0,160}?하겠습니다"
    r"|요청된\s?형식\s?\("
    # an instruction to the writer copied into the text:
    # 「수정: 근거 없는 수치를 「[수치]」 등으로 비워 두고, 요청의 값을 채워 넣어 주세요.」)
    r"|수정\s?[:：][^\n]{0,200}?(?:비워\s?두|채워\s?넣|바꿔\s?주|고쳐\s?주|빼\s?주)"
    # a check's mark explained (「확인 전까지는 「확인 필요」 상태로 남겨둡니다」)
    r"|[「『\"']확인\s?필요[」』\"']\s?(?:상태|표시)로\s?(?:남겨|두|둡)"
    # the paper search's own note (「… KCI·DBpia·RISS 등을 찾았으나 국내 논문을 찾지 못했습니다」)
    r"|KCI\s?·\s?DBpia\s?·\s?RISS\s?등을\s?(?:찾았|검색했)")
#: …and, in a document or a slide's notes (which speak to the reader, not the requester),
#: the writer's account of its own work
_DOC_NOTE = re.compile(
    r"채워\s?넣어\s?주(?:세요|십시오)"
    # what it left out (「96퍼센트와 33퍼센트는 자료에 근거가 없어 … 쓰지 않습니다」)
    r"|자료에\s?근거가\s?없어[^\n.]{0,60}?(?:쓰지\s?않|적지\s?않|넣지\s?않|뺐|뺍니다|생략)"
    # why a part is not there (「자료에 사전 배포 목록이 없어 별도의 표를 만들지
    # 않습니다」, the template's rule for an empty section told to the reader)
    r"|자료에\s?[^\n.]{0,40}?(?:없어|없으므로|없기\s?때문에)\s?[^\n.]{0,40}?"
    r"(?:표|절|열|칸|항목|목록|섹션)[을를은는]?\s?(?:따로\s?|별도로\s?)?"
    r"(?:만들지|두지|넣지|싣지|쓰지|적지|작성하지)\s?(?:않습니다|않았습니다|않음)"
    # how the values were made (「여러 수치와 수식은 계산 도구로 구한 결과입니다」)
    r"|계산\s?도구로\s?(?:구한|계산한|얻은)|코드로\s?계산한\s?출력값|코드\s?출력\s?그대로")


def strip_work_sentences(text: str) -> str:
    """Sentences of `text` that are the writer's notes or instructions dropped (a slide's
    speaker notes); lines otherwise kept as written."""
    if not text or not (_KO_NOTE.search(text) or _DOC_NOTE.search(text)):
        return text
    out = []
    for line in text.split("\n"):
        sentences = [s for s in units.SENTENCE_BREAK.split(line) if s.strip()]
        left = [s for s in sentences if not (_KO_NOTE.search(s) or _DOC_NOTE.search(s))]
        if len(left) == len(sentences):
            out.append(line)
        elif left:
            out.append(" ".join(s.strip() for s in left))
    return "\n".join(out).strip("\n")


#: a check's verdict belongs in the steps (「근거 대조 결과 근거 없는 값 0개.」)
_CHECK_RESULT = re.compile(
    r"^\W{0,4}(?:근거\s?대조|출처\s?점검|카피\s?점검|계산[·ㆍ]?\s?단위\s?검증|자체\s?(?:점검|검증|검토))"
    r"\s?결과|근거\s?없는\s?값\s?\d{1,3}\s?개"
    # a pass told under a notice (「일정 점검: … 차단 항목 0건」, 「점검 결과 … 없습니다」)
    r"|^\W{0,4}(?:일정|날짜|요일|문구|최종)?\s?(?:점검|검증|검산|검수)\s?(?:결과|에서)?\s?[:：]?\s"
    r"[^\n]{0,80}?(?:0\s?건|없습니다|없고|맞습니다|맞고|일치합니다|일치하고|통과)"
    r"|(?:요일|날짜)[^\n.]{0,60}?(?:실제\s?)?달력(?:과|이|와)\s?(?:모두\s?)?일치합니다"
    r"|차단\s?(?:\(block\)\s?)?(?:항목|값|표현|사항)\S{0,2}\s?\d{1,3}\s?건"
    # a script's coverage told as a sentence (「항목 9개 중 노트 7 · 뺌 2 · 빠짐 0,
    # 점검 차단 사항 0건…입니다」, 「두 문서 모두 항목 7개 대비 빠짐 0, 차단 0으로 검증을
    # 마쳤습니다」)
    r"|\d{1,3}\s?개\s?(?:중|대비)[^\n.]{0,60}?(?:빠짐|뺌|누락|차단)\s?\d"
    r"|(?:빠짐|뺌|누락|차단)\s?\d{1,3}\s?(?:건|개)?\s?(?:으로|이고|이며)?\s?(?:검증|점검|검수)"
    r"(?:을|를)?\s?마쳤"
    # a script's count told bare (「block 0입니다.」)
    r"|^\W{0,4}(?:block|check)\s?\d{1,3}\s?(?:건|개)?\s?(?:입니다|이고|이며|,)"
    # a script's verdict by its own words (「점검 결과: … check 0 block …」)
    r"|(?<![A-Za-z])(?:check|claims|scan)\s?\d{1,3}\s?block"
    # a check told as done (「근거 대조 점검을 마쳤고, 메모에 없는 수치·날짜는
    # 없습니다.」)
    r"|(?:근거\s?대조|출처\s?점검|카피\s?점검|자체\s?점검|검산|대조)\s?(?:점검)?\s?(?:을|를)?\s?마쳤"
    # a length the writer measured on its own copy (「…는 18자로 한도 안입니다.」)
    r"|\d{1,4}\s?자(?:로|라서?)?\s?(?:한도|제한)\s?(?:안|내|이내)(?:입니다|이고|예요)"
    # a weekday the writer checked (「2026-11-04는 수요일로 확인됐습니다.」)
    r"|(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\s?월\s?\d{1,2}\s?일)(?:\s?\([월화수목금토일]\))?(?:은|는)"
    r"\s?[월화수목금토일]요일(?:로|임이|인\s?것으로)\s?확인(?:됐|되었|했)"
    r"|사용자가\s?(?:준|주신|적은|말한)\s?(?:요일|날짜)[^\n.]{0,20}?(?:과|와)\s?(?:모두\s?)?일치합니다")
#: the writer announcing its next piece of work before the answer starts (「이제 두
#: 문서를 작성합니다.」, 「두 버전으로 작성합니다.」, 「이제 소재를 정리해
#: 드리겠습니다.」, 「최종 확정된 25개입니다.」): dropped only ahead of the first
#: content line
_OPENING_NARRATION = re.compile(
    r"^(?:이제|먼저|그럼|그러면|바로|자,)?\s?[^\n.?!]{0,30}?(?:작성합니다|작성하겠습니다|작성해\s?드리겠습니다"
    r"|정리해\s?드리겠습니다|정리하겠습니다|짜\s?드리겠습니다|만들어\s?드리겠습니다|시작하겠습니다)[.!]?\s*$"
    r"|^최종\s?(?:확정된|확정한|정리한)\s?[^\n.]{0,20}?입니다[.!]?\s*$")
#: the writer telling what it did to its own copy (「권장 150자를 12자 초과해서 줄였습니다」):
#: an edit verb in the past with a length or check word beside it
_SELF_EDIT = re.compile(
    r"(?=[^\n]{0,120}?(?:\d{1,4}\s?자|글자\s?수|초과|한도|검수|block|차단))"
    r"[^\n]{0,160}?(?:줄였|고쳤|수정했|바꿨|다듬었|뺐|맞췄|덜어냈|잘랐)(?:습니다|어요|다)[.!]?\s*$")
#: a request that asks for edits: then telling them is the answer
_ASKED_EDIT = re.compile(
    r"(?:고쳐|다듬어|줄여|수정해|바꿔|윤문|교정|첨삭)\s?(?:줘|주세요|주십시오|달라)")
#: generic verdict, dropped only as the opening line
_OPENING_VERDICT = re.compile(r"^\W{0,4}(?:검증|점검|검산|대조)\s?결과[,:：]?\s")
#: request that asked for a check, whose verdict is then the content
_ASKED_CHECK = re.compile(
    r"(?:검증|점검|검산|대조|검토|확인)\s?(?:해|하여|해서|하고)\s?(?:줘|주세요|주십시오|주실|봐|달라)"
    r"|근거\s?대조|출처\s?점검|팩트\s?체크|fact[- ]?check|verify", re.I)
#: lone marker line (「check」)
_LONE_MARK = re.compile(r"^\s{0,4}(?:check|Check|CHECK|체크|TODO|todo|TBD)\s{0,2}[.:]?\s*$")
#: question stub left in the text
_QUESTION_STUB = re.compile(
    r"^\s{0,4}(?:[-*]\s{1,3})?\[(?:질문|문의|Question)\][^\n]{0,400}?"
    r"(?:알려\s?주|주십시오|주세요|미기재|누락|확인이?\s?필요|please (?:provide|confirm))", re.I)
#: label line of a writer's worksheet (「**작성 계획 (2~4단락):**」)
_NOTE_LABEL = re.compile(
    r"^\s{0,4}(?:[-*]\s{1,3})?(?:\*\*|__)?\s?(?P<label>자료\s?분석(?:\s?및\s?추출)?(?:\s?내용)?"
    r"|추출\s?내용|수치\s?제한\s?확인|사용\s?가능한\s?수치|작성\s?계획|초안(?:\s?작성)?(?:\s?및\s?수정)?"
    r"|자체\s?검토|검토|형식\s?확인|문체\s?확인|최종\s?(?:텍스트|본문|원고|답변)(?:\s?생성)?|최종본"
    r"|Analysis|Plan|Draft|Review|Final(?:\s(?:text|answer|draft))?)"
    r"\s?(?:\([^)\n]{0,40}\))?\s?[:：]\s?(?:\*\*|__)?\s*$")


def _latin_share(text: str) -> float:
    hangul, latin = len(_HANGUL.findall(text)), len(_LATIN.findall(text))
    return latin / (hangul + latin) if hangul + latin else 0.0


def _english_note(paragraph: str, *, opening: bool = False) -> bool:
    """`opening`: the answer's first paragraph, ahead of Korean text — there a first-person
    plan of the work is a note even without naming the task (「I need to design
    a vegetarian meal for 2 servings … so I'm thinking through …, then calculating …」)."""
    if not (len(_LATIN.findall(paragraph)) >= 20 and _latin_share(paragraph) >= 0.7
            and _EN_NOTE_OPEN.match(paragraph)):
        return False
    return bool(_EN_NOTE_TOPIC.search(paragraph)
                or (opening and _EN_NOTE_PLAN.search(paragraph)))


#: the writer planning its own work in English: 「I'm thinking through …」, 「then calculating
#: the breakdown」, 「to make sure it hits those targets」
_EN_NOTE_PLAN = re.compile(
    r"\b(?:I'm\s(?:thinking|going\sto|planning|considering)|thinking\sthrough"
    r"|I(?:'ll|\swill)\s(?:start|first|now|then|calculate|compute|check|draft|write|structure)"
    r"|make\ssure\sit|then\s(?:calculat|comput|check|verify|draft|writ)|let\sme)", re.I)
#: an English answer's opening line narrating a tool step (「Let me re-run with a
#: fixed reference date so the 90-day window is deterministic and the edge cases actually
#: show.」): one sentence, ahead of the answer
_EN_TOOL_STEP = re.compile(
    r"^\s{0,4}(?:Let me|I'll|I will|Now I'll|Now let me|Next, I'll)\s(?:\w+\s){0,3}?"
    r"(?:re-?run|run|search|look\sup|fetch|check|calculate|compute|execute|test|try|retry"
    r"|verify|query)\b[^\n]{0,240}?[.!:]\s*$", re.I)


def _structural(paragraph: str) -> bool:
    return paragraph.lstrip().startswith(("#", "|", "```", "!["))


def _worksheet(text: str) -> str:
    """A worksheet (two or more labels like 「작성 계획:」「초안:」「최종 텍스트 생성:」) cut to
    its final text, or to its draft prose without the notes."""
    lines = text.split("\n")
    labels = [(i, re.sub(r"\s", "", m.group("label"))) for i, line in enumerate(lines)
              if (m := _NOTE_LABEL.match(line))]
    if len({label for _, label in labels}) < 2:
        return text
    first = labels[0][0]
    finals = [i for i, label in labels if label.startswith(("최종", "Final"))]
    if finals and "\n".join(lines[finals[-1] + 1:]).strip():
        return "\n".join(lines[:first] + lines[finals[-1] + 1:])
    out: list[str] = lines[:first]
    keeping = under_label = False
    for line in lines[first:]:
        m = _NOTE_LABEL.match(line)
        if m:
            keeping, under_label = m.group("label").startswith(("초안", "Draft")), True
            continue
        if line.lstrip().startswith("#"):
            keeping = True
        if under_label and line.strip():
            under_label = False
            if re.fullmatch(r"\s*\([^)\n]{0,120}\)\s*", line):
                continue  # 「(문체: …)」 note under the label
        if keeping:
            out.append(line)
    return "\n".join(out)


def strip_work_notes(text: str, *, request: str = "", document: bool = False) -> str:
    """`text` minus the writer's work notes: plan narration (English or Korean), a worksheet's
    labels and notes, check verdicts, lone 「check」 lines and 「[질문]」 stubs. Headings,
    tables and fences stay; an English deliverable is left alone. `document`: a report section
    or slide, whose reader is not the requester (the writer's account of its work goes too)."""
    if not text or not text.strip():
        return text
    korean = _latin_share(request) < 0.5 if request.strip() else _latin_share(text) < 0.5
    asked_check = bool(_ASKED_CHECK.search(request or ""))
    asked_edit = bool(_ASKED_EDIT.search(request or ""))
    out = _worksheet(text) if korean else text
    paragraphs = re.split(r"\n[ \t]*\n", out)
    if korean:
        # an English preamble of notes before the answer: cut through its last English paragraph
        first = next((i for i, p in enumerate(paragraphs) if p.strip()), None)
        planned = first is not None and not _english_note(paragraphs[first]) \
            and _english_note(paragraphs[first], opening=True)
        if planned and (_ASKED_ENGLISH.search(request or "") or first + 1 >= len(paragraphs)
                        or _latin_share(paragraphs[first + 1]) >= 0.5):
            # a plan-shaped opening is a note only as the one English paragraph before Korean
            # text, never an English deliverable the person asked for
            planned = False
        if first is not None and (planned or _english_note(paragraphs[first])):
            heading = next((i for i, p in enumerate(paragraphs) if _structural(p)),
                           len(paragraphs))
            last = max((i for i in range(first, heading) if _latin_share(paragraphs[i]) >= 0.6),
                       default=first)
            rest = "\n\n".join(paragraphs[last + 1:])
            if len(_HANGUL.findall(rest)) >= 40 and _latin_share(rest) < 0.5:
                paragraphs = paragraphs[last + 1:]
        # a note elsewhere: in Korean text, or naming the task itself (an English deliverable
        # in a Korean conversation keeps its own 「I'll …」)
        whole_korean = _latin_share("\n".join(paragraphs)) < 0.5
        paragraphs = [p for p in paragraphs if _structural(p) or not _english_note(p)
                      or not (whole_korean or _EN_NOTE_TASK.search(p))]
    else:
        # an English answer: its first paragraph, one line narrating a tool step, goes
        first = next((i for i, p in enumerate(paragraphs) if p.strip()), None)
        if first is not None and _EN_TOOL_STEP.match(paragraphs[first].strip()) \
                and "\n" not in paragraphs[first].strip() \
                and len("\n\n".join(paragraphs[first + 1:]).strip()) >= 80:
            paragraphs = paragraphs[first + 1:]
    kept: list[str] = []
    fenced = False
    opening = True  # no text line yet
    started = False  # no content line kept yet
    for line in "\n\n".join(paragraphs).split("\n"):
        stripped = line.lstrip()
        first_line, opening = opening, opening and not stripped
        if stripped.startswith("```"):
            fenced = not fenced
        if fenced or stripped.startswith(("```", "#", "|", "![")):
            kept.append(line)
            started = started or bool(stripped)
            continue
        if _LONE_MARK.match(line) or _QUESTION_STUB.match(line):
            continue
        if korean and stripped:
            lead = re.match(r"\s{0,24}(?:(?:[-*+]|\d{1,3}[.)])\s{1,4})?", line).group(0)
            sentences = [s for s in units.SENTENCE_BREAK.split(line[len(lead):]) if s.strip()]
            left = [s for s in sentences
                    if not _KO_NOTE.search(s)
                    and not (document and _DOC_NOTE.search(s))
                    and (asked_check or not _CHECK_RESULT.search(s))
                    and (asked_edit or not _SELF_EDIT.search(s))
                    and not (first_line and not asked_check and len(line) <= 100
                             and _OPENING_VERDICT.match(s))]
            if not started and not lead.strip() and not document:
                # narration of the next step, ahead of the answer's first content
                while left and _OPENING_NARRATION.match(left[0].strip()):
                    left.pop(0)
            if len(left) != len(sentences):
                if left:
                    kept.append(lead + " ".join(s.strip() for s in left))
                    started = True
                continue
        if stripped:
            started = True
        kept.append(line)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n")
    if cleaned == text.strip("\n"):
        return text
    # a rule left at the top (or the end) once the lines beside it went
    cleaned = re.sub(r"^(?:\s*(?:-{3,}|\*{3,}|_{3,})\s*\n)+", "", cleaned)
    return re.sub(r"(?:\n\s*(?:-{3,}|\*{3,}|_{3,})\s*)+$", "", cleaned).strip("\n")


#: a sentence about the work between tool calls (「…스크립트로 각도별 배분을
#: 받겠습니다.」「각도별 배분이 나왔습니다.」, 「점검 통과했습니다.」, 「수치를 다시
#: 확인했습니다.」 「…로 교체해 답을 다시 씁니다.」, 「(검색은 최신 사실 확인용이었고 …)」)
_WORK_SENTENCE = re.compile(
    r"(?:하겠습니다|겠습니다|할게요|볼게요|받겠습니다|드릴게요)[.!…]?\)?$"
    r"|(?:확인|점검|검증|검산|계산|재계산|실행|검색|조사|분류|수정|교체|반영|통과|완료|확정|파악|정리)"
    r"\s?(?:했|하였|됐|되었|해\s?봤|해\s?보았)습니다"
    r"|(?:나왔|잡혔|잡힙|찾았|바꿨|뺐|돌렸|받았|마쳤)습니다|잡힙니다"
    r"|(?:바꿉니다|뺍니다|씁니다|둡니다|넣습니다|고칩니다|확인합니다|돌립니다)[.!…]?\)?$"
    r"|검색\s?결과|^\(?\s?검색(?:은|을|으로|해)|스크립트|점검\s?결과|코드\s?(?:출력|실행)|실행\s?출력"
    r"|이전\s?턴|무관합니다|완성된\s?[^\s.]{1,12}입니다")
#: where a deliverable starts: a heading, table, rule, bold title, list item or fence
_DELIVERABLE_LINE = re.compile(
    r"^\s{0,3}(?:#{1,6}\s|\||-{3,}\s*$|\*{3,}\s*$|\*\*[^*\n]{1,80}\*\*|[-*+]\s|\d{1,2}[.)]\s"
    r"|[□○■●▶◆]|```)")
#: a Korean sentence's end, spaced or not (「…확인합니다.여전히 …」)
_KO_SENTENCE_END = re.compile(r"(?<=[다요죠][.!?…])\)?\s*|\n+")


def strip_tool_preamble(text: str) -> str:
    """`text` minus the work narration a tool turn wrote ahead of its deliverable: the lines
    before the first heading, table, rule or list, when they are all prose about the work
    (their first sentence and at least half of them) and the deliverable after them is the
    longer part. Prose that answers stays: a lead without a work sentence first, or one
    followed by little, is the answer."""
    lines = (text or "").split("\n")
    at = next((i for i, line in enumerate(lines) if _DELIVERABLE_LINE.match(line)), None)
    if not at:
        return text
    lead = "\n".join(lines[:at]).strip()
    rest = "\n".join(lines[at:])
    if not lead or len(rest.strip()) < max(300, len(lead)):
        return text
    sentences = [x.strip() for x in _KO_SENTENCE_END.split(lead) if x and x.strip()]
    work = [x for x in sentences if _WORK_SENTENCE.search(x)]
    if not sentences or not _WORK_SENTENCE.search(sentences[0]) or len(work) * 2 < len(sentences):
        return text
    return re.sub(r"^(?:\s*(?:-{3,}|\*{3,}|_{3,})\s*\n)+", "", rest.lstrip("\n"))


#: how a finished line ends: punctuation, a closing mark, or a Korean sentence or 개조식
#: ending (「…함」「…예정」「…완료」「…드림」)
_DONE_END = re.compile(r"(?:[.!?…。)\]」』\"'`|:*~>]|[다요죠음함임됨까네정료능중림것])\s*$")
#: a line ending on a particle or connective, which no phrase ends on
_PARTICLE_END = re.compile(r"(?:[가-힣](?:는|을|를|에서|및)|,)\s*$")
_ITEM_MARK = re.compile(r"^\s{0,8}(?:[-*+•·]|\d{1,3}[.)]|[□○◦▪■●ㆍ])\s")


def cut_mid_sentence(text: str, *, min_chars: int = 400) -> bool:
    """Whether a long answer or section stops in the middle of its last sentence
    (「○ 2027년 3월」 after items ending 「…배분함」; 「이수진 차장이 온라인 동시 송출」).
    A table, heading, fence, label line (「출처: …」) or link ends whole; a list item counts as
    cut only when the items above it end in sentence endings and it does not."""
    body = (text or "").rstrip()
    if len(body) < min_chars:
        return False
    lines = [line for line in body.split("\n") if line.strip()]
    last = lines[-1].strip()
    if (last.startswith(("|", "#", "```", "!", "<", "[")) or re.fullmatch(r"[-*_=]{3,}", last)
            or _DONE_END.search(last) or re.search(r"[:：]|https?://\S+$", last)
            or not re.search(r"[가-힣A-Za-z0-9,]$", last)):
        return False
    if _ITEM_MARK.match(lines[-1]):
        # a stub after full items: 「○ 2027년 3월」 under 「○ …직접 설계·배분함」
        above = [line.strip() for line in lines[-4:-1] if _ITEM_MARK.match(line)]
        return (len(above) >= 2 and all(_DONE_END.search(a) for a in above)
                and (_PARTICLE_END.search(last) is not None
                     or len(last) * 2 <= min(len(a) for a in above)))
    return len(_HANGUL.findall(last)) >= 3


#: a paragraph announcing the writer's own next step (「Let me draft:」「Now I'll write …」)
_EN_PLAN_STEP = re.compile(
    r"^\s{0,4}(?:[-*>]\s{1,3})?(?:\*\*)?(?:Let me(?!\sknow)|Let's|I(?:'ll| will| need to"
    r"| should| must| have to| could| can)|Now(?:,)?\s(?:I|let)|First(?:,)?\sI|Then(?:,)?\sI"
    r"|Next(?:,)?\sI|Actually|Better to|So I|OK(?:ay)?,?\s(?:I|let)|The user\b|Format\s?:"
    r"|Key points\b)")
#: …about the writing task itself
_EN_PLAN_TOPIC = re.compile(
    r"\b(?:the user|draft|skill|skeleton|format|structure|section|compose|rule|the (?:answer"
    r"|report|memo|material|request|check)|tools?)\b", re.I)
#: the person asked for English text: its 「I'll …」 is the deliverable
_ASKED_ENGLISH = re.compile(r"영문|영어|English", re.I)


#: a sentence that only says what the writer is about to do (「…공개 자료로 확인하겠습니다.」)
_KO_INTENT = re.compile(
    r"(?:확인|검색|조사|찾아\s?보|찾|알아\s?보|살펴\s?보|살펴|정리|작성|분석|검토|수집|파악"
    r"|준비|읽어\s?보|읽|모아\s?보|모으|비교|계산|구성|진행)"
    r"(?:해\s?보|하여\s?보|해)?\s?(?:하겠습니다|겠습니다|드리겠습니다|할게요|볼게요|하겠어요)"
    r"\s?[.!…]?\s*$")
#: …or asks to wait for it
_KO_WAIT = re.compile(r"^(?:잠시만?|조금만)\s?기다려\s?주(?:세요|십시오)[.!]?$")


def is_intent_only(text: str, *, request: str = "") -> bool:
    """Whether a Korean answer is only a line or two saying the work is about to be done
    (「K-water 개요·조직·재무 … 공개 자료로 확인하겠습니다.」), no deliverable."""
    body = (text or "").strip()
    if not body or len(body) > 300 or _latin_share(request or body) >= 0.5:
        return False
    if any(_structural(line) or re.match(r"\s{0,4}(?:[-*+]|\d{1,2}[.)])\s", line)
           for line in body.split("\n") if line.strip()):
        return False
    sentences = [s.strip() for s in units.SENTENCE_BREAK.split(" ".join(body.split())) if s.strip()]
    return bool(sentences) and len(sentences) <= 3 and all(
        _KO_INTENT.search(s) or _KO_WAIT.match(s) for s in sentences) and any(
        _KO_INTENT.search(s) for s in sentences)


#: a sentence announcing the writer's next piece of work (「Let me compute the totals.」)
_EN_ANNOUNCE = re.compile(
    r"(?:\*\*)?(?:Let me(?!\s+know)|Let's|I(?:'ll| will|'m going to| am going to| need to)"
    r"|Now(?:,)?\s(?:I|let me)|Next(?:,)?\s(?:I|let me)|First(?:,)?\s(?:I|let me))\b"
    r"[^\n]{0,160}?\b(?:comput|calculat|check|search|look\s(?:up|into|at)|verif|writ|draft"
    r"|prepar|gather|fetch|find|review|analy[sz]|summari[sz]|confirm|run\b|research|compil"
    r"|put together|work out|figure out)", re.I)


def is_announcement(text: str) -> bool:
    """Whether a short reply, in any language asked for, only announces work still to do
    (「I have enough verified data. Let me compute the recipe's calorie and
    macronutrient totals precisely.」 was the whole answer): no list, table or heading, at
    most three sentences, the last one an announcement, almost no Hangul."""
    body = (text or "").strip()
    if not body or len(body) > 300 or len(_HANGUL.findall(body)) >= 10:
        return False
    if any(_structural(line) or re.match(r"\s{0,4}(?:[-*+]|\d{1,2}[.)])\s", line)
           for line in body.split("\n") if line.strip()):
        return False
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…:])\s+|\n+", body) if s.strip()]
    return 0 < len(sentences) <= 3 and bool(_EN_ANNOUNCE.match(sentences[-1]))


def is_work_plan(text: str, *, request: str = "") -> bool:
    """Whether a Korean request's answer is the writer's plan or narration instead of the
    deliverable: three or more English paragraphs about its own next steps, the last one
    among them (it stops at 「Let me write the final answer …」), or nothing but notes left
    once `strip_work_notes` runs; for any request, a short reply that only announces the
    work (`is_announcement`)."""
    if not text or not text.strip():
        return False
    if is_announcement(text):
        return True
    if (_latin_share(request) >= 0.5 if request.strip() else _latin_share(text) >= 0.5) or (
            _ASKED_ENGLISH.search(request)):
        return False  # English asked for: its 「I'll …」 is the prose
    if is_intent_only(text, request=request):
        return True
    paragraphs =[p for p in re.split(r"\n[ \t]*\n", text) if p.strip()]
    steps = [i for i, p in enumerate(paragraphs)
             if not _structural(p) and _EN_PLAN_STEP.match(p) and len(_LATIN.findall(p)) >= 12]
    if (len(steps) >= 3 and steps[-1] == len(paragraphs) - 1
            and sum(bool(_EN_PLAN_TOPIC.search(paragraphs[i])) for i in steps) >= 2):
        return True
    left = strip_work_notes(text, request=request)
    return len(text.strip()) >= 200 and left != text and len(_HANGUL.findall(left)) < 20


def has_work_notes(text: str, *, request: str = "") -> bool:
    """Whether `text` carries work notes `strip_work_notes` would remove."""
    return strip_work_notes(text, request=request) != text


__all__ = ["DOCUMENT_TERMS", "JSON_LINE", "TERMS", "TOOL_APOLOGY", "TOOL_REPORT", "WRITER_NOTE",
           "found", "has_tool_call", "has_work_notes", "is_announcement", "is_intent_only",
           "is_work_plan", "scrub",
           "strip_check_block",
           "strip_echoed_heading", "strip_reports", "strip_tool_apologies", "strip_tool_calls",
           "written_tool_calls",
           "strip_work_notes"]
