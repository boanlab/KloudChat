"""Side calls (fact-check, title, memory extractor) report their tokens and reach the ledger."""

from __future__ import annotations

import pytest

from app.models.chat import ChatSession
from app.models.user import CreditLedger, User
from app.models.workspace import Artifact, ArtifactKind, Memory, MemoryType
from app.routers import sessions as sessions_router
from app.routers import workspace as workspace_router
from app.schemas.workspace import SlideFactCheck
from app.services import auto_memory, chat, factcheck
from app.services.credits import charge_for_tokens


def _reply(text: str, prompt_tokens: int, completion_tokens: int) -> dict:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


class _Response:
    def __init__(self, payload: dict):
        self.status_code = 200
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


_CLAIMS = '["2024년 등록대수는 60만 대를 넘었다", "국내 점유율은 12%다"]'
_VERDICT = '{"verdict": "supported", "note": "자료로 확인된다", "source": 1}'


class _CheckClient:
    """Both halves of a check; replies chosen by prompt text because judgements run concurrently."""

    def __init__(self, posts: list[dict], *, hits: bool = True, **_kwargs):
        self.posts = posts
        self.hits = hits

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _url: str, *, params: dict):
        results = (
            [{"title": "등록 통계", "url": "https://example.test/1", "content": "본문"}]
            if self.hits
            else []
        )
        return _Response({"results": results})

    async def post(self, _path: str, *, json: dict):
        self.posts.append(json)
        prompt = json["messages"][0]["content"]
        if "판정하라" in prompt:
            return _Response(_reply(_VERDICT, 40, 10))
        return _Response(_reply(_CLAIMS, 300, 30))


class _EmptyExtraction(_CheckClient):
    """The checker's other ordinary answer: this slide has no claims on it."""

    async def post(self, _path: str, *, json: dict):
        self.posts.append(json)
        return _Response(_reply("[]", 300, 30))


