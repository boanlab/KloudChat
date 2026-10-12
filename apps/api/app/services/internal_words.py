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
    # a check script's verdict counts: 「block 0 · check 0 · info 1」, 「(block 0)」
    r"|(?<![A-Za-z_])(?:block|check|info)\s{0,2}\d{1,4}\s{0,2}(?:[·,)]|$)"
    r"|\(\s{0,2}block\s{0,2}\d{1,4}\s{0,2}\)"
    r"|(?<![A-Za-z_])block\s{0,2}\)?\s{0,2}판정"
    r"|(?<![A-Za-z_])(?:calculate|run_code|run_skill_script|use_skill|web_search)\s{0,2}[:(（]"
    r"|스크립트(?:가|로|를|의|는|에서|로는)?\s{0,3}(?:[^\s.]{1,12}\s{1,2}){0,4}"
    r"(?:오류|실행|결과|한도|돌려|돌린|확인했|점검했|냈|검증|남긴|계산|지적|항목|판정)"
    # a check skill named in the answer: 「`copy-check` 로 한도 재검사」
    r"|`?(?<![A-Za-z_-])(?:copy-check|grounding-check|gap-check|calc-check|source-check|date-calc"
    r"|release-notes|internal-comms|generic-language-killer|pii-mask|text-fit)(?![A-Za-z_-])`?"
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


def strip_echoed_heading(text: str) -> str:
    """`text` without a prompt heading the model copied (「# Answer」), and without the
    prompt's problem list it echoed above one."""
    if not text:
        return text
    text = _ECHOED_HEADING.sub("", text, count=1).lstrip("\n")
    if m := _ECHOED_LINE.search(text):
        text = text[m.end():].lstrip("\n")
    return text


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
    r"|요청된\s?형식\s?\(")
#: a check's verdict belongs in the steps (「근거 대조 결과 근거 없는 값 0개.」)
_CHECK_RESULT = re.compile(
    r"^\W{0,4}(?:근거\s?대조|출처\s?점검|카피\s?점검|계산[·ㆍ]?\s?단위\s?검증|자체\s?(?:점검|검증|검토))"
    r"\s?결과|근거\s?없는\s?값\s?\d{1,3}\s?개")
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


def _english_note(paragraph: str) -> bool:
    return (len(_LATIN.findall(paragraph)) >= 20 and _latin_share(paragraph) >= 0.7
            and bool(_EN_NOTE_OPEN.match(paragraph)) and bool(_EN_NOTE_TOPIC.search(paragraph)))


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


def strip_work_notes(text: str, *, request: str = "") -> str:
    """`text` minus the writer's work notes: plan narration (English or Korean), a worksheet's
    labels and notes, check verdicts, lone 「check」 lines and 「[질문]」 stubs. Headings,
    tables and fences stay; an English deliverable is left alone."""
    if not text or not text.strip():
        return text
    korean = _latin_share(request) < 0.5 if request.strip() else _latin_share(text) < 0.5
    asked_check = bool(_ASKED_CHECK.search(request or ""))
    out = _worksheet(text) if korean else text
    paragraphs = re.split(r"\n[ \t]*\n", out)
    if korean:
        # an English preamble of notes before the answer: cut through its last English paragraph
        first = next((i for i, p in enumerate(paragraphs) if p.strip()), None)
        if first is not None and _english_note(paragraphs[first]):
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
    kept: list[str] = []
    fenced = False
    opening = True  # no text line yet
    for line in "\n\n".join(paragraphs).split("\n"):
        stripped = line.lstrip()
        first_line, opening = opening, opening and not stripped
        if stripped.startswith("```"):
            fenced = not fenced
        if fenced or stripped.startswith(("```", "#", "|", "![")):
            kept.append(line)
            continue
        if _LONE_MARK.match(line) or _QUESTION_STUB.match(line):
            continue
        if korean and stripped:
            lead = re.match(r"\s{0,24}(?:(?:[-*+]|\d{1,3}[.)])\s{1,4})?", line).group(0)
            sentences = [s for s in units.SENTENCE_BREAK.split(line[len(lead):]) if s.strip()]
            left = [s for s in sentences
                    if not _KO_NOTE.search(s)
                    and (asked_check or not _CHECK_RESULT.search(s))
                    and not (first_line and not asked_check and len(line) <= 100
                             and _OPENING_VERDICT.match(s))]
            if len(left) != len(sentences):
                if left:
                    kept.append(lead + " ".join(s.strip() for s in left))
                continue
        kept.append(line)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n")
    if cleaned == text.strip("\n"):
        return text
    # a rule left at the top once the line above it went
    return re.sub(r"^(?:\s*(?:-{3,}|\*{3,}|_{3,})\s*\n)+", "", cleaned).strip("\n")


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


def is_work_plan(text: str, *, request: str = "") -> bool:
    """Whether a Korean request's answer is the writer's plan or narration instead of the
    deliverable: three or more English paragraphs about its own next steps, the last one
    among them (it stops at 「Let me write the final answer …」), or nothing but notes left
    once `strip_work_notes` runs."""
    if not text or not text.strip():
        return False
    if (_latin_share(request) >= 0.5 if request.strip() else _latin_share(text) >= 0.5) or (
            _ASKED_ENGLISH.search(request)):
        return False  # English asked for: its 「I'll …」 is the prose
    paragraphs = [p for p in re.split(r"\n[ \t]*\n", text) if p.strip()]
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
           "found", "has_tool_call", "has_work_notes", "is_work_plan", "scrub", "strip_check_block",
           "strip_echoed_heading", "strip_reports", "strip_tool_apologies", "strip_tool_calls",
           "strip_work_notes"]
