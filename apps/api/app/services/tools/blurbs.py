"""What a shipped connector's tools do, said for the person (tool panels and pickers).

A server's own tool description is written for the model (the YouTube one is an English
docstring with trigger phrases); people read these instead. Other connectors' tools show the
first line of their description.
"""

from __future__ import annotations

#: (connector slug, tool name) → one Korean line
BLURBS: dict[tuple[str, str], str] = {
    ("youtube", "transcript"):
        "YouTube 영상의 자막을 가져오고, 자막이 없으면 음성을 받아 적습니다.",
    ("time", "get_current_time"): "지정한 시간대의 지금 날짜와 시각을 알려 줍니다.",
    ("time", "convert_time"): "한 시간대의 시각을 다른 시간대의 시각으로 바꿉니다.",
    ("deep-research", "quick_research"):
        "웹을 여러 번 찾아 출처가 달린 짧은 조사 결과를 냅니다(1~5분).",
    ("deep-research", "detailed_research"):
        "웹·논문을 깊이 찾아 자세한 조사 결과를 냅니다(5~15분).",
    ("deep-research", "generate_report"): "출처가 달린 긴 조사 보고서를 씁니다(10~30분).",
    ("deep-research", "search"): "검색 엔진 하나로 찾은 결과 목록을 그대로 가져옵니다.",
    ("deep-research", "analyze_documents"): "색인해 둔 문서 모음에서 찾아 요약합니다.",
    ("deep-research", "list_search_engines"): "쓸 수 있는 검색 엔진 목록을 보여 줍니다.",
    ("deep-research", "list_strategies"): "쓸 수 있는 조사 방식 목록을 보여 줍니다.",
    ("deep-research", "get_configuration"): "조사 서버의 현재 설정을 보여 줍니다.",
}


def first_line(text: str) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line[:160]


def blurb(slug: str, name: str, description: str) -> str:
    """The person-facing line for a connector tool."""
    return BLURBS.get((slug, name)) or first_line(description)


__all__ = ["BLURBS", "blurb", "first_line"]
