"""Three things every answer is built on that the conversation never mentions.

Memories, attached files and project knowledge all reach the model without
passing through the transcript. A person who asks "why did it say that" could
not find the answer in the turn — and worse, could not tell that the document
they had just watched a chip appear for went out cut in half, or not at all.

Each of those now leaves one quiet line in the timeline the applied skills
already use. What the tests here hold to is the shape of that line:

1. The memories are named and never quoted. The name is what lets somebody go
   and find the fact; the body is the private half, and the timeline is a
   surface people screen-share.
2. Every attached file gets its own verdict — included, cut, or dropped for
   size — because "3개 중 1개" does not say which document is missing.
3. Project knowledge is reported the same way, out of the same code.
4. A memory written out of a finished turn says so where it happened, and the
   line survives a reload.
"""

from __future__ import annotations

from app.core.config import settings
from app.models.chat import ChatSession, Message, Role, SessionKind
from app.models.user import User, UserStatus, utcnow
from app.models.workspace import Memory, Project, StoredFile
from app.routers import sessions as sessions_router
from app.routers.sessions import _context_steps, _prelude_steps, _step_event
from app.services.workspace_context import assemble

# ── memories ───────────────────────────────────────────────────────────


async def test_the_turn_names_the_memories_it_answered_from_and_quotes_none():
    """The line is the index; the block is the content.

    Somebody reading "메모리 2건 참고 — 말투 · 소속" can go and look at those two
    rows. Printing the bodies instead would put a private fact on screen every
    time a turn ran, which is the failure the memory drawer exists to avoid.
    """
    db = _Db(memories=[_memory("소속", "단국대학교"), _memory("말투", "존댓말을 쓴다")])
    context = await assemble(db, _user(), _session())

    assert set(context.loaded_memories) == {"말투", "소속"}
    assert context.total_memories == 2
    step = _one(_context_steps(context), "context-memories")
    assert step["label"] == "메모리 2건 참고"
    assert "단국대학교" not in repr(step)
    # The bodies did go upstream — the block is the model's copy, not this one.
    assert "단국대학교" in next(b.text for b in context.blocks if b.source == "memory")


async def test_a_turn_that_could_only_carry_some_of_them_says_which_some():
    """Forty of sixty is a different answer from sixty of sixty."""
    db = _Db(memories=[_memory(f"사실 {i:02d}", "본문") for i in range(45)])
    context = await assemble(db, _user(), _session())

    assert len(context.loaded_memories) == 40
    assert context.total_memories == 45
    step = _one(_context_steps(context), "context-memories")
    assert step["label"] == "메모리 40건 참고"
    assert step["detail"].endswith("외 34건 · 저장된 45건 중 최근 40건")


async def test_a_turn_with_nothing_remembered_adds_no_line():
    """Quiet: the timeline is for what happened, not for what did not."""
    context = await assemble(_Db(), _user(), _session())
    assert _context_steps(context) == []


# ── attachments ────────────────────────────────────────────────────────


async def test_each_attached_file_reports_what_survived_the_budget(monkeypatch):
    """The budget is spent in order, so the last file pays for the first.

    Before this the only notice was a sentence inside the prompt, addressed to
    the model. The person waited, paid, and read an answer built on half of
    their document with the chip still saying it was attached.
    """
    monkeypatch.setattr(settings, "file_context_chars", 30)
    files = [
        _file("첫째.txt", "가" * 20),
        _file("둘째.txt", "나" * 40),
        _file("셋째.txt", "다" * 10),
    ]
    context = await _assembled_with(files)

    assert [(f.name, f.state) for f in context.attachments] == [
        ("첫째.txt", "included"),
        ("둘째.txt", "truncated"),
        ("셋째.txt", "omitted"),
    ]
    assert context.attachments[1].kept_chars == 10
    assert context.attachments[1].total_chars == 40

    step = _one(_context_steps(context), "context-attachments")
    # Both fates named: one document arrived at half length, another not at all.
    assert step["label"] == "첨부 3개 중 1개 잘림, 1개 빠짐"
    assert step["detail"] == "둘째.txt 10자만 반영 · 셋째.txt 분량을 넘겨 제외"
    assert step["files"][2] == {
        "name": "셋째.txt",
        "state": "omitted",
        "keptChars": 0,
        "totalChars": 10,
    }


