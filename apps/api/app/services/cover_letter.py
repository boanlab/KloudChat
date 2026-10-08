"""Server post-processing of a cover-letter answer (자기소개서): lengths measured, not trusted.

The answer's 「[제출용 답안 n]」 blocks are measured with text-fit against the limits the
person stated (「공백 포함 700자」, 「1,000바이트 이내」); a block outside its range gets up to
two rewrites asking for the exact amount to add or cut, and the closest result is kept. An answer
written without the tags is read by the question headings the request numbered. Numbers, titles and blind-hiring
words the person's material does not carry are flagged, never filled in. Only the blocks are
measured (contract C1): an explanation or a refusal outside them is not.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.core import logs
from app.services import report_checks, skill_scripts

log = logging.getLogger(__name__)

_MARKER = re.compile(
    r"^[ \t]{0,3}[>*_#\s]{0,6}[\[［]\s{0,2}제출용\s{0,2}답안\s{0,2}(\d{1,2})\s{0,2}[\]］][*_ \t]{0,4}(.{0,200})$",
    re.M,
)
#: A line that ends a block: a heading, a rule, a table, a count line, a note, the next tag.
_BLOCK_END = re.compile(
    r"^[ \t]{0,3}(?:#{1,6}\s|-{3,}\s*$|\*{3,}\s*$|\||[\[［]|\*\*[\[［(]|※|\(?\s{0,2}(?:글자\s{0,2}수|바이트\s{0,2}수|분량)"
    r"|>\s{0,2}※|(?:설명|참고|메모|안내)\s{0,2}[:：])"
    # A table written without its outer pipes: 「문항 | 기준 | 실제 | 한도」.
    r"|^[ \t]{0,3}[^|\n]{1,40}\|[^|\n]{1,80}\|"
)
_FENCE = re.compile(r"^\s{0,3}```")
#: A question heading in an answer written without the tags: 「**1. 지원 동기**」, 「### 문항 2」,
#: 「[문항 1] …」, 「Q3.」. The rest of the line is a short title.
_HEADING = re.compile(
    r"^[ \t]{0,3}(?:#{1,6}[ \t]{1,3})?[*_]{0,3}[ \t]{0,2}[\[［(]?[ \t]{0,2}"
    r"(?:(?:문항|Q)[ \t]{0,2}(\d{1,2})[ \t]{0,2}(?:번)?|(\d{1,2})[ \t]{0,2}(?:번[ \t]{0,2}문항|[.)번]))"
    r"[ \t]{0,2}[\]］)]?[ \t]{0,2}[.:：]?(.{0,120})$"
)

_LIMIT = re.compile(
    r"(?P<pre>공백\s{0,2}(?:포함|제외)\s{0,2})?(?P<n>\d{1,2}(?:,\d{3})|\d{2,5})\s{0,2}"
    r"(?P<unit>자|글자|바이트|bytes?|byte)(?![가-힣A-Za-z])"
    r"(?P<post>\s{0,2}\(\s{0,2}공백\s{0,2}(?:포함|제외)\s{0,2}\))?"
    r"(?P<tail>\s{0,2}(?:이내|이하|내외|제한|까지|미만|안으로|안쪽))?",
    re.I,
)
_QUESTION = re.compile(r"^\s{0,4}(?:\*\*)?\s{0,2}(?:문항\s{0,2}|Q\s{0,2})?(\d{1,2})\s{0,2}(?:[.)번]|\s{0,2}[:：])")
_QUESTION_ANYWHERE = re.compile(r"(?:문항|Q)\s{0,2}(\d{1,2})|(\d{1,2})\s{0,2}번\s{0,2}문항")
_MIN_RATIO = re.compile(r"(\d{2})\s{0,2}%\s{0,2}(?:이상|넘게|넘도록)")
_NEWLINE_BYTES = re.compile(r"줄\s{0,2}바꿈\s{0,2}(?:은|는|:)?\s{0,2}(\d)\s{0,2}(?:바이트|byte|자)", re.I)
_TITLE_WORDS = re.compile(
    r"팀장|파트장|파트\s{0,2}리드|본부장|센터장|실장|부장|차장|과장(?![하된되])|대리|수석|책임(?![지져질감을이은의])|선임|매니저|"
    r"대표(?![적하로])|위원장|회장|조장|"
    # 「리더십」 is a quality, not a title.
    r"(?<![A-Za-z])(?:PM|PL|CTO|CEO|리더(?!십))(?![A-Za-z])"
)
_SCHOOL = re.compile(r"[가-힣]{2,12}(?:대학교|여자대학|고등학교|과학고|외고)")
_FAMILY = re.compile(r"아버지|어머니|부모님|아빠|엄마|형제|누나|언니|오빠|가족|고향")


@dataclass(slots=True)
class Limit:
    question: int | None
    limit: int
    unit: str
    newline: int | None = None
    #: Said to the person when the counting rule was not stated and the strictest was used.
    assumed: str = ""


@dataclass(slots=True)
class Block:
    number: int
    text: str
    #: The block's lines as written in the answer, for replacing it.
    raw: str


@dataclass(slots=True)
class Outcome:
    content: str
    rows: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    rewritten: list[int] = field(default_factory=list)
    #: The judge's 「same experience, same content」 candidates, for a person to check.
    repeats: list[str] = field(default_factory=list)
    detail: str = ""


def limits(text: str) -> tuple[list[Limit], float | None]:
    """The length limits a request states, by question number when it numbers them."""
    found: list[Limit] = []
    lines = (text or "").splitlines()
    for line in lines:
        for m in _LIMIT.finditer(line):
            context = line[max(0, m.start() - 20): m.end() + 20]
            if not (m.group("pre") or m.group("post") or m.group("tail")
                    or re.search(r"글자\s{0,2}수|분량|제한|기준", context)):
                continue
            n = int(m.group("n").replace(",", ""))
            if n < 50:
                continue
            unit_word = m.group("unit").lower()
            rule = line[m.start(): m.end() + 120]
            newline = None
            assumed = ""
            if unit_word in ("자", "글자"):
                spaces = (m.group("pre") or "") + (m.group("post") or "")
                unit = "chars_no_spaces" if "제외" in spaces else "chars_with_spaces"
                if not spaces:
                    assumed = "세는 법이 없어 공백 포함으로 맞췄습니다"
            else:
                if re.search(r"한글\s{0,2}2\s{0,2}바이트|2\s{0,2}바이트\s{0,2}\(?\s{0,2}한글", rule):
                    unit = "bytes_ko2"
                elif re.search(r"UTF-?8|한글\s{0,2}3\s{0,2}바이트", rule, re.I):
                    unit = "bytes_utf8"
                else:
                    unit = "bytes_utf8"
                    assumed = "바이트 규칙이 없어 가장 엄격한 기준(UTF-8, 줄바꿈 2바이트)으로 맞췄습니다"
                if nl := _NEWLINE_BYTES.search(rule):
                    newline = int(nl.group(1))
            question = None
            if q := _QUESTION.match(line):
                question = int(q.group(1))
            elif q := _QUESTION_ANYWHERE.search(line[: m.start()]):
                question = int(q.group(1) or q.group(2))
            found.append(Limit(question, n, unit, newline, assumed))
    ratio = None
    if m := _MIN_RATIO.search(text or ""):
        value = int(m.group(1))
        if 50 <= value <= 100:
            ratio = value / 100
    return found, ratio


#: A turn asking for the letter to be written or changed, not a question about it.
_WRITE_ASK = re.compile(r"자기\s{0,2}소개서|자소서|문항|써\s{0,2}줘|고쳐|다시\s{0,2}써|늘려|줄여|넣어|빼\s{0,2}줘|맞춰")


def blocks(answer: str, request: str = "", material: str = "") -> list[Block]:
    """The 「[제출용 답안 n]」 blocks: the lines after each tag up to the next tag or a note.

    With no tag, when this turn asks for writing and the request or material numbers its
    questions with limits, the answer's own question headings mark the blocks instead."""
    tagged = _tagged_blocks(answer)
    if tagged or not _WRITE_ASK.search(request or ""):
        return tagged
    stated = limits(request)[0] or limits(material)[0]
    numbers = {lim.question for lim in stated if lim.question is not None}
    return _heading_blocks(answer, numbers) if numbers else []


