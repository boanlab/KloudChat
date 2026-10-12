"""Pipeline runs kept across requests: the bundle after each stage, a rerun from a stage, the
gap card as a stage the run waits at, the marks a document carries, and the read API."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, Message, PipelineRun, Role, SessionKind
from app.models.user import User
from app.models.workspace import Agent, Artifact, ArtifactVersion
from app.pipeline import marks, registry, runs
from app.pipeline.bundle import Bundle, Part, Request
from app.pipeline.check_stages import GapCard
from app.pipeline.runner import Runner
from app.pipeline.stage import Base, Context, StepRecord, Wait
from app.routers import pipelines as pipelines_router
from app.routers import runs as runs_router
from app.routers import sessions
from app.services import doc_formats, key_figures, process_settings, revise

# ── encoding and restoring the bundle ──────────────────────────────────


def _bundle(**turn) -> Bundle:
    return Bundle(request=Request(text="요청", model="m", api_key="secret", turn=dict(turn)))


def test_a_bundle_value_round_trips_through_its_encoding():
    fmt = next(f for f in doc_formats.FORMATS if f.id == "minutes")
    value = {
        "format": fmt,
        "figure": key_figures.Figure(name="예산", value="9억 원"),
        "plan": revise.Plan(scope="parts", targets=[1, 2], note="고쳐"),
        "tuple": (1, "a"),
        "day": dt.date(2026, 10, 8),
        "__odd": {1: "x"},
        "callable": print,
    }
    back = runs.decode(json.loads(json.dumps(runs.encode(value))))
    assert back["format"] is fmt
    assert back["figure"] == value["figure"] and back["plan"] == value["plan"]
    assert back["tuple"] == (1, "a") and back["day"] == dt.date(2026, 10, 8)
    assert back["__odd"] == {1: "x"}
    # What cannot be kept comes back empty, never as something else.
    assert back["callable"] is None


def test_the_request_is_kept_without_its_key():
    params = runs.request_params(_bundle())
    assert "api_key" not in params["f"]
    restored = runs.restore(params, {"start": {}, "after": [{"stage": "a", "fields": {}}]},
                            before="a", api_key="fresh")
    assert restored.request.api_key == "fresh" and restored.request.text == "요청"


class _Count(Base):
    """A stage that writes one section body, counting its runs."""

    kind = "model"
    writes = ("document.sections",)

    def __init__(self, name: str, text: str) -> None:
        self.name, self.text, self.runs = name, text, 0

    async def apply(self, bundle, ctx):
        self.runs += 1
        body = (bundle.document.sections[0]["content"] + " ") if bundle.document.sections else ""
        bundle.document.sections = [{"id": "s1", "heading": "현황",
                                     "content": f"{body}{self.text}{self.runs}"}]


async def _drain(runner, bundle, **kwargs):
    return [e async for e in runner.run(bundle, Context(), **kwargs)]


async def test_a_recorded_run_restores_the_bundle_before_any_stage():
    a, b, c = _Count("t-a", "A"), _Count("t-b", "B"), _Count("t-c", "C")
    bundle, recorder = _bundle(), runs.Recorder("r1")
    recorder.begin(bundle)
    await _drain(Runner([a, b, c], recorder=recorder), bundle)
    snaps = json.loads(json.dumps(recorder.snapshots()))
    assert runs.rerunnable_from(snaps) == ["t-a", "t-b", "t-c"]
    before_c = runs.restore(recorder.request, snaps, before="t-c", api_key="k")
    assert before_c.document.sections[0]["content"] == "A1 B1"
    before_a = runs.restore(recorder.request, snaps, before="t-a", api_key="k")
    assert before_a.document.sections == []
    # The records say what each stage changed, old → new.
    record = bundle.steps[1]
    assert record.evidence["changes"] == [{"sectionId": "s1", "heading": "현황", "old": "",
                                           "new": "B1"}]
    assert record.rerunnable and record.run_id == "r1" and record.started_at


async def test_a_rerun_runs_the_stage_and_those_after_it_only():
    a, b, c = _Count("t-a", "A"), _Count("t-b", "B"), _Count("t-c", "C")
    first, recorder = _bundle(), runs.Recorder("r1")
    recorder.begin(first)
    await _drain(Runner([a, b, c], recorder=recorder), first)
    snaps = recorder.snapshots()
    again = runs.restore(recorder.request, snaps, before="t-b", api_key="k")
    second = runs.Recorder("r2")
    second.begin(again, carried=runs.carried(snaps, before="t-b"))
    runner = Runner([a, b, c], recorder=second)
    runner.attempts = {"t-a": 1, "t-b": 2, "t-c": 2}
    await _drain(runner, again, start="t-b")
    # The stage before is not run again; its body stands as it was.
    assert (a.runs, b.runs, c.runs) == (1, 2, 2)
    assert again.document.sections[0]["content"] == "A1 B2 C2"
    assert [r.attempt for r in again.steps if r.status == "done"] == [2, 2]
    # The rerun can itself be rerun from any stage, the carried ones included.
    assert runs.rerunnable_from(second.snapshots()) == ["t-a", "t-b", "t-c"]


async def test_records_are_sent_in_the_shape_the_surface_reads():
    bundle = _bundle()
    events = await _drain(Runner([_Count("t-a", "A")], emit_records=True), bundle)
    shown = events[-1]
    assert (shown["type"], shown["category"], shown["id"], shown["status"]) == (
        "step", "pipeline", "stage:t-a", "done")
    stage = shown["stage"]
    assert {"id", "runId", "name", "label", "kind", "status", "reason", "summary", "startedAt",
            "finishedAt", "ms", "attempt", "rerunnable"} <= set(stage)
    assert stage["status"] == "done" and stage["attempt"] == 1


# ── the gap card waits ─────────────────────────────────────────────────


_GAP = {"kind": "gap", "fact": "meeting_date", "label": "회의 일시",
        "question": "회의 일시가 자료에 없습니다. 언제였나요?", "why": "회의록 머리에 적힙니다.",
        "placeholder": "[회의 일시]"}


async def test_the_gap_card_is_a_stage_the_run_waits_at():
    bundle = _bundle(card={"plans_first": True, "may_ask": True, "files": [], "asked": []})
    bundle.findings.gaps = [_GAP]
    events = await _drain(Runner([GapCard()], emit_records=True), bundle)
    record = bundle.steps[0]
    assert record.status == "waiting" and record.summary == "질문 1개"
    question = record.evidence["questions"][0]
    assert {k: question[k] for k in ("id", "fact", "label", "why", "placeholder", "freeText")} == {
        "id": "gap:meeting_date", "fact": "meeting_date", "label": "회의 일시",
        "why": "회의록 머리에 적힙니다.", "placeholder": "[회의 일시]", "freeText": True}
    assert events[-1]["stage"]["status"] == "waiting"
    assert events[-1]["label"].endswith("답 기다림")


@pytest.mark.parametrize(("card", "gaps", "reason"), [
    (None, [_GAP], "no card on this turn"),
    ({"may_ask": False, "plans_first": True}, [_GAP], "a reply to the card already up"),
    ({"may_ask": True, "plans_first": False}, [], "nothing missing"),
    ({"may_ask": True, "plans_first": True, "asked": ["gap:meeting_date"]}, [_GAP],
     "nothing left to ask"),
])
def test_the_gap_card_says_why_it_asks_nothing(card, gaps, reason):
    bundle = _bundle(**({"card": card} if card is not None else {}))
    bundle.findings.gaps = gaps
    assert GapCard().skip(bundle) == reason


# ── marks ──────────────────────────────────────────────────────────────


def test_marks_name_blanks_replaced_values_and_what_the_check_left():
    bundle = _bundle()
    bundle.findings.gaps = [_GAP]
    bundle.steps = [StepRecord("check:grounding", "check", "done", evidence={"changes": [
        {"sectionId": "s1", "heading": "현황", "old": "1억 3천만 원", "new": "[금액]"}]})]
    bundle.findings.still = [SimpleNamespace(section=0, sentence="고장은 매주 늘었다.",
                                             code="unsupported", why="자료에 없는 추세")]
    sections = [{"id": "s1", "heading": "현황",
                 "content": "회의는 [회의 일시]에 열렸다. 교체 비용은 [금액]이다. "
                            "고장은 매주 늘었다. [1]"}]
    got = marks.for_sections(bundle, sections)
    spans = got[0]["spans"]
    assert [(s["kind"], s["text"]) for s in spans] == [
        ("placeholder", "[회의 일시]"), ("ungrounded-replaced", "[금액]"),
        ("check-finding", "고장은 매주 늘었다.")]
    content = sections[0]["content"]
    assert all(content[s["start"]:s["end"]] == s["text"] for s in spans)
    assert spans[0]["reason"] == "자료에 없는 정보: 회의 일시"
    assert spans[1]["original"] == "1억 3천만 원" and spans[1]["stage"] == "check:grounding"
    # Found again after an edit; a span whose text is gone is dropped.
    edited = [{"id": "s1", "heading": "현황",
               "content": "교체 비용은 [금액]이다. 새 문장 [담당자]."}]
    again = marks.relocate(got, edited)[0]["spans"]
    assert [(s["kind"], s["text"], s["start"]) for s in again] == [
        ("ungrounded-replaced", "[금액]", 7), ("placeholder", "[담당자]", 20)]


# ── the definitions ────────────────────────────────────────────────────


def test_a_pipeline_reads_with_its_stages_labels_and_checks():
    got = pipelines_router.describe(registry.pipeline("report"), ("gap-check",
                                                                   "grounding-check"),
                                    process_settings.for_format("minutes"))
    write = {row["name"]: row for row in got["flows"]["write"]}
    assert write["verify"]["label"] == "검증" and write["verify"]["kind"] == "check"
    assert write["verify"]["checks"] == ["lead-dates", "minutes-rows", "material-blanks",
                                         "judge-owners"]
    assert write["check:grounding"]["checks"] == ["grounding-check"]
    assert got["flows"]["pre"][0]["checks"] == ["gap-check"]
    assert [r["name"] for r in got["flows"]["pre"]] == ["check:gaps", "gap-card"]
    assert write["report-save"]["kind"] == "io"
    assert got["lists"]["prepass"] == ["date-calc"]


def test_an_agent_reads_the_pipeline_its_turns_run():
    # Every document is written by `report`; what differs is its 진행 과정 설정.
    assert pipelines_router.pipeline_for_agent("minutes", None, None).name == "report"
    assert pipelines_router.pipeline_for_agent(None, "gaejo", "report").name == "report"
    assert pipelines_router.pipeline_for_agent("data-analysis", None, None).name == "chat"
    assert pipelines_router.pipeline_for_agent(None, None, "report").name == "report"
    assert pipelines_router.pipeline_for_agent(None, None, "chat").name == "chat"


def test_every_stage_has_a_korean_label():
    from app.pipeline.labels import LABELS

    assert set(registry.stages()) <= set(LABELS)


# ── the migration ──────────────────────────────────────────────────────


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0053_pipeline_runs.py"
    spec = importlib.util.spec_from_file_location("pipeline_runs_0053", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_adds_and_drops_the_runs_table_on_sqlite():
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0053", "0052")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id VARCHAR PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE sessions (id VARCHAR PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        columns = {c["name"] for c in sa.inspect(conn).get_columns("pipeline_runs")}
        assert {c.name for c in PipelineRun.__table__.columns} == columns
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "pipeline_runs" not in sa.inspect(conn).get_table_names()


# ── a rerun through the endpoint ───────────────────────────────────────


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
        User, ChatSession, Message, Artifact, ArtifactVersion, PipelineRun, Agent)]
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=tables)
    try:
        yield engine
    finally:
        await engine.dispose()


_MODEL = {"id": "strict-local/writer", "label": "w", "kinds": ["report"], "creditCost": 1,
          "dataBoundary": "self_hosted"}


@pytest.fixture
def wired(monkeypatch, engine):
    settled: list[dict] = []

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    async def catalogue():
        return {"models": [_MODEL]}

    async def nothing(*_a, **_k):
        return None

    async def credentials(_user):
        return "u", "fresh-key"

    async def policy():
        return SimpleNamespace(intent_filter=False, pii_masking=False, blocked_categories=[])

    monkeypatch.setattr(sessions, "SessionLocal", local)
    monkeypatch.setattr(sessions, "settle", lambda *_a, **k: settled.append(k))
    monkeypatch.setattr(sessions, "record_searches", lambda *_a, **_k: None)
    monkeypatch.setattr(runs_router.model_service, "list_models_for_egress", catalogue)
    monkeypatch.setattr(runs_router.litellm_service, "ensure_key", nothing)
    monkeypatch.setattr(runs_router.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(runs_router, "has_headroom", lambda *_a: True)
    monkeypatch.setattr(sessions, "_require_egress_policy", policy)
    monkeypatch.setenv("KLOUDCHAT_PIPELINE_STEPS", "1")
    return SimpleNamespace(local=local, settled=settled)


class _Answers(Base):
    """Writes the answers it was given into the body, so a rerun's answers show."""

    name = "t-answers"
    reads = ("material.parts",)
    writes = ("document.sections",)

    async def apply(self, bundle, ctx):
        given = [p.text for p in bundle.material.parts if p.origin == "answer"]
        if given:
            bundle.document.sections = [{**bundle.document.sections[0], "content":
                                         bundle.document.sections[0]["content"] + " | "
                                         + given[0].splitlines()[-1]}]


