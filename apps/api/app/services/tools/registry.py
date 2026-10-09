"""Assembles the tool list for one turn.

Built-ins win name collisions (the loop resolves tools by name); connector tools
are namespaced `mcp__<slug>__<tool>`.
"""

from __future__ import annotations

import logging

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.user import User
from app.models.workspace import Connector, ConnectorStatus, ConnectorTool
from app.services import mcp, settings_store
from app.services.tools import catalog
from app.services.tools.base import Tool, ToolResult
from app.services.tools.builtin import (
    CALCULATE,
    CREATE_ARTIFACT,
    CREATE_CHART,
    EXECUTE_CODE,
    FETCH_URL,
    RUN_CODE,
    RUN_SKILL_SCRIPT,
    SHARE_NOTE,
    WEB_SEARCH,
    available_builtins,
    knowledge_tool,
    skill_tool,
)

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
            return ToolResult(content="(빈 응답)", detail="결과 없음", empty=True)
        # Caps what a server can push into the context.
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
            await catalog.connector_env(connector),
            user_id=user.id,
            user_email=user.email,
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
    # Stable order: the privacy decision token hashes the exact tool list.
    return sorted(out, key=lambda tool: tool.name)


async def build_tools(
    db: AsyncSession,
    user: User,
    *,
    web_search: bool,
    allowed: list[str] | None = None,
    knowledge: list[tuple[str, str, str | None]] | None = None,
    knowledge_collection: str = "",
    include_connectors: bool = True,
    strict_local: bool = False,
    skills: list[tuple[str, str, str]] | None = None,
) -> list[Tool]:
    """The turn's tool list.

    `allowed`: None inherits everything the user has, [] denies all, a list is a hard filter.
    `knowledge`: the agent's documents, offered as `search_knowledge` under the same allowlist.
    `skills`: installed skills not switched on, offered as `use_skill` for the model to pick.
    """
    if strict_local:
        # Only in-process built-ins; no connectors, no vector retrieval. `share_note`
        # is excluded because a note reaches later turns that may run on external models.
        tools = [CALCULATE, CREATE_ARTIFACT, CREATE_CHART]
        wanted = allowed is None or bool({RUN_SKILL_SCRIPT.name, RUN_CODE.name} & set(allowed))
        if settings.sandbox_in_boundary and wanted and (await settings_store.tools_config()).exec:
            # The sandbox runs in the self-hosted stack with no network; run_code then
            # writes its code on a strict-local model (see `sandbox_tools.code_models`).
            tools += [RUN_SKILL_SCRIPT, RUN_CODE]
        include_connectors = False
        knowledge_collection = ""
    else:
        tools = await available_builtins(web_search)
    seen = {t.name for t in tools}

    if include_connectors:
        for tool in await connector_tools(db, user):
            if tool.name in seen:
                log.warning("connector tool %s shadows a built-in; ignored", tool.name)
                continue
            seen.add(tool.name)
            tools.append(tool)

    if allowed is not None:
        keep = set(allowed)
        tools = [t for t in tools if t.name in keep]
    if knowledge and (allowed is None or "search_knowledge" in allowed):
        tools.append(knowledge_tool(knowledge, knowledge_collection))
    if skills and (allowed is None or "use_skill" in allowed):
        tools.append(skill_tool(skills))
    return tools


#: The group every in-process tool is listed under in the pickers.
BUILTIN_GROUP = "기본 도구"

#: One line a person reads in the tool picker; the model reads `Tool.description`.
_BUILTIN_BLURB: dict[str, str] = {
    "calculate": "사칙연산과 거듭제곱을 정확히 계산합니다.",
    "web_search": "웹에서 최신 자료를 찾아 출처와 함께 답합니다.",
    "fetch_url": "주소를 주면 그 웹 문서를 읽습니다.",
    "execute_code": "샌드박스에서 Python 코드로 계산하고 데이터를 다룹니다.",
    "run_skill_script": "스킬에 딸린 정해진 스크립트를 첨부 파일에 실행합니다.",
    "run_code": "첨부한 엑셀·CSV·PDF 같은 파일을 읽고 고칩니다.",
    "create_artifact": "문서·코드·웹 페이지 같은 결과물을 따로 만듭니다.",
    "create_chart": "숫자를 차트로 그립니다.",
    "share_note": "다음 단계가 이어받을 결론을 공유 메모에 남깁니다.",
    "search_knowledge": "에이전트에 붙여 둔 자료에서 찾습니다.",
}


def _humanize(tool_name: str) -> str:
    """`list_open_issues` → `list open issues`; a connector's tool with no title of its own."""
    return tool_name.replace("__", " ").replace("_", " ").strip() or tool_name


def _first_line(text: str) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line[:160]


async def tool_catalog(db: AsyncSession, user: User) -> list[dict[str, object]]:
    """Tool names an agent or skill may reference, with labels and current availability.

    Each row also carries what the pickers show a person: `group` (기본 도구 or the
    connector's name), a short `title` and a one-line `description`. The raw name stays
    for the allowlist and is never the headline.
    """
    available = {tool.name: tool for tool in await build_tools(db, user, web_search=True)}
    rows: dict[str, dict[str, object]] = {}
    for tool in (
        CALCULATE,
        WEB_SEARCH,
        FETCH_URL,
        EXECUTE_CODE,
        RUN_SKILL_SCRIPT,
        RUN_CODE,
        CREATE_ARTIFACT,
        CREATE_CHART,
        SHARE_NOTE,
    ):
        title = tool.title or tool.label or tool.name
        rows[tool.name] = {
            "name": tool.name,
            "label": title,
            "available": tool.name in available,
            "group": BUILTIN_GROUP,
            "title": title,
            "description": _BUILTIN_BLURB.get(tool.name) or _first_line(tool.description),
        }

    # Connector names stay valid while disconnected; availability is rechecked at execution.
    connectors = (
        await db.exec(
            select(Connector).where(
                Connector.owner_id == user.id,
                Connector.installed == True,  # noqa: E712
            )
        )
    ).all()
    if connectors:
        connector_rows = (
            await db.exec(
                select(ConnectorTool).where(
                    ConnectorTool.connector_id.in_([item.id for item in connectors])  # type: ignore[attr-defined]
                )
            )
        ).all()
        by_id = {item.id: item for item in connectors}
        for row in connector_rows:
            connector = by_id.get(row.connector_id)
            if connector is None:
                continue
            name = qualified(connector.slug, row.name)
            rows[name] = {
                "name": name,
                "label": _label_for(connector, row.name),
                "available": name in available,
                "group": connector.name,
                "title": _humanize(row.name),
                "description": _first_line(row.description),
            }

    # Exists only for an agent with shelf documents (see AgentOut.has_knowledge).
    rows["search_knowledge"] = {
        "name": "search_knowledge",
        "label": "에이전트 지식이 있을 때 검색",
        "available": False,
        "group": BUILTIN_GROUP,
        "title": "자료 찾기",
        "description": _BUILTIN_BLURB["search_knowledge"],
    }
    return list(rows.values())
