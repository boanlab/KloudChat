"""지식 서가: per-conversation source switches reach retrieval, guides and studio outputs are
grounded in the picked sources and cite only them, quizzes are checked before they are kept,
speech never leaves where internal data must stay inside, and notes become sources."""

from __future__ import annotations

import csv
import io
import json
import random
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import sqlite
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import db as core_db
from app.core.config import settings
from app.core.db import get_session
from app.core.deps import current_user
from app.models.chat import ChatSession
from app.models.shelf import ProjectNote, SourceGuide, SourceSelection, StudioItem
from app.models.user import CreditLedger, User
from app.models.workspace import Artifact, ArtifactVersion, Project, StoredFile
from app.routers import shelf as shelf_router
from app.services import (
    shelf_calls,
    shelf_jobs,
    shelf_sources,
    source_selection,
    studio,
    studio_kinds,
    workspace_context,
)


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw) -> str:
    return "JSON"


STRICT = {"id": "strict-local/qwen3.8-27b", "label": "27B", "kinds": ["chat"],
          "dataBoundary": "self_hosted", "strictLocal": True, "creditCost": 0,
          "inputCreditCost": 0, "contextWindow": 131072}
OUTSIDE = {"id": "openai/gpt-x", "label": "GPT", "kinds": ["chat"], "dataBoundary": "external",
           "strictLocal": False, "creditCost": 2, "inputCreditCost": 1}
SPEECH = {"id": "openai/gpt-audio", "kinds": ["av"], "dataBoundary": "external",
          "strictLocal": False, "creditCost": 3, "inputCreditCost": 1}

LECTURE = (
    "# 3강 논리 시계\n\n[페이지 1]\n분산 시스템에는 전역 시계가 없다. "
    "램포트 시계는 사건마다 정수를 붙여 순서를 정한다. "
    "같은 프로세스에서 뒤의 사건은 더 큰 값을 받는다.\n\n[페이지 2]\n"
    "벡터 시계는 프로세스 수만큼의 칸을 가진다. 벡터 시계로는 두 사건이 동시에 일어났는지 "
    "판단할 수 있다. 램포트 시계 값의 대소만으로는 인과 관계를 알 수 없다.\n"
)
VIDEO = ("[00:00:05]\n(말) 오늘은 합의 알고리즘을 다룹니다.\n[00:02:10]\n(말) 래프트는 리더를 "
         "선출하고 로그를 복제합니다.\n(화면) 래프트 상태도\n")


def _users_ddl() -> str:
    dialect = sqlite.dialect()
    columns = ", ".join(
        f"{c.name} {c.type.compile(dialect=dialect)}" + (" PRIMARY KEY" if c.primary_key else "")
        for c in User.__table__.columns)
    return f"CREATE TABLE users ({columns})"


@pytest.fixture
async def shelf(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    tables = [m.__table__ for m in (CreditLedger, Project, StoredFile, ChatSession,
                                    Artifact, ArtifactVersion, SourceSelection, SourceGuide,
                                    ProjectNote, StudioItem)]
    async with engine.begin() as connection:
        # the users table carries Postgres-only defaults: its columns are made plainly
        await connection.exec_driver_sql(_users_ddl())
        await connection.run_sync(lambda c: SQLModel.metadata.create_all(c, tables=tables))
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(core_db, "SessionLocal", maker)
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    me = User(id="me", email="me@example.test", password_hash="unused", name="Me",
              monthly_credits=1000)
    async with maker() as db:
        db.add(me)
        project = Project(user_id=me.id, name="분산 시스템")
        other = Project(user_id="other", name="남의 것")
        db.add_all([project, other])
        await db.flush()
        lecture = StoredFile(user_id=me.id, project_id=project.id, name="03강 논리 시계.pdf",
                             mime="application/pdf", text=LECTURE, tokens=200)
        video = StoredFile(user_id=me.id, project_id=project.id, name="래프트 강의.mp4",
                           mime="video/mp4", text=VIDEO, tokens=60)
        session = ChatSession(user_id=me.id, project_id=project.id, title="질문")
        db.add_all([lecture, video, session])
        await db.commit()
        ids = SimpleNamespace(project=project.id, other=other.id, lecture=lecture.id,
                              video=video.id, session=session.id)

    rules = SimpleNamespace(internal_data_strict_local=True, external_data_guard=True,
                            pii_masking=False)
    catalogue = {"models": [STRICT, OUTSIDE, SPEECH], "defaultChatModel": OUTSIDE["id"]}
    calls: list[dict] = []
    answers: list[str] = []

    async def policy():
        return rules

    async def models_for_egress():
        return catalogue

    async def complete(user, model, system, prompt, max_tokens):
        calls.append({"model": model["id"], "system": system, "prompt": prompt})
        return answers.pop(0), {"inputTokens": 1000, "outputTokens": 200}

    monkeypatch.setattr(shelf_calls, "policy", policy)
    monkeypatch.setattr(shelf_calls.model_service, "list_models_for_egress", models_for_egress)
    monkeypatch.setattr(studio.model_service, "list_models_for_egress", models_for_egress)
    monkeypatch.setattr(shelf_calls, "complete", complete)

    async def no_index(*args, **kwargs):
        return False

    from app.routers import workspace as workspace_router

    monkeypatch.setattr(workspace_router, "_index_document", no_index)

    app = FastAPI()
    app.include_router(shelf_router.router)

    async def session_dep():
        async with maker() as db:
            yield db

    app.dependency_overrides[get_session] = session_dep
    app.dependency_overrides[current_user] = lambda: me
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, ids=ids, maker=maker, rules=rules, calls=calls,
                              answers=answers, catalogue=catalogue, me=me)
    await shelf_jobs.drain()


