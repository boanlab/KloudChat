"""A conversation longer than the model's window keeps its thread: the oldest turns
leave as a summary, this turn's attachment sits next to its question, and a window
the provider still refuses is named as such."""

from __future__ import annotations

import pytest

from app.models.chat import ChatSession, Message, Role, SessionKind
from app.models.user import User
from app.routers import sessions as sessions_router
from app.services import conversation_summary
from app.services.chat import ChatStreamError
from app.services.context import (
    TURN_CONTEXT_ACK,
    answer_reserve,
    build_messages,
    envelope_tokens,
    estimate_tokens,
    fit_history,
    history_budget,
    summary_block,
    system_prompt,
)


def _turns(n: int, *, chars: int = 400) -> list[dict]:
    """`n` exchanges of Korean prose, oldest first."""
    history: list[dict] = []
    for i in range(n):
        history.append({"role": "user", "content": f"질문 {i} " + "가" * chars})
        history.append({"role": "assistant", "content": f"답 {i} " + "나" * chars})
    return history


# ── counting ──────────────────────────────────────────────────────────────


def test_korean_is_counted_heavier_than_english_per_character():
    korean = "한" * 100
    english = "a" * 100
    assert estimate_tokens(korean) > estimate_tokens(english)
    # Close to one token a syllable, never under half.
    assert 60 <= estimate_tokens(korean) <= 100
    assert estimate_tokens("") == 0


def test_the_answer_reserve_is_bounded_both_ways():
    assert answer_reserve(8_000) == 2_048
    assert answer_reserve(32_768) == 32_768 // 6
    assert answer_reserve(1_000_000) == 8_192


def test_the_history_budget_is_what_is_left_after_the_fixed_parts():
    assert history_budget(32_768, fixed_tokens=10_000) == 32_768 - answer_reserve(32_768) - 10_000
    assert history_budget(8_000, fixed_tokens=9_000) == 0


# ── fitting ───────────────────────────────────────────────────────────────


def test_a_conversation_that_fits_is_sent_whole():
    history = _turns(3)
    kept, dropped = fit_history(history, budget=10**6)
    assert kept == history
    assert dropped == []


def test_the_oldest_exchanges_go_first_and_the_cut_opens_on_a_question():
    history = _turns(10)
    budget = sum(envelope_tokens([m]) for m in history) // 2
    kept, dropped = fit_history(history, budget)
    assert dropped and kept
    assert dropped + kept == history
    assert kept[0]["role"] == "user"
    # The newest turn is always among the kept.
    assert kept[-1] == history[-1]
    assert envelope_tokens(kept) <= budget


def test_a_cut_leaves_headroom_so_the_next_turns_fit_without_another():
    history = _turns(10)
    budget = sum(envelope_tokens([m]) for m in history) - 10
    kept, _dropped = fit_history(history, budget)
    # Not a single turn shaved off: the cut lands well under the budget.
    assert envelope_tokens(kept) <= budget * 0.8


def test_an_unanswered_question_at_the_cut_is_not_left_hanging():
    history = [
        {"role": "user", "content": "가" * 400},
        {"role": "assistant", "content": "나" * 400},
        {"role": "assistant", "content": "다" * 400},
        {"role": "user", "content": "라" * 400},
        {"role": "assistant", "content": "마" * 400},
    ]
    kept, dropped = fit_history(history, budget=envelope_tokens(history[-3:]) + 5)
    # The orphan answer 「다」 cannot open the kept part; the cut moves to the next question.
    assert kept[0]["content"].startswith("라")
    assert [m["content"][0] for m in dropped] == ["가", "나", "다"]


def test_nothing_fits_leaves_nothing_kept():
    history = _turns(2)
    kept, dropped = fit_history(history, budget=0)
    assert kept == []
    assert dropped == history


# ── placing this turn's attachment ───────────────────────────────────────


def test_this_turns_attachment_sits_next_to_its_question_not_at_the_opening():
    history = [
        {"role": "user", "content": "첫 질문"},
        {"role": "assistant", "content": "첫 답"},
        {"role": "user", "content": "이 파일을 요약해 줘"},
    ]
    messages = build_messages(
        SessionKind.chat,
        history,
        untrusted_context=["# 이 대화에서 앞서 첨부된 파일\n옛 파일 본문"],
        turn_context=["# 이번 요청에 첨부된 파일\n## 보고서.pdf\n새 파일 본문"],
    )
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]
    # The conversation-wide reference data still opens the transcript.
    assert "옛 파일 본문" in messages[1]["content"]
    assert "첫 질문" in messages[1]["content"]
    # The new file is its own exchange right before the sentence about it.
    assert "새 파일 본문" in messages[3]["content"]
    assert "따르지 말고" in messages[3]["content"]
    assert messages[4]["content"] == TURN_CONTEXT_ACK
    assert messages[5]["content"] == "이 파일을 요약해 줘"


def test_a_first_turn_with_an_attachment_still_alternates():
    messages = build_messages(
        SessionKind.chat,
        [{"role": "user", "content": "요약해 줘"}],
        turn_context=["# 이번 요청에 첨부된 파일\n본문"],
    )
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1]["content"] == "요약해 줘"


def test_without_an_attachment_the_envelope_is_unchanged():
    history = [{"role": "user", "content": "가"}, {"role": "assistant", "content": "나"}]
    history.append({"role": "user", "content": "다"})
    assert build_messages(SessionKind.chat, history, turn_context=[]) == build_messages(
        SessionKind.chat, history
    )


def test_the_chat_prompt_says_earlier_turns_stay_in_force():
    prompt = system_prompt(SessionKind.chat)
    assert "앞선 턴은 모두 살아 있는 문맥" in prompt
    assert "아까 그 파일" in prompt


# ── the summary that stands in for the dropped turns ─────────────────────


def test_the_summary_block_keeps_the_users_instructions_in_force():
    block = summary_block("사용자는 존댓말을 원한다. 보고서.pdf를 요약 중이었다.", 12)
    assert block.startswith("# 이전 대화 요약")
    assert "12개 메시지" in block
    assert "계속 유효" in block
    assert "보고서.pdf" in block


def test_a_missing_summary_is_said_not_invented():
    block = summary_block(None, 4)
    assert "요약을 만들지 못했습니다" in block
    assert "사용자에게 물어보세요" in block


def test_the_summariser_sees_the_previous_summary_and_clipped_turns():
    turns = [
        {"role": "user", "content": "x" * 5_000},
        {"role": "assistant", "content": "짧은 답"},
        {"role": "system", "content": ""},
    ]
    text = conversation_summary.prompt("앞선 요약", turns)
    assert "## 이전 요약\n앞선 요약" in text
    assert "사용자: " in text and "어시스턴트: 짧은 답" in text
    assert "이하 생략" in text
    # An empty turn adds no line.
    assert "시스템:" not in text
    assert "반드시 남길 것" in text


class _Db:
    def __init__(self):
        self.added: list[object] = []
        self.commits = 0

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1


def _rows(n: int) -> list[Message]:
    rows = []
    for i in range(n):
        rows.append(Message(id=f"q{i}", session_id="s1", role=Role.user, content=f"질문 {i}"))
        rows.append(Message(id=f"a{i}", session_id="s1", role=Role.assistant, content=f"답 {i}"))
    return rows


def _wire(rows: list[Message]) -> list[dict]:
    return [{"role": row.role.value, "content": row.content} for row in rows]


def _patch_summariser(monkeypatch, calls: list[tuple[str | None, list[dict]]]):
    async def enrichment(writer, **_kwargs):
        return writer

    async def summarize(_model, previous, turns, _key, **_kwargs):
        calls.append((previous, turns))
        return f"요약({len(turns)}개 추가)", {"inputTokens": 100, "outputTokens": 20}

    monkeypatch.setattr(sessions_router, "_enrichment_model", enrichment)
    monkeypatch.setattr(sessions_router.conversation_summary, "summarize", summarize)


async def _summarise(db, session, rows, monkeypatch_calls=None):
    return await sessions_router._conversation_summary(
        db,
        session,
        rows=rows,
        turns=_wire(rows),
        writer={"id": "model-a", "creditCost": 1, "inputCreditCost": 1},
        api_key="key",
        strict_local=False,
        disable_fallbacks=False,
        masker=None,
        mask_at_rest=False,
        redact_logging=False,
    )


@pytest.mark.asyncio
async def test_the_summary_is_written_once_and_then_grown_not_rewritten(monkeypatch):
    calls: list[tuple[str | None, list[dict]]] = []
    _patch_summariser(monkeypatch, calls)
    db = _Db()
    session = ChatSession(id="s1", user_id="u1", kind=SessionKind.chat)
    rows = _rows(4)

    # First overflow: everything dropped is summarised, and cached by its last message.
    text, credits = await _summarise(db, session, rows[:4])
    assert text == "요약(4개 추가)"
    assert credits > 0
    assert session.summary == {"through": "a1", "text": "요약(4개 추가)", "turns": 4}
    assert db.commits == 1
    assert [len(turns) for _, turns in calls] == [4]

    # Same cut again (the next turn still fits): nothing is called.
    text, credits = await _summarise(db, session, rows[:4])
    assert (text, credits) == ("요약(4개 추가)", 0)
    assert len(calls) == 1

    # The cut moves down two more messages: only those two join the previous summary.
    text, _credits = await _summarise(db, session, rows[:6])
    assert len(calls) == 2
    previous, turns = calls[-1]
    assert previous == "요약(4개 추가)"
    assert [turn["content"] for turn in turns] == ["질문 2", "답 2"]
    assert session.summary["through"] == "a2"


@pytest.mark.asyncio
async def test_a_summary_that_could_not_be_written_is_not_cached(monkeypatch):
    async def enrichment(writer, **_kwargs):
        return writer

    async def summarize(*_args, **_kwargs):
        return None, {"inputTokens": 50, "outputTokens": 0}

    monkeypatch.setattr(sessions_router, "_enrichment_model", enrichment)
    monkeypatch.setattr(sessions_router.conversation_summary, "summarize", summarize)
    db = _Db()
    session = ChatSession(id="s1", user_id="u1", kind=SessionKind.chat)

    text, credits = await _summarise(db, session, _rows(2))

    assert text is None
    # The tokens were still spent.
    assert credits > 0
    assert session.summary is None
    assert db.commits == 0


@pytest.mark.asyncio
async def test_a_stale_cache_whose_anchor_was_edited_away_starts_over(monkeypatch):
    calls: list[tuple[str | None, list[dict]]] = []
    _patch_summariser(monkeypatch, calls)
    db = _Db()
    session = ChatSession(
        id="s1", user_id="u1", kind=SessionKind.chat,
        summary={"through": "gone", "text": "옛 요약", "turns": 2},
    )

    await _summarise(db, session, _rows(2))

    previous, turns = calls[-1]
    assert previous is None
    assert len(turns) == 4


def test_the_timeline_names_what_happened_to_the_old_turns():
    step = sessions_router._summary_context_step(8, summarised=True)
    assert step["label"] == "앞선 대화 8개 메시지를 요약해 전달"
    assert step["summarisedTurns"] == 8
    assert step["type"] == "thinking" and step["status"] == "done"
    assert "생략" in sessions_router._summary_context_step(8, summarised=False)["label"]


# ── the window the provider still refuses ────────────────────────────────


@pytest.mark.parametrize(
    "failure",
    [
        "upstream_400: This model's maximum context length is 32768 tokens.",
        "upstream_400: {\"error\":{\"message\":\"ContextWindowExceededError: ...\"}}",
        "upstream_413: prompt is too long: 210000 tokens > 200000 maximum",
        "upstream_422: input length and `max_tokens` exceed context limit",
    ],
)
def test_a_prompt_that_does_not_fit_is_recognised(failure):
    assert sessions_router._context_overflow(failure)


def test_other_refusals_are_not_mistaken_for_it():
    assert not sessions_router._context_overflow("upstream_400: invalid tool schema")
    assert not sessions_router._context_overflow("upstream_500: context length unknown")
    assert not sessions_router._context_overflow("upstream_429: rate limit")


def test_the_error_event_can_carry_a_more_precise_code():
    exc = ChatStreamError("upstream_400: maximum context length is 32768 tokens")
    event = sessions_router._error_event("문맥을 넘었습니다.", exc, code="context_length_exceeded")
    assert event["code"] == "context_length_exceeded"
    assert event["reason"].startswith("maximum context length")
    # Without an override, the upstream status still names the failure.
    assert sessions_router._error_event("실패", exc)["code"] == "upstream_400"
    assert sessions_router._error_event("실패")["code"] == "internal_error"


# ── a ballpark ask is not a live lookup ──────────────────────────────────


@pytest.mark.parametrize(
    "request_text",
    [
        "아까 그 기차 얘기로 돌아가서, 왕복 요금은 보통 얼마나 해?",
        "서울에서 부산까지 KTX 요금은 대략 얼마야?",
        "How much does a KTX ticket usually cost?",
    ],
)
def test_a_ballpark_price_question_gets_an_ordinary_answer(request_text):
    from app.services.freshness import current_fact_required

    assert not current_fact_required(request_text)


@pytest.mark.parametrize(
    "request_text",
    [
        "지금 KTX 요금은 보통 얼마야?",
        "현재 비트코인 가격은 보통 얼마야?",
        "올해 KTX 요금은 대략 얼마야?",
        "현재 대한민국 대통령은 보통 누구야?",
    ],
)
def test_a_live_marker_still_makes_it_a_current_fact(request_text):
    from app.services.freshness import current_fact_required

    assert current_fact_required(request_text)


# ── a rerun keeps the answer it replaces ─────────────────────────────────


@pytest.mark.asyncio
async def test_regenerating_an_answered_question_carries_the_old_answer(monkeypatch):
    from test_retry_in_place import _capture_chat_turn, _chat, _question, _reply, _send, _Transcript

    from app.schemas.chat import SendMessage

    question = _question("q1", "전이학습이 뭐야?")
    answer = _reply("a1", "첫 번째 답")
    answer.model = "model-a"
    answer.usage = {"inputTokens": 10, "outputTokens": 20, "credits": 1}
    db = _Transcript(_chat(), [question, answer])
    captured = _capture_chat_turn(monkeypatch)

    await _send(db, SendMessage(content="전이학습이 뭐야?", retry_of="q1"))

    # The old row stays until the new answer is stored; its words travel with the turn.
    assert db.deleted == []
    assert captured["superseded_ids"] == ["a1"]
    carried = captured["earlier_answers"]
    assert [c["content"] for c in carried] == ["첫 번째 답"]
    assert carried[0]["model"] == "model-a"
    assert carried[0]["usage"]["credits"] == 1
    # The model is asked the question once, with no stale answer in its history.
    wire = [m for m in captured["messages"] if m["role"] in ("user", "assistant")]
    assert [m["content"] for m in wire] == ["전이학습이 뭐야?"]


@pytest.mark.asyncio
async def test_a_second_rerun_keeps_both_earlier_answers_in_order(monkeypatch):
    from test_retry_in_place import _capture_chat_turn, _chat, _question, _reply, _send, _Transcript

    from app.schemas.chat import SendMessage

    question = _question("q1", "질문")
    second = _reply("a2", "둘째 답")
    second.superseded = [
        {"id": "a1", "content": "첫째 답", "model": None, "usage": None, "createdAt": None}
    ]
    db = _Transcript(_chat(), [question, second])
    captured = _capture_chat_turn(monkeypatch)

    await _send(db, SendMessage(content="질문", retry_of="q1"))

    assert [c["content"] for c in captured["earlier_answers"]] == ["첫째 답", "둘째 답"]


@pytest.mark.asyncio
async def test_a_failed_answer_is_not_carried_as_an_earlier_answer(monkeypatch):
    from test_retry_in_place import _capture_chat_turn, _chat, _question, _reply, _send, _Transcript

    from app.models.chat import TurnFailure
    from app.schemas.chat import SendMessage

    question = _question("q1", "질문")
    partial = _reply("a1", "쓰다 말았", failure=TurnFailure.interrupted)
    db = _Transcript(_chat(), [question, partial])
    captured = _capture_chat_turn(monkeypatch)

    await _send(db, SendMessage(content="질문", retry_of="q1"))

    assert captured["earlier_answers"] is None
    assert captured["superseded_ids"] is None
    # A failed reply still goes at once, as before.
    assert db.deleted == [partial]


def test_an_earlier_answer_keeps_its_steps_artifacts_and_route():
    from app.models.chat import MessageRating

    row = Message(
        id="a1", session_id="s1", role=Role.assistant, content="답",
        model="m", usage={"credits": 2}, steps=[{"id": "s", "type": "tool"}],
        artifact_ids=["art1"], routing={"action": "none"}, rating=MessageRating.up,
    )
    carried = sessions_router._earlier_answer(row)
    assert carried["steps"] == [{"id": "s", "type": "tool"}]
    assert carried["artifactIds"] == ["art1"]
    assert carried["routing"] == {"action": "none"}
    assert carried["rating"] == "up"


# ── one answer at a time ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_message_while_the_answer_is_still_being_written_is_refused(monkeypatch):
    import asyncio

    from test_retry_in_place import _capture_chat_turn, _chat, _send, _Transcript

    from app.schemas.chat import SendMessage

    db = _Transcript(_chat(), [])
    _capture_chat_turn(monkeypatch)
    live = asyncio.Event()
    sessions_router._STOPPING["session-1"] = {live}
    try:
        with pytest.raises(sessions_router.HTTPException) as refused:
            await _send(db, SendMessage(content="두 번째 질문"))
        assert refused.value.status_code == 409
        assert refused.value.detail == "session_busy"
        assert db.added == []
    finally:
        sessions_router._STOPPING.pop("session-1", None)


@pytest.mark.asyncio
async def test_a_message_after_stop_goes_through(monkeypatch):
    import asyncio

    from test_retry_in_place import _capture_chat_turn, _chat, _send, _Transcript

    from app.schemas.chat import SendMessage

    db = _Transcript(_chat(), [])
    captured = _capture_chat_turn(monkeypatch)
    stopped = asyncio.Event()
    stopped.set()
    sessions_router._STOPPING["session-1"] = {stopped}
    try:
        await _send(db, SendMessage(content="중단 뒤의 질문"))
        assert captured["first_user_message"] == "중단 뒤의 질문"
    finally:
        sessions_router._STOPPING.pop("session-1", None)


