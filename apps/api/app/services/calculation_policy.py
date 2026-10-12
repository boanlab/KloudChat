"""Conservative request-only cues for a required arithmetic tool call."""

from __future__ import annotations

import ast
import re
import unicodedata

_MAX_REQUEST_CHARS = 16_384
_MAX_EXPRESSION_CHARS = 1_024
_NUMBER = re.compile(r"(?<![\w.])[+-]?(?:\d+(?:\.\d+)?|\.\d+)", re.ASCII)
_FENCE = re.compile(r"```[\s\S]*?(?:```|\Z)")
_QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’"}
_QUOTE_ENDS = frozenset(_QUOTE_PAIRS.values())
_CLAUSE = re.compile(
    r"\n|[;!?]+|\.(?!\d)|\b(?:then|also|and)\b|그리고|또한|그다음|(?<=[고며]),?\s+",
    re.IGNORECASE,
)
_NEW_TASK = re.compile(r"(?:then|also|and|그리고|또한|그다음)\Z", re.IGNORECASE)
_TRANSFORM = re.compile(
    r"번역|그대로\s*출력|서식|쉼표로\s*연결|\b(?:translate|translation|paraphrase|format)\b",
    re.IGNORECASE,
)
_PROGRAM = re.compile(
    r"코드|파이썬|함수|프로그램|계산기|\b(?:code|python|function|script|ui|html|calculator)\b",
    re.IGNORECASE,
)
_BUILD = re.compile(r"만들|작성|구현|개발|\b(?:write|build|create|generate|implement)\b", re.I)
_CODE = re.compile(r"\b(?:function\s+\w+\s*\(|def\s+\w+\s*\(|return\s+)", re.I)
_DECLINE = re.compile(
    r"(?:계산|검산)(?:을|은|는|도)?\s*하지(?:는|도)?\s*(?:마|말)|"
    # 「계산 안 함」 asks nothing
    r"(?:계산|검산)(?:을|은|는|도)?\s{0,3}(?:안|못)\s{0,3}(?:함|했|하였|됨|됐)|"
    r"(?:계산|검산)(?:을|은|는|도)?\s{0,3}하지\s{0,3}(?:않|못)(?:았|음|았음)|"
    r"\b(?:do\s+not|don['’]t|never)\s+(?:calculate|compute|evaluate|add|subtract|multiply|divide)\b",
    re.IGNORECASE,
)
_MISSING = re.compile(
    r"(?:인원|조건|값|표|자료|정보|데이터|수치)[^.!?\n]{0,30}"
    r"(?:없[다어는음]|모르|부족|누락|미제공)|"
    r"\b(?:data|value|input|condition|size|table|number)[^.!?\n]{0,30}"
    r"\b(?:missing|unknown|unavailable|not\s+(?:provided|given|known))\b",
    re.IGNORECASE,
)
_CALCULATE = re.compile(r"계산|검산|산출|\b(?:calculate|compute|evaluate)\b", re.IGNORECASE)
#: writing length limits (text-fit's job, not arithmetic)
_LENGTH_LIMIT = re.compile(
    r"\d[\d,]{0,6}\s{0,2}(?:자|글자|바이트|bytes?)\s{0,2}(?:이내|이하|내외|제한|미만|까지|안으로)"
    r"|(?:공백|띄어쓰기)\s{0,2}(?:포함|제외)\s{0,2}\d[\d,]{0,6}\s{0,2}(?:자|글자|바이트)",
    re.IGNORECASE,
)
_WRITING = re.compile(r"자기\s{0,2}소개서|자소서|문항|써\s{0,2}줘|작성|다듬|고쳐", re.IGNORECASE)
#: arithmetic ask, not a note that something was computed
_CALCULATE_ASK = re.compile(
    r"계산\s{0,2}(?:해|하여|하고|해서|좀|부탁)|검산\s{0,2}(?:해|좀)|구해|얼마|몇\s{0,2}(?:%|퍼센트|원|명|개)"
    r"|\b(?:calculate|compute)\b",
    re.IGNORECASE,
)
#: correction ("10분이 아니라 20분", "10 → 20"): one operand
_CORRECTION = re.compile(
    r"[+-]?\d+(?:\.\d+)?\s*[^\s\d,.;:]{0,6}\s*"
    r"(?:(?:이|가)?\s*아니라|아닌|→|->|⇒|대신에?|instead of)\s*"
    r"|\bnot\s+[+-]?\d+(?:\.\d+)?\s*[^\s\d,.;:]{0,6}\s*(?:but|,)\s*",
    re.IGNORECASE,
)
_OPERAND = r"(?<![0-9A-Za-z_.])[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?![\d.])"
_QUANTITY = rf"{_OPERAND}\s*(?:원|점|명|개)?"
_ARITHMETIC_VERB = r"(?:더해|더하|빼\s*줘|빼면|곱하|곱해|곱하면|나누|나눠)"
_ARITHMETIC_ACTION = re.compile(
    rf"{_QUANTITY}\s*(?:와|과|에|에서|을|를)\s*{_QUANTITY}\s*(?:을|를|으로|로)?\s*"
    rf"{_ARITHMETIC_VERB}|"
    rf"\badd\s+(?:the\s+numbers?\s+)?{_OPERAND}"
    rf"(?:\s+(?:and|to|plus)\s+|\s*,\s*|\s+){_OPERAND}|"
    rf"\bsubtract\s+{_OPERAND}\s+from\s+{_OPERAND}|"
    rf"\b(?:multiply|divide)\s+{_OPERAND}\s+by\s+{_OPERAND}|"
    rf"{_OPERAND}\s+times\s+{_OPERAND}",
    re.IGNORECASE,
)
_NEGATED_ARITHMETIC = re.compile(r"^\s*(?:주)?지(?:는|도)?\s*(?:마|말|않)")
_REPLACEMENT_ARITHMETIC = re.compile(
    rf"^\s*(?:주)?지(?:는|도)?\s*(?:말고|않고)\s*(?:대신\s*)?{_ARITHMETIC_VERB}"
)
_EDITING_PREFIX = re.compile(r"(?:문단|예시|항목|제목|섹션|그룹|문서)\s*$")
_EDITING_SUFFIX = re.compile(r"^\s*(?:examples?|sections?|paragraphs?|items?|groups?)\b", re.I)
_TARGET = re.compile(
    r"가중\s*평균|평균|증가율|감소율|증감률|변화율|백분율|퍼센트|%|합계|총액|총합|"
    r"\b(?:mean|average|total|sum|percentage|percent|increase|decrease|growth)\b",
    re.IGNORECASE,
)
_ASK = re.compile(
    r"구해|구하|얼마|알려|계산|검산|산출|(?:평균|증가율|감소율|합계)[은는]?\s*[?？]|"
    r"\b(?:find|determine|calculate|compute|what\s+is|how\s+much|how\s+many)\b",
    re.IGNORECASE,
)
_CONVERT = re.compile(r"환산|변환|바꿔|\bconvert\b", re.IGNORECASE)
_REPRESENTATION_CONVERSION = re.compile(
    r"(?<![\w])(?:JSON|CSV|YAML|Markdown|마크다운|문자열|텍스트|배열|표)"
    r"(?:\s*(?:문자열|배열|형식))*\s*(?:으로|로)\s*(?:변환|바꿔)",
    re.IGNORECASE,
)
_UNIT = re.compile(
    r"킬로미터|센티미터|밀리미터|미터|킬로그램|밀리그램|그램|밀리리터|리터|"
    r"시간|분|초|(?<![a-z])(?:km/h|m/s|mm|cm|km|mg|kg|ml|ms|min|kb|mb|gb|m|g|l|s|h)(?![a-z])",
    re.IGNORECASE,
)
_PERCENT_CONVERSION = re.compile(r"백분율|퍼센트|소수|%|\b(?:percentage|percent|decimal)\b", re.I)
_DATE = re.compile(r"\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?\Z")
_EXPRESSION_CHARS = re.compile(r"[0-9.()+\-*/\s]+\Z")
_EXPRESSION_PREFIX = re.compile(r"^(?:what\s+is|calculate|compute|evaluate)\s+", re.IGNORECASE)
_EXPRESSION_SUFFIX = re.compile(
    r"(?:[은는]?\s*얼마(?:야|인가|인가요)?|[을를]?\s*(?:계산|검산)해\s*줘)?[?.=\s]*$"
)
_DIRECT_LITERAL = re.compile(r"[0-9.()+\-*/ \t]+")
_DIRECT_CORE_SUFFIX = re.compile(
    r"(?:[은는]?\s*얼마(?:야|인가|인가요)?|"
    r"[을를]?\s*(?:계산|검산)해\s*(?:주세요|줘))?"
)
_DIRECT_FORMAT = re.compile(
    r"(?:(?:(?:계산식|식)(?:과|와)\s*(?:답|결과)만|(?:답|정답|결과)만)"
    r"\s*(?:짧게|간단히|간결하게)?\s*(?:(?:써|알려|보여|답해)\s*(?:주세요|줘))?|"
    r"(?:짧게|간단히|간결하게)\s*(?:답해|설명해|알려|써)\s*(?:주세요|줘)|"
    r"(?:just|only)\s+(?:the\s+)?(?:answer|result|expression\s+and\s+(?:the\s+)?answer)|"
    r"(?:answer|respond)\s+(?:briefly|concisely|only))",
    re.IGNORECASE,
)
_DIRECT_NO_FILE = re.compile(
    r"(?:(?:파일|아티팩트|문서)(?:은|는|을|를)?\s*(?:만들지|생성하지)\s*"
    r"(?:마세요|마|말아\s*(?:주세요|줘))|"
    r"(?:do\s+not|don't|never)\s+(?:create|make|generate)\s+(?:any\s+)?"
    r"(?:files?|artifacts?|documents?)|no\s+(?:files?|artifacts?|documents?))",
    re.IGNORECASE,
)
_FOLLOWUP_NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
_PRIOR_KR = r"(?:그|이전|앞의|방금)\s*(?:결과|값|합계|답)"
_PRIOR_EN = r"(?:(?:the\s+)?(?:previous|last|prior)|that)\s+(?:result|answer|total|value)"
_FOLLOWUP_CORE = re.compile(
    rf"(?:(?:그럼|그러면)\s*|(?:then|next),?\s+)?(?:please\s+)?(?:"
    rf"{_PRIOR_KR}(?:에|에서|을|를)\s*{_FOLLOWUP_NUMBER}\s*(?:을|를|으로|로)?\s*"
    r"(?:더해|빼|곱해|나눠|나누어)\s*(?:주세요|줘)|"
    rf"{_PRIOR_KR}의\s*{_FOLLOWUP_NUMBER}\s*(?:%|퍼센트)(?:은|는)?\s*"
    r"(?:얼마(?:야|인가요)?|구해\s*(?:주세요|줘))|"
    rf"add\s+{_FOLLOWUP_NUMBER}\s+to\s+{_PRIOR_EN}|"
    rf"subtract\s+{_FOLLOWUP_NUMBER}\s+from\s+{_PRIOR_EN}|"
    rf"(?:multiply|divide)\s+{_PRIOR_EN}\s+by\s+{_FOLLOWUP_NUMBER}|"
    rf"what\s+is\s+{_FOLLOWUP_NUMBER}\s*(?:%|percent)\s+of\s+{_PRIOR_EN})",
    re.I,
)
_PLAIN_NUMBER = re.compile(rf"{_FOLLOWUP_NUMBER}\Z")