# ── per-conversation switches ─────────────────────────────────────────


async def test_switched_off_sources_leave_the_context_and_the_search_shelf(shelf):
    ids = shelf.ids
    stored = await shelf.client.put(f"/sessions/{ids.session}/sources",
                                    json={"off": [ids.video, "not-a-project-file"]})
    assert stored.status_code == 200, stored.text
    # an id that is not this project's source is not kept
    assert stored.json() == {"off": [ids.video]}
    assert (await shelf.client.get(f"/sessions/{ids.session}/sources")).json() == {
        "off": [ids.video]}

    async with shelf.maker() as db:
        project = await db.get(Project, ids.project)
        user = await db.get(User, "me")
        off = await source_selection.excluded(db, ids.session)
        knowledge, used = await workspace_context._project_knowledge(
            db, user, project, "래프트", 50_000, off=off)
        assert "논리 시계" in knowledge and "래프트는 리더" not in knowledge
        assert [f.id for f in used] == [ids.lecture]

        from app.routers import sessions as sessions_router

        session = await db.get(ChatSession, ids.session)
        rows, _ = await sessions_router._knowledge_shelf(db, user, session, None)
        assert [name for name, _, _ in rows] == ["03강 논리 시계.pdf"]

    # back to all on
    assert (await shelf.client.put(f"/sessions/{ids.session}/sources",
                                   json={"off": []})).json() == {"off": []}


async def test_a_conversation_outside_a_project_has_no_switches(shelf):
    async with shelf.maker() as db:
        loose = ChatSession(user_id="me", title="밖")
        db.add(loose)
        await db.commit()
        loose_id = loose.id
    refused = await shelf.client.put(f"/sessions/{loose_id}/sources", json={"off": []})
    assert refused.status_code == 409


# ── sources, guides, the viewer ───────────────────────────────────────


async def test_the_shelf_lists_each_source_with_its_kind_and_pasted_text_joins_it(shelf):
    added = await shelf.client.post(f"/projects/{shelf.ids.project}/shelf/text",
                                    json={"title": "회의 메모",
                                          "text": "합의는 다음 주에 다시 논의한다."})
    assert added.status_code == 201, added.text
    listing = (await shelf.client.get(f"/projects/{shelf.ids.project}/shelf")).json()
    kinds = {s["name"]: s["kind"] for s in listing["sources"]}
    assert kinds == {"03강 논리 시계.pdf": "file", "래프트 강의.mp4": "video",
                     "회의 메모.txt": "text"}
    assert listing["strictLocal"] is True
    # someone else's project is not found
    assert (await shelf.client.get(f"/projects/{shelf.ids.other}/shelf")).status_code == 404