def _patch_factcheck(
    monkeypatch, posts: list[dict], *, hits: bool = True, client=_CheckClient
) -> None:
    async def litellm_config():
        return "http://litellm.test", "master"

    class _Backends:
        search = "http://searxng.test"

    async def tools_config():
        return _Backends()

    monkeypatch.setattr(factcheck.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(factcheck.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(
        factcheck.httpx,
        "AsyncClient",
        lambda **kw: client(posts, hits=hits, **kw),
    )


_SLIDE = {
    "id": "s_1",
    "title": "전기차 현황",
    "bullets": ["2024년 등록대수는 60만 대를 넘었다", "국내 점유율은 12%다"],
}


@pytest.mark.asyncio
async def test_a_checked_slide_reports_every_call_it_made(monkeypatch) -> None:
    posts: list[dict] = []
    _patch_factcheck(monkeypatch, posts)

    result, usage = await factcheck.check_slide(
        slide=_SLIDE, model="vendor/cheap", api_key="key"
    )

    assert [claim["verdict"] for claim in result["claims"]] == ["supported", "supported"]
    # One extraction plus one judgement per claim.
    assert len(posts) == 3
    assert usage == {"inputTokens": 300 + 40 * 2, "outputTokens": 30 + 10 * 2}


@pytest.mark.asyncio
async def test_a_slide_with_nothing_to_check_still_reports_the_call_that_said_so(
    monkeypatch,
) -> None:
    """A cleared slide still reports the extraction call's tokens."""
    posts: list[dict] = []
    _patch_factcheck(monkeypatch, posts, client=_EmptyExtraction)

    result, usage = await factcheck.check_slide(
        slide=_SLIDE, model="vendor/cheap", api_key="key"
    )

    assert result == {"status": "done", "claims": []}
    assert usage == {"inputTokens": 300, "outputTokens": 30}


@pytest.mark.asyncio
async def test_a_slide_nothing_could_be_found_for_reports_the_extraction(
    monkeypatch,
) -> None:
    """An empty search reports the extraction's tokens."""
    posts: list[dict] = []
    _patch_factcheck(monkeypatch, posts, hits=False)

    result, usage = await factcheck.check_slide(
        slide=_SLIDE, model="vendor/cheap", api_key="key"
    )

    assert [claim["verdict"] for claim in result["claims"]] == ["uncertain", "uncertain"]
    assert usage == {"inputTokens": 300, "outputTokens": 30}
    # A priced model is never billed nothing for work it did.
    priced = {"creditCost": 2, "inputCreditCost": 1}
    assert charge_for_tokens(priced, usage["inputTokens"], usage["outputTokens"]) == 1


class _ArtifactDb:
    """Enough of a session for one annotation: a fetch, some adds, a commit."""

    def __init__(self):
        self.added: list[object] = []
        self.commits = 0

    def is_modified(self, _value) -> bool:
        return False

    def add(self, value) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _value) -> None:
        return None


@pytest.mark.asyncio
async def test_a_fact_checked_slide_reaches_the_ledger(monkeypatch) -> None:
    """The fact-check endpoint settles at the checker model's price."""
    user = User(
        email="person@example.test",
        password_hash="hash",
        name="Person",
        monthly_credits=1_000,
    )
    artifact = Artifact(
        user_id=user.id,
        session_id="session-1",
        kind=ArtifactKind.deck,
        title="전기차 현황",
        data={"slides": [dict(_SLIDE)]},
    )

    async def own(*_args, **_kwargs):
        return artifact

    async def available():
        return True

    async def catalogue(*_args, **_kwargs):
        return {
            "models": [
                {
                    "id": "vendor/dear",
                    "kinds": ["chat"],
                    "creditCost": 40,
                    "inputCreditCost": 20,
                },
                {
                    "id": "vendor/cheap",
                    "kinds": ["chat"],
                    "creditCost": 2,
                    "inputCreditCost": 1,
                },
            ]
        }

    seen: dict = {}

    async def check_slide(*, slide, model, api_key):
        seen["model"] = model
        return {"status": "done", "claims": []}, {
            "inputTokens": 4_000,
            "outputTokens": 500,
        }

    async def ensure_key(*_args, **_kwargs):
        return None

    async def credentials(*_args, **_kwargs):
        return "http://litellm.test", "key"

    monkeypatch.setattr(workspace_router, "_own", own)
    monkeypatch.setattr(workspace_router.factcheck, "available", available)
    monkeypatch.setattr(workspace_router.factcheck, "check_slide", check_slide)
    monkeypatch.setattr(workspace_router.model_service, "list_models", catalogue)
    monkeypatch.setattr(workspace_router, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(workspace_router.litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(workspace_router.litellm_service, "credentials_for", credentials)

    db = _ArtifactDb()
    await workspace_router.factcheck_slide(
        artifact.id, SlideFactCheck(slide_id=_SLIDE["id"]), user, db
    )

    assert seen["model"] == "vendor/cheap"
    entry = next(row for row in db.added if isinstance(row, CreditLedger))
    assert entry.reason == "deck.factcheck"
    assert entry.session_id == "session-1"
    # 4_000 input and 500 output at the checker's price, not the deck's.
    assert entry.delta == -5
    assert user.credits_used == 5


class _TitleClient:
    def __init__(self, payload: dict, **_kwargs):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, _path: str, *, json: dict):
        return _Response(self.payload)


@pytest.mark.asyncio
async def test_a_generated_title_comes_back_with_what_it_cost(monkeypatch) -> None:
    async def litellm_config():
        return "http://litellm.test", "master"

    monkeypatch.setattr(chat.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(
        chat.httpx,
        "AsyncClient",
        lambda **kw: _TitleClient(_reply("전기차 등록 현황", 220, 8), **kw),
    )

    title, usage = await chat.generate_title(
        "local/small", "질문", "답변", "key"
    )

    assert title == "전기차 등록 현황"
    assert usage == {"inputTokens": 220, "outputTokens": 8}


@pytest.mark.asyncio
async def test_a_title_the_model_refused_to_write_still_cost_tokens(monkeypatch) -> None:
    async def litellm_config():
        return "http://litellm.test", "master"

    monkeypatch.setattr(chat.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(
        chat.httpx,
        "AsyncClient",
        lambda **kw: _TitleClient(_reply("   ", 220, 4), **kw),
    )

    title, usage = await chat.generate_title("local/small", "질문", "답변", "key")

    assert title is None
    assert usage == {"inputTokens": 220, "outputTokens": 4}


class _MemoryDb:
    """Enough of a session for the extractor: one query and a few adds."""

    def __init__(self, rows: list[Memory]):
        self.rows = rows
        self.added: list[object] = []

    async def exec(self, _statement):
        rows = self.rows

        class _Result:
            def all(self):
                return rows

        return _Result()

    def add(self, value) -> None:
        self.added.append(value)


@pytest.mark.asyncio
async def test_a_turn_worth_remembering_nothing_from_still_cost_a_call(
    monkeypatch,
) -> None:
    """An empty extraction still reports the call's tokens."""

    async def litellm_config():
        return "http://litellm.test", "master"

    monkeypatch.setattr(auto_memory.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(
        auto_memory.httpx,
        "AsyncClient",
        lambda **kw: _TitleClient(_reply("[]", 640, 3), **kw),
    )

    user = User(email="person@example.test", password_hash="hash", name="Person")
    written, usage = await auto_memory.extract(
        _MemoryDb([]),
        user,
        user_message="저는 단국대 보안랩 소속입니다",
        assistant_message="알겠습니다",
        api_key="key",
        model="local/small",
    )

    assert written == 0
    assert usage == {"inputTokens": 640, "outputTokens": 3}


class _TurnDb:
    """The connections a turn opens for itself, once the streaming is over."""

    def __init__(self, session: ChatSession, user: User):
        self.session = session
        self.user = user
        self.added: list[object] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, model, key):
        if model is ChatSession and key == self.session.id:
            return self.session
        if model is User and key == self.user.id:
            return self.user
        return None

    def add(self, value) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_a_generated_title_gets_its_own_ledger_line(monkeypatch) -> None:
    """The title is a separate ledger line at the title model's price."""
    session = ChatSession(id="session-1", user_id="user-1")
    user = User(
        id="user-1",
        email="person@example.test",
        password_hash="hash",
        name="Person",
    )
    db = _TurnDb(session, user)
    turn_model = {"id": "vendor/dear", "creditCost": 40, "inputCreditCost": 20}
    cheap = {"id": "local/small", "kinds": ["chat"], "creditCost": 2, "inputCreditCost": 1}
    seen: dict = {}

    async def run_turn(*_args, **_kwargs):
        yield {"type": "delta", "text": "답변"}
        yield {"type": "usage", "inputTokens": 1_000, "outputTokens": 100}

    async def enrichment_model():
        return cheap["id"]

    async def catalogue():
        return {"models": [cheap]}

    async def title(model_id, *_args, **_kwargs):
        seen["title"] = model_id
        return "전기차 등록 현황", {"inputTokens": 4_000, "outputTokens": 500}

    async def enrich(**_kwargs):
        # `(artifact_id, memory_step)`; the step is not looked at here.
        return None, None

    monkeypatch.setattr(sessions_router, "SessionLocal", lambda: db)
    monkeypatch.setattr(sessions_router.agent_service, "run_turn", run_turn)
    monkeypatch.setattr(
        sessions_router.model_service, "resolve_enrichment_model", enrichment_model
    )
    monkeypatch.setattr(sessions_router.model_service, "list_models", catalogue)
    monkeypatch.setattr(sessions_router.chat_service, "generate_title", title)
    monkeypatch.setattr(sessions_router, "_enrich", enrich)

    async for _ in sessions_router._run_turn(
        user_id=user.id,
        api_key="key",
        auto_memory=False,
        session_id=session.id,
        model=turn_model,
        messages=[],
        tools=[],
        first_user_message="질문",
        is_first_turn=True,
    ):
        pass

    assert seen["title"] == cheap["id"]
    assert session.title == "전기차 등록 현황"
    reasons = [row.reason for row in db.added if isinstance(row, CreditLedger)]
    assert reasons == ["chat.completion", "chat.title"]
    entries = {row.reason: row.delta for row in db.added if isinstance(row, CreditLedger)}
    # The answer at the answer's price, the title at the title's.
    assert entries["chat.completion"] == -24
    assert entries["chat.title"] == -5


@pytest.mark.asyncio
async def test_a_memory_extraction_is_billed_at_the_model_that_read_the_turn(
    monkeypatch,
) -> None:
    """The memory extraction is a separate ledger line at the enrichment model's price."""
    session = ChatSession(id="session-1", user_id="user-1")
    user = User(
        id="user-1",
        email="person@example.test",
        password_hash="hash",
        name="Person",
    )
    db = _TurnDb(session, user)
    turn_model = {"id": "vendor/dear", "creditCost": 40, "inputCreditCost": 20}
    cheap = {"id": "local/small", "kinds": ["chat"], "creditCost": 2, "inputCreditCost": 1}
    seen: dict = {}

    async def enrichment_model():
        return cheap["id"]

    async def catalogue():
        return {"models": [cheap]}

    async def extract(_db, _user, **kwargs):
        seen.update(kwargs)
        return 0, {"inputTokens": 4_000, "outputTokens": 500}

    async def nothing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(sessions_router, "SessionLocal", lambda: db)
    monkeypatch.setattr(sessions_router.artifact_extract, "store_requested", nothing)
    monkeypatch.setattr(sessions_router.artifact_extract, "extract", nothing)
    monkeypatch.setattr(
        sessions_router.model_service, "resolve_enrichment_model", enrichment_model
    )
    monkeypatch.setattr(sessions_router.model_service, "list_models", catalogue)
    monkeypatch.setattr(sessions_router.auto_memory_service, "extract", extract)

    await sessions_router._enrich(
        user_id=user.id,
        session_id=session.id,
        content="답변",
        first_user_message="질문",
        api_key="key",
        model=turn_model,
        auto_memory=True,
        requested_artifacts=[],
    )

    assert seen["model"] == cheap["id"]
    entry = next(row for row in db.added if isinstance(row, CreditLedger))
    assert entry.reason == "chat.memory"
    assert entry.session_id == session.id
    # The extractor's own price, not the turn's.
    assert entry.delta == -5


@pytest.mark.asyncio
async def test_an_extraction_that_never_reached_the_model_costs_nothing(
    monkeypatch,
) -> None:
    """A full memory store means no call and no charge."""

    async def litellm_config():  # pragma: no cover - the point is it is unused
        raise AssertionError("a full memory store must not reach the gateway")

    monkeypatch.setattr(auto_memory.settings_store, "litellm_config", litellm_config)
    full = [
        Memory(user_id="u", name=f"m{n}", body=f"사실 {n}", type=MemoryType.user)
        for n in range(auto_memory._MAX_TOTAL)
    ]

    user = User(email="person@example.test", password_hash="hash", name="Person")
    written, usage = await auto_memory.extract(
        _MemoryDb(full),
        user,
        user_message="질문",
        assistant_message="답변",
        api_key="key",
        model="local/small",
    )

    assert (written, usage) == (0, {"inputTokens": 0, "outputTokens": 0})
