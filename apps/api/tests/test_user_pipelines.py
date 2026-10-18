"""User pipelines: contracts, recursion guard, approval, and composed runs."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, Message, PipelineRun, Role, SessionKind
from app.models.user import AuditEvent, User, UserRole
from app.models.workspace import Agent, Artifact, ArtifactVersion, UserPipeline
from app.pipeline import compose, registry
from app.pipeline.bundle import Bundle, Request
from app.pipeline.stage import Context
from app.routers import runs as runs_router
from app.routers import sessions
from app.routers import user_pipelines as up
from app.services import agent as agent_service
from app.services import gap_questions, report

_MODEL = {"id": "strict-local/writer", "label": "w", "kinds": ["report", "chat"],
          "creditCost": 1, "dataBoundary": "self_hosted"}
_JUDGE = {"id": "strict-local/judge", "label": "j", "kinds": ["chat"], "creditCost": 2,
          "dataBoundary": "self_hosted"}


def _info(id_: str, output: str = "document", **kw) -> compose.AgentInfo:
    # The questions its card asks, resolved as `user_pipelines.agents_for` does.
    kw.setdefault("questions", tuple(gap_questions.resolve(
        own=None, base_key=kw.get("catalog_key") or None, doc_format=kw.get("doc_format"))))
    return compose.AgentInfo(id=id_, name=kw.pop("name", id_.upper()), output=output, **kw)


_AGENTS = {
    "data": _info("data", "answer", name="데이터 분석", catalog_key="data-analysis"),
    "cmp": _info("cmp", name="비교 보고서", doc_format="comparison",
                 catalog_key="comparison-report"),
    "gaejo": _info("gaejo", name="개조식 보고", doc_format="gaejo", catalog_key="gaejo-report"),
    "mins": _info("mins", name="회의록", doc_format="minutes", catalog_key="minutes"),
    "deck": _info("deck", "", name="슬라이드"),
}


def _steps(*rows) -> list[dict]:
    return compose.normalize([{"kind": k, "ref": r, "settings": s}
                              for k, r, s in (row if len(row) == 3 else (*row, {})
                                              for row in rows)])


def _codes(verdict: compose.Verdict) -> list[tuple[int, str]]:
    return [(p.step, p.code) for p in verdict.problems]


# ── contracts ──────────────────────────────────────────────────────────


def test_a_valid_chain_names_its_surface_by_the_last_agent():
    steps = _steps(("agent", "data"), ("agent", "cmp"), ("check", "grounding-check"),
                   ("agent", "gaejo"), ("review", "", {"model": "strict-local/judge"}),
                   ("export", "", {"formats": ["docx", "pdf"]}))
    verdict = compose.validate(steps, agents=_AGENTS, allowed_models={"strict-local/judge"})
    assert verdict.ok, verdict.problems
    assert verdict.surface == "report"
    assert compose.validate(_steps(("agent", "cmp"), ("agent", "data")),
                            agents=_AGENTS).surface == "chat"


def test_a_step_that_needs_a_product_is_refused_before_one_exists():
    steps = _steps(("check", "grounding-check"), ("agent", "data"), ("check", "copy-check"),
                   ("agent", "cmp"), ("check", "copy-check"), ("export", "", {"formats": []}))
    verdict = compose.validate(steps, agents=_AGENTS)
    assert _codes(verdict) == [(1, "needs_product"), (5, "needs_product"), (6, "no_formats")]
    # The message says which step and what it needs, in Korean.
    assert verdict.problems[0].message == (
        "1번 검사(근거 대조): 앞 단계에서 만든 문서나 답이 있어야 합니다.")
    assert verdict.problems[1].message == (
        "5번 검사(카피 점검): 앞 단계에서 만든 답이 있어야 합니다.")


def test_order_rules_for_exports_outlines_and_cards():
    verdict = compose.validate(_steps(
        ("human", "outline"), ("agent", "data"), ("agent", "cmp"),
        ("export", "", {"formats": ["pdf"]}), ("check", "grounding-check"),
        ("human", "gaps")), agents=_AGENTS)
    assert _codes(verdict) == [(1, "outline_needs_writer"), (5, "after_export"),
                               (6, "after_export"), (6, "needs_agent_after")]
    assert compose.validate(_steps(("export", "", {"formats": ["pdf"]})),
                            agents=_AGENTS).problems[-1].code == "no_agent"
    # Exporting needs the last agent to write a document.
    last = compose.validate(_steps(("agent", "cmp"), ("export", "", {"formats": ["pdf"]}),
                                   ("agent", "data")), agents=_AGENTS)
    assert ("export_needs_document" in [p.code for p in last.problems])


def test_unknown_or_unusable_steps_and_limits():
    steps = _steps(("agent", "nobody"), ("agent", "deck"), ("skill", "pdf-table"),
                   ("check", "gap-check"), ("human", "coffee"), ("dance", "x"))
    assert _codes(compose.validate(steps, agents=_AGENTS)) == [
        (1, "agent_missing"), (2, "agent_surface"), (3, "skill_unknown"), (4, "check_unknown"),
        (5, "human_unknown"), (6, "unknown_kind")]
    many = _steps(*[("agent", "data")] * (compose.MAX_STEPS + 1))
    assert _codes(compose.validate(many, agents=_AGENTS)) == [(0, "too_many")]
    assert _codes(compose.validate([], agents=_AGENTS)) == [(0, "empty")]


def test_the_review_model_must_be_one_the_person_may_use():
    steps = _steps(("agent", "data"), ("review", "", {"model": "cloud/big"}))
    verdict = compose.validate(steps, agents=_AGENTS, allowed_models={"strict-local/judge"})
    assert _codes(verdict) == [(2, "model_not_allowed")]


def test_a_card_before_an_agent_with_nothing_to_ask_is_a_warning_not_a_problem():
    verdict = compose.validate(_steps(("human", "gaps"), ("agent", "cmp")), agents={
        "cmp": _info("cmp", name="자유 보고서")})
    assert verdict.ok and [w.code for w in verdict.warnings] == ["nothing_to_ask"]
    assert compose.validate(_steps(("human", "gaps"), ("agent", "mins")),
                            agents=_AGENTS).warnings == []


def test_a_pipeline_cannot_include_an_agent_bound_to_itself_even_through_another():
    agents = {**_AGENTS, "self": _info("self", name="나", pipeline_id="P"),
              "via": _info("via", name="경유", pipeline_id="Q")}
    direct = compose.validate(_steps(("agent", "self")), agents=agents, self_id="P")
    assert _codes(direct) == [(1, "recursion")]
    # Q runs an agent bound to P: P → via → Q → … → P.
    loop = compose.validate(_steps(("agent", "via")), agents=agents, self_id="P",
                            graph={"Q": {"P"}})
    assert _codes(loop) == [(1, "recursion")]
    assert compose.validate(_steps(("agent", "via")), agents=agents, self_id="P",
                            graph={"Q": set()}).ok


def test_sharing_needs_agents_others_can_run():
    agents = {**_AGENTS, "mine": _info("mine", name="내 것")}
    steps = _steps(("agent", "mine"))
    assert compose.validate(steps, agents=agents).ok
    assert _codes(compose.validate(steps, agents=agents, sharing=True)) == [(1, "agent_private")]


def test_compiling_puts_the_save_before_the_first_export():
    steps = _steps(("human", "outline"), ("agent", "cmp"), ("check", "grounding-check"),
                   ("export", "", {"formats": ["pdf"]}), ("export", "", {"formats": ["md"]}))
    stages = compose.compile_steps(steps, _AGENTS)
    assert [s.name for s in stages] == ["1:human", "2:agent", "3:check", "save", "4:export",
                                        "5:export"]
    assert [s.title for s in stages] == ["개요 확인", "비교 보고서", "근거 대조", "저장",
                                         "내보내기", "내보내기"]
    assert stages[1].after_outline and stages[1].final
    chat = compose.compile_steps(_steps(("agent", "cmp"), ("agent", "data")), _AGENTS)
    assert isinstance(chat[-1], compose.AnswerSaveStep) and not chat[0].final


def test_settings_are_cleaned_to_what_each_kind_reads():
    steps = compose.normalize([
        {"kind": "agent", "ref": "a", "settings": {"instruction": " 표로 ", "evil": 1}},
        {"kind": "export", "settings": {"formats": ["pdf", "exe", "pdf"]}},
        {"id": "x", "kind": "review", "settings": {"model": "m", "repair": 1}},
        {"id": "x", "kind": "check", "ref": "grounding-check", "settings": {"a": 1}},
    ])
    assert [s["id"] for s in steps] == ["s1", "s2", "x", "xx"]
    assert steps[0]["settings"] == {"instruction": "표로"}
    assert steps[1]["settings"] == {"formats": ["pdf"]}
    assert steps[2]["settings"] == {"model": "m", "repair": True}
    assert steps[3]["settings"] == {}


# ── storage, editing and approval ──────────────────────────────────────


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
        UserPipeline, AuditEvent)]
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=tables)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def wired(monkeypatch, engine):
    settled: list[dict] = []

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    async def models(_user):
        return [_MODEL, _JUDGE]

    async def catalogue():
        return {"models": [_MODEL, _JUDGE]}

    async def nothing(*_a, **_k):
        return None

    async def credentials(_user):
        return "u", "fresh-key"

    async def policy():
        return SimpleNamespace(intent_filter=False, pii_masking=False, blocked_categories=[])

    monkeypatch.setattr(sessions, "SessionLocal", local)
    monkeypatch.setattr(sessions, "_require_egress_policy", policy)
    monkeypatch.setattr(sessions, "settle", lambda *_a, **k: settled.append(k))
    monkeypatch.setattr(sessions, "record_searches", lambda *_a, **_k: None)
    monkeypatch.setattr(up, "review_models", models)
    monkeypatch.setattr(runs_router.model_service, "list_models_for_egress", catalogue)
    monkeypatch.setattr(runs_router.litellm_service, "ensure_key", nothing)
    monkeypatch.setattr(runs_router.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(runs_router, "has_headroom", lambda *_a: True)
    monkeypatch.setenv("KLOUDCHAT_PIPELINE_STEPS", "1")
    return SimpleNamespace(local=local, settled=settled)


async def _people(local) -> SimpleNamespace:
    async with local() as db:
        me = User(email="me@example.com", name="나", password_hash="x")
        other = User(email="o@example.com", name="남", password_hash="x")
        admin = User(email="a@example.com", name="관리자", password_hash="x", role=UserRole.admin)
        db.add_all([me, other, admin])
        await db.commit()
        agents = [
            Agent(owner_id=me.id, slug="a0", name="데이터 분석", kinds=["chat"], surface="chat",
                  catalog_key="data-analysis", system_prompt="수치는 자료에서만."),
            Agent(owner_id=me.id, slug="a1", name="비교 보고서", kinds=["report"], surface="report",
                  doc_format="comparison", catalog_key="comparison-report"),
            Agent(owner_id=me.id, slug="a2", name="회의록", kinds=["report"], surface="report",
                  doc_format="minutes", catalog_key="minutes"),
            Agent(owner_id=me.id, slug="a3", name="내 메모", kinds=["chat"], surface="chat"),
            Agent(owner_id=other.id, slug="a4", name="남의 비교 보고서", kinds=["report"],
                  surface="report",
                  doc_format="comparison", catalog_key="comparison-report"),
            Agent(owner_id=other.id, slug="a5", name="남의 비공개", kinds=["chat"], surface="chat"),
        ]
        db.add_all(agents)
        await db.commit()
        return SimpleNamespace(me=me, other=other, admin=admin, data=agents[0], cmp=agents[1],
                               mins=agents[2], memo=agents[3], their_cmp=agents[4],
                               their_private=agents[5])


async def _pipeline(db, user: User, name: str, stages: list[dict], **fields) -> UserPipeline:
    """A stored pipeline, its agent steps keyed as the process form stores them."""
    agents = await up.agents_for(db, user)
    row = UserPipeline(owner_id=user.id, name=name,
                       stages=up._with_keys(compose.normalize(stages), agents), **fields)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def test_a_pending_pipeline_is_approved_or_rejected_with_a_reason(wired):
    p = await _people(wired.local)
    async with wired.local() as db:
        made = await _pipeline(db, p.me, "분석→비교", [
            {"kind": "agent", "ref": p.data.id}, {"kind": "agent", "ref": p.cmp.id}],
            review="pending")
        assert [r["id"] for r in await up.admin_list(p.admin, db)] == [made.id]
        with pytest.raises(HTTPException) as caught:
            await up.reject(made.id, up.RejectIn(note=" "), p.admin, db)
        assert caught.value.detail == "reason_required"
        approved = await up.approve(made.id, p.admin, db)
        assert (approved["review"], approved["visibility"]) == ("approved", "org")
        with pytest.raises(HTTPException) as caught:
            await up.approve(made.id, p.admin, db)
        assert caught.value.status_code == 409
        assert await up.admin_list(p.admin, db) == []

        # An approved pipeline runs for others; a private one only for its author.
        own = await _pipeline(db, p.me, "메모", [{"kind": "agent", "ref": p.memo.id}])
        shared = ChatSession(user_id=p.other.id, kind=SessionKind.report, pipeline_id=made.id)
        private = ChatSession(user_id=p.other.id, kind=SessionKind.chat, pipeline_id=own.id)
        assert (await up.bound(db, p.other, shared, None)).id == made.id
        assert await up.bound(db, p.other, private, None) is None

        again = await _pipeline(db, p.me, "다시", [{"kind": "agent", "ref": p.cmp.id}],
                                review="pending")
        no = await up.reject(again.id, up.RejectIn(note="검토 모델이 비쌉니다"), p.admin, db)
        assert (no["review"], no["reviewNote"]) == ("rejected", "검토 모델이 비쌉니다")
        assert no["visibility"] == "private"


async def test_a_pipeline_cannot_run_the_agent_it_is_bound_to(wired):
    p = await _people(wired.local)
    async with wired.local() as db:
        doc = await _pipeline(db, p.me, "비교", [
            {"kind": "agent", "ref": p.data.id}, {"kind": "agent", "ref": p.cmp.id}])
        mins = await db.get(Agent, p.mins.id)
        mins.pipeline_id = doc.id
        db.add(mins)
        await db.commit()
        # Now 회의록 runs `doc`; a pipeline that runs 회의록 would call itself.
        loop = await up.validate_draft(up.ValidateIn(id=doc.id, stages=[
            {"kind": "agent", "ref": p.mins.id}]), p.me, db)
        assert [x["code"] for x in loop["problems"]] == ["recursion"]


# ── running one ────────────────────────────────────────────────────────


def _events(chunks: list[str]) -> list[dict]:
    return [json.loads(line[6:]) for chunk in chunks for line in chunk.splitlines()
            if line.startswith("data: ")]


@pytest.fixture
def writers(monkeypatch):
    """The sub-runs' model calls: a chat agent's answer, a document agent's plan and write,
    a reviewer's findings."""
    seen: dict[str, list] = {"plan": [], "write": [], "complete": []}

    async def complete(model, messages, api_key, max_tokens):
        text = messages[-1]["content"]
        seen["complete"].append((model, text))
        if "independent reviewer" in text:
            return json.dumps({"findings": [
                {"quote": "전년 대비 두 배", "why": "자료에 없는 비교입니다", "fix": "전년과 비슷"},
                {"quote": "없는 문장", "why": "찾을 수 없음", "fix": ""}]}), {
                "inputTokens": 7, "outputTokens": 3}
        return "분석: A안 총액 9억 원, B안 총액 7억 원.", {"inputTokens": 11, "outputTokens": 5}

    async def plan(bundle, info, *, request):
        seen["plan"].append((info.name, request))
        return {"title": "비교", "sections": [{"title": "결론"}]}

    def write(bundle, info, *, request, approved, built):
        async def run():
            material = "\n".join(compose._material(bundle))
            seen["write"].append((info.name, approved, material))
            sub = Bundle(request=Request(text=request, model="m", api_key="k"))
            sub.spec.format_id = info.doc_format
            sub.document.sections = [{"id": "s1", "heading": "결론", "level": 1,
                                      "content": "B안이 7억 원으로 싸다. 전년 대비 두 배다."}]
            sub.wire["title"] = {"type": "title", "title": "A안과 B안 비교"}
            built.append((sub, "comparison-report", "write"))
            yield {"type": "section", "sectionId": "s1", "heading": "결론", "content": "…"}
            yield {"type": "usage", "inputTokens": 100, "outputTokens": 40}
        return run()

    async def run_turn(model, messages, tools, ctx, **kwargs):
        # A chat agent step's own tool loop: its code printed the totals it answers with.
        seen.setdefault("turn", []).append((model, messages, sorted(t.name for t in tools)))
        ctx.outputs.append("A안 총액 9억 원, B안 총액 7억 원")
        yield {"type": "step", "id": "t1", "category": "tool", "label": "코드 실행",
               "status": "done"}
        yield {"type": "delta", "text": "분석: A안 총액 9억 원, B안 총액 7억 원."}
        yield {"type": "usage", "inputTokens": 11, "outputTokens": 5}

    monkeypatch.setattr(report, "_complete", complete)
    monkeypatch.setattr(compose, "plan_for", plan)
    monkeypatch.setattr(compose, "write_pass", write)
    monkeypatch.setattr(agent_service, "run_turn", run_turn)
    return seen


