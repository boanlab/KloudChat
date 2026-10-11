"""Chat answer shape: forbidden whole-answer fence, unopenable links."""

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


#: Markdown link/picture; target may contain spaces
_LINK = re.compile(r"(?P<bang>!?)\[(?P<text>[^\]\n]{0,300})\]\((?P<target>[^)\n]{1,500})\)")
_WEB = re.compile(r"^(?:https?://|mailto:|tel:|#|data:image/)", re.I)
#: code-sandbox paths
_SANDBOX_PATH = re.compile(r"^/(?:mnt|tmp|home|workspace|sandbox|outputs|data)/", re.I)


def _target_name(target: str) -> str:
    """File named by a local target: sandbox:/mnt/data/x.png -> x.png."""
    target = re.sub(r"^[a-z][\w+.-]{1,20}:", "", target.strip().strip("<>"), flags=re.I)
    return target.split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1].strip()


def unlink_local_targets(text: str, made: set[str] | frozenset[str] = frozenset()) -> str:
    """Sandbox/store links as plain words (made files → chips); web links kept."""
    if not text or "](" not in text:
        return text

    def swap(m: re.Match[str]) -> str:
        target = m.group("target").strip()
        if _WEB.match(target) or (target.startswith("/") and not _SANDBOX_PATH.match(target)):
            return m.group(0)
        words = m.group("text").strip()
        name = _target_name(target)
        known = name if name in made or name.lower() in {n.lower() for n in made} else ""
        if m.group("bang"):
            if not known:
                return words
            return f"*그림 「{words}」: 답 아래 파일 {known}*" if words and words != known else (
                f"*그림: 답 아래 파일 {known}*")
        if known and words != known:
            return f"{words}(답 아래 파일 {known})"
        return words or known

    out, at = [], 0
    for fence in re.finditer(r"```.*?(?:```|\Z)", text, re.S):
        out.append(_LINK.sub(swap, text[at:fence.start()]))
        out.append(fence.group(0))
        at = fence.end()
    out.append(_LINK.sub(swap, text[at:]))
    return "".join(out)
