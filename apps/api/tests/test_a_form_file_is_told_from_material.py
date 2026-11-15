"""Form files (양식): an Office upload is profiled once, told apart from material, and saved as a
project's or agent's 기본 양식 파일; 디자인 지침 words become style parameters."""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from docx import Document
from fastapi import HTTPException, UploadFile
from pptx import Presentation
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession
from app.models.user import AuditEvent, User
from app.models.workspace import Agent, Artifact, Project, StoredFile
from app.routers import forms as forms_router
from app.routers import workspace as ws
from app.schemas.chat import SendMessage
from app.schemas.workspace import ProjectIn, ProjectPatch
from app.services import files as file_service
from app.services import form_files, form_profile, report_export, style_spec

# ── files ──────────────────────────────────────────────────────────────


def _pptx(*, filled: int = 0) -> bytes:
    deck = Presentation()
    if filled:
        for n in range(filled):
            slide = deck.slides.add_slide(deck.slide_layouts[1])
            slide.shapes.title.text = f"{n + 1}장 매출 현황"
            slide.placeholders[1].text = "지난 분기 매출은 12% 늘었다. " * 30
    else:
        deck.slides.add_slide(deck.slide_layouts[0])
    out = io.BytesIO()
    deck.save(out)
    return out.getvalue()


def _form_docx() -> bytes:
    doc = Document()
    doc.add_heading("1. 사업 개요", 1)
    doc.add_paragraph("※ 작성요령: 사업의 배경과 목적을 적는다.")
    doc.add_heading("2. 추진 계획", 1)
    table = doc.add_table(rows=3, cols=2)
    for row, label in enumerate(("사업명", "기관명", "대표자")):
        table.cell(row, 0).text = label
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _essay_docx() -> bytes:
    doc = Document()
    doc.add_heading("분기 실적 분석", 1)
    for _ in range(40):
        doc.add_paragraph("이번 분기 실적은 지난해보다 나아졌고 원가도 줄었다. " * 6)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _form_hwpx() -> bytes:
    table = "| 사업명 | |\n|---|---|\n| 기관명 | |\n| 대표자 | |\n| 연락처 | |"
    return report_export.to_hwpx("지원 신청서", [{"heading": "신청 기관", "content": table}])


def _as_template(data: bytes, kind: str) -> bytes:
    """The same file typed as an Office template (.potx/.dotx)."""
    import zipfile

    main = {
        "pptx": ("presentationml.presentation.main+xml", "presentationml.template.main+xml"),
        "docx": ("wordprocessingml.document.main+xml", "wordprocessingml.template.main+xml"),
    }[kind]
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as copy:
        for info in source.infolist():
            body = source.read(info.filename)
            if info.filename == "[Content_Types].xml":
                body = body.replace(main[0].encode(), main[1].encode())
            copy.writestr(info, body)
    return out.getvalue()


# ── profiles ───────────────────────────────────────────────────────────


def test_a_powerpoint_with_only_prompt_slides_is_a_form_and_lists_its_layouts():
    found = form_profile.profile("회사_발표양식.pptx", _pptx())
    assert found["kind"] == "pptx" and found["verdict"] == "form"
    classes = {layout["name"]: layout["class"] for layout in found["layouts"]}
    assert classes["Title Slide"] == "title"
    assert classes["Title and Content"] == "content"
    assert classes["Section Header"] == "section"
    assert classes["Two Content"] == "two"
    assert classes["Comparison"] == "comparison"
    assert classes["Title Only"] == "title_only"
    assert classes["Blank"] == "blank"
    assert found["theme"]["colours"]["accent1"].startswith("#")
    summary = form_profile.summary(found)
    assert summary["layouts"] == len(found["layouts"]) and summary["size"] == "4:3"


def test_a_filled_deck_is_material():
    found = form_profile.profile("3분기 실적.pptx", _pptx(filled=8))
    assert found["verdict"] == "material"


def test_a_word_form_shows_its_headings_guidance_and_label_cells():
    found = form_profile.profile("신청서.docx", _form_docx())
    assert found["verdict"] == "form"
    assert found["headings"]["1"]["style"] == "Heading1"
    assert [s["text"] for s in found["skeleton"]] == ["1. 사업 개요", "2. 추진 계획"]
    assert found["guidance"] == 1 and found["fields"]["label_cells"] == 3


def test_a_long_word_essay_is_material():
    assert form_profile.profile("분석.docx", _essay_docx())["verdict"] == "material"


