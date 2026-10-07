"""Derived values computed in the code sandbox before a document is written.

The model writes a Python program over the given numbers; its printed lines reach the
writer as fixed values. A failed or empty run leaves the document unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import re

from app.services import settings_store
from app.services.context import instruction_part, pasted_material, prompt_request

log = logging.getLogger("kchat")

#: Words that ask for derived values.
_ASKS = re.compile(
    r"계산|구해|구하|산출|환산|변환|이득|데시벨|(?<![A-Za-z])dB(?![A-Za-z])|오차|평균|표준\s{0,2}편차|"
    r"분산|비율|증가율|감소율|증감|성장률|합계|총액|총합|(?<![A-Za-z])TCO(?![A-Za-z])|"
    r"(?<![A-Za-z])ROI(?![A-Za-z])|효율|백분율|퍼센트|회귀|상관|차단\s{0,2}주파수|손익|"
    r"단가|이자|수익률|할인율|환율|검산",
    re.I,
)
#: A data row of a Markdown table: 「| 1590 | 0.70 | …」.
_TABLE_ROW = re.compile(r"(?m)^\|\s{0,4}-?\d[\d.,]{0,15}\s{0,4}\|(?:[^|\n]{0,40}\|){1,20}")
_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]{0,15}(?:\.\d{1,10})?")

PROMPT = """아래 요청과 자료로 문서를 쓰기 전에, 문서에 들어갈 계산 값을 Python 으로 구한다.

할 일: 요청이 계산하라고 한 값(예: 측정점마다의 이득·이론값·오차, 합계, 비율, 증감)을
자료의 **모든 데이터 행에 대해** 구한다. 측정값에서 구하는 값(측정 이득 = 출력/입력,
측정 dB 등)과 이론값, 그 차이(오차)를 **모두** 구한다 — 이론값만 구하고 끝내지 마라.
자료의 측정값·표를 코드 안의 리스트로 그대로 옮겨 적고, 반복문으로 행마다 계산해
출력한다. 요약값 한두 개로 끝내지 마라.

규칙:
- 단위를 먼저 SI 기본 단위(N, m, Pa, s, 원 등)로 바꿔 계산한다. 출력은 반드시 요청·자료에
  쓰인 단위(mm, MPa, kN·m, 만 원 등)로 바꿔 적고, 출력 줄에 단위를 적는다.
- 자료와 요청에 있는 숫자만 쓴다. 없는 측정값·가정값을 만들지 마라. 이론값은 자료에
  있는 부품값·조건으로 공식에서 구한다.
- 표준 라이브러리(math, statistics)만 쓴다. 입력·파일·네트워크는 쓰지 않는다.
- 결과는 한 줄에 하나씩 print 한다: 「항목 = 값 단위」. 표가 될 값은 행마다
  「행 이름 | 열1 = 값 | 열2 = 값」 처럼 한 줄에 적는다. 값은 유효숫자 4자리(정수는 그대로).
- 문서에 필요한 값만, 60줄 이내로.

코드 블록 하나만 답하라:
```python
...
```

요청:
{request}

