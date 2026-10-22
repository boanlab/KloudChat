"""Korean money amounts: parsing, table unit labels, written-calculation fixes."""

from __future__ import annotations

import re

#: unit words → won, longest first (백만 before 만)
_SCALE = (("천만", 1e7), ("백만", 1e6), ("억", 1e8), ("만", 1e4), ("천", 1e3), ("", 1.0))
_GIVEN = re.compile(r"(?<![\d.])(\d[\d,]{0,15}(?:\.\d{1,6})?)\s{0,2}(억|천만|백만|만|천)?\s{0,1}원")
_LABEL_SPOT = re.compile(
    r"[(（]\s{0,2}(?:단위\s{0,2}[:：]\s{0,2})?((?:천만|백만|억|만|천)?\s{0,1}원)\s{0,2}[)）]"
    r"|단위\s{0,2}[:：]\s{0,2}((?:천만|백만|억|만|천)?\s{0,1}원)"
)
_CELL_NUMBER = re.compile(r"^\**\s{0,2}(-?\d[\d,]{0,15}(?:\.\d{1,6})?)\s{0,2}\**$")

#: zero-width after sentence marks, never inside dotted dates/number runs nor after 「vs.」
NO_NUMBER_DOT_BREAK = (r"(?!(?<=\d\.)\s{0,8}[\d(])(?<!\d\.\s\d\.)(?<!\d\.\s\d\d\.)"
                       r"(?<![^A-Za-z][Vv][Ss]\.)")
#: sentence-ending whitespace outside dotted runs
SENTENCE_BREAK = re.compile(r"(?<=[.!?。])" + NO_NUMBER_DOT_BREAK + r"\s+")


def _scale(word: str) -> float:
    word = (word or "").replace(" ", "").removesuffix("원")
    return dict(_SCALE).get(word, 1.0)


def _given(request: str) -> set[float]:
    out = set()
    for number, unit in _GIVEN.findall(request or ""):
        try:
            out.add(round(float(number.replace(",", "")) * _scale(unit or ""), 2))
        except ValueError:
            continue
    return out


#: digits the person wrote before a scale word (「4,215만」 → 4215)
_SCALED_DIGITS = re.compile(r"(?<![\d.])(\d[\d,]{0,15}(?:\.\d{1,6})?)\s{0,2}(?:억|천만|백만|만|천)")


def _scaled_digits(request: str) -> set[float]:
    out = set()
    for number in _SCALED_DIGITS.findall(request or ""):
        try:
            out.add(round(float(number.replace(",", "")), 2))
        except ValueError:
            continue
    return out


def _label(scale: float) -> str:
    word = next(w for w, s in _SCALE if s == scale)
    return f"{word} 원" if word else "원"


def fix_unit_labels(text: str, request: str) -> str:
    """Money table unit label fitted to the person's amounts; figures kept."""
    given = _given(request)
    if len(given) < 2 or "|" not in (text or ""):
        return text
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        spots = [k for k in (i - 1, i) if k >= 0 and _LABEL_SPOT.search(lines[k])]
        if spots:
            k = spots[-1]
            m = _LABEL_SPOT.search(lines[k])
            stated = _scale(m.group(1) or m.group(2) or "")
            values = []
            for line in lines[i + 1 : j]:
                for cell in line.strip().strip("|").split("|"):
                    n = _CELL_NUMBER.match(cell.strip())
                    if n:
                        values.append(float(n.group(1).replace(",", "")))

            def fits(scale: float, values: list[float] = values) -> int:
                return sum(1 for v in values if round(v * scale, 2) in given)

            best = max((s for _, s in _SCALE), key=fits)
            if fits(best) >= 2 and fits(best) > fits(stated) and best != stated:
                old = m.group(1) or m.group(2)
                lines[k] = lines[k].replace(old, _label(best), 1)
        i = j
    return "\n".join(lines)


#: 「A ÷ B = C」 in prose or a table row
_EXPRESSION = re.compile(
    r"(?<![\d.,])(?P<a>\d[\d,]{0,15}(?:\.\d{1,6})?)\s{0,2}(?P<op>[÷/×*x])\s{0,2}"
    r"(?P<b>\d[\d,]{0,15}(?:\.\d{1,6})?)(?![\d.,]\d)"
    r"(?:\s{0,3}(?:=|\|)\s{0,3}(?:약\s{0,2})?\**\s{0,2}(?P<c>\d[\d,]{0,15}(?:\.\d{1,6})?))?"
)


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def _like(value: float, template: str) -> str:
    """value formatted like template (commas, significant digits)."""
    digits = len(re.sub(r"[^\d]", "", template).lstrip("0")) or 1
    if value and abs(value) < 1:
        text = f"{value:.{max(digits, 2)}g}"
    elif "." in template:
        places = max(0, digits - len(str(int(abs(value)))))
        text = f"{value:,.{places}f}" if "," in template else f"{value:.{places}f}"
    else:
        text = f"{round(value):,}" if "," in template else str(round(value))
    return text


def _swap(text: str, old: str, new: str) -> str:
    # 「4,215만」 is already scaled
    scaled = r"\s?(?:억|천만|백만|만|천)"
    return re.sub(rf"(?<![\d.,]){re.escape(old)}(?![\d]|[.,]\d|{scaled})", new, text)


def fix_magnitude_slips(text: str, request: str) -> str:
    """Operands a power of ten off the person's amounts reset; results recomputed."""
    given = _given(request)
    if not given or not text:
        return text
    exact = {round(g, 2) for g in given}
    # the person's own digits (「4,215만원」 → 4,215) are in their unit, not slipped
    own = _scaled_digits(request)
    fixes: dict[str, str] = {}
    for m in _EXPRESSION.finditer(text):
        for key in ("a", "b"):
            raw = m.group(key)
            value = _num(raw)
            if value < 1000 or round(value, 2) in exact or round(value, 2) in own or raw in fixes:
                continue
            for g in given:
                if any(abs(value * 10**k - g) < 0.5 or abs(value - g * 10**k) < 0.5
                       for k in (1, 2, 3, 4)):
                    fixes[raw] = f"{round(g):,}" if "," in raw else str(round(g))
                    break
    if not fixes:
        return text
    results: dict[str, str] = {}
    for m in _EXPRESSION.finditer(text):
        a, b, c = m.group("a"), m.group("b"), m.group("c")
        if not c or (a not in fixes and b not in fixes):
            continue
        x, y = _num(fixes.get(a, a)), _num(fixes.get(b, b))
        op = m.group("op")
        if op in "÷/" and not y:
            continue
        value = x / y if op in "÷/" else x * y
        if abs(value - _num(c)) > max(abs(value), abs(_num(c))) * 0.005:
            results[c] = _like(value, c)
    for old, new in fixes.items():
        text = _swap(text, old, new)
    for old, new in results.items():
        if len(re.findall(rf"(?<![\d.,]){re.escape(old)}(?![\d]|[.,]\d)", text)) <= 6:
            text = _swap(text, old, new)
    return text