def _heading_blocks(answer: str, numbers: set[int]) -> list[Block]:
    lines = (answer or "").split("\n")
    heads: list[tuple[int, int]] = []
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        m = None if in_fence or _table_row(line) else _HEADING.match(line)
        if m and int(m.group(1) or m.group(2)) in numbers:
            heads.append((i, int(m.group(1) or m.group(2))))
    seen = [n for _, n in heads]
    # Each asked question once, in order: a numbered list inside an answer is not a heading.
    if not heads or len(set(seen)) != len(seen) or seen != sorted(seen):
        return []
    out: list[Block] = []
    for k, (start, number) in enumerate(heads):
        stop = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        body: list[str] = []
        j = start + 1
        while j < stop and not (_BLOCK_END.match(lines[j]) and lines[j].strip()
                                and not lines[j].lstrip().startswith(("**[", "[", "［"))):
            body.append(lines[j])
            j += 1
        while body and not body[-1].strip():
            body.pop()
        lead = 0
        while lead < len(body) and not body[lead].strip():
            lead += 1
        text = "\n".join(body[lead:])
        if len(text.strip()) < 100:
            return []
        raw_end = j
        while raw_end > start + 1 and not lines[raw_end - 1].strip():
            raw_end -= 1
        out.append(Block(number, text, "\n".join(lines[start + 1:raw_end])))
    return out


