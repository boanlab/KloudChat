# ruff: noqa: F811 — fixtures imported from test_user_pipelines are parameters here.
"""An agent's 진행 과정: 바로 답하기, 기본 과정에서 시작 with the 묶음 it ticks, 직접 구성.

묶음 are data, the shipped ones (`app/bundles/*.toml`) and a person's own alike, and an
official agent is its base and its bundles: a person ticking the same bundles on the same base
gets the same agent. Bundle items that are checks run inside the agent's own flow; only a step
(a chat agent's calculation, an export, a person's bundle with a review or another agent)
makes a pipeline of the agent's own, whose `@self` step runs the agent's own flow with the
turn's web search and connectors. A 직접 구성 or a person's bundle that exports, reviews or
runs another agent waits for an administrator before others run it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# The `wired` and `writers` fixtures are shared with the pipeline tests.
from test_user_pipelines import _MODEL, wired, writers  # noqa: F401

from app.models.chat import ChatSession, Message, PipelineRun, SessionKind
from app.models.user import AuditEvent, User, UserRole
from app.models.workspace import (
    Agent,
    Artifact,
    ArtifactVersion,
    DesignSystem,
    Skill,
    StoredFile,
    UserPipeline,
    Visibility,
)
from app.pipeline import compose
from app.routers import sessions
from app.routers import user_pipelines as up
from app.routers import workspace as ws
from app.routers.pipelines import describe, pipeline_for_agent
from app.schemas.workspace import AgentIn
from app.services import bases, bundles, check_skills, starter

# ── the shipped bundles and bases ──────────────────────────────────────


def test_the_shipped_bundles_are_data_in_the_users_shape():
    shipped = bundles.shipped()
    assert list(shipped) == ["prepare", "review", "finish"]
    assert [b.name for b in shipped.values()] == ["쓰기 전에 준비하기", "다 쓴 뒤 검토하기",
                                                  "마무리하기"]
    assert [i.id for i in shipped["review"].items] == ["ground", "numbers", "titles", "format",
                                                       "repair"]
    # Where an item runs follows its kind, on each surface.
    review = shipped["review"]
    assert [bundles.target(i, "report") for i in review.items] == [
        "check", "option", "option", "flow", "flow"]
    assert [bundles.target(i, "chat") for i in review.items] == [
        "check", "option", "option", None, "flow"]
    prepare = shipped["prepare"]
    assert [bundles.target(i, "report") for i in prepare.items] == ["check", "flow", "flow"]
    assert [bundles.target(i, "chat") for i in prepare.items] == ["check", "before", "before"]
    finish = shipped["finish"]
    assert [bundles.target(i, "chat") for i in finish.items] == ["check", None]
    assert all(bundles.target(i, "slides") is None for b in shipped.values() for i in b.items)
    # Shipped bundles never wait for review, whatever they hold.
    assert not finish.risky


def test_the_bases_are_the_offered_agents_with_their_steps_and_bundles():
    everyone = [b.key for b in bases.bases()]
    admins = [b.key for b in bases.bases(drafts=True)]
    assert everyone == ["gaejo-report", "minutes", "comparison-report", "data-analysis"]
    assert admins == ["gaejo-report", "document", "minutes", "comparison-report",
                      "data-analysis", "slides"]
    assert bases.get("career-docs") is None and bases.get("client-proposal") is None
    for base in bases.bases(drafts=True):
        assert 1 <= len(base.steps) <= 5
    minutes = bases.get("minutes")
    assert minutes.steps[1] == ("question", "회의 날짜처럼 빠진 정보가 있으면 먼저 물어봅니다")
    assert (minutes.surface, minutes.doc_format, minutes.template) == (
        "report", "minutes", "doc-minutes")
    # 기록 정리 = 준비 + 검토 (its 형식 검사 checks owners and deadlines) + 마무리 without export.
    assert [b["bundle"] for b in minutes.bundles] == ["prepare", "review", "finish"]
    assert "할 일의 담당자·기한 확인" in minutes.format_checks and minutes.asks
    assert not bases.get("document").asks
    assert bases.get("slides").bundles == () and not bases.get("slides").custom


@pytest.mark.parametrize("key", [s["key"] for s in starter._AGENTS])
def test_an_agents_checks_are_what_its_bundles_compile_to(key):
    spec = starter.agent_spec(key)
    surface = spec["surface"]
    made = bundles.compile(surface, spec["bundles"], bundles.shipped())
    assert made.check_skills == spec["checks"] and made.check_options == spec["check_options"]
    # No shipped agent needs a pipeline of its own.
    assert made.steps is None


def test_steps_only_where_an_item_is_a_step():
    shipped = bundles.shipped()
    data = [{"bundle": "prepare", "items": {"dates": True, "amounts": False}},
            {"bundle": "review", "items": {"numbers": True}}]
    made = bundles.compile("chat", data, shipped)
    assert made.check_skills == ["gap-check", "grounding-check"]
    assert made.check_options == {"tool_numbers": True}
    assert [(s["kind"], s["ref"]) for s in made.steps] == [
        ("human", "gaps"), ("skill", "date-calc"), ("agent", "@self")]
    report = bundles.compile("report", [
        {"bundle": "prepare", "items": {"ask": False}},
        {"bundle": "finish", "items": {"export": "hwpx"}}], shipped)
    assert report.check_skills == ["generic-language-killer"]
    assert [(s["kind"], s["ref"], s["settings"]) for s in report.steps] == [
        ("human", "outline", {}), ("agent", "@self", {}), ("export", "", {"formats": ["hwpx"]})]
    # A report flow computes its dates itself: no step for it.
    assert bundles.compile("report", [{"bundle": "prepare"}], shipped).steps is None
    # The finish bundle's export is a DOCX unless a format is picked.
    finished = bundles.compile("report", [{"bundle": "finish"}], shipped)
    assert finished.steps[-1]["settings"] == {"formats": ["docx"]}


def test_strictness_comes_from_the_agent_not_its_name():
    assert check_skills.options_for("data-analysis") == {"today": ""}
    assert check_skills.options_for("", None, {"tool_numbers": True}) == {
        "today": "", "bare": True, "numbers": "computed"}
    assert check_skills.options_for("", None, {"titles": True, "evil": True}) == {
        "today": "", "titles": True, "products": True}


def test_what_needs_review_is_a_custom_order_with_an_export_a_review_or_another_agent():
    own = compose.normalize([{"kind": "agent", "ref": "@self"},
                             {"kind": "check", "ref": "grounding-check"}])
    assert not bases.needs_review(None, own)
    for extra in ({"kind": "export", "settings": {"formats": ["pdf"]}},
                  {"kind": "review", "settings": {"model": "m"}},
                  {"kind": "agent", "ref": "someone-else"}):
        assert bases.needs_review(None, [*own, extra])


# ── the API ─────────────────────────────────────────────────────────────


@pytest.fixture
async def engine():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    tables = [metadata.tables[m.__tablename__] for m in (
        User, ChatSession, Message, Artifact, ArtifactVersion, PipelineRun, Agent,
        UserPipeline, AuditEvent, StoredFile, Skill, DesignSystem)]
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=tables)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _people(local):
    async with local() as db:
        me = User(email="me@example.com", name="나", password_hash="x")
        other = User(email="o@example.com", name="남", password_hash="x")
        admin = User(email="a@example.com", name="관리자", password_hash="x", role=UserRole.admin)
        db.add_all([me, other, admin])
        await db.commit()
        return me, other, admin


def _agent(name="내 회의 기록", **kw) -> AgentIn:
    return AgentIn(name=name, system_prompt="너는 우리 팀의 회의 기록을 쓴다.", **kw)


async def test_a_base_sets_the_bindings_and_ticks_make_and_drop_its_own_pipeline(wired):
    me, _, _ = await _people(wired.local)
    async with wired.local() as db:
        made = await ws.create_agent(_agent(kinds=["chat"]), me, db)
        assert made.process["mode"] == "direct" and made.process["chain"] == ["바로 답합니다"]

        out = await up.put_process(made.id, up.ProcessIn(mode="base", base="minutes"), me, db)
        assert (out["mode"], out["base"]) == ("base", "minutes")
        assert [b["bundle"] for b in out["bundles"]] == ["prepare", "review", "finish"]
        assert out["bundles"][2]["items"] == {"plain": True, "export": ""}
        row = await db.get(Agent, made.id)
        assert (row.base_key, row.surface, row.doc_format, row.render_template_id) == (
            "minutes", "report", "minutes", "doc-minutes")
        # Exactly what the official agent holds: its checks, on the row.
        assert row.check_skills == starter.agent_spec("minutes")["checks"]
        assert row.bundles == starter.agent_spec("minutes")["bundles"]
        assert "report" in row.kinds and row.pipeline_id is None
        # The agent runs the base's process: its checks and question card.
        assert await sessions._agent_catalog_key(db, row) == "minutes"

        out = await up.put_process(made.id, up.ProcessIn(
            mode="base", base="minutes", bundles=[
                {"bundle": "prepare", "items": {"ask": False}}, {"bundle": "review"},
                {"bundle": "finish", "items": {"export": "docx"}}]), me, db)
        assert out["mode"] == "base" and out["bundles"][2]["items"]["export"] == "docx"
        assert out["bundles"][0]["items"]["ask"] is False
        assert out["chain"][-1] == "다 쓰면 파일로 내보내기 (DOCX)"
        pipe = await db.get(UserPipeline, out["pipelineId"])
        assert pipe.agent_id == made.id and pipe.base == "minutes"
        assert "gap-check" not in (await db.get(Agent, made.id)).check_skills
        assert [(s["kind"], s["ref"]) for s in pipe.stages] == [
            ("human", "outline"), ("agent", "@self"), ("export", "")]
        # Not a pipeline to start a 직접 구성 from: it is this agent's own.
        assert await up.list_mine(me, db) == []

        out = await up.put_process(made.id, up.ProcessIn(mode="base", base="minutes"), me, db)
        assert out["mode"] == "base" and "pipelineId" not in out
        assert await db.get(UserPipeline, pipe.id) is None
        assert (await db.get(Agent, made.id)).pipeline_id is None

        out = await up.put_process(made.id, up.ProcessIn(mode="direct", bundles=[
            {"bundle": "review", "items": {"titles": True}}]), me, db)
        assert out["mode"] == "direct" and (await db.get(Agent, made.id)).base_key is None
        # 바로 답하기 runs the checks the person ticked.
        row = await db.get(Agent, made.id)
        assert row.check_skills == ["grounding-check"] and row.check_options == {"titles": True}
        assert out["chain"][0] == "바로 답합니다"


async def test_a_custom_order_runs_the_agent_itself_and_is_checked(wired):
    me, _, _ = await _people(wired.local)
    async with wired.local() as db:
        made = await ws.create_agent(_agent(kinds=["report"], surface="report"), me, db)
        with pytest.raises(HTTPException) as caught:
            await up.put_process(made.id, up.ProcessIn(mode="custom", stages=[
                {"kind": "check", "ref": "grounding-check"}, {"kind": "agent", "ref": "@self"}]),
                me, db)
        assert caught.value.detail["problems"][0]["code"] == "needs_product"
        out = await up.put_process(made.id, up.ProcessIn(mode="custom", base="comparison-report",
                                                         stages=[
            {"kind": "human", "ref": "gaps"}, {"kind": "agent", "ref": "@self"},
            {"kind": "check", "ref": "generic-language-killer"}]), me, db)
        assert out["mode"] == "custom" and out["base"] == "comparison-report"
        assert [s["title"] for s in out["steps"]] == [
            "확인 질문", "내 회의 기록", "상투어 걷어내기"]
        assert out["chain"] == ["확인 질문", "내 회의 기록", "상투어 걷어내기"]
        assert not out["needsReview"] and out["review"] == ""
        # 발표 자료 has no step a pipeline can run.
        with pytest.raises(HTTPException) as caught:
            await up.put_process(made.id, up.ProcessIn(mode="custom", base="slides", stages=[]),
                                 me, db)
        assert caught.value.detail["problems"][0]["code"] in ("base_unknown",
                                                              "custom_not_offered")


async def test_a_catalogue_agent_keeps_its_base(wired):
    me, _, _ = await _people(wired.local)
    async with wired.local() as db:
        row = Agent(owner_id=me.id, name="기록 정리", slug="rec", kinds=["report"],
                    surface="report", doc_format="minutes", catalog_key="minutes")
        db.add(row)
        await db.commit()
        with pytest.raises(HTTPException) as caught:
            await up.put_process(row.id, up.ProcessIn(mode="direct"), me, db)
        assert caught.value.detail["problems"][0]["code"] == "base_fixed"
        out = await up.put_process(row.id, up.ProcessIn(
            mode="base", base="gaejo-report", bundles=[
                {"bundle": "finish", "items": {"export": "pdf"}}]), me, db)
        assert out["base"] == "minutes" and out["bundles"][0]["items"]["export"] == "pdf"


async def test_the_own_step_keeps_web_search_and_connectors(wired, writers, monkeypatch):
    from app.services.tools import registry as tool_registry

    seen: list[dict] = []

    async def build_tools(db, person, **kwargs):
        seen.append(kwargs)
        return []

    monkeypatch.setattr(tool_registry, "build_tools", build_tools)
    me, _, _ = await _people(wired.local)
    async with wired.local() as db:
        made = await ws.create_agent(_agent(name="내 분석", kinds=["chat"]), me, db)
        out = await up.put_process(made.id, up.ProcessIn(
            mode="base", base="data-analysis", bundles=[
                {"bundle": "prepare", "items": {"ask": False, "amounts": False}},
                {"bundle": "review", "items": {"numbers": True}}]), me, db)
        agent = await db.get(Agent, made.id)
        row = await db.get(UserPipeline, out["pipelineId"])
        session = ChatSession(user_id=me.id, kind=SessionKind.chat, agent_id=agent.id)
        db.add(session)
        await db.commit()
        assert (await up.bound(db, me, session, agent)).id == row.id

    async with wired.local() as db:
        response = await up.turn_response(
            db, me, session, row, pending={}, approved_plan=None, proceed_as_is=False,
            request="합계를 내 줘", model=_MODEL, outline_model=None, api_key="k",
            routing=None, trusted=[], untrusted=["# 표\nA 1"], design_tokens=None,
            skills_event=None, context_steps=None, web_search=True, attachments=[],
            catalogue=[_MODEL], agent=agent)
        chunks = [c async for c in response.body_iterator]
    assert any('"type": "done"' in c or '"type":"done"' in c for c in chunks)
    # The turn's web search and the person's connectors, as a chat turn with the agent has.
    assert (seen[0]["web_search"], seen[0]["include_connectors"]) == (True, True)
    # The step that ran is the agent itself, on its own prompt.
    assert writers["turn"][0][1][0]["content"].startswith("너는 우리 팀의 회의 기록을 쓴다.")


async def test_the_own_step_on_a_cloud_model_searches_and_reaches_connectors(monkeypatch):
    from types import SimpleNamespace

    from app.pipeline.bundle import Bundle, Request
    from app.services.tools import registry as tool_registry

    seen: list[dict] = []

    async def build_tools(db, person, **kwargs):
        seen.append(kwargs)
        return []

    class Local:
        async def __aenter__(self):
            return SimpleNamespace(get=self.get)

        async def __aexit__(self, *_a):
            return False

        async def get(self, *_a):
            return SimpleNamespace(id="u")

    monkeypatch.setattr(tool_registry, "build_tools", build_tools)
    monkeypatch.setattr(sessions, "SessionLocal", Local)
    step = compose.AgentStep(0, {"kind": "agent", "ref": "@self", "settings": {}}, "나",
                             output="answer", after_outline=False, final=True)
    bundle = Bundle(request=Request(text="x", model="cloud", api_key="k", user_id="u",
                                    turn={"user_id": "u", "web_search": True}))
    own = compose.AgentInfo(id="@self", name="나", output="answer", self_of="a1")
    other = compose.AgentInfo(id="a2", name="남", output="answer")
    for info in (own, other):
        await step._tools(bundle, info, {"id": "cloud"}, False)
    assert (seen[0]["web_search"], seen[0]["include_connectors"]) == (True, True)
    assert (seen[1]["web_search"], seen[1]["include_connectors"]) == (False, False)
    # A strict-local turn sends nothing out, its own step included.
    await step._tools(bundle, own, {"id": "local"}, True)
    assert (seen[2]["web_search"], seen[2]["include_connectors"], seen[2]["strict_local"]) == (
        False, False, True)


async def test_sharing_a_preset_needs_no_review_and_a_custom_order_waits(wired):
    me, other, admin = await _people(wired.local)
    async with wired.local() as db:
        shared = await ws.create_agent(_agent(kinds=["report"], visibility=Visibility.org),
                                       me, db)
        out = await up.put_process(shared.id, up.ProcessIn(
            mode="base", base="minutes", bundles=[{"bundle": "finish"}]), me, db)
        assert out["review"] == "" and not out["needsReview"]
        agent = await db.get(Agent, shared.id)
        session = ChatSession(user_id=other.id, kind=SessionKind.report, agent_id=agent.id)
        db.add(session)
        await db.commit()
        # Someone the agent is shared with runs its preset as it is.
        assert (await up.bound(db, other, session, agent)).id == out["pipelineId"]

        out = await up.put_process(shared.id, up.ProcessIn(mode="custom", stages=[
            {"kind": "human", "ref": "outline"}, {"kind": "agent", "ref": "@self"},
            {"kind": "review", "settings": {"model": "strict-local/judge"}}]), me, db)
        assert out["needsReview"] and out["review"] == "pending"
        assert await up.bound(db, other, session, agent) is None
        assert [r["id"] for r in await up.admin_list(admin, db)] == [out["pipelineId"]]
        await up.approve(out["pipelineId"], admin, db)
        assert (await up.bound(db, other, session, agent)).id == out["pipelineId"]


async def test_a_private_agent_shared_later_sends_its_custom_order_for_review(wired):
    me, _, _ = await _people(wired.local)
    async with wired.local() as db:
        made = await ws.create_agent(_agent(kinds=["report"]), me, db)
        out = await up.put_process(made.id, up.ProcessIn(mode="custom", stages=[
            {"kind": "human", "ref": "outline"}, {"kind": "agent", "ref": "@self"},
            {"kind": "export", "settings": {"formats": ["pdf", "md"]}}]), me, db)
        assert out["needsReview"] and out["review"] == ""
        patched = await ws.patch_agent(made.id, AgentIn(name=made.name, kinds=["report"],
                                                        visibility=Visibility.org), me, db)
        assert patched.process["review"] == "pending"


async def test_installing_clones_a_preset_and_deleting_drops_it(wired):
    me, other, _ = await _people(wired.local)
    async with wired.local() as db:
        shared = await ws.create_agent(_agent(kinds=["report"], visibility=Visibility.org),
                                       me, db)
        out = await up.put_process(shared.id, up.ProcessIn(
            mode="base", base="minutes", bundles=[
                {"bundle": "review"}, {"bundle": "finish", "items": {"export": "pdf"}}]), me, db)
        copy = await ws.install_agent(shared.id, other, db)
        assert copy.base_key == "minutes" and copy.process["mode"] == "base"
        assert [b["bundle"] for b in copy.process["bundles"]] == ["review", "finish"]
        assert copy.process["bundles"][1]["items"]["export"] == "pdf"
        assert copy.check_skills == ["grounding-check", "generic-language-killer"]
        theirs = await db.get(UserPipeline, copy.pipeline_id)
        assert theirs.owner_id == other.id and theirs.agent_id == copy.id
        assert theirs.origin_id == out["pipelineId"]

        # A custom order not yet approved stays with its author: the copy runs the base.
        custom = await ws.create_agent(_agent(name="검토까지", kinds=["report"],
                                              visibility=Visibility.org), me, db)
        await up.put_process(custom.id, up.ProcessIn(mode="custom", base="minutes", stages=[
            {"kind": "human", "ref": "outline"}, {"kind": "agent", "ref": "@self"},
            {"kind": "export", "settings": {"formats": ["pdf", "md"]}}]), me, db)
        second = await ws.install_agent(custom.id, other, db)
        assert second.pipeline_id is None and second.process["mode"] == "base"

        session = ChatSession(user_id=other.id, kind=SessionKind.report, agent_id=copy.id,
                              pipeline_id=theirs.id)
        db.add(session)
        await db.commit()
        await ws.delete_agent(copy.id, other, db)
        assert await db.get(UserPipeline, theirs.id) is None
        assert (await db.get(ChatSession, session.id)).pipeline_id is None
        # The author's own is untouched.
        assert await db.get(UserPipeline, out["pipelineId"]) is not None


async def test_a_retired_agent_takes_no_process(wired):
    me, _, _ = await _people(wired.local)
    async with wired.local() as db:
        row = Agent(owner_id=me.id, name="고객 맞춤 제안서", slug="p",
                    catalog_key="client-proposal",
                    kinds=["report"], retired=True)
        db.add(row)
        await db.commit()
        with pytest.raises(HTTPException) as caught:
            await up.put_process(row.id, up.ProcessIn(mode="base"), me, db)
        assert caught.value.status_code == 410


async def test_the_bases_endpoint_offers_drafts_to_administrators_only(wired):
    me, _, admin = await _people(wired.local)
    async with wired.local() as db:
        mine = await up.list_bases(me, db)
        assert "document" not in [b["key"] for b in mine["bases"]]
        assert "document" in [b["key"] for b in (await up.list_bases(admin, db))["bases"]]
        assert [b["ref"] for b in mine["bundles"]] == ["prepare", "review", "finish"]
    assert set(starter.draft_agents()) == {"document", "slides"}


@pytest.mark.parametrize("key", [b.key for b in bases.bases(drafts=True)])
async def test_a_user_agent_on_a_base_with_its_bundles_is_the_official_agent(wired, key):
    """A person picking the base and ticking the same bundles gets the same agent: the same
    checks, strictness, format, template and pipeline, with no pipeline of its own."""
    me, _, admin = await _people(wired.local)
    spec = starter.agent_spec(key)
    async with wired.local() as db:
        official = Agent(owner_id=admin.id, name=spec["name"], slug=key, catalog_key=key,
                         kinds=spec["kinds"], surface=spec["surface"],
                         doc_format=spec.get("doc_format"),
                         render_template_id=spec.get("template"),
                         check_skills=spec["checks"], check_options=spec["check_options"] or None,
                         bundles=spec["bundles"])
        db.add(official)
        await db.commit()
        made = await ws.create_agent(_agent(name="내 것", kinds=[spec["surface"]]), admin, db)
        await up.put_process(made.id, up.ProcessIn(mode="base", base=key,
                                                   bundles=spec["bundles"]), admin, db)
        mine = await db.get(Agent, made.id)
        assert (mine.check_skills, mine.check_options or None) == (
            official.check_skills, official.check_options or None)
        assert (mine.surface, mine.doc_format, mine.render_template_id) == (
            official.surface, official.doc_format, official.render_template_id)
        assert mine.pipeline_id is None and mine.bundles == official.bundles
        keys = [await sessions._agent_catalog_key(db, a) for a in (mine, official)]
        assert keys == [key, key]
        attached = [check_skills.attached(agent_checks=a.check_skills) for a in (mine, official)]
        assert attached[0] == attached[1]
        pipes = [describe(pipeline_for_agent(k, a.doc_format, a.surface), checks)
                 for k, a, checks in zip(keys, (mine, official), attached, strict=True)]
        assert pipes[0] == pipes[1]
        assert check_skills.fact_set(mine.doc_format, keys[0]) == check_skills.fact_set(
            official.doc_format, keys[1])
        # Without ticks the base brings its own.
        again = await ws.create_agent(_agent(name="둘째", kinds=[spec["surface"]]), admin, db)
        await up.put_process(again.id, up.ProcessIn(mode="base", base=key), admin, db)
        assert (await db.get(Agent, again.id)).check_skills == official.check_skills


async def test_a_persons_bundle_is_ticked_like_a_shipped_one(wired):
    me, other, admin = await _people(wired.local)
    async with wired.local() as db:
        with pytest.raises(HTTPException) as caught:
            await up.create_bundle(up.BundleIn(name="이상한", stages=[
                {"kind": "check", "ref": "no-such-check"}]), me, db)
        assert caught.value.detail["code"] == "invalid_bundle"
        safe = await up.create_bundle(up.BundleIn(name="꼼꼼히", stages=[
            {"kind": "check", "ref": "grounding-check"},
            {"kind": "check", "ref": "generic-language-killer"}]), me, db)
        files = await up.create_bundle(up.BundleIn(name="두 형식으로", stages=[
            {"kind": "agent", "ref": "@self"},
            {"kind": "export", "settings": {"formats": ["pdf", "hwpx"]}}]), me, db)
        assert not safe["risky"] and files["risky"]
        # Not a pipeline to start a 직접 구성 from, and not in the old store.
        assert await up.list_mine(me, db) == []

        made = await ws.create_agent(_agent(kinds=["report"], visibility=Visibility.org),
                                     me, db)
        out = await up.put_process(made.id, up.ProcessIn(mode="base", base="gaejo-report",
                                                         bundles=[
            {"bundle": safe["ref"]}, {"bundle": files["ref"]}]), me, db)
        row = await db.get(Agent, made.id)
        assert row.check_skills == ["grounding-check", "generic-language-killer"]
        pipe = await db.get(UserPipeline, out["pipelineId"])
        assert [(s["kind"], s["ref"]) for s in pipe.stages] == [
            ("human", "outline"), ("agent", "@self"), ("export", "")]
        # Its export waits for an administrator before anyone else runs the agent with it.
        assert out["needsReview"]
        session = ChatSession(user_id=other.id, kind=SessionKind.report, agent_id=row.id)
        db.add(session)
        await db.commit()
        assert await up.bound(db, other, session, row) is None
        assert files["ref"] not in {b["ref"] for b in (await up.list_bases(other, db))["bundles"]}

        shared = await up.share_bundle(files["id"], up.ShareIn(visibility="org"), me, db)
        assert (shared["review"], shared["visibility"]) == ("pending", "private")
        assert files["id"] in [r["id"] for r in await up.admin_list(admin, db)]
        await up.approve(files["id"], admin, db)
        assert files["ref"] in {b["ref"] for b in (await up.list_bases(other, db))["bundles"]}
        assert (await up.bound(db, other, session, row)).id == pipe.id
        # A bundle with nothing to approve is shared at once.
        open_one = await up.share_bundle(safe["id"], up.ShareIn(visibility="org"), me, db)
        assert open_one["visibility"] == "org" and open_one["review"] == ""
        listed = await up.list_bundles(other, db)
        assert {b["name"] for b in listed["shared"]} == {"꼼꼼히", "두 형식으로"}
        # Editing an approved bundle sends it back.
        edited = await up.update_bundle(files["id"], up.BundleIn(name="두 형식으로", stages=[
            {"kind": "agent", "ref": "@self"},
            {"kind": "export", "settings": {"formats": ["pdf"]}}]), me, db)
        assert (edited["review"], edited["visibility"]) == ("pending", "private")


# ── migration 0060 ──────────────────────────────────────────────────────


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0060_agent_process.py"
    spec = importlib.util.spec_from_file_location("agent_process_0060", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_retires_backfills_and_clears_on_sqlite():
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0060", "0059")
    assert set(migration.RETIRED_AGENTS) == set(starter.retired_agents())
    assert migration.BASE_FOR_FORMAT == bases.BY_FORMAT
    for spec in starter._AGENTS:
        assert migration.CHECKS[spec["key"]] == list(spec.get("checks") or []), spec["key"]
        assert migration.OPTIONS.get(spec["key"]) == (spec["check_options"] or None)
    from app.services import design_templates

    assert set(migration.RETIRED_TEMPLATES) == set(design_templates.RETIRED_KEPT)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE agents (id VARCHAR PRIMARY KEY, catalog_key VARCHAR, origin_id VARCHAR,"
            " doc_format VARCHAR, render_template_id VARCHAR)"))
        conn.execute(sa.text("CREATE TABLE user_pipelines (id VARCHAR PRIMARY KEY)"))
        conn.execute(sa.text(
            "CREATE TABLE templates (id VARCHAR PRIMARY KEY, render_template_id VARCHAR)"))
        conn.execute(sa.text(
            "CREATE TABLE projects (id VARCHAR PRIMARY KEY, render_templates JSON)"))
        conn.execute(sa.text(
            "INSERT INTO agents VALUES"
            " ('cat', 'client-proposal', NULL, 'client_proposal', 'doc-client-proposal'),"
            " ('copy', NULL, 'cat', NULL, 'doc-client-proposal'),"
            " ('mine', NULL, NULL, 'minutes', 'doc-career-history'),"
            " ('free', NULL, NULL, NULL, NULL),"
            " ('gaejo', 'gaejo-report', NULL, 'gaejo', 'doc-gaejo-report')"))
        conn.execute(sa.text("INSERT INTO templates VALUES ('t', 'doc-career-history')"))
        conn.execute(sa.text(
            "INSERT INTO projects VALUES ('p', '{\"report\": \"doc-client-proposal\","
            " \"slides\": \"deck-briefing\"}')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        checks = {r[0]: (r[1], r[2]) for r in conn.execute(sa.text(
            "SELECT id, check_skills, check_options FROM agents"))}
        full = '["gap-check", "grounding-check", "generic-language-killer"]'
        assert checks == {"cat": (full, None), "copy": (full, None), "mine": (full, None),
                          "free": (None, None), "gaejo": (full, None)}
        rows = {r[0]: r[1:] for r in conn.execute(sa.text(
            "SELECT id, retired, base_key, render_template_id FROM agents"))}
        assert rows == {
            "cat": (1, None, "doc-client-proposal"),
            "copy": (1, None, "doc-client-proposal"),
            "mine": (0, "minutes", None),
            "free": (0, None, None),
            "gaejo": (0, None, "doc-gaejo-report"),
        }
        assert conn.execute(sa.text("SELECT render_template_id FROM templates")).scalar() == ""
        assert "client" not in conn.execute(sa.text(
            "SELECT render_templates FROM projects")).scalar()
        assert "agent_id" in {c["name"] for c in sa.inspect(conn).get_columns("user_pipelines")}
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        columns = {c["name"] for c in sa.inspect(conn).get_columns("agents")}
        assert not {"retired", "base_key"} & columns
