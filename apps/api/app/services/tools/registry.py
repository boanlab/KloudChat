"""Assembles the tool list for one turn.

Order of precedence when names collide: built-ins win. A connector cannot shadow
`execute_code` by naming a tool that — the agent loop resolves by name, and a
server that could redefine a built-in could redirect it.

Connector tools are namespaced `mcp__<slug>__<tool>` for the same reason: two
servers may both expose `search`, and the model needs to be able to mean one.
"""

from __future__ import annotations

import logging

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import User
from app.models.workspace import Connector, ConnectorStatus, ConnectorTool
from app.services import mcp
from app.services.tools import catalog
from app.services.tools.base import Tool, ToolResult
from app.services.tools.builtin import available_builtins, knowledge_tool

log = logging.getLogger(__name__)

_NS = "mcp__"


def qualified(slug: str, tool_name: str) -> str:
    return f"{_NS}{slug}__{tool_name}"


def _label_for(connector: Connector, tool_name: str) -> str:
    pretty = tool_name.replace("_", " ")
    return f"{connector.name} · {pretty}"


def _make_runner(connector: Connector, tool_name: str, env: dict[str, str]):
    async def run(arguments: dict) -> ToolResult:
        try:
            endpoint = await catalog.effective_endpoint(connector)
            text = await mcp.call_tool(
                connector.transport.value, endpoint, tool_name, arguments, env
            )
        except mcp.McpError as exc:
            return ToolResult(content=f"오류: {exc}", failed=True)
        if not text:
            return ToolResult(content="(빈 응답)", detail="결과 없음")
        # Cap what a server can push into the context. A runaway response would
        # otherwise evict the conversation it was meant to help with.
        capped = text[:30_000]
        detail = f"{len(text):,}자" + (" (일부)" if len(text) > 30_000 else "")
        return ToolResult(content=capped, detail=detail)

    return run


async def connector_tools(db: AsyncSession, user: User) -> list[Tool]:
    """Every enabled tool from every enabled, connected connector this user has."""
    connectors = (
        await db.exec(
            select(Connector).where(
                Connector.owner_id == user.id,
                Connector.installed == True,  # noqa: E712 — SQL, not Python truthiness
                Connector.enabled == True,  # noqa: E712
                Connector.status == ConnectorStatus.connected,
            )
        )
    ).all()
    if not connectors:
        return []

    rows = (
        await db.exec(
            select(ConnectorTool).where(
                ConnectorTool.connector_id.in_([c.id for c in connectors]),  # type: ignore[attr-defined]
                ConnectorTool.enabled == True,  # noqa: E712
            )
        )
    ).all()

    by_id = {c.id: c for c in connectors}
    out: list[Tool] = []
    for row in rows:
        connector = by_id.get(row.connector_id)
        if connector is None:
            continue
        env = mcp.substitute(
            await catalog.resolve_env(connector.env),
            user_id=user.id, user_email=user.email,
        )
        out.append(
            Tool(
                name=qualified(connector.slug, row.name),
                description=row.description or f"{connector.name} 의 {row.name} 도구",
                parameters=row.parameters or {"type": "object", "properties": {}},
                run=_make_runner(connector, row.name, env),
                label=_label_for(connector, row.name),
                read_only=row.read_only,
                source=connector.slug,
            )
        )
    return out


async def build_tools(
    db: AsyncSession,
    user: User,
    *,
    web_search: bool,
    allowed: list[str] | None = None,
    knowledge: list[tuple[str, str, str | None]] | None = None,
    knowledge_collection: str = "",
) -> list[Tool]:
    """The turn's tool list.

    `allowed` is an agent's allowlist. An empty or absent list means "everything
    this user has"; a populated one is a hard filter, so an agent built for one
    job cannot quietly reach a connector its author never considered.

    `knowledge` is the running agent's own documents. It is exempt from the
    allowlist: the shelf belongs to this agent, was attached by whoever built
    it, and an allowlist written before the shelf existed would silently switch
    it off.
    """
    tools = await available_builtins(web_search)
    seen = {t.name for t in tools}

    for tool in await connector_tools(db, user):
        if tool.name in seen:
            log.warning("connector tool %s shadows a built-in; ignored", tool.name)
            continue
        seen.add(tool.name)
        tools.append(tool)

    if allowed:
        keep = set(allowed)
        tools = [t for t in tools if t.name in keep]
    if knowledge:
        tools.append(knowledge_tool(knowledge, knowledge_collection))
    return tools
