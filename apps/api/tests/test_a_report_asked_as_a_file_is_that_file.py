"""A report asked for as an HWP or Word file: the report pipeline writes it, the file is what
is stored and opened beside the chat, and the conversation's later turns edit that file with
the office tools, as a chat's office document."""

from __future__ import annotations

import base64
import inspect
import io
import zipfile

import pytest
from sqlalchemy import MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.chat import ChatSession, Message, Role, SessionKind
from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind, ArtifactVersion
from app.pipeline import registry as pipeline_registry
from app.pipeline.runner import Runner
from app.routers import sessions
from app.services import docx_doc, docx_edit, hwp_doc, hwpx_edit, report_export, report_office


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw) -> str:
    """SQLite has no JSONB; the schema only needs to be creatable in memory."""
    return "JSON"


#: a 1×1 white PNG, the stand-in for a drawn figure
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR42mP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)
CHART = "bar | 억 원\n연도 | 예산\n2025 | 260\n2026 | 340"
FLOW = "flowchart LR\n  A[인증] --> B[조달]"


@pytest.mark.parametrize(
    ("request_text", "kind"),
    [
        ("한글로 2027 사업계획 보고서 써줘", ArtifactKind.hwp),
        ("한글 문서로 분기 실적 보고서를 작성해 줘", ArtifactKind.hwp),
        ("HWPX 파일로 연구 계획서 만들어 줘", ArtifactKind.hwp),
        ("Word 문서로 시장 조사 보고서 써 줘", ArtifactKind.docx),
        ("write the quarterly report as a Word document", ArtifactKind.docx),
        ("워드로 제안서 초안 작성해 줘", ArtifactKind.docx),
        ("워드 말고 한글 파일로 보고서 써 줘", ArtifactKind.hwp),
        # the first one named
        ("Word 파일로, 안 되면 한글 파일로 보고서 써 줘", ArtifactKind.docx),
    ],
)
def test_the_request_names_the_file(request_text, kind):
    assert report_office.asked_file(request_text) is kind


@pytest.mark.parametrize(
    "request_text",
    [
        "2027 사업계획 보고서 써줘",
        # the language, not the program
        "영어 말고 한글로 보고서 써줘",
        "외국인 직원용 안내를 한글로 번역해서 보고서로 정리해 줘",
        "키워드로 정리한 보고서",
        # pasted material under the request names no file
        "아래 자료로 보고서 써 줘\n---\n담당: 김 대리, 제출 형식은 hwp 파일",
        # a condition the server appended is not the person's words
        "보고서 써 줘\n\n덧붙인 조건:\n- 한글 파일로 제출",
    ],
)
def test_a_request_without_a_file_stays_the_html_report(request_text):
    assert report_office.asked_file(request_text) is None


def _report_data() -> dict:
    key = report_export.diagram_key(CHART)
    return {
        "design": {"accent": "#1F4E79"},
        "citationStyle": "APA",
        "sources": [{"title": "클라우드산업 실태조사", "url": "https://example.org/s",
                     "year": "2026"}],
        "sections": [
            {"id": "s1", "heading": "개요", "level": 1, "status": "done",
             "content": "**핵심**: 매출 목표는 [목표 매출]이다 [1]."},
            {"id": "s2", "heading": "시장", "level": 1, "status": "done",
             "content": f"예산이 늘었다 [1].\n\n```chart\n{CHART}\n```",
             "diagrams": {key: "data:image/png;base64," + base64.b64encode(PNG).decode()}},
            *({"id": f"s{n}", "heading": f"{n}장", "level": 1, "status": "done",
               "content": "본문 " * 400} for n in range(3, 9)),
        ],
    }


def test_the_file_is_the_report_with_its_sources_pictures_and_contents():
    data = _report_data()
    hwpx = report_office.file_bytes(ArtifactKind.hwp, "2027년 사업계획", data)
    assert hwp_doc.file_format(hwpx) == "hwpx"
    text = hwpx_edit.plain_text(hwpx)
    assert "2027년 사업계획" in text and "참고문헌" in text and "[1]" in text
    assert "[목표 매출]" in text
    assert any(name.startswith("BinData/") for name in zipfile.ZipFile(io.BytesIO(hwpx)).namelist())
    # **bold** is a bold run, not lost markers
    assert "**" not in text

    docx = report_office.file_bytes(ArtifactKind.docx, "2027년 사업계획", data)
    assert docx_doc.file_format(docx) == "docx"
    text = docx_edit.plain_text(docx)
    assert "참고문헌" in text and "[1]" in text and "**" not in text
    names = zipfile.ZipFile(io.BytesIO(docx)).namelist()
    # the chart as its picture, as in the HWP file; no chart part the web editor cannot draw
    assert any(name.startswith("word/media/") for name in names)
    assert not any(name.startswith("word/charts/") for name in names)
    # a 목차 the editor can show: the headings, no field asking for F9
    assert "F9" not in text and "목차" in text and "8장" in text


