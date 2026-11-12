"""Connector tools sent by name until the model asks for them (`load_tools`).

A connector with many tools (a GitHub or Notion server lists dozens) costs every agent hop
its full JSON schemas. When deferral applies, such a connector's tools go out only as a name
and one line inside the `load_tools` tool's description; the model calls `load_tools` with
the names it needs, gets their full definitions back as the tool result, and from the next
hop on those tools are offered with their schemas. A connector with few tools
(`connector_tools_eager_max`) is sent whole, as are the tools a turn must be able to call
at once (a forced or preset tool, a skill's source tool). A direct call to a deferred tool
still runs and loads it.

Everything sent comes from the turn's tool-definition snapshot, so deferral never puts a
byte on the wire that the privacy scan did not see (`routers/sessions._tool_definition_source`).
Who gets deferral is decided by model group (`applies`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.services import model_tier
from app.services.tools.base import Tool, ToolResult

LOAD_TOOLS = "load_tools"
_NS = "mcp__"
#: one line per deferred tool in the `load_tools` description
_LINE_CHARS = 110


def applies(model: str | None) -> bool:
    """External models defer large connectors; self-hosted ones only when
    `self_hosted_defer_connector_tools` is on (27B tool choice with an extra step is unmeasured)."""
    if model_tier.self_hosted(model):
        return settings.self_hosted_defer_connector_tools
    return True


def _name(definition: dict[str, Any]) -> str:
    return str((definition.get("function") or {}).get("name") or "")


def _connector(name: str) -> str | None:
    """`mcp__<slug>__<tool>` → slug."""
    if not name.startswith(_NS):
        return None
    slug, sep, _ = name[len(_NS):].partition("__")
    return slug if sep else None


def _one_line(text: str) -> str:
    line = " ".join(str(text or "").split())
    for stop in (". ", "다. ", "。"):
        if stop in line:
            line = line[: line.index(stop) + len(stop)].strip()
            break
    return line if len(line) <= _LINE_CHARS else line[: _LINE_CHARS - 1].rstrip() + "…"


@dataclass(slots=True)
class Deferral:
    """One turn's deferred connector tools."""

    #: full definitions by tool name, from the turn's snapshot
    full: dict[str, dict[str, Any]]
    deferred: list[str]
    loaded: set[str] = field(default_factory=set)
    loader: Tool | None = None

    def pending(self) -> list[str]:
        return [name for name in self.deferred if name not in self.loaded]

    def narrow(
        self, tools: list[Tool], definitions: list[dict[str, Any]] | None
    ) -> tuple[list[Tool], list[dict[str, Any]] | None]:
        """This hop's tools: deferred ones out until loaded, `load_tools` in while any wait."""
        hidden = set(self.pending())
        tools = [tool for tool in tools if tool.name not in hidden]
        if definitions is not None:
            definitions = [d for d in definitions if _name(d) not in hidden]
        if hidden and self.loader is not None:
            tools = [*tools, self.loader]
            if definitions is not None:
                definitions = [*definitions, loader_definition(self.loader)]
        return tools, definitions

    def mark_called(self, name: str) -> None:
        if name in self.full:
            self.loaded.add(name)


def loader_definition(loader: Tool) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": loader.name, "description": loader.description, "parameters": loader.parameters}}


def plan(
    model: str | None,
    tools: list[Tool],
    definitions: list[dict[str, Any]] | None,
    *,
    eager: set[str] = frozenset(),  # type: ignore[assignment]
) -> Deferral | None:
    """The turn's deferral, or None when nothing is deferred."""
    if not applies(model):
        return None
    if definitions is None:
        from app.services.tools.base import to_openai

        definitions = to_openai(tools)
    offered = {tool.name for tool in tools}
    full = {_name(d): d for d in definitions if _name(d) in offered}
    by_connector: dict[str, list[str]] = {}
    for name in full:
        if (slug := _connector(name)) is not None:
            by_connector.setdefault(slug, []).append(name)
    limit = max(0, settings.connector_tools_eager_max)
    deferred = sorted(
        name
        for names in by_connector.values() if len(names) > limit
        for name in names if name not in eager
    )
    if not deferred or LOAD_TOOLS in offered:
        return None
    deferral = Deferral(full=full, deferred=deferred)
    deferral.loader = _loader(deferral)
    return deferral


def _loader(deferral: Deferral) -> Tool:
    listed = "; ".join(
        f"{name} — {_one_line((deferral.full[name].get('function') or {}).get('description', ''))}"
        for name in deferral.deferred
    )

    async def run(arguments: dict[str, Any]) -> ToolResult:
        asked = arguments.get("names")
        names = [str(n).strip() for n in asked] if isinstance(asked, list) else []
        known = [n for n in names if n in deferral.full]
        unknown = [n for n in names if n not in deferral.full]
        if not known:
            return ToolResult(
                content=("오류: 불러올 도구 이름이 없습니다. 이 목록의 이름을 그대로 쓰세요: "
                         + ", ".join(deferral.pending())),
                failed=True,
            )
        deferral.loaded.update(known)
        body = json.dumps([deferral.full[n]["function"] for n in known], ensure_ascii=False)
        note = f" 목록에 없는 이름: {', '.join(unknown)}." if unknown else ""
        return ToolResult(
            content=(f"불러온 도구 정의입니다. 다음 단계부터 이 도구를 호출할 수 있습니다.{note}\n"
                     + body),
            detail=", ".join(known),
        )

    return Tool(
        name=LOAD_TOOLS,
        description=(
            "Connector tools below are available but sent by name only. Call load_tools with "
            "the names you need; their full definitions come back and you can call them in "
            "the next step. Available: " + listed
        ),
        parameters={
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tool names from the list, exactly as written.",
                }
            },
            "required": ["names"],
        },
        run=run,
        label="도구 설명 읽는 중",
        title="도구 설명 읽기",
        read_only=True,
    )