def test_a_recent_unanswered_question_counts_as_busy_on_any_replica():
    from datetime import timedelta

    from app.models.user import utcnow

    fresh = Message(id="q", session_id="s1", role=Role.user, content="?")
    assert sessions_router._answer_in_flight(fresh)
    stale = Message(id="q", session_id="s1", role=Role.user, content="?")
    stale.created_at = utcnow() - timedelta(seconds=10_000)
    assert not sessions_router._answer_in_flight(stale)
    answered = Message(id="a", session_id="s1", role=Role.assistant, content="답")
    assert not sessions_router._answer_in_flight(answered)
    failed = Message(id="q", session_id="s1", role=Role.user, content="?")
    failed.failure = sessions_router.TurnFailure.no_answer
    assert not sessions_router._answer_in_flight(failed)


def test_a_large_drop_is_summarised_in_batches_from_the_start():
    turns = [{"role": "user", "content": "가" * 3_000} for _ in range(12)]
    batches = conversation_summary._batches(turns)
    assert len(batches) >= 3
    assert sum(len(b) for b in batches) == 12
    # The first batch opens on the first turn: nothing is clipped off the front.
    assert batches[0][0] is turns[0]
    assert all(len(conversation_summary._transcript(b)) <= 14_000 + 200 for b in batches)


# ── a stop broadcast does not come back to bite its sender ───────────────


def test_a_process_ignores_the_echo_of_its_own_stop_broadcast():
    from app.services import stop_signal

    own = stop_signal.payload_for("s1")
    assert own.endswith(":s1")
    # The echo of this process's own NOTIFY names no session to stop.
    assert stop_signal.session_from(own) is None
    # Another process's broadcast does.
    assert stop_signal.session_from("otherprocess:s1") == "s1"
    # A bare session id from an older build is still honoured.
    assert stop_signal.session_from("s1") == "s1"
    assert stop_signal.session_from("") is None


# ── knowledge: excerpts keep their case, follow the question, and are searchable ──


def test_an_excerpt_keeps_the_documents_own_case():
    from app.services.workspace_context import _excerpt

    filler = ("배경 설명 문단입니다. " * 60 + "\n") * 6
    text = filler + "ACME Corp Annual Report: 제290조 특례 조항은 2024년에 개정되었다.\n" + filler
    picked = _excerpt(text, 1_200, "제290조 특례")
    assert "ACME Corp Annual Report" in picked
    assert "acme corp" not in picked


def test_a_follow_up_that_names_nothing_borrows_the_previous_question():
    from app.models.chat import Message, Role

    history = [
        Message(id="q1", session_id="s", role=Role.user, content="히트율 표에서 캐시 32 값은?"),
        Message(id="a1", session_id="s", role=Role.assistant, content="0.718"),
    ]
    focus = sessions_router._asked_about("그 표의 두 번째 행은?", history)
    assert focus.startswith("그 표의 두 번째 행은?")
    assert "히트율" in focus
    assert sessions_router._asked_about("첫 질문", []) == "첫 질문"


@pytest.mark.asyncio
async def test_project_files_join_the_search_shelf_and_a_shared_agents_vectors_stay_home():
    from app.models.user import User, UserStatus
    from app.models.workspace import Agent, StoredFile

    class Result:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return list(self._rows)

    class Db:
        def __init__(self, rows, project=None):
            self.rows = rows
            self.project = project

        async def exec(self, _query):
            return Result(self.rows)

        async def get(self, _model, _row_id):
            return self.project

    me = User(
        id="me", email="me@example.com", password_hash="x", name="Me",
        status=UserStatus.active, monthly_credits=1,
    )
    rows = [
        StoredFile(
            id="p", user_id="me", project_id="proj", name="policy.md", size=1,
            mime="text/markdown", text="연차 15일",
        ),
        StoredFile(
            id="s", user_id="me", session_id="sess", name="note.md", size=1,
            mime="text/markdown", text="메모",
        ),
        StoredFile(
            id="o", user_id="me", project_id="other", name="x.md", size=1,
            mime="text/markdown", text="딴 프로젝트",
        ),
    ]
    from app.models.workspace import Project

    session = ChatSession(
        id="sess", user_id="me", kind=SessionKind.chat, project_id="proj", index_key="k-sess"
    )
    # A project conversation searches the project's collection, which also holds its uploads.
    project = Project(id="proj", user_id="me", name="P", index_key="k-proj")
    shelf, key = await sessions_router._knowledge_shelf(Db(rows, project), me, session, None)
    assert [name for name, _, _ in shelf] == ["policy.md", "note.md"]
    # Both the project's collection and the chat's own, for uploads indexed before it.
    assert key == "k-proj,k-sess"
    # No project collection yet: the conversation's own.
    _shelf, key = await sessions_router._knowledge_shelf(
        Db(rows, Project(id="proj", user_id="me", name="P")), me, session, None
    )
    assert key == "k-sess"

    # A shared agent run by someone else: lexical shelf of their own uploads, no owner collection.
    shared = Agent(id="ag", owner_id="author", name="봇", index_key="k-author")
    with_agent = ChatSession(id="sess", user_id="me", kind=SessionKind.chat, agent_id="ag")
    _shelf, key = await sessions_router._knowledge_shelf(Db([]), me, with_agent, shared)
    assert key == ""
    mine = Agent(id="ag", owner_id="me", name="봇", index_key="k-me")
    _shelf, key = await sessions_router._knowledge_shelf(Db([]), me, with_agent, mine)
    assert key == "k-me"