@pytest.mark.asyncio
async def test_the_figures_are_drawn_where_the_exporters_look(monkeypatch):
    async def slide(_blob):
        return PNG

    async def flow(_source, _accent="#2563EB", _box=None):
        return PNG

    from app.services import diagram_render

    monkeypatch.setattr(report_office, "_slide_png", slide)
    monkeypatch.setattr(diagram_render, "render_png", flow)
    sections = [{"id": "s", "heading": "h",
                 "content": f"```chart\n{CHART}\n```\n\n```mermaid\n{FLOW}\n```"}]
    drawn = await report_office.drawn(sections, None)
    assert set(drawn[0]["diagrams"]) == {report_export.diagram_key(CHART),
                                         report_export.diagram_key(FLOW)}


@pytest.mark.asyncio
async def test_without_the_printer_a_chart_keeps_its_table(monkeypatch):
    monkeypatch.setattr(settings, "print_base_url", "")
    sections = [{"id": "s", "heading": "h", "content": f"```chart\n{CHART}\n```"}]
    assert (await report_office.drawn(sections, None)) == sections


@pytest.fixture
async def maker(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    # Postgres-only server defaults (「'[]'::jsonb」) left out: SQLite cannot create them
    copied = MetaData()
    tables = [m.__table__.to_metadata(copied)
              for m in (User, ChatSession, Message, Artifact, ArtifactVersion)]
    for table in tables:
        for column in table.columns:
            column.server_default = None
    async with engine.begin() as conn:
        await conn.run_sync(copied.create_all)
    made = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sessions, "SessionLocal", made)
    yield made
    await engine.dispose()


async def _save(maker, session_id: str, turn_office: str) -> None:
    model = {"id": "fixture/writer", "creditCost": 0, "inputCreditCost": 0}
    turn = sessions._report_turn(
        user_id="u1", model=model, outline_model=None, project_id=None, routing=None,
        skills_event=None, context_steps=None, carry_from=None, carry_note="",
        office=turn_office)
    data = _report_data()
    work = sessions._told_bundle(
        {"report": {"sections": data["sections"]}, "sources": {"sources": data["sources"]},
         "title": {"title": "2027년 사업계획"}},
        usage={"inputTokens": 0, "outputTokens": 0}, turn=turn, request="한글로 사업계획 보고서",
        session_id=session_id, approved_plan=None, template=None, design_tokens=None,
        model=model)
    async for _ in Runner([pipeline_registry.stage("report-save")], name="report.save",
                          emit_records=False).run(work):
        pass


@pytest.mark.asyncio
async def test_the_save_stores_the_file_and_a_rerun_makes_its_next_version(maker, monkeypatch):
    monkeypatch.setattr(settings, "print_base_url", "")
    async with maker() as db:
        db.add(User(id="u1", email="u1@example.test", name="U", password_hash="x"))
        db.add(ChatSession(id="s1", user_id="u1", kind=SessionKind.report))
        db.add(ChatSession(id="s2", user_id="u1", kind=SessionKind.report))
        await db.commit()

    await _save(maker, "s1", "hwp")
    async with maker() as db:
        session = await db.get(ChatSession, "s1")
        made = (await db.exec(select(Artifact).where(Artifact.session_id == "s1"))).all()
        said = (await db.exec(select(Message).where(Message.session_id == "s1"))).all()
        # the file, not an HTML report beside it
        assert [a.kind for a in made] == [ArtifactKind.hwp]
        assert session.artifact_id == made[0].id and made[0].version == 1
        assert hwp_doc.file_format(hwp_doc.read(made[0])) == "hwpx"
        assert "참고문헌" in made[0].data["text"]
        assert [m.role for m in said] == [Role.assistant] and "한글 문서" in said[0].content
        assert await sessions._office_report(db, session) is not None

    # a rerun of the run (no file named in its turn) writes the same file's next version
    await _save(maker, "s1", "")
    async with maker() as db:
        made = (await db.exec(select(Artifact).where(Artifact.session_id == "s1"))).all()
        assert [(a.kind, a.version) for a in made] == [(ArtifactKind.hwp, 2)]

    # nothing named: the HTML report as before, and its turns stay the report's
    await _save(maker, "s2", "")
    async with maker() as db:
        session = await db.get(ChatSession, "s2")
        made = (await db.exec(select(Artifact).where(Artifact.session_id == "s2"))).all()
        assert [a.kind for a in made] == [ArtifactKind.report]
        assert await sessions._office_report(db, session) is None


