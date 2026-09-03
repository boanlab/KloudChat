"""Tool contract shared by every provider.

A tool is an OpenAI function definition plus something that runs it. Providers
(built-ins, MCP servers, later connectors) all hand back `Tool` objects, so the
agent loop never learns where a tool came from — which is what keeps adding a
provider from touching the loop.
"""

from __future__ import annotations

import json
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
    #: The tool's name as a noun, e.g. "web search" — what a permission list
    #: and a finished step show. `label` used to serve both, so 도구 권한 read
    #: as if everything were running and a finished row still said 검색 중.
    #: Empty falls back to `label`, for tools built before this existed.
    title: str = ""
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
    #: Ran, but found nothing the answer can lean on — a search whose every
    #: hit was off topic, or none at all. Counted by the loop: a turn whose
    #: searches all came back empty says so under the answer.
    empty: bool = False


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
    #: Which project this turn is running inside, if any. What decides how far
    #: a shared note reaches: inside a project it is the project's, and outside
    #: one it belongs to this conversation alone.
    project_id: str = ""
    #: The agent doing the work, for the byline on a shared note. Empty on a
    #: plain conversation, which is the note saying nobody in particular wrote
    #: it rather than inventing an author.
    agent_name: str = ""
    #: What the person actually typed this turn.
    #:
    #: Carried because one rule cannot be decided from the payload alone:
    #: whether a short piece of writing was asked for *as a file*. The model
    #: reports that itself in `create_artifact(userRequested=…)` and reports it
    #: wrong in the one direction that matters — asked for a mail draft, it sets
    #: the flag because a draft was requested, and a three-sentence mail lands
    #: behind a preview tab, a source tab and a version history. The person's
    #: own words are the evidence; the model's claim about them is not.
    request: str = ""
    #: Artifacts the model asked to create this turn, in call order.
    #:
    #: Collected rather than written on the spot: the turn owns one database
    #: session, opened after the stream finishes, and a tool reaching for its own
    #: would commit rows for a turn that may still fail.
    pending_artifacts: list[dict] = field(default_factory=list)
    #: Notes this turn wants left for whatever runs next. Same deferral, and for
    #: the same reason: a handoff written by a turn that then failed would be a
    #: finding nobody actually produced.
    pending_notes: list[dict] = field(default_factory=list)


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
    """Materializes the exact detached definitions sent to the model.

    ``to_openai`` intentionally reuses each tool's ``parameters`` object.  A
    privacy decision, however, must bind the immutable outbound bytes rather
    than a registry object that a later callback could mutate.  The JSON
    round-trip both validates the schema and makes the inspected snapshot
    independent from the runtime runners.
    """
    return json.loads(json.dumps(to_openai(tools), ensure_ascii=False))
