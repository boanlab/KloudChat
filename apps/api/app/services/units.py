"""Korean money amounts: parsing, table unit labels, and re-working written calculations."""

from __future__ import annotations

import re

#: Unit words and their size in won, longest first so 「백만」 wins over 「만」.
_SCALE = (("천만", 1e7), ("백만", 1e6), ("억", 1e8), ("만", 1e4), ("천", 1e3), ("", 1.0))
_GIVEN = re.compile(r"(?<![\d.])(\d[\d,]{0,15}(?:\.\d{1,6})?)\s{0,2}(억|천만|백만|만|천)?\s{0,1}원")
_LABEL_SPOT = re.compile(
    r"[(（]\s{0,2}(?:단위\s{0,2}[:：]\s{0,2})?((?:천만|백만|억|만|천)?\s{0,1}원)\s{0,2}[)）]"
    r"|단위\s{0,2}[:：]\s{0,2}((?:천만|백만|억|만|천)?\s{0,1}원)"
)
_CELL_NUMBER = re.compile(r"^\**\s{0,2}(-?\d[\d,]{0,15}(?:\.\d{1,6})?)\s{0,2}\**$")


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


def _label(scale: float) -> str:
    word = next(w for w, s in _SCALE if s == scale)
    return f"{word} 원" if word else "원"


def fix_unit_labels(text: str, request: str) -> str:
    """A money table's unit label set to the unit the person's amounts fit; figures kept."""
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


#: 「40,500,000 ÷ 3,000,000 = 13.5」 or a table row 「| 40,500,000 ÷ 3,000,000 | 13.5 |」.
_EXPRESSION = re.compile(
    r"(?<![\d.,])(?P<a>\d[\d,]{0,15}(?:\.\d{1,6})?)\s{0,2}(?P<op>[÷/×*x])\s{0,2}"
    r"(?P<b>\d[\d,]{0,15}(?:\.\d{1,6})?)(?![\d.,]\d)"
    r"(?:\s{0,3}(?:=|\|)\s{0,3}(?:약\s{0,2})?\**\s{0,2}(?P<c>\d[\d,]{0,15}(?:\.\d{1,6})?))?"
)


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def _like(value: float, template: str) -> str:
    """`value` written like `template`: same thousands commas and significant digits."""
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
    return re.sub(rf"(?<![\d.,]){re.escape(old)}(?![\d]|[.,]\d)", new, text)


def fix_magnitude_slips(text: str, request: str) -> str:
    """Operands a power of ten off one of the person's amounts are reset; results recomputed."""
    given = _given(request)
    if not given or not text:
        return text
    exact = {round(g, 2) for g in given}
    fixes: dict[str, str] = {}
    for m in _EXPRESSION.finditer(text):
        for key in ("a", "b"):
            raw = m.group(key)
            value = _num(raw)
            if value < 1000 or round(value, 2) in exact or raw in fixes:
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


#: A Korean amount: 「1억 8천만」, 「3,000만」, 「5천」, 「1.2조」, or a bare number.
#: 「천」 must be matched inside a 만/억 group, or 「1억 8천만」 reads as 「1억 8」.
_TERM = (
    r"\d[\d,]*(?:\.\d+)?\s?(?:조|억|천만|만|천)(?:\s?\d[\d,]*(?:\.\d+)?\s?(?:억|천만|만|천))*"
    r"|\d[\d,]*(?:\.\d+)?"
)
#: 「원/박스」, 「박스/월」: a per-unit amount reads as its amount.
_UNIT_WORD = (r"(?:\s?(?:원|명|박스|회차|회|개월|개|건|세트|가구|%|배)"
              r"(?:\s?/\s?(?:월|년|연|박스|개|명|회|건|가구|세트|주|일))?)?")