def _events(chunks: list[str]) -> list[dict]:
    return [json.loads(line[6:]) for chunk in chunks for line in chunk.splitlines()
            if line.startswith("data: ")]


async def test_a_run_is_rerun_from_a_stage_as_a_new_version(monkeypatch, wired):
    a, b = _Count("t-a", "A"), _Count("t-b", "B")
    for stage in (a, b, _Answers()):
        monkeypatch.setitem(registry._STAGES, stage.name, stage)
    pipe = registry.Pipeline(name="fake", flows={"write": ("t-a", "t-b", "t-answers",
                                                           "report-save")})
    monkeypatch.setattr(runs_router.pipeline_registry, "pipeline", lambda _name: pipe)

    async with wired.local() as db:
        user = User(email="w@example.com", name="w", password_hash="x")
        db.add(user)
        await db.commit()
        session = ChatSession(user_id=user.id, kind=SessionKind.report, title="보고")
        db.add(session)
        await db.commit()

    turn = sessions._report_turn(user_id=user.id, model=_MODEL, outline_model=None,
                                 project_id=None, routing=None, skills_event=None,
                                 context_steps=None, carry_from=None, carry_note="")
    work = Bundle(request=Request(text="보고서", model=_MODEL["id"], api_key="k",
                                  approved_plan={"title": "보고"}, session_id=session.id,
                                  turn=turn))
    work.usage = {"inputTokens": 10, "outputTokens": 5}
    recorder = runs.Recorder("first")
    recorder.pipeline, recorder.flow = "fake", "write"
    first = _events([c async for c in runs_router._write_pass(
        work, pipe, recorder, session_id=session.id, user_id=user.id, start=None,
        carried=None, attempts={}, parent=None)])
    artifact_id = next(e["artifactId"] for e in first if e["type"] == "artifact")

    async with wired.local() as db:
        run = await db.get(PipelineRun, "first")
        assert run.status == "done" and run.artifact_id == artifact_id
        assert [r["stage"] for r in run.stages] == ["t-a", "t-b", "t-answers", "report-save"]
        message = await db.get(Message, run.message_id)
        assert message.routing["run"]["id"] == "first"
        assert [s["name"] for s in message.routing["run"]["stages"]][-1] == "report-save"
        user = await db.get(User, user.id)
        response = await runs_router.rerun(
            session.id, "first", runs_router.RerunIn(from_stage="t-b",
                                                     answers={"gap:meeting_date": "10월 7일"}),
            user, db)
    events = _events([c async for c in response.body_iterator])

    # The stage before is not run again; it and its body stand as they were.
    assert (a.runs, b.runs) == (1, 2)
    assert events[0]["type"] == "run" and events[0]["parentId"] == "first"
    assert events[0]["fromStage"] == "t-b"
    shown = [e["stage"]["name"] for e in events if e.get("category") == "pipeline"]
    assert shown == ["t-b", "t-answers", "report-save"]
    assert [e["type"] for e in events][-3:] == ["artifact", "usage", "done"]
    assert events[-2]["inputTokens"] == 0  # only this pass's tokens are billed

    async with wired.local() as db:
        artifact = await db.get(Artifact, artifact_id)
        assert artifact.version == 2
        assert artifact.data["sections"][0]["content"] == "A1 B2 | - 10월 7일"
        old = (await db.exec(select(ArtifactVersion).where(
            ArtifactVersion.artifact_id == artifact_id))).all()
        assert [v.data["sections"][0]["content"] for v in old] == ["A1 B1"]
        rerun = (await db.exec(select(PipelineRun).where(PipelineRun.parent_id == "first"))).one()
        assert rerun.from_stage == "t-b" and rerun.artifact_version == 2
        stages = {r["stage"]: r for r in rerun.stages}
        assert stages["t-a"]["attempt"] == 1 and stages["t-a"]["status"] == "done"
        assert stages["t-b"]["attempt"] == 2
        said = (await db.exec(select(Message).where(Message.session_id == session.id,
                                                    Message.role == Role.assistant)
                              .order_by(Message.created_at))).all()
        assert said[-1].content == "「t-b」 단계부터 다시 만들었습니다."
    assert [s["reason"] for s in wired.settled] == ["report.generate", "report.generate"]

    async with wired.local() as db:
        got = await runs_router.get_run(session.id, rerun.id, user, db)
        assert got["parentId"] == "first" and got["stages"][1]["rerunnable"]
        listed = await runs_router.list_runs(session.id, user, db)
        assert [r["id"] for r in listed] == [rerun.id, "first"]
        assert "evidence" not in listed[0]["stages"][1]