def _mask_quoted_intent(text: str) -> str:
        # first closing quote per opener, one reverse pass; skips apostrophes, Korean suffixes
    ends: list[int | None] = [None] * len(text)
    next_closing: dict[str, int] = {}
    for index in range(len(text) - 1, -1, -1):
        character = text[index]
        if character == "\n":
            next_closing.clear()
            continue
        before = text[index - 1] if index else ""
        after = text[index + 1] if index + 1 < len(text) else ""
        latin_before = before.isascii() and before.isalpha()
        latin_after = after.isascii() and after.isalpha()
        closing = _QUOTE_PAIRS.get(character)
        if closing is not None and not (character == "'" and latin_before):
            ends[index] = next_closing.get(closing)
        if character in _QUOTE_ENDS and not (
            character in {"'", "’"} and latin_before and latin_after
        ):
            next_closing[character] = index

    intent = list(text)
    index = 0
    while index < len(text):
        end = ends[index]
        if end is None:
            index += 1
            continue
        intent[index : end + 1] = " " * (end + 1 - index)
        index = end + 1
    return "".join(intent)


def _has_numeric_arithmetic_action(text: str) -> bool:
    # numbered objects ("section 2"), not operands
    text = " ".join(text.split())
    for match in _ARITHMETIC_ACTION.finditer(text):
        tail = text[match.end():]
        if (
            _EDITING_PREFIX.search(text[max(0, match.start() - 40):match.start()])
            or _EDITING_SUFFIX.search(tail[:40])
        ):
            continue
        if _NEGATED_ARITHMETIC.match(tail):
            # explicit replacement on same operands
            replacement = _REPLACEMENT_ARITHMETIC.match(tail)
            if replacement is None or _NEGATED_ARITHMETIC.match(tail[replacement.end():]):
                continue
        return True
    return False