async def _run(local, user, session, row, *, request="A안과 B안을 비교해 줘", pending=None,
               approved_plan=None, proceed_as_is=False, kind=SessionKind.report):
    async with local() as db:
        response = await up.turn_response(
            db, user, session, row, pending=pending or {}, approved_plan=approved_plan,
            proceed_as_is=proceed_as_is, request=request, model=_MODEL, outline_model=None,
            api_key="k", routing=None, trusted=[], untrusted=["# 견적\nA안 9억 원, B안 7억 원"],
            design_tokens=None, skills_event=None, context_steps=None, web_search=False,
            attachments=[], catalogue=[_MODEL, _JUDGE])
        return _events([c async for c in response.body_iterator])


async def test_a_composed_run_hands_each_product_on_and_reviews_the_last(wired, writers):
    p = await _people(wired.local)
    async with wired.local() as db:
        row = await _pipeline(db, p.me, "분석→비교", [
            {"kind": "agent", "ref": p.data.id},
            {"kind": "agent", "ref": p.cmp.id, "settings": {"instruction": "결론만 1쪽"}},
            {"kind": "check", "ref": "grounding-check"},
            {"kind": "review", "settings": {"model": _JUDGE["id"], "repair": True}},
        ])
        session = ChatSession(user_id=p.me.id, kind=SessionKind.report, pipeline_id=row.id)
        db.add(session)
        await db.commit()
        assert (await up.bound(db, p.me, session, None)).id == row.id
    events = await _run(wired.local, p.me, session, row)

    assert events[0]["type"] == "run" and events[0]["flow"] == "compose"
    assert events[0]["pipeline"] == f"user:{row.id}"
    records = [e["stage"] for e in events if e.get("category") == "pipeline"]
    assert [(r["name"], r["label"], r["status"]) for r in records] == [
        ("1:agent", "데이터 분석", "done"), ("2:agent", "비교 보고서", "done"),
        ("3:check", "근거 대조", "done"), ("4:review", "검토", "done"), ("save", "저장", "done")]
    assert [e["type"] for e in events][-3:] == ["artifact", "usage", "done"]
    # The answer of the first agent is the second one's material, with its own instruction.
    name, approved, material = writers["write"][0]
    assert name == "비교 보고서" and approved == {"title": "비교", "sections": [{"title": "결론"}]}
    assert "# 앞 단계 결과: 데이터 분석\n분석: A안 총액 9억 원" in material
    assert writers["plan"][0][1].endswith("이 단계에서 할 일:\n결론만 1쪽")
    # The review ran on its own model and its fix was applied once.
    assert writers["complete"][-1][0] == _JUDGE["id"]
    assert records[3]["summary"] == "지적 1개 · 고침 1개"

    async with wired.local() as db:
        artifact = (await db.exec(select(Artifact))).one()
        assert artifact.data["sections"][0]["content"] == "B안이 7억 원으로 싸다. 전년과 비슷다."
        run = (await db.exec(select(PipelineRun))).one()
        assert (run.flow, run.status, run.artifact_id) == ("compose", "done", artifact.id)
        message = await db.get(Message, run.message_id)
        assert [s["name"] for s in message.routing["run"]["stages"]][-1] == "save"
    # The writer's tokens at its price, the review's at the judge's.
    assert [s["reason"] for s in wired.settled] == ["report.generate", "pipeline.review"]
    assert wired.settled[1]["model"] == _JUDGE["id"]

    # 「이 단계부터 다시」 from the review: the agents are not run again.
    async with wired.local() as db:
        session = await db.get(ChatSession, session.id)
        session.artifact_id = artifact.id
        db.add(session)
        await db.commit()
        user = await db.get(User, p.me.id)
        response = await runs_router.rerun(session.id, run.id,
                                           runs_router.RerunIn(from_stage="4:review"), user, db)
        again = _events([c async for c in response.body_iterator])
    assert again[0]["parentId"] == run.id and again[0]["fromStage"] == "4:review"
    assert [e["stage"]["name"] for e in again if e.get("category") == "pipeline"] == [
        "4:review", "save"]
    assert len(writers["write"]) == 1
    async with wired.local() as db:
        artifact = await db.get(Artifact, artifact.id)
        assert artifact.version == 2
        rerun = (await db.exec(select(PipelineRun).where(PipelineRun.parent_id == run.id))).one()
        stages = {r["stage"]: r for r in rerun.stages}
        assert stages["1:agent"]["attempt"] == 1 and stages["4:review"]["attempt"] == 2
        said = (await db.exec(select(Message).where(Message.role == Role.assistant)
                              .order_by(Message.created_at))).all()
        assert said[-1].content == "「검토」 단계부터 다시 만들었습니다."