async def test_a_guide_is_written_by_the_strict_local_model_and_kept(shelf):
    shelf.answers.append(json.dumps({
        "summary": "논리 시계로 사건 순서를 정하는 법.", "topics": ["램포트 시계", "벡터 시계"],
        "questions": ["벡터 시계로 동시성을 어떻게 판단하나요?", "짧음"]}, ensure_ascii=False))
    url = f"/projects/{shelf.ids.project}/shelf/guide/{shelf.ids.lecture}"
    assert (await shelf.client.get(url)).json()["state"] == "none"
    started = await shelf.client.post(url)
    assert started.json()["state"] == "running"
    await shelf_jobs.drain()
    guide = (await shelf.client.get(url)).json()
    assert guide["state"] == "done"
    assert guide["topics"] == ["램포트 시계", "벡터 시계"]
    # a one-word "question" is dropped
    assert guide["questions"] == ["벡터 시계로 동시성을 어떻게 판단하나요?"]
    assert shelf.calls[0]["model"] == STRICT["id"]
    # the material is fenced as data
    assert "<material>" in shelf.calls[0]["prompt"]
    # asked again: the kept guide, no new call
    await shelf.client.post(url)
    await shelf_jobs.drain()
    assert len(shelf.calls) == 1
    async with shelf.maker() as db:
        ledger = (await db.exec(select(CreditLedger))).all()
    assert [row.reason for row in ledger] == ["knowledge.guide"]


async def test_the_viewer_finds_the_cited_passage_its_page_and_time(shelf):
    base = f"/projects/{shelf.ids.project}/shelf/locate"
    found = (await shelf.client.get(base, params={
        "name": "03강 논리 시계.pdf",
        "q": "벡터 시계로는 동시에 일어났는지 판단할 수 있다 [1]"})).json()
    assert found["fileId"] == shelf.ids.lecture and found["page"] == 2 and found["matched"]
    focus = LECTURE[found["focusStart"]:found["focusEnd"]]
    assert "동시에 일어났는지" in focus
    clip = (await shelf.client.get(base, params={"file_id": shelf.ids.video,
                                                 "q": "래프트는 리더를 선출"})).json()
    assert clip["kind"] == "video" and clip["seconds"] == 130.0


# ── studio ────────────────────────────────────────────────────────────


async def test_a_study_guide_cites_only_listed_sources_and_opens_as_a_report(shelf):
    shelf.answers.append(json.dumps({"title": "논리 시계 학습 가이드", "sections": [
        {"heading": "개요",
         "content": "전역 시계가 없다 [1]. 래프트는 리더를 뽑는다 [2]. 지어낸 것 [7]."},
        {"heading": "빈 절", "content": ""}]}, ensure_ascii=False))
    started = await shelf.client.post(f"/projects/{shelf.ids.project}/studio", json={
        "kind": "study_guide", "sourceIds": [shelf.ids.lecture, shelf.ids.video],
        "options": {"format": "web"}})
    assert started.status_code == 202, started.text
    await shelf_jobs.drain()
    item = (await shelf.client.get(f"/studio/{started.json()['id']}")).json()
    assert item["state"] == "done", item
    sections = item["content"]["sections"]
    assert len(sections) == 1
    assert "[7]" not in sections[0]["content"] and "[2]" in sections[0]["content"]
    async with shelf.maker() as db:
        artifact = await db.get(Artifact, item["artifactId"])
        session = await db.get(ChatSession, item["sessionId"])
    assert artifact.kind.value == "report" and artifact.project_id == shelf.ids.project
    assert session.artifact_id == artifact.id and session.project_id == shelf.ids.project
    assert [s["title"] for s in artifact.data["sources"]] == [
        "03강 논리 시계.pdf", "래프트 강의.mp4"]
    # strict-local by default, sources numbered as the prompt lists them
    assert shelf.calls[0]["model"] == STRICT["id"]
    assert "[1] 03강 논리 시계.pdf" in shelf.calls[0]["prompt"]


async def test_only_picked_sources_are_read(shelf):
    shelf.answers.append(json.dumps({"title": "용어", "terms": [
        {"term": "램포트 시계", "definition": "사건에 정수를 붙인다 [1]."}]}, ensure_ascii=False))
    started = await shelf.client.post(f"/projects/{shelf.ids.project}/studio", json={
        "kind": "glossary", "sourceIds": [shelf.ids.lecture]})
    await shelf_jobs.drain()
    assert "래프트" not in shelf.calls[0]["prompt"]
    item = (await shelf.client.get(f"/studio/{started.json()['id']}")).json()
    assert "| **램포트 시계** |" in item["content"]["sections"][0]["content"]