#: A short name between an operator and its amount: 「× 마진 12,000원」.
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
    # 「8천만」 → 「8000만」, 「5천」 → 「5000」: a thousand of the digit before it.
    text = re.sub(
        r"(\d[\d,]*(?:\.\d+)?)천",
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
    """`value` in the style of `like`: Korean 만/억/조 when it used them, else digits."""
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
    """Written 「A × B = D」 whose D is a thousandfold or more off: an operand's unit slipped."""
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


def fix_written_arithmetic(text: str) -> str:
    """Wrong results of written products 「A × B = D」 replaced, here and where D repeats.

    Sums, differences and percentage results are left to other passes."""
    if not text or ("×" not in text and "÷" not in text):
        return text
    fixes: dict[str, str] = {}

    def work(m: re.Match) -> str:
        lhs, rhs = m.group("lhs"), m.group("rhs")
        if "%" in (m.group("rhs_unit") or ""):
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
        # A rounded result written to fewer places is still right (「≈ 3.3」 for 3.33).
        if abs(value - stated) / max(abs(stated), 1e-9) < 0.02 and "." in rhs:
            return m.group(0)
        # A thousandfold or more off is a slipped operand unit; left for the checks to report.
        ratio = abs(value / stated)
        if any(abs(ratio - 10**k) < 1e-6 * 10**k or abs(ratio - 10**-k) < 1e-6 * 10**-k
               for k in range(3, 9)):
            return m.group(0)
        right = _ko_written(value, rhs)
        fixes[rhs.strip()] = right
        return _swap_rhs(m, right)

    def _swap_rhs(m: re.Match, right: str) -> str:
        whole, start = m.group(0), m.start()
        return whole[: m.start("rhs") - start] + right + whole[m.end("rhs") - start:]

    def keep(m: re.Match) -> str:
        # Collect correct results so restatements a power of ten off can be made to agree.
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
            # A formula's own operands are not restatements of its result.
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
        out = re.sub(r"[^.!?\n]*(?:\([^)]*\)[^.!?\n]*)*[.!?]?", agree, out)
    # A figure that is also an operand somewhere (「4,000만원 × 2회」) is not the result.
    operands = " ".join(m.group("lhs") for m in _KO_EXPRESSION.finditer(out))
    for wrong, right in fixes.items():
        if re.search(r"[조억만]", wrong) and wrong not in operands:
            # Korean-unit figures only, matched whole (not 「1억 8」 inside 「1억 8,000만」).
            out = re.sub(rf"(?<![\d.,]){re.escape(wrong)}(?![\d,.]|\s?[천만억조])", right, out)
    return out


#: A written calculation with sums or differences and parentheses, and its result;
#: a short word between 「=」 and the result (「영업이익」) is skipped.
_OPERAND = rf"\(?\s?(?:{_TERM}){_UNIT_WORD}\s?\)?"
_SUM_EXPRESSION = re.compile(
    rf"(?P<lhs>{_OPERAND}(?:\s?[×x*÷+\-−]\s?{_OPERAND}){{1,6}})"
    rf"\s?=\s?(?:[가-힣]{{1,6}}\s)?(?:약\s?|월\s?|연\s?|총\s?)?"
    rf"(?P<rhs>{_TERM})(?P<rhs_unit>{_UNIT_WORD})"
)
_TOKEN = re.compile(rf"\s*(?:(?P<num>{_TERM}){_UNIT_WORD}|(?P<op>[×x*÷+\-−()]))")


def _evaluate(lhs: str) -> float | None:
    """The value of a written calculation in Korean amounts, by the usual precedence."""
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
    """Wrong results of written sums and differences replaced.

    A result a thousandfold or more off is a slipped operand and is left as written."""
    if not text or "=" not in text:
        return text

    def work(m: re.Match) -> str:
        lhs = m.group("lhs")
        if not re.search(r"[+\-−]", lhs) or "%" in (m.group("rhs_unit") or ""):
            return m.group(0)
        value = _evaluate(lhs)
        # Terms a thousandfold apart with no product are missing a quantity; skipped.
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
        # A loss is written as its size, so compare without sign.
        value = abs(value)
        # Sums are exact; any gap beyond rounding noise is a slip.
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