자료:
{material}"""


def needed(request: str, context: list[str] | None = None) -> bool:
    """Whether the instruction asks for derived values over data that is present."""
    instruction = instruction_part(request or "")
    if not _ASKS.search(instruction):
        return False
    data = "\n".join([request or "", *(context or [])])
    return len(_NUMBER.findall(data)) >= 4


def _code_of(text: str) -> str:
    match = re.search(r"```(?:python|py)?\s{0,4}\n(.{1,20000}?)```", text or "", re.S)
    return (match.group(1) if match else "").strip()


def _lines(stdout: str) -> list[str]:
    out = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line and ("=" in line or line.count("|") >= 2) and len(line) <= 300:
            out.append(line)
    return out[:60]


async def _one_run(
    prompt: str, material: str, *, complete, model: str, api_key: str, usage: dict[str, int]
) -> list[str]:
    """One program written and run: its printed lines, `[]` when it fails twice."""
    from app.services.tools import builtin

    error = ""
    for _attempt in range(2):
        messages = [{"role": "user", "content": prompt + error}]
        try:
            text, spent = await complete(model, messages, api_key, max_tokens=2500)
        except Exception as exc:  # noqa: BLE001 — any model failure leaves the document as is
            log.info("calculation code request failed: %s", exc)
            return []
        usage["inputTokens"] += spent.get("inputTokens", 0)
        usage["outputTokens"] += spent.get("outputTokens", 0)
        code = _code_of(text)
        if not code:
            error = "\n\n앞 답에 코드 블록이 없었다. ```python 블록 하나로 답하라."
            continue
        result = await builtin.execute_code({"code": code, "language": "python"})
        stdout = result.content.split("stdout:\n", 1)[-1].split("\n\nstderr:", 1)[0]
        lines = [] if result.failed else _lines(stdout)
        if lines and len(lines) < 3 and _TABLE_ROW.search(material):
            # The material has a table of data and the program printed a summary.
            error = (
                "\n\n앞 코드는 요약값만 출력했다. 자료의 데이터 표 행마다 요청한 값을 계산해 "
                "한 줄씩 출력하도록 고쳐서 다시 답하라."
            )
            continue
        if lines:
            return lines
        error = (
            "\n\n앞 코드는 실행에 실패했거나 「항목 = 값」 줄을 출력하지 않았다:\n"
            + result.content[:1500]
            + "\n고쳐서 다시 답하라."
        )
    return []


_FIGURE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def _item(line: str) -> tuple[str, list[float]]:
    """A printed line as `(name, figures)`; a table row's name is its first cell."""
    if "|" in line:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        name, rest = cells[0], " ".join(cells[1:])
    elif "=" in line:
        name, rest = line.split("=", 1)
    else:
        return "", []
    name = re.sub(r"[(（][^()（）]*[)）]|이론|계산|값", "", name)
    # Only Hangul and digits name an item, so 「차단 주파수 fc」 matches 「차단 주파수」.
    name = re.sub(r"[^가-힣0-9]", "", name) or re.sub(r"\s", "", name)
    figures = []
    for token in _FIGURE.findall(rest):
        try:
            figures.append(float(token))
        except ValueError:
            continue
    if "|" in line:
        # A row's own key (「100」 Hz) may repeat in its cells; the values follow it.
        name = _key(name) if re.match(r"^-?\d", name) else name
    return name, figures


def _close(x: float, y: float) -> bool:
    return abs(x - y) <= max(abs(x), abs(y)) * 0.005 + 1e-9


def confirmed(first: list[str], second: list[str]) -> list[str]:
    """Lines of `first` that `second` also computed: same item name, figures within 0.5%."""
    other = {}
    for line in second:
        name, figures = _item(line)
        if name and figures:
            other.setdefault(name, figures)
    out = []
    for line in first:
        name, figures = _item(line)
        theirs = other.get(name)
        if not (name and figures and theirs):
            continue
        # One run may print more columns; the shorter list must appear in the longer.
        short, long_ = sorted((figures, theirs), key=len)
        if all(any(_close(x, y) for y in long_) for x in short):
            out.append(line)
    return out


def agree(first: list[str], second: list[str]) -> bool:
    """Whether two runs agree: at least two shared items, four in five of them equal."""
    names_a = {n for n, f in map(_item, first) if n and f}
    names_b = {n for n, f in map(_item, second) if n and f}
    shared = names_a & names_b
    if len(shared) < 2:
        return False
    same = {n for n, _ in map(_item, confirmed(first, second))}
    return len(same & shared) >= 0.8 * len(shared)


async def computed_values(
    request: str,
    context: list[str] | None,
    *,
    complete,
    model: str,
    api_key: str,
) -> tuple[str, dict[str, int], str]:
    """`(block for the writer, usage, status)`; status is done/failed/unavailable/skipped/disputed.

    Independent programs must agree before any value is handed to the writer."""
    usage = {"inputTokens": 0, "outputTokens": 0}
    if not needed(request, context):
        return "", usage, "skipped"
    backends = await settings_store.tools_config()
    if not backends.exec:
        return "", usage, "unavailable"

    material = "\n\n".join(
        part for part in [pasted_material(request), *(context or [])] if part
    )[:14000]
    # A short paste is not split off as material: the request is then quoted whole.
    prompt = PROMPT.format(
        request=prompt_request(request, 6000), material=material or "(요청 안)"
    )
    again = prompt + (
        "\n\n(검산용으로 처음부터 독립적으로 다시 작성한다. 다른 풀이 방법을 써도 좋다. "
        "모든 값을 SI 기본 단위로 바꿔 계산한다.)"
    )
    runs: list[list[str]] = []

    def settled() -> str:
        for i, earlier in enumerate(runs):
            for lines in runs[i + 1 :]:
                if agree(earlier, lines) and agree(lines, earlier):
                    kept = confirmed(earlier, lines)
                    # A table keeps its header line when any of its rows is confirmed.
                    headers = [ln for ln in earlier if "|" in ln and not _FIGURE.search(ln)]
                    kept = (headers if any("|" in ln for ln in kept) else []) + kept
                    return (
                        "# 코드로 계산한 값\n"
                        "아래 값은 주어진 자료로 Python 을 실행해 구했고, 독립적으로 짠 두 "
                        "프로그램의 결과가 일치했다. 본문과 표에는 이 값을 그대로 옮기고, 같은 "
                        "항목을 다시 셈하거나 다른 값으로 적지 마라. 여기 없는 계산이 필요하면 "
                        "「(계산 필요)」로 남겨라.\n\n"
                        + "\n".join(kept)
                    )
        return ""

    # Up to two pairs of runs; a failed run does not count against agreement.
    for pair in ((prompt, again), (again, again)):
        results = await asyncio.gather(*(
            _one_run(p_, material, complete=complete, model=model, api_key=api_key, usage=usage)
            for p_ in pair
        ))
        runs.extend(lines for lines in results if lines)
        if block := settled():
            return block, usage, "done"
    if len(runs) >= 2:
        log.info("calculation runs disagree; no computed values handed to the writer")
        return "", usage, "disputed"
    return "", usage, "failed"


