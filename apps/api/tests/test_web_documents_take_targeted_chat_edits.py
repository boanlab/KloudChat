"""A web report or web deck is edited from the chat as the office files are: read with
numbers, then targeted ops that change only what they name, saved from the version read; a
whole rewrite, a new structure, an undo and a file hand-over stay with the writers."""

from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, SessionKind
from app.models.workspace import Artifact, ArtifactKind, ArtifactVersion
from app.routers import sessions
from app.schemas.chat import SendMessage
from app.services import artifact_versions, web_doc_edit
from app.services.tools import web_doc
from app.services.tools.base import ToolContext


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw) -> str:
    return "JSON"


TABLE = "| 구분 | 내용 |\n| --- | --- |\n| 일정 | 3월 |\n| 장소 | 본관 |"
REPORT = {
    "sections": [
        {"id": "a", "heading": "개요", "content": "예산은 3억 원입니다. 담당은 [담당자]입니다."},
        {"id": "b", "heading": "운영", "content": f"운영 결과는 아래와 같습니다.\n\n{TABLE}"},
        {"id": "c", "heading": "위험", "content": "- 일정 지연\n- 예산 초과"},
    ],
    "titleBlock": {"format": "result_report", "fields": [["작성일", ""], ["작성자", ""]]},
}
DECK = {"slides": [
    {"id": "s0", "layout": "title", "title": "사업 계획"},
    {"id": "s1", "layout": "bullets", "title": "현황", "bullets": ["매출 120억", "고객 3만"],
     "notes": "현황을 길게 설명하는 메모입니다. 여러 문장으로 되어 있습니다."},
    {"id": "s2", "layout": "bullets", "title": "요청", "bullets": ["예산 승인"]},
]}


def test_report_ops_change_only_what_they_name():
    done = web_doc_edit.apply(REPORT, "결과 보고", [
        {"op": "replace_text", "section": "s1", "find": "3억 원", "text": "3억 5천만 원"},
        {"op": "fill_blank", "blank": "[담당자]", "value": "김민수"},
        {"op": "set_cell", "section": "s2", "table": 1, "row": 2, "col": 1,
         "text": "본관 대강당"},
        {"op": "insert_table_column", "section": "s2", "table": 1, "after": 1, "text": "비고"},
        {"op": "move_section", "section": "s3", "after": "s1"},
        {"op": "set_head", "fields": {"작성일": "2026. 11. 13.", "작성자": "홍길동"}},
        {"op": "set_theme", "accent": "#1f3a68"},
    ], deck=False)
    sections = done.data["sections"]
    assert sections[0]["content"] == "예산은 3억 5천만 원입니다. 담당은 김민수입니다."
    assert [s["id"] for s in sections] == ["a", "c", "b"]
    assert "| 장소 | 본관 대강당 |  |" in sections[2]["content"]
    assert "| 구분 | 내용 | 비고 |" in sections[2]["content"]
    assert done.data["titleBlock"]["fields"] == [["작성일", "2026. 11. 13."], ["작성자", "홍길동"]]
    assert done.data["design"]["accent"] == "#1F3A68"
    # the input is not touched; a refused op changes nothing
    assert REPORT["sections"][0]["content"].startswith("예산은 3억 원")
    with pytest.raises(web_doc_edit.WebDocError, match="없습니다"):
        web_doc_edit.apply(REPORT, "t", [
            {"op": "rename_section", "section": "s1", "heading": "새 이름"},
            {"op": "replace_text", "find": "없는 글", "text": "x"}], deck=False)


def test_deck_ops_notes_tables_and_theme():
    done = web_doc_edit.apply(DECK, "사업 계획", [
        {"op": "set_text", "slide": "s2", "field": "notes", "text": "짧은 메모."},
        {"op": "set_table", "slide": "s3", "rows": [["항목", "금액"], ["예산", "[금액]"]]},
        {"op": "move_slide", "slide": "s3", "after": "s1"},
        {"op": "set_theme", "visual_style": "slate"},
    ], deck=True)
    slides = done.data["slides"]
    assert slides[1]["rows"] == [["항목", "금액"], ["예산", "[금액]"]]
    assert slides[1]["layout"] == "table" and "bullets" not in slides[1]
    # notes only: the slide's bullets as they were
    assert slides[2]["notes"] == "짧은 메모." and slides[2]["bullets"] == ["매출 120억", "고객 3만"]
    assert done.data["design"]["visualStyle"] == "slate"
    text = web_doc_edit.outline(done.data, done.title, deck=True)
    assert "s2 (table)" in text and "N번째" in text


@pytest.fixture
async def maker(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: SQLModel.metadata.create_all(c, tables=[
            ChatSession.__table__, Artifact.__table__, ArtifactVersion.__table__]))
    made = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(web_doc, "SessionLocal", made)
    yield made
    await engine.dispose()


async def _report(maker) -> tuple[str, str]:
    async with maker() as db:
        session = ChatSession(user_id="u1", kind=SessionKind.report)
        artifact = Artifact(user_id="u1", session_id=session.id, kind=ArtifactKind.report,
                            title="결과 보고", data=dict(REPORT))
        session.artifact_id = artifact.id
        db.add_all([session, artifact])
        await db.commit()
        return session.id, artifact.id


async def test_the_tools_save_from_the_version_read(maker):
    session_id, artifact_id = await _report(maker)
    ctx = ToolContext(user_id="u1", session_id=session_id, open_artifact={"id": artifact_id},
                      request="1절 예산을 고쳐 줘")
    read = await web_doc.report_read({}, ctx)
    assert "s2: 운영" in read.content and "[담당자]" in read.content
    edited = await web_doc.report_edit({"ops": [
        {"op": "replace_text", "section": "s1", "find": "3억", "text": "4억"}]}, ctx)
    assert not edited.failed, edited.content
    assert edited.trace == {"artifactId": artifact_id, "artifactVersion": 2}
    assert ctx.sandbox["_report"]["saved"][0]["version"] == 2
    # a hand save lands: the next edit is told to read again
    async with maker() as db:
        artifact = await db.get(Artifact, artifact_id)
        assert await artifact_versions.advance(db, artifact, 2, "편집기에서 저장")
        await db.commit()
    stale = await web_doc.report_edit({"ops": [
        {"op": "replace_text", "section": "s1", "find": "4억", "text": "5억"}]}, ctx)
    assert stale.failed and "다시 읽" in stale.content


async def test_which_turns_edit_in_place(maker):
    session_id, artifact_id = await _report(maker)
    async with maker() as db:
        session = await db.get(ChatSession, session_id)
        for text in ("2절의 예산을 4억으로 고쳐 줘", "작성자는 홍길동으로 해 줘",
                     "1장을 절반으로 줄여 줘", "이 보고서 몇 절이에요?"):
            found = await sessions._web_edit_target(db, session, SendMessage(content=text))
            assert found is not None and found.id == artifact_id, text
        for text in ("전체를 다시 써 줘", "되돌려 줘", "PDF로 줘"):
            assert await sessions._web_edit_target(
                db, session, SendMessage(content=text)) is None, text
        session.pending = {"stage": "outline"}
        assert await sessions._web_edit_target(
            db, session, SendMessage(content="2절 고쳐 줘")) is None