def test_an_office_template_file_is_a_form_by_kind_and_opens_as_a_document():
    potx = _as_template(_pptx(filled=8), "pptx")
    found = form_profile.profile("회사.potx", potx)
    assert found["verdict"] == "form" and "template_file" in found["signals"]
    # python-pptx refuses a template's content type; the copy it reads is a presentation
    Presentation(io.BytesIO(form_profile.as_document(potx)))
    dotx = _as_template(_essay_docx(), "docx")
    assert form_profile.profile("보고.dotx", dotx)["verdict"] == "form"
    Document(io.BytesIO(form_profile.as_document(dotx)))
    assert form_profile.as_document(b"plain") == b"plain"


def test_a_hwpx_table_form_lists_its_value_cells_by_label():
    found = form_profile.profile("신청서_별지1호.hwpx", _form_hwpx())
    labels = [f["label"] for f in found["fields"]]
    assert {"사업명", "기관명", "대표자", "연락처"} <= set(labels)
    assert found["verdict"] == "form"


def test_a_binary_hwp_is_never_a_form_and_a_form_like_one_says_how_to_save_hwpx():
    found = form_profile.profile("신청서 양식.hwp", b"", "※ 작성요령\n성명: ○○○\n기관: ○○○\n")
    assert found["verdict"] == "material" and found["form_like"]
    assert "(*.hwpx)" in form_profile.summary(found)["advice"]
    plain = form_profile.profile("메모.hwp", b"", "회의에서 정한 것들")
    assert "advice" not in form_profile.summary(plain)


def test_a_broken_office_file_is_material_with_its_reason():
    found = form_profile.profile("깨짐.pptx", b"not a zip")
    assert found["verdict"] == "material" and found["error"]


def test_other_files_have_no_profile():
    assert form_profile.profile("표.xlsx", b"x") is None
    assert form_profile.summary(None) is None


# ── roles in a turn ────────────────────────────────────────────────────


def _row(name: str, data: bytes, text: str = "") -> StoredFile:
    row = StoredFile(user_id="u", name=name)
    row.form_profile = form_profile.profile(name, data, text)
    return row


def test_a_turn_follows_an_attached_form_and_reads_the_rest_as_material():
    form = _row("발표양식.pptx", _pptx())
    material = _row("초안.docx", _essay_docx())
    turn = form_files.choose([form, material], None, "초안으로 발표 자료 만들어 줘")
    assert turn.form_for("pptx").file_id == form.id
    assert turn.form_for("pptx").source == "attached"
    assert turn.material == [material.id]
    assert turn.roles == {form.id: "form", material.id: "material"}


def test_the_chip_role_wins_over_the_profile():
    form = _row("발표양식.pptx", _pptx())
    turn = form_files.choose([form], {form.id: "material"}, "요약해 줘")
    assert not turn.forms and turn.material == [form.id]
    filled = _row("작년 발표.pptx", _pptx(filled=8))
    turn = form_files.choose([filled], {filled.id: "form"}, "올해 것도 써 줘")
    assert turn.form_for("pptx").file_id == filled.id


def test_every_output_kind_with_a_writer_is_followed():
    assert form_files.FOLLOWED == ("pptx", "docx", "hwpx")


def test_the_requests_words_settle_an_unsure_file():
    # a few filled slides: neither clearly a form nor clearly material
    unsure = _row("발표.pptx", _pptx(filled=2))
    assert form_files.role_of(unsure, "정리해 줘", None)[0] in ("material", "unsure")
    assert form_files.role_of(unsure, "이 양식에 맞춰 만들어 줘", None)[0] == "form"
    turn = form_files.choose([unsure], None, "발표 자료 만들어 줘")
    if form_files.role_of(unsure, "발표 자료 만들어 줘", None)[0] == "unsure":
        assert turn.unsure == [unsure.id] and turn.material == [unsure.id]


def test_a_hwp_asked_to_be_a_form_is_read_as_material_with_the_hwpx_advice():
    hwp = _row("신청서.hwp", b"", "사업명 ○○○")
    turn = form_files.choose([hwp], {hwp.id: "form"}, "이 양식에 채워 줘")
    assert not turn.forms and turn.material == [hwp.id]
    assert turn.notes == {hwp.id: form_profile.HWP_FORM_ADVICE}


def test_two_forms_of_one_kind_keep_the_first():
    first, second = _row("양식A.pptx", _pptx()), _row("양식B.pptx", _pptx())
    turn = form_files.choose([first, second], None, "발표 자료")
    assert turn.form_for("pptx").file_id == first.id
    assert turn.material == [second.id] and "양식A.pptx" in turn.notes[second.id]