def test_its_follow_ups_run_as_chat_turns_with_the_office_tools_and_the_pre_read():
    source = inspect.getsource(sessions._send_message)
    # the turn's surface is decided once, from the conversation's document
    assert ("kind = SessionKind.chat if await _office_report(db, session) else session.kind"
            in source)
    # the office tools, their documents and the pre-read hang on the turn's surface
    assert "if kind is SessionKind.chat and requested_model.get(\"supportsTools\"):" in source
    assert "_document_preset(" in source
    assert "session.kind is SessionKind.chat" not in source
    # the format reaches the report run from the request (or its waiting outline)
    assert "report_office.asked_file(" in source and "office=office_file" in source


@pytest.mark.parametrize("kind", [ArtifactKind.hwp, ArtifactKind.docx])
def test_a_table_is_added_to_the_written_file(kind):
    # 「표 추가」: a follow-up adds a new table after a paragraph, its first row the head
    edit, error = ((hwpx_edit, hwpx_edit.HwpxError) if kind is ArtifactKind.hwp
                   else (docx_edit, docx_edit.DocxError))
    data = report_office.file_bytes(kind, "2027년 사업계획", _report_data())
    before = edit.outline(data)
    tables = before.count("(표 ")
    made = edit.apply(data, [
        {"op": "insert_paragraph", "after": "p3", "text": "표 9. 핵심 요약"},
        {"op": "insert_table", "after": "p3",
         "rows": [["구분", "내용"], ["시장", "예산 증가"], ["과제", "[담당자] 지정"]]},
    ])
    after = edit.outline(made.data)
    assert after.count("(표 ") == tables + 1
    assert "r0: 구분 | 내용" in after and "r2: 과제 | [담당자] 지정" in after
    assert any("표 추가" in line for line in made.changes)
    # the new table is a table the next edit can work on
    table = next(line.split()[0] for line in after.splitlines()
                 if line.startswith("t") and "3×2" in line)
    again = edit.apply(made.data, [{"op": "set_cell", "table": table, "row": 1, "col": 1,
                                    "text": "예산 30% 증가"}])
    assert "예산 30% 증가" in edit.outline(again.data)
    with pytest.raises(error):
        edit.apply(data, [{"op": "insert_table", "after": "p3", "rows": "구분|내용"}])
    with pytest.raises(error):
        edit.apply(data, [{"op": "insert_table", "after": "p3",
                           "rows": [[str(c) for c in range(9)]]}])


def test_an_hwp_file_made_elsewhere_gets_no_table_it_would_break():
    data = report_office.file_bytes(ArtifactKind.hwp, "제목", _report_data())
    source = io.BytesIO(data)
    out = io.BytesIO()
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(out, "w") as after:
        for item in before.infolist():
            body = before.read(item.filename)
            if item.filename == "Contents/header.xml":
                body = body.replace(b'<hh:borderFill id="3"', b'<hh:borderFill id="33"')
            after.writestr(item, body)
    with pytest.raises(hwpx_edit.HwpxError, match="새 표를 넣을 수 없습니다"):
        hwpx_edit.apply(out.getvalue(), [{"op": "insert_table", "after": "p2",
                                          "rows": [["a", "b"]]}])


def test_the_hwp_outline_names_its_chapters_so_a_numbered_part_can_be_found():
    # 「3장 줄여 줘」: the model counts the headings the outline marks, as in a Word file
    data = report_office.file_bytes(ArtifactKind.hwp, "2027년 사업계획", _report_data())
    lines = hwpx_edit.outline(data).splitlines()
    heads = [line.split(": ", 1)[1] for line in lines if "(heading 1)" in line]
    assert any("(title): 2027년 사업계획" in line for line in lines)
    assert heads[:3] == ["개요", "시장", "3장"] and heads[-1] == "참고문헌"
