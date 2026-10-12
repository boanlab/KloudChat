"""The product is general-purpose: the NCS checker, its skills and its preflight are gone."""

import importlib.util

import pytest

from app.routers import sessions
from app.services import starter
from app.services.tools import builtin, registry


@pytest.mark.parametrize("module", ["app.services.tools.ncs_check", "app.services.ncs_submission"])
def test_the_ncs_modules_are_removed(module):
    assert importlib.util.find_spec(module) is None


def test_no_ncs_tool_or_skill_is_shipped():
    assert not hasattr(builtin, "CHECK_NCS_ANSWER")
    assert not hasattr(registry, "CHECK_NCS_ANSWER")
    assert not any("ncs" in spec["key"] for spec in starter._SKILLS)
    assert "check_ncs_answer" not in sessions._STRICT_LOCAL_TOOL_NAMES
    assert not hasattr(sessions, "_ncs_preflight_tool")
    assert "check_ncs_answer" not in sessions._CALCULATION_INSTRUCTION


async def test_no_builtin_offers_the_ncs_checker(monkeypatch):
    from app.services import settings_store

    async def backends():
        return settings_store.ToolBackends(exec="http://exec.test", fetch="http://fetch.test")

    monkeypatch.setattr(settings_store, "tools_config", backends)
    names = {tool.name for tool in await builtin.available_builtins(True)}
    assert "calculate" in names and "check_ncs_answer" not in names
