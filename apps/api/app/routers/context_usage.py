"""How much of the model's window a conversation's next turn takes, for the composer's gauge.

Built from the pieces the turn itself uses (`routers.sessions._send_message`): the same
workspace assembly, tool list, `context.build_messages` envelope, `context.fit_history` cut and
cached summary, counted with `context.estimate_tokens`. No model is called and nothing is
written; per-turn extras the turn adds from the question's wording (a date rule, a forced
search's instructions, a table brief) are left out, so the figure is an estimate."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import Field

from app.core.deps import CurrentUser, DbSession
from app.models.chat import ChatSession, Message, Role, SessionKind
from app.routers import sessions as turn
from app.schemas.auth import Wire
from app.services import conversation_summary
from app.services.context import (
    answer_reserve,
    build_messages,
    carried_pastes,
    effective_window,
    envelope_tokens,
    estimate_tokens,
    fit_history,
    history_budget,
    summary_block,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])

#: Breakdown order, as the popover lists it.
PARTS = ("system", "instructions", "memory", "files", "history", "draft", "tools")


class ContextQuery(Wire):
    #: the conversation; None for one not started yet (only its base cost is counted)
    session_id: str | None = Field(default=None, max_length=64)
    kind: Literal["chat", "report", "slides"] = "chat"
    project_id: str | None = Field(default=None, max_length=64)
    agent_id: str | None = Field(default=None, max_length=64)
    #: the model picked in the composer; else the conversation's, the agent's, the default
    model: str | None = Field(default=None, max_length=200)
    #: the unsent question, counted as the next turn's question
    draft: str = Field(default="", max_length=200_000)
    attachments: list[str] = Field(default_factory=list, max_length=20)


class LastTurn(Wire):
    #: what the provider reported for the last answer (every tool round of it added up)
    input_tokens: int
    model: str | None = None


class ContextUsage(Wire):
    model: str
    #: the model's own window (`contextWindow`, else the deployment default)
    stated_window: int
    #: the window in estimate units the conversation is fitted to (`context.effective_window`)
    window: int
    #: true when the model counts with another tokenizer and the window is taken smaller
    foreign_tokenizer: bool
    #: kept free for the answer; past `window - reserve` older turns are summarised
    reserve: int
    used: int
    parts: dict[str, int]
    #: older turns already stand in as a summary on the next turn
    summarised: bool
    summarised_messages: int
    #: history tokens that still fit before the next cut
    headroom: int
    last_turn: LastTurn | None = None
    #: the model is chosen per turn by a router (Auto): the window shown is `routed_basis`'s
    routed: bool = False
    routed_basis: Literal["last", "default"] | None = None


def _last_turn(history: list[Message]) -> LastTurn | None:
    for row in reversed(history):
        if row.role is Role.assistant and isinstance(row.usage, dict):
            spent = int(row.usage.get("inputTokens") or 0)
            if spent > 0:
                return LastTurn(input_tokens=spent, model=row.model)
    return None


async def _model_for(db, user, session: ChatSession | None, query: ContextQuery, kind: str):
    """`(model, routed, basis)`: the turn's own precedence (picked, conversation, agent,
    default); under Auto the model the router last answered with, else its default."""
    catalogue = await turn.model_service.list_models_for_egress()
    models = catalogue["models"]
    usable = turn._allowed_models(user, models, kind=kind)
    routed = bool(session is not None and session.routing_mode in turn._AUTO_MODES)
    basis: Literal["last", "default"] | None = None
    wanted = query.model or (session.model if session else "")
    if routed and session is not None:
        last = next((row.model for row in reversed(await turn._history(db, session.id))
                     if row.role is Role.assistant and row.model), None)
        basis = "last" if last and turn.model_service.find(usable, last) else "default"
        wanted = last if basis == "last" else session.model
    if not wanted:
        try:
            agent_model, _tools, _temperature = await turn.agent_settings(
                db, user, session or _draft_session(user, query))
        except turn.WorkspaceContextError:
            agent_model = None
        wanted = agent_model or ""
    model = turn.model_service.find(usable, wanted) if wanted else None
    if model is None:
        model = turn.model_service.pick_default(usable, catalogue, kind)
    return model, routed, basis


def _draft_session(user, query: ContextQuery) -> ChatSession:
    """Stand-in for a conversation not started yet; never added to the database."""
    return ChatSession(id="", user_id=user.id, kind=SessionKind(query.kind),
                       project_id=query.project_id, agent_id=query.agent_id)


async def _tools(db, user, session: ChatSession, model: dict, agent_row) -> list[dict]:
    """Tool schemas the turn would offer, as `openai_snapshot` sends them."""
    if not model.get("supportsTools"):
        return []
    _model, allowed, _temperature = await turn.agent_settings(db, user, session)
    shelf, shelf_key = (await turn._knowledge_shelf(db, user, session, agent_row)
                        if session.id else ([], ""))
    strict = turn._strict_model(model)
    tools = await turn.build_tools(
        db, user, web_search=not strict, allowed=allowed, knowledge=shelf,
        knowledge_collection="" if strict else shelf_key,
        include_connectors=not strict, strict_local=strict,
    )
    return turn.openai_snapshot(tools)


async def usage(db, user, query: ContextQuery) -> ContextUsage:
    session = await turn._owned(db, user, query.session_id) if query.session_id else None
    kind = (session.kind if session else SessionKind(query.kind))
    if session is not None and await turn._office_report(db, session):
        kind = SessionKind.chat
    model, routed, basis = await _model_for(db, user, session, query, kind.value)
    if model is None:
        raise turn.HTTPException(status_code=503, detail="no_models_available")
    host = session or _draft_session(user, query)
    rows = await turn._history(db, session.id) if session else []
    agent_row = await db.get(turn.WorkspaceAgent, host.agent_id) if host.agent_id else None
    try:
        definitions = await _tools(db, user, host, model, agent_row) \
            if kind is SessionKind.chat else []
        standing_skills = (await turn._standing_skill_ids(db, user, host, agent_row)
                           if session else [])
        workspace = await turn.assemble(
            db, user, host,
            attachment_ids=query.attachments or None,
            vision=turn.reads_pictures(model),
            file_budget=turn.file_budget(model),
            activated_skill_ids=standing_skills,
            question=query.draft,
            focus=query.draft if kind is not SessionKind.chat else "",
            available_tool_names={tool["function"]["name"] for tool in definitions
                                  if isinstance(tool.get("function"), dict)},
            kind=kind,
        )
    except turn.WorkspaceContextError as exc:
        turn._raise_workspace_error(exc)

    trusted = list(workspace.trusted)
    untrusted = list(workspace.untrusted)
    attached: list[str] = []
    if kind is SessionKind.chat:
        attached = [b.text for b in workspace.blocks if b.source == "attachment"]
        untrusted = [b.text for b in workspace.blocks
                     if not b.trusted and b.source != "attachment"]

    wire = [{"role": row.role.value,
             "content": turn.server_notes.strip(row.content) if row.role is Role.assistant
             else row.content} for row in rows]
    question = {"role": "user", "content": query.draft}
    standing = turn.standing_directives([*wire, question]) if kind is SessionKind.chat else []

    def envelope(history: list[dict], extra: list[str], tools: bool = True) -> list[dict]:
        return build_messages(
            kind, history, with_tools=bool(definitions) and tools, extra=extra,
            untrusted_context=untrusted, turn_context=attached,
            remind_standing=kind is SessionKind.chat, standing=standing,
            light=turn.model_tier.strong(model["id"]),
            cache_order=turn.model_tier.cache_ordered(model["id"]),
        )

    window = effective_window(model)
    reserve = answer_reserve(window)
    fixed = envelope_tokens(envelope([question], trusted), definitions or None)
    budget = history_budget(window, fixed)
    anchor = turn._summary_anchor(session.summary, rows) if session else None
    kept, dropped = fit_history(wire, budget, keep_from=anchor)
    # material pasted into the cut requests rides along, excerpted, as the turn carries it
    carried = carried_pastes(
        dropped, query.draft, int(budget * turn._CARRIED_SHARE * 1.4)) if dropped else ""
    if carried:
        untrusted.append(carried)
        kept, dropped = fit_history(
            wire, max(0, budget - estimate_tokens(carried)), keep_from=anchor)
    memory_text = ""
    if dropped:
        cached = (session.summary if session else None) or {}
        memory_text = summary_block(
            str(cached.get("text") or "") or None, len(dropped),
            conversation_summary.pinned_lines(cached.get("pinned")),
        )
    sent = envelope([*kept, question], [*trusted, *([memory_text] if memory_text else [])])
    used = envelope_tokens(sent, definitions or None)

    by_source = {block.source: block for block in workspace.blocks}
    files = sum(estimate_tokens(b.text) for b in workspace.blocks
                if b.source.startswith("attachment") or b.source in (
                    "project.knowledge", "files.report"))
    memories = estimate_tokens(by_source["memory"].text) if "memory" in by_source else 0
    instructions = sum(estimate_tokens(text) for text in trusted) - sum(
        estimate_tokens(b.text) for b in workspace.blocks
        if b.trusted and b.source == "files.report")
    kept_tokens = envelope_tokens(kept)
    parts = {
        "instructions": max(0, instructions),
        "memory": memories + sum(estimate_tokens(text) for text in (memory_text, carried) if text),
        "files": files,
        "history": kept_tokens,
        "draft": envelope_tokens([question]) if query.draft else 0,
        "tools": envelope_tokens([], definitions) if definitions else 0,
    }
    # the rest is the deployment's own system prompt and the envelope's framing
    parts = {"system": max(0, used - sum(parts.values())), **parts}
    stated = int(model.get("contextWindow") or 0) or int(turn.settings.default_context_window)
    return ContextUsage(
        model=model["id"],
        stated_window=stated,
        window=window,
        foreign_tokenizer=not turn.model_tier.self_hosted(model["id"]),
        reserve=reserve,
        used=used,
        parts={name: parts[name] for name in PARTS},
        summarised=bool(dropped),
        summarised_messages=len(dropped),
        headroom=max(0, budget - (estimate_tokens(carried) if carried else 0) - kept_tokens),
        last_turn=_last_turn(rows),
        routed=routed,
        routed_basis=basis,
    )


@router.post("/context-usage", response_model=ContextUsage, response_model_by_alias=True)
async def context_usage(query: ContextQuery, user: CurrentUser, db: DbSession):
    """The next turn's share of the model's window; cheap — no model call, nothing written."""
    return await usage(db, user, query)