def test_hwpx_table_text_is_read_once_and_xlsx_inline_strings_are_read():
    import io
    import zipfile

    from app.services.files import _from_hwpx, _from_xlsx

    section = (
        '<hs:sec xmlns:hp="urn:hp" xmlns:hs="urn:hs">'
        "<hp:p><hp:run><hp:t>본문 첫 문단</hp:t></hp:run></hp:p>"
        "<hp:p><hp:run><hp:tbl><hp:tr><hp:tc><hp:subList>"
        "<hp:p><hp:run><hp:t>셀 하나</hp:t></hp:run></hp:p>"
        "</hp:subList></hp:tc></hp:tr></hp:tbl></hp:run></hp:p>"
        "</hs:sec>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("Contents/section0.xml", section)
    text = _from_hwpx(buf.getvalue())
    assert text.count("셀 하나") == 1
    assert "본문 첫 문단" in text

    sheet = (
        '<worksheet><sheetData><row r="1">'
        '<c r="A1" t="inlineStr"><is><t>요금제</t></is></c>'
        '<c r="B1"><v>9900</v></c>'
        "</row></sheetData></worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    assert "요금제\t9900" in _from_xlsx(buf.getvalue())


# ── a stuck decoder is caught however the chunks are cut ─────────────────


def test_a_stream_of_one_punctuation_mark_is_stopped():
    from app.services.agent import _is_looping, _runaway

    one_at_a_time = ["!"] * 3000
    assert _runaway(one_at_a_time) is not None
    assert _is_looping(one_at_a_time)
    # Letters and digits as before, and a Hangul syllable.
    assert _runaway(["0"] * 50) is not None
    assert _runaway(["아"] * 50) is not None
    # Code banners and markdown rules are not a stuck decoder.
    assert _runaway(["/" + "*" * 60 + "/"]) is None
    assert _runaway(["=" * 80]) is None
    assert _runaway(["-" * 80]) is None
    assert _runaway(["단어 "] * 50) is None


def test_a_file_grounded_price_question_is_read_not_withheld():
    from app.services.freshness import names_the_present
    from app.services.workspace_context import ContextFile, WorkspaceContext

    assert not names_the_present("Pro 요금제는 한 달에 얼마야?")
    assert names_the_present("지금 Pro 요금제는 얼마야?")
    assert names_the_present("올해 요금은?")
    with_file = WorkspaceContext(
        (), (), knowledge=(ContextFile("pricing.csv", "included", 100, 100),)
    )
    assert sessions_router._grounded_in_files(with_file)
    omitted = WorkspaceContext((), (), knowledge=(ContextFile("x.csv", "omitted", 0, 100),))
    assert not sessions_router._grounded_in_files(omitted)
    assert not sessions_router._grounded_in_files(WorkspaceContext((), ()))


# ── skills stay on for the conversation ──────────────────────────────────


@pytest.mark.asyncio
async def test_a_skill_picked_once_applies_to_the_next_turn_too(monkeypatch):
    from test_retry_in_place import _capture_chat_turn, _chat, _send, _Transcript
    from test_starting_points import _skill

    from app.schemas.chat import SendMessage

    skill = _skill()
    db = _Transcript(_chat(), [])
    db.skills = [skill]
    captured = _capture_chat_turn(monkeypatch)

    # Picked for this turn: applied, and remembered on the conversation.
    await _send(db, SendMessage(content="첫 질문", activated_skill_ids=[skill.id]))
    assert [s["id"] for s in captured["skills_event"]["skills"]] == [skill.id]
    assert db.session.skill_ids == [skill.id]

    # The next turn names no skill and still runs with it.
    await _send(db, SendMessage(content="다음 질문"))
    assert [s["id"] for s in captured["skills_event"]["skills"]] == [skill.id]


@pytest.mark.asyncio
async def test_a_conversation_with_no_standing_skills_runs_plain(monkeypatch):
    from test_retry_in_place import _capture_chat_turn, _chat, _send, _Transcript

    from app.schemas.chat import SendMessage

    db = _Transcript(_chat(), [])
    captured = _capture_chat_turn(monkeypatch)
    await _send(db, SendMessage(content="질문"))
    assert captured["skills_event"] is None
    assert db.session.skill_ids is None


def test_project_knowledge_takes_what_the_files_left_but_never_less_than_a_quarter():
    from app.services.workspace_context import knowledge_budget_after

    assert knowledge_budget_after(100_000, 0) == 100_000
    assert knowledge_budget_after(100_000, 60_000) == 40_000
    # Attachments ate the budget: the knowledge still gets its quarter, excerpted.
    assert knowledge_budget_after(100_000, 95_000) == 25_000
    assert knowledge_budget_after(24_000, 24_000) == 6_000


# ── a skill the model reaches for by itself ──────────────────────────────


@pytest.mark.asyncio
async def test_use_skill_hands_back_the_installed_skills_instructions():
    from app.services.tools.builtin import skill_tool

    tool = skill_tool([
        (
            "회의록 정리",
            "회의 녹취나 대화를 결정·조치·미결로 나눌 때",
            "결정사항, 조치사항, 미결을 표로.",
        ),
        ("쉬운 설명", "개념을 처음 배우는 사람에게", ""),
    ])
    assert tool.name == "use_skill" and tool.read_only
    assert "「회의록 정리」" in tool.description and "결정·조치·미결" in tool.description
    result = await tool.run({"name": "회의록 정리"})
    assert not result.failed
    assert "결정사항, 조치사항, 미결을 표로." in result.content
    assert result.detail == "「회의록 정리」 적용"
    # Skills are used silently: never advertised, never narrated when they do not fit.
    assert "언급하거나 권하지 않습니다" in tool.description
    assert "사용자는 결과만 봅니다" in result.content
    # A skill with no body falls back to its description; an unknown name is refused.
    assert "처음 배우는" in (await tool.run({"name": "「쉬운 설명」"})).content
    missing = await tool.run({"name": "없는 스킬"})
    assert missing.failed and "회의록 정리" in missing.content


@pytest.mark.asyncio
async def test_installed_skills_not_switched_on_are_offered_through_use_skill(monkeypatch):
    from test_retry_in_place import _capture_chat_turn, _chat, _send, _Transcript
    from test_starting_points import _skill

    from app.schemas.chat import SendMessage

    picked = _skill()
    other = _skill()
    other.id, other.name, other.when_to_use = "skill-2", "회의록 정리", "회의 녹취를 정리할 때"
    db = _Transcript(_chat(), [])
    db.skills = [picked, other]
    captured = _capture_chat_turn(monkeypatch)
    # The harness's models do not support tools, so the shelf itself is what we check.
    from app.models.user import User, UserStatus

    me = User(
        id="user-1", email="u@example.com", password_hash="x", name="U",
        status=UserStatus.active, monthly_credits=1,
    )
    shelf = await sessions_router._skill_shelf(db, me, exclude={picked.id})
    assert [name for name, _, _ in shelf] == ["회의록 정리"]
    # The harness serves every skill row to every query; a send resolves only the picked one.
    db.skills = [picked]
    await _send(db, SendMessage(content="질문", activated_skill_ids=[picked.id]))
    assert [s["id"] for s in captured["skills_event"]["skills"]] == [picked.id]


@pytest.mark.asyncio
async def test_a_standing_skill_that_was_removed_drops_out_instead_of_failing_every_turn():
    from test_retry_in_place import _chat, _Transcript
    from test_starting_points import _skill

    from app.models.user import User, UserStatus
    from app.models.workspace import Agent

    me = User(
        id="user-1", email="u@example.com", password_hash="x", name="U",
        status=UserStatus.active, monthly_credits=1,
    )
    kept = _skill()
    disabled = _skill()
    disabled.id, disabled.enabled = "skill-off", False
    session = _chat()
    session.skill_ids = [kept.id, "skill-gone", disabled.id]
    db = _Transcript(session, [])
    db.skills = [kept, disabled]
    assert await sessions_router._standing_skill_ids(db, me, session, None) == [kept.id]
    assert session.skill_ids == [kept.id]
    # An agent that does not allow the kept skill bars it as well.
    agent = Agent(id="a", owner_id="user-1", user_id="user-1", name="A", skill_ids=["other"])
    assert await sessions_router._standing_skill_ids(db, me, session, agent) == []


@pytest.mark.asyncio
async def test_the_use_skill_shelf_honours_the_agents_allowlist():
    from test_retry_in_place import _chat, _Transcript
    from test_starting_points import _skill

    from app.models.user import User, UserStatus
    from app.models.workspace import Agent

    me = User(
        id="user-1", email="u@example.com", password_hash="x", name="U",
        status=UserStatus.active, monthly_credits=1,
    )
    allowed = _skill()
    barred = _skill()
    barred.id, barred.name = "skill-barred", "금지된 스킬"
    db = _Transcript(_chat(), [])
    db.skills = [allowed, barred]
    agent = Agent(id="a", owner_id="user-1", user_id="user-1", name="A", skill_ids=[allowed.id])
    shelf = await sessions_router._skill_shelf(db, me, agent=agent, exclude=set())
    assert [name for name, _, _ in shelf] == [allowed.name]


def test_only_a_verification_or_date_hedge_counts_as_a_caveat():
    from app.services.current_evidence import has_caveat

    assert not has_caveat("이 시점에서 가장 널리 쓰이는 버전은 3.12입니다.")
    assert not has_caveat("성능 기준으로 보면 3.12가 최신입니다.")
    assert has_caveat("학습 시점 기준으로 3.12가 최신이며, 이후 바뀔 수 있습니다.")
    assert has_caveat("2025년 6월 기준 3.13입니다.")


def test_the_style_guide_carries_no_worked_example_the_model_could_quote_as_fact():
    """A transfer-learning example in the guide came back as "내 300장" in an answer
    about transfer learning before the person ever mentioned 300 photos."""
    from app.services import context

    source = open(context.__file__, encoding="utf-8").read()
    assert "300장" not in source
    assert "형태**를 보이는 예시" in source
    assert "one-off" in context._CHAT_TASK_CONTRACT


def test_a_block_written_twice_in_a_row_is_detected_with_its_exact_tail():
    from app.services.agent import _repeated_tail

    block = "알겠습니다. 앞으로 이 대화에서는 모든 답을 세 문장 이내로 쓰겠습니다. 이상입니다."
    text = block + "\n\n\n" + block
    assert _repeated_tail(text) == "\n\n\n" + block
    assert text[: len(text) - len(_repeated_tail(text))] == block
    # Different second paragraph, a short text, or one copy: nothing to take back.
    assert _repeated_tail(block + "\n\n다음 질문을 주세요.") == ""
    assert _repeated_tail("네.\n\n네.") == ""
    assert _repeated_tail(block) == ""


@pytest.mark.asyncio
async def test_the_agent_takes_back_a_repeated_answer_while_streaming(monkeypatch):
    from test_freshness_calculation_integration import _execute

    block = "알겠습니다. 앞으로 이 대화에서는 모든 답을 세 문장 이내로 쓰겟습니다. 이상입니다."
    captured = {
        "preflight_tool": None,
        "calculation_required": False,
        "freshness_request": "",
        "messages": [{"role": "user", "content": "규칙을 정해 줘"}],
        "model": {"id": "synthetic/model"},
    }
    _, _, events = await _execute(
        monkeypatch, [{"text": block + "\n\n\n" + block}], tools=[], captured=captured
    )
    visible = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    for e in events:
        if e["type"] == "retract":
            visible = visible.replace(e["text"], "", 1)
    assert visible == block
    assert any(e["type"] == "retract" for e in events)


def test_standing_directives_are_replayed_next_to_the_question():
    from app.services.context import (
        STANDING_ACK,
        build_messages,
        standing_directives,
    )

    rule = (
        "앞으로 이 대화에서는 모든 답을 세 문장 이내로 쓰고, "
        "답 끝에 꼭 「이상입니다.」라고 붙여 줘."
    )
    history = [
        {"role": "user", "content": rule},
        {"role": "assistant", "content": "알겠습니다. 이상입니다."},
        {"role": "user", "content": "전이학습이 뭐야?"},
        {"role": "assistant", "content": "…"},
        {"role": "user", "content": "그걸 300장에 쓰면?"},
    ]
    assert standing_directives(history) == [rule]
    out = build_messages(SessionKind.chat, history, remind_standing=True)
    roles = [m["role"] for m in out]
    # system, (rule, ack, Q, A), reminder, ack, question
    assert out[-1]["content"] == "그걸 300장에 쓰면?"
    assert out[-2]["content"] == STANDING_ACK
    assert rule in out[-3]["content"] and out[-3]["role"] == "user"
    assert roles[-4:] == ["assistant", "user", "assistant", "user"]
    # Off by default, and a document surface never gets it.
    assert all(STANDING_ACK != m["content"] for m in build_messages(SessionKind.chat, history))


def test_standing_directives_skip_tasks_withdrawals_and_the_live_question():
    from app.services.context import standing_directives

    long_task = "앞으로 " + "아주 긴 작업 설명 " * 60
    history = [
        {"role": "user", "content": long_task},
        {"role": "assistant", "content": "…"},
        {"role": "user", "content": "항상 영어로 답해."},
        {"role": "assistant", "content": "OK."},
        {"role": "user", "content": "규칙은 취소. 원래대로 해 줘."},
        {"role": "assistant", "content": "네."},
        {"role": "user", "content": "이제부터 반말로 해."},
        {"role": "assistant", "content": "응."},
        {"role": "user", "content": "앞으로 뭘 하면 좋을까?"},
    ]
    assert standing_directives(history) == ["이제부터 반말로 해."]


def test_a_ballpark_question_about_a_changing_value_is_marked_for_a_caveat():
    from app.services import freshness

    q = "아까 그 기차 얘기로 돌아가서, 왕복 요금은 보통 얼마나 해?"
    assert not freshness.current_fact_required(q)
    assert freshness.ballpark_current_value(q)
    assert freshness.ballpark_current_value("Roughly what is the price of a KTX ticket?")
    # Definitions, history and non-value questions carry no caveat.
    assert not freshness.ballpark_current_value("보통 LRU 캐시는 어떻게 동작해?")
    assert not freshness.ballpark_current_value("1990년 지하철 요금은 보통 얼마였어?")


@pytest.mark.asyncio
async def test_a_ballpark_answer_streams_live_and_closes_with_the_caveat(monkeypatch):
    from test_freshness_calculation_integration import _mock_model

    from app.services import agent, current_evidence
    from app.services.tools.base import ToolContext

    q = "왕복 요금은 보통 얼마나 해?"
    body = "서울-부산 KTX 왕복은 보통 12만 원 안팎입니다."
    snapshots = []
    _mock_model(monkeypatch, [{"text": body}], snapshots)
    events = [e async for e in agent.run_turn(
        "synthetic/model",
        [{"role": "user", "content": q}],
        [],
        ToolContext(user_id="u", session_id="s"),
        caveat_request=q,
    )]
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert deltas[0] == body  # the answer itself went out as it was spoken, not held
    assert "".join(deltas).endswith(current_evidence.caveat(q))
    # The model was told to give its best figure and mark it as from training data.
    sent = str(snapshots[0])
    assert "학습" in sent or "training" in sent


@pytest.mark.parametrize(
    "request_text",
    [
        "오늘 하루 일 좀 도와줘. 규칙 하나: 내가 '요약'이라고만 쓰면 "
        "바로 앞 답을 두 문장으로 줄여 줘.",
        "다른 얘기. 내일 발표 10분인데 슬라이드 몇 장이 적당해?",
        "아, 발표는 10분이 아니라 20분이야. 다시 계산해 줘.",
        "오늘 처음에 내가 정한 규칙이 뭐였지?",
        "모든 값이 비어 있으면 지금 코드는 어떻게 돼?",
        "아 미안, 이거: 월요일 회의 10시, 수요일 치과 3시, 금요일 보고서 마감, "
        "목요일 저녁 민수랑 약속 7시.",
        "서울역에서 부산역까지 KTX로 가면 보통 몇 시간쯤 걸려?",
        "아까 그 기차 얘기로 돌아가서, 왕복 요금은 보통 얼마나 해?",
        "우리 팀 남은 예산은 1200만 원이고, 서버 비용이 매달 150만 원씩 나가.",
        "서버 비용을 20% 줄이면 얼마나 더 버틸 수 있어?",
        "오늘 회의 메모를 정리할 건데, 먹을 거 비유로 기억해 둘게. "
        "우리 제품 코드명은 「도토리」야. 알겠지?",
        "첫 안건: 출시일은 11월 14일로 잡혔어. 짧게 확인만.",
        "그럼 마케팅 금액은 얼마야?",
        "자, 이제 지금까지 회의 메모를 한눈에 정리해 줘. "
        "코드명, 출시일, 담당자, 예산, 다음 회의를 전부 포함해서.",
    ],
)
def test_auto_search_is_not_forced_on_the_persons_own_matters(request_text):
    """Every one of these forced a web search in auto mode; none asks about the world."""
    from app.services.context import needs_web_search, search_plan

    assert not needs_web_search(request_text)
    assert search_plan("auto", request_text) == (True, None)


@pytest.mark.parametrize(
    "request_text",
    [
        "오늘 비트코인 시세 얼마야?",
        "파이썬 최신 버전이 뭐야?",
        "올해 전기요금 얼마나 올랐어?",
        "내일 KTX 운행 일정 바뀐 거 있어?",
        "React 19에서 달라진 점 알려 줘.",
        "최근 LLM 보안 뉴스 정리해 줘.",
    ],
)
def test_auto_search_is_still_forced_on_the_worlds_changing_facts(request_text):
    from app.services.context import needs_web_search

    assert needs_web_search(request_text)


# ── documents: revising one part the way the person said ────────────────


def test_a_layout_word_followed_by_ro_names_the_layout_the_slide_should_become():
    from app.services.deck import requested_layout

    assert requested_layout("다음 단계 장을 연표로 바꿔 줘. 9/8 배포, 9/22 오픈.") == "timeline"
    assert requested_layout("오버헤드 결과 장을 막대 차트로 바꿔 줘. 단위 %.") == "chart"
    assert requested_layout("발생 조건 장을 네 단계로 바꿔 줘.") == "steps"
    assert requested_layout("결론 장을 핵심 메시지 한 문장으로 크게 세워 줘.") == "statement"
    assert requested_layout("수치를 큰 숫자로 세워 줘.") == "big-number"
    assert requested_layout("공격 분류 장을 표로 바꿔 줘.") == "table"
    # 「연표」 is not a 「표」; a layout word that is just the topic is not a request.
    assert requested_layout("연표로 바꿔 줘") == "timeline"
    assert requested_layout("단계별 계획을 더 자세히 써 줘") is None
    assert requested_layout("표의 둘째 행 숫자를 고쳐 줘") is None


@pytest.mark.asyncio
async def test_a_slide_rewrite_can_change_its_chart_metrics_and_asked_for_title(monkeypatch):
    from app.services import deck

    async def fake_complete(model, messages, api_key, max_tokens):
        prompt = messages[-1]["content"]
        if "chart" in prompt and "categories" in prompt:
            return (
                '{"chart": {"kind": "bar", "unit": "%", "categories": ["nginx", "redis"],'
                ' "series": [{"name": "오버헤드", "values": [3.2, 3.7]}]}, "notes": "n"}',
                {"inputTokens": 1, "outputTokens": 1},
            )
        return (
            '{"title": "공격 150건 중 143건 탐지", "bullets": ["nginx 48/50", "redis 50/50"],'
            ' "notes": "n"}',
            {"inputTokens": 1, "outputTokens": 1},
        )

    monkeypatch.setattr(deck, "_complete", fake_complete)
    slides = [
        {"id": "a", "title": "오버헤드 결과", "layout": "chart",
         "chart": {"kind": "bar", "unit": "ms", "categories": ["nginx"],
                   "series": [{"name": "기준", "values": [412]}]}},
        {"id": "b", "title": "탐지율 결과", "layout": "bullets", "bullets": ["x"]},
    ]
    written, _ = await deck.rewrite_slide(
        request="실험 결과 발표", slides=slides, target_id="a", model="m", api_key="k",
        typed="오버헤드 결과 장을 막대 차트로 바꿔 줘. 워크로드별 overhead_pct, 단위 %.",
    )
    assert written["layout"] == "chart"
    assert written["chart"]["unit"] == "%"
    assert written["chart"]["series"][0]["values"] == [3.2, 3.7]
    assert written["title"] == "오버헤드 결과"  # no title change was asked

    renamed, _ = await deck.rewrite_slide(
        request="실험 결과 발표", slides=slides, target_id="b", model="m", api_key="k",
        typed="탐지율 장 제목을 결과 문장으로 바꿔 줘: 「공격 150건 중 143건 탐지」. "
        "본문은 그대로.",
    )
    assert renamed["title"] == "공격 150건 중 143건 탐지"

    # The model's reply carried no title at all: the quoted one still lands.
    async def no_title(model, messages, api_key, max_tokens):
        return '{"bullets": ["nginx 48/50"], "notes": "n"}', {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(deck, "_complete", no_title)
    renamed, _ = await deck.rewrite_slide(
        request="실험 결과 발표", slides=slides, target_id="b", model="m", api_key="k",
        typed="탐지율 장 제목을 「공격 150건 중 143건 탐지」로 바꿔 줘.",
    )
    assert renamed["title"] == "공격 150건 중 143건 탐지"


def test_the_revision_planner_reads_an_insert_as_one_new_part_after_a_neighbour():
    from app.services.revise import Plan, _parse, label

    plan = _parse(
        '{"scope": "insert", "after": 6, "names": ["위험"], "note": "표로, 열은 위험·영향·대응"}',
        8, "위험 장을 하나 추가해 줘",
    )
    assert plan.inserts and plan.scope == "insert"
    assert plan.after == 5 and plan.names == ["위험"]  # zero-based: after the sixth part
    assert label(plan, []) == "추가하는 중: 위험"
    # No names: fall back to a restructure. No place: before the last part.
    assert _parse('{"scope": "insert", "names": []}', 8, "x").restructures
    assert _parse('{"scope": "insert", "names": ["한계"]}', 8, "x").after == 6
    assert not Plan(scope="insert").inserts


def test_a_quoted_term_swap_across_the_document_is_recognised_and_applied_by_hand():
    from app.services.revise import replace_term, term_swap

    assert term_swap(
        "문서 전체에서 「관리형 OpenSearch Service」를 「AWS OpenSearch Service」로 통일해 줘. "
        "그 외는 바꾸지 마."
    ) == ("관리형 OpenSearch Service", "AWS OpenSearch Service")
    assert term_swap('"도토리"를 "밤"으로 전부 바꿔 줘') == ("도토리", "밤")
    # Not a swap: one section, or no quotes, or the same term.
    assert term_swap("비용 절에서 「월」을 「개월」로 바꿔 줘") is None
    assert term_swap("관리형을 AWS로 통일해 줘") is None
    assert term_swap("「A」를 「A」로 통일") is None

    parts = [
        {"id": "s1", "heading": "비교", "content": "관리형 OpenSearch Service는 월 690만 원.",
         "accent": "관리형 OpenSearch Service"},
        {"id": "s2", "title": "관리형 OpenSearch Service 비용", "layout": "table",
         "rows": [["안", "월"], ["관리형 OpenSearch Service", "690"]]},
    ]
    out, hits = replace_term(parts, "관리형 OpenSearch Service", "AWS OpenSearch Service")
    assert hits == 3
    assert out[0]["content"].startswith("AWS OpenSearch Service는")
    assert out[0]["accent"] == "관리형 OpenSearch Service"  # untouched fields stay
    assert out[1]["title"] == "AWS OpenSearch Service 비용"
    assert out[1]["rows"][1][0] == "AWS OpenSearch Service"


def test_a_restructure_carries_the_parts_the_person_did_not_touch():
    from app.services.revise import carry_parts

    old = [
        {"id": "o1", "title": "사내 문서검색 시스템 구축 현황", "layout": "title", "body": "x"},
        {"id": "o2", "title": "한 줄 요약", "layout": "statement", "body": "성능 개선 후 오픈"},
        {"id": "o3", "title": "진척 현황", "layout": "table", "rows": [["a", "b"]],
         "accent": "#111"},
        {"id": "o4", "title": "핵심 지표", "layout": "big-number",
         "metrics": [["742명", "파일럿"]]},
        {"id": "o5", "title": "요청 사항", "layout": "bullets", "bullets": ["노드 증설 승인"]},
    ]
    new = [
        {"id": "n1", "title": "사내 문서검색 시스템 구축 현황", "layout": "title", "body": "y"},
        {"id": "n2", "title": "한 줄 요약", "layout": "statement", "body": "다른 말"},
        {"id": "n3", "title": "진척 현황", "layout": "table", "rows": [["c", "d"]],
         "accent": "#222"},
        {"id": "n4", "title": "요청 사항", "layout": "bullets", "bullets": ["다시 쓴 요청"]},
    ]
    out, carried = carry_parts(
        new, old, is_deck=True, mentioned="전체를 4장으로 줄여 줘. 지표와 진척은 한 장으로 합쳐."
    )
    # 진척 was named in the instruction (merged with 지표): written fresh. The rest carried.
    assert carried == 3
    assert out[0]["body"] == "x" and out[0]["id"] == "n1"
    assert out[1]["body"] == "성능 개선 후 오픈"
    assert out[2]["rows"] == [["c", "d"]] and out[2]["accent"] == "#222"
    assert out[3]["bullets"] == ["노드 증설 승인"]

    sections_old = [{"id": "a", "heading": "요약", "content": "원문"}]
    sections_new = [
        {"id": "b", "heading": "요약", "content": "재작성"},
        {"id": "c", "heading": "한계", "content": "새 절"},
    ]
    out, carried = carry_parts(
        sections_new, sections_old, is_deck=False, mentioned="한계 절을 추가해 줘"
    )
    assert carried == 1 and out[0]["content"] == "원문" and out[0]["id"] == "b"
    assert out[1]["content"] == "새 절"


def test_a_quoted_rename_is_read_only_when_the_instruction_talks_about_the_title():
    from app.services.revise import requested_title

    assert requested_title(
        "위험 절 제목을 「위험과 대응」으로 바꾸고, 각 위험에 담당 부서를 붙여 줘."
    ) == "위험과 대응"
    assert requested_title(
        '탐지율 장 제목을 결과 문장으로 바꿔 줘: "공격 150건 중 143건 탐지"'
    ) == "공격 150건 중 143건 탐지"
    # Quotes without a title word are content, not a rename.
    assert requested_title("「현행 유지」라는 말을 본문에 넣어 줘") is None
    assert requested_title("제목을 더 짧게 해 줘") is None


def test_an_insert_aimed_into_an_existing_part_becomes_that_parts_edit():
    from app.services.revise import Plan, into_existing_part

    parts = ["요약", "현황과 요구사항", "대안 비교", "비용", "위험", "권고"]
    plan = Plan(scope="insert", after=3, names=["3년 TCO 비교"], note="표를 넣는다")
    fixed = into_existing_part(plan, parts, "비용 절에 3년 TCO 표를 넣어 줘. 월 비용 × 36개월.")
    assert fixed.scope == "parts" and fixed.targets == [3] and fixed.note == "표를 넣는다"
    # A genuinely new part keeps the insert.
    kept = into_existing_part(plan, parts, "위험 절 뒤에 한계 절을 하나 추가해 줘.")
    assert kept.inserts


def test_an_insert_that_names_its_neighbour_lands_exactly_there():
    from app.services.revise import Plan, place_insert

    parts = ["제목", "발표 순서", "한 줄 요약", "진척 현황", "핵심 지표", "이슈 및 조치 계획",
             "요청 사항 및 다음 단계", "마무리"]
    plan = Plan(scope="insert", after=4, names=["위험"], note="표로")
    before = place_insert(
        plan, parts, "위험 장을 하나 추가해 줘. 표로. 요청 사항 및 다음 단계 장 앞에."
    )
    assert before.after == 5  # right before 요청 사항
    after = place_insert(plan, parts, "핵심 지표 뒤에 위험 장을 넣어 줘")
    assert after.after == 4
    # No neighbour named: the planner's choice stands.
    assert place_insert(plan, parts, "위험 장을 하나 추가해 줘").after == 4


def test_a_structure_slide_is_not_left_on_steps_where_no_diagram_can_land():
    from app.services.deck import _DIAGRAM_LAYOUTS, structure_as_drawable

    plan = [
        {"title": "사내 문서검색 시스템", "layout": "title"},
        {"title": "전체 구조", "layout": "steps"},
        {"title": "검색 요청 흐름", "layout": "steps"},
        {"title": "System architecture", "layout": "chart"},
        {"title": "도입 단계", "layout": "steps"},
        {"title": "성능 지표", "layout": "chart"},
    ]
    out = structure_as_drawable(plan)
    assert out[1]["layout"] in _DIAGRAM_LAYOUTS and out[3]["layout"] in _DIAGRAM_LAYOUTS
    assert out[5]["layout"] == "chart"  # a numbers slide keeps its chart
    # A flow and a rollout are sequences: steps stays.
    assert out[2]["layout"] == "steps" and out[4]["layout"] == "steps"


def test_amounts_spelled_with_korean_units_count_as_facts_for_a_chart():
    from app.services.deck import _bullets_from_notes, _facts_set, _numbers_come_from

    facts = _facts_set(
        "문서 4만 2천 건, 월 검색 6.1만 건, 예산 1200만 원, p95 2.8초, hwp 3천 건(7%)"
    )
    assert _numbers_come_from(["42000", "61000.0", "12000000", "3000", "2.8", "7"], facts)
    assert not _numbers_come_from(["58000"], facts)
    # A slide whose chart the guard took away keeps its content as bullets from the notes.
    notes = (
        "현재 p95 지연시간은 2.8초입니다. 권한 필터 개선 후 1.5초로 단축할 계획입니다. "
        "월간 검색량은 6.1만 건입니다."
    )
    assert _bullets_from_notes(notes) == [
        "현재 p95 지연시간은 2.8초입니다",
        "권한 필터 개선 후 1.5초로 단축할 계획입니다",
        "월간 검색량은 6.1만 건입니다",
    ]
    assert _bullets_from_notes("") == []


def test_an_instruction_naming_one_part_targets_that_part_whatever_the_planner_guessed():
    from app.services.revise import Plan, named_target

    parts = ["제목", "목차", "진행 상황", "핵심 지표", "이슈 세 가지", "요청 사항", "다음 단계"]
    guessed = Plan(scope="parts", targets=[5], note="연표로")
    fixed = named_target(guessed, parts, "다음 단계 장을 연표로 바꿔 줘. 9/8 배포, 9/22 오픈.")
    assert fixed.targets == [6]
    # Two parts named, or none: the planner's reading stands. Insert and whole too.
    assert named_target(guessed, parts, "요청 사항과 다음 단계를 합쳐 줘").targets == [5]
    assert named_target(guessed, parts, "좀 더 간결하게").targets == [5]
    whole = Plan(scope="whole", targets=list(range(7)), note="x")
    assert named_target(whole, parts, "다음 단계 장을 줄여").scope == "whole"
    # Read as a restructure by mistake: one named part and no structural ask is that part.
    outline = Plan(scope="outline", note="연표로")
    assert named_target(outline, parts, "다음 단계 장을 연표로 바꿔 줘").targets == [6]
    assert named_target(outline, parts, "다음 단계 장을 둘로 나눠 줘").scope == "outline"
    assert named_target(outline, parts, "다음 단계 장 뒤에 위험 장을 추가해 줘").scope == "outline"
    # A date in the instruction that is also a slide's title is not a mention of that slide.
    dated = ["제목", "목차", "9/22 오픈", "진척", "핵심 지표", "이슈", "요청 사항", "다음 단계"]
    assert named_target(
        Plan(scope="parts", targets=[6], note="연표로"), dated,
        "다음 단계 장을 연표로 바꿔 줘. 9/8 배포, 9/15 재색인, 9/22 오픈.",
    ).targets == [7]
    # Every word of the phrase in one longer name: that part, not a new one.
    measured = ["제목", "연구 질문", "오버헤드 측정 결과", "탐지율 측정 결과", "한계"]
    assert named_target(
        Plan(scope="parts", targets=[3], note="차트로"), measured,
        "오버헤드 결과 장을 막대 차트로 바꿔 줘.",
    ).targets == [2]
    # A named part the deck does not have is made, before the closing slide — not guessed.
    none = [
        "제목", "발표 순서", "한 줄 요약", "진척 현황",
        "핵심 지표", "주요 이슈", "요청 사항", "마무리",
    ]
    made = named_target(
        Plan(scope="parts", targets=[3], note="연표로"), none, "다음 단계 장을 연표로 바꿔 줘."
    )
    assert made.inserts and made.names == ["다음 단계"] and made.after == 6
    # The person's short name for a longer-titled part finds that part.
    longer = ["제목", "목차", "진행 상황", "핵심 지표", "이슈", "요청 사항", "다음 단계와 마무리"]
    assert named_target(
        Plan(scope="parts", targets=[5], note="연표로"), longer, "다음 단계 장을 연표로 바꿔 줘."
    ).targets == [6]
    # A shorter name inside a longer part's name is the longer part's mention.
    merged = ["제목", "요청 사항 및 다음 단계", "마무리"]
    assert named_target(Plan(scope="parts", targets=[2], note=""), merged,
                        "요청 사항 및 다음 단계 장을 연표로").targets == [1]


@pytest.mark.asyncio
async def test_the_figure_planner_looks_once_more_when_diagrams_were_asked_for():
    from app.services import diagrams

    calls = []

    async def complete(model, messages, api_key, max_tokens):
        calls.append(messages[-1]["content"])
        if len(calls) == 1:
            return "[]", {"inputTokens": 1, "outputTokens": 1}
        return (
            '[{"part": 2, "figure": "method", "caption": "전체 구조",'
            ' "description": "크롤러가 색인기로, 색인기가 검색 API로 흐른다"}]',
            {"inputTokens": 1, "outputTokens": 1},
        )

    parts = [
        ("개요", "…"), ("전체 구조", "크롤러, 색인기, 검색 API, 웹으로 구성된다."), ("결론", "…"),
    ]
    planned, usage = await diagrams.plan(
        parts=parts, eligible=[0, 1, 2], request="구조도와 흐름도가 있으면 넣어 줘.",
        model="m", api_key="k", complete=complete, slide=False,
    )
    assert len(calls) == 2 and "구조도·흐름도를 명시적으로 요구" in calls[1]
    assert [p.index for p in planned] == [1] and planned[0].figure == "method"
    assert usage == {"inputTokens": 2, "outputTokens": 2}
    # No diagram asked for: an empty plan is accepted as the planner's judgement.
    calls.clear()
    planned, _ = await diagrams.plan(
        parts=parts, eligible=[0, 1, 2], request="현황 보고서를 써 줘.",
        model="m", api_key="k", complete=complete, slide=False,
    )
    assert len(calls) == 1 and planned == []


def test_a_section_rewrite_that_drops_the_figure_gets_it_back():
    from app.services.revise import keep_figure

    old = (
        "흐름은 다음과 같다.\n\n```mermaid\nflowchart LR\n  a --> b\n```\n\n"
        "*그림: 검색 요청 처리 흐름도*"
    )
    kept = keep_figure(old, "흐름은 다음과 같다. 캐시 조회가 추가됐다.", "캐시 조회 단계를 더해 줘")
    assert kept.endswith(
        "```mermaid\nflowchart LR\n  a --> b\n```\n\n*그림: 검색 요청 처리 흐름도*"
    )
    # ASCII boxes drawn in place of the diagram go; the diagram comes back.
    ascii_art = (
        "본문.\n\n```\n+--------+     +--------+\n| 사용자 | --> | 웹 화면 |\n"
        "+--------+     +--------+\n"
        "     |\n+--------+\n| 검색 API |\n+--------+\n```"
    )
    restored = keep_figure(old, ascii_art, "캐시 조회 단계를 더해 줘")
    assert "+--------+" not in restored and restored.count("```mermaid") == 1
    # A rewrite that drew its own figure keeps that one; an asked-for removal is honoured.
    fresh = "본문\n\n```mermaid\nflowchart LR\n  a --> c\n```"
    assert keep_figure(old, fresh, "노드를 바꿔 줘") == fresh
    assert keep_figure(old, "그림 없는 본문", "흐름도는 빼 줘") == "그림 없는 본문"
    assert keep_figure("그림 없던 절", "새 본문", "줄여 줘") == "새 본문"


def test_an_outline_that_is_the_planners_own_notes_is_refused():
    from app.services.deck import sane_outline

    leaked = [
        {"title": "Overview/Title", "layout": "title"},
        {"title": "Table of Contents (Agenda)", "layout": "agenda"},
        {"title": "Progress and Metrics (Integrating \"Progress Status\")", "layout": "table"},
        {"title": "Overview/Title", "layout": "title"},
        {"title": "Agenda", "layout": "agenda"},
        {"title": "Slides: 5 to 12. (6 is fine)", "layout": "bullets"},
        {"title": "First slide: layout \"title\", title same as presentation title.",
         "layout": "bullets"},
    ]
    assert not sane_outline(leaked, "현황 보고 발표를 6장으로 줄여 줘")
    english = [{"title": f"Section {i}", "layout": "bullets"} for i in range(5)]
    assert not sane_outline(english, "현황 보고 발표를 만들어 줘")
    assert sane_outline(english, "Make a status deck")
    fine = [
        {"title": "사내 문서검색 시스템", "layout": "title"}, {"title": "목차", "layout": "agenda"},
        {"title": "진척 현황과 핵심 지표", "layout": "table"},
        {"title": "이슈와 위험", "layout": "table"},
        {"title": "요청 사항과 다음 단계", "layout": "timeline"},
        {"title": "마무리", "layout": "closing"},
    ]
    assert sane_outline(fine, "현황 보고 발표를 6장으로 줄여 줘")


def test_a_restructure_leaves_only_its_own_count_in_the_request():
    from app.services.revise import without_counts

    original = "현황 보고 발표를 만들어 줘. 장수 8장, 청중 플랫폼팀장과 CIO. 분량: 총 8장 안팎으로."
    stripped = without_counts(original)
    assert "8장" not in stripped
    assert "현황 보고 발표를 만들어 줘." in stripped and "청중 플랫폼팀장과 CIO" in stripped
    # Numbers that are not part counts stay.
    assert without_counts("월 검색 6.1만 건, p95 2.8초") == "월 검색 6.1만 건, p95 2.8초"


def test_a_stated_target_count_makes_the_revision_a_restructure():
    from app.services.revise import Plan, count_change

    parts = [f"장 {i}" for i in range(9)]
    partial = Plan(scope="parts", targets=[2, 3], note="지표와 진척을 합친다")
    out = count_change(partial, parts, "전체를 6장으로 줄여 줘. 지표와 진척은 한 장으로 합치고.")
    assert out.restructures and out.note.startswith("장수는 6장으로 맞춘다.")
    # A planner note that names both counts keeps only the target.
    both = Plan(scope="outline", note="현재 9장을 6장으로 줄인다. 지표와 진척을 합친다")
    assert count_change(both, parts, "전체를 6장으로 줄여 줘").scope == "outline"
    partial_both = Plan(scope="parts", targets=[1], note="현재 9장을 6장으로 줄인다")
    fixed = count_change(partial_both, parts, "전체를 6장으로 줄여 줘")
    assert fixed.note.count("장으로") == 1 and fixed.note.startswith("장수는 6장으로 맞춘다.")
    # The count already matches, or no count is stated: the planner's reading stands.
    assert count_change(partial, parts[:6], "6장으로 줄여 줘").scope == "parts"
    assert count_change(partial, parts, "지표 장을 줄여 줘").scope == "parts"
    # Read as a new document by mistake: still the restructure the words describe.
    assert count_change(Plan(scope="new"), parts, "전체를 6장으로 줄여 줘").restructures
    assert count_change(Plan(scope="new"), [], "6장으로 줄여 줘").scope == "new"


def test_the_restructure_note_survives_the_merge_as_a_single_wanted_count():
    from app.services import grounding, revise
    from app.services.deck import requested_slides

    original = "현황 보고 발표를 만들어 줘. 장수 8장, 청중 CIO. 적어 준 수치만 써."
    plan = revise.count_change(
        revise.Plan(scope="parts", targets=[1], note="현재 9장을 6장으로 줄인다"),
        [f"p{i}" for i in range(9)],
        "전체를 6장으로 줄여 줘.",
    )
    merged = grounding.merge_answers(revise.without_counts(original), {"_note": plan.note})
    assert requested_slides(merged) == 6


def test_a_plan_a_slide_or_two_over_the_count_loses_its_dividers_and_agenda_first():
    from app.services.deck import fit_count

    plan = [
        {"title": "제목", "layout": "title"}, {"title": "목차", "layout": "agenda"},
        {"title": "현황", "layout": "table"}, {"title": "2부", "layout": "section"},
        {"title": "이슈", "layout": "table"}, {"title": "요청", "layout": "bullets"},
        {"title": "마무리", "layout": "closing"},
    ]
    six = fit_count(plan, 6)
    assert [p["title"] for p in six] == ["제목", "목차", "현황", "이슈", "요청", "마무리"]
    five = fit_count(plan, 5)
    assert [p["title"] for p in five] == ["제목", "현황", "이슈", "요청", "마무리"]
    # Content is never dropped: still over, the plan comes back untouched for the error.
    assert fit_count(plan, 4) == plan
    assert fit_count(plan, 7) == plan


def test_a_written_product_is_recomputed_and_the_wrong_figure_replaced_everywhere():
    from app.services.report import fix_products

    text = (
        "현행은 470만 원 × 36개월 = 169,200만 원, "
        "노드 추가는 515만 원 × 36개월 = 185,400만 원입니다.\n\n"
        "| 기준 | 현행 | 노드 추가 |\n|---|---|---|\n| 3년 총액 | 169,200만 원 | 185,400만 원 |"
    )
    fixed = fix_products(text)
    assert "16,920만 원" in fixed and "18,540만 원" in fixed
    assert "169,200" not in fixed and "185,400" not in fixed
    assert fixed.count("16,920만 원") == 2  # the sentence and the table cell
    # A right product is left alone; a mismatched scale is not touched.
    assert fix_products("3 × 4 = 12") == "3 × 4 = 12"
    mixed = "470만 원 × 36개월 = 1억 6,920만 원"
    assert fix_products(mixed) == mixed


def test_a_notes_request_for_every_slide_is_a_whole_pass_that_leaves_the_words_alone():
    from app.services.revise import Plan, notes_everywhere, notes_only

    assert notes_only("모든 장에 발표자 노트를 두 문장씩 넣어 줘. 본문은 한 글자도 바꾸지 말고.")
    assert notes_only("Add speaker notes to every slide.")
    assert not notes_only("노트와 본문도 함께 고쳐 줘")
    assert not notes_only("보안 규칙 장을 표로 바꿔 줘")
    parts = ["제목", "첫 주", "계정", "보안", "도움", "마무리"]
    plan = notes_everywhere(
        Plan(scope="parts", targets=[0, 1, 2], note="노트 추가"), parts,
        "모든 장에 발표자 노트를 두 문장씩 넣어 줘.",
    )
    assert plan.scope == "whole" and plan.targets == list(range(6))
    # Notes for one named slide stays a one-slide edit.
    one = notes_everywhere(
        Plan(scope="parts", targets=[3], note="x"), parts, "보안 장에 노트를 넣어 줘"
    )
    assert one.targets == [3]


@pytest.mark.asyncio
async def test_a_notes_only_rewrite_changes_nothing_but_the_notes(monkeypatch):
    from app.services import deck

    async def complete(model, messages, api_key, max_tokens):
        return (
            '{"bullets": ["다시 쓴 글머리표"], "notes": "첫 문장입니다. 둘째 문장입니다."}',
            {"inputTokens": 1, "outputTokens": 1},
        )

    monkeypatch.setattr(deck, "_complete", complete)
    slides = [
        {"id": "a", "title": "보안 규칙", "layout": "bullets",
         "bullets": ["USB 금지", "2단계 인증"]}
    ]
    written, _ = await deck.rewrite_slide(
        request="온보딩", slides=slides, target_id="a", model="m", api_key="k",
        typed="모든 장에 발표자 노트를 두 문장씩 넣어 줘.", notes_only=True,
    )
    assert written["bullets"] == ["USB 금지", "2단계 인증"]
    assert written["notes"] == "첫 문장입니다. 둘째 문장입니다."


def test_a_json_wrapped_section_is_unwrapped_and_placeholder_frames_are_recognised():
    from app.services.report import placeholder_heavy, unwrap_json_prose

    wrapped = (
        '{"결과의 섹션 본문": "실험 결과, 명중률은 0.345에서 0.930으로 올랐다.\\n\\n| a | b |"}'
    )
    assert unwrap_json_prose(wrapped) == (
        "실험 결과, 명중률은 0.345에서 0.930으로 올랐다.\n\n| a | b |"
    )
    assert unwrap_json_prose("그냥 본문 {괄호} 포함") == "그냥 본문 {괄호} 포함"
    assert unwrap_json_prose('{"a": 1, "b": [2]}') == '{"a": 1, "b": [2]}'
    frame = "| 8 | (미정) | (미정) |\n| 16 | (미정) | (측정값) |"
    assert placeholder_heavy(frame)
    assert not placeholder_heavy("| 8 | 20000 | 0.345 |\n(미정) 하나")


def test_a_corrected_product_never_touches_a_number_that_merely_contains_the_wrong_digits():
    from app.services.report import fix_products

    assert fix_products("3 × 4 = 13; 별도 값 130과 213") == "3 × 4 = 12; 별도 값 130과 213"
    spaced = "470만 원 × 36개월 = 169,200 만 원이고 표에는 169,200 만 원, 합계 2,169,200원"
    fixed = fix_products(spaced)
    assert fixed.count("16,920 만 원") == 2 and "2,169,200원" in fixed


def test_a_rename_with_both_names_quoted_takes_the_new_one():
    from app.services.revise import requested_title

    assert requested_title("「위험」 절 제목을 「위험과 대응」으로 바꿔 줘") == "위험과 대응"
    both = '"탐지율 결과" 장 제목을 "공격 150건 중 143건 탐지"로'
    assert requested_title(both) == "공격 150건 중 143건 탐지"
    assert requested_title("제목을 「새 이름」으로, 「옛 이름」은 버려") == "새 이름"


# ── the whole-codebase review's findings ─────────────────────────────────


def test_a_stdio_server_inherits_only_a_minimal_environment(monkeypatch):
    from app.services import mcp

    monkeypatch.setenv("DATABASE_URL", "postgres://secret")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    session = mcp._StdioSession("echo hi", {"MY_TOKEN": "x"})
    assert "DATABASE_URL" not in session.env and "LITELLM_MASTER_KEY" not in session.env
    assert session.env["PATH"] == "/usr/bin" and session.env["MY_TOKEN"] == "x"


def test_a_declared_zip_bomb_is_refused_before_it_is_read():
    import io
    import zipfile

    from app.services.files import _MAX_UNPACKED, _open_zip

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:p>안녕</w:p>")
    assert _open_zip(buf.getvalue()).namelist() == ["word/document.xml"]
    # A header that claims more than the limit is turned away without inflating it.
    big = io.BytesIO()
    with zipfile.ZipFile(big, "w") as z:
        info = zipfile.ZipInfo("word/document.xml")
        z.writestr(info, "x")
    data = bytearray(big.getvalue())
    # Patch the central-directory uncompressed size to look enormous.
    cd = data.rfind(b"PK\x01\x02")
    data[cd + 24 : cd + 28] = (_MAX_UNPACKED + 1).to_bytes(4, "little")
    with pytest.raises(RuntimeError):
        _open_zip(bytes(data))


@pytest.mark.asyncio
async def test_a_turn_claim_is_taken_once_and_released(monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool
    from sqlmodel.ext.asyncio.session import AsyncSession
    from test_shared_notes import _DDL

    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        for statement in _DDL:
            await conn.exec_driver_sql(statement)
    monkeypatch.setattr(
        sessions_router, "SessionLocal", lambda: AsyncSession(engine, expire_on_commit=False)
    )
    async with AsyncSession(engine, expire_on_commit=False) as db:
        user = User(email="p@example.test", password_hash="x", name="P")
        db.add(user)
        await db.commit()
        session = ChatSession(user_id=user.id, kind=SessionKind.chat)
        db.add(session)
        await db.commit()
        assert await sessions_router._claim_turn(db, session)
        await db.commit()
        # A second request, on this or another replica, finds the turn taken.
        assert not await sessions_router._claim_turn(db, session)
        await db.commit()
        await sessions_router._release_turn(session.id)
        assert await sessions_router._claim_turn(db, session)
        await db.commit()
        # Stop releases whatever token holds it.
        await sessions_router._release_turn(session.id, token="")
        assert await sessions_router._claim_turn(db, session)
    await engine.dispose()


@pytest.mark.asyncio
async def test_rewrite_slide_with_a_title_only_reply_renames_instead_of_failing(monkeypatch):
    from app.services import deck

    async def complete(model, messages, api_key, max_tokens):
        return '{"title": "공격 150건 중 143건 탐지"}', {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(deck, "_complete", complete)
    slides = [{"id": "b", "title": "탐지율 결과", "layout": "table", "rows": [["a", "b"]]}]
    written, _ = await deck.rewrite_slide(
        request="실험 결과 발표", slides=slides, target_id="b", model="m", api_key="k",
        typed="탐지율 장 제목을 결과 문장으로 바꿔 줘: 「공격 150건 중 143건 탐지」. "
        "본문은 그대로.",
    )
    assert written["title"] == "공격 150건 중 143건 탐지" and written["rows"] == [["a", "b"]]


def test_a_report_picture_fills_the_column_unless_it_is_an_icon():
    import io

    import PIL.Image
    from reportlab.lib.units import mm

    from app.services import report_export

    def png(w, h):
        buf = io.BytesIO()
        PIL.Image.new("RGB", (w, h), "white").save(buf, format="PNG")
        return buf.getvalue()

    column = report_export._PICTURE_MM * mm
    floor = column * report_export._PICTURE_FLOOR
    # A 300px photo is grown to the floor; a 2000px one is shrunk to the column; a 64px
    # mark stays as it is.
    w, h = report_export._picture_size(png(300, 200))
    assert abs(w - floor) < 0.5 and abs(h - floor * 200 / 300) < 0.5
    w, _ = report_export._picture_size(png(2000, 800))
    assert abs(w - column) < 0.5
    w, _ = report_export._picture_size(png(64, 64))
    assert abs(w - 64 * report_export._POINTS_PER_PIXEL) < 0.5
    # A tall picture is bounded by the page height limit, not blown past it.
    _, h = report_export._picture_size(png(400, 1600))
    assert h <= report_export._PICTURE_MAX_MM * mm + 0.5


def test_an_unsized_slide_picture_takes_the_large_column():
    from app.services.deck_export import _picture_span

    assert _picture_span({"image": {"src": "x"}}) == 390.0
    assert _picture_span({"image": {"src": "x", "size": "small"}}) == 230.0


def test_a_subject_has_several_colours_and_a_request_keeps_its_own():
    from app.services.deck import _THEMES, topic_accent, topic_theme

    requests = [f"사내 {w} 시스템 소개 발표 {n}" for n in range(12) for w in ("문서검색", "결제")]
    seen = {topic_theme(r) for r in requests}
    assert len(seen) >= 2  # systems are not always the same blue
    assert topic_theme("보안 점검 보고서") == topic_theme("보안 점검 보고서")  # stable per request
    assert topic_accent("환경 보호 캠페인") in (_THEMES["초록"], _THEMES["청록"])


def test_an_undressed_report_gets_a_room_look_and_a_subject_colour():
    from app.services.design import report_look_for

    seminar = report_look_for("연구실 세미나용 실험 결과 보고서", "#0f766e")
    assert seminar["visualStyle"] == "minimal" and seminar["accent"] == "#0f766e"
    launch = report_look_for("제품 소개 홍보 자료", "#b91c1c")
    assert launch["visualStyle"] == "poster"
    plain = report_look_for("현황 보고서", "#1e3a8a")
    assert plain["visualStyle"] in ("editorial", "poster", "minimal")
    assert plain["accent"] == "#1e3a8a"


def test_the_academy_and_the_quiet_looks_read_in_a_serif():
    from app.services.design import font_for, report_look_for

    assert font_for("학회 발표용 연구 결과 보고서") == "serif"
    assert font_for("제품 출시 데모 슬라이드", "dark") == "gothic"
    assert font_for("분기 현황 보고", "minimal") == "serif"
    assert report_look_for("논문 요약 보고서", "#1e3a8a")["font"] == "serif"
    assert report_look_for("마케팅 캠페인 홍보 자료", "#b91c1c")["font"] == "gothic"


def test_two_named_parts_trade_places_without_the_model():
    from app.services.revise import order_swap

    parts = ["신규 입사자 온보딩 안내", "첫 주 일정", "필수 계정", "보안 규칙", "도움 받는 곳",
             "마무리"]
    assert order_swap("보안 규칙 장과 필수 계정 장의 순서를 바꿔 줘.", parts) == (3, 2)
    assert order_swap("첫 주 일정이랑 도움 받는 곳 자리를 서로 바꿔", parts) == (1, 4)
    # A name that matches nothing, or the same part twice, is left to the planner.
    assert order_swap("예산 장과 보안 규칙 장의 순서를 바꿔 줘", parts) is None
    assert order_swap("보안 장과 보안 규칙 장의 순서를 바꿔 줘", parts) is None
    # An ordinary instruction is not a swap.
    assert order_swap("보안 규칙 장을 더 짧게 써 줘", parts) is None


def test_a_number_the_brief_states_comes_back_to_the_slide_that_dropped_it():
    from app.services.deck import restate_missing_facts

    request = ("사내 문서검색 시스템(문서 4만 2천 건, 임직원 1,850명, 파일럿 742명) 구축 현황 "
               "슬라이드를 만들어 줘. 전체를 6장으로 줄여 줘.")
    slides = [
        {"title": "현황", "layout": "title"},
        {"title": "진행 현황과 핵심 지표", "layout": "bullets",
         "bullets": ["문서 4만 2천 건 색인 완료", "임직원 1,850명 대상"]},
        {"title": "다음 단계", "layout": "timeline", "timeline": [["10월", "오픈"]]},
        {"title": "마무리", "layout": "closing"},
    ]
    changed = restate_missing_facts(slides, request)
    assert changed == [1]
    assert slides[1]["bullets"][-1] == "파일럿 742명"
    # Numbers already on a slide, and the slide-count instruction, are left alone.
    assert len(slides[1]["bullets"]) == 3
    assert not any("6장" in b for b in slides[1]["bullets"])
    # Nothing to do the second time.
    assert restate_missing_facts(slides, request) == []


def test_a_brief_with_one_number_or_no_body_slides_is_left_alone():
    from app.services.deck import restate_missing_facts

    slides = [{"title": "표지", "layout": "title"}, {"title": "끝", "layout": "closing"}]
    assert restate_missing_facts(slides, "임직원 1,850명과 파일럿 742명") == []
    slides = [{"title": "본론", "layout": "bullets", "bullets": ["하나"]}]
    assert restate_missing_facts(slides, "임직원 1,850명 대상 안내") == []
    # Dates and durations are about the occasion, not facts for the slides.
    assert restate_missing_facts(slides, "2026년 동아리 신청 안내, 3분 발표, 10월 오픈") == []


@pytest.mark.asyncio
async def test_an_order_swap_is_routed_in_place_without_asking_the_planner(monkeypatch):
    """「보안 규칙 장과 필수 계정 장의 순서를 바꿔 줘」 used to be read as a restructure, so
    the deck was redrawn (and grew a 목차) to move two slides. It is now an in-place plan."""
    from types import SimpleNamespace

    from app.routers import sessions as sessions_router
    from app.services import revise

    names = ["신규 입사자 온보딩 안내", "첫 주 일정", "필수 계정", "보안 규칙", "도움 받는 곳",
             "마무리"]

    class Db:
        async def get(self, _model, _id):
            return SimpleNamespace(title="온보딩", data={"slides": [{"title": n} for n in names]})

    monkeypatch.setattr(sessions_router, "_skeleton", lambda _artifact: names)

    async def planner(**_kwargs):
        raise AssertionError("the planner must not be asked for a two-part swap")

    monkeypatch.setattr(revise, "plan", planner)
    plan, skeleton = await sessions_router._revision_plan(
        Db(), SimpleNamespace(artifact_id="a1"),
        instruction="보안 규칙 장과 필수 계정 장의 순서를 바꿔 줘.", model={"id": "m"},
        api_key="k",
    )
    assert skeleton == names
    assert plan is not None and not plan.restructures
    assert plan.note == "보안 규칙 ↔ 필수 계정"


def test_an_outline_keeps_every_enumerated_part_as_its_own_slide():
    from app.services.deck import enumerated_items, keep_enumerated

    request = (
        "신규 입사자 온보딩 안내 발표 6장을 만들어 줘. 내용: (1) 첫 주 일정 — 월 OT, 화 보안 교육 "
        "(2) 필수 계정 — 메일, 메신저 (3) 보안 규칙 — 외부 메일 첨부 금지, 2단계 인증, USB 금지 "
        "(4) 도움 받는 곳 — IT 헬프데스크 내선 1234. 표지·마무리 포함 6장, 적어 준 것만 써."
    )
    assert enumerated_items(request) == ["첫 주 일정", "필수 계정", "보안 규칙", "도움 받는 곳"]
    # A numbered list of one part's items (the issues of a status deck) names no parts.
    issues = ("이슈: (1) p95 미달 — 권한 필터 개선 (2) 조직도 API 보안 검토 미완 "
              "(3) 인덱스 노드 증설")
    assert enumerated_items(issues) == []
    assert enumerated_items("구성: 1) 배경 2) 방법 3) 결과") == ["배경", "방법", "결과"]
    plan = [
        {"title": "신규 입사자 온보딩 안내", "layout": "title"},
        {"title": "첫 주 일정", "layout": "steps"},
        {"title": "필수 계정 및 보안 규칙", "layout": "cards"},
        {"title": "도움 받는 곳", "layout": "bullets"},
        {"title": "보안이 최우선", "layout": "statement"},
        {"title": "기억할 것", "layout": "closing"},
    ]
    fixed = keep_enumerated(plan, request, 6)
    assert [s["title"] for s in fixed] == [
        "신규 입사자 온보딩 안내", "첫 주 일정", "필수 계정", "보안 규칙", "도움 받는 곳",
        "기억할 것",
    ]
    assert fixed[2]["layout"] == "cards" and fixed[3]["layout"] == "bullets"
    # A request that lists nothing, or an outline already complete, is untouched.
    assert keep_enumerated(plan, "온보딩 발표 6장", 6) == plan
    assert keep_enumerated(fixed, request, 6) == fixed
    # A request to merge parts, or a count with no room for one slide each, is left alone.
    merged = request + " 지표와 진척은 한 장으로 합치고, 이슈·위험도 한 장으로."
    assert keep_enumerated(plan, merged, 6) == plan
    assert keep_enumerated(plan, request, 4) == plan
    # A part the planner renamed is a body slide covering no item: it takes the name back.
    renamed = [
        {"title": "신규 입사자 온보딩 안내", "layout": "title"},
        {"title": "첫 주 일정", "layout": "steps"},
        {"title": "필수 계정 네 가지", "layout": "cards"},
        {"title": "외부 유출 금지", "layout": "bullets"},
        {"title": "도움 받는 곳", "layout": "bullets"},
        {"title": "첫 주를 잘 시작하는 방법", "layout": "closing"},
    ]
    titles = [s["title"] for s in keep_enumerated(renamed, request, 6)]
    assert titles == ["신규 입사자 온보딩 안내", "첫 주 일정", "필수 계정 네 가지", "보안 규칙",
                      "도움 받는 곳", "첫 주를 잘 시작하는 방법"]
    # A missing item is added before the closing slide.
    short = [plan[0], plan[1], plan[3], plan[5]]
    tail = [s["title"] for s in keep_enumerated(short, request, None)][-3:]
    assert tail == ["필수 계정", "보안 규칙", "기억할 것"]


def test_an_external_writer_gets_the_local_model_as_its_revision_judge():
    from types import SimpleNamespace

    from app.core.config import settings
    from app.routers import sessions as sessions_router

    local = {"id": settings.default_chat_model, "kinds": ["chat", "report", "slides"],
             "dataBoundary": "hybrid", "creditCost": 0}
    external = {"id": "deepseek/deepseek-v4-flash", "kinds": ["chat", "report", "slides"],
                "dataBoundary": "external", "creditCost": 18}
    user = SimpleNamespace(allowed_models=[], role="member")
    catalogue = [local, external]
    # An external writer is judged by the local model …
    judge = sessions_router._local_planner(
        user=user, catalogue=catalogue, kind="report", writer=external, strict_local=False
    )
    assert judge is not None and judge["id"] == local["id"]
    # … a local writer judges itself, and a strict-local route takes no other model.
    assert sessions_router._local_planner(
        user=user, catalogue=catalogue, kind="report", writer=local, strict_local=False
    ) is None
    assert sessions_router._local_planner(
        user=user, catalogue=catalogue, kind="report", writer=external, strict_local=True
    ) is None


def test_an_answer_that_echoes_the_previous_one_or_switches_language_is_asked_again():
    from app.services.agent import _answer_anomaly

    earlier = (
        "전이학습은 이미 학습한 모델의 지식을 새 작업에 다시 활용하는 방법입니다. "
        "소량의 데이터로도 좋은 성능을 낼 수 있습니다. 이상입니다."
    )
    history = [
        {"role": "user", "content": "전이학습이 뭐야?"},
        {"role": "assistant", "content": earlier},
        {"role": "user", "content": "그걸 사진 300장짜리 데이터셋에 쓰면 뭘 주의해야 해?"},
    ]
    assert _answer_anomaly(earlier, history)[0] == "echo"
    fresh = "데이터가 적으면 과적합이 쉬우니 증강과 정규화를 쓰세요. 이상입니다."
    assert _answer_anomaly(fresh, history) is None
    english = (
        "## Reverse-issued tax invoice workflow\n\nThe user is asking about a specific legal "
        "workflow where the buyer prepares the draft and the supplier approves it. " * 3
    )
    assert _answer_anomaly(english, history)[0] == "language"
    # English was asked for: not an anomaly. Code answers carry Korean prose around them.
    asked_english = history[:2] + [{"role": "user", "content": "이걸 영어로 설명해 줘"}]
    assert _answer_anomaly(english, asked_english) is None
    code = (
        "아까 정한 구조대로 작성했습니다.\n\n```python\n" + "x = compute_value(path)\n" * 30 + "```"
    )
    assert _answer_anomaly(code, history) is None


def test_a_provider_that_refuses_the_reasoning_switch_is_remembered():
    from app.services import thinking

    model = "test/mandatory-reasoning"
    thinking._MANDATORY.discard(model)
    assert thinking.switch(model) == {"reasoning": thinking.NO_REASONING}
    from types import SimpleNamespace as R

    assert not thinking.refused(model, R(status_code=429, text="rate limited"))
    assert not thinking.refused(model, R(status_code=400, text="invalid messages"))
    assert not thinking.refused(model, R(status_code=400))  # a double with no body
    assert thinking.refused(
        model, R(status_code=400, text='{"error":{"message":"Reasoning is mandatory"}}')
    )
    # From then on the switch is left out, so the next call is not refused again.
    assert thinking.switch(model) == {}
    thinking._MANDATORY.discard(model)


@pytest.mark.asyncio
async def test_a_gateway_hiccup_keeps_the_last_catalogue(monkeypatch):
    """A failed refresh must not turn every proxied model into a 503."""
    from app.services import litellm as litellm_client
    from app.services import models as model_service

    good = {"models": [{"id": "local/x", "kinds": ["chat"], "modality": "chat", "provider": "local",
                        "creditCost": 0}], "litellmAvailable": True}
    model_service._CACHE["value"] = good
    model_service._CACHE["at"] = 0.0  # expired

    async def failing():
        raise litellm_client.LiteLLMError("model_info_failed: 502")

    monkeypatch.setattr(litellm_client, "model_info", failing)
    assert await model_service.list_models() is good
    # A retry is due soon, not after the whole TTL.
    import time

    elapsed = time.monotonic() - model_service._CACHE["at"]
    assert elapsed >= model_service._CACHE_TTL_SEC - model_service._RETRY_AFTER_FAILURE_SEC - 1
    model_service._CACHE["value"] = None
    model_service._CACHE["at"] = 0.0


def test_a_self_hosted_qwen_is_told_not_to_think_through_its_chat_template():
    from app.services import thinking

    local = thinking.switch("local/qwen3.8-flash-next")
    assert local["chat_template_kwargs"] == {"enable_thinking": False}
    assert local["reasoning"] == thinking.NO_REASONING
    assert "chat_template_kwargs" in thinking.switch("strict-local/qwen3.8-flash-next")
    # A self-hosted model that never thinks is not handed the template variable.
    assert "chat_template_kwargs" not in thinking.switch("local/qwen3.8-27b")
    assert "chat_template_kwargs" not in thinking.switch("deepseek/deepseek-v4-flash")


def test_self_hosted_chat_models_are_told_not_to_think_unless_configured(monkeypatch):
    import inspect

    from app.core.config import settings
    from app.services import agent

    source = inspect.getsource(agent._stream_once)
    assert "thinking.template_switch(model)" in source and "self_hosted_chat_thinking" in source
    assert settings.self_hosted_chat_thinking is False
    # External chat models get the OpenRouter switch, and a refusal is retried without it.
    assert "external_chat_thinking" in source and "thinking.switch(model)" in source
    assert "thinking.refused(model, _Refusal(400, body))" in source
    assert settings.external_chat_thinking is False


def test_the_default_local_model_plans_for_a_different_writer_but_not_for_itself():
    from types import SimpleNamespace

    from app.core.config import settings
    from app.routers import sessions as sessions_router

    local = {"id": settings.default_chat_model, "kinds": ["chat", "report", "slides"],
             "dataBoundary": "hybrid", "creditCost": 0}
    other = {"id": "local/qwen3.8-flash-next", "kinds": ["chat", "report", "slides"],
             "dataBoundary": "hybrid", "creditCost": 0}
    user = SimpleNamespace(allowed_models=[], role="member")
    planner = sessions_router._planner_model(
        settings.default_chat_model, user=user, catalogue=[local, other], kind="slides",
        writer=other, strict_local=False,
    )
    assert planner is not None and planner["id"] == local["id"]
    # For the default model itself the row comes back too; the route drops it there.
    same = sessions_router._planner_model(
        settings.default_chat_model, user=user, catalogue=[local, other], kind="slides",
        writer=local, strict_local=False,
    )
    assert same is not None and same["id"] == local["id"]


def test_an_editor_flags_repeats_and_filler_and_keeps_only_fact_preserving_edits():
    from app.services.report import drop_redundant_kpi, edit_keeps_facts, editing_issues

    earlier = [
        "파일럿은 4개 부서 742명을 대상으로 6주간 운영되었습니다. 월 검색량은 6.1만 건입니다."
    ]
    body = (
        "파일럿은 4개 부서의 742명 참여자를 대상으로 6주간 운영되었습니다. 평균 검색 시간을 "
        "추적했습니다. 나머지 지표를 확인하는 데는 자료에 다른 문제가 명시되어 있지 않습니다."
    )
    issues = editing_issues(body, earlier)
    assert any(i.startswith("앞 절과 같은 말") for i in issues)
    assert any(i.startswith("빈말") for i in issues)
    assert editing_issues("검색 시간은 14분에서 3분으로 줄었습니다.", earlier) == []
    # An edit that drops or invents a number, or halves the text, is refused.
    kept = (
        "742명을 6주간 살피며 모든 검색의 평균 소요 시간을 추적했습니다. 그 밖의 문제는 없었습니다."
    )
    assert edit_keeps_facts(body, kept)
    invented = "742명을 6주간 살폈고 평균 검색 시간을 추적했습니다. 비용은 300만 원입니다."
    assert not edit_keeps_facts(body, invented)
    assert not edit_keeps_facts(body, "짧게.")
    table = (
        "| 지표 | 수치 |\n|---|---|\n| 참여 인원 | 742명 |\n| 만족도 | 4.1/5 |\n\n"
        "```kpi\n742명 | 참여 인원\n4.1/5 | 만족도\n```\n\n설명."
    )
    assert "```kpi" not in drop_redundant_kpi(table)
    partial = table.replace("4.1/5 | 만족도\n```", "3분 | 검색 시간\n```")
    assert "```kpi" in drop_redundant_kpi(partial)


def test_speaker_notes_that_only_read_the_slide_aloud_are_dropped():
    from app.services.deck import notes_without_echo

    slide = {
        "title": "왜 만들었나",
        "bullets": ["문서 4만 2천 건이 파일 서버·위키·메일에 흩어져 있음", "찾는 데 평균 14분"],
    }
    notes = ("사내에는 파일 서버, 위키, 메일에 흩어진 문서가 4만 2천 건 있습니다. "
             "이 문서들을 찾으려면 평균 14분이 걸렸습니다. "
             "이 시간 손실을 줄이기 위해 통합 검색 시스템을 만들었습니다.")
    kept = notes_without_echo(notes, slide)
    assert "통합 검색 시스템을 만들었습니다" in kept
    assert "4만 2천 건 있습니다" not in kept and "평균 14분이 걸렸습니다" not in kept
    # Notes that add something are left whole; empty notes stay empty.
    fresh = "검색 시간이 줄면 신규 입사자의 첫 주 적응이 빨라집니다. 다음 장에서 구조를 봅니다."
    assert notes_without_echo(fresh, slide) == fresh
    assert notes_without_echo("", slide) == ""
    # Stage directions are not speech: 「…을 강조합니다」 goes, the spoken sentence stays.
    spoken = "인사 DB가 있어야 누가 무엇을 볼 수 있는지 정해집니다."
    meta = "검색 API 옆의 인사 DB가 권한 검증의 근거라는 점을 강조합니다. " + spoken
    assert notes_without_echo(meta, slide) == spoken


def test_the_editor_sees_the_same_facts_told_in_new_words_and_a_bold_heading_at_the_top():
    from app.services.report import _without_own_heading, editing_issues

    earlier = [
        "파일럿은 4개 부서 742명을 대상으로 6주간 운영되었습니다. 월 검색량은 6.1만 건입니다."
    ]
    reworded = (
        "대상은 4개 부서의 742명 참여자였고, 기간은 6주간이었습니다. "
        "측정 지표는 평균 검색 시간입니다."
    )
    issues = editing_issues(reworded, earlier)
    assert any("다시 말함" in i for i in issues)
    # New numbers are new facts, not a restatement.
    fresh = editing_issues("검색 시간은 14분에서 3분으로 줄었습니다.", earlier)
    assert not any("다시 말함" in i for i in fresh)
    body = "**위험과 대응**\n\np95 미달 위험은 권한 필터링 개선이 목표에 닿지 못할 때 생깁니다."
    assert _without_own_heading(body, "위험").startswith("p95 미달 위험은")
    bold_prose = "**굵은 강조는 본문입니다.** 이어지는 글."
    assert _without_own_heading(bold_prose, "위험").startswith("**굵은")
    # The new name of a renamed section, written plainly over the old heading, goes too.
    renamed = "위험과 대응\n\n```callout\n성능 목표 미달 시 전환 리스크\n```"
    assert _without_own_heading(renamed, "위험").startswith("```callout")
    one_line_paragraph = "검색 시간이 줄었습니다\n\n다음 문단."
    assert _without_own_heading(one_line_paragraph, "결과") == one_line_paragraph


def test_speaker_notes_keep_only_the_numbers_the_request_gave():
    from app.services.deck import _facts_set, _plain_notes, notes_grounded

    facts = _facts_set("문서 4만 2천 건, 검색에 평균 14분. 월 검색 6.1만 건.")
    notes = ("14분이라는 시간은 개발자 10명 기준으로 하루에 1시간 20분을 낭비하는 것과 같습니다. "
             "이 14분을 줄이는 것이 이 시스템의 목적입니다.")
    assert notes_grounded(notes, facts) == "이 14분을 줄이는 것이 이 시스템의 목적입니다."
    assert _plain_notes('"따옴표에 싸인 문장입니다."') == "따옴표에 싸인 문장입니다."


def test_pure_restatements_and_filler_are_trimmed_by_hand_and_nothing_else():
    from app.services.report import trim_restatements

    earlier = [
        "결론: 4개 부서 742명 대상 파일럿에서 평균 검색 시간이 14분에서 3분으로 줄었고, "
        "만족도는 4.1점입니다. 월 검색량은 6.1만 건이었습니다."
    ]
    body = (
        "파일럿은 문서 찾는 시간을 줄이려는 시도였습니다. 기간은 6주였습니다. "
        "대상은 4개 부서 742명이었고 월 검색량은 6.1만 건이었습니다. "
        "평균 검색 시간을 매 검색마다 기록했습니다. "
        "나머지 지표를 확인하는 데는 자료에 다른 문제가 명시되어 있지 않습니다.\n\n"
        "| 지표 | 수치 |\n|---|---|\n| 참여 인원 | 742명 |\n"
    )
    trimmed, cut = trim_restatements(body, earlier)
    assert len(cut) == 2
    assert "대상은 4개 부서 742명이었고" not in trimmed and "명시되어 있지 않습니다" not in trimmed
    # The opening, the new measurement sentence and the table survive untouched.
    assert trimmed.startswith("파일럿은 문서 찾는 시간을") and "매 검색마다 기록했습니다" in trimmed
    assert "| 참여 인원 | 742명 |" in trimmed
    # The first section has nothing earlier to restate: only its filler sentence goes.
    first, cut_first = trim_restatements(body, [])
    assert cut_first == ["나머지 지표를 확인하는 데는 자료에 다른 문제가 명시되어 있지 않습니다."]
    assert "대상은 4개 부서 742명이었고" in first


def test_the_arithmetic_a_request_implies_is_done_once_in_the_numbers_list():
    from app.services.report import _facts_line, _won, derived_values

    request = (
        "세 가지를 비교해: 자체 운영 OpenSearch(현행, 월 470만 원), "
        "관리형 OpenSearch Service(월 약 690만 원), Elasticsearch 구독(월 약 820만 원). "
        "연 예산 6,000만 원. 비용 절에 3년 TCO 표를 넣어 줘. 월 비용 × 36개월로 세 안을 "
        "한 표에, 현행에 노드 1대 추가(월 45만 원) 시나리오도 한 행 더."
    )
    lines = derived_values(request)
    joined = "\n".join(lines)
    assert "470만 원 × 36개월 = 1억 6,920만 원" in joined
    assert "690만 원 × 36개월 = 2억 4,840만 원" in joined
    assert "(470만 + 45만) × 36개월 = 1억 8,540만 원" in joined
    assert "690만 원 × 12개월 = 8,280만 원" in joined
    assert "연 8,280만 원 vs 연 예산 6,000만 원: 예산보다 2,280만 원 초과" in joined
    assert "연 5,640만 원 vs 연 예산 6,000만 원: 예산보다 360만 원 여유" in joined
    assert _won(16920) == "1억 6,920만 원" and _won(20000) == "2억 원" and _won(45) == "45만 원"
    # A request with no monthly amounts adds nothing; the plain list stays as it was.
    assert derived_values("문서 4만 2천 건, 임직원 1,850명") == []
    assert "계산된 값" in _facts_line(request, [])
    assert "계산된 값" not in _facts_line("742명 대상", [])


def test_a_restatement_that_opens_a_section_is_cut_too_but_never_the_whole_section():
    from app.services.report import trim_restatements

    earlier = [
        "4개 부서 742명을 대상으로 6주간 운영된 파일럿입니다. 월 검색량은 6.1만 건이었습니다."
    ]
    body = ("파일럿은 4개 부서에 소속된 총 742명을 대상으로 총 6주간 진행되었습니다. "
            "모든 검색의 평균 소요 시간을 추적했습니다.")
    trimmed, cut = trim_restatements(body, earlier)
    assert cut and trimmed.strip() == "모든 검색의 평균 소요 시간을 추적했습니다."
    only = "이번 파일럿은 총 4개 부서에 소속된 742명을 대상으로 6주 동안 진행되었습니다."
    assert trim_restatements(only, earlier) == (only, [])


def test_the_deck_facts_line_carries_the_same_finished_arithmetic():
    from app.services.deck import _facts_line

    line = _facts_line(
        "자체 운영(현행, 월 470만 원)과 관리형(월 690만 원)을 36개월 TCO로 비교하는 발표"
    )
    assert "470만 원 × 36개월 = 1억 6,920만 원" in line and "한 장에서만" in line
    assert "계산된 값" not in _facts_line("임직원 1,850명 대상 온보딩 발표")


def test_a_total_the_request_asked_for_is_added_to_the_cost_slide_when_the_deck_forgot_it():
    from app.services.deck import required_totals, restate_missing_facts

    request = ("비용 비교 발표 5장. 자체 운영(현행, 월 470만 원), 관리형(월 약 690만 원). "
               "연 예산 6,000만 원. 3년 TCO(36개월) 표 한 장 포함. 적어 준 수치만 써.")
    totals = required_totals(request)
    assert any(d == "16920" for _, d in totals) and any(d == "24840" for _, d in totals)
    slides = [
        {"title": "비용 비교", "layout": "title"},
        {"title": "월별 지출", "layout": "table",
         "rows": [["구분", "자체", "관리형"], ["월 비용(만원)", "470", "690"]]},
        {"title": "요약", "layout": "bullets", "bullets": ["연 예산 6,000만 원 안에서 현행 유지"]},
        {"title": "마무리", "layout": "closing"},
    ]
    changed = restate_missing_facts(slides, request)
    assert changed
    text = "\n".join(str(s) for s in slides)
    assert "1억 6,920만 원" in text and "2억 4,840만 원" in text
    # Already present: nothing added twice.
    assert restate_missing_facts(slides, request) == []


def test_prose_that_reads_its_own_table_back_and_a_pointer_to_a_missing_table_are_cut():
    from app.services.report import trim_restatements

    body = (
        "3년 TCO를 월 비용 × 36개월로 비교했습니다.\n\n"
        "| 대안 | 월 비용 | 3년 TCO |\n|---|---|---|\n| 자체 운영 | 470만 원 | 1억 6,920만 원 |\n"
        "| 관리형 | 690만 원 | 2억 4,840만 원 |\n\n"
        "자체 운영의 3년 TCO는 1억 6,920만 원이고 관리형은 2억 4,840만 원입니다. "
        "관리형은 현행보다 3년간 7,920만 원을 더 쓰게 됩니다."
    )
    trimmed, cut = trim_restatements(body, ["앞 절."])
    assert len(cut) == 1 and cut[0].startswith("자체 운영의 3년 TCO는")
    assert "7,920만 원을 더 쓰게" in trimmed and "| 관리형 | 690만 원" in trimmed
    no_table = (
        "위험은 세 가지입니다. 위 표는 각 위험의 담당 부서를 보여줍니다. 담당은 플랫폼팀입니다."
    )
    trimmed, cut = trim_restatements(no_table, ["앞 절."])
    assert cut == ["위 표는 각 위험의 담당 부서를 보여줍니다."]
    assert "담당은 플랫폼팀입니다" in trimmed


def test_a_table_count_does_not_unsettle_the_deck_count():
    from app.services.deck import requested_slides

    deck = "검색엔진 비용 비교 발표 5장을 만들어 줘. 3년 TCO(36개월) 표 한 장 포함."
    assert requested_slides(deck) == 5
    assert requested_slides("현황 발표 8장, 그림 두 장 넣어 줘") == 8
    assert requested_slides("표 한 장만 만들어 줘") is None


def test_a_single_number_said_again_with_nothing_new_is_cut_and_a_twice_drawn_figure_once():
    from app.services.report import drop_repeated_figures, trim_restatements

    earlier = ["현행 p95 응답 시간은 2.8초이고 목표는 1.5초입니다."]
    body = (
        "개선 방향입니다. 현행 p95는 2.8초입니다. 권한 필터를 쿼리 단계로 옮기면 지연이 줄어듭니다."
    )
    trimmed, cut = trim_restatements(body, earlier)
    assert cut == ["현행 p95는 2.8초입니다."] and "권한 필터를" in trimmed
    own = (
        "```mermaid\ngraph LR\n    A[크롤러] --> B[색인기<br>OpenSearch 3노드]\n"
        "    B --> C[검색 API]\n```\n"
    )
    planned = (
        "```mermaid\nflowchart LR\n    crawler[크롤러]\n    index[(색인기<br>OpenSearch 3노드)]\n"
        "    api(검색 API)\n    crawler --> index --> api\n```\n*그림: 전체 구조*\n"
    )
    once = drop_repeated_figures("설명.\n\n" + own + "\n더 설명.\n\n" + planned)
    assert once.count("```mermaid") == 1 and "*그림: 전체 구조*" in once and "더 설명." in once
    different = own.replace("크롤러", "결제").replace("색인기", "정산").replace("검색 API", "장부")
    assert drop_repeated_figures(own + "\n" + different).count("```mermaid") == 2


def test_a_total_written_before_its_formula_is_recomputed_from_the_formula():
    from app.services.report import fix_value_then_product

    text = (
        "| 자체 운영 | 470만 원 | 16억 9,200만 원 (470만 원 × 36개월) |\n"
        "| 구독 | 820만 원 | 29억 5,200만 원 (820만 원 × 36개월) |\n\n"
        "```kpi\n16억 9,200만 원 | 현행 3년 TCO\n```\n"
        "정확한 행: 2억 4,840만 원 (690만 원 × 36개월)."
    )
    fixed = fix_value_then_product(text)
    assert "1억 6,920만 원 (470만 원 × 36개월)" in fixed and "16억 9,200만" not in fixed
    assert "2억 9,520만 원 (820만 원 × 36개월)" in fixed
    assert "2억 4,840만 원 (690만 원 × 36개월)" in fixed  # already right, untouched


def test_the_sub_items_a_request_lists_for_a_part_appear_on_its_slide_in_the_users_words():
    from app.services.deck import keep_listed_items, listed_items

    request = (
        "신규 입사자 온보딩 안내 발표 6장을 만들어 줘. 내용: (1) 첫 주 일정 — 월 OT, 화 보안 교육, "
        "수 팀 소개, 목 장비 설정, 금 1:1 (2) 필수 계정 — 메일, 메신저, 위키, 문서검색 "
        "(3) 보안 규칙 — 외부 메일 첨부 금지, 2단계 인증, USB 금지 (4) 도움 받는 곳 — "
        "IT 헬프데스크 내선 1234, 인사팀 내선 5678. 표지·마무리 포함 6장."
    )
    items = dict(listed_items(request))
    assert items["필수 계정"] == ["메일", "메신저", "위키", "문서검색"]
    assert items["첫 주 일정"][-1] == "금 1:1"
    slides = [
        {"title": "신규 입사자 온보딩", "layout": "title"},
        {"title": "첫 주 일정", "layout": "steps",
         "steps": [["월", "OT"], ["화", "보안 교육"], ["수", "팀 소개"], ["목", "장비 설정"]]},
        {"title": "필수 계정", "layout": "bands",
         "bands": [["메일", "업무 메일"], ["사내 메신저", "협업"]]},
        {"title": "보안 규칙", "layout": "bullets",
         "bullets": ["외부 메일 첨부 금지", "2단계 인증 사용", "USB 사용 금지"]},
    ]
    changed = keep_listed_items(slides, request)
    assert sorted(changed) == [1, 2]
    assert slides[1]["steps"][-1][0] == "금 1:1"
    assert [b[0] for b in slides[2]["bands"]][-2:] == ["위키", "문서검색"]
    # The security slide already carries every listed rule (as substrings): untouched.
    assert keep_listed_items(slides, request) == []
    # A rule the writer phrased its own way (「USB: 개인 USB 꽂지 않기」) is not re-added.
    phrased = [{"title": "보안 규칙", "layout": "bullets",
                "bullets": ["외부 첨부: 외부 메일에 첨부 보내지 않기",
                            "2단계 인증: 모든 계정에 활성화", "USB: 개인 USB 꽂지 않기"]}]
    assert keep_listed_items(phrased, request) == []


def test_a_short_list_stays_whole_under_a_figure_and_a_spilled_item_counts_as_missing():
    from app.services.deck import _words_under_figure, keep_listed_items

    slide = {
        "title": "첫 주 일정", "layout": "steps", "diagram": {"source": "x"},
        "steps": [["월", "OT"], ["화", "보안 교육"], ["수", "팀 소개"], ["목", "장비 설정"],
                  ["금", "1:1"]],
    }
    _words_under_figure(slide)
    assert slide["layout"] == "bullets" and len(slide["bullets"]) == 5 and not slide.get("notes")
    long_line = (
        "이 항목은 설명이 길어서 한 줄에 다 들어가지 않을 만큼 많은 말을 담고 있습니다 정말로"
    )
    long_items = {"title": "설명", "layout": "bullets", "diagram": {"source": "x"},
                  "bullets": [long_line] * 5}
    _words_under_figure(long_items)
    assert len(long_items["bullets"]) == 3 and long_items["notes"]
    # An item that only survives in the notes is put back on the slide.
    request = (
        "온보딩 발표 6장. 내용: (1) 첫 주 일정 — 월 OT, 화 보안 교육, 수 팀 소개, "
        "목 장비 설정, 금 1:1 (2) 필수 계정 — 메일, 위키"
    )
    spilled = [{"title": "첫 주 일정", "layout": "bullets",
                "bullets": ["월요일 – OT", "화요일 – 보안 교육", "수요일 – 팀 소개"],
                "notes": "목요일 – 장비 설정\n금요일 – 1:1 면담"}]
    assert keep_listed_items(spilled, request) == [0]
    assert spilled[0]["bullets"][-2:] == ["목 장비 설정", "금 1:1"]


def test_a_parts_own_words_do_not_read_as_a_merge_ask_and_an_asked_for_agenda_stays():
    from app.services.deck import keep_enumerated

    request = (
        "사내 문서검색 시스템 소개 발표 9장을(표지·목차 포함) 만들어 줘. 내용: "
        "(1) 왜 만들었나 — 문서가 흩어짐 (2) 전체 구조 — 크롤러 → 색인기 "
        "(3) 검색 요청 흐름 — 웹 → API → 결과 병합 (4) 성능 — p95 "
        "(5) 권한 모델 — 부서·직급 (6) 운영 — hwp 7% (7) 로드맵 — 9/22 오픈."
    )
    plan = [
        {"title": "사내 문서검색 시스템 개요", "layout": "title"},
        {"title": "목차", "layout": "agenda"},
        {"title": "배경: 분산된 문서", "layout": "bullets"},
        {"title": "전체 아키텍처", "layout": "bullets"},
        {"title": "검색 요청 처리 흐름", "layout": "steps"},
        {"title": "성능 지표", "layout": "chart"},
        {"title": "권한 모델: 규칙", "layout": "bullets"},
        {"title": "운영 현황 및 개선 일정", "layout": "bullets"},
        {"title": "마무리", "layout": "closing"},
    ]
    titles = [s["title"] for s in keep_enumerated(plan, request, 9)]
    # 「결과 병합」 is a part's content, not an instruction to merge: the renames happen.
    assert titles[2] == "왜 만들었나" and titles[3] == "전체 구조"
    # No room for a tenth slide and the agenda is asked for: the roadmap stays merged.
    assert len(titles) == 9 and "목차" in titles


def test_a_note_that_reads_terse_steps_aloud_is_an_echo_too():
    from app.services.deck import notes_without_echo

    slide = {"title": "첫 주 일정", "layout": "steps",
             "steps": [["월", "OT"], ["화", "보안 교육"], ["수", "팀 소개"]]}
    notes = ("월요일에는 OT로 시작합니다. 화요일에는 보안 교육이 잡혀 있어요. "
             "첫 주는 가볍게 듣기만 하셔도 충분하니 부담 갖지 마세요.")
    kept = "첫 주는 가볍게 듣기만 하셔도 충분하니 부담 갖지 마세요."
    assert notes_without_echo(notes, slide) == kept


def test_notes_answered_as_a_json_object_are_read_for_their_notes():
    from app.services.deck import _plain_notes

    blob = (
        '{ "slides": [ { "title": "도움 받는 곳", "bullets": [], '
        '"speaker_notes": "문제가 생기면 화면의 연락처를 바로 확인하세요.\\n'
        '다음 장으로 갑니다." } ] }'
    )
    spoken = "문제가 생기면 화면의 연락처를 바로 확인하세요. 다음 장으로 갑니다."
    assert _plain_notes(blob) == spoken
    assert _plain_notes('{"notes": ["첫 문장.", "둘째 문장."]}') == "첫 문장. 둘째 문장."
    assert _plain_notes("그냥 문장입니다.") == "그냥 문장입니다."


def test_step_slides_may_get_a_flow_diagram_when_the_request_asks_for_pictures():
    import inspect

    from app.services import deck, diagrams

    assert diagrams.asks_for_diagrams("구조와 흐름은 그림으로 그려 줘")
    source = inspect.getsource(deck._draw_figures)
    assert '"steps") if asked' in source and "asks_for_diagrams(request)" in source


def test_a_restated_total_outranks_the_original_count_and_a_picture_ask_is_a_diagram_ask():
    from app.services import diagrams
    from app.services.deck import requested_slides

    merged = (
        "현황 보고 발표를 만들어 줘. 장수 8장, 청중 CIO. 장수는 6장으로 맞춘다. "
        "지표와 진척은 한 장으로."
    )
    assert requested_slides(merged) == 6
    assert diagrams.asks_for_diagrams("구조와 흐름은 그림으로")
    assert diagrams.asks_for_diagrams("권한 모델 — 부서·직급 기반. 구조와 흐름은 그림으로.")
    assert not diagrams.asks_for_diagrams("표지에 회사 로고 그림을 넣어 줘")


def test_a_table_answer_is_read_however_the_writer_shaped_it():
    from app.services.deck import _json_object, _rows_any

    cut = (
        '{"rows": [["위험", "영향", "대응"], ["권한 필터 지연", "오픈 지연", "인력 투입"], '
        '["노드 증설 미승인", "성능 저'
    )
    parsed = _json_object(cut)
    head = [["위험", "영향", "대응"], ["권한 필터 지연", "오픈 지연", "인력 투입"]]
    assert _rows_any(parsed, cut)[:2] == head
    objects = {"table": [{"위험": "a", "영향": "b"}, {"위험": "c", "영향": "d"}]}
    assert _rows_any(objects, "") == [["위험", "영향"], ["a", "b"], ["c", "d"]]
    piped = "| 위험 | 영향 |\n|---|---|\n| a | b |\n| c | d |"
    assert _rows_any({}, piped) == [["위험", "영향"], ["a", "b"], ["c", "d"]]


def test_a_restructure_shows_the_writer_what_each_part_says():
    from app.services.revise import outline_block

    block = outline_block(
        ["위험", "다음 단계"],
        [
            "위험 | 영향 | 대응\n권한 필터 개선 지연 | 오픈 지연 | 인력 투입",
            "9/8 권한 필터 개선 배포",
        ],
    )
    assert "1. 위험\n   내용: 위험 | 영향 | 대응 권한 필터 개선 지연" in block
    assert "2. 다음 단계\n   내용: 9/8 권한 필터 개선 배포" in block
    assert "요청 원문에 없는 것도 지금 문서에 있으면 남긴다" in block
    # Without texts the block is the bare skeleton it always was.
    assert "내용:" not in outline_block(["위험", "다음 단계"])


def test_the_document_on_screen_is_material_for_a_restructure():
    from app.services.revise import document_block

    block = document_block(
        ["표지", "위험", "다음 단계"], ["", "권한 필터 개선 지연 | 오픈 지연", "9/8 배포"]
    )
    assert block.startswith("# 지금 문서의 내용")
    assert "## 위험\n권한 필터 개선 지연 | 오픈 지연" in block and "## 다음 단계\n9/8 배포" in block
    assert "## 표지" not in block


def test_a_clause_with_two_numbers_is_restated_once():
    from app.services.deck import restate_missing_facts

    request = "현황 보고. 파일럿 4개 부서 742명. 월 검색 6.1만 건, p95 2.8초."
    slides = [
        {"title": "표지", "layout": "title"},
        {"title": "요청 사항", "layout": "bullets", "bullets": ["p95 개선 승인"]},
        {"title": "지표", "layout": "bullets", "bullets": ["월 6.1만 건", "p95 2.8초"]},
    ]
    restate_missing_facts(slides, request)
    added = [b for b in slides[1]["bullets"] + slides[2]["bullets"] if "742" in b]
    assert len(added) == 1


def test_a_closing_slide_is_not_titled_with_a_farewell():
    from app.services.deck import closing_title

    assert closing_title("안녕히 가십시오") == "마무리"
    assert closing_title("수고하셨습니다!") == "마무리"
    assert closing_title("") == "마무리"
    # Conventional closing headings stay.
    assert closing_title("감사합니다") == "감사합니다"
    assert closing_title("요약 및 다음 단계") == "요약 및 다음 단계"


def test_a_restarted_phrase_keeps_its_finished_half():
    from app.services.report import undouble_words

    assert undouble_words("협업 방향으로 방향을 잡는다.") == "협업 방향을 잡는다."
    assert undouble_words("비용이 비용을 낳는다.") == "비용이 비용을 낳는다."
    # Legitimate repetition across a boundary and table cells are untouched.
    assert undouble_words("| 방향으로 | 방향을 |") == "| 방향으로 | 방향을 |"
    assert undouble_words("```\nx로 x를\n```") == "```\nx로 x를\n```"
    assert undouble_words("서울에서 서울의 미래를") == "서울의 미래를"


def test_a_section_does_not_read_its_own_table_aloud():
    from app.services.report import trim_table_echo

    body = (
        "캐시가 8에서 256으로 커지는 동안 적중률은 0.345에서 0.930으로 오릅니다. "
        "8에서 16으로 갈 때는 0.521 - 0.345 = 0.176의 큰 폭으로 올라갑니다. "
        "반면 128에서 256으로 갈 때는 0.018로 증가 폭이 크게 줄었습니다.\n\n"
        "| 구간 | 변화량 |\n| :--- | :--- |\n| 8 → 16 | 0.176 |\n| 128 → 256 | 0.018 |\n\n"
        "이 변화는 캐시가 커질수록 추가 공간의 효과가 줄어드는 경향을 보여 줍니다."
    )
    trimmed = trim_table_echo(body)
    assert "0.521" not in trimmed and "0.018로 증가 폭" not in trimmed
    assert trimmed.startswith("캐시가 8에서 256으로") and trimmed.rstrip().endswith("보여 줍니다.")
    assert "| 8 → 16 | 0.176 |" in trimmed
    # A section with no table, or one whose prose judges rather than repeats, is untouched.
    plain = "지연은 2.8초였고 목표는 1.5초입니다."
    assert trim_table_echo(plain) == plain
    judged = "| p95 |\n| 2.8초 |\n| 1.5초 |\n\n2.8초는 체감 지연의 경계를 넘는 수치입니다."
    assert trim_table_echo(judged) == judged


def test_a_section_does_not_repeat_its_own_numbers():
    from app.services.report import trim_restatements

    body = (
        "캐시가 64에서 128로 커지는 구간에서는 명중률이 0.853에서 0.912로 올라 "
        "상승 폭이 줄어듭니다. 이는 캐시가 커질수록 추가 공간의 기여가 낮아지는 경향을 "
        "시사합니다. 데이터에서 확인되는 바와 같이 캐시가 64에서 128로 2배가 될 때 "
        "명중률은 0.853에서 0.912로 증가합니다."
    )
    trimmed, cut = trim_restatements(body, [])
    assert len(cut) == 1 and cut[0].startswith("데이터에서 확인되는")
    assert trimmed.strip().endswith("시사합니다.")
    # A single sentence, or sentences with their own numbers, stay whole.
    one = "p95는 2.8초에서 1.5초로 줄었습니다."
    assert trim_restatements(one, [])[0] == one


def test_shown_arithmetic_becomes_its_result_even_without_a_table():
    from app.services.report import trim_table_echo

    body = "8에서 16으로 갈 때 적중률 증가는 0.521 - 0.345 = 0.176으로 측정됩니다."
    assert trim_table_echo(body) == "8에서 16으로 갈 때 적중률 증가는 0.176으로 측정됩니다."
    # A cost working is asked for and stays, in prose and in a table alike.
    cost = "현행은 월 470만 원 × 36개월 = 1억 6,920만 원입니다."
    assert trim_table_echo(cost) == cost
    table = "| 안 | 식 |\n|---|---|\n| 현행 | 470만 × 36 = 16,920만 |"
    assert trim_table_echo(table) == table


def test_notes_written_as_an_object_with_content_are_their_text():
    from app.services.deck import _notes_in_json, _plain_notes

    blob = (
        '{ "title": "진척 현황 발표 노트", '
        '"content": "검색 API가 완료되어 오픈 준비가 순조롭습니다." }'
    )
    assert _notes_in_json(blob) == "검색 API가 완료되어 오픈 준비가 순조롭습니다."
    assert _plain_notes(blob) == "검색 API가 완료되어 오픈 준비가 순조롭습니다."


def test_a_salvaged_paragraph_splits_on_dropped_full_stops():
    from app.services.deck import _salvaged_bullets

    data = {
        "title": "진척 현황",
        "summary": "진척 현황을 표로 정리했습니다 검색 API는 완료되었습니다 결과 화면은 60%입니다",
    }
    assert _salvaged_bullets(data) == [
        "진척 현황을 표로 정리했습니다", "검색 API는 완료되었습니다", "결과 화면은 60%입니다"
    ]


def test_a_merged_table_slide_keeps_the_rows_it_absorbed_and_the_agenda_follows():
    from app.services.revise import absorb_rows, refresh_agenda

    old = [
        {"title": "주요 이슈", "layout": "table",
         "rows": [["이슈", "현황"], ["p95 지연", "미달"], ["조직도 API", "검토 중"]]},
        {"title": "위험", "layout": "table",
         "rows": [["위험", "영향", "대응"], ["권한 필터 개선 지연", "오픈 지연", "인력 투입"],
                  ["노드 증설 미승인", "성능 병목", "재요청"],
                  ["겸직자 기준 미정", "권한 오류", "회신 대기"]]},
        {"title": "목차", "layout": "agenda", "bullets": ["주요 이슈", "위험", "요청 사항"]},
    ]
    new = [
        {"title": "표지", "layout": "title"},
        {"title": "목차", "layout": "agenda", "bullets": ["주요 이슈", "위험", "요청 사항"]},
        {"title": "이슈와 위험", "layout": "table",
         "rows": [["이슈", "현황", "시점"], ["p95 지연", "권한 필터 개선", "9/11"]]},
        {"title": "요청 사항", "layout": "bullets", "bullets": ["승인"]},
    ]
    note = "전체를 6장으로 줄여 줘. 이슈·위험도 한 장으로. 장수는 6장으로 맞춘다."
    out = refresh_agenda(absorb_rows(new, old, mentioned=note))
    rows = out[2]["rows"]
    firsts = [r[0] for r in rows]
    assert "노드 증설 미승인" in firsts and "겸직자 기준 미정" in firsts
    # 「권한 필터 개선 지연」 is already there as 「권한 필터 개선」: not added twice.
    assert "권한 필터 개선 지연" not in firsts and all(len(r) == 3 for r in rows)
    assert out[1]["bullets"] == ["이슈와 위험", "요청 사항"]


def test_merging_two_parts_into_one_slide_is_not_a_deck_total():
    from app.services.deck import requested_slides

    ask = "전체를 6장으로 줄여 줘. 지표와 진척은 한 장으로 합치고, 이슈·위험도 한 장으로."
    assert requested_slides(ask) == 6
    assert requested_slides("현황 보고 발표. 6장으로 줄인다. 지표와 진척은 한 장으로 묶어.") == 6
    # A lone 「한 장」 that is the whole ask still counts.
    assert requested_slides("요약을 한 장으로 만들어 줘.") == 1


def test_a_generic_word_in_the_note_does_not_mark_a_slide_as_changed():
    from app.services.revise import carry_parts

    old = [{"id": "a", "title": "다음 단계", "layout": "timeline",
            "timeline": [["9/8", "배포"], ["9/15", "재색인"]]}]
    new = [{"id": "b", "title": "다음 단계", "layout": "closing", "bullets": ["감사"]}]
    note = "전체를 6장으로 줄여 줘. 지표와 진척은 한 장으로. 다음 구성은 6장이다."
    out, carried = carry_parts(new, old, is_deck=True, mentioned=note)
    assert carried == 1 and out[0]["layout"] == "timeline" and out[0]["id"] == "b"
    # Naming the part itself does change it.
    out, carried = carry_parts(new, old, is_deck=True, mentioned="다음 단계를 마무리로 바꿔")
    assert carried == 0


def test_a_timeline_keeps_every_date_the_instruction_listed():
    from app.services.deck import keep_listed_dates

    typed = (
        "다음 단계 장을 연표로 바꿔 줘. 9/8 권한 필터 개선 배포, 9/11 겸직자 기준 회신, "
        "9/15 무중단 재색인, 9/22 오픈."
    )
    slide = {
        "title": "다음 단계", "layout": "timeline",
        "timeline": [
            ["9/8", "권한 필터 개선 배포"], ["9/11", "겸직자 기준 회신"], ["9/22", "오픈"],
        ],
    }
    out = keep_listed_dates(slide, typed)
    assert [p[0] for p in out["timeline"]] == ["9/8", "9/11", "9/15", "9/22"]
    assert out["timeline"][2] == ["9/15", "무중단 재색인"]
    # Nothing listed, or nothing missing: untouched.
    assert keep_listed_dates(slide, "연표로 바꿔 줘") is slide


def test_parts_listed_with_arrows_name_the_slides_and_a_statement_is_the_one_liner():
    from app.services.deck import arrow_parts, keep_enumerated

    request = (
        "현황 보고 발표를 만들어 줘. 장수 8장. 한 줄 요약 → 진척(표) → 지표(큰 숫자) → "
        "이슈 셋(표) → 요청 사항 → 다음 단계 순서. 적어 준 수치만 써."
    )
    assert arrow_parts(request) == ["한 줄 요약", "진척", "지표", "이슈", "요청 사항", "다음 단계"]
    assert arrow_parts("세미나 6장 발표를 만들어 줘: 제목 → 질문(RQ1) → 방법 → 결과 → 한계.") == [
        "제목", "질문", "방법", "결과", "한계"
    ]
    # A flow inside one part is content, not the deck's order.
    assert arrow_parts("(3) 검색 요청 흐름 — 웹 → API → 결과 병합 (4) 성능") == []
    plan = [
        {"title": "문서검색 시스템 구축 현황", "layout": "title"},
        {"title": "기초 기능 완료", "layout": "statement"},
        {"title": "단계별 진척", "layout": "table"},
        {"title": "핵심 운영 지표", "layout": "metrics"},
        {"title": "미해결 이슈 3건", "layout": "table"},
        {"title": "결정 요청 사항", "layout": "bullets"},
        {"title": "오픈 전 일정", "layout": "timeline"},
        {"title": "확인 요청", "layout": "closing"},
    ]
    titles = [s["title"] for s in keep_enumerated(plan, request, 8)]
    # The statement stays the one-liner; the planner's 「오픈 전 일정」 is 「다음 단계」.
    assert titles[1] == "기초 기능 완료" and titles[6] == "다음 단계" and len(titles) == 8


def test_a_counted_amount_the_request_never_gave_leaves_the_table_cell():
    from app.services.deck import _facts_set, ground_cells

    facts = _facts_set("크롤러 hwp 3천 건(7%) 추출 실패. 결과 화면 60%. 월 45만 원.")
    rows = [
        ["항목", "현황", "비고"], ["hwp 추출", "3천 건 중 7% 실패", "크롤러에서 95건 오류 발생"],
        ["결과 화면", "60% 완료", "남은 작업 40%"], ["노드", "1대 증설", "월 45만 원"],
    ]
    out = ground_cells(rows, facts)
    assert out[1] == ["hwp 추출", "3천 건 중 7% 실패", "크롤러에서 오류 발생"]
    assert out[2] == ["결과 화면", "60% 완료", "남은 작업 40%"]
    assert out[3][2] == "월 45만 원" and out[0] == rows[0]


def test_a_renamed_timeline_keeps_the_dates_the_person_listed():
    from app.services.revise import absorb_rows, carry_by_content

    old = [
        {"title": "다음 단계", "layout": "timeline",
         "timeline": [["9/8", "배포"], ["9/11", "회신"], ["9/15", "재색인"], ["9/22", "오픈"]]},
        {"title": "위험", "layout": "table",
         "rows": [["위험", "영향", "대응"], ["노드 증설 미승인", "병목", "재요청"]]},
    ]
    new = [
        {"title": "오픈까지 남은 일정", "layout": "timeline",
         "timeline": [["9/11", "p95 개선 완료"], ["9/22", "파일럿 오픈"]]},
        {"title": "리소스 및 보안 이슈", "layout": "table",
         "rows": [["이슈", "현황", "시점"], ["p95 미달", "개선 중", "9/11"]]},
    ]
    note = "전체를 6장으로 줄여 줘. 지표와 진척은 한 장으로 합치고, 이슈·위험도 한 장으로."
    out = carry_by_content(absorb_rows(new, old, mentioned=note), old, mentioned=note)
    assert [p[0] for p in out[0]["timeline"]] == ["9/8", "9/11", "9/15", "9/22"]
    assert out[0]["title"] == "오픈까지 남은 일정"
    # The gone 「위험」 table's row went to the slide named with it in the same phrase.
    assert out[1]["rows"][-1][0] == "노드 증설 미승인"


def test_under_only_what_i_wrote_the_notes_add_no_claim():
    from app.services.deck import notes_only_given

    request = (
        "온보딩 발표 6장. 보안 규칙: 외부 메일 첨부 금지, 2단계 인증, USB 금지. 적어 준 것만 써."
    )
    slide = {"title": "보안 규칙", "layout": "bullets",
             "bullets": ["외부 메일 첨부 금지", "2단계 인증 활성화", "USB 사용 금지"]}
    notes = "세 가지 보안 규칙을 꼭 지켜 주세요. 위반 시 계정 정지 등 제재가 따릅니다."
    assert notes_only_given(notes, slide, request) == "세 가지 보안 규칙을 꼭 지켜 주세요."
    # Without the condition the notes are the writer's to shape.
    assert notes_only_given(notes, slide, "온보딩 발표 6장.") == notes


def test_a_table_from_a_csv_keeps_every_category_with_the_persons_word_for_an_empty_cell():
    from app.services.deck import fill_csv_categories

    csv = (
        "workload\tbaseline_ms\tours_ms\toverhead_pct\tdetected\ttotal_attacks\n"
        "nginx\t120\t123.8\t3.2\t48\t50\nredis\t8\t8.3\t3.7\t50\t50\n"
        "sqlite\t45\t46.3\t2.8\t\t\nffmpeg\t900\t932\t3.6\t45\t50\n"
    )
    request = "CSV에 없는 값은 만들지 말고, 비어 있는 값은 「측정 안 함」으로."
    slides = [
        {"title": "표지", "layout": "title"},
        {"title": "공격 150건 중 143건 탐지", "layout": "table",
         "rows": [["워크로드", "검출", "총 공격"], ["nginx", "48", "50"], ["redis", "50", "50"],
                  ["ffmpeg", "45", "50"]]},
    ]
    assert fill_csv_categories(slides, [csv], request) == [1]
    assert slides[1]["rows"] == [
        ["워크로드", "검출", "총 공격"], ["nginx", "48", "50"], ["redis", "50", "50"],
        ["sqlite", "측정 안 함", "측정 안 함"], ["ffmpeg", "45", "50"],
    ]
    # A table about something else (one match) is left alone.
    other = [
        {"title": "비교", "layout": "table",
         "rows": [["항목", "값"], ["nginx", "1"], ["기타", "2"]]}
    ]
    assert fill_csv_categories(other, [csv], request) == []


def test_a_difference_of_two_ratios_is_not_percentage_points():
    from app.services.report import fix_point_units

    ratio = "캐시가 64에서 128로 2배가 될 때 적중률은 0.853에서 0.912로 0.059%p 증가합니다."
    fixed = "캐시가 64에서 128로 2배가 될 때 적중률은 0.853에서 0.912로 0.059 증가합니다."
    assert fix_point_units(ratio) == fixed
    percent = "점유율은 48%에서 52%로 4%p 올랐습니다."
    assert fix_point_units(percent) == percent
    # One ratio only: not enough evidence; the sentence stands.
    lone = "오차는 0.5%p 안쪽입니다."
    assert fix_point_units(lone) == lone


def test_re_asked_notes_join_what_is_already_said_without_repeating_it():
    from app.services.deck import _join_notes, _sentence_count

    have = "첫 주 일정은 월요일 OT에서 시작합니다."
    added = "첫 주 일정은 월요일 OT에서 시작합니다. 금요일 1:1 면담에서 질문을 모아 두세요."
    joined = _join_notes(have, added)
    assert _sentence_count(joined) == 2 and joined.endswith("모아 두세요.")
    assert _join_notes("", added) == added and _sentence_count("") == 0


def test_an_unasked_closing_gives_way_to_a_listed_part():
    from app.services.deck import keep_enumerated

    request = (
        "사내 문서검색 시스템 소개 발표 9장을(표지·목차 포함) 만들어 줘. 내용: "
        "(1) 왜 만들었나 (2) 전체 구조 (3) 검색 요청 흐름 (4) 성능 (5) 권한 모델 "
        "(6) 운영 (7) 로드맵."
    )
    plan = [
        {"title": "사내 문서검색 시스템", "layout": "title"},
        {"title": "발표 순서", "layout": "agenda"},
        {"title": "왜 만들었나", "layout": "bullets"},
        {"title": "검색 요청 흐름", "layout": "steps"},
        {"title": "성능 지표", "layout": "chart"},
        {"title": "권한 모델", "layout": "bullets"},
        {"title": "운영 현황", "layout": "bullets"},
        {"title": "로드맵", "layout": "timeline"},
        {"title": "마무리", "layout": "closing"},
    ]
    titles = [s["title"] for s in keep_enumerated(plan, request, 9)]
    assert "전체 구조" in titles and "마무리" not in titles and len(titles) == 9


def test_a_shown_percentage_formula_is_checked_by_arithmetic():
    from app.services.report import fix_percent_formulas

    body = (
        "14분에서 3분으로 줄어들면 검색 시간 단축률은 (14-3) ÷ 14 × 100 = 1,400%입니다.\n\n"
        "```kpi\n1,400% | 검색 시간 단축률\n```"
    )
    fixed = fix_percent_formulas(body)
    assert "(14-3) ÷ 14 × 100 = 78.6%" in fixed and "1,400%" not in fixed
    assert "78.6% | 검색" in fixed
    right = "만족도 4.1 ÷ 5 × 100 = 82%로 높았다."
    assert fix_percent_formulas(right) == right


def test_the_95_of_p95_does_not_vouch_for_95_cases_and_a_covered_row_is_not_absorbed_twice():
    from app.services.deck import _facts_set, ground_cells
    from app.services.revise import absorb_rows

    given = "월 검색 6.1만 건, p95 2.8초(목표 1.5초), 파일럿 742명."
    rows = [
        ["구분", "상태", "비고"], ["월 검색", "6.1만건", "95건"], ["p95", "2.8초", "742명 대상"],
    ]
    out = ground_cells(rows, _facts_set(given), given)
    assert out[1] == ["월 검색", "6.1만건", "—"] and out[2][2] == "742명 대상"
    old = [{"title": "위험", "layout": "table",
            "rows": [["위험", "영향", "대응"], ["p95 응답시간 미달", "지연", "9/11"],
                     ["노드 증설 미승인", "병목", "재요청"]]}]
    new = [{"title": "이슈와 위험", "layout": "table",
            "rows": [["이슈", "원인", "대응"], ["p95 2.8초", "권한 필터", "9/11"]]}]
    out = absorb_rows(new, old, mentioned="이슈·위험도 한 장으로")
    assert [r[0] for r in out[0]["rows"]] == ["이슈", "p95 2.8초", "노드 증설 미승인"]


def test_a_date_and_the_word_improve_are_not_counted_amounts():
    from app.services.deck import _facts_set, ground_cells

    given = "p95 미달은 9/11 개선 완료 예정. 월 45만 원."
    rows = [["이슈", "조치"], ["p95 미달", "9/11 개선 완료 예정"], ["증설", "월 45만 원 필요"]]
    assert ground_cells(rows, _facts_set(given), given) == rows


def test_rows_that_strayed_into_another_table_go_back_out_and_a_timeline_survives_a_redraw():
    from app.services.revise import keep_timeline_layout, unmix_rows

    old = [
        {"title": "진척 현황", "layout": "table",
         "rows": [["항목", "진행"], ["검색 API", "100%"], ["결과 화면", "60%"]]},
        {"title": "다음 단계", "layout": "timeline",
         "timeline": [["9/8", "배포"], ["9/11", "회신"], ["9/15", "재색인"], ["9/22", "오픈"]]},
    ]
    new = [
        {"title": "진척 및 핵심 지표", "layout": "metrics", "metrics": [["742명", "참여"]]},
        {"title": "이슈 및 위험", "layout": "table",
         "rows": [["이슈", "상태"], ["p95 미달", "지연"], ["보안 검토", "미완"],
                  ["인덱스 증설", "대기"], ["검색 API", "100%"], ["결과 화면", "60%"]]},
        {"title": "다음 단계", "layout": "steps", "steps": [["1", "개선"], ["2", "오픈"]]},
    ]
    note = "전체를 6장으로 줄여 줘. 지표와 진척은 한 장으로 합치고, 이슈·위험도 한 장으로."
    out = keep_timeline_layout(unmix_rows(new, old, mentioned=note), old, mentioned=note)
    assert [r[0] for r in out[1]["rows"]] == ["이슈", "p95 미달", "보안 검토", "인덱스 증설"]
    assert out[2]["layout"] == "timeline" and [p[0] for p in out[2]["timeline"]] == [
        "9/8", "9/11", "9/15", "9/22"
    ]
    assert "steps" not in out[2]


def test_a_leading_conclusion_does_not_relist_and_a_sized_section_keeps_its_count():
    from app.services.report import (
        enforce_sentence_counts,
        requested_sentence_counts,
        trim_leading_conclusion,
    )

    sections = [
        {"heading": "결론", "format": "markdown", "content": (
            "전사 확대를 권고합니다. 파일럿은 4개 부서 742명이 6주 동안 썼고 "
            "평균 검색 시간은 14분에서 3분으로 줄었습니다. 만족도는 4.1점이었습니다."
        )},
        {"heading": "결과", "format": "markdown", "content": (
            "4개 부서 742명이 6주 동안 참여했고 평균 검색 시간은 14분에서 3분으로 줄었다. "
            "만족도는 5점 만점에 4.1점."
        )},
    ]
    out = trim_leading_conclusion(sections)
    assert out[0]["content"].strip() == "전사 확대를 권고합니다."
    assert requested_sentence_counts("요약은 세 문장, 결론 두 문장으로.") == {"요약": 3, "결론": 2}
    sized = [{"heading": "요약", "format": "markdown", "content": (
        "첫째 문장입니다. 둘째 문장입니다. 셋째 문장입니다. 넷째 문장입니다.\n\n"
        "| 표 | 값 |\n|---|---|\n| a | 1 |"
    )}]
    out = enforce_sentence_counts(sized, "요약은 세 문장으로.")
    assert out[0]["content"].startswith("첫째 문장입니다. 둘째 문장입니다. 셋째 문장입니다.\n")
    assert "넷째" not in out[0]["content"] and "| a | 1 |" in out[0]["content"]


def test_an_explicit_add_is_an_insert_without_the_planner():
    from app.services.revise import explicit_insert

    parts = [
        "표지", "목차", "한 줄 요약", "진척 현황", "핵심 지표", "이슈", "요청 사항", "다음 단계"
    ]
    ask = (
        "위험 장을 하나 추가해 줘. 표로, 열은 위험·영향·대응, 행은 권한 필터 개선 지연·"
        "노드 증설 미승인·겸직자 열람 기준 미정. 요청 사항 장 앞에."
    )
    plan = explicit_insert(ask, parts)
    assert plan is not None and plan.scope == "insert" and plan.names == ["위험"]
    assert plan.after == 5  # after 「이슈」, i.e. before 「요청 사항」
    assert explicit_insert("결론 뒤에 한계 절을 넣어 줘.", ["서론", "결론"]).names == ["한계"]
    # Restructures, existing parts and several parts stay with the planner.
    assert explicit_insert("전체를 6장으로 줄여 줘. 위험 장 추가.", parts) is None
    assert explicit_insert("이슈 장을 추가해 줘", parts) is None
    assert explicit_insert("위험 장을 두 개 추가해 줘", parts) is None


def test_a_table_that_says_one_thing_twice_keeps_the_first_and_frees_the_slot():
    from app.services.deck import dedupe_rows
    from app.services.revise import absorb_rows, carry_by_content

    rows = [
        ["이슈", "상세", "리스크"],
        ["p95 미달", "권한 필터 쿼리 단계 이동(9/11)", "성능 저하"],
        ["인덱스 노드", "1대 증설, 월 45만 원", "비용 증가"],
        ["인프라 증설", "인덱스 노드 1대(월 45만 원)", "결정 대기"],
        ["권한 필터 지연", "p95 미달", "9/11 완료"],
    ]
    assert [r[0] for r in dedupe_rows(rows)] == ["이슈", "p95 미달", "인덱스 노드"]
    old = [
        {"title": "위험", "layout": "table",
         "rows": [["위험", "영향", "대응"], ["노드 증설 미승인", "병목", "재요청"],
                  ["겸직자 열람 기준 미정", "권한 오류", "회신 대기"]]},
        {"title": "다음 단계", "layout": "timeline",
         "timeline": [["9/8", "배포"], ["9/11", "회신"], ["9/15", "재색인"], ["9/22", "오픈"]]},
    ]
    new = [
        {"title": "미해결 이슈와 위험", "layout": "table", "rows": [list(r) for r in rows]},
        {"title": "요청 사항과 다음 단계", "layout": "timeline",
         "timeline": [["9/11", "p95 개선 완료"], ["9/22", "시스템 오픈"]]},
    ]
    note = "6장으로 줄여 줘. 이슈·위험도 한 장으로. 요청 사항과 다음 단계를 한 장으로."
    out = carry_by_content(absorb_rows(new, old, mentioned=note), old, mentioned=note)
    firsts = [r[0] for r in out[0]["rows"]]
    assert "노드 증설 미승인" in firsts and "겸직자 열람 기준 미정" in firsts
    assert [p[0] for p in out[1]["timeline"]] == ["9/8", "9/11", "9/15", "9/22"]


def test_a_timeline_dropped_to_make_the_count_takes_an_unasked_agendas_slot():
    from app.services.revise import keep_timeline_layout

    old = [{"id": "t9", "title": "다음 단계", "layout": "timeline",
            "timeline": [["9/8", "배포"], ["9/11", "회신"], ["9/15", "재색인"], ["9/22", "오픈"]]}]
    new = [
        {"id": "a", "title": "현황", "layout": "title"},
        {"id": "b", "title": "발표 순서", "layout": "agenda", "bullets": ["…"]},
        {"id": "c", "title": "9/22 정상 오픈", "layout": "statement"},
        {"id": "d", "title": "진척 및 핵심 지표", "layout": "table", "rows": [["a"], ["b"]]},
        {"id": "e", "title": "이슈 및 위험", "layout": "table", "rows": [["a"], ["b"]]},
        {"id": "f", "title": "요청 사항", "layout": "bullets", "bullets": ["승인"]},
    ]
    note = "전체를 6장으로 줄여 줘. 지표와 진척은 한 장으로 합치고, 이슈·위험도 한 장으로."
    request = (
        "현황 보고 발표. 한 줄 요약 → 진척(표) → 지표 → 이슈 셋(표) → 요청 사항 → "
        "다음 단계 순서."
    )
    out = keep_timeline_layout(new, old, mentioned=note, request=request)
    assert [s["layout"] for s in out] == [
        "title", "statement", "table", "table", "bullets", "timeline"
    ]
    assert [p[0] for p in out[-1]["timeline"]] == ["9/8", "9/11", "9/15", "9/22"]


def test_under_only_what_i_wrote_a_slide_with_nothing_to_add_keeps_its_read_out():
    from app.services.deck import notes_only_given, notes_without_echo

    request = "도움: IT 헬프데스크 내선 1234, 인사팀 내선 5678. 적어 준 것만 써."
    slide = {"title": "도움 받는 곳", "layout": "bullets",
             "bullets": ["IT 헬프데스크 내선 1234", "인사팀 내선 5678"]}
    notes = "IT 헬프데스크는 내선 1234입니다. 인사팀은 내선 5678입니다."
    assert notes_only_given(notes_without_echo(notes, slide), slide, request) == ""
    assert notes_only_given(notes, slide, request) == notes


def test_a_ledger_amount_off_by_a_power_of_ten_is_the_ledger_amount():
    from app.services.report import fix_ledger_magnitudes

    request = (
        "자체 운영 OpenSearch(현행, 월 470만 원), 관리형(월 약 690만 원). 연 예산 6,000만 원. "
        "비용 절에 3년 TCO 표를 넣어 줘. 월 비용 × 36개월로."
    )
    body = "| 36개월 TCO | 169,200만 원 | 248,400만 원 |\n현행은 연 5,640만 원입니다."
    fixed = fix_ledger_magnitudes(body, request)
    assert "1억 6,920만 원" in fixed and "2억 4,840만 원" in fixed
    assert "연 5,640만 원" in fixed
    # An amount that is not a ledger result is left alone.
    assert fix_ledger_magnitudes("예비비 300만 원.", request) == "예비비 300만 원."
