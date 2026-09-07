from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models.user import User
from app.routers.sessions import _ncs_preflight_tool, _strict_local_tools
from app.services.tools import registry
from app.services.tools.arithmetic import CALCULATE
from app.services.tools.ncs_check import CHECK_NCS_ANSWER


def test_preflight_requires_explicit_arithmetic_skill_and_allowed_tool():
    assert _ncs_preflight_tool({"ncs-arithmetic"}, [CHECK_NCS_ANSWER]) == "check_ncs_answer"
    with pytest.raises(HTTPException) as error:
        _ncs_preflight_tool({"ncs-arithmetic"}, [CALCULATE])
    assert error.value.status_code == 409
    assert error.value.detail == "ncs_verification_tool_unavailable"


def test_unrelated_agent_or_no_selected_skill_does_not_force_verification():
    assert _ncs_preflight_tool(set(), [CHECK_NCS_ANSWER]) is None
    assert _ncs_preflight_tool({"ncs-reasoning"}, []) is None


@pytest.mark.asyncio
async def test_preflight_is_in_process_and_does_not_add_other_permissions(monkeypatch):
    remote = AsyncMock(side_effect=AssertionError("remote discovery must not run"))
    monkeypatch.setattr(registry, "available_builtins", remote)
    monkeypatch.setattr(registry, "connector_tools", remote)
    user = User(email="synthetic@example.test", password_hash="x")
    tools = await registry.build_tools(
        object(), user, web_search=True, allowed=["check_ncs_answer"], strict_local=True
    )
    assert [tool.name for tool in _strict_local_tools(tools)] == ["check_ncs_answer"]
    assert remote.await_count == 0
    assert (
        await registry.build_tools(object(), user, web_search=True, allowed=[], strict_local=True)
        == []
    )
