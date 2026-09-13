"""A bounded display contract for present-state answers without retrieved evidence.

This is not a truth classifier. It prevents an unsupported current value from
being displayed as an answer while preserving a short, explicitly dated memory.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime

from app.services.tools.base import SearchEvidence, Tool, ToolResult

_MAX_PAST_CHARACTERS = 240
_PAST_YEAR = re.compile(r"(?<!\d)((?:1\d{3}|20\d{2}))(?:\s*년|\b)")
_PRESENT_MARKER = re.compile(
    r"현재|현직|현행|지금|오늘|올해|내년|이번|최근|최신|여전히|앞으로|"
    r"재임\s*중|재직\s*중|예정|예상|전망|"
    r"\b(?:current(?:ly)?|incumbent|today|now|present(?:ly)?|still|upcoming|"
    r"next|until|will|serves|holds|remains)\b",
    re.I,
)
_PRESENT_TENSE = re.compile(
    r"입니다|이다(?:[.!。]|$)|고\s*있(?:음|다|습니다|어(?:요)?|는)|\b(?:is|are|has|have)\b",
    re.I,
)
_PAST_TENSE = re.compile(
    r"했|하였|되었|됐|였|이었|왔(?:다|습니다|어요)|"
    r"(?:취임|당선|출시|발표|공개|출범|설립|임명|완료|도입|퇴임|사임|폐지)(?:함|됨)|"
    r"\b(?:was|were|had|became|served|announced|released|"
    r"launched|appointed|elected|took)\b",
    re.I,
)
_KNOWLEDGE_DETAIL = re.compile(r"(?:[1-9]\d*개 대목|자료 [1-9]\d*건 전문)")


def _korean(request: str) -> bool:
    return bool(re.search(r"[가-힣]", request))


def instruction(request: str) -> str:
    if _korean(request):
        return (
            "현재 상태를 뒷받침할 읽기 도구의 결과를 아직 받지 못했습니다. 허용된 도구를 "
            "호출할 수는 있지만 검색 권한을 확대하지 마세요. 도구 근거 없이 답할 때는 "
            "일반 답변 대신 아래 두 항목만 정확히 출력하세요.\n"
            "- 과거 사실: <명시적인 과거 연도가 있는 짧은 과거 사실 한 문장, 없으면 비움>\n"
            "- 현재 상태: 현재 상태는 이번 요청에서 확인하지 못했음.\n"
            "과거 사실에는 올해나 미래 연도, 현재·현직·오늘·재임 중 같은 표현, 현재 값의 "
            "추측, 링크, 추가 항목을 넣지 마세요. 과거 임기에서 현재 재임을 추론하지 마세요. "
            "이 형식은 근거 없는 현재 값을 차단하기 위한 것으로, 과거 사실을 검증했다는 "
            "의미는 아닙니다."
        )
    return (
        "A permitted read tool has not yet supplied evidence for the current state. You may "
        "call permitted tools, but must not expand search permissions. If answering without "
        "retrieved evidence, output exactly these two items instead of an ordinary answer:\n"
        "- Past fact: <one short past fact with an explicit past year, or leave empty>\n"
        "- Current status: Not verified in this request.\n"
        "The past fact must not contain this year or a future year, present-state language "
        "such as current/incumbent/today/still, a guessed current value, links or additional "
        "items. Do not infer continued service from an old term. This format does not "
        "certify the remembered past fact as verified."
    )


def _valid_past(past: str, *, as_of: date) -> bool:
    if any(unicodedata.category(char) in {"Cf", "Cc"} for char in past):
        return False
    if not past or len(past) > _MAX_PAST_CHARACTERS:
        return False
    # A date is necessary, not sufficient, evidence that this is a past claim.
    years = [int(value) for value in _PAST_YEAR.findall(past)]
    if (
        not years
        or any(year >= as_of.year for year in years)
        or _PRESENT_MARKER.search(past)
        or _PRESENT_TENSE.search(past)
        or not _PAST_TENSE.search(past)
    ):
        return False
    if "부터" in past and "까지" not in past or re.search(r"\bsince\b", past, re.I):
        return False
    if re.search(
        r"https?://|www\.|[<>\[\]`]|(?:과거 사실|현재 상태|Past fact|Current status):", past
    ):
        return False
    return True


def _past_fact(content: str, request: str, *, as_of: date) -> str | None:
    label = "과거 사실" if _korean(request) else "Past fact"
    candidates: set[str] = set()
    for line in unicodedata.normalize("NFKC", content).splitlines():
        match = re.fullmatch(rf"- {label}:\s*(.*)", line.strip())
        if match and _valid_past(past := match[1].strip(), as_of=as_of):
            candidates.add(past)
    # Extra prose and status fields never render. Distinct valid past fields
    # have no deterministic winner, so do not silently choose between them.
    return next(iter(candidates)) if len(candidates) == 1 else None


def render(content: str, request: str, *, as_of: date | None = None) -> str:
    """Extract one bounded past field; never pass through other model prose."""
    past = _past_fact(content, request, as_of=as_of or datetime.now(UTC).date())
    if _korean(request):
        status = "현재 상태는 이번 요청에서 확인하지 못했습니다."
        if past:
            return f"- 학습지식의 과거 정보(미검증): {past}\n- 현재 상태: {status}"
        return status
    status = "The current state was not verified in this request."
    if past:
        return f"- Remembered past information (not verified): {past}\n- Current status: {status}"
    return status


def usable_read_result(tool: Tool | None, result: ToolResult) -> bool:
    """Recognize retrieved material, not whether it proves the user's claim."""
    if (
        tool is None
        or tool.read_only is not True
        or result.failed
        or result.empty
        or not result.content.strip()
    ):
        return False
    if re.match(r"\s*(?:오류|error|실패)\s*[:：]", result.content, re.I):
        return False
    if tool.source != "builtin":
        # Authorized MCP read data is as useful as a retrieved snippet. Its
        # presence does not certify relevance, freshness or truth.
        return True
    if tool.name == "web_search":
        return isinstance(result.search_evidence, SearchEvidence) and bool(
            result.search_evidence.source_urls
        )
    if tool.name == "fetch_url":
        return not result.content.lstrip().startswith("오류:")
    if tool.name == "weather":
        return bool(
            re.search(r"기준 시각: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}", result.content)
            and re.search(r"현재: -?\d+(?:\.\d+)?°C", result.content)
        )
    if tool.name == "search_knowledge":
        return bool(_KNOWLEDGE_DETAIL.fullmatch(result.detail or ""))
    return False
