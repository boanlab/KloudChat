"""Which empty sessions the work list hides and which it keeps."""

from __future__ import annotations

from datetime import timedelta

from app.models.chat import ChatSession, SessionKind
from app.models.user import utcnow
from app.routers import sessions as sessions_router


def _session(**kwargs) -> ChatSession:
    values = {
        "user_id": "u",
        "kind": SessionKind.chat,
        "title": "",
        "created_at": utcnow() - timedelta(hours=1),
    }
    values.update(kwargs)
    return ChatSession(**values)


def test_a_conversation_nobody_spoke_in_is_not_listed() -> None:
    row = _session(id="empty")

    assert sessions_router._worth_listing([row], {"empty"}) == []


def test_a_turn_in_flight_stays_in_the_list() -> None:
    """A freshly created empty session is listed while its first turn runs."""
    row = _session(id="running", created_at=utcnow())

    assert sessions_router._worth_listing([row], {"running"}) == [row]


def test_an_empty_conversation_holding_a_document_stays() -> None:
    """An empty session with an artifact is listed."""
    row = _session(id="made", artifact_id="a1")

    assert sessions_router._worth_listing([row], {"made"}) == [row]


def test_a_pinned_conversation_stays_however_empty() -> None:
    """A pinned session is listed however empty."""
    row = _session(id="kept", pinned=True)

    assert sessions_router._worth_listing([row], {"kept"}) == [row]


def test_conversations_with_messages_are_untouched() -> None:
    rows = [_session(id="a"), _session(id="b")]

    assert sessions_router._worth_listing(rows, set()) == rows