#: amount with a scale word: 「10,000,000만 원」
_SCALED_WON = re.compile(
    r"(?<![\d.,])(\d[\d,]{0,15}(?:\.\d{1,6})?)\s{0,2}(억|천만|백만|만|천)\s{0,1}원")


def fix_double_scale(text: str, request: str) -> str:
    """Double-scaled amount (「10,000,000만 원」) rewritten as the person wrote it."""
    given = _given(request)
    if not given or not text:
        return text

    def one(m: re.Match) -> str:
        try:
            digits = float(m.group(1).replace(",", ""))
        except ValueError:
            return m.group(0)
        scale = _scale(m.group(2))
        if digits < 10_000 or round(digits * scale, 2) in given or round(digits, 2) not in given:
            return m.group(0)
        shown = digits / scale
        number = (f"{int(shown):,}" if shown == int(shown)
                  else f"{shown:,.2f}".rstrip("0").rstrip("."))
        return f"{number}{m.group(2)} 원"

    return _SCALED_WON.sub(one, text)


#: same scale word twice after digits: 「4,200만만 원」
_REPEATED_SCALE = re.compile(r"(?<=\d)(억|천만|백만|만|천)\1+(?=\s{0,1}원)")
#: scaled amount the person wrote without 원 (「강사비 4,200만 /」)
_SCALED_BARE = re.compile(
    r"(?<![\d.,])(\d[\d,]{0,15}(?:\.\d{1,6})?)\s{0,2}(억|천만|백만|만|천)(?=\s{0,2}(?:[/,)·]|$))",
    re.M)


def restore_material_scale(text: str, material: str) -> str:
    """Amount scaled twice (「45,000,000만 원」, 「4,200만만 원」) in the material's form."""
    if not text or not material:
        return text
    text = _REPEATED_SCALE.sub(r"\1", text)
    extra = []
    for number, word in _SCALED_BARE.findall(material):
        extra.append(f"{number}{word}원")
    return fix_double_scale(text, "\n".join([material, *extra]))


#: Korean amount; 천 matched inside 만/억 groups (else 「1억 8」)
_SCALE_WORD = r"(?:조(?![건사정직치합언각])|천억|억|천만|만(?![큼약일족])|천(?![국재억]))"
_TERM = (
    rf"\d[\d,]*(?:\.\d+)?\s?{_SCALE_WORD}(?:\s?\d[\d,]*(?:\.\d+)?\s?{_SCALE_WORD})*"
    # a bare tail under the last scale word is part of the amount: 「8만 2,080」
    rf"(?:\s?(?:\d{{1,3}},\d{{3}}|\d{{1,4}})(?![\d,.]|\s?{_SCALE_WORD}))?"
    r"|\d[\d,]*(?:\.\d+)?"
)
#: per-unit amount reads as its amount
_UNIT_WORD = (r"(?:\s?(?:원|명|박스|회차|회|개월|개년|개|건|세트|가구|곳|%|배"
              r"|시간|년|장|대|일|주|km|kWh)"
              r"(?:\s?/\s?(?:월|년|연|박스|개|명|회|건|가구|세트|주|일|석|인|곳|사))?)?")
#: a source number after an operand: 「500만 명[2]」
_CITE = r"(?:\s?\[\d{1,3}\])*"
#: short name between operator and amount: 「× 연 비용 118,800원」
_NAMED = r"(?:[가-힣]{1,6}\s){0,2}"
_KO_EXPRESSION = re.compile(
    rf"(?P<lhs>(?:{_TERM}){_UNIT_WORD}{_CITE}"
    rf"(?:\s?[×x*÷]\s?{_NAMED}(?:{_TERM}){_UNIT_WORD}{_CITE}){{1,5}})"
    rf"\s?(?:=|→)\s?(?:약\s?|월\s?|연\s?|연간\s?|총\s?)?"
    rf"(?P<rhs>(?:{_TERM}))(?P<rhs_unit>{_UNIT_WORD})"
)


def _ko_number(raw: str) -> float:
    """「4억 8,000만」 → 480000000; 「3%」 → 0.03; 「1.2조」 → 1.2e12."""
    text = raw.replace(" ", "")
    percent = text.endswith("%")
    # 「8천만」 → 「8000만」
    text = re.sub(
        r"(\d[\d,]{0,20}(?:\.\d{1,15})?)천",
        lambda m: f"{float(m.group(1).replace(',', '')) * 1000:g}",
        text,
    )
    text = re.sub(r"[^\d.,조억만]", "", text)
    total, rest = 0.0, text
    for unit, scale in (("조", 1e12), ("억", 1e8), ("만", 1e4)):
        if unit in rest:
            head, rest = rest.split(unit, 1)
            total += float(head.replace(",", "") or 0) * scale
    if rest.replace(",", ""):
        total += float(rest.replace(",", ""))
    return total / 100 if percent else total


def _operand(term: str) -> float:
    """An operand's value, its name words and source numbers aside (「연 비용 118,800원」,
    「500만 명[2]」)."""
    return _ko_number(re.sub(r"^(?:[가-힣]{1,6}\s){1,2}", "", re.sub(r"\[\d{1,3}\]", "", term)))


def _ko_written(value: float, like: str) -> str:
    """value in like's style (만/억/조 or digits)."""
    if re.search(r"[조억만]", like):
        if value >= 1e12:
            return f"{value / 1e12:,.2f}".rstrip("0").rstrip(".") + "조"
        if value >= 1e8:
            whole, rest = divmod(round(value), 10**8)
            return f"{whole:,}억" + (f" {rest / 1e4:,.0f}만" if rest >= 1e4 else "")
        return f"{value / 1e4:,.2f}".rstrip("0").rstrip(".") + "만"
    if abs(value - round(value)) < 1e-9:
        return f"{round(value):,}" if "," in like or value >= 10000 else str(round(value))
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def slipped_formulas(text: str) -> list[str]:
    """「A × B = D」 with D ≥1000× off: slipped operand unit."""
    found = []
    for m in _KO_EXPRESSION.finditer(text or ""):
        parts = re.split(r"\s?([×x*÷])\s?", m.group("lhs"))
        try:
            value = _operand(parts[0])
            for op, term in zip(parts[1::2], parts[2::2], strict=False):
                number = _operand(term)
                value = value / number if op == "÷" else value * number
            stated = _ko_number(m.group("rhs"))
        except (ValueError, ZeroDivisionError):
            continue
        if stated and value and "%" not in (m.group("rhs_unit") or ""):
            ratio = abs(value / stated)
            if any(abs(ratio - 10**k) < 1e-6 * 10**k or abs(ratio - 10**-k) < 1e-6 * 10**-k
                   for k in range(3, 9)):
                found.append(m.group(0))
    return found