def _tagged_blocks(answer: str) -> list[Block]:
    lines = (answer or "").split("\n")
    out: list[Block] = []
    i = 0
    while i < len(lines):
        m = _MARKER.match(lines[i])
        if not m:
            i += 1
            continue
        number, trailing = int(m.group(1)), m.group(2).strip()
        start = i
        body: list[str] = []
        # A long remainder on the tag line is the answer itself; a short one is its title.
        if len(trailing) > 60:
            body.append(trailing)
        j = i + 1
        fenced = False
        while j < len(lines):
            line = lines[j]
            if _FENCE.match(line):
                fenced = not fenced
                j += 1
                if not fenced and body:
                    break
                continue
            if not fenced and (_MARKER.match(line) or _BLOCK_END.match(line)):
                break
            body.append(re.sub(r"^\s{0,3}>\s?", "", line))
            j += 1
        while body and not body[-1].strip():
            body.pop()
        while body and not body[0].strip():
            body.pop(0)
        raw_end = j
        while raw_end > start + 1 and not lines[raw_end - 1].strip():
            raw_end -= 1
        out.append(Block(number, "\n".join(body), "\n".join(lines[start + 1:raw_end])))
        i = j
    return out


def _pair(found: list[Block], stated: list[Limit]) -> list[tuple[Block, Limit]]:
    numbered = {lim.question: lim for lim in stated if lim.question is not None}
    pairs = []
    for index, block in enumerate(found):
        if block.number in numbered:
            pairs.append((block, numbered[block.number]))
        elif len(stated) == 1:
            pairs.append((block, stated[0]))
        elif not numbered and index < len(stated):
            pairs.append((block, stated[index]))
    return pairs


def measure(block: Block, limit: Limit, ratio: float | None) -> dict:
    fit = skill_scripts.text_fit()
    item = {"label": f"답안 {block.number}", "text": block.text, "limit": limit.limit,
            "unit": limit.unit, "min_ratio": ratio or 0.8}
    if limit.newline is not None:
        item["newline"] = limit.newline
    row = fit.fit(item)
    row["shown"] = fit.shown(row)
    row["number"] = block.number
    if limit.assumed:
        row["assumed"] = limit.assumed
    return row


def unsupported(text: str, material: str) -> list[str]:
    """Numbers, titles and blind-hiring words in `text` that the person's material lacks."""
    numbers = report_checks.figure_tokens(material)
    known = set(numbers) | report_checks._derived(numbers)
    flags: list[str] = []
    for value in report_checks.figure_tokens(text):
        if value >= 2 and not report_checks._known(value, known) and not 1900 <= value <= 2100:
            flags.append(f"{value:g}")
    for m in _TITLE_WORDS.finditer(text):
        word = re.sub(r"\s+", "", m.group(0))
        if word not in re.sub(r"\s+", "", material):
            flags.append(m.group(0))
    if "블라인드" in material:
        flags.extend(m.group(0) for m in _SCHOOL.finditer(text))
        flags.extend(m.group(0) for m in _FAMILY.finditer(text))
    return list(dict.fromkeys(flags))