async def test_a_file_that_fit_whole_still_gets_its_line():
    context = await _assembled_with([_file("메모.txt", "짧다")])
    step = _one(_context_steps(context), "context-attachments")
    assert step["label"] == "첨부 1개 반영"
    assert step["detail"] == "메모.txt"


async def test_a_file_nothing_could_be_read_out_of_is_reported_where_it_was_attached():
    """The order is the person's, not the assembler's.

    The block is built from the readable files and then the unreadable ones,
    but the list on screen is the order they were attached in, and that is the
    list this line will be read against.
    """
    files = [_file("보고서.pdf", "", error="스캔본"), _file("메모.txt", "읽힌다")]
    context = await _assembled_with(files)

    assert [(f.name, f.state) for f in context.attachments] == [
        ("보고서.pdf", "unreadable"),
        ("메모.txt", "included"),
    ]
    step = _one(_context_steps(context), "context-attachments")
    assert step["label"] == "첨부 2개 중 1개 빠짐"
    assert step["detail"] == "보고서.pdf 읽지 못함"


# ── project knowledge ──────────────────────────────────────────────────


async def test_project_knowledge_dropped_for_size_gets_the_same_line(monkeypatch):
    """Same failure, same code, same line — under its own name."""
    monkeypatch.setattr(settings, "file_context_chars", 5)
    project = Project(id="project-1", user_id="user-1", name="연구")
    db = _Db(project=project, files=[_file("규정.md", "가" * 50), _file("연혁.md", "나" * 50)])
    context = await assemble(db, _user(), _session(project_id=project.id))

    assert [(f.name, f.state) for f in context.knowledge] == [
        ("규정.md", "truncated"),
        ("연혁.md", "omitted"),
    ]
    step = _one(_context_steps(context), "context-knowledge")
    assert step["label"] == "프로젝트 지식 2개 중 1개 잘림, 1개 빠짐"
    assert step["detail"] == "규정.md 5자만 반영 · 연혁.md 분량을 넘겨 제외"


async def test_the_prompt_the_model_reads_is_unchanged_by_the_reporting(monkeypatch):
    """The account is new; what goes upstream is not.

    The notices inside the block were the whole mechanism before this, and they
    are still the model's copy of the same facts — it has to know the document
    it is quoting stops early.
    """
    monkeypatch.setattr(settings, "file_context_chars", 10)
    context = await _assembled_with([_file("긴글.txt", "가" * 30), _file("남은글.txt", "나" * 5)])

    block = next(b.text for b in context.blocks if b.source == "attachment")
    # Not "the last 20 characters are missing" any more: an excerpt chosen
    # around what somebody asked for can leave out the beginning too, and a
    # notice that named only the tail would be wrong exactly when it mattered.
    assert "…(전체 30자 중 일부입니다)" in block
    assert "## 포함되지 않은 파일\n남은글.txt" in block


# ── how the line travels ───────────────────────────────────────────────


def test_a_step_is_stored_by_category_and_sent_by_event_name():
    """One shape, two envelopes.

    A stored step spends `type` on the display category the timeline reads; a
    stream event has already spent it on the event name, so the category rides
    alongside. Backwards, every context line renders as a tool call.
    """
    stored = {"id": "context-memories", "type": "thinking", "label": "메모리 1건 참고"}
    wire = _step_event(stored)

    assert wire["type"] == "step"
    assert wire["category"] == "thinking"
    assert stored["type"] == "thinking"


def test_the_turn_opens_with_what_it_was_given_skills_first():
    """The prelude is the given, in the order it was decided."""
    skills = {
        "type": "skills_applied",
        "skills": [{"id": "s1", "name": "검증", "catalogKey": None, "estimatedTokens": 12}],
        "estimatedTokens": 12,
    }
    context = [{"id": "context-memories", "type": "thinking", "label": "메모리 1건 참고"}]

    assert [step["id"] for step in _prelude_steps(skills, context)] == [
        "skills-applied",
        "context-memories",
    ]
    assert _prelude_steps(None, None) == []


# ── memories written out of the turn ───────────────────────────────────