#: an operator before a term, its label words between (「+ 보증 연장 45,000원 × 30대」)
_OPERATOR_BEFORE = re.compile(
    r"(?:[+\-−±×x*÷]\s{0,3}|[+−±×*÷]\s{0,3}(?:[가-힣()]{1,8}\s{1,2}){1,3})$")


def inside_longer_expression(text: str, start: int) -> bool:
    """Whether the expression at `start` is one term of a longer one on its line."""
    line_start = text.rfind("\n", 0, start) + 1
    return bool(_OPERATOR_BEFORE.search(text[max(line_start, start - 30):start]))


#: what may follow a result the code reads whole: not a word run on from its digits (「= 1박당
#: 104,000원」 is not 「1」) and not more digits
_RESULT_CUT = re.compile(r"[가-힣A-Za-z]|\s?\d")


def _cut_result(m: re.Match) -> bool:
    """Whether the result `m` read is only the head of what the line writes after 「=」."""
    return not (m.group("rhs_unit") or "").strip() and bool(
        _RESULT_CUT.match(m.string, m.end("rhs")))


def _cut_expression(m: re.Match) -> bool:
    """Whether the left side `m` read is only the tail of a longer calculation."""
    line_start = m.string.rfind("\n", 0, m.start()) + 1
    return bool(re.search(r"(?:[\d)]|[×x*÷+\-−])\s{0,3}$", m.string[line_start:m.start()]))


def fix_written_arithmetic(text: str) -> str:
    """Wrong 「A × B = D」 results replaced everywhere D repeats; sums/percentages elsewhere.
    A wrong result the next step of a chain starts from is carried into it and that step
    redone (「500만 명 × 118,800원 = 5조 9,400억 원」 then 「5조 9,400억 원 × 30%
    = …」 and 「… × 2% = …」, each ten times over)."""
    if not text or ("×" not in text and "÷" not in text):
        return text
    out, fixes = _fix_products(text)
    for _ in range(4):
        carried = _carry(out, fixes)
        if carried == out:
            break
        out, more = _fix_products(carried)
        fixes.update(more)
    # figure that is also an operand isn't the result
    operands = " ".join(m.group("lhs") for m in _KO_EXPRESSION.finditer(out))
    for wrong, right in fixes.items():
        if re.search(r"[조억만]", wrong) and wrong not in operands:
            # Korean-unit figures only, whole-matched
            out = re.sub(rf"(?<![\d.,]){re.escape(wrong)}(?![\d,.]|\s?[천만억조])", right, out)
    return out


def _carry(text: str, fixes: dict[str, str]) -> str:
    """A chain step whose first operand is a result code corrected above it starts from the
    corrected value."""
    if not fixes:
        return text
    for m in reversed(list(_KO_EXPRESSION.finditer(text))):
        head = re.split(r"\s?[×x*÷]\s?", m.group("lhs"), maxsplit=1)[0]
        for wrong, right in fixes.items():
            if not re.search(r"[조억만]", wrong) or not head.startswith(wrong):
                continue
            if re.match(r"[\d,.]|\s?[천만억조]", head[len(wrong):]):
                continue
            # the corrected result stands before this step
            seen = text.find(right)
            if seen < 0 or seen >= m.start():
                continue
            text = text[: m.start()] + right + text[m.start() + len(wrong):]
            break
    return text


def _fix_products(text: str) -> tuple[str, dict[str, str]]:
    fixes: dict[str, str] = {}

    def work(m: re.Match) -> str:
        lhs, rhs = m.group("lhs"), m.group("rhs")
        if "%" in (m.group("rhs_unit") or ""):
            return m.group(0)
        if _cut_result(m) or _cut_expression(m):
            return m.group(0)
        # product inside a sum is one term; the tail of a longer product is no product
        if inside_longer_expression(m.string, m.start()):
            return m.group(0)
        parts = re.split(r"\s?([×x*÷])\s?", lhs)
        try:
            value = _operand(parts[0])
            for op, term in zip(parts[1::2], parts[2::2], strict=False):
                number = _operand(term)
                value = value / number if op == "÷" else value * number
            stated = _ko_number(rhs)
        except (ValueError, ZeroDivisionError):
            return m.group(0)
        if not stated or abs(value - stated) / max(abs(stated), 1e-9) < 0.005:
            return m.group(0)
        # rounded result to fewer places still right
        if abs(value - stated) / max(abs(stated), 1e-9) < 0.02 and "." in rhs:
            return m.group(0)
        # ≥1000× off: slipped unit, left for checks
        ratio = abs(value / stated)
        if any(abs(ratio - 10**k) < 1e-6 * 10**k or abs(ratio - 10**-k) < 1e-6 * 10**-k
               for k in range(3, 9)):
            return m.group(0)
        # plain-digit left side under 1만 beside 만/억 result: other scale, not wrong
        if re.search(r"[조억만]", rhs) and not re.search(r"[조억만천]", lhs) and abs(value) < 1e4:
            return m.group(0)
        if len(parts) == 3 and parts[1] == "÷" and value and abs(1 / value - stated) \
                / max(abs(stated), 1e-9) < 0.005:
            # 「80만원 ÷ 30만원 = 0.375」: the operands swapped, the result right
            whole, start = m.group(0), m.start()
            lead = whole[: m.start("lhs") - start]
            body = whole[m.start("lhs") - start: m.end("lhs") - start]
            turned = re.sub(r"^(.*?)(\s?÷\s?)(.*)$", r"\3\2\1", body, flags=re.S)
            return lead + turned + whole[m.end("lhs") - start:]
        right = _ko_written(value, rhs)
        # never 「0만」
        if not re.search(r"[1-9]", right):
            return m.group(0)
        fixes[rhs.strip()] = right
        return _swap_rhs(m, right)

    def _swap_rhs(m: re.Match, right: str) -> str:
        whole, start = m.group(0), m.start()
        return whole[: m.start("rhs") - start] + right + whole[m.end("rhs") - start:]

    def keep(m: re.Match) -> str:
        # collect correct results to align restatements
        try:
            stated = _ko_number(m.group("rhs"))
        except ValueError:
            return m.group(0)
        if stated:
            unit = (m.group("rhs_unit") or "").strip()
            results.append((stated, m.group("rhs").strip(), unit))
        return m.group(0)

    results: list[tuple[float, str, str]] = []
    out = _KO_EXPRESSION.sub(work, text)
    _KO_EXPRESSION.sub(keep, out)
    for stated, written, unit in results:
        if not unit:
            continue
        def agree(m: re.Match, stated=stated, written=written, unit=unit) -> str:
            sentence = m.group(0)
            if written not in sentence:
                return sentence
            pattern = rf"(?<![\d.,])({_TERM})\s?{re.escape(unit)}"
            # formula operands aren't restatements
            operands = [(e.start("lhs"), e.end("lhs")) for e in _KO_EXPRESSION.finditer(sentence)]
            for figure in reversed(list(re.finditer(pattern, sentence))):
                if any(a <= figure.start() < b for a, b in operands):
                    continue
                try:
                    value = _ko_number(figure.group(1))
                except ValueError:
                    continue
                if value and value != stated and any(
                    abs(value * 10**k - stated) < 0.5 for k in (-3, -2, -1, 1, 2, 3)
                ):
                    sentence = sentence[: figure.start(1)] + written + sentence[figure.end(1):]
            return sentence
        out = re.sub(r"[^.!?\n]{0,2000}(?:\([^)]{0,500}\)[^.!?\n]{0,2000}){0,20}[.!?]?", agree, out)
    return out, fixes