def _flag_note(flags: list[str]) -> str:
    """The flagged words, with the blank to put in their place: a number the material does not
    carry becomes 「[구체적 성과 수치]」, a title 「[직함]」."""
    numbers = [f for f in flags if re.fullmatch(r"[\d.]+", f)]
    hint = "「[구체적 성과 수치]」" if numbers else ""
    if len(numbers) < len(flags):
        hint += ("·" if hint else "") + "「[직함]」"
    return (f"자료에 없는 숫자·직함·블라인드 항목: {', '.join(flags[:8])} — 지어내지 말고 "
            f"{hint}처럼 비워 두세요")


_REWRITE = """아래는 자기소개서 「[제출용 답안 {n}]」의 글이다. {how} 기준으로 {low:,}~{high:,}{suffix} 안에 들어가게 고쳐 써라.
지금 {value:,}{suffix}이라 {gap}. 약 {delta:,}{suffix}{verb}{approx} 목표는 {target:,}{suffix}이다.

규칙:
- 사실·숫자·회사명·직함은 그대로 둔다. 아래 자료에 없는 사실, 숫자, 성과를 더하지 마라.
- 「[구체적 성과 수치]」 같은 대괄호 빈칸은 채우지 말고 그대로 둔다.
{rule}
- 문단 나눔은 유지한다. 고친 글만 답하라(태그·제목·설명·글자 수 없이).{avoid}

자료(늘릴 때 쓸 수 있는 사실은 여기까지):
{material}

글:
{text}"""

_CUT = "- 줄일 때는 꾸밈말과 되풀이를 덜고, 사실과 수치는 남긴다."
_ADD = ("- 늘릴 때는 위 자료에 있는 사실로만 이미 쓴 경험의 상황·행동·배운 점을 구체적으로 쓴다. "
        "자료에 없는 수치·성과가 들어갈 자리는 「[구체적 성과 수치]」처럼 대괄호 빈칸으로 둔다.")

_HOW = {"chars_with_spaces": "공백 포함 글자 수", "chars_no_spaces": "공백 제외 글자 수",
        "bytes_utf8": "UTF-8 바이트(한글 3바이트, 영문·숫자·공백 1바이트, 줄바꿈 {nl}바이트)",
        "bytes_ko2": "한글 2바이트·그 밖 1바이트(줄바꿈 {nl}바이트)"}
#: Bytes one Korean letter takes, for 「한글로 약 n자」.
_PER_LETTER = {"bytes_utf8": 3, "bytes_ko2": 2}
#: Rewrites per block.
_ATTEMPTS = 2
_THINK = re.compile(r"<think>.{0,20000}?</think>", re.S)
_LEAD_LABEL = re.compile(r"^\s{0,3}(?:\*\*)?(?:고친|수정한|다시 쓴)\s{0,2}(?:글|답안|본문)(?:\*\*)?\s{0,2}[:：]?\s{0,2}\n")


def _distance(row: dict) -> int:
    return int(row.get("over", 0)) + int(row.get("under", 0))


def _cleaned(reply: str) -> str:
    text = _THINK.sub("", reply or "").strip()
    text = _LEAD_LABEL.sub("", text, count=1)
    text = _MARKER.sub("", text).strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.strip("`").strip()
    return text


def _rewrite_prompt(number: int, text: str, row: dict, limit: Limit, material: str,
                    avoid: list[str]) -> str:
    high, low = row["limit"], row["min"]
    over = row["status"] == "over"
    target = math.floor(high * 0.95) if over else (low + high + 1) // 2
    target = max(low, min(high, target))
    delta = abs(row["value"] - target)
    per = _PER_LETTER.get(limit.unit)
    approx = f"(한글로 약 {math.ceil(delta / per):,}자)" if per else ""
    gap = (f"한도를 {row['over']:,}{row['suffix']} 넘는다" if over
           else f"하한 {low:,}{row['suffix']}보다 {row['under']:,}{row['suffix']} 모자란다")
    return _REWRITE.format(
        n=number, how=_HOW[limit.unit].format(nl=row.get("newline", 2)), low=low, high=high,
        suffix=row["suffix"], value=row["value"], gap=gap, delta=delta,
        verb=" 줄여라" if over else " 늘려라", approx=approx + "." if approx else ".",
        target=target, rule=_CUT if over else _ADD,
        avoid=("\n- 지난번 고친 글에 자료에 없는 말이 들어가 버렸다. 쓰지 마라: " + ", ".join(avoid[:8]))
        if avoid else "",
        material=(material or "(없음)").strip()[:6000], text=text)


