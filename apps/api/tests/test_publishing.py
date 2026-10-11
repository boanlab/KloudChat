"""Published agents (공개 챗봇), phase 1: the owner publishes, a member asks, and the turn runs on
the owner's account under the bot's fixed version — strict-local, without the owner's memory,
settings or other files — while the owner sees statistics, not conversations.

Real HTTP and SQLite; the model is a fake agent loop that reads the bot's tools."""

from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import db as core_db
from app.core.db import get_session
from app.core.deps import current_user, require_admin
from app.models.chat import ChatSession, Message
from app.models.governance import Governance
from app.models.publishing import (
    Publication,
    PublicationCounter,
    PublicationKey,
    PublicationQuestion,
    PublicationSlot,
    PublicationVersion,
    PublicationVisitor,
)
from app.models.user import AuditEvent, CreditLedger, User, UserRole, UserStatus, utcnow
from app.models.workspace import Agent, Memory, StoredFile
from app.routers import bot_api, publications, search, sessions
from app.routers import workspace as ws
from app.services import agent as agent_service
from app.services import publishing, settings_store

# As the catalogue lists a strict-local route: privacy-only, out of the picker.
_STRICT = {"id": "strict-local/m", "label": "내부 모델", "dataBoundary": "self_hosted",
           "strictLocal": True, "privacyOnly": True, "kinds": ["chat"], "creditCost": 0,
           "inputCreditCost": 0, "supportsTools": True, "contextWindow": 64000}
_LOCAL = {"id": "local/m", "label": "로컬 모델", "dataBoundary": "hybrid",
          "strictLocal": False, "kinds": ["chat"], "creditCost": 0, "inputCreditCost": 0,
          "supportsTools": True, "contextWindow": 64000}
_CLOUD = {"id": "cloud/big", "label": "외부 모델", "dataBoundary": "external",
          "strictLocal": False, "kinds": ["chat"], "creditCost": 2, "inputCreditCost": 1,
          "supportsTools": True, "contextWindow": 64000}

_RULES = """학사 운영 규정
[시행 2025. 3. 1.]
제1조(목적) 이 규정은 학사 운영에 필요한 사항을 정한다.
제12조(휴학) ① 휴학은 학기 개시일 전까지 신청한다. ② 질병 휴학은 진단서를 낸다.
제13조(복학) 복학은 휴학 기간이 끝나는 학기 개시 30일 전까지 신청한다.
"""