#: written calc with +/− and parens; short word after 「=」 skipped
_OPERAND = rf"\(?\s?(?:{_TERM}){_UNIT_WORD}{_CITE}\s?\)?"
#: the result may be bold: 「= **477,600원**」
_SUM_EXPRESSION = re.compile(
    rf"(?P<lhs>{_OPERAND}(?:\s?[×x*÷+\-−]\s?{_OPERAND}){{1,6}})"
    rf"\s?=\s?(?:\*\*)?(?:[가-힣]{{1,6}}\s)?(?:\*\*)?(?:약\s?|월\s?|연\s?|총\s?)?"
    rf"(?P<rhs>{_TERM})(?P<rhs_unit>{_UNIT_WORD})"
)
_TOKEN = re.compile(rf"\s*(?:(?P<num>{_TERM}){_UNIT_WORD}{_CITE}|(?P<op>[×x*÷+\-−()]))")
#: the mark a figure code cannot settle gets
CHECK_MARK = "(확인 필요)"
_MARKED_AFTER = re.compile(r"\s?\*{0,2}\s?\((?:확인\s?필요|추정|가정)")


def _evaluate(lhs: str) -> float | None:
    """Value of a written Korean-amount calculation (usual precedence)."""
    tokens: list[object] = []
    pos = 0
    while pos < len(lhs):
        m = _TOKEN.match(lhs, pos)
        if not m or m.end() == pos:
            if lhs[pos:].strip():
                return None
            break
        if m.group("num") is not None:
            try:
                number = _ko_number(m.group("num"))
            except ValueError:
                return None
            # a rate is a hundredth: 「× (1 - 5%)」
            tokens.append(number / 100 if m.group(0)[m.end("num") - m.start():].lstrip()
                          .startswith("%") else number)
        else:
            tokens.append({"x": "×", "*": "×", "−": "-"}.get(m.group("op"), m.group("op")))
        pos = m.end()

    def expr(i):
        value, i = term(i)
        while i < len(tokens) and tokens[i] in ("+", "-"):
            op = tokens[i]
            right, i = term(i + 1)
            value = value + right if op == "+" else value - right
        return value, i

    def term(i):
        value, i = factor(i)
        while i < len(tokens) and tokens[i] in ("×", "÷"):
            op = tokens[i]
            right, i = factor(i + 1)
            value = value * right if op == "×" else value / right
        return value, i

    def factor(i):
        if i >= len(tokens):
            raise ValueError
        if tokens[i] == "(":
            value, i = expr(i + 1)
            if i >= len(tokens) or tokens[i] != ")":
                raise ValueError
            return value, i + 1
        if isinstance(tokens[i], float):
            return tokens[i], i + 1
        raise ValueError

    try:
        value, end = expr(0)
    except (ValueError, ZeroDivisionError, IndexError):
        return None
    return value if end == len(tokens) else None


def _printed_values(printed) -> list[float]:
    """Every amount code printed (a check script's stdout, a calculation block)."""
    out: list[float] = []
    for block in printed or ():
        for m in re.finditer(rf"(?<![\d.,])({_TERM})", str(block or "")):
            try:
                out.append(_ko_number(m.group(1)))
            except ValueError:
                continue
    return out


def _is_printed(value: float, printed: list[float]) -> bool:
    return any(abs(value - p) <= 1e-6 * max(1.0, abs(value)) for p in printed)


#: a mark asked for inside a substitution, set in place once the whole text is known
_PENDING = "\x00mark\x00"


def _place_marks(text: str) -> str:
    """Pending marks set after a closing bold, never twice."""
    while _PENDING in text:
        at = text.index(_PENDING)
        text = _mark_after(text[:at] + text[at + len(_PENDING):], at)
    return text


def _mark_after(text: str, end: int) -> str:
    """`text` with the check mark at `end` (after a bold close), unless one stands there."""
    if _MARKED_AFTER.match(text, end):
        return text
    while text.startswith("**", end):
        end += 2
    return text[:end] + CHECK_MARK + text[end:]


def fix_written_sums(text: str, printed=()) -> str:
    """Wrong sum/difference results replaced; ≥1000× off left as written. A sum written after
    its listed amounts (「운임 119,600원 + … + 숙박비 160,000원으로 합계 329,600원」) and a table's
    total row are recomputed too. A stated result that code printed itself
    (`printed`: script stdout) is marked, never rewritten: the printed run and the written
    operands disagree, and code cannot tell which is meant (every writer, as `code_checked`)."""
    if not text:
        return text
    values = _printed_values(printed)
    fixes: dict[str, str] = {}
    if "=" in text:
        text = _fix_equal_sums(text, values, fixes)
    if "+" in text:
        text = _fix_listed_sums(text, values, fixes)
    if "|" in text:
        text = fix_table_totals(text, printed=values, fixes=fixes)
    return _repeat_totals(text, fixes)