def test_an_attached_form_beats_the_agents_which_beats_the_projects():
    attached = _row("첨부양식.pptx", _pptx())
    agents, projects = _row("에이전트.pptx", _pptx()), _row("프로젝트.docx", _form_docx())
    project_pptx = _row("프로젝트.pptx", _pptx())
    turn = form_files.choose(
        [attached], None, "만들어 줘", agent_forms={"pptx": agents},
        project_forms={"pptx": project_pptx, "docx": projects})
    assert turn.form_for("pptx").source == "attached"
    assert turn.form_for("docx").source == "project"
    turn = form_files.choose([], None, "", agent_forms={"pptx": agents},
                             project_forms={"pptx": project_pptx})
    assert turn.form_for("pptx").file_id == agents.id
    assert not form_files.choose([], None, "", project_forms={"pptx": project_pptx},
                                 skip_defaults=True).forms


def test_a_turn_keeps_the_chip_roles_on_the_payload():
    payload = SendMessage(content="만들어 줘", attachments=["f1"],
                          attachmentRoles={"f1": "form"})
    assert payload.attachment_roles == {"f1": "form"}


# ── 디자인 지침 → style parameters ───────────────────────────────────────


def test_a_design_brief_reads_colour_faces_table_density_and_tone():
    spec = style_spec.parse(
        "남색 하나만 강조색으로(#1F3A6B). 본문은 명조, 제목은 고딕. 표는 머리 행만 옅게 칠하고 "
        "세로선 없이. 슬라이드는 한 장에 글머리 4개까지, 여백 넉넉히. 말투는 공문체.")
    assert spec.accent == "#1F3A6B"
    assert (spec.heading_face, spec.body_face) == ("gothic", "serif")
    assert spec.table.header_fill is True and spec.table.vertical_rules is False
    assert (spec.density, spec.max_bullets) == ("airy", 4)
    # 「여백 넉넉히」 is density, not the minimal look
    assert spec.visual_style is None
    assert spec.tone == ["말투는 공문체"]


def test_named_fonts_page_and_numbering_are_read():
    spec = style_spec.parse("제목은 나눔스퀘어, 본문은 함초롬바탕. A4 가로 방향. 번호는 1.1 식으로")
    assert (spec.heading_face, spec.body_face) == ("나눔스퀘어", "함초롬바탕")
    assert spec.face_family("body") == "serif" and spec.face_family("heading") == "gothic"
    assert (spec.page_size, spec.orientation, spec.numbering) == ("A4", "landscape", "decimal")


def test_a_table_colour_is_the_tables_not_the_accent():
    spec = style_spec.parse("표는 회색으로 해 줘")
    assert spec.accent is None and spec.table.colour == "#6B7280"


def test_this_turns_wish_beats_the_saved_brief():
    turn = style_spec.parse("빨간색으로")
    brief = style_spec.parse("남색 강조, 본문 명조")
    spec = style_spec.merged(turn, brief)
    assert spec.accent == "#C0392B" and spec.body_face == "serif"
    assert style_spec.tokens(spec)["font"] == "serif"
    assert style_spec.StyleSpec.of(spec.wire()).wire() == spec.wire()


def test_an_empty_brief_sets_nothing():
    assert style_spec.parse("").wire() == {}


