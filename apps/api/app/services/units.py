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

#: zero-width after sentence marks, never inside dotted dates/number runs
NO_NUMBER_DOT_BREAK = r"(?!(?<=\d\.)\s{0,8}[\d(])(?<!\d\.\s\d\.)(?<!\d\.\s\d\d\.)"
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
_TERM = (
    r"\d[\d,]*(?:\.\d+)?\s?(?:조|억|천만|만|천)(?:\s?\d[\d,]*(?:\.\d+)?\s?(?:억|천만|만|천))*"
    r"|\d[\d,]*(?:\.\d+)?"
)
#: per-unit amount reads as its amount
_UNIT_WORD = (r"(?:\s?(?:원|명|박스|회차|회|개월|개년|개|건|세트|가구|%|배"
              r"|시간|년|장|대|일|주|km|kWh)"
              r"(?:\s?/\s?(?:월|년|연|박스|개|명|회|건|가구|세트|주|일))?)?")
#: short name between operator and amount
_NAMED = r"(?:[가-힣]{1,6}\s)?"
_KO_EXPRESSION = re.compile(
    rf"(?P<lhs>(?:{_TERM}){_UNIT_WORD}(?:\s?[×x*÷]\s?{_NAMED}(?:{_TERM}){_UNIT_WORD}){{1,5}})"
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
            value = _ko_number(parts[0])
            for op, term in zip(parts[1::2], parts[2::2], strict=False):
                number = _ko_number(re.sub(r"^[가-힣]{1,6}\s", "", term))
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


def fix_written_arithmetic(text: str) -> str:
    """Wrong 「A × B = D」 results replaced everywhere D repeats; sums/percentages elsewhere."""
    if not text or ("×" not in text and "÷" not in text):
        return text
    fixes: dict[str, str] = {}

    def work(m: re.Match) -> str:
        lhs, rhs = m.group("lhs"), m.group("rhs")
        if "%" in (m.group("rhs_unit") or ""):
            return m.group(0)
        # product inside a sum is one term; the tail of a longer product is no product
        if inside_longer_expression(m.string, m.start()):
            return m.group(0)
        parts = re.split(r"\s?([×x*÷])\s?", lhs)
        try:
            value = _ko_number(parts[0])
            for op, term in zip(parts[1::2], parts[2::2], strict=False):
                number = _ko_number(re.sub(r"^[가-힣]{1,6}\s", "", term))
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
    # figure that is also an operand isn't the result
    operands = " ".join(m.group("lhs") for m in _KO_EXPRESSION.finditer(out))
    for wrong, right in fixes.items():
        if re.search(r"[조억만]", wrong) and wrong not in operands:
            # Korean-unit figures only, whole-matched
            out = re.sub(rf"(?<![\d.,]){re.escape(wrong)}(?![\d,.]|\s?[천만억조])", right, out)
    return out


#: written calc with +/− and parens; short word after 「=」 skipped
_OPERAND = rf"\(?\s?(?:{_TERM}){_UNIT_WORD}\s?\)?"
_SUM_EXPRESSION = re.compile(
    rf"(?P<lhs>{_OPERAND}(?:\s?[×x*÷+\-−]\s?{_OPERAND}){{1,6}})"
    rf"\s?=\s?(?:[가-힣]{{1,6}}\s)?(?:약\s?|월\s?|연\s?|총\s?)?"
    rf"(?P<rhs>{_TERM})(?P<rhs_unit>{_UNIT_WORD})"
)
_TOKEN = re.compile(rf"\s*(?:(?P<num>{_TERM}){_UNIT_WORD}|(?P<op>[×x*÷+\-−()]))")


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
                tokens.append(_ko_number(m.group("num")))
            except ValueError:
                return None
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


def fix_written_sums(text: str) -> str:
    """Wrong sum/difference results replaced; ≥1000× off left as written."""
    if not text or "=" not in text:
        return text

    def work(m: re.Match) -> str:
        lhs = m.group("lhs")
        if not re.search(r"[+\-−]", lhs) or "%" in (m.group("rhs_unit") or ""):
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
        right = _ko_written(value, m.group("rhs"))
        start = m.start()
        return m.group(0)[: m.start("rhs") - start] + right + m.group(0)[m.end("rhs") - start:]

    return _SUM_EXPRESSION.sub(work, text)