def _fix_equal_sums(text: str, printed: list[float], fixes: dict[str, str]) -> str:
    def work(m: re.Match) -> str:
        lhs = m.group("lhs")
        if not re.search(r"[+\-−]", lhs) or "%" in (m.group("rhs_unit") or ""):
            return m.group(0)
        if _cut_result(m) or _cut_expression(m):
            return m.group(0)
        value = _evaluate(lhs)
        # terms 1000× apart without a product: missing quantity, skip
        added = [_ko_number(t.group("num")) for t in _TOKEN.finditer(lhs) if t.group("num")]
        apart = added and min(added) and max(added) / min(added) >= 1000
        if apart and not re.search(r"[×x*÷]", lhs):
            return m.group(0)
        try:
            stated = _ko_number(m.group("rhs"))
        except ValueError:
            return m.group(0)
        if value is None or not value or not stated:
            return m.group(0)
        # losses written unsigned
        value = abs(value)
        # sums exact: any gap beyond rounding is a slip
        if abs(value - stated) <= 1e-4 * abs(stated):
            return m.group(0)
        ratio = value / stated
        if any(abs(ratio - 10**k) < 1e-6 * 10**k or abs(ratio - 10**-k) < 1e-6 * 10**-k
               for k in range(3, 9)):
            return m.group(0)
        start = m.start()
        if _is_printed(stated, printed):
            return m.group(0) + _PENDING
        right = _ko_written(value, m.group("rhs"))
        if "원" in (m.group("rhs_unit") or ""):
            fixes[m.group("rhs").strip()] = right
        return m.group(0)[: m.start("rhs") - start] + right + m.group(0)[m.end("rhs") - start:]

    return _place_marks(_SUM_EXPRESSION.sub(work, text))


#: an amount's name before it: 「운임 」, 「숙박비(2박) 」
_ADDEND_NAME = r"(?:[가-힣A-Za-z][가-힣A-Za-z()·]{0,11}\s{1,2}(?:[가-힣()]{1,8}\s)?)?"
_WON_TERM = rf"(?:{_TERM})\s?원{_CITE}"
#: listed amounts then their total in words: 「A원 + B원 + C원으로 합계 N원」, 「…, 총 N원」
_LISTED_SUM = re.compile(
    rf"(?P<lhs>{_ADDEND_NAME}{_WON_TERM}(?:\s?\+\s?{_ADDEND_NAME}{_WON_TERM}){{1,9}})"
    r"\s?(?:으로|로|을|를)?\s?(?:모두\s)?(?:더하면|합치면|합하면)?\s?,?\s?"
    r"(?:합계|총액|총합|합산액|총)\s?(?:은|는|이|금액은)?\s?(?:\*\*)?\s?(?:약\s?)?"
    rf"(?P<rhs>{_TERM})(?P<rhs_unit>\s?원)")
#: what before a listed sum makes it the tail of a longer calculation
_LONGER_BEFORE = re.compile(r"(?:[\d)]|[+\-−×x*÷])\s{0,3}$")


def _fix_listed_sums(text: str, printed: list[float], fixes: dict[str, str]) -> str:
    def work(m: re.Match) -> str:
        line_start = m.string.rfind("\n", 0, m.start()) + 1
        if _LONGER_BEFORE.search(m.string[line_start:m.start()]):
            return m.group(0)
        try:
            added = [_ko_number(a.group(1)) for a in re.finditer(
                rf"(?<![\d.,])({_TERM})\s?원", m.group("lhs"))]
            stated = _ko_number(m.group("rhs"))
        except ValueError:
            return m.group(0)
        if len(added) < 2 or not stated or not all(added):
            return m.group(0)
        # amounts 1000× apart: one is a unit price, not an addend
        if max(added) / min(added) >= 1000:
            return m.group(0)
        value = sum(added)
        if abs(value - stated) <= 1e-4 * abs(stated):
            return m.group(0)
        start = m.start()
        if _is_printed(stated, printed) or _power_off(value, stated):
            return m.group(0) + _PENDING
        right = _ko_written(value, m.group("rhs"))
        fixes[m.group("rhs").strip()] = right
        return m.group(0)[: m.start("rhs") - start] + right + m.group(0)[m.end("rhs") - start:]

    return _place_marks(_LISTED_SUM.sub(work, text))


def _power_off(value: float, stated: float) -> bool:
    """Whether `value` and `stated` differ by a power of ten of 1000 or more (a slipped unit,
    not a slipped addition)."""
    if not value or not stated:
        return False
    ratio = abs(value / stated)
    return any(abs(ratio - 10**k) < 1e-6 * 10**k or abs(ratio - 10**-k) < 1e-6 * 10**-k
               for k in range(3, 9))


#: a total row's name: 「합계」, 「**총계**」, 「총액」, 「계」
_TOTAL_ROW = re.compile(r"^\s*\**\s*(?:합\s?계|총\s?계|총\s?합계?|총액|총\s?비용|계|합)\s*\**\s*$")
_SUBTOTAL_ROW = re.compile(r"소\s?계|부분\s?합|subtotal", re.I)
#: a row whose figures do not add into the total: an average, a rate, a change
_NOT_ADDED_ROW = re.compile(r"평균|중앙값|비율|비중|증감|대비|차이|단가|부가세\s?포함|1인당|인당")
#: a column of figures that do not add: rates, unit prices, shares
_NOT_ADDED_COLUMN = re.compile(
    r"단가|비율|비중|율\b|%|평균|인당|증감|대비|순위|번호|연도|기간|일수")
_AMOUNT_CELL = re.compile(
    rf"^\s*(?:\*\*)?\s*(?P<sign>[-−△▲]\s?)?(?P<num>{_TERM})\s?(?P<unit>원|명|건|개|곳|시간|kWh)?"
    r"\s*(?:\*\*)?\s*$")
_EMPTY_CELL = re.compile(r"^\s*(?:-|–|—|\s)?\s*$")


def _cell_amount(cell: str) -> tuple[float, str, str] | None:
    """`(value, number as written, unit)` of a cell holding one amount only."""
    m = _AMOUNT_CELL.match(cell)
    if not m:
        return None
    try:
        value = _ko_number(m.group("num"))
    except ValueError:
        return None
    if m.group("sign"):
        value = -value
    return value, m.group("num"), m.group("unit") or ""


