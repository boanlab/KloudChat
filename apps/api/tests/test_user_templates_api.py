"""A person's own templates: made in the editor, picked like a shipped 서식, shared, and safe.

Every field a shipped document 서식 sets can be set here (`draft_of` fills them all from one);
what a person writes is stored as text and reaches a page only as text."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, Message
from app.models.user import User
from app.models.workspace import (
    Agent,
    DesignSystem,
    Project,
    Skill,
    StoredFile,
    UserPipeline,
    UserTemplate,
)
from app.routers import user_templates as router
from app.routers import workspace as ws
from app.schemas.workspace import AgentIn
from app.services import design_templates, doc_formats, process_settings, user_templates


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
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=[
            metadata.tables[m.__tablename__] for m in (
                User, UserTemplate, Agent, DesignSystem, Project, ChatSession, Message,
                StoredFile, Skill, UserPipeline)])
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


async def _people(db):
    me = User(email="me@example.com", name="나", password_hash="x")
    other = User(email="o@example.com", name="남", password_hash="x")
    db.add_all([me, other])
    await db.commit()
    return me, other


def _payload(**over) -> router.TemplateIn:
    draft = user_templates.draft_of(design_templates.get("doc-minutes"))
    return router.TemplateIn(**{**draft, **over})


async def test_doc_minutes_recreated_in_the_editor_is_the_same_template(db):
    me, _ = await _people(db)
    made = await router.create_template(_payload(name="우리 회의록"), me, db)
    template = design_templates.get(made["id"])
    shipped = design_templates.get("doc-minutes")
    fmt, want = template.doc_format_def, doc_formats.BY_ID["minutes"]
    assert (fmt.fields, fmt.numbering, fmt.head, fmt.lists, fmt.closing) == (
        want.fields, want.numbering, want.head, want.lists, want.closing)
    assert template.questions == shipped.questions
    assert process_settings.resolve(template_id=template.id, format_id=None) \
        == process_settings.for_format("minutes")
    assert process_settings.resolve(template_id=template.id, format_id=None).outline \
        == process_settings.for_format("minutes").outline
    assert (template.instructions, template.checklist, template.check_skills) == (
        shipped.instructions, shipped.checklist, shipped.check_skills)
    assert doc_formats.detect("", template.id) is fmt
    # Drawn in the shared seed: no HTML or CSS of its own.
    assert template.seed == design_templates.rendering("_document").seed


async def test_what_a_person_writes_stays_text_and_bounded(db):
    me, _ = await _people(db)
    made = await router.create_template(_payload(
        name="<script>alert(1)</script>‮이름", description="a\x00b",
        instructions="지침​입니다\r\n둘째 줄"), me, db)
    template = design_templates.get(made["id"])
    assert "‮" not in template.name and "\x00" not in template.description
    assert template.instructions == "지침입니다\n둘째 줄"
    # A name is shown as text by the client; the page never carries it as markup.
    assert "<script>" not in design_templates.render(
        template, title="제목", tokens={}, body="<p>본문</p>")
    with pytest.raises(HTTPException) as caught:
        await router.create_template(_payload(process={"checks": ["rm -rf"]}), me, db)
    assert caught.value.detail == "process_check"
    with pytest.raises(HTTPException) as caught:
        await router.create_template(_payload(doc_format={"numbering": "evil"}), me, db)
    assert caught.value.detail == "template_numbering"


async def test_only_the_owner_edits_and_a_shared_one_is_picked_by_others(db):
    me, other = await _people(db)
    made = await router.create_template(_payload(name="팀 회의록"), me, db)
    with pytest.raises(HTTPException):
        await router.update_template(made["id"], _payload(name="뺏기"), other, db)
    with pytest.raises(HTTPException):
        await router.get_template(made["id"], other, db)
    # Not shared: another person cannot bind an agent to it.
    with pytest.raises(HTTPException):
        await ws.create_agent(AgentIn(name="x", kinds=["report"],
                                      render_template_id=made["id"]), other, db)
    await router.update_template(made["id"], _payload(name="팀 회의록", visibility="org"), me, db)
    seen = await router.list_templates(other, db)
    assert [t["id"] for t in seen["shared"]] == [made["id"]] and seen["mine"] == []
    assert seen["shared"][0]["instructions"] == ""  # only the owner reads the instructions
    agent = await ws.create_agent(AgentIn(name="x", kinds=["report"],
                                          render_template_id=made["id"]), other, db)
    assert agent.render_template_id == made["id"]
    listed = await ws.list_design_templates(other, db)
    assert any(t.id == made["id"] and t.user and not t.mine for t in listed)
    # Deleted: the agents that wrote in it go back to none.
    await router.delete_template(made["id"], me, db)
    assert (await db.get(Agent, agent.id)).render_template_id is None
    assert design_templates.get(made["id"]) is None


async def test_a_form_file_is_reference_text_only(db, tmp_path):
    from io import BytesIO

    from fastapi import UploadFile

    me, _ = await _people(db)
    made = await router.create_template(_payload(name="양식 있음"), me, db)
    form = design_templates.get("doc-minutes").form_file
    upload = UploadFile(file=BytesIO(open(form, "rb").read()), filename="회의록 양식.docx")
    out = await router.upload_form(made["id"], me, db, file=upload)
    assert out["hasForm"] and out["formName"] == "회의록 양식.docx"
    template = design_templates.get(made["id"])
    assert design_templates.form_text(template) == template.form_reference != ""
    # Never the export base: the shared seed's.
    assert template.docx_template == design_templates.rendering("_document").docx_template
    with pytest.raises(HTTPException):
        await router.upload_form(made["id"], me, db, file=UploadFile(
            file=BytesIO(b"x"), filename="evil.exe"))
