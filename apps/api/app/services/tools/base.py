"""Tool contract shared by every provider.

A tool is an OpenAI function definition plus something that runs it. Providers
(built-ins, MCP servers, later connectors) all hand back `Tool` objects, so the
agent loop never learns where a tool came from — which is what keeps adding a
provider from touching the loop.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    #: JSON Schema for the arguments, as the OpenAI tools API expects.
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], Awaitable[str]]
    #: Shown in the UI while the call is in flight, e.g. "searching the web".
    label: str
    #: Read-only tools run unattended. Write tools are gated — see connectors.
    read_only: bool = True
    #: Where it came from, for the audit trail and the UI badge.
    source: str = "builtin"
    #: Receives the `ToolContext` as a second argument. Off by default: most
    #: tools are pure functions of their arguments, and handing every one of them
    #: the caller's identity widens what a broken tool can reach.
    wants_context: bool = False


@dataclass(slots=True)
class ToolResult:
    """What the loop feeds back to the model, plus what the UI should show."""

    content: str
    #: Optional extra line under the step, e.g. "5 results".
    detail: str | None = None
    #: Marks the step red without aborting the turn.
    failed: bool = False


@dataclass(slots=True)
class ToolContext:
    """Per-request state a tool may need. Kept explicit rather than global so a
    tool cannot quietly reach for another user's data.
    """

    user_id: str
    session_id: str
    #: The caller's LiteLLM virtual key. Present so the model calls this turn
    #: makes are billed and rate-limited against them, not against the instance.
    api_key: str = ""
    #: Tool names the caller enabled for this turn. Empty means "all available".
    allowed: set[str] = field(default_factory=set)
    #: Artifacts the model asked to create this turn, in call order.
    #:
    #: Collected rather than written on the spot: the turn owns one database
    #: session, opened after the stream finishes, and a tool reaching for its own
    #: would commit rows for a turn that may still fail.
    pending_artifacts: list[dict] = field(default_factory=list)


def to_openai(tools: list[Tool]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]