Complete = Callable[..., Awaitable[tuple[str, dict]]]


async def post_process(
    answer: str, *, request: str, material: str, complete: Complete | None = None,
    model: str = "", api_key: str = "", usage: dict | None = None, judge: str = "",
) -> Outcome | None:
    """None when the answer has no submit blocks or the request states no limit.

    With `judge`, two or more blocks are read once for the same experience told twice."""
    found = blocks(answer, request, material)
    if not found:
        return None
    stated, ratio = limits(request)
    if not stated:
        stated, ratio_more = limits(material)
        ratio = ratio or ratio_more
    pairs = _pair(found, stated)
    outcome = Outcome(content=answer)
    if not pairs:
        outcome.flags = unsupported("\n".join(b.text for b in found), material)
        if judge and complete is not None and len(found) >= 2:
            outcome.repeats = await repeats({b.number: b.text for b in found}, complete=complete,
                                            judge=judge, api_key=api_key, usage=usage)
        outcome.detail = " · ".join(x for x in (
            _flag_note(outcome.flags) if outcome.flags else "",
            "같은 경험 반복 후보(판정, 확인 필요): " + " / ".join(outcome.repeats[:4])
            if outcome.repeats else "") if x)
        return outcome if outcome.flags or outcome.repeats else None
    rows = {block.number: measure(block, limit, ratio) for block, limit in pairs}
    failing = [(b, lim) for b, lim in pairs if rows[b.number].get("status") in ("over", "under")]
    if failing and complete is not None and model:
        async def repair(block: Block, limit: Limit) -> None:
            # Up to two targeted rewrites; each starts from the closest text so far.
            best_text, best_row = block.text, rows[block.number]
            before = set(unsupported(block.text, material))
            avoid: list[str] = []
            for attempt in range(_ATTEMPTS):
                prompt = _rewrite_prompt(block.number, best_text, best_row, limit, material, avoid)
                try:
                    text, spent = await complete(
                        model, [{"role": "user", "content": prompt}], api_key,
                        min(4000, 3 * max(len(best_text), best_row["limit"]) + 400))
                except Exception as exc:  # noqa: BLE001 — the block stays as it was
                    log.info("cover letter rewrite %d of block %d failed: %r",
                             attempt + 1, block.number, exc)
                    continue
                for key in ("inputTokens", "outputTokens"):
                    if usage is not None:
                        usage[key] = usage.get(key, 0) + int((spent or {}).get(key, 0))
                new = _cleaned(text)
                if not new or not block.raw.strip():
                    log.info("cover letter rewrite %d of block %d came back empty",
                             attempt + 1, block.number)
                    continue
                again = measure(Block(block.number, new, block.raw), limit, ratio)
                invented = [f for f in unsupported(new, material) if f not in before]
                if invented:
                    log.info("cover letter rewrite %d of block %d added %s; not kept",
                             attempt + 1, block.number, invented[:4])
                    avoid.extend(f for f in invented if f not in avoid)
                    continue
                if _distance(again) < _distance(best_row):
                    best_text, best_row = new, again
                if best_row["status"] == "ok":
                    break
            if best_text != block.text:
                outcome.content = outcome.content.replace(block.raw, best_text, 1)
                rows[block.number] = {**best_row, "rewritten": True}
                outcome.rewritten.append(block.number)

        await asyncio.gather(*(repair(b, lim) for b, lim in failing))
        # Measured again from the answer as it now stands.
        final_blocks = {b.number: b for b in blocks(outcome.content, request, material)}
        for block, limit in pairs:
            if block.number in final_blocks:
                fresh = measure(final_blocks[block.number], limit, ratio)
                rows[block.number] = {**fresh, **({"rewritten": True}
                                                  if rows[block.number].get("rewritten") else {})}
    outcome.rows = [rows[b.number] for b, _ in pairs]
    outcome.content = fix_count_table(outcome.content, outcome.rows)
    outcome.content = _measured_wording(
        outcome.content, outcome.rows, [b.text for b in blocks(outcome.content, request, material)])
    final = {b.number: b.text for b in blocks(outcome.content, request, material)}
    outcome.flags = unsupported("\n".join(final.values()), material)
    outcome.detail = " · ".join(
        f"{r['label']} {r['shown']}" + (" (다시 씀)" if r.get("rewritten") else "")
        + {"over": f" — {r['over']:,}{r['suffix']} 초과", "under": f" — {r['under']:,}{r['suffix']} 모자람"}
        .get(r.get("status", ""), "")
        for r in outcome.rows
    )
    if outcome.flags:
        outcome.detail += " · " + _flag_note(outcome.flags)
    if judge and complete is not None and len(final) >= 2:
        outcome.repeats = await repeats(final, complete=complete, judge=judge, api_key=api_key,
                                        usage=usage)
        if outcome.repeats:
            outcome.detail += " · 같은 경험 반복 후보(판정, 확인 필요): " + " / ".join(outcome.repeats[:4])
    log.info("cover letter measured: %s", logs.safe(outcome.detail, 600))
    return outcome