async def test_a_written_memory_says_so_on_the_row_it_came_from(monkeypatch):
    """The answer is already durable when auto-memory runs, so the step is an
    edit to the message it belongs to as well as an event. Streamed only, it
    would vanish on the next reload.
    """
    answer = Message(id="message-1", session_id="session-1", role=Role.assistant, content="네")
    db = _EnrichDb(answer)
    _patch_enrichment(monkeypatch, db, written=2)

    artifact_id, step = await sessions_router._enrich(
        user_id="user-1",
        session_id="session-1",
        content="네",
        first_user_message="기억해 줘",
        api_key="key",
        model={"id": "model-a"},
        auto_memory=True,
        message_id=answer.id,
    )

    assert artifact_id is None
    assert step["label"] == "메모리 2건 저장"
    assert answer.steps == [step]
    assert db.commits == 1


async def test_a_turn_that_remembered_nothing_leaves_the_row_alone(monkeypatch):
    answer = Message(id="message-1", session_id="session-1", role=Role.assistant, content="네")
    db = _EnrichDb(answer)
    _patch_enrichment(monkeypatch, db, written=0)

    _artifact_id, step = await sessions_router._enrich(
        user_id="user-1",
        session_id="session-1",
        content="네",
        first_user_message="안녕",
        api_key="key",
        model={"id": "model-a"},
        auto_memory=True,
        message_id=answer.id,
    )

    assert step is None
    assert answer.steps is None


# ── fakes ──────────────────────────────────────────────────────────────


def _one(steps: list[dict], step_id: str) -> dict:
    matched = [step for step in steps if step["id"] == step_id]
    assert len(matched) == 1, [step["id"] for step in steps]
    return matched[0]


async def _assembled_with(files: list[StoredFile]):
    return await assemble(
        _Db(files=files), _user(), _session(), attachment_ids=[f.id for f in files]
    )


class _Result:
    def __init__(self, rows: list):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Db:
    """Enough of the session to assemble one context."""

    def __init__(
        self,
        *,
        memories: list[Memory] | None = None,
        files: list[StoredFile] | None = None,
        project: Project | None = None,
    ):
        self.memories = memories or []
        self.files = files or []
        self.project = project

    async def get(self, model, row_id):
        if model is Project and self.project is not None and row_id == self.project.id:
            return self.project
        return None

    async def exec(self, query):
        table = query.get_final_froms()[0].name
        if table == "memories":
            return _Result(self.memories)
        if table == "files":
            return _Result(self.files)
        if table == "skills":
            return _Result([])
        raise AssertionError(f"unexpected query: {query}")


class _EnrichDb:
    """The enrichment transaction: one message row and a commit count."""

    def __init__(self, message: Message):
        self.message = message
        self.session = ChatSession(id="session-1", user_id="user-1")
        self.user = _user()
        self.commits = 0

    async def get(self, model, row_id):
        if model is Message:
            return self.message if row_id == self.message.id else None
        if model is ChatSession:
            return self.session
        if model is User:
            return self.user
        return None

    def add(self, _row):
        return None

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _patch_enrichment(monkeypatch, db: _EnrichDb, *, written: int) -> None:
    monkeypatch.setattr(sessions_router, "SessionLocal", lambda: db)

    async def store_requested(*_args, **_kwargs):
        return None

    async def extract(*_args, **_kwargs):
        return None

    async def remember(*_args, **_kwargs):
        # `(written, usage)` since the extractor started reporting what it
        # spent — its own line on the ledger is a separate property, tested in
        # tests/test_side_calls_are_billed.py.
        return written, {"inputTokens": 0, "outputTokens": 0}

    async def enrichment_model():
        return "model-a"

    monkeypatch.setattr(sessions_router.artifact_extract, "store_requested", store_requested)
    monkeypatch.setattr(sessions_router.artifact_extract, "extract", extract)
    monkeypatch.setattr(sessions_router.auto_memory_service, "extract", remember)
    monkeypatch.setattr(
        sessions_router.model_service, "resolve_enrichment_model", enrichment_model
    )


def _user() -> User:
    return User(
        id="user-1",
        email="user@example.com",
        password_hash="hash",
        name="User",
        status=UserStatus.active,
        monthly_credits=100,
    )


def _session(project_id: str | None = None) -> ChatSession:
    return ChatSession(
        id="session-1", user_id="user-1", kind=SessionKind.chat, project_id=project_id
    )


def _memory(name: str, body: str) -> Memory:
    return Memory(user_id="user-1", name=name, body=body, updated_at=utcnow())


def _file(name: str, text: str, *, error: str | None = None) -> StoredFile:
    return StoredFile(id=f"file-{name}", user_id="user-1", name=name, text=text, error=error)