def fix_table_totals(text: str, *, printed=(), fixes: dict[str, str] | None = None) -> str:
    """A table's 「합계」 row set to the sum of the rows above it where those rows are plain
    amounts of one unit (「| **합계** | … | **329,600원** |」 over 119,600 + 75,000 +
    75,000 + 160,000); where the rows are not that plain (a subtotal, a blank, a second total
    below a first), a total that matches no reading is marked 「(확인 필요)」. Rates, averages,
    unit prices and other columns that do not add are left alone."""
    if not text or "|" not in text:
        return text
    printed_values = printed if isinstance(printed, list) else _printed_values(printed)
    fixes = {} if fixes is None else fixes
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].lstrip().startswith("|"):
            j += 1
        _total_block(lines, i, j, printed_values, fixes)
        i = j
    return "\n".join(lines)


def _cells(line: str) -> list[str]:
    return line.strip().strip("|").split("|")


def _total_block(lines: list[str], start: int, end: int, printed: list[float],
                 fixes: dict[str, str]) -> None:
    if end - start < 4:
        return
    header = _cells(lines[start])
    if not re.fullmatch(r"[\s:|-]+", lines[start + 1]):
        return
    rows = list(range(start + 2, end))
    labels = {k: next((c for c in _cells(lines[k]) if c.strip()), "") for k in rows}
    if any(_NOT_ADDED_ROW.search(labels[k]) for k in rows if not _TOTAL_ROW.match(labels[k])):
        return
    totals = [k for k in rows if _TOTAL_ROW.match(labels[k])]
    for t in totals:
        earlier = [k for k in totals if k < t]
        above = [k for k in rows if (earlier[-1] if earlier else start + 1) < k < t]
        cells = _cells(lines[t])
        if len(cells) != len(header):
            continue
        changed = False
        for c, cell in enumerate(cells):
            if c == 0 or _NOT_ADDED_COLUMN.search(header[c]):
                continue
            stated = _cell_amount(cell)
            if stated is None or not stated[0]:
                continue
            plain, subtotal, blank, amounts = True, [], False, []
            for k in above:
                row = _cells(lines[k])
                if len(row) != len(header):
                    plain = False
                    break
                if _EMPTY_CELL.match(row[c]):
                    blank = True
                    continue
                got = _cell_amount(row[c])
                if got is None:
                    plain = False
                    break
                if got[2] != stated[2]:
                    plain = False
                    break
                (subtotal if _SUBTOTAL_ROW.search(labels[k]) else amounts).append(got[0])
            if not plain or len(amounts) + len(subtotal) < 2:
                continue
            readings = [sum(amounts)]
            if subtotal:
                readings.append(sum(subtotal))
            if earlier:
                carried = _cell_amount(_cells(lines[earlier[-1]])[c])
                if carried is not None:
                    readings.append(carried[0] + sum(amounts))
            if any(abs(r - stated[0]) <= 1e-4 * abs(stated[0]) for r in readings):
                continue
            ambiguous = bool(subtotal or blank or earlier)
            if ambiguous or _is_printed(stated[0], printed) or _power_off(readings[0], stated[0]):
                cells[c] = _mark_after(cell.rstrip(), len(cell.rstrip())) + (
                    " " if cell.endswith(" ") else "")
            else:
                right = _ko_written(readings[0], stated[1])
                if readings[0] < 0:
                    continue
                cells[c] = cell.replace(stated[1], right, 1)
                fixes[stated[1].strip()] = right
            changed = True
        if changed:
            lead = lines[t][: len(lines[t]) - len(lines[t].lstrip())]
            lines[t] = lead + "|" + "|".join(cells) + "|"


#: a total's name close before an amount it restates
_TOTAL_BEFORE = re.compile(
    r"(?:합계|총액|총합|합산액|총)\s?(?:은|는|이|금액은)?\s?(?:\*\*)?\s?(?:약\s?)?$")


def _repeat_totals(text: str, fixes: dict[str, str]) -> str:
    """A corrected total restated elsewhere as a total (「합계 329,600원」 in the head
    sentence, the bold cell) takes the corrected value; a conditional total stays (「소명하면 …
    총 N원까지」)."""
    for wrong, right in fixes.items():
        if not re.search(r"\d,\d{3}|[조억만]", wrong):
            continue
        out, last = [], 0
        for m in re.finditer(rf"(?<![\d.,]){re.escape(wrong)}(?=\s?(?:\*\*)?\s?원)", text):
            before = text[max(0, m.start() - 16):m.start()]
            line_start = text.rfind("\n", 0, m.start()) + 1
            sentence = text[max(line_start, text.rfind(". ", line_start, m.start()) + 1):m.end()]
            if _TOTAL_BEFORE.search(before) and not _CONDITION.search(sentence):
                out.append(text[last:m.start()] + right)
                last = m.end()
        if out:
            text = "".join(out) + text[last:]
    return text


#: the writer's own correction line: 「수정: 합계는 429,600원입니다.」
_CORRECTION = re.compile(
    r"^[ \t]{0,4}(?:[-*][ \t]{1,3})?(?:\*\*)?(?:수정|정정|바로잡음)(?:\*\*)?[ \t]{0,2}[:：]"
    r"(?:\*\*)?"
    r"[ \t]{0,2}(?P<body>[^\n]{1,200})\n?", re.M)
#: a total's name before its amount: 「총 409,600원」, 「합계는 429,600원」
_TOTAL_WORD = r"(?:총액|합계|총|합산액)"
_WON = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d{4,})\s{0,1}원")
#: a conditional total stays (「소명하면 총액은 453,600원까지」)
_CONDITION = re.compile(r"경우|소명|(?<=[가-힣])면\s|(?<=[가-힣])시\s|추가|까지")


def _total_stated(text: str, amount: str) -> bool:
    """Whether `text` already names `amount` as a total (the correction only repeats it)."""
    return bool(re.search(_TOTAL_WORD + r"[^\n]{0,40}?" + re.escape(amount) + r"\s{0,1}원", text)
                or re.search(r"=\s{0,2}" + re.escape(amount) + r"\s{0,1}원", text))