def _key(cell: str) -> str:
    """A row key as a plain number in base units: 「1 kHz」 → 「1000」, 「1,590 Hz」 → 「1590」."""
    text = (cell or "").replace(",", "").replace("*", "").strip()
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s{0,2}(k|M)?", text)
    if not m:
        return text
    value = float(m.group(1)) * {"k": 1e3, "M": 1e6}.get(m.group(2) or "", 1)
    return f"{value:g}"


def computed_table(block: str) -> tuple[list[str], list[list[str]]] | None:
    """The per-row table in a computed block, as `(header, rows)`; None without one.

    Two shapes are read: a header line and rows of bare cells (「100 | 0.990 | -0.087」),
    or rows naming each value (「100 Hz | 측정 dB = -0.087 | 이론 dB = -0.017」)."""
    lines = [line for line in (block or "").splitlines() if line.count("|") >= 1]
    named = [line for line in lines if "=" in line and line.count("|") >= 1]
    if len(named) >= 3:
        header = ["항목"] + [part.split("=")[0].strip() for part in named[0].split("|")[1:]]
        rows = []
        for line in named:
            parts = [p.strip() for p in line.split("|")]
            values = [p.split("=", 1)[1].strip() if "=" in p else p for p in parts[1:]]
            rows.append([parts[0], *values])
        if all(len(r) == len(header) for r in rows):
            return header, rows
    bare = [line for line in lines if "=" not in line]
    if len(bare) >= 4:
        cells = [[c.strip() for c in line.strip("|").split("|")] for line in bare]
        header, rows = cells[0], [r for r in cells[1:] if len(r) == len(cells[0])]
        if len(rows) >= 3 and not re.match(r"^-?\d", header[0]):
            return header, rows
    return None


def replace_tables(sections: list[dict], block: str) -> tuple[list[dict], int]:
    """Tables whose rows match the computed table's first cells, replaced by it.

    Returns (sections, number of tables replaced)."""
    table = computed_table(block)
    if table is None:
        return sections, 0
    header, rows = table
    keys = {_key(r[0]) for r in rows}
    rendered = "\n".join(
        ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        + ["| " + " | ".join(r) + " |" for r in rows]
    )
    changed = 0
    out = []
    for sec in sections:
        text = str(sec.get("content") or "")
        if str(sec.get("format") or "markdown") != "markdown" or "|" not in text:
            out.append(sec)
            continue
        lines = text.split("\n")
        result: list[str] = []
        i = 0
        while i < len(lines):
            if lines[i].strip().startswith("|"):
                j = i
                while j < len(lines) and lines[j].strip().startswith("|"):
                    j += 1
                block_rows = [
                    [c.strip() for c in line.strip().strip("|").split("|")]
                    for line in lines[i:j]
                    if not re.fullmatch(r"[\s|:-]+", line)
                ]
                body_keys = {_key(r[0]) for r in block_rows[1:]}
                if len(body_keys) >= 3 and len(body_keys & keys) >= 0.6 * len(body_keys):
                    result.append(rendered)
                    changed += 1
                else:
                    result.extend(lines[i:j])
                i = j
                continue
            result.append(lines[i])
            i += 1
        out.append({**sec, "content": "\n".join(result)})
    return out, changed
