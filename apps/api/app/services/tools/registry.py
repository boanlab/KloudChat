"""Per-turn tool list; built-ins win collisions, connector tools as `mcp__<slug>__<tool>`."""

from __future__ import annotations

import logging

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.user import User, UserRole
from app.models.workspace import Connector, ConnectorStatus, ConnectorTool
from app.services import mcp, settings_store
from app.services.tools import blurbs, catalog
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


#: connector modes named for people: 「심층 리서치 · 빠른 조사(1~5분)」, not 「quick research」
_MODE_LABELS = {
    "quick_research": ("빠른 조사(1~5분)", "빠른 조사"),
    "detailed_research": ("자세한 조사(5~15분)", "자세한 조사"),
    "generate_report": ("조사 보고서(10~30분)", "조사 보고서"),
}


def _label_for(connector: Connector, tool_name: str) -> str:
    pretty = _MODE_LABELS.get(tool_name, (tool_name.replace("_", " "),))[0]
    return f"{connector.name} · {pretty}"


def _title_for(connector: Connector, tool_name: str) -> str:
    """Finished-step noun; empty keeps `label`."""
    mode = _MODE_LABELS.get(tool_name)
    return f"{connector.name} · {mode[1]}" if mode else ""


#: modes that run past the per-tool ceiling
_SLOW_MODES = frozenset({"detailed_research", "generate_report"})


def _timeout_for(tool_name: str) -> float | None:
    return settings.research_timeout_sec if tool_name in _SLOW_MODES else None


def _make_runner(connector: Connector, tool_name: str, env: dict[str, str], *,
                 owner_is_admin: bool = False):
    async def run(arguments: dict) -> ToolResult:
        try:
            await catalog.check_runnable(connector, owner_is_admin=owner_is_admin)
            endpoint = await catalog.effective_endpoint(connector)
            slow = _timeout_for(tool_name)
            guarded = catalog.public_only(connector, owner_is_admin=owner_is_admin)
            text = await mcp.call_tool(
                connector.transport.value, endpoint, tool_name, arguments, env,
                **({"timeout": slow} if slow else {}),
                **({"public_only": True} if guarded else {}),
            )
        except (mcp.McpError, catalog.NotRunnable) as exc:
            return ToolResult(content=f"오류: {exc}", failed=True)
        if not text:
            return ToolResult(content="(빈 응답)", detail="결과 없음", empty=True)
        # Cap on server-pushed context.
        capped = text[:30_000]
        detail = f"{len(text):,}자" + (" (일부)" if len(text) > 30_000 else "")
        return ToolResult(content=capped, detail=detail)

    return run


async def connector_tools(db: AsyncSession, user: User) -> list[Tool]:
    """Enabled tools from the user's connected connectors."""
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
                run=_make_runner(connector, row.name, env,
                                 owner_is_admin=user.role is UserRole.admin),
                label=_label_for(connector, row.name),
                title=_title_for(connector, row.name),
                timeout_sec=_timeout_for(row.name),
                read_only=row.read_only,
                source=connector.slug,
            )
        )
    # Stable order: privacy decision token hashes the tool list.
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
    """Turn tool list; `allowed`: None = all, [] = none, list = hard filter."""
    if strict_local:
        # In-process built-ins only; `share_note` excluded (notes reach later external-model turns).
        tools = [CALCULATE, CREATE_ARTIFACT, CREATE_CHART]
        wanted = allowed is None or bool({RUN_SKILL_SCRIPT.name, RUN_CODE.name} & set(allowed))
        if settings.sandbox_in_boundary and wanted and (await settings_store.tools_config()).exec:
            # Sandbox is self-hosted and offline; run_code uses a strict-local model.
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


#: Picker group for in-process tools.
BUILTIN_GROUP = "기본 도구"

#: Picker blurb; model reads `Tool.description`.
_BUILTIN_BLURB: dict[str, str] = {
    "calculate": "사칙연산과 거듭제곱을 정확히 계산합니다.",
    "web_search": "웹에서 최신 자료를 찾아 출처와 함께 답합니다.",
    "fetch_url": "주소를 주면 그 웹 문서를 읽습니다.",
    "execute_code": "샌드박스에서 Python 코드로 계산하고 데이터를 다룹니다.",
    "run_skill_script": "스킬에 딸린 정해진 스크립트를 첨부 파일에 실행합니다.",
    "run_code": "첨부한 엑셀·CSV·PDF 같은 파일을 읽고 고칩니다.",
    "create_artifact": "문서·코드·웹 페이지 같은 아티팩트를 따로 만듭니다.",
    "create_chart": "숫자를 차트로 그립니다.",
    "share_note": "다음 단계가 이어받을 결론을 공유 메모에 남깁니다.",
    "search_knowledge": "에이전트에 붙여 둔 자료에서 찾습니다.",
}


def _humanize(tool_name: str) -> str:
    """`list_open_issues` → `list open issues`."""
    return tool_name.replace("__", " ").replace("_", " ").strip() or tool_name


def _first_line(text: str) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line[:160]


async def tool_catalog(db: AsyncSession, user: User) -> list[dict[str, object]]:
    """Referenceable tool names with picker labels and availability."""
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

    # Disconnected connectors stay valid; availability rechecked at execution.
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
                "description": blurbs.blurb(connector.slug, row.name, row.description),
            }

    # Only for agents with shelf documents.
    rows["search_knowledge"] = {
        "name": "search_knowledge",
        "label": "에이전트 지식이 있을 때 검색",
        "available": False,
        "group": BUILTIN_GROUP,
        "title": "자료 찾기",
        "description": _BUILTIN_BLURB["search_knowledge"],
    }
    return list(rows.values())