async def test_a_rerun_is_refused_for_someone_else_or_a_stage_never_recorded(monkeypatch, wired):
    from fastapi import HTTPException

    async with wired.local() as db:
        owner, other = (User(email="o@example.com", name="o", password_hash="x"),
                        User(email="x@example.com", name="x", password_hash="x"))
        db.add_all([owner, other])
        await db.commit()
        session = ChatSession(user_id=owner.id, kind=SessionKind.report, artifact_id="now")
        db.add(session)
        await db.commit()
        db.add(PipelineRun(id="old", session_id=session.id, user_id=owner.id, artifact_id="was",
                           pipeline="report", flow="write", status="done",
                           params={"request": {"f": {}}}, stages=[],
                           snapshots={"start": {}, "after": [{"stage": "draft", "fields": {}}]}))
        db.add(PipelineRun(id="r", session_id=session.id, user_id=owner.id, pipeline="report",
                           flow="write", status="done", params={"request": {"f": {}}},
                           stages=[], snapshots={"start": {}, "after": [{"stage": "draft",
                                                                         "fields": {}}]}))
        db.add(PipelineRun(id="pre", session_id=session.id, user_id=owner.id, pipeline="report",
                           flow="pre", status="waiting", stages=[], snapshots={}))
        await db.commit()
        cases = [(other, "r", "draft", 404), (owner, "r", "verify", 409),
                 (owner, "pre", "gap-card", 409), (owner, "nope", "draft", 404),
                 (owner, "old", "draft", 409)]
        for who, run_id, stage, code in cases:
            with pytest.raises(HTTPException) as caught:
                await runs_router.rerun(session.id, run_id, runs_router.RerunIn(from_stage=stage),
                                        who, db)
            assert caught.value.status_code == code


