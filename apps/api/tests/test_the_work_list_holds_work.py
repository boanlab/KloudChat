"""작업 목록에 실제로 한 일만 남는가.

The failure: opening 새 보고서 and changing your mind wrote a session row, and
the row stayed. It said 새 작업 — the label a title-less session gets — and it
led to a blank screen. Four hundred of them accumulated on one instance, which
is what somebody's sidebar looks like after two weeks of trying things: a
column of identical labels, none of which is the thing they came back for.

What must not happen is the opposite. A session is written before its first
message, so a turn in flight is an empty session for as long as the model takes
to answer, and hiding that one would take the conversation somebody is watching
out of their own sidebar.
"""

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
    """The row exists before the first message does. Hiding it would take the
    conversation somebody is watching out of their own sidebar."""
    row = _session(id="running", created_at=utcnow())

    assert sessions_router._worth_listing([row], {"running"}) == [row]


def test_an_empty_conversation_holding_a_document_stays() -> None:
    """On the surfaces where the answer is a file, the file is the record."""
    row = _session(id="made", artifact_id="a1")

    assert sessions_router._worth_listing([row], {"made"}) == [row]


def test_a_pinned_conversation_stays_however_empty() -> None:
    """Pinning is somebody saying keep this. That outranks a tidy list."""
    row = _session(id="kept", pinned=True)

    assert sessions_router._worth_listing([row], {"kept"}) == [row]


def test_conversations_with_messages_are_untouched() -> None:
    rows = [_session(id="a"), _session(id="b")]

    assert sessions_router._worth_listing(rows, set()) == rows