async def test_a_run_waits_at_its_card_and_resumes_with_the_answers(wired, writers):
    p = await _people(wired.local)
    async with wired.local() as db:
        row = await _pipeline(db, p.me, "회의록", [
            {"kind": "human", "ref": "gaps"}, {"kind": "agent", "ref": p.mins.id}])
        session = ChatSession(user_id=p.me.id, kind=SessionKind.report)
        db.add(session)
        await db.commit()
    first = await _run(wired.local, p.me, session, row, request="회의록 써 줘")
    assert [e["type"] for e in first][-3:] == ["needs", "usage", "done"]
    questions = next(e for e in first if e["type"] == "needs")["questions"]
    assert questions and all(q["id"].startswith("gap:") for q in questions)
    assert writers["write"] == []
    async with wired.local() as db:
        session = await db.get(ChatSession, session.id)
        pending = dict(session.pending)
        assert pending["stage"] == "clarify" and pending["compose"]["stage"] == "1:human"
        waiting = await db.get(PipelineRun, pending["compose"]["runId"])
        assert waiting.status == "waiting"
    answer = {questions[0]["id"]: "10월 7일"}
    second = await _run(wired.local, p.me, session, row, request="회의록 써 줘",
                        pending={**pending, "answers": answer})
    records = [e["stage"] for e in second if e.get("category") == "pipeline"]
    assert [(r["name"], r["status"]) for r in records] == [
        ("1:human", "done"), ("2:agent", "done"), ("save", "done")]
    assert records[0]["summary"] == "답 1개"
    assert "10월 7일" in writers["write"][0][2]
    assert second[0]["parentId"] == waiting.id