def settle_corrections(text: str) -> str:
    """A 「수정: 합계는 N원」 line taken off, its value put in place of the differing total it
    corrects (the unconditioned total named before it; 「총 409,600원」 then
    「수정: 합계는 429,600원입니다.」). A line with no single total amount stays."""
    if not text or not re.search(r"수정|정정|바로잡음", text):
        return text
    for m in reversed(list(_CORRECTION.finditer(text))):
        body = m.group("body")
        amounts = _WON.findall(body)
        if len(amounts) != 1 or not re.search(_TOTAL_WORD, body):
            continue
        right = amounts[0]
        value = float(right.replace(",", ""))
        before, after = text[: m.start()], text[m.end():]
        fixed = before
        for spot in re.finditer(_TOTAL_WORD + r"[^\n.|]{0,24}?" + _WON.pattern, before):
            other = float(spot.group(1).replace(",", ""))
            if other == value or not value or abs(other - value) / value > 0.5:
                continue
            line_start = before.rfind("\n", 0, spot.start()) + 1
            if before[line_start:].lstrip().startswith("|"):
                continue
            sentence_start = max(line_start, before.rfind(". ", line_start, spot.start()) + 1)
            if _CONDITION.search(before[sentence_start: spot.end()]):
                continue
            fixed = before[: spot.start(1)] + right + before[spot.end(1):]
            break
        if fixed == before and not _total_stated(before, right):
            continue
        text = fixed + after
    return re.sub(r"\n{3,}", "\n\n", text)


def mark_slipped_formulas(text: str) -> str:
    """A written 「A × B = D」 whose D is off by 1000× or more is marked 「(확인 필요)」: an
    operand or the result slipped a unit, and code cannot tell which (「25곳 × 1명 ×
    40만 원/석 × 12개월 = 1200억 원」 is 1억 2,000만 원). `fix_written_arithmetic` leaves such a
    formula as written."""
    if not text or ("×" not in text and "÷" not in text):
        return text
    slipped = set(slipped_formulas(text))
    if not slipped:
        return text
    for m in reversed(list(_KO_EXPRESSION.finditer(text))):
        if m.group(0) in slipped and not inside_longer_expression(text, m.start()):
            text = _mark_after(text, m.end())
    return text


#: words that name no quantity
_NO_NAME = frozenset({"원", "약", "기준", "합계", "총", "연", "월", "연간", "경우", "이는", "이를",
                      "계산", "계산하면", "산출", "가정", "적용", "결과", "그리고", "따라서"})


def _lead(text: str, at: int) -> str:
    """What names the figure at `at`: the words before it on its line, and a table row's
    first cell."""
    line_start = text.rfind("\n", 0, at) + 1
    lead = text[max(line_start, at - 40):at]
    line = text[line_start:at]
    if line.lstrip().startswith("|"):
        first = line.strip().strip("|").split("|")[0]
        lead = f"{first} {lead.split('|')[-1]}"
    return lead


def _names(lead: str) -> set[str]:
    words = {re.sub(r"(?:은|는|이|가|을|를|의|과|와|도)$", "", w)
             for w in re.findall(r"[가-힣A-Za-z]{2,}", lead)}
    return {w for w in words if len(w) >= 2 and w not in _NO_NAME} | {
        w for w in re.findall(r"[A-Z]{2,}", lead)}


def _significand(value: float) -> str:
    digits = f"{abs(value):.0f}".rstrip("0")
    return digits


def mark_scaled_restatements(text: str, reference: str = "") -> str:
    """An amount with the same digits as a worked result in the answer (or `reference`, the
    whole document) but a power of ten off is marked 「(확인 필요)」 (「하향식 SOM은
    175억 2천만 원」 in the summary, 「1조 7,520억 원」 in a table, over the body's 「8조 7,600억
    원 × 2% = 1,752억 원」). Only results of three significant digits or more count, so 「1억」
    and 「10억」 never read as one figure; the restatement is marked, not rewritten: code
    knows the result, not that the restatement means it."""
    if not text:
        return text
    whole = f"{text}\n{reference}"
    results: dict[str, tuple[float, set[str]]] = {}
    for m in [*_KO_EXPRESSION.finditer(whole), *_SUM_EXPRESSION.finditer(whole)]:
        if "%" in (m.group("rhs_unit") or ""):
            continue
        try:
            value = _ko_number(m.group("rhs"))
        except ValueError:
            continue
        digits = _significand(value)
        if value >= 1e4 and len(digits) >= 3:
            names = results.setdefault(digits, (value, set()))[1]
            names |= _names(_lead(whole, m.start()))
    if not results:
        return text
    # operands and results of the answer's own formulas are not restatements
    inside = [(m.start(), m.end()) for m in
              [*_KO_EXPRESSION.finditer(text), *_SUM_EXPRESSION.finditer(text)]]
    spots = []
    for m in re.finditer(rf"(?<![\d.,])(?P<num>{_TERM})\s?원", text):
        if not re.search(r"[조억만,]", m.group("num")):
            continue
        if any(a <= m.start() < b for a, b in inside):
            continue
        try:
            value = _ko_number(m.group("num"))
        except ValueError:
            continue
        known = results.get(_significand(value))
        if known is None or abs(value - known[0]) <= 1e-6 * known[0]:
            continue
        ratio = value / known[0]
        if not any(abs(ratio - 10**k) < 1e-6 * 10**k or abs(ratio - 10**-k) < 1e-6 * 10**-k
                   for k in range(1, 5)):
            continue
        # the same quantity by name: 「SOM」 before both (a ten-year total of a yearly fee is
        # its own figure)
        if _names(_lead(text, m.start())) & known[1]:
            spots.append(m.end())
    for end in reversed(spots):
        text = _mark_after(text, end)
    return text


#: a period word before an amount
_PERIOD = r"(?P<period>연간?|월|매월|한 달|1년)?\s?"
_REVENUE = re.compile(
    rf"(?:(?P<pre>연간?|월|매월)\s?)?매출(?:액)?(?:은|이|을)?\s?(?:약\s?)?{_PERIOD}(?:약\s?)?(?P<num>{_TERM})\s?원")
_COST_NAMES = (r"원재료비|재료비|원가|임대료|임차료|월세|인건비|급여|관리비|수수료|이자|공공요금"
               r"|광고비|마케팅비|운영비|감가상각비|물류비|배송비")
_COST = re.compile(
    rf"(?P<name>{_COST_NAMES})(?:를|을|은|는|이|가|로|으로)?\s?(?:약\s?)?{_PERIOD}(?:약\s?)?"
    rf"(?P<num>{_TERM})\s?원")
_PROFIT = re.compile(
    rf"(?P<name>(?:세전\s?|세후\s?)?(?:영업\s?이익|순\s?이익|이익|순수익))(?![률율])(?:은|는|이)?"
    rf"\s?(?:약\s?)?{_PERIOD}(?:약\s?)?(?P<num>{_TERM})\s?원")


def _yearly(m: re.Match, default: str) -> float:
    period = (m.group("period") or default or "").strip()
    value = _ko_number(m.group("num"))
    return value * 12 if period in ("월", "매월", "한 달") else value