def _standalone_expression(request: str) -> bool:
    expression = _EXPRESSION_PREFIX.sub("", request.strip())
    expression = _EXPRESSION_SUFFIX.sub("", expression).strip()
    if (
        not expression
        or len(expression) > _MAX_EXPRESSION_CHARS
        or not _EXPRESSION_CHARS.fullmatch(expression)
        or _DATE.fullmatch(expression)
    ):
        return False
    try:
        nodes = list(ast.walk(ast.parse(expression, mode="eval")))
    except (SyntaxError, ValueError, RecursionError):
        return False
    return bool(
        len(nodes) <= 128
        and sum(isinstance(node, ast.Constant) for node in nodes) >= 2
        and any(isinstance(node, ast.BinOp) for node in nodes)
        and all(
            isinstance(
                node,
                (
                    ast.Expression,
                    ast.BinOp,
                    ast.UnaryOp,
                    ast.Constant,
                    ast.Add,
                    ast.Sub,
                    ast.Mult,
                    ast.Div,
                    ast.UAdd,
                    ast.USub,
                ),
            )
            for node in nodes
        )
    )


def _has_non_positional_notation(text: str) -> bool:
    return any(
        unicodedata.category(character) == "No"
        or unicodedata.decomposition(character).startswith(("<super>", "<sub>"))
        for character in text
    )