async def repeats(final: dict[int, str], *, complete: Complete, judge: str, api_key: str,
                  usage: dict | None = None) -> list[str]:
    """「답안 2: …」 for each sentence the judge reads as an experience already told the same way."""
    text = "\n\n".join(f"[제출용 답안 {n}]\n{body}" for n, body in sorted(final.items()))
    items, spent = await report_checks.judge_json(
        complete, judge, api_key, report_checks._REPEAT_PROMPT.format(
            what="자기소개서 답안", text=text[:10000]), max_tokens=800)
    for key in ("inputTokens", "outputTokens"):
        if usage is not None:
            usage[key] = usage.get(key, 0) + int((spent or {}).get(key, 0))
    out = []
    for item in items:
        quoted = report_checks._flat(str(item.get("sentence") or ""))
        number = next((n for n, body in final.items()
                       if len(quoted) >= 6 and quoted in report_checks._flat(body)), None)
        if number is not None:
            out.append(f"답안 {number}: 「{quoted[:60]}」")
    return list(dict.fromkeys(out))[:6]


_COUNT_HEADER = re.compile(r"실제|측정|글자\s{0,2}수|바이트\s{0,2}수|분량")
_CAP_HEADER = re.compile(r"한도|제한|최대")
_LIMIT_HEADER = re.compile(r"한도|제한|기준|목표|최대|하한|최소|%|비율")


def _table_row(line: str) -> bool:
    """A row of a pipe table, with or without its outer pipes (「1 | 공백 포함 700자 | 397자」)."""
    return line.count("|") >= 2 or (line.lstrip().startswith("|") and line.count("|") >= 1)


def _measured_cell(cell: str, measured: dict) -> str:
    """The table cell with the code's count: the number swapped when the cell already says the
    same unit, the whole cell otherwise (「공백 포함 330자 (바이트는 확인하지 못함)」)."""
    suffix = measured.get("suffix", "")
    numbers = re.findall(r"\d[\d,]*", cell)
    same_unit = suffix and re.search(r"\d[\d,]*\s{0,2}" + re.escape(suffix), cell)
    if len(numbers) == 1 and same_unit and not _UNSURE.search(cell):
        return re.sub(r"\d[\d,]*", f"{measured['value']:,}", cell, count=1)
    return fit_shown(measured)


def fit_shown(row: dict) -> str:
    """「한글 2바이트 538바이트」: the count without its limit."""
    return f"{row.get('counted_as', '')} {row['value']:,}{row.get('suffix', '')}".strip()