@pytest.fixture
async def world(monkeypatch, tmp_path):
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copied = table.to_metadata(metadata)
        for column in copied.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    names = ["users", "agents", "sessions", "messages", "files", "artifacts",
             "artifact_versions", "credit_ledger", "audit_events", "skills", "pipeline_runs",
             "memories", "projects", "design_systems", "user_pipelines", "jobs",
             "user_templates", "publications", "publication_versions", "publication_visitors",
             "publication_counters", "publication_slots", "publication_questions",
             "publication_keys"]
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=[metadata.tables[n] for n in names])

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    monkeypatch.setattr(sessions, "SessionLocal", local)
    monkeypatch.setattr(core_db, "SessionLocal", local)
    monkeypatch.setattr(sessions, "_STOPPING", {})

    state = SimpleNamespace(
        catalogue=[_LOCAL, _STRICT, _CLOUD],
        policy=Governance(internal_data_strict_local=False, external_data_guard=False,
                          privacy_safe_model_ids=[], published_concurrency_max=16),
        calls=[], memory_calls=[], answer="휴학은 학기 개시일 전까지 신청합니다 [1].",
        search=True, user=None, side_models=[],
    )

    async def catalogue(*_args, **_kwargs):
        return {"models": list(state.catalogue), "litellmAvailable": True}

    async def policy(*_args, **_kwargs):
        return state.policy

    async def no_backends():
        return settings_store.ToolBackends()

    async def key(*_args, **_kwargs):
        return "fixture"

    async def credentials(*_args, **_kwargs):
        return "http://litellm.invalid", "sk-fixture"

    async def title(model, *_args, **kwargs):
        state.side_models.append(("title", model, kwargs.get("strict_local")))
        return "휴학 문의", {"inputTokens": 5, "outputTokens": 2}

    async def memory(**kwargs):
        state.memory_calls.append(kwargs)
        return None

    async def run_turn(model, messages, tools, ctx, **kwargs):
        state.calls.append({"model": model, "messages": messages,
                            "tools": sorted(t.name for t in tools), "ctx": ctx,
                            "strict": kwargs.get("strict_local"),
                            "preset": kwargs.get("preset_call"),
                            "not_covered": kwargs.get("preset_empty_answer")})
        knowledge = next((t for t in tools if t.name == "search_knowledge"), None)
        if knowledge is not None and state.search:
            found = await knowledge.run({"query": "휴학 신청"})
            ctx.outputs.append(found.content)
        yield {"type": "delta", "text": state.answer}
        yield {"type": "usage", "inputTokens": 120, "outputTokens": 30}

    monkeypatch.setattr(sessions.model_service, "list_models_for_egress", catalogue)
    monkeypatch.setattr(sessions.governance, "current_for_egress", policy)
    monkeypatch.setattr(settings_store, "tools_config", no_backends)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", key)
    monkeypatch.setattr(sessions.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions.chat_service, "generate_title", title)
    monkeypatch.setattr(sessions, "_enrich_memory", memory)
    monkeypatch.setattr(sessions.answer_plan, "wanted", lambda *_a, **_k: False)
    monkeypatch.setattr(agent_service, "run_turn", run_turn)

    owner = User(id="owner", email="owner@example.test", name="만든 사람", password_hash="x",
                 monthly_credits=1000, preferences={"aboutMe": "나는 회계팀 김철수다",
                                                    "autoMemory": True})
    member = User(id="member", email="member@example.test", name="구성원", password_hash="x",
                  monthly_credits=1000)
    other = User(id="other", email="other@example.test", name="다른 사람", password_hash="x",
                 monthly_credits=1000)
    admin = User(id="admin", email="admin@example.test", name="관리자", password_hash="x",
                 role=UserRole.admin, monthly_credits=1000)
    for user in (owner, member, other, admin):
        user.status = UserStatus.active
    agent = Agent(id="agent-1", owner_id=owner.id, name="학사 안내", slug="haksa",
                  model="local/m", kinds=["chat"], surface="chat",
                  system_prompt="규정에서 찾아 답한다.", tools=["search_knowledge", "calculate",
                                                              "web_search"],
                  contact="학사지원팀 02-760-1234, haksa-desk@example.ac.kr",
                  check_skills=["answer-hold", "contact-note", "pii-mask"],
                  starters=["휴학은 언제까지 하나요?"])
    rules = StoredFile(id="rules", user_id=owner.id, agent_id=agent.id, name="학사규정.pdf",
                       text=_RULES, size=len(_RULES), mime="application/pdf")
    secret = StoredFile(id="secret", user_id=owner.id, name="급여대장.xlsx",
                        text="김철수 급여 9,000,000원", size=10)
    remembered = Memory(user_id=owner.id, name="급여 비밀", body="대표 급여는 1억이다")
    async with local() as db:
        db.add_all([owner, member, other, admin, agent, rules, secret, remembered])
        await db.commit()

    app = FastAPI()
    for router in (sessions.router, publications.router, ws.router, search.router,
                   bot_api.router):
        app.include_router(router)

    async def db_session():
        async with local() as db:
            yield db

    app.dependency_overrides[get_session] = db_session
    app.dependency_overrides[current_user] = lambda: state.user
    app.dependency_overrides[require_admin] = lambda: state.user
    # The bot pages resolve the caller themselves: a member, or None without an account.
    app.dependency_overrides[publications.bot_caller] = lambda: state.user
    state.app = app
    state.users = {"owner": owner, "member": member, "other": other, "admin": admin}
    state.local = local

    def as_(name: str):
        state.user = state.users[name]

    state.as_ = as_
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        state.client = client
        try:
            yield state
        finally:
            await engine.dispose()


async def _publish(w, **settings) -> dict:
    w.as_("owner")
    response = await w.client.post("/publications", json={"agentId": "agent-1", **settings})
    assert response.status_code == 201, response.text
    return response.json()


async def _ask(w, slug: str, text: str, who: str = "member") -> tuple[str, str]:
    w.as_(who)
    page = (await w.client.get(f"/b/{slug}")).json()
    if not page["consented"]:
        assert (await w.client.post(f"/b/{slug}/consent",
                                    json={"version": page["consentVersion"]})).status_code == 204
    made = await w.client.post(f"/b/{slug}/conversations")
    assert made.status_code == 201, made.text
    sid = made.json()["id"]
    reply = await w.client.post(f"/b/{slug}/conversations/{sid}/messages", json={"content": text})
    assert reply.status_code == 200, reply.text
    return sid, reply.text