def _answer_modifiers_only(remainder: str) -> bool:
    clauses = [clause.strip() for clause in re.split(r"[.!?;\n]+", remainder) if clause.strip()]
    if len(clauses) > 2:
        return False
    seen: set[str] = set()
    for clause in clauses:
        category = (
            "format" if _DIRECT_FORMAT.fullmatch(clause)
            else "no_file" if _DIRECT_NO_FILE.fullmatch(clause)
            else None
        )
        if category is None or category in seen:
            return False
        seen.add(category)
    return True


def is_calculation_followup(request: str) -> bool:
    """Explicit previous-result operation; grants nothing without a same-session chain."""
    if not isinstance(request, str) or len(request) > 1024 or _has_non_positional_notation(request):
        return False
    text = unicodedata.normalize("NFKC", request).strip().replace("−", "-")
    matched = _FOLLOWUP_CORE.match(text)
    return bool(matched and _answer_modifiers_only(text[matched.end():]))


def is_plain_numeric_answer(answer: str) -> bool:
    """Bare number/equation shape check, not truth."""
    if not isinstance(answer, str) or len(answer) > 1024 or _has_non_positional_notation(answer):
        return False
    text = unicodedata.normalize("NFKC", answer).strip().removesuffix(".").rstrip()
    text = text.replace("×", "*").replace("÷", "/").replace("−", "-")
    parts = [part.strip() for part in text.split("=")]
    if len(parts) == 1:
        return bool(_PLAIN_NUMBER.fullmatch(text))
    return bool(
        len(parts) == 2
        and _PLAIN_NUMBER.fullmatch(parts[1])
        and _EXPRESSION_CHARS.fullmatch(parts[0])
        and direct_calculation_expression(parts[0]) is not None
    )