async def test_a_chat_pipeline_stores_the_answer_as_the_message(wired, writers, monkeypatch):
    released: list[str] = []

    async def release(session_id, token=None):
        released.append(session_id)

    monkeypatch.setattr(sessions, "_release_turn", release)
    p = await _people(wired.local)
    async with wired.local() as db:
        row = await _pipeline(db, p.me, "분석", [
            {"kind": "agent", "ref": p.data.id}, {"kind": "check", "ref": "grounding-check"}])
        session = ChatSession(user_id=p.me.id, kind=SessionKind.chat)
        db.add(session)
        await db.commit()
    events = await _run(wired.local, p.me, session, row, request="견적 비교")
    assert next(e for e in events if e["type"] == "delta")["text"].startswith("분석:")
    done = events[-1]
    assert done["type"] == "done" and done["messageId"]
    assert released == [session.id]
    async with wired.local() as db:
        message = await db.get(Message, done["messageId"])
        assert message.content.startswith("분석:") and message.routing["run"]["flow"] == "compose"
    # A pipeline that writes a document is not run on a chat screen.
    async with wired.local() as db:
        row = await _pipeline(db, p.me, "문서", [{"kind": "agent", "ref": p.cmp.id}])
    refused = await _run(wired.local, p.me, session, row)
    assert refused[0]["code"] == "pipeline_surface"


