"""A person's own templates: made in the editor, picked, shared, and stored as text."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, Message
from app.models.user import AuditEvent, User
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
from app.services import design_templates, user_templates


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
                StoredFile, Skill, UserPipeline, AuditEvent)])
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


async def _stored(db, owner: User, **over) -> UserTemplate:
    """A template as people made them before the editor was retired."""
    row = UserTemplate(owner_id=owner.id, **{
        **user_templates.clean(_payload(name="우리 회의록").model_dump()), **over})
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def test_templates_are_no_longer_made_or_changed(db):
    me, _ = await _people(db)
    row = await _stored(db, me)
    for call in (
        router.create_template(_payload(name="새 것"), me),
        router.update_template(user_templates.template_id(row), _payload(name="고침"), me),
        router.upload_form(user_templates.template_id(row), me, file=None),
    ):
        with pytest.raises(HTTPException) as refused:
            await call
        assert refused.value.status_code == 410 and refused.value.detail == "templates_retired"


async def test_a_template_becomes_an_agent_with_its_format_questions_checks_and_process(db):
    me, other = await _people(db)
    row = await _stored(db, me, body_face="serif", example_prompt="회의 메모를 정리해 줘.")
    listed = await router.list_templates(me, db)
    assert [t["name"] for t in listed["mine"]] == ["우리 회의록"]

    made = await router.template_to_agent(user_templates.template_id(row), me, db)
    agent = await db.get(Agent, made.id)
    shipped = design_templates.get("doc-minutes")
    assert (agent.owner_id, agent.kinds, agent.surface) == (me.id, ["report"], "report")
    assert agent.doc_format == "minutes"
    assert [q["id"] for q in agent.gap_questions] == [q["id"] for q in shipped.questions]
    assert agent.check_skills == list(shipped.check_skills)
    assert agent.process_settings == row.process
    assert agent.design_brief == "본문은 명조."
    assert agent.starters == ["회의 메모를 정리해 줘."]
    assert shipped.instructions.strip()[:40] in agent.system_prompt
    # the template stays; a second conversion gets its own handle
    again = await router.template_to_agent(user_templates.template_id(row), me, db)
    assert again.slug != made.slug and await db.get(UserTemplate, row.id) is not None
    # only the owner converts
    with pytest.raises(HTTPException) as refused:
        await router.template_to_agent(user_templates.template_id(row), other, db)
    assert refused.value.status_code == 404


async def test_a_template_of_its_own_format_carries_it_in_the_instructions(db):
    me, _ = await _people(db)
    row = await _stored(db, me, doc_format={"label": "현장 점검표", "fields": ["점검일", "점검자"],
                                            "numbering": "korean"})
    made = await router.template_to_agent(user_templates.template_id(row), me, db)
    agent = await db.get(Agent, made.id)
    assert agent.doc_format is None
    assert "문서 이름: 현장 점검표" in agent.system_prompt
    assert "점검일, 점검자" in agent.system_prompt
