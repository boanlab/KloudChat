"""Tool contract shared by every provider: an OpenAI function definition plus a runner."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    #: OpenAI-style JSON Schema for arguments.
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], Awaitable[str]]
    #: UI label while running.
    label: str
    #: Noun title for permission lists; empty → `label`.
    title: str = ""
    #: Read-only runs unattended; writes gated.
    read_only: bool = True
    #: Provider slug for audit and UI badge.
    source: str = "builtin"
    #: `run` gets `ToolContext` as second arg.
    wants_context: bool = False


@dataclass(frozen=True, slots=True)
class SearchEvidence:
    """Trusted search metadata, outside result text so remote content can't self-verify."""

    source_urls: tuple[str, ...]


@dataclass(slots=True)
class ToolResult:
    """Model-facing content plus UI display."""

    content: str
    #: Extra step line, e.g. "5 results".
    detail: str | None = None
    #: Step marked failed; turn continues.
    failed: bool = False
    #: Ran but found nothing; counted per turn.
    empty: bool = False
    #: Trusted tool's final answer, no extra model hop; masking still applied.
    final_text: str | None = None
    search_evidence: SearchEvidence | None = None
    #: Stored output files `{"id", "name", "size"}`.
    outputs: list[dict[str, Any]] | None = None
    #: Code/output view fields: `code`, `codeAttempt`, `stdout`, `stderr`.
    trace: dict[str, Any] | None = None


@dataclass(slots=True)
class ToolContext:
    """Per-request tool state; explicit to block cross-user access."""

    user_id: str
    session_id: str
    #: Caller's LiteLLM key for billing.
    api_key: str = ""
    #: Enabled tool names; empty = all.
    allowed: set[str] = field(default_factory=set)
    #: Shared-note scope: project, else session.
    project_id: str = ""
    #: Shared-note byline.
    agent_name: str = ""
    #: User message text; checked by `create_artifact` for file requests.
    request: str = ""
    #: Artifacts stored after the turn (tools hold no DB session).
    pending_artifacts: list[dict] = field(default_factory=list)
    #: Shared notes stored after the turn.
    pending_notes: list[dict] = field(default_factory=list)
    #: Calls actually run, by tool name; refused repeats excluded.
    tool_calls: dict[str, int] = field(default_factory=dict)
    #: Strict-local turn; model-calling tools stay strict-local.
    strict_local: bool = False
    #: Sandbox state for this turn's code tools.
    sandbox: dict[str, Any] = field(default_factory=dict)
    #: Tool outputs in call order; number sources for `check_skills`.
    outputs: list[str] = field(default_factory=list)
    #: Web search results; evidence for the post-writing value guard, not number sources.
    searched: list[str] = field(default_factory=list)
    #: Own skill scripts by key; sandbox-only via `run_skill_script`.
    own_scripts: dict[str, dict[str, Any]] = field(default_factory=dict)


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


def openai_snapshot(tools: list[Tool]) -> list[dict[str, Any]]:
    """Detached `to_openai` copy; privacy decision binds immutable bytes."""
    return json.loads(json.dumps(to_openai(tools), ensure_ascii=False))