async def test_an_outside_model_is_refused_while_internal_data_stays_inside(shelf):
    refused = await shelf.client.post(f"/projects/{shelf.ids.project}/studio/estimate", json={
        "kind": "faq", "options": {"model": OUTSIDE["id"]}})
    assert refused.status_code == 409
    assert refused.json()["detail"] == "internal_data_requires_strict_local"
    shelf.rules.internal_data_strict_local = False
    allowed = await shelf.client.post(f"/projects/{shelf.ids.project}/studio/estimate", json={
        "kind": "faq", "options": {"model": OUTSIDE["id"]}})
    body = allowed.json()
    assert allowed.status_code == 200 and body["strict"] is False and body["credits"] > 0
    assert body["sources"] == 2 and body["inputTokens"] > 0


async def test_an_unreadable_answer_is_asked_again_then_fails(shelf):
    shelf.answers.extend(["말로만 답함", "또 말로만"])
    started = await shelf.client.post(f"/projects/{shelf.ids.project}/studio",
                                      json={"kind": "faq"})
    await shelf_jobs.drain()
    item = (await shelf.client.get(f"/studio/{started.json()['id']}")).json()
    assert item["state"] == "failed" and len(shelf.calls) == 2


async def test_a_quiz_keeps_only_sound_questions_and_exports(shelf):
    good = {"type": "mc", "question": "벡터 시계로 판단할 수 있는 것은?",
            "choices": ["동시성", "물리 시각", "네트워크 지연", "메모리 크기"], "answer": 0,
            "explanation": "벡터 시계는 동시성을 판단한다 [1].", "cite": [1]}
    shelf.answers.append(json.dumps({"title": "퀴즈", "questions": [
        good,
        {**good, "question": "답 번호가 범위 밖", "answer": 9},
        {**good, "question": "보기가 겹침", "choices": ["가", "가", "나", "다"]},
        {**good, "question": "모두 정답 보기", "choices": ["가", "나", "다", "모두 정답"]},
        {"type": "short", "question": "같은 프로세스 뒤 사건의 시계 값은?", "answer": "더 큰 값",
         "explanation": "뒤의 사건은 더 큰 값을 받는다 [1].", "cite": [1]},
        {"type": "short", "question": "해설 없음", "answer": "x", "explanation": ""},
        {**good, "question": "벡터 시계의 칸 수는?",
         "choices": ["프로세스 수", "1", "2", "사건 수"],
         "explanation": "칸은 프로세스 수만큼이다 [1]."},
    ]}, ensure_ascii=False))
    started = await shelf.client.post(f"/projects/{shelf.ids.project}/studio", json={
        "kind": "quiz", "sourceIds": [shelf.ids.lecture], "options": {"count": 5}})
    await shelf_jobs.drain()
    item = (await shelf.client.get(f"/studio/{started.json()['id']}")).json()
    questions = item["content"]["questions"]
    assert [q["question"] for q in questions] == [
        good["question"], "같은 프로세스 뒤 사건의 시계 값은?", "벡터 시계의 칸 수는?"]
    first = questions[0]
    assert first["choices"][first["answer"]] == "동시성"

    exported = await shelf.client.get(f"/studio/{item['id']}/export", params={"format": "csv"})
    rows = list(csv.reader(io.StringIO(exported.text.lstrip("﻿"))))
    assert rows[0][:3] == ["번호", "유형", "문제"] and rows[1][8] == "동시성"
    assert rows[1][10] == "03강 논리 시계.pdf"
    anki = (await shelf.client.get(f"/studio/{item['id']}/export",
                                   params={"format": "anki"})).text
    assert anki.startswith("#separator:tab") and "\t" in anki.splitlines()[2]

    sheet = await shelf.client.post(f"/studio/{item['id']}/sheet", json={"format": "web"})
    async with shelf.maker() as db:
        artifact = await db.get(Artifact, sheet.json()["artifactId"])
    assert [s["heading"] for s in artifact.data["sections"]] == ["문제", "정답과 해설"]
    assert "① " in artifact.data["sections"][0]["content"]