async def test_an_export_step_hands_over_the_stored_document(wired, monkeypatch):
    class _FakeExport:
        name, kind = "export", "io"

        async def apply(self, bundle, ctx):
            bundle.files = [{"name": "비교.pdf"}]
            bundle.document.handed = {"id": "export", "label": "파일 1개 만듦", "status": "done",
                                      "outputs": bundle.files}
            bundle.document.reply = "「비교.pdf」 파일을 만들었습니다."

    monkeypatch.setitem(registry._STAGES, "export", _FakeExport())
    p = await _people(wired.local)
    async with wired.local() as db:
        session = ChatSession(user_id=p.me.id, kind=SessionKind.report)
        artifact = Artifact(user_id=p.me.id, kind="report", title="비교", data={})
        message = Message(session_id=session.id, role=Role.assistant, content="썼습니다.")
        db.add_all([session, artifact, message])
        await db.commit()
    step = compose.ExportStep(4, {"kind": "export", "ref": "", "settings": {"formats": ["pdf"]}},
                              "내보내기")
    work = Bundle(request=Request(text="", model="m", api_key="k", session_id=session.id,
                                  turn={"user_id": p.me.id}))
    work.document.saved = {"artifactId": artifact.id, "messageId": message.id}
    out = [e async for e in step.run(work, compose.Context())]
    assert out[0]["outputs"] == [{"name": "비교.pdf"}] and out[-1]["type"] == "delta"
    async with wired.local() as db:
        stored = await db.get(Message, message.id)
        assert stored.steps[0]["label"] == "파일 1개 만듦"
        assert stored.content.endswith("「비교.pdf」 파일을 만들었습니다.")


