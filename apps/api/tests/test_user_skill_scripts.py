"""A person's skill opts into what the server runs exactly as a shipped SKILL.md does, by
naming shipped scripts; its own scripts run only in the code sandbox.

- (가) `server` fields (`post_check`, `post_judge`, `stage`/`entry`, `copy_check`,
  `copy_kinds`, `tables`, `own_stage`, `uses`) are checked against the shipped allowlists in
  the form and in an uploaded SKILL.md, stored on the row, and read by the same checks: a
  person's skill with a shipped skill's fields gets the same result.
- (나) A person's Python scripts are kept in the row, scanned, run by `run_skill_script` in
  the sandbox (mocked here), never imported by the server; sharing a skill with scripts waits
  for an administrator.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import zipfile

import pytest
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.datastructures import Headers, UploadFile

from app.models.user import AuditEvent, User, UserRole
from app.models.workspace import Agent, Skill, UserPipeline, Visibility
from app.routers import skill_files
from app.routers import workspace as ws
from app.schemas.workspace import SkillIn, SkillServer
from app.services import (
    bundles,
    check_skills,
    copy_check,
    post_checks,
    sandbox,
    skill_import,
    skill_packages,
    skill_scripts,
    skill_specs,
)
from app.services.tools import registry, sandbox_tools
from app.services.tools.base import ToolContext

FOCUS = ("claims about the company, competitors, market or customers, what an interview or "
         "survey found, what a cited source [n] says, and capabilities the material does not give")


# ── (가) the allowlists ──────────────────────────────────────────────────


def test_the_allowlists_are_what_shipped_skills_use():
    assert "source-check/post" in skill_specs.post_check_refs()
    assert "market-sizing/size" in skill_specs.post_check_refs()
    assert skill_specs.entry_refs()["pre"] == ("gap-check/gaps",)
    assert set(skill_specs.entry_refs()["post"]) == {
        "copy-check/claims", "generic-language-killer/scan", "grounding-check/trace",
        "answer-hold/hold", "contact-note/note", "pii-mask/mask"}
    assert {"claim", "medical", "guarantee"} <= set(skill_specs.copy_kinds())
    catalogue = skill_specs.catalogue()
    assert {r["ref"] for r in catalogue["postChecks"]} == set(skill_specs.post_check_refs())
    assert catalogue["entries"]["pre"][0]["skill"] == "빠진 정보 묻기"


def test_unknown_names_are_refused_and_own_scripts_never_run_on_the_server():
    fields, errors = skill_specs.validate(
        {"stage": "post", "entry": "grounding-check/evil", "post_check": ["mine", "x/y"],
         "copy_kinds": ["claim", "bogus"], "uses": ["date-calc/dates", "mine", "nope/x"]},
        own_scripts=("mine",))
    codes = [e.code for e in errors]
    assert codes == ["skill_entry_unknown", "skill_post_check_own_script",
                     "skill_post_check_unknown", "skill_copy_kind_unknown",
                     "skill_script_unknown"]
    # A person's own script may be named only for the sandbox (`uses`).
    assert fields["uses"] == ["date-calc/dates", "mine"]
    assert fields["post_check"] == [] and fields["stage"] == "" and fields["copy_kinds"] == [
        "claim"]


def test_every_shipped_package_s_fields_pass_the_person_s_validation():
    """The allowlists cover every shipped skill: a person can reproduce each one."""
    for package in skill_packages.packages().values():
        fields = skill_specs.package_fields(package)
        kept, errors = skill_specs.validate(fields)
        assert not errors, (package.key, errors)
        assert kept == skill_specs.normalize(fields), package.key


def _row(**fields) -> Skill:
    return Skill(id="u1", owner_id="me", name="내 경쟁 분석", body="경쟁사를 표로 비교한다.",
                 server_fields=fields)


def test_a_person_s_skill_with_a_shipped_skill_s_fields_is_checked_the_same():
    shipped = skill_packages.package_for("competitor-analysis")
    mine = skill_specs.of_row(_row(**skill_specs.package_fields(shipped)))
    specs = {mine.key: mine}
    assert mine.key == "skill:u1"
    assert post_checks.entries(mine.key, specs) == post_checks.entries("competitor-analysis")
    assert post_checks.on_turn((mine.key,), [], specs) == [mine.key]
    # Without its spec the key means nothing: the server never guesses a person's skill.
    assert post_checks.on_turn((mine.key,)) == []

    seen: list[dict] = []

    async def complete(_model, messages, _key, _n):
        seen.append({"prompt": messages[0]["content"][:4000]})
        return json.dumps({"unsupported": []}), {"inputTokens": 1, "outputTokens": 1}

    answer = "| 항목 | A사 |\n|---|---|\n| 가격 | 월 3만 원 |"
    kwargs = dict(request="비교해 줘", material="A사 가격 자료 없음", attached="", computed=[],
                  today="2026-10-10", complete=complete, model="m", judge="j", api_key="k")
    theirs = asyncio.run(post_checks.run(answer, keys=["competitor-analysis"], **kwargs))
    shipped_prompts = list(seen)
    seen.clear()
    ours = asyncio.run(post_checks.run(answer, keys=[mine.key], specs=specs, **kwargs))
    assert (ours.text, ours.fixed, ours.sent, ours.flags) == (
        theirs.text, theirs.fixed, theirs.sent, theirs.flags)
    # The judge was asked with the same focus.
    assert [p["prompt"].count(FOCUS) for p in seen] == [
        p["prompt"].count(FOCUS) for p in shipped_prompts]


def test_a_person_s_copy_skill_narrows_the_copy_check_like_a_content_plan(monkeypatch):
    shipped = skill_packages.package_for("content-strategy")
    mine = skill_specs.of_row(_row(**skill_specs.package_fields(shipped)))
    specs = {mine.key: mine}
    assert copy_check.active((mine.key,), [], specs)
    assert not copy_check.active((mine.key,))
    # Picked through `use_skill`: the output carries its body.
    assert copy_check.on_turn((), ["지침\n" + mine.body], specs) == {mine.key}

    options: list[dict] = []
    real = copy_check._claims()

    class Claims:
        def __getattr__(self, name):
            return getattr(real, name)

        def check(self, text, material, replies, opts):
            options.append(dict(opts))
            return []

    monkeypatch.setattr(copy_check, "_claims", lambda: Claims())

    async def complete(_model, _messages, _key, _n):
        return json.dumps({"items": []}), {}

    for keys, given in ((frozenset({"content-strategy"}), None), (frozenset({mine.key}), specs)):
        asyncio.run(copy_check.run("새 기능 출시", request="카피", material="", complete=complete,
                                   model="m", judge="j", api_key="k", skills=keys, specs=given))
    assert options[0]["kinds"] == options[1]["kinds"] == list(shipped.copy_kinds)


def test_a_person_s_check_skill_runs_the_shipped_entry_it_names():
    mine = skill_specs.of_row(_row(stage="post", entry="grounding-check/trace"))
    specs = {mine.key: mine}
    keys = check_skills.attached(agent_checks=[mine.key], specs=specs)
    assert keys == (mine.key,)
    assert check_skills.entry(mine.key, specs) is skill_scripts.module("grounding-check", "trace")
    text = "참가비는 35,000원이다."
    ours = check_skills.ground(keys, text, material="참가비 자료 없음", computed=[], options={},
                               specs=specs)
    theirs = check_skills.ground(("grounding-check",), text, material="참가비 자료 없음",
                                 computed=[], options={})
    assert (ours.text, ours.found) == (theirs.text, theirs.found) and ours.found
    # Its bundle item attaches it the way a shipped check is ticked.
    bundle = bundles.own_checks([_row(stage="post", entry="grounding-check/trace")])
    made = bundles.compile("report", [{"bundle": bundle.ref, "items": {}}],
                           {bundle.ref: bundle})
    assert made.check_skills == [mine.key]


# ── (나) a person's scripts ───────────────────────────────────────────────


SKILL_MD = """---
name: 표 정리
description: 첨부한 표를 정리합니다.
required_tools: [run_skill_script]
post_check: source-check/post
post_judge: true
uses: [tidy]
---
1. `run_skill_script` 로 tidy 를 돌린다.
"""

TIDY = '''"""표의 빈 행을 지운다."""
import json, sys
print(json.dumps({"ok": True}))
'''


def _zip(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_an_upload_keeps_its_python_scripts_with_their_scan_and_its_fields():
    data = _zip({"tidy/SKILL.md": SKILL_MD, "tidy/scripts/tidy.py": TIDY,
                 "tidy/scripts/fetch.py": "import requests\nrequests.get('http://x')\n",
                 "tidy/scripts/wipe.py": "import shutil\nshutil.rmtree('/etc')\n",
                 "tidy/scripts/run.sh": "echo hi\n"})
    preview = skill_import.inspect("tidy.zip", data, {"run_skill_script"})
    assert preview.ok, preview.findings
    assert [s.name for s in preview.scripts] == ["fetch", "tidy", "wipe"]
    assert preview.scripts[1].summary == "표의 빈 행을 지운다."
    assert preview.required_tools == ["run_skill_script"]
    assert preview.server["post_check"] == ["source-check/post"]
    assert preview.server["uses"] == ["tidy"] and preview.server["post_judge"] is True
    by_code = {f.code: f for f in preview.findings}
    assert by_code["script_network"].file == "scripts/fetch.py"
    assert by_code["script_file_write"].file == "scripts/wipe.py"
    assert by_code["script_network"].severity == by_code["script_file_write"].severity == "warn"
    assert {e.path: e.reason for e in preview.excluded}["scripts/run.sh"] == "script"


def test_a_secret_or_broken_script_blocks_the_upload():
    leaked = _zip({"SKILL.md": SKILL_MD,
                   "scripts/tidy.py": 'KEY = "ghp_' + "A" * 36 + '"\n'})
    preview = skill_import.inspect("x.zip", leaked, {"run_skill_script"})
    assert not preview.ok and "secret_like" in {f.code for f in preview.findings if f.blocking}
    broken = skill_import.inspect("x.zip", _zip({"SKILL.md": SKILL_MD,
                                                 "scripts/tidy.py": "def (:\n"}),
                                  {"run_skill_script"})
    assert not broken.ok and "script_syntax" in {f.code for f in broken.findings}


class _Sandbox:
    def __init__(self):
        self.uploads: list[list[tuple[str, bytes]]] = []
        self.commands: list[str] = []

    async def upload(self, items, *, kind=None, read_only=False):
        self.uploads.append([(name, data) for name, data in items])
        assert read_only and kind == "skill"
        return [{"name": name, "id": f"f{i}"} for i, (name, _) in enumerate(items)]

    async def exec(self, code, *, lang, files=None, session_id=None):
        self.commands.append(code)
        return sandbox.interpret({"stdout": '{"ok": true}\n\n__KLOUDCHAT_EXIT__ 0',
                                  "session_id": "s1"})


def test_a_person_s_script_runs_in_the_sandbox_and_is_never_imported(monkeypatch):
    fake = _Sandbox()

    async def client():
        return fake

    async def no_files(_user, _session):
        return []

    def refuse(*_a, **_k):
        raise AssertionError("a person's script must not be imported by the server")

    monkeypatch.setattr(sandbox, "client", client)
    monkeypatch.setattr(sandbox, "session_files", no_files)
    monkeypatch.setattr(importlib.util, "spec_from_file_location", refuse)
    own = {"skill:u1": {"name": "표 정리", "files": {"tidy": TIDY}}}
    ctx = ToolContext(user_id="me", session_id="s", own_scripts=own)
    result = asyncio.run(sandbox_tools.run_skill_script(
        {"skill": "표 정리", "script": "tidy", "args": {"x": 1}}, ctx))
    assert not result.failed, result.content
    assert fake.uploads == [[("skills/own-u1/scripts/tidy.py", TIDY.encode())]]
    assert fake.commands[0].startswith("python3 skills/own-u1/scripts/tidy.py ")
    # Its key resolves nowhere on the server side.
    with pytest.raises(LookupError):
        skill_scripts.module("skill:u1", "tidy")
    with pytest.raises(LookupError):
        check_skills.entry("skill:u1", {"skill:u1": skill_specs.of_row(_row())})
    # The tool offers it by key beside the shipped scripts.
    tool = sandbox_tools.with_own_scripts([sandbox_tools.RUN_SKILL_SCRIPT], own)[0]
    assert "skill:u1" in tool.parameters["properties"]["skill"]["enum"]
    assert "표 정리" in tool.description
    # Someone else's turn does not see it.
    other = asyncio.run(sandbox_tools.run_skill_script(
        {"skill": "표 정리", "script": "tidy"}, ToolContext(user_id="o", session_id="s")))
    assert other.failed


# ── routes ─────────────────────────────────────────────────────────────


@pytest.fixture
async def db():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all, tables=[
                metadata.tables[m.__tablename__]
                for m in (User, Skill, AuditEvent, Agent, UserPipeline)])
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _tools(monkeypatch):
    async def catalog(_db, _user):
        return [{"name": "web_search"}, {"name": "run_skill_script"}]

    monkeypatch.setattr(registry, "tool_catalog", catalog)
    skill_files._calls.clear()


class _Request:
    def __init__(self):
        self.headers = Headers({"user-agent": "pytest", "content-length": "100"})
        self.client = type("C", (), {"host": "10.0.0.9"})()


async def _people(db):
    me = User(email="me@example.com", name="Me", password_hash="x")
    other = User(email="other@example.com", name="Other", password_hash="x")
    admin = User(email="admin@example.com", name="Admin", password_hash="x", role=UserRole.admin)
    db.add_all([me, other, admin])
    await db.commit()
    return me, other, admin


async def test_the_form_stores_fields_and_refuses_an_unknown_script(db):
    me, _, _ = await _people(db)
    made = await ws.create_skill(SkillIn(name="내 분석", body="표로 비교한다.", server=SkillServer(
        post_check=["source-check/post"], post_judge="수치와 출처", tables=True)), me, db)
    assert made.server.post_check == ["source-check/post"] and made.server.tables
    assert made.server.post_judge == "수치와 출처"
    row = await db.get(Skill, made.id)
    assert skill_specs.of_row(row).judge_focus == "수치와 출처"
    with pytest.raises(HTTPException) as caught:
        await ws.patch_skill(made.id, SkillIn(name="내 분석", server=SkillServer(
            post_check=["evil/run"])), me, db)
    assert caught.value.status_code == 422 and caught.value.detail == "skill_post_check_unknown"
    # Asking for nothing stores nothing.
    cleared = await ws.patch_skill(made.id, SkillIn(name="내 분석", server=SkillServer()), me, db)
    assert (await db.get(Skill, cleared.id)).server_fields is None


async def test_sharing_a_skill_with_scripts_waits_for_an_administrator(db):
    me, other, admin = await _people(db)
    upload = UploadFile(file=io.BytesIO(_zip({"SKILL.md": SKILL_MD, "scripts/tidy.py": TIDY})),
                        filename="tidy.zip")
    out = await skill_files.import_skills(_Request(), me, db, files=[upload], dry_run=False,
                                          draft=False)
    skill_id = out.created[0].id
    assert [s.name for s in out.created[0].script_files] == ["tidy"]
    assert out.created[0].script_files[0].source == TIDY
    shared = await ws.patch_skill(skill_id, SkillIn(name="표 정리", visibility="org"), me, db)
    assert shared.visibility is Visibility.private and shared.review == "pending"
    assert await ws.list_skill_store(other, db) == []
    queue = await skill_files.admin_skills(admin, db)
    assert [q.skill.id for q in queue] == [skill_id]
    assert queue[0].skill.script_files[0].source == TIDY
    with pytest.raises(HTTPException) as caught:
        await skill_files.reject_skill(skill_id, skill_files.SkillRejectIn(), admin, db)
    assert caught.value.detail == "reason_required"
    await skill_files.approve_skill(skill_id, admin, db)
    store = await ws.list_skill_store(other, db)
    assert [s.id for s in store] == [skill_id] and store[0].script_files[0].source == ""
    copy = await ws.install_skill(skill_id, other, db)
    row = await db.get(Skill, copy.id)
    assert [f["name"] for f in row.script_files] == ["tidy"]
    assert row.server_fields["uses"] == ["tidy"]
    events = (await db.exec(select(AuditEvent).where(AuditEvent.action == "skill.approve"))).all()
    assert len(events) == 1


async def test_a_skill_without_scripts_shares_at_once(db):
    me, other, _ = await _people(db)
    made = await ws.create_skill(SkillIn(name="내 분석", body="표로 비교한다.", server=SkillServer(
        post_check=["source-check/post"])), me, db)
    shared = await ws.patch_skill(made.id, SkillIn(name="내 분석", visibility="org"), me, db)
    assert shared.visibility is Visibility.org and shared.review == ""


async def test_an_agent_attaches_only_its_owner_s_check_skills(db):
    me, other, _ = await _people(db)
    server = SkillServer(stage="post", entry="grounding-check/trace")
    check = await ws.create_skill(SkillIn(name="내 근거 확인", body="근거를 본다.", server=server),
                                  me, db)
    plain = await ws.create_skill(SkillIn(name="그냥 스킬", body="x"), me, db)
    ok = await ws._validated_checks(db, me, {"check_skills": [f"skill:{check.id}"]})
    assert ok["check_skills"] == [f"skill:{check.id}"]
    for user, key in ((me, f"skill:{plain.id}"), (other, f"skill:{check.id}")):
        with pytest.raises(HTTPException):
            await ws._validated_checks(db, user, {"check_skills": [key]})