async def test_speech_stays_off_where_internal_data_must_stay_inside(shelf):
    shelf.answers.append(json.dumps({"title": "개요", "lines": [
        {"speaker": "A", "text": "오늘은 논리 시계 이야기예요."},
        {"speaker": "B", "text": "전역 시계가 없어서 순서를 따로 정해야 해요 [1]."},
        {"speaker": "A", "text": "램포트 시계는 어떻게 하나요?"},
        {"speaker": "B", "text": "**사건마다** 정수를 붙여요."},
    ]}, ensure_ascii=False))
    started = await shelf.client.post(f"/projects/{shelf.ids.project}/studio", json={
        "kind": "audio", "sourceIds": [shelf.ids.lecture], "options": {"length": "short"}})
    await shelf_jobs.drain()
    item = (await shelf.client.get(f"/studio/{started.json()['id']}")).json()
    lines = item["content"]["lines"]
    assert [line["speaker"] for line in lines] == ["A", "B", "A", "B"]
    # no citations or markdown in what is read aloud
    assert lines[1]["text"].endswith("정해야 해요.") and "**" not in lines[3]["text"]
    assert item["speech"]["state"] == "idle"

    refused = await shelf.client.post(f"/studio/{item['id']}/speech", json={"consent": True})
    assert refused.status_code == 409 and refused.json()["detail"] == "strict_local_no_tts"
    shelf.rules.internal_data_strict_local = False
    unconsented = await shelf.client.post(f"/studio/{item['id']}/speech", json={})
    assert unconsented.json()["detail"] == "speech_consent_required"
    plan = (await shelf.client.get(f"/projects/{shelf.ids.project}/studio/speech")).json()
    assert plan["mode"] == "outside" and plan["model"] == SPEECH["id"]


async def test_speech_from_two_voices_becomes_one_clip(shelf, monkeypatch):
    shelf.rules.internal_data_strict_local = False
    async with shelf.maker() as db:
        item = StudioItem(user_id="me", project_id=shelf.ids.project, kind="audio",
                          title="개요", state="done", speech_state="idle",
                          content={"lines": [{"speaker": "A", "text": "안녕하세요 010-1234-5678"},
                                             {"speaker": "B", "text": "반갑습니다"}]})
        db.add(item)
        await db.commit()
        item_id = item.id
    voices: list[tuple[str, str]] = []

    async def speak(**kwargs):
        voices.append((kwargs["voice"], kwargs["prompt"]))
        from app.services import audiogen

        return audiogen.GeneratedAudio(audiogen._wav(b"\x01\x00" * 2400), "audio/wav", "wav",
                                       10, 50, "")

    async def key(user):
        return "k"

    async def credentials(user):
        return "http://litellm", "k"

    monkeypatch.setattr(studio.audiogen, "generate", speak)
    from app.services import litellm as litellm_service

    monkeypatch.setattr(litellm_service, "ensure_key", key)
    monkeypatch.setattr(litellm_service, "credentials_for", credentials)
    started = await shelf.client.post(f"/studio/{item_id}/speech", json={"consent": True})
    assert started.status_code == 202, started.text
    await shelf_jobs.drain()
    done = (await shelf.client.get(f"/studio/{item_id}")).json()
    assert done["speech"]["state"] == "done" and done["audioFileId"]
    assert [v for v, _ in voices] == ["nova", "onyx"]
    # an outside speech model gets the script masked for personal data
    assert "010-1234-5678" not in voices[0][1]
    assert done["credits"] > 0


# ── notes ─────────────────────────────────────────────────────────────


async def test_notes_are_edited_with_a_conflict_check_and_become_sources(shelf):
    made = (await shelf.client.post(f"/projects/{shelf.ids.project}/notes", json={
        "body": "## 시험 범위\n3강까지", "origin": "answer", "sessionId": shelf.ids.session,
    })).json()
    assert made["title"] == "시험 범위" and made["origin"] == "answer"
    edited = await shelf.client.patch(f"/notes/{made['id']}", json={
        "body": "## 시험 범위\n4강까지", "expected": made["updatedAt"]})
    assert edited.status_code == 200
    stale = await shelf.client.patch(f"/notes/{made['id']}", json={
        "body": "옛 판에서 고침", "expected": made["updatedAt"]})
    assert stale.status_code == 409 and stale.json()["detail"] == "note_conflict"

    source = await shelf.client.post(f"/notes/{made['id']}/source")
    assert source.status_code == 200, source.text
    file_id = source.json()["id"]
    listing = (await shelf.client.get(f"/projects/{shelf.ids.project}/shelf")).json()
    assert {s["id"]: s["kind"] for s in listing["sources"]}[file_id] == "note"
    # a later edit of the note updates the source it became
    latest = (await shelf.client.get(f"/projects/{shelf.ids.project}/notes")).json()[0]
    await shelf.client.patch(f"/notes/{made['id']}", json={
        "body": "## 시험 범위\n5강까지", "expected": latest["updatedAt"]})
    async with shelf.maker() as db:
        assert "5강까지" in (await db.get(StoredFile, file_id)).text
    # empty notes are refused
    empty = await shelf.client.post(f"/projects/{shelf.ids.project}/notes", json={"body": " "})
    assert empty.status_code == 422