def mark_profit_contradictions(text: str) -> str:
    """A stated profit above what the paragraph's own revenue less its named costs leaves is
    marked with that arithmetic (「연매출 4억 원에서 원재료비 1억 4,000만 원과 임대료
    7,200만 원을 뺀 … 인건비를 월 1,800만 원으로 잡으면 세전 영업이익은 연 1억 원 안팎」, though
    4억 − 1억 4,000만 − 7,200만 − 2억 1,600만 = −2,800만 원). Costs the paragraph does not name
    only lower the profit, so only a profit above the bound is a contradiction; the sentence
    is never rewritten. One revenue and one profit per paragraph, else nothing is read."""
    if not text or "이익" not in text or "매출" not in text:
        return text
    out = []
    for para in re.split(r"(\n\s*\n)", text):
        out.append(_profit_mark(para))
    return "".join(out)


def _profit_mark(para: str) -> str:
    if para.lstrip().startswith("|"):
        return para
    revenues = list(_REVENUE.finditer(para))
    profits = [m for m in _PROFIT.finditer(para)]
    if len(revenues) != 1 or len(profits) != 1:
        return para
    revenue, profit = revenues[0], profits[0]
    # the profit is stated after the figures it comes from
    if profit.start() < revenue.end():
        return para
    try:
        base = (revenue.group("period") or revenue.group("pre") or "연").strip()
        default = "월" if base in ("월", "매월", "한 달") else "연"
        income = _yearly(revenue, default)
        costs: dict[str, tuple[float, str]] = {}
        for m in _COST.finditer(para, revenue.end(), profit.start()):
            # 「(직원 1인 평균 300만 원)」: a per-head figure inside brackets is no cost line
            if para.count("(", 0, m.start()) > para.count(")", 0, m.start()):
                continue
            costs.setdefault(m.group("name"), (_yearly(m, default), m.group(0)))
        stated = _yearly(profit, default)
    except ValueError:
        return para
    if not costs or not income or not stated:
        return para
    left = income - sum(v for v, _ in costs.values())
    if stated <= left + 0.05 * income:
        return para
    if default == "월":
        left /= 12
    parts = " − ".join(_ko_written(v / (12 if default == "월" else 1), "만").replace(" ", " ")
                       for v, _ in costs.values())
    shown = (f"{_ko_written(abs(left), '만')}" if left >= 0
             else f"−{_ko_written(abs(left), '만')}")
    note = (f"(확인 필요: {_ko_written(income / (12 if default == '월' else 1), '만')} − {parts} "
            f"= {shown} 원)")
    end = profit.end()
    if _MARKED_AFTER.match(para, end):
        return para
    return para[:end] + note + para[end:]


def _known(value: float, known: list[float]) -> bool:
    return any(abs(value - k) <= 0.005 * max(abs(value), 1e-9) for k in known)


def _constant(token: re.Match) -> bool:
    """A bare small count in a formula (12 months, 80 people): no amount to source."""
    whole = token.group(0)
    return (not re.search(r"[조억만천%원]", whole)
            and _operand(token.group("num")) < 1000)


def label_estimates(text: str, known_text: str) -> str:
    """Each worked result in `text` that is none of `known_text`'s figures (the person's
    material and what code printed) gets 「(추정)」 after it: on a turn whose code calculation
    did not finish, those are the writer's own arithmetic, not code's. A result already marked
    (「(추정)」, 「(확인 필요)」, 「(가정)」) stays as it is."""
    if not text or "=" not in text:
        return text
    known = _printed_values([known_text])
    spots = []
    for m in [*_KO_EXPRESSION.finditer(text), *_SUM_EXPRESSION.finditer(text)]:
        if _cut_result(m) or inside_longer_expression(text, m.start()):
            continue
        try:
            value = _ko_number(m.group("rhs"))
        except ValueError:
            continue
        # amounts only: a count summed (「47+38=75」) is no estimate
        if not ("원" in (m.group("rhs_unit") or "") or re.search(r"[조억만]", m.group("rhs"))):
            continue
        if not value or _known(value, known):
            continue
        # a result worked from known figures only is arithmetic code has read, no estimate
        # (「80 × 6,000 × 12 = 5,760,000원」 on the given head count and prices)
        if all(_constant(t) or _known(abs(_operand(t.group("num"))), known)
               for t in _TOKEN.finditer(m.group("lhs")) if t.group("num")):
            continue
        spots.append(m.end())
    for end in sorted(set(spots), reverse=True):
        if _MARKED_AFTER.match(text, end):
            continue
        while text.startswith("**", end):
            end += 2
        text = text[:end] + "(추정)" + text[end:]
    return text


#: an assumption's basis claiming the person's company: 「가정(사내)」, 「사내 가정」
_COMPANY_CLAIM = re.compile(
    r"가정\s?\(\s?(?:사내|회사|당사|내부)\s?\)|(?:사내|회사|당사)\s?가정(?:값|치)?")
_VALUED = re.compile(rf"(?<![\d.,])(?P<num>{_TERM})\s?(?P<unit>%|원|명|곳|개|건)?")


def _valued(text: str) -> list[tuple[float, str]]:
    out = []
    for m in _VALUED.finditer(text or ""):
        try:
            out.append((_ko_number(m.group("num")), m.group("unit") or ""))
        except ValueError:
            continue
    return out


def label_assumptions(text: str, person: str) -> str:
    """A table row whose basis says the person's company set it (「가정(사내)」) while none of
    its figures is in what the person gave is relabelled 「가정(본 검토 설정)」. A row with one
    given figure (「118,800원 (9,900 × 12)」) stays."""
    if not text or "|" not in text or not _COMPANY_CLAIM.search(text):
        return text
    given = _valued(person)
    lines = text.split("\n")
    for k, line in enumerate(lines):
        if not line.lstrip().startswith("|") or not _COMPANY_CLAIM.search(line):
            continue
        cells = _cells(line)
        basis = [c for c, cell in enumerate(cells) if _COMPANY_CLAIM.search(cell)]
        values = [v for c, cell in enumerate(cells) if c and c not in basis
                  for v in _valued(cell)]
        if not values:
            continue
        if any(abs(v - g) <= 1e-9 * max(1.0, abs(v)) and (u == gu or not u or not gu)
               for v, u in values for g, gu in given):
            continue
        for c in basis:
            cells[c] = _COMPANY_CLAIM.sub(
                lambda m: "가정(본 검토 설정)" if m.group(0).startswith("가정") else "본 검토 가정",
                cells[c])
        lead = line[: len(line) - len(line.lstrip())]
        lines[k] = lead + "|" + "|".join(cells) + "|"
    return "\n".join(lines)
