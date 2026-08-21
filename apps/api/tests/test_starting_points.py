"""시작점: what a starting point is allowed to do to a turn.

Picking one used to type the template's whole framing into the composer, so
the transcript attributed the product's own sentences to the person who sent
them. A starting point is now carried by the turn the way an activated skill
is, and three properties are what that buys — each with a test here that fails
without the code it guards:

1. The catalogue is the server's, so an id can be checked. One that names
   nothing, or somebody else's private row, is refused before any write.
2. The person's words stay the person's words. The template reaches the model
   as its own block, `content` is untouched, and the block lands where the
   contract puts it — after the skills, before the memories.
3. The record names the template rather than quoting it. A transcript read a
   year from now says which starting point a turn began from, and never prints
   the machinery.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.core.deps import current_user
from app.models.chat import ChatSession, Message, SessionKind
from app.models.governance import Governance
from app.models.user import CreditLedger, User, UserStatus
from app.models.workspace import Memory, Skill, SkillSource, Template
from app.routers import sessions as sessions_router
from app.routers import workspace as workspace_router
from app.schemas.chat import CompareRequest, SendMessage
from app.schemas.workspace import PromptTemplateOut
from app.services import prompt_templates
from app.services.workspace_context import WorkspaceContextError, assemble

# ── the shipped catalogue ──────────────────────────────────────────────


def test_every_starting_point_is_offered_once_and_hands_the_turn_back():
    """The two promises the module makes about its own entries.

    A duplicate id would silently shadow one card with another — the dict is
    built by id, so the second entry wins and the first stops existing. And a
    prompt that ends in a full stop is a template that says everything, which
    leaves the person nothing to add and puts the whole request in the
    product's voice again.
    """
    ids = [t.id for t in prompt_templates.all_templates()]
    assert len(ids) == len(set(ids))
    for template in prompt_templates.all_templates():
        assert template.id.startswith("t_"), template.id
        assert template.title and template.description and template.fills
        assert template.prompt.endswith(": "), template.id


def test_a_built_in_card_travels_in_the_shape_the_gallery_already_renders():
    """One card renders both lists, so the shared keys have to mean one thing."""
    card = PromptTemplateOut.of(prompt_templates.get("t_incident"))
    assert card.kind == card.surface == SessionKind.report.value
    assert card.builtin is True
    # The English half is declared and unwritten; the client falls back.
    assert card.title_en == "" and card.fills_en == []
    wire = card.model_dump(by_alias=True)
    assert wire["fills"] == list(prompt_templates.get("t_incident").fills)
    assert wire["titleEn"] == ""


def test_the_catalogue_route_serves_the_whole_list_and_one_surface():
    user = _user()
    app = FastAPI()
    app.include_router(workspace_router.router)

    async def override_user():
        return user

    app.dependency_overrides[current_user] = override_user
    with TestClient(app) as client:
        everything = client.get("/prompt-templates")
        assert everything.status_code == 200
        assert len(everything.json()) == len(prompt_templates.all_templates())

        slides = client.get("/prompt-templates", params={"surface": "slides"})
        assert {row["id"] for row in slides.json()} == {
            t.id for t in prompt_templates.all_templates() if t.kind is SessionKind.slides
        }


# ── who may attach one ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_id_that_names_nothing_is_refused():
    with pytest.raises(WorkspaceContextError, match="starting_template_not_found"):
        await assemble(
            _Db(_session()), _user(), _session(), starting_template_id="t_invented"
        )


@pytest.mark.asyncio
async def test_somebody_elses_private_starting_point_is_refused_and_a_shared_one_is_not():
    private = Template(
        id="template-1", owner_id="other-user", kind="report", title="남의 서식", prompt="비공개. "
    )
    db = _Db(_session(), templates=[private])
    with pytest.raises(WorkspaceContextError, match="starting_template_not_found"):
        await assemble(db, _user(), _session(), starting_template_id=private.id)

    private.shared = True
    context = await assemble(db, _user(), _session(), starting_template_id=private.id)
    assert context.started_from == {"templateId": "template-1", "title": "남의 서식"}


# ── what it does to the turn ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_starting_point_is_one_block_and_sits_after_the_skills():
    """A skill is a procedure somebody keeps; a starting point is this turn.

    So the starting point is the later, more specific instruction — and it is
    still an instruction, which is what keeps it above the memories.
    """
    skill = _skill()
    db = _Db(_session(), skills=[skill], memories=[_memory()])
    context = await assemble(
        db,
        _user(),
        _session(),
        activated_skill_ids=[skill.id],
        starting_template_id="t_essay",
        available_tool_names=set(),
    )
    sources = [block.source for block in context.blocks]
    assert sources.count("template:t_essay") == 1
    assert sources.index(f"skill:{skill.id}") < sources.index("template:t_essay")
    assert sources.index("template:t_essay") < sources.index("memory")

    block = next(b for b in context.blocks if b.source == "template:t_essay")
    # Trusted, because the person chose it: the same standing as a skill body.
    assert block.trusted
    assert block.text.startswith("# 시작점 — 업무·기술 보고서")
    assert prompt_templates.get("t_essay").prompt.strip() in block.text


@pytest.mark.asyncio
async def test_a_turn_carries_the_template_and_records_only_its_name(monkeypatch):
    """The whole point, end to end: the model is told, the transcript is not.

    What the person typed is what the message row keeps. The template's own
    sentence goes upstream as its own block and stops there — a transcript that
    quoted it would be back to attributing the product's words to a person.
    """
    user = _user()
    db = _Db(_session(SessionKind.report))
    captured = _patch_document_turn(monkeypatch)

    response = await sessions_router.send_message(
        db.session.id,
        SendMessage(content="어제 새벽 장애 정리해 줘", starting_template_id="t_incident"),
        _request(f"/sessions/{db.session.id}/messages"),
        user,
        db,
    )
    async for _chunk in response.body_iterator:
        pass

    template = prompt_templates.get("t_incident")
    started = [line for line in captured["trusted_context"] if "# 시작점" in line]
    assert len(started) == 1
    assert template.prompt.strip() in started[0]

    row = next(r for r in db.added if isinstance(r, Message) and r.role.value == "user")
    assert row.content == "어제 새벽 장애 정리해 줘"
    assert row.started_from == {"templateId": "t_incident", "title": "장애 보고"}
    # Named, not quoted — nowhere on the row, not only outside `content`.
    assert "재발 방지책" not in repr(row.model_dump())


@pytest.mark.asyncio
async def test_a_turn_with_no_starting_point_records_nothing(monkeypatch):
    db = _Db(_session(SessionKind.report))
    _patch_document_turn(monkeypatch)

    response = await sessions_router.send_message(
        db.session.id,
        SendMessage(content="어제 새벽 장애 정리해 줘"),
        _request(f"/sessions/{db.session.id}/messages"),
        _user(),
        db,
    )
    async for _chunk in response.body_iterator:
        pass

    row = next(r for r in db.added if isinstance(r, Message) and r.role.value == "user")
    assert row.started_from is None


@pytest.mark.parametrize("route", ["send", "compare"])
@pytest.mark.asyncio
async def test_both_turn_routes_refuse_an_unknown_id_before_any_write(monkeypatch, route: str):
    """Refused rather than dropped: a card was picked and a chip was shown.

    `/compare` matters as much as `/messages` here — it is the second door into
    the same turn, and a starting point that fell out of it would produce
    columns answering a request nobody made, at two or three times the price.
    """
    db = _Db(_session())
    upstream = {"calls": 0}
    _patch_route_services(monkeypatch, upstream)

    with pytest.raises(HTTPException) as raised:
        if route == "send":
            await sessions_router.send_message(
                db.session.id,
                SendMessage(content="안녕", starting_template_id="t_invented"),
                _request(f"/sessions/{db.session.id}/messages"),
                _user(),
                db,
            )
        else:
            await sessions_router.compare_models(
                db.session.id,
                CompareRequest(
                    content="안녕",
                    models=["model-a", "model-b"],
                    starting_template_id="t_invented",
                ),
                _request(f"/sessions/{db.session.id}/compare"),
                _user(),
                db,
            )

    assert raised.value.status_code == 404
    assert raised.value.detail == "starting_template_not_found"
    assert not any(isinstance(row, (Message, CreditLedger)) for row in db.added)
    assert db.commits == 0
    assert upstream["calls"] == 0


# ── fakes ──────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list | None = None):
        self._rows = rows or []

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    """Enough of the session to assemble a context and serve one turn."""

    def __init__(
        self,
        session: ChatSession,
        *,
        skills: list[Skill] | None = None,
        memories: list[Memory] | None = None,
        templates: list[Template] | None = None,
    ):
        self.session = session
        self.skills = skills or []
        self.memories = memories or []
        self.templates = {row.id: row for row in templates or []}
        self.added: list[object] = []
        self.commits = 0

    async def get(self, model, row_id):
        if model is ChatSession and row_id == self.session.id:
            return self.session
        if model is Template:
            return self.templates.get(row_id)
        return None

    async def exec(self, query):
        table = query.get_final_froms()[0].name
        if table == "skills":
            return _Result(self.skills)
        if table == "memories":
            return _Result(self.memories)
        if table in {"messages", "files"}:
            return _Result()
        raise AssertionError(f"unexpected query: {query}")

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1

    def is_modified(self, _row):
        return False


def _user() -> User:
    return User(
        id="user-1",
        email="user@example.com",
        password_hash="hash",
        name="User",
        status=UserStatus.active,
        monthly_credits=100,
    )


def _session(kind: SessionKind = SessionKind.chat) -> ChatSession:
    return ChatSession(id="session-1", user_id="user-1", kind=kind)


def _skill() -> Skill:
    return Skill(
        id="skill-1",
        owner_id="user-1",
        name="검증",
        slug="검증",
        body="검증한다.",
        source=SkillSource.personal,
        kinds=["chat"],
        required_tools=[],
        estimated_tokens=12,
        enabled=True,
    )


def _memory() -> Memory:
    return Memory(id="memory-1", user_id="user-1", name="소속", body="단국대학교")


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "query_string": b"",
        }
    )


def _patch_route_services(monkeypatch, upstream: dict[str, int]) -> None:
    async def policy():
        return Governance()

    async def models():
        return {
            "models": [
                {
                    "id": model_id,
                    "kinds": ["chat", "report", "slides"],
                    "supportsTools": False,
                    "creditCost": 1,
                    "inputCreditCost": 1,
                }
                for model_id in ("model-a", "model-b")
            ]
        }

    async def ensure_key(_user):
        upstream["calls"] += 1

    async def credentials(_user):
        upstream["calls"] += 1
        return "http://unused", "unused"

    monkeypatch.setattr(sessions_router.governance, "current_for_egress", policy)
    monkeypatch.setattr(sessions_router.model_service, "list_models_for_egress", models)
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(sessions_router.litellm_service, "credentials_for", credentials)


def _patch_document_turn(monkeypatch) -> dict:
    """A report turn with the writer replaced, so the context is what is read."""
    captured: dict = {}
    _patch_route_services(monkeypatch, {"calls": 0})

    async def run_report(**kwargs):
        captured.update(kwargs)
        yield 'data: {"type":"done"}\n\n'

    monkeypatch.setattr(sessions_router, "_run_report", run_report)
    return captured
