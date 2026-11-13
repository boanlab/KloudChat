"""Every agent hop resends the tool schemas: `run_skill_script` names its scripts without
their summaries (the summaries come back on a wrong name), and the built-ins stay in budget."""

from __future__ import annotations

import asyncio
import json

from app.services import skill_packages
from app.services.tools import builtin, sandbox_tools
from app.services.tools.arithmetic import CALCULATE
from app.services.tools.base import ToolContext, to_openai


def test_run_skill_script_names_every_script_and_no_summary():
    tool = sandbox_tools.RUN_SKILL_SCRIPT
    for package in skill_packages.scripted():
        assert f"{package.key} 「{package.name}」: {', '.join(package.scripts)}" in tool.description
        for summary in package.scripts.values():
            if summary:
                assert summary not in tool.description
    assert len(tool.description) < 1000


def test_a_wrong_script_name_gets_the_summaries_back():
    package = next(p for p in skill_packages.scripted() if p.key == "xlsx-clean")
    result = asyncio.run(sandbox_tools.run_skill_script(
        {"skill": "xlsx-clean", "script": "nope"}, ToolContext(user_id="u", session_id="s")))
    assert result.failed and package.scripts["clean"] in result.content
    unknown = asyncio.run(sandbox_tools.run_skill_script(
        {"skill": "no-such-skill"}, ToolContext(user_id="u", session_id="s")))
    assert unknown.failed and "pdf-table 「PDF 표 → 엑셀」: extract(" in unknown.content


def test_the_built_ins_stay_in_budget():
    tools = [CALCULATE, builtin.WEB_SEARCH, builtin.FETCH_URL, builtin.WEATHER,
             sandbox_tools.EXECUTE_CODE, sandbox_tools.RUN_SKILL_SCRIPT, sandbox_tools.RUN_CODE,
             builtin.CREATE_ARTIFACT, builtin.CREATE_CHART, builtin.SHARE_NOTE]
    assert len(json.dumps(to_openai(tools), ensure_ascii=False)) < 9_500