# ── the migration ──────────────────────────────────────────────────────


def test_the_migration_adds_and_drops_user_pipelines_on_sqlite():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0054_user_pipelines.py"
    spec = importlib.util.spec_from_file_location("user_pipelines_0054", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("0054", "0053")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id VARCHAR PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE agents (id VARCHAR PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE sessions (id VARCHAR PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        columns = {c["name"] for c in sa.inspect(conn).get_columns("user_pipelines")}
        # `agent_id` and `kind` came later (0060).
        assert {c.name for c in UserPipeline.__table__.columns} - {"agent_id", "kind"} == columns
        assert "pipeline_id" in {c["name"] for c in sa.inspect(conn).get_columns("agents")}
        assert "pipeline_id" in {c["name"] for c in sa.inspect(conn).get_columns("sessions")}
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "user_pipelines" not in sa.inspect(conn).get_table_names()


def test_runs_of_a_user_pipeline_are_rerunnable_flows():
    assert "compose" in runs_router.RERUNNABLE


async def test_a_pipeline_bound_to_an_agent_is_not_a_starting_point(wired):
    p = await _people(wired.local)
    async with wired.local() as db:
        runs_cmp = await _pipeline(db, p.me, "비교까지", [
            {"kind": "agent", "ref": p.data.id}, {"kind": "agent", "ref": p.cmp.id}])
        runs_mins = await _pipeline(db, p.me, "회의록까지", [
            {"kind": "agent", "ref": p.data.id}, {"kind": "agent", "ref": p.mins.id}])
        mins = await db.get(Agent, p.mins.id)
        mins.pipeline_id = runs_cmp.id
        db.add(mins)
        await db.commit()
        # A pipeline bound to an agent is that agent's 진행 과정 now, not one to start from.
        listed = await up.list_mine(p.me, db)
        assert [r["id"] for r in listed] == [runs_mins.id]
        assert listed[0]["problems"] == [] and listed[0]["boundAgents"] == []


def test_product_messages_take_the_right_particle():
    from app.pipeline.compose import AgentInfo, validate

    agents = {"c": AgentInfo(id="c", name="데이터 분석", output="answer"),
              "d": AgentInfo(id="d", name="비교", output="document")}
    said = [p.message for p in validate([
        {"kind": "agent", "ref": "c", "settings": {}},
        {"kind": "export", "ref": "", "settings": {"formats": ["hwpx"]}}], agents=agents).problems]
    assert said == ["2번 내보내기(내보내기): 앞 단계에서 만든 문서가 있어야 합니다."]
    said = [p.message for p in validate([
        {"kind": "check", "ref": "grounding-check", "settings": {}},
        {"kind": "agent", "ref": "d", "settings": {}}], agents=agents).problems]
    assert said == ["1번 검사(근거 대조): 앞 단계에서 만든 문서나 답이 있어야 합니다."]


async def test_a_review_fix_with_a_figure_no_one_gave_is_not_applied(monkeypatch):
    from app.pipeline.stage import Context

    async def complete(model, messages, api_key, max_tokens):
        return json.dumps({"findings": [
            {"quote": "대출 [수치]권", "why": "빈칸", "fix": "대출 16,600권"},
            {"quote": "방학 평균 5,800권", "why": "계산 오류", "fix": "방학 평균 6,100권"}]}), {}

    monkeypatch.setattr(report, "_complete", complete)
    bundle = Bundle(request=Request(text="비교해 줘", model="m", api_key="k",
                                    untrusted=["7월 대출 6,100권, 8월 5,800권"]))
    bundle.wire["product"] = {"stage": "1:agent", "kind": "document", "title": "보고"}
    bundle.document.sections = [{"heading": "현황",
                                 "content": "학기 대출 [수치]권. 방학 평균 5,800권."}]
    step = compose.ReviewStep(1, {"kind": "review", "ref": "",
                                  "settings": {"model": "m", "repair": True}}, "검토")
    events = [e async for e in step.run(bundle, Context())]
    # The figure the material has goes in; the one the reviewer worked out alone does not.
    assert bundle.document.sections[0]["content"] == "학기 대출 [수치]권. 방학 평균 6,100권."
    assert events[-1]["label"] == "지적 2개 · 고침 1개"
    assert "자료에 없는 수치(16600)가 있어 적용하지 않음" in bundle.findings.still[0].why


def test_a_handed_on_document_carries_captions_not_inline_pictures():
    text = compose._document_text([{"heading": "현황", "content": (
        "표 아래.\n\n![그림 1. 월별 방문자](data:image/png;base64," + "A" * 5000 + ")\n\n끝.")}])
    assert text == "## 현황\n표 아래.\n\n[그림: 그림 1. 월별 방문자]\n\n끝."


async def test_a_shared_pipeline_runs_on_the_takers_own_copies(wired):
    from app.models.workspace import Visibility

    p = await _people(wired.local)
    async with wired.local() as db:
        original = Agent(owner_id=p.admin.id, slug="o1", name="비교·분석 보고서", kinds=["report"],
                         surface="report", doc_format="comparison",
                         catalog_key="comparison-report", visibility=Visibility.org)
        db.add(original)
        await db.commit()
        made = await _pipeline(db, p.me, "비교만", [{"kind": "agent", "ref": p.cmp.id}],
                               review="pending")
        await up.approve(made.id, p.admin, db)
        # The author's step runs the author's private copy; the taker holds their own copy
        # and also sees the organisation's original: their copy is the one it runs on.
        row = await db.get(UserPipeline, made.id)
        steps = up._steps_for(row, p.other, await up.agents_for(db, p.other))
        assert steps[0]["ref"] == p.their_cmp.id
        # Someone with no copy runs the organisation's original.
        third = User(email="t@example.com", name="셋", password_hash="x")
        db.add(third)
        await db.commit()
        steps = up._steps_for(row, third, await up.agents_for(db, third))
        assert steps[0]["ref"] == original.id
        # The author's own view is untouched.
        assert up._steps_for(row, p.me, await up.agents_for(db, p.me))[0]["ref"] == p.cmp.id


async def test_a_chat_step_is_told_nobody_can_answer_a_question(monkeypatch):
    seen: list[list[dict]] = []

    async def run_turn(model, messages, tools, ctx, **kwargs):
        seen.append(messages)
        yield {"type": "delta", "text": "학기 중 월평균 방문자 67,375명."}
        yield {"type": "usage", "inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(agent_service, "run_turn", run_turn)
    bundle = Bundle(request=Request(text="학기와 방학을 비교해 줘", model="m", api_key="k",
                                    untrusted=["월,방문자\n3월,61200"]))
    info = compose.AgentInfo(id="a", name="데이터 분석", output="answer",
                             system_prompt="Ask which split the user wants before computing.")
    step = compose.AgentStep(0, {"kind": "agent", "ref": "a", "settings": {}}, "데이터 분석",
                             output="answer", after_outline=False, final=False)
    [e async for e in step._answer(bundle, info)]
    system = seen[0][0]["content"]
    assert system.startswith("Ask which split")
    assert "nobody can answer a question" in system and "Do not ask anything" in system
    assert bundle.document.answer == "학기 중 월평균 방문자 67,375명."


# ── provenance: an earlier step's product is read, never trusted for figures ──


async def test_a_chat_agent_step_runs_its_tools_and_its_finish_flow(monkeypatch):
    seen: dict = {}

    async def tools(self, bundle, info, model, strict):
        return [SimpleNamespace(name="run_code"), SimpleNamespace(name="calculate")]

    async def run_turn(model, messages, tools, ctx, **kwargs):
        seen["tools"] = [t.name for t in tools]
        seen["definitions"] = kwargs.get("tool_definitions")
        ctx.outputs.append("방학 월평균 방문자 26300")
        yield {"type": "step", "id": "c1", "category": "tool", "label": "코드 실행",
               "status": "done"}
        yield {"type": "delta", "text": "방학 월평균 26,300명, 학기 월평균 99,999명."}
        yield {"type": "usage", "inputTokens": 40, "outputTokens": 9}

    monkeypatch.setattr(compose.AgentStep, "_tools", tools)
    monkeypatch.setattr("app.services.tools.base.to_openai", lambda ts: [{"n": t.name} for t in ts])
    monkeypatch.setattr(agent_service, "run_turn", run_turn)
    # a self-hosted writer: its marks stay where they stand (a small external one's go to the
    # end box, docs/architecture.md "Mechanisms by model group")
    bundle = Bundle(request=Request(text="학기와 방학 비교", model="local/m", api_key="k",
                                    untrusted=["월,방문자\n2025-07,28400\n2025-08,25900"]),
                    usage={"inputTokens": 0, "outputTokens": 0})
    info = compose.AgentInfo(id="a", name="데이터 분석", output="answer",
                             catalog_key="data-analysis", checks=("grounding-check",),
                             tools=("run_code", "calculate"))
    step = compose.AgentStep(0, {"kind": "agent", "ref": "a", "settings": {}}, "데이터 분석",
                             output="answer", after_outline=False, final=True)
    events = [e async for e in step._answer(bundle, info)]
    assert seen["tools"] == ["run_code", "calculate"] and seen["definitions"]
    # The data-analysis finish traced every figure to what the code printed; the one it did
    # not print stays as written, marked for checking.
    assert bundle.document.answer == "방학 월평균 26,300명, 학기 월평균 99,999명(확인 필요)."
    assert any(e["id"].startswith("compose:1:agent:") for e in events)
    computed = [p for p in bundle.material.parts if p.origin == "computed"]
    assert computed and "26300" in computed[0].text
    assert bundle.usage["inputTokens"] == 40


def _product_bundle(answer: str) -> Bundle:
    from app.pipeline.bundle import Part

    bundle = Bundle(request=Request(text="방학 비교", model="m", api_key="k",
                                    untrusted=["월,방문자\n2025-07,28400\n2025-08,25900"]))
    bundle.material.parts = [
        Part("# 앞 단계 결과: 데이터 분석\n방학 월평균 "
             "방문자 30,000명", "derived", "handoff:1:agent"),
        Part("# 코드 실행 결과: 데이터 분석\n방학 월평균 26300", "computed", "tool:1:agent")]
    bundle.document.answer = answer
    bundle.wire["product"] = {"stage": "2:agent", "kind": "answer", "title": "정리"}
    return bundle


async def test_a_check_downstream_does_not_take_a_figure_from_an_earlier_step():
    bundle = _product_bundle("방학 월평균 26,300명. 앞 단계 기준으로는 30,000명.")
    step = compose.CheckStep(2, {"kind": "check", "ref": "grounding-check", "settings": {}},
                             "근거 대조")
    [e async for e in step.run(bundle, Context())]
    assert "26,300명." in bundle.document.answer
    # the earlier step's figure is not evidence: marked, not taken as supported
    assert "30,000명(확인 필요)" in bundle.document.answer
    args = compose._sub_args(bundle, _info("cmp"), "비교 보고서")
    assert args["derived_context"] == ["# 앞 단계 결과: 데이터 분석\n방학 월평균 방문자 30,000명"]
    assert args["tool_outputs"] == ["# 코드 실행 결과: 데이터 분석\n방학 월평균 26300"]
    assert not any("30,000" in t for t in args["untrusted_context"])


async def test_the_handoff_is_derived_material():
    bundle = Bundle(request=Request(text="x", model="m", api_key="k"))
    bundle.document.answer = "방학 월평균 30,000명"
    bundle.wire["product"] = {"stage": "1:agent", "kind": "answer", "title": "데이터 분석"}
    compose._handoff(bundle)
    assert [(p.origin, p.label) for p in bundle.material.parts] == [("derived", "handoff:1:agent")]
    assert bundle.material.grounds == [] and len(bundle.material.document) == 1


async def test_a_report_settles_figures_from_files_and_code_not_from_an_earlier_step(monkeypatch):
    from app.pipeline import report_stages

    given: list[str] = []

    async def settle_all(text, complete, model, api_key):
        given.append(text)
        return [], [], {}

    monkeypatch.setattr(report_stages.key_figures, "settle_all", settle_all)
    bundle = report_stages.bundle_for(
        request="비교 보고서", model="m", api_key="k", untrusted_context=["원자료 28400"],
        derived=["# 앞 단계 결과\n30,000명"], tool_outputs=["코드 출력 26300"])
    await registry.stage("settle").apply(bundle, Context())
    assert "28400" in given[0] and "26300" in given[0] and "30,000" not in given[0]


def test_a_chat_agent_step_with_no_check_after_it_is_a_warning():
    verdict = compose.validate(_steps(("agent", "data"), ("agent", "cmp")), agents=_AGENTS)
    assert verdict.ok and [(w.step, w.code) for w in verdict.warnings] == [
        (1, "answer_unchecked")]
    assert "검사나 검토" in verdict.warnings[0].message
    checked = compose.validate(_steps(("agent", "data"), ("review", ""), ("agent", "cmp")),
                               agents=_AGENTS)
    assert checked.warnings == []


def test_usage_counts_keep_the_planner_and_reviewer_counters():
    assert compose.usage_counts({"inputTokens": 5, "outlineInputTokens": 7,
                                 "model:j:outputTokens": 2, "credits": 9, "note": "x"}) == {
        "inputTokens": 5, "outputTokens": 0, "outlineInputTokens": 7, "model:j:outputTokens": 2}
