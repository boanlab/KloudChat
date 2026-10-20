"""「심층 리서치로」 runs the connector's thorough mode with its own time limit, the step names
the mode, and a source listed by its file name is named by the document instead."""

from __future__ import annotations

from types import SimpleNamespace

from app.core.config import settings
from app.routers import sessions
from app.services import agent
from app.services.tools import registry
from app.services.tools.base import Tool

ASK = ("심층 리서치로 국내 대학 근로장학생 제도의 최근 쟁점(최저임금 연동, 근로시간 상한, "
       "4대보험)을 조사해서 출처 링크와 함께 정리해 줘.")


async def _never(_arguments):
    return ""


def _tool(name: str) -> Tool:
    return Tool(name=name, description="", parameters={}, run=_never, label=name)


TOOLS = [_tool(registry.qualified("deep-research", mode))
         for mode in ("quick_research", "detailed_research", "generate_report")] + [
    _tool("web_search")]


def test_a_named_deep_research_runs_the_thorough_mode() -> None:
    assert sessions._deep_research_tool(ASK, TOOLS) == "mcp__deep-research__detailed_research"
    # without the thorough mode offered, the quick one still runs as asked
    quick_only = [t for t in TOOLS if "detailed" not in t.name]
    assert sessions._deep_research_tool(ASK, quick_only) == "mcp__deep-research__quick_research"


def test_an_ordinary_question_forces_no_research_mode() -> None:
    assert sessions._deep_research_tool("근로장학생 시급이 얼마야?", TOOLS) is None
    assert sessions._deep_research_tool(ASK, [_tool("web_search")]) is None


def test_the_research_modes_say_which_ran_and_the_slow_ones_wait_longer() -> None:
    connector = SimpleNamespace(name="심층 리서치")
    assert registry._label_for(connector, "quick_research") == "심층 리서치 · 빠른 조사(1~5분)"
    assert registry._title_for(connector, "detailed_research") == "심층 리서치 · 자세한 조사"
    assert registry._label_for(connector, "list_strategies") == "심층 리서치 · list strategies"
    assert registry._timeout_for("detailed_research") == settings.research_timeout_sec
    assert registry._timeout_for("quick_research") is None


def test_a_source_titled_by_its_file_name_is_named_by_the_document() -> None:
    url = "https://www.kunsan.ac.kr/upload/176335726922156.hwp"
    excerpt = f"[3] 176335726922156.hwp\n{url}\n2026학년도 국가근로장학사업 업무편람\n시급 …"
    assert agent._source_label(url, "176335726922156.hwp", excerpt) == (
        "2026학년도 국가근로장학사업 업무편람 · kunsan.ac.kr")
    # nothing readable: the kind of document, never the id
    assert agent._source_label(url, "176335726922156.hwp") == "kunsan.ac.kr 게시 문서(HWP)"
    assert agent._source_label(url) == "kunsan.ac.kr 게시 문서(HWP)"