def direct_calculation_expression(request: str) -> str | None:
    """Copy one unambiguous user expression for an authorized calculator; else None."""
    if not isinstance(request, str) or len(request) > _MAX_REQUEST_CHARS:
        return None
    # NFKC can merge powers/indices into digits (2² -> 22); left to the model
    if _has_non_positional_notation(request):
        return None
    text = unicodedata.normalize("NFKC", request).strip()
    text = text.replace("×", "*").replace("÷", "/").replace("−", "-")
    text = _EXPRESSION_PREFIX.sub("", text, count=1)
    literal = _DIRECT_LITERAL.match(text)
    if literal is None:
        return None
    expression = literal.group().strip()
    if ".." in expression:
        return None
    # final period not part of the expression
    expression = expression.removesuffix(".").rstrip()
    if _DATE.fullmatch(re.sub(r"\s", "", expression).strip("()")):
        return None
    if not _standalone_expression(expression):
        return None
    remainder = text[literal.end():]
    suffix = _DIRECT_CORE_SUFFIX.match(remainder)
    remainder = remainder[suffix.end():] if suffix else remainder
    if not _answer_modifiers_only(remainder):
        return None

    # calculator literal/depth bounds, no computation
    nesting = 0
    for character in expression:
        if character == "(":
            nesting += 1
            if nesting > 32:
                return None
        elif character == ")":
            nesting -= 1
    tree = ast.parse(expression, mode="eval")
    stack = [(tree.body, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > 32:
            return None
        if isinstance(node, ast.Constant):
            segment = ast.get_source_segment(expression, node) or ""
            if sum(character.isdigit() for character in segment) > 64:
                return None
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return expression


#: unit asking for something: the person's own instruction
_REQUEST_CUE = re.compile(
    r"줘|주세요|주실|해\s{0,2}봐|하세요|할래|해라|부탁|알려|구해|얼마|몇\s{0,2}(?:%|원|명|개)|[?？]"
    r"|\b(?:please|calculate|compute|what|how)\b",
    re.IGNORECASE,
)
#: rule line (「---」) above pasted material
_RULE_LINE = re.compile(r"\n[ \t]{0,8}-{3,8}[ \t]{0,8}\n")


def _mask_pasted_intent(text: str, intent: str) -> str:
    """`intent` with pasted material blanked; its numbers still count."""
    rule = _RULE_LINE.search(text)
    if rule:
        return intent[: rule.start()] + re.sub(r"[^\n]", " ", intent[rule.start():])
    units = [m for m in re.finditer(r"(?:[^\n]|\n(?![ \t]{0,8}\n))+", text)]
    if len(units) < 2:
        units = list(re.finditer(r"[^\n]+", text))
    if len(units) < 2 or not any(_REQUEST_CUE.search(m.group()) for m in units):
        return intent
    out = list(intent)
    for m in units[1:]:
        if not _REQUEST_CUE.search(m.group()):
            out[m.start():m.end()] = re.sub(r"[^\n]", " ", intent[m.start():m.end()])
    return "".join(out)


def requires_calculation(request: str) -> bool:
    """Supplied-number arithmetic cue, no inferred operands; False = no strong cue."""
    if not isinstance(request, str) or len(request) > _MAX_REQUEST_CHARS:
        return False
    text = unicodedata.normalize("NFKC", request)
    text = text.replace("×", "*").replace("÷", "/").replace("−", "-")
    text = _FENCE.sub(" ", text).strip()
    if _standalone_expression(text):
        return True

    # quotes supply numbers, not intent
    intent = _mask_pasted_intent(text, _mask_quoted_intent(text))
    clauses: list[tuple[str, str, str]] = []
    start = 0
    previous_delimiter = ""
    for delimiter in _CLAUSE.finditer(intent):
        clauses.append(
            (text[start : delimiter.start()], intent[start : delimiter.start()], previous_delimiter)
        )
        start = delimiter.end()
        previous_delimiter = delimiter.group()
    clauses.append((text[start:], intent[start:], previous_delimiter))
    eligible: list[tuple[str, str]] = []
    transform_payload = False
    for data, words, before in clauses:
        if _NEW_TASK.fullmatch(before.strip()):
            transform_payload = False
        if _TRANSFORM.search(words):
            # unquoted "Translate this:\n..." stays material
            transform_payload = words.rstrip().endswith(":")
            continue
        if transform_payload:
            continue
        if _CODE.search(words) or _DECLINE.search(words):
            continue
        if _PROGRAM.search(words) and _BUILD.search(words):
            continue
        # format conversion isn't unit arithmetic
        words = _REPRESENTATION_CONVERSION.sub(" ", words)
        eligible.append((data, words))
    # self-contained expression needs no outside operand
    if any(_standalone_expression(raw) for raw, _ in eligible):
        return True
    data = " ".join(raw for raw, _ in eligible)
    if _MISSING.search(data):
        return False
    if (_LENGTH_LIMIT.search(text) and _WRITING.search(intent)
            and not any(_CALCULATE_ASK.search(words) for _, words in eligible)):
        return False
    # correction supplies only the new value
    number_count = len(_NUMBER.findall(_CORRECTION.sub(" ", data)))
    if number_count < 1:
        return False
    # "and" split leaves whitespace the grammar accepts
    arithmetic_action = _has_numeric_arithmetic_action(" ".join(words for _, words in eligible))
    for raw, words in eligible:
        if number_count >= 2 and (
            _CALCULATE.search(words)
            or arithmetic_action
            or (_TARGET.search(words) and _ASK.search(words + ("?" if "?" in text else "")))
        ):
            return True
        if _CONVERT.search(words):
            units = {match.group().casefold() for match in _UNIT.finditer(raw)}
            if len(units) >= 2 or _PERCENT_CONVERSION.search(raw):
                return True
    return False


#: figures from attached data: totals, rates, trends, chart, table
_TABLE_ANALYSIS = re.compile(
    r"집계|평균|비율|비중|합계|총합|합산|추이|분석|차트|그래프|통계|증감|변화|비교|순위|"
    r"최댓값|최솟값|최대|최소|중앙값|분포|표로|표\s{0,2}(?:를\s{0,2})?(?:만들|작성|정리)|"
    r"몇\s{0,2}(?:%|퍼센트|명|개|건|권|원|배)|얼마|"
    r"\b(?:average|mean|median|sum|total|ratio|share|trend|analy[sz]e|analysis|chart|plot|"
    r"compare|aggregate|pivot)\b",
    re.IGNORECASE,
)
#: question carrying a figure
_DIGIT_QUESTION = re.compile(r"\d[^\n?？]{0,80}[?？]|[?？][^\n]{0,80}\d")
#: request limited to reading the file
_DESCRIBE_ONLY = re.compile(
    r"계산\s{0,2}(?:은|는|도)?\s{0,2}(?:없이|말고|빼고)|"
    r"(?:설명|소개|내용\s{0,2}파악|구조)\s{0,2}만|"
    r"\b(?:just|only)\s+(?:describe|explain|summari[sz]e)\b",
    re.IGNORECASE,
)


def asks_table_analysis(request: str) -> bool:
    """Whether a request about attached tabular data wants computed figures."""
    if not isinstance(request, str) or len(request) > _MAX_REQUEST_CHARS:
        return False
    text = unicodedata.normalize("NFKC", _FENCE.sub(" ", request))
    if _DECLINE.search(text) or _DESCRIBE_ONLY.search(text):
        return False
    return bool(_TABLE_ANALYSIS.search(text) or _DIGIT_QUESTION.search(text))
