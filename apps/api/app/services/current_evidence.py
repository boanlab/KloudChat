"""Present-state answers without retrieved evidence: best effort, dated, marked unverified."""

from __future__ import annotations

import re
from datetime import date

from app.services.tools.base import SearchEvidence, Tool, ToolResult

_KNOWLEDGE_DETAIL = re.compile(r"(?:[1-9]\d*개 대목|자료 [1-9]\d*건 전문)")


def _korean(request: str) -> bool:
    return bool(re.search(r"[가-힣]", request))


def instruction(request: str) -> str:
    """Instruction for a present-state question with no retrieved evidence this turn."""
    if _korean(request):
        return (
            "이 질문은 시간이 지나면 달라지는 현재 상태를 묻는데, 이번 요청에는 그것을 확인할 "
            "검색·열람 결과가 없습니다. 그래도 아는 범위에서 유용하게 답하세요: 마지막으로 알고 "
            "있는 값이나 사실을 연도·시점과 함께 적고, 「현재」「지금」「오늘」처럼 지금 그렇다고 "
            "단정하는 표현은 쓰지 마세요. 답 끝에 그 값이 학습 시점의 지식이며 이번 요청에서 "
            "확인하지 않았다는 점을 한 문장으로 밝히고, 확인할 방법(공식 사이트, 검색 켜기)을 "
            "짧게 안내하세요. 모르는 세부는 모른다고 쓰고 지어내지 마세요."
        )
    return (
        "This question asks about a present state that changes over time, and this turn "
        "has no retrieved evidence for it. Still answer usefully from what you know: give "
        "the last value or fact you know with its year or date, and do not phrase it as "
        "what holds now or today. End with one sentence saying the answer is based on "
        "training-time knowledge and was not verified in this request, and how to verify "
        "it (an official site, enabling search). Say what you do not know rather than guess."
    )


#: Words that already mark an answer as dated or unverified.
_HEDGED = re.compile(
    r"확인(?:하지|되지|되지\s*않|할\s*수\s*없|이\s*필요)|미검증|검증되지|검증이\s*필요|"
    r"(?:학습|지식|훈련|데이터)\s*(?:시점|기준)|\d{4}\s*년[^.\n]{0,8}기준|(?:시점|기준)\s*(?:기준|시점)|"
    r"(?:이후|그\s*뒤|지금은|현재는|실제로는)[^.\n]{0,12}(?:바뀔|달라질|변경될|다를)\s*수|최신\s*정보는|"
    r"확정(?:할\s*수\s*없|하지\s*못)|완료하지\s*못|"
    r"\b(?:unverified|not\s+verified|as\s+of|may\s+have\s+changed|could\s+not\s+verify|"
    r"based\s+on\s+(?:my\s+)?training|knowledge\s+cutoff|check\s+the\s+official)\b",
    re.I,
)


def caveat(request: str) -> str:
    """The sentence appended when the model did not mark its answer as unverified."""
    if _korean(request):
        return (
            "\n\n_위 내용은 학습 시점의 지식이며, 현재 상태는 이번 요청에서 확인하지 않았습니다. "
            "최신 값은 공식 자료나 웹 검색을 켜서 확인하세요._"
        )
    return (
        "\n\n_This reflects training-time knowledge; the current state was not verified in "
        "this request. Check an official source or enable web search for the latest value._"
    )


def has_caveat(content: str) -> bool:
    return bool(_HEDGED.search(content))


def render(content: str, request: str, *, as_of: date | None = None) -> str:
    """The answer as shown: the model's words, plus the caveat when they lack one.

    `as_of` is accepted for callers that pass it; the caveat does not depend on it.
    """
    del as_of
    body = (content or "").strip()
    if not body:
        status = (
            "현재 상태는 이번 요청에서 확인하지 않았습니다."
            if _korean(request)
            else "The current state was not verified in this request."
        )
        return status
    if has_caveat(body):
        return body
    return body + caveat(request)


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
        # Authorized MCP read data counts as retrieved, without certifying its truth.
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
