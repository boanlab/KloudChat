"""The tool pickers group tools and name them in words; the raw name is detail, not the headline."""

from __future__ import annotations

import pytest

from app.models.user import User, UserStatus
from app.models.workspace import Connector, ConnectorStatus, ConnectorTool
from app.schemas.workspace import ToolCatalogOut
from app.services.tools import registry


class _Result:
    def __init__(self, rows: list):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Db:
    """First query: the user's connectors; second: their tools."""

    def __init__(self, connectors: list[Connector], tools: list[ConnectorTool]):
        self._answers = [connectors, tools]

    async def exec(self, _query):
        return _Result(self._answers.pop(0) if self._answers else [])


def _user() -> User:
    return User(
        id="user-1",
        email="user@example.com",
        password_hash="hash",
        name="User",
        status=UserStatus.active,
    )


@pytest.fixture
def _no_live_tools(monkeypatch):
    async def none(*_args, **_kwargs):
        return []

    monkeypatch.setattr(registry, "build_tools", none)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_live_tools")
async def test_builtins_are_grouped_titled_and_described():
    rows = await registry.tool_catalog(_Db([], []), _user())
    by_name = {row["name"]: row for row in rows}
    search = by_name["web_search"]
    assert search["group"] == registry.BUILTIN_GROUP == "기본 도구"
    assert search["title"] == "웹 검색"
    assert search["description"] and "\n" not in str(search["description"])
    for row in rows:
        assert row["group"] == "기본 도구"
        assert row["title"] and not str(row["title"]).startswith("mcp__")
        assert row["description"]
    # The knowledge tool keeps its label and stays unavailable until an agent has documents.
    assert by_name["search_knowledge"]["available"] is False
    assert by_name["search_knowledge"]["title"] == "자료 찾기"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_live_tools")
async def test_connector_tools_group_under_the_connector_name():
    connector = Connector(
        id="c-1",
        owner_id="user-1",
        name="GitHub",
        slug="github",
        installed=True,
        status=ConnectorStatus.connected,
    )
    tool = ConnectorTool(
        id="t-1",
        connector_id="c-1",
        name="list_open_issues",
        description="Lists open issues in a repository.\nSecond line is detail.",
    )
    rows = await registry.tool_catalog(_Db([connector], [tool]), _user())
    row = next(item for item in rows if item["name"] == "mcp__github__list_open_issues")
    assert row["group"] == "GitHub"
    assert row["title"] == "list open issues"
    assert row["description"] == "Lists open issues in a repository."
    # The older label stays for clients that read it.
    assert row["label"] == "GitHub · list open issues"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_live_tools")
async def test_the_wire_shape_carries_the_new_fields():
    rows = await registry.tool_catalog(_Db([], []), _user())
    wire = ToolCatalogOut(**rows[0]).model_dump(by_alias=True)
    assert {"name", "label", "available", "group", "title", "description"} <= set(wire)