# ── the migration ──────────────────────────────────────────────────────


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0090_form_files.py"
    spec = importlib.util.spec_from_file_location("form_files_0090", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_adds_and_drops_the_form_columns_on_sqlite():
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0090", "0089")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for table in ("files", "projects", "agents", "sessions"):
            conn.execute(sa.text(f"CREATE TABLE {table} (id VARCHAR PRIMARY KEY)"))
        conn.execute(sa.text("INSERT INTO projects (id) VALUES ('p1')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        inspector = sa.inspect(conn)
        assert {"role", "form_profile"} <= {c["name"] for c in inspector.get_columns("files")}
        for table in ("projects", "agents"):
            assert {"form_files", "design_brief"} <= {
                c["name"] for c in inspector.get_columns(table)}
        assert conn.execute(sa.text("SELECT design_brief FROM projects")).scalar_one() == ""
        assert "form_files" in {c["name"] for c in inspector.get_columns("sessions")}
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert {c["name"] for c in sa.inspect(conn).get_columns("files")} == {"id"}


# ── API ────────────────────────────────────────────────────────────────


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
    models = (User, Agent, StoredFile, Project, ChatSession, AuditEvent, Artifact)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                metadata.create_all,
                tables=[metadata.tables[model.__tablename__] for model in models])
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _scratch_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(file_service, "storage_root", lambda: tmp_path)

    async def no_index(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ws, "_index_document", no_index)


async def _person(db) -> User:
    me = User(email="me@example.com", name="Me", password_hash="x")
    db.add(me)
    await db.commit()
    return me


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


async def _upload_file(me: User, db, name: str, data: bytes):
    return await ws.upload_file(me, db, file=_upload(name, data), project_id=None,
                                session_id=None)


async def test_an_upload_carries_its_form_profile(db):
    me = await _person(db)
    out = await _upload_file(me, db, "발표양식.pptx", _pptx())
    assert out.form["kind"] == "pptx" and out.form["verdict"] == "form"
    text = await _upload_file(me, db, "메모.txt", "그냥 메모".encode())
    assert text.form is None


async def test_a_saved_form_is_kept_out_of_the_file_lists(db):
    me = await _person(db)
    source = await _upload_file(me, db, "발표양식.pptx", _pptx())
    saved = await forms_router.form_from_file(forms_router.FromFileIn(fileId=source.id), me, db)
    assert saved.role == "form" and saved.id != source.id
    listed = {row.id for row in await ws.list_files(me, db)}
    assert source.id in listed and saved.id not in listed
    page = await ws.page_files(me, db, box=True, origin=None, q=None, project_id=None,
                               session_id=None, sort="created", dir="desc", cursor=None,
                               limit=50)
    assert saved.id not in {row.id for row in page.items}


async def test_a_hwp_or_a_spreadsheet_cannot_be_saved_as_a_form(db):
    me = await _person(db)
    with pytest.raises(HTTPException) as refused:
        await forms_router.upload_form(me, db, file=_upload("표.xlsx", b"PK\x03\x04x"))
    assert refused.value.detail == "form_kind_unknown"
    with pytest.raises(HTTPException) as refused:
        await forms_router.upload_form(
            me, db, file=_upload("신청서.hwp", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600))
    assert refused.value.detail == "form_hwp_needs_hwpx"
    assert not (await db.exec(select(StoredFile))).all()


async def test_a_project_keeps_one_form_per_kind_and_its_design_brief(db):
    me = await _person(db)
    deck = await forms_router.upload_form(me, db, file=_upload("발표양식.pptx", _pptx()))
    word = await forms_router.upload_form(me, db, file=_upload("신청서.docx", _form_docx()))
    project = await ws.create_project(
        ProjectIn(name="지원사업", formFiles={"pptx": deck.id}, designBrief="남색, 본문 명조"),
        me, db)
    assert project.form_files == {"pptx": deck.id}
    assert [row.id for row in project.form_rows] == [deck.id]
    assert project.design_brief == "남색, 본문 명조"
    # a Word form in the PowerPoint slot is refused
    with pytest.raises(HTTPException) as refused:
        await ws.patch_project(project.id, ProjectPatch(formFiles={"pptx": word.id}), me, db)
    assert refused.value.detail == "form_kind_mismatch"
    patched = await ws.patch_project(
        project.id, ProjectPatch(formFiles={"docx": word.id}), me, db)
    assert patched.form_files == {"pptx": deck.id, "docx": word.id}
    # a replaced form nothing else names is deleted
    other = await forms_router.upload_form(me, db, file=_upload("새양식.pptx", _pptx()))
    await ws.patch_project(project.id, ProjectPatch(formFiles={"pptx": other.id}), me, db)
    assert await db.get(StoredFile, deck.id) is None
    cleared = await ws.patch_project(project.id, ProjectPatch(formFiles={"docx": None}), me, db)
    assert cleared.form_files == {"pptx": other.id}


async def test_a_form_another_project_names_survives_a_replacement(db):
    me = await _person(db)
    shared = await forms_router.upload_form(me, db, file=_upload("공통양식.pptx", _pptx()))
    first = await ws.create_project(ProjectIn(name="A", formFiles={"pptx": shared.id}), me, db)
    await ws.create_project(ProjectIn(name="B", formFiles={"pptx": shared.id}), me, db)
    await ws.patch_project(first.id, ProjectPatch(formFiles={"pptx": None}), me, db)
    assert await db.get(StoredFile, shared.id) is not None


async def test_another_persons_form_cannot_be_named(db):
    me = await _person(db)
    other = User(email="other@example.com", name="Other", password_hash="x")
    db.add(other)
    await db.commit()
    theirs = await forms_router.upload_form(other, db, file=_upload("양식.pptx", _pptx()))
    with pytest.raises(HTTPException) as refused:
        await ws.create_project(ProjectIn(name="X", formFiles={"pptx": theirs.id}), me, db)
    assert refused.value.detail == "form_not_found"


async def test_the_design_brief_reader_returns_the_chips():
    out = await forms_router.read_brief(
        forms_router.BriefIn(text="파란 계열, 맑은 고딕. 존댓말로."), User(email="x", name="x"))
    assert out.spec["accent"] == "#2563EB" and out.spec["body_face"] == "맑은 고딕"
    assert out.tone == ["존댓말로"] and "tone" not in out.spec
