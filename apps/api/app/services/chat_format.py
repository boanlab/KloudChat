"""Remove only a whole-answer fence explicitly forbidden by the user."""

import re

from app.services.freshness import without_quoted_transform_sources

_NO_FENCE = re.compile(
    r"(?:코드\s*펜스|코드\s*블록)(?:(?:나|와|과)\s*(?:설명|부연|해설))?"
    r"(?:는|은|을|를)?\s*(?:없이|빼고|금지|제외|"
    r"필요\s*없(?:어(?:요)?|다|습니다)(?=$|[.!?\s])|"
    r"(?:붙이지|넣지|사용하지)\s*(?:마|말))|"
    r"\b(?:without|no)\s+(?:markdown\s+)?code\s+(?:fences?|blocks?)\b",
    re.I,
)
_FORMATS = re.compile(r"\b(json|yaml|yml|csv)\b|(?<![A-Za-z])(JSON|YAML|YML|CSV)(?=[가-힣])", re.I)


def normalize_raw_payload(content: str, request: str) -> str:
    """Keep payload bytes; do not repair invalid data, prose or partial generations."""
    request = without_quoted_transform_sources(request)
    if not _NO_FENCE.search(request):
        return content
    formats = {match[0].lower().replace("yml", "yaml") for match in _FORMATS.finditer(request)}
    if len(formats) != 1:
        return content
    lines = content.strip().splitlines(keepends=True)
    if len(lines) < 3:
        return content
    opening = re.fullmatch(r"(`{3,}|~{3,})(json|yaml|yml|csv)[ \t]*", lines[0].strip(), re.I)
    if not opening or opening[2].lower().replace("yml", "yaml") not in formats:
        return content
    if lines[-1].strip() != opening[1]:
        return content
    if any(line.lstrip().startswith(("```", "~~~")) for line in lines[1:-1]):
        return content
    return "".join(lines[1:-1])