def fix_count_table(answer: str, rows: list[dict]) -> str:
    """The answer's own count table, its measured column set to text-fit's numbers.

    The table may be written without outer pipes or a rule line; its rows are matched
    to blocks by the number in their first cell, or in order when no row is numbered.
    """
    by_number = {r["number"]: r for r in rows}
    lines = answer.split("\n")
    i = 0
    while i < len(lines):
        if not _table_row(lines[i]) or report_checks._TABLE_RULE.match(lines[i]):
            i += 1
            continue
        header = report_checks.cells(lines[i])
        column = next((k for k, h in enumerate(header)
                       if _COUNT_HEADER.search(h) and not _LIMIT_HEADER.search(h)), None)
        # 「한도」 alone is the stated limit; a model sometimes writes its own target there.
        cap = next((k for k, h in enumerate(header) if _CAP_HEADER.fullmatch(h.strip())), None)
        j = i + 1
        body: list[int] = []
        while j < len(lines) and _table_row(lines[j]):
            if not report_checks._TABLE_RULE.match(lines[j]):
                body.append(j)
            j += 1
        numbered = [re.search(r"\d{1,2}", (report_checks.cells(lines[k]) or [""])[0]) for k in body]
        by_order = not any(m and int(m.group(0)) in by_number for m in numbered) and len(body) == len(rows)
        for index, k in enumerate(body):
            row = report_checks.cells(lines[k])
            m = numbered[index]
            measured = (rows[index] if by_order
                        else by_number.get(int(m.group(0))) if m else None)
            if column is None or measured is None or column >= len(row):
                continue
            if re.search(r"\d", row[column]) or _UNSURE.search(row[column]):
                row[column] = _measured_cell(row[column], measured)
                if cap is not None and cap < len(row) and measured.get("limit") \
                        and re.search(r"\d", row[cap]):
                    row[cap] = f"{measured['limit']:,}{measured.get('suffix', '')}"
                piped = lines[k].lstrip().startswith("|")
                lines[k] = ("| " + " | ".join(row) + " |") if piped else " | ".join(row)
        i = max(j, i + 1)
    return "\n".join(lines)


#: 「바이트는 확인하지 못함」: a count the model could not make. Code made it.
_UNSURE = re.compile(r"확인\s{0,2}(?:하지|되지|을\s{0,2}하지)\s{0,2}(?:못|않)|검증\s{0,2}(?:하지|되지)\s{0,2}(?:못|않)"
                     r"|세지\s{0,2}못|측정\s{0,2}(?:하지|되지)\s{0,2}(?:못|않)|미확인")
_COUNT_WORD = re.compile(r"바이트|글자\s{0,2}수|글자|자\s{0,2}수|분량|byte", re.I)
_ALL_FIT = re.compile(r"(?:모두|모든\s{0,2}(?:답안|문항)|두\s{0,2}(?:답안|문항)\s{0,2}모두)[^\n]{0,30}"
                      r"(?:한도|기준|범위|분량)[^\n]{0,10}(?:이내|안|충족|맞)")


def _measured_wording(answer: str, rows: list[dict], texts: list[str]) -> str:
    """The answer's own words about the counts, said with code's numbers: a 「확인하지 못했습니다」
    line about the count gives way to the measured line, and 「모두 한도 이내」 goes when one is not."""
    if not rows:
        return answer
    inside = {line.strip() for text in texts for line in text.split("\n") if line.strip()}
    said = ("글자 수는 서버가 코드로 다시 쟀습니다: "
            + " · ".join(f"{r['label']} {r['shown']}" for r in rows) + ".")
    out: list[str] = []
    replaced = False
    all_ok = all(r.get("status") == "ok" for r in rows)
    for line in answer.split("\n"):
        plain = line.strip()
        if plain and plain not in inside and not _table_row(line):
            unsure = _UNSURE.search(line) and _COUNT_WORD.search(line)
            if unsure or (not all_ok and _ALL_FIT.search(line)):
                if not replaced:
                    out.append(said)
                    replaced = True
                continue
        out.append(line)
    return "\n".join(out)


def wire(outcome: Outcome) -> dict:
    """What the answer's routing carries for the line under it."""
    return {
        "rows": [
            {"number": r["number"], "label": r["label"], "unit": r["unit"], "value": r["value"], "limit": r.get("limit"),
             "min": r.get("min"), "status": r.get("status", "ok"),
             **({"newline": r["newline"]} if "newline" in r else {}),
             **({"rewritten": True} if r.get("rewritten") else {}),
             **({"assumed": r["assumed"]} if r.get("assumed") else {})}
            for r in outcome.rows
        ],
        "flags": outcome.flags[:12],
        **({"repeats": outcome.repeats[:6]} if outcome.repeats else {}),
    }
