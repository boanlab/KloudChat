"""A turn about the person's Drive (or Slack) handles internal material: with internal material
kept strict-local, a strict-local model answers it and an outside model never gets the tools;
with the rule off, the tools join the turn as any other."""

from __future__ import annotations

import pytest
import test_super_agent_turns as turns
from test_super_agent_turns import STRICT_FLASH, _turn

from app.models.governance import Governance
from app.routers import sessions as sessions_router
from app.services.tools.base import Tool, ToolResult


def _drive_tool() -> Tool:
    async def run(_arguments, _ctx):
        return ToolResult(content="")

    return Tool(name="drive_search", description="d", label="drive",
                parameters={"type": "object", "properties": {}}, run=run, source="google",
                wants_context=True)


def _connected(monkeypatch, seen: list[str]) -> None:
    guard = turns._patch_guard_dependencies

    async def no_agent(monkeypatch_, **kwargs):
        await guard(monkeypatch_, **kwargs)

        async def settings(*_args, **_kwargs):
            # no agent: every tool the turn may have
            return None, None, None

        monkeypatch_.setattr(sessions_router, "agent_settings", settings)

    monkeypatch.setattr(turns, "_patch_guard_dependencies", no_agent)

    async def turn_tools(_db, _user, text, _agent_tools=None):
        seen.append(text)
        return [_drive_tool()] if "드라이브" in text else []

    monkeypatch.setattr(sessions_router.integration_tools_service, "turn_tools", turn_tools)


@pytest.mark.asyncio
async def test_a_drive_turn_goes_strict_local_where_internal_material_stays_inside(
        monkeypatch) -> None:
    seen: list[str] = []
    _connected(monkeypatch, seen)
    strict = Governance(external_data_guard=True, internal_data_strict_local=True,
                        adaptive_classifier_model_id=STRICT_FLASH["id"])
    out = await _turn(monkeypatch, "드라이브에 있는 회의록에서 결정 사항만 뽑아 줘", policy=strict)
    call = out["calls"][0]
    assert call["model"].startswith("strict-local/")
    assert "drive_search" in call["tools"]
    assert seen and "회의록" in seen[0]
    assert out["answer"].routing.get("integrationMaterial") is True


@pytest.mark.asyncio
async def test_without_the_rule_a_drive_turn_keeps_its_model_and_gets_the_tools(
        monkeypatch) -> None:
    seen: list[str] = []
    _connected(monkeypatch, seen)
    out = await _turn(monkeypatch, "드라이브에 있는 회의록에서 결정 사항만 뽑아 줘")
    assert "drive_search" in out["calls"][0]["tools"]


@pytest.mark.asyncio
async def test_a_turn_not_about_drive_gets_no_drive_tools(monkeypatch) -> None:
    seen: list[str] = []
    _connected(monkeypatch, seen)
    out = await _turn(monkeypatch, "마이크로서비스로 쪼갤지 판단해 줘")
    assert "drive_search" not in out["calls"][0]["tools"]


def test_a_conversation_that_held_drive_material_stays_internal() -> None:
    from app.models.chat import ChatSession, Message, Role

    session = ChatSession(user_id="u")
    plain = [Message(session_id=session.id, role=Role.user, content="안녕", routing={})]
    assert not sessions_router._internal_data_present(session, None, None, plain)
    held = [*plain, Message(session_id=session.id, role=Role.assistant, content="요약",
                            routing={"action": "strict_local", "integrationMaterial": True})]
    # a later turn without the words still counts: the history holds the person's files
    assert sessions_router._internal_data_present(session, None, None, held)