# ── publishing ─────────────────────────────────────────────────────────


async def test_with_no_internal_model_the_bot_is_not_published(world):
    world.catalogue = [_LOCAL, _CLOUD]
    world.as_("owner")
    checked = (await world.client.post("/publications/preflight",
                                       json={"agentId": "agent-1"})).json()
    assert checked["ok"] is False and "no_local_model" in checked["problems"]
    refused = await world.client.post("/publications", json={"agentId": "agent-1"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["problems"] == ["no_local_model"]


async def test_the_preflight_names_the_model_what_is_left_out_and_personal_details(world):
    async with world.local() as db:
        row = await db.get(StoredFile, "rules")
        row.text = _RULES + "\n문의: 담당 조교 010-1234-5678"
        db.add(row)
        await db.commit()
    world.as_("owner")
    checked = (await world.client.post("/publications/preflight",
                                       json={"agentId": "agent-1"})).json()
    assert checked["ok"] and checked["model"]["id"] == "strict-local/m"
    assert checked["tools"] == ["calculate", "search_knowledge"]
    assert {"memory", "web", "files", "connectors", "tools"} <= set(checked["disabled"])
    assert checked["knowledge"][0]["findings"] == [{"category": "phone", "count": 1}]


async def test_a_version_is_a_hidden_fixed_copy_and_rollback_brings_one_back(world):
    pub = await _publish(world, note="첫판")
    assert pub["status"] == "approved" and pub["visibility"] == "internal"
    # An address that copies as written: no Korean to percent-encode.
    assert pub["slug"].startswith("bot-") and pub["slug"].isascii()
    (v1,) = pub["versions"]
    async with world.local() as db:
        version = await db.get(PublicationVersion, v1["id"])
        snap = await db.get(Agent, version.agent_snapshot_id)
        assert snap.snapshot_of == "agent-1" and snap.model == "strict-local/m"
        assert snap.tools == ["calculate", "search_knowledge"] and snap.skill_ids == []
        copies = (await db.exec(select(StoredFile).where(StoredFile.agent_id == snap.id))).all()
        assert [c.text for c in copies] == [_RULES] and copies[0].storage_key == ""
        # The owner edits the original afterwards; the version keeps what was published.
        original = await db.get(Agent, "agent-1")
        original.system_prompt = "바뀐 지시"
        db.add(original)
        await db.commit()
        assert (await db.get(Agent, snap.id)).system_prompt == "규정에서 찾아 답한다."
    # The copy is listed nowhere and edited by no one, the owner included.
    world.as_("owner")
    listed = [a["id"] for a in (await world.client.get("/agents")).json()]
    assert "agent-1" in listed and version.agent_snapshot_id not in listed
    assert (await world.client.patch(f"/agents/{version.agent_snapshot_id}",
                                     json={"name": "x"})).status_code == 404
    files = [f["id"] for f in (await world.client.get("/files")).json()]
    assert "rules" in files and copies[0].id not in files
    # A second version from the edited agent, then back to the first.
    second = (await world.client.post(f"/publications/{pub['id']}/versions",
                                      json={"note": "고침"})).json()
    assert [v["number"] for v in second["versions"]] == [2, 1]
    assert second["versions"][0]["active"]
    back = (await world.client.post(
        f"/publications/{pub['id']}/versions/{v1['id']}/activate")).json()
    assert back["activeVersionId"] == v1["id"]
    # Nothing changed since the newest version: a second click makes no third one.
    same = await world.client.post(f"/publications/{pub['id']}/versions", json={"note": "또"})
    assert same.status_code == 409 and same.json()["detail"] == "no_change"


# ── the visitor's turn ─────────────────────────────────────────────────


async def test_a_visitor_must_read_the_notice_before_the_first_question(world):
    pub = await _publish(world)
    world.as_("member")
    page = (await world.client.get(f"/b/{pub['slug']}")).json()
    assert page["consented"] is False and page["conversationView"] == "off"
    assert page["branding"]["suggestedPrompts"] == ["휴학은 언제까지 하나요?"]
    refused = await world.client.post(f"/b/{pub['slug']}/conversations")
    assert refused.status_code == 409 and refused.json()["detail"] == "consent_required"


async def test_the_turn_runs_strict_local_on_the_owners_account_with_nothing_else_of_theirs(
        world):
    pub = await _publish(world)
    sid, stream = await _ask(world, pub["slug"], "휴학은 언제까지 신청해요?")
    (call,) = world.calls
    # The version's strict-local model even though the policy would not force it.
    assert call["model"] == "strict-local/m" and call["strict"] is True
    assert call["tools"] == ["calculate", "search_knowledge"]
    sent = str(call["messages"])
    assert "규정에서 찾아 답한다" in sent and "학사지원팀 02-760-1234" in sent
    # Not the owner's memory, 개인 맞춤 설정 or other files.
    for private in ("대표 급여", "김철수", "급여대장", "회계팀"):
        assert private not in sent
    assert world.memory_calls == []
    # The visitor's stream carries the answer and none of the owner's routing or credits.
    assert "휴학은 학기 개시일 전까지" in stream and "credits" not in stream
    assert "privacy_route" not in stream
    # Stages that did not apply are not steps of the visitor's answer.
    assert "해당 없음" not in stream
    shown = publications._visible_steps([
        {"id": "h0_0", "label": "자료 찾기", "status": "done"},
        {"id": "stage:check:copy", "label": "카피 점검 · 해당 없음", "status": "done",
         "stage": {"status": "skipped"}}], "summary")
    assert [s["label"] for s in shown] == ["자료 찾기"]
    async with world.local() as db:
        session = await db.get(ChatSession, sid)
        assert session.user_id == "owner" and session.publication_id == pub["id"]
        assert session.model == "strict-local/m"
        ledger = (await db.exec(select(CreditLedger).where(
            CreditLedger.session_id == sid))).all()
        assert ledger == [] or all(row.publication_id == pub["id"] for row in ledger)
        assert (await db.exec(select(Memory).where(Memory.user_id == "owner"))).all()[0].name \
            == "급여 비밀"
        audited = (await db.exec(select(AuditEvent).where(
            AuditEvent.action == "publication.published"))).all()
        assert audited and audited[0].actor_id == "owner"


async def test_the_bot_searches_its_material_before_the_model_answers(world):
    pub = await _publish(world)
    await _ask(world, pub["slug"], "휴학은 언제까지 신청해요?")
    (call,) = world.calls
    # The server's own first call, with the visitor's question; the model is told the
    # passages are in hand, to cite them and to say when they do not answer.
    assert call["preset"] == ("search_knowledge", {"query": "휴학은 언제까지 신청해요?"})
    assert "Material only" in call["messages"][0]["content"]
    assert call["not_covered"] == (
        "제공된 자료에서 이 질문에 대한 내용을 찾지 못했습니다.\n\n"
        "담당 부서·연락처: 학사지원팀 02-760-1234, haksa-desk@example.ac.kr")


async def test_the_answer_cites_the_article_and_the_owner_counts_it(world):
    pub = await _publish(world)
    sid, stream = await _ask(world, pub["slug"], "휴학 신청 기한 알려 주세요")
    world.as_("member")
    got = (await world.client.get(f"/b/{pub['slug']}/conversations/{sid}")).json()
    answer = got["messages"][-1]
    assert answer["role"] == "assistant" and "[1]" in answer["content"]
    # What the bot searched carries the document, its article and its 시행일.
    (call,) = world.calls
    assert "[2] 자료: 학사규정.pdf · 제12조(휴학) · 시행 2025. 3. 1." in call["ctx"].outputs[0]
    world.as_("owner")
    stats = (await world.client.get(f"/publications/{pub['id']}/stats?days=7")).json()
    assert stats["totals"]["turns"] == 1 and stats["totals"]["answers"] == 1
    assert stats["totals"]["conversations"] == 1 and stats["totals"]["visitorsAll"] == 1
    assert "휴학" in [t["word"] for t in stats["topics"]]


async def test_an_answer_with_nothing_found_is_held_and_points_to_the_contact(world):
    pub = await _publish(world)
    world.search = False
    world.answer = "주차 요금은 한 시간에 1,000원입니다. 정기권은 한 달 3만 원입니다."
    sid, _ = await _ask(world, pub["slug"], "주차 요금 얼마예요? 제 번호 010-9876-5432로 알려줘")
    world.as_("member")
    answer = (await world.client.get(
        f"/b/{pub['slug']}/conversations/{sid}")).json()["messages"][-1]["content"]
    assert answer.startswith("제공된 자료에서 이 질문에 대한 내용을 찾지 못했습니다.")
    assert "학사지원팀 02-760-1234, haksa-desk@example.ac.kr" in answer
    assert "1,000원" not in answer
    world.as_("owner")
    stats = (await world.client.get(f"/publications/{pub['id']}/stats")).json()
    assert stats["totals"]["unanswered"] == 1
    (question,) = stats["unanswered"]
    assert "주차 요금" in question["text"] and "010-9876-5432" not in question["text"]
    assert "[전화번호]" in question["text"]


async def test_published_conversations_stay_out_of_the_owners_own_screens(world):
    pub = await _publish(world)
    sid, _ = await _ask(world, pub["slug"], "휴학 기한")
    world.as_("owner")
    assert sid not in [s["id"] for s in (await world.client.get("/sessions")).json()]
    assert sid not in [s["id"] for s in (await world.client.get("/sessions/paged")).json()["items"]]
    assert (await world.client.get(f"/sessions/{sid}")).status_code == 404
    assert (await world.client.get(f"/sessions/{sid}/messages")).status_code == 404
    assert (await world.client.delete(f"/sessions/{sid}")).status_code == 404
    found = (await world.client.get("/search", params={"q": "휴학"})).json()
    assert sid not in str(found)
    # Deleting all of one's conversations leaves the bot's alone.
    await world.client.post("/sessions/delete", json={"all": True})
    async with world.local() as db:
        assert await db.get(ChatSession, sid) is not None


async def test_a_visitor_reads_only_their_own_conversation(world):
    pub = await _publish(world)
    sid, _ = await _ask(world, pub["slug"], "휴학 기한")
    world.as_("other")
    page = (await world.client.get(f"/b/{pub['slug']}")).json()
    await world.client.post(f"/b/{pub['slug']}/consent", json={"version": page["consentVersion"]})
    assert (await world.client.get(f"/b/{pub['slug']}/conversations/{sid}")).status_code == 404
    assert (await world.client.post(f"/b/{pub['slug']}/conversations/{sid}/messages",
                                    json={"content": "끼어들기"})).status_code == 404
    assert (await world.client.get(f"/b/{pub['slug']}/conversations")).json() == []


async def test_side_calls_stay_local_and_the_spend_is_the_owners_marked_as_the_bots(
        world, monkeypatch):
    priced = {**_STRICT, "creditCost": 2, "inputCreditCost": 1}
    world.catalogue = [_LOCAL, priced, _CLOUD]

    async def plan(messages, *, complete, model, api_key):
        world.side_models.append(("plan", model, None))
        return ""

    monkeypatch.setattr(sessions.answer_plan, "wanted", lambda *_a, **_k: True)
    monkeypatch.setattr(sessions.answer_plan, "plan", plan)
    pub = await _publish(world)
    sid, _ = await _ask(world, pub["slug"], "휴학과 복학 절차를 비교해서 자세히 설명해 주세요")
    assert world.side_models == [("plan", "strict-local/m", None),
                                 ("title", "strict-local/m", True)]
    async with world.local() as db:
        rows = (await db.exec(select(CreditLedger).where(CreditLedger.session_id == sid))).all()
        assert {r.reason for r in rows} == {"chat.completion", "chat.title"}
        assert {(r.user_id, r.publication_id) for r in rows} == {("owner", pub["id"])}
        assert (await db.get(User, "owner")).credits_used > 0
        assert (await db.get(User, "member")).credits_used == 0


# ── limits ─────────────────────────────────────────────────────────────


async def test_the_limits_refuse_with_429_and_say_when_to_come_back(world):
    pub = await _publish(world, limits={"perVisitorPerMinute": 1, "maxInputChars": 20})
    slug = pub["slug"]
    sid, _ = await _ask(world, slug, "휴학 기한")
    again = await world.client.post(f"/b/{slug}/conversations/{sid}/messages",
                                    json={"content": "한 번 더"})
    assert again.status_code == 429 and again.json()["detail"] == "visitor_rate_limited"
    assert 1 <= int(again.headers["Retry-After"]) <= 60
    long = await world.client.post(f"/b/{slug}/conversations/{sid}/messages",
                                   json={"content": "가" * 21})
    assert long.status_code == 422 and long.json()["detail"]["code"] == "too_long"
    # The owner may lower a limit, never raise it.
    assert publishing.clean_limits({"perBotPerMinute": 10_000}) == {"perBotPerMinute": 60}


async def test_a_free_model_still_counts_one_credit_a_turn_against_the_daily_cap(world):
    # strict-local/m is priced at 0: without a floor the cap would never be reached.
    pub = await _publish(world, limits={"creditsPerDay": 2})
    slug = pub["slug"]
    sid, _ = await _ask(world, slug, "휴학 기한")
    second = await world.client.post(f"/b/{slug}/conversations/{sid}/messages",
                                     json={"content": "복학 기한"})
    assert second.status_code == 200
    third = await world.client.post(f"/b/{slug}/conversations/{sid}/messages",
                                    json={"content": "한 번 더"})
    assert third.status_code == 429 and third.json()["detail"] == "daily_credits_reached"


async def test_a_bot_runs_at_most_its_concurrency_and_then_waits_in_line(world):
    first = await publishing.acquire_slot("bot", per_bot=1, instance_max=16, wait=0)
    assert first is not None
    assert await publishing.acquire_slot("bot", per_bot=1, instance_max=16, wait=0) is None
    # The instance-wide cap counts every bot.
    assert await publishing.acquire_slot("other-bot", per_bot=4, instance_max=1, wait=0) is None
    await publishing.release_slot(first)
    assert await publishing.acquire_slot("bot", per_bot=1, instance_max=16, wait=0) is not None
    async with world.local() as db:
        db.add(PublicationSlot(publication_id="bot",
                               acquired_at=utcnow() - timedelta(hours=1)))
        await db.commit()
    # A crashed turn's slot expires instead of holding the bot forever.
    await publishing.release_slot((await publishing.acquire_slot(
        "bot", per_bot=1, instance_max=16, wait=0)) or "")


async def test_an_owner_out_of_credits_pauses_the_bot_until_the_credits_come_back(world):
    pub = await _publish(world)
    async with world.local() as db:
        owner = await db.get(User, "owner")
        owner.credits_used = owner.monthly_credits
        db.add(owner)
        await db.commit()
    world.as_("member")
    page = (await world.client.get(f"/b/{pub['slug']}")).json()
    await world.client.post(f"/b/{pub['slug']}/consent", json={"version": page["consentVersion"]})
    sid = (await world.client.post(f"/b/{pub['slug']}/conversations")).json()["id"]
    refused = await world.client.post(f"/b/{pub['slug']}/conversations/{sid}/messages",
                                      json={"content": "휴학"})
    assert refused.status_code == 409 and refused.json()["detail"] == "bot_paused"
    async with world.local() as db:
        row = await db.get(Publication, pub["id"])
        assert (row.status, row.pause_reason) == ("paused", "credits")
        owner = await db.get(User, "owner")
        owner.credits_used = 0
        db.add(owner)
        await db.commit()
        assert await publishing.check_owners(db) == 1
        assert (await db.get(Publication, pub["id"])).status == "approved"


# ── what the owner and an administrator may read ───────────────────────


async def test_the_owner_reads_a_conversation_only_when_the_visitor_sends_it(world):
    pub = await _publish(world)
    sid, _ = await _ask(world, pub["slug"], "휴학 기한, 제 번호는 010-2222-3333")
    world.as_("owner")
    assert (await world.client.get(f"/publications/{pub['id']}/conversations")).json() == []
    assert (await world.client.get(
        f"/publications/{pub['id']}/conversations/{sid}")).status_code == 404
    world.as_("member")
    answer = (await world.client.get(f"/b/{pub['slug']}/conversations/{sid}")).json()
    message_id = answer["messages"][-1]["id"]
    assert (await world.client.patch(f"/b/{pub['slug']}/messages/{message_id}/rating",
                                     json={"rating": "down"})).status_code == 200
    assert (await world.client.post(
        f"/b/{pub['slug']}/conversations/{sid}/share")).status_code == 200
    world.as_("owner")
    (row,) = (await world.client.get(f"/publications/{pub['id']}/conversations")).json()
    assert row["id"] == sid and row["sharedAt"]
    read = (await world.client.get(f"/publications/{pub['id']}/conversations/{sid}")).json()
    question = read["messages"][0]["content"]
    assert "010-2222-3333" not in question and "[전화번호]" in question
    stats = (await world.client.get(f"/publications/{pub['id']}/stats")).json()
    assert (stats["totals"]["down"], stats["totals"]["shared"]) == (1, 1)
    # The visitor takes it back.
    world.as_("member")
    await world.client.delete(f"/b/{pub['slug']}/conversations/{sid}/share")
    world.as_("owner")
    assert (await world.client.get(f"/publications/{pub['id']}/conversations")).json() == []


async def test_conversation_view_asks_consent_again_and_shows_what_came_after(world):
    pub = await _publish(world)
    before, _ = await _ask(world, pub["slug"], "휴학 기한")
    world.as_("owner")
    changed = (await world.client.patch(f"/publications/{pub['id']}",
                                        json={"conversationView": "on",
                                              "retentionDays": 14})).json()
    assert changed["consentVersion"] == 2
    world.as_("member")
    page = (await world.client.get(f"/b/{pub['slug']}")).json()
    assert page["consented"] is False and page["retentionDays"] == 14
    after, _ = await _ask(world, pub["slug"], "복학 기한")
    world.as_("owner")
    ids = [r["id"] for r in (await world.client.get(
        f"/publications/{pub['id']}/conversations")).json()]
    assert after in ids and before not in ids
    async with world.local() as db:
        consent = (await db.exec(select(AuditEvent).where(
            AuditEvent.action == "publication.consent"))).all()
        assert consent and consent[0].actor_id == "member"


async def test_an_administrator_reads_a_conversation_only_with_a_recorded_reason(world):
    pub = await _publish(world)
    sid, _ = await _ask(world, pub["slug"], "휴학 기한")
    world.as_("admin")
    no_reason = await world.client.get(f"/admin/publications/{pub['id']}/conversations/{sid}")
    assert no_reason.status_code == 422
    read = await world.client.get(f"/admin/publications/{pub['id']}/conversations/{sid}",
                                  params={"reason": "신고 접수 2026-10-10 남용 조사"})
    assert read.status_code == 200 and read.json()["messages"]
    async with world.local() as db:
        (event,) = (await db.exec(select(AuditEvent).where(
            AuditEvent.action == "publication.admin_read"))).all()
        assert event.actor_id == "admin" and event.severity == "warn"
        assert event.event_metadata["reason"].startswith("신고 접수")
    paused = await world.client.post(f"/admin/publications/{pub['id']}/status",
                                     json={"status": "paused", "reason": "남용 조사 중"})
    assert paused.json()["status"] == "paused"
    world.as_("owner")
    resumed = await world.client.patch(f"/publications/{pub['id']}", json={"status": "approved"})
    assert resumed.status_code == 403


# ── retention, members' list, deleting ─────────────────────────────────


async def test_retention_removes_old_conversations_and_questions(world):
    pub = await _publish(world, retentionDays=7)
    world.search = False
    sid, _ = await _ask(world, pub["slug"], "주차 요금")
    async with world.local() as db:
        session = await db.get(ChatSession, sid)
        session.updated_at = utcnow() - timedelta(days=8)
        db.add(session)
        for q in (await db.exec(select(PublicationQuestion))).all():
            q.at = utcnow() - timedelta(days=8)
            db.add(q)
        db.add(PublicationCounter(publication_id=pub["id"], window="m200001010000",
                                  key="turns", value=3))
        await db.commit()
        swept = await publishing.sweep(db)
        assert swept == {"sessions": 1, "questions": 1}
        assert await db.get(ChatSession, sid) is None
        assert (await db.exec(select(Message).where(Message.session_id == sid))).all() == []
        assert (await db.exec(select(PublicationCounter).where(
            PublicationCounter.window == "m200001010000"))).all() == []


async def test_members_find_open_bots_and_owners_their_own(world):
    pub = await _publish(world)
    world.as_("member")
    (bot,) = (await world.client.get("/bots")).json()
    assert bot["slug"] == pub["slug"] and bot["ownerName"] == "만든 사람"
    assert bot["mine"] is False and bot["id"] is None
    world.as_("owner")
    await world.client.patch(f"/publications/{pub['id']}", json={"status": "paused"})
    assert (await world.client.get("/bots")).json()[0]["mine"] is True
    world.as_("member")
    assert (await world.client.get("/bots")).json() == []


async def test_deleting_the_bot_takes_its_conversations_and_copies(world):
    pub = await _publish(world)
    sid, _ = await _ask(world, pub["slug"], "휴학 기한")
    world.as_("owner")
    assert (await world.client.delete(f"/publications/{pub['id']}")).status_code == 204
    async with world.local() as db:
        assert await db.get(ChatSession, sid) is None
        assert (await db.exec(select(Agent).where(col(Agent.snapshot_of).is_not(None)))).all() \
            == []
        assert await db.get(Agent, "agent-1") is not None


# ── the migration ──────────────────────────────────────────────────────


def _migration(name: str = "0064_agent_publishing"):
    path = Path(__file__).parents[1] / "alembic" / "versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"migration_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migrations_add_and_drop_what_publishing_needs_on_sqlite():
    first, outside = _migration(), _migration("0065_publishing_outside")
    channels = _migration("0066_publishing_channels")
    assert (first.revision, first.down_revision) == ("0064", "0063")
    assert (outside.revision, outside.down_revision) == ("0065", "0064")
    assert (channels.revision, channels.down_revision) == ("0066", "0065")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for ddl in ("CREATE TABLE users (id VARCHAR PRIMARY KEY)",
                    "CREATE TABLE sessions (id VARCHAR PRIMARY KEY)",
                    "CREATE TABLE credit_ledger (id VARCHAR PRIMARY KEY)",
                    "CREATE TABLE agents (id VARCHAR PRIMARY KEY)",
                    "CREATE TABLE governance (id VARCHAR PRIMARY KEY)"):
            conn.execute(sa.text(ddl))
        with Operations.context(MigrationContext.configure(conn)):
            first.upgrade()
            outside.upgrade()
            channels.upgrade()
        inspector = sa.inspect(conn)
        for model in (Publication, PublicationVersion, PublicationVisitor, PublicationCounter,
                      PublicationSlot, PublicationQuestion, PublicationKey):
            assert {c.name for c in model.__table__.columns} == {
                c["name"] for c in inspector.get_columns(model.__tablename__)}
        assert {"publication_id", "visitor_id", "publication_version_id", "shared_at",
                "viewable"} <= {c["name"] for c in inspector.get_columns("sessions")}
        assert {"snapshot_of", "contact"} <= {c["name"] for c in inspector.get_columns("agents")}
        conn.execute(sa.text(
            "INSERT INTO publications (id, owner_id, slug, visibility, created_at, updated_at) "
            "VALUES ('p', 'u', 's', 'link', '2026-10-10', '2026-10-10')"))
        with Operations.context(MigrationContext.configure(conn)):
            channels.downgrade()
        inspector = sa.inspect(conn)
        assert "publication_keys" not in inspector.get_table_names()
        assert "channels" not in {c["name"] for c in inspector.get_columns("publications")}
        with Operations.context(MigrationContext.configure(conn)):
            outside.downgrade()
        inspector = sa.inspect(conn)
        assert "review_state" not in {c["name"] for c in inspector.get_columns("publications")}
        assert "anon_id" not in {c["name"] for c in inspector.get_columns("publication_visitors")}
        # The code before 0065 serves members only: every bot goes back to members.
        assert conn.execute(sa.text("SELECT visibility FROM publications")).scalar() == "internal"
        with Operations.context(MigrationContext.configure(conn)):
            first.downgrade()
        inspector = sa.inspect(conn)
        assert "publications" not in inspector.get_table_names()
        assert {c["name"] for c in inspector.get_columns("sessions")} == {"id"}
        assert {c["name"] for c in inspector.get_columns("agents")} == {"id"}


def test_frequent_topics_are_nouns_not_question_forms_or_verb_pieces():
    # The ops lane's 「자주 나온 주제」 read 있나 · 빌릴 · 열어 · 시까지 · 비우면 · 1은.
    asked = {
        "연체료 있나요?": ["연체료"],
        "일요일에도 열어요?": ["일요일"],
        "대학원생은 몇 권 빌릴 수 있어요?": ["대학원생"],
        "평일 몇 시까지 해요?": ["평일"],
        "열람실 자리를 30분 비우면 어떻게 돼요?": ["열람실", "자리"],
        "토요일 운영 시간은?": ["토요일", "운영", "시간"],
    }
    for question, words in asked.items():
        assert publishing.topics(question) == words, question
    assert "1은" not in publishing.topics("1은 2 더하기 3이면?")