# ── pure parts ────────────────────────────────────────────────────────


def test_citations_are_kept_to_the_listed_numbers():
    assert studio_kinds.clean_citations("가 [1, 4]. 나 [5]. 다 [2]", 3) == "가 [1]. 나. 다 [2]"
    assert studio_kinds.cited("x [2] y [1, 2]") == [2, 1]


def test_a_mind_map_label_cannot_break_the_mermaid_block():
    source = studio_kinds.mindmap_source("분산 (시스템)", [
        {"label": "시계 [논리]", "children": [{"label": "램포트: 정수"}]}])
    assert source.splitlines()[0] == "mindmap"
    assert "root((분산  시스템))" in source
    assert "[" not in source.split("root", 1)[1].replace("root((", "")
    assert "램포트  정수" in source


def test_mc_answers_follow_their_choice_through_the_shuffle():
    data = {"questions": [{"type": "mc", "question": f"질문 {n}",
                           "choices": ["정답", "가", "나", "다"], "answer": "정답",
                           "explanation": "근거 [1]"} for n in range(12)]}
    made = studio_kinds.quiz(data, 1, random.Random(3))
    assert all(q["choices"][q["answer"]] == "정답" for q in made)
    # the right answer does not sit in one place
    assert len({q["answer"] for q in made}) > 1


def test_a_long_source_is_read_across_its_length():
    text = "앞" * 5000 + "중간표시" + "뒤" * 5000 + "끝표시"
    made = shelf_sources.bundle([SimpleNamespace(id="a", name="a.txt", text=text, mime="",
                                                 source_url=None)], 3000)
    assert "끝표시" in made.text and made.partial == ["a.txt"]
    assert studio_kinds.language_of("The quick brown fox " * 50) == "en"
    assert studio_kinds.language_of("분산 시스템 " * 50) == "ko"


def test_the_migration_makes_the_tables_the_models_read():
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0086_knowledge_shelf.py"
    spec = importlib.util.spec_from_file_location("m0086", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    models = (SourceSelection, SourceGuide, ProjectNote, StudioItem)
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id VARCHAR PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        inspector = sa.inspect(conn)
        for model in models:
            table = model.__table__
            assert {c["name"] for c in inspector.get_columns(table.name)} == {
                c.name for c in table.columns}, table.name
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert not {m.__table__.name for m in models} & set(sa.inspect(conn).get_table_names())


async def test_a_document_without_citations_is_asked_again_and_chinese_slips_are_mended(shelf):
    uncited = {"title": "브리핑", "summary": "분散 시스템은 여러 컴퓨터로 동작한다.",
               "sections": [{"heading": "핵심 내용", "content": "- 전역 시계가 없다."}]}
    cited = {"title": "브리핑", "summary": "분散 시스템은 여러 컴퓨터로 동작한다 [1].",
             "sections": [{"heading": "핵심 내용", "content": "- 전역 시계가 없다 [1]."}]}
    shelf.answers.extend([json.dumps(uncited, ensure_ascii=False),
                          json.dumps(cited, ensure_ascii=False),
                          json.dumps(["분산 시스템은 여러 컴퓨터로 동작한다 [1]."],
                                     ensure_ascii=False)])
    started = await shelf.client.post(f"/projects/{shelf.ids.project}/studio", json={
        "kind": "briefing", "sourceIds": [shelf.ids.lecture]})
    await shelf_jobs.drain()
    item = (await shelf.client.get(f"/studio/{started.json()['id']}")).json()
    assert item["state"] == "done", item
    body = "\n".join(s["content"] for s in item["content"]["sections"])
    assert "[1]" in body and "散" not in body and "분산 시스템은" in body
    # first answer, the re-ask, and the Korean rewrite
    assert len(shelf.calls) == 3 and "cited no sources" in shelf.calls[1]["system"]


def test_a_glossary_keeps_only_the_sources_own_terms():
    data = {"terms": [
        {"term": "맵리듀스(MapReduce)", "definition": "맵과 리듀스 [1]."},
        {"term": "벡터 시계", "definition": "칸이 프로세스 수 [1]."},
        {"term": "인수 일관성", "definition": "지어낸 낱말 [1]."},
    ]}
    sources = "MapReduce는 2004년에 발표되었다. 벡터시계는 칸을 가진다."
    doc = studio_kinds.document("glossary", data, 1, "ko", sources)
    body = doc["sections"][0]["content"]
    assert "맵리듀스" in body and "벡터 시계" in body and "인수 일관성" not in body