# ── the card's run, waiting and answered ───────────────────────────────


async def test_the_card_keeps_a_waiting_run_closed_by_the_answer(wired):
    async with wired.local() as db:
        user = User(email="c@example.com", name="c", password_hash="x")
        db.add(user)
        await db.commit()
        session = ChatSession(user_id=user.id, kind=SessionKind.report)
        db.add(session)
        await db.commit()
        pre = _bundle(card={"plans_first": True, "may_ask": True, "files": [], "asked": []},
                      pipeline="minutes")
        pre.findings.gaps = [_GAP]
        await _drain(Runner([GapCard()], emit_records=False), pre)
        response = await sessions._ask_before_writing(
            db, session, request="회의록", typed="회의록 써 줘", attachments=[],
            questions=pre.findings.questions, pre=pre)
    body = json.loads(response.body)
    run_id = body["runId"]
    assert body["pending"]["runId"] == run_id and body["stage"]["status"] == "waiting"
    assert body["pending"]["questions"][0]["placeholder"] == "[회의 일시]"
    async with wired.local() as db:
        run = await db.get(PipelineRun, run_id)
        assert (run.status, run.flow, run.pipeline) == ("waiting", "pre", "minutes")
        said = (await db.exec(select(Message).where(Message.role == Role.assistant))).one()
        assert said.routing["run"]["stages"][0]["status"] == "waiting"
    await sessions._close_waiting_run(run_id, answers={}, as_is=True, blanks=1)
    async with wired.local() as db:
        run = await db.get(PipelineRun, run_id)
        assert run.status == "done"
        assert run.stages[0]["summary"] == "있는 자료로 진행 · 빈칸 1개"
        # The card's message says so too: a reloaded transcript does not show it still waiting.
        assert run.message_id == said.id
        said = await db.get(Message, said.id)
        assert said.routing["run"]["status"] == "done"
        assert said.routing["run"]["stages"][0]["status"] == "done"
        assert said.routing["run"]["stages"][0]["summary"] == "있는 자료로 진행 · 빈칸 1개"


def test_answers_join_the_material_by_the_card_labels():
    part = runs_router.answers_part({"gap:meeting_date": "10월 7일", "x": ""}, [_GAP])
    assert isinstance(part, Part) and part.origin == "answer"
    assert part.text == "# 확인 질문에 대한 답\n- 회의 일시: 10월 7일"


def test_wait_is_a_stop_recorded_as_waiting():
    assert issubclass(Wait, Exception) and Wait("x").status == "waiting"
