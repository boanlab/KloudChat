"""A project's or agent's Excel form (기본 양식 파일): saved like the other forms, refused when it
is a macro workbook or an old `.xls`; a new workbook starts from it, its examples taken out."""

from __future__ import annotations

import io
from types import SimpleNamespace

import openpyxl
import pytest
from fastapi import HTTPException, UploadFile
from openpyxl.styles import Border, PatternFill, Side

from app.models.workspace import StoredFile
from app.routers import forms as forms_router
from app.services import form_files, form_marks, form_profile, sheet_edit, sheet_form, sheet_model
from app.services.tools import sheet as sheet_tools


def _form(*, name: str = "견적서.xlsx") -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "견적"
    sheet["A1"] = "견적서"
    sheet.merge_cells("A1:D1")
    sheet["A2"] = "※ 작성요령: 품목을 적는다"
    for col, head in enumerate(("품목", "수량", "단가", "금액"), 1):
        sheet.cell(3, col, head).fill = PatternFill("solid", fgColor="DDDDDD")
    sheet["A4"], sheet["B4"], sheet["C4"] = "홍길동", 3, 1200
    sheet["D4"] = "=B4*C4"
    sheet["C4"].number_format = "#,##0"
    thin = Side(style="thin")
    for row in range(5, 8):
        for col in range(1, 5):
            sheet.cell(row, col).border = Border(top=thin, bottom=thin, left=thin, right=thin)
    sheet.column_dimensions["A"].width = 30
    book.create_sheet("메모")["A1"] = "비고"
    book.defined_names["세율"] = openpyxl.workbook.defined_name.DefinedName(
        "세율", attr_text="견적!$F$1")
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _resolved(data: bytes) -> form_files.Resolved:
    found = form_profile.profile("견적서.xlsx", data)
    return form_files.Resolved(form_files.FormChoice("xlsx", "f1", "견적서.xlsx", "project", found),
                               data)


def test_an_excel_file_has_a_profile_with_its_sheets():
    found = form_profile.profile("견적서.xlsx", _form())
    assert found["kind"] == "xlsx" and found["sheets"] == ["견적", "메모"] and not found["macro"]
    assert form_profile.summary(found)["sheets"] == 2
    assert form_profile.kind_of("서식.xltx") == "xlsx"


def _data_book() -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["월", "매출", "비용"])
    for month in range(1, 31):
        sheet.append([f"{month}월", 1000 + month * 13, 400 + month * 7])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_a_workbook_is_told_from_data_like_a_word_or_powerpoint_file():
    # a template with guidance, filler and empty input cells is a form; a table of numbers is not
    assert form_profile.profile("견적서 양식.xlsx", _form())["verdict"] == "form"
    data = form_profile.profile("월별 매출.xlsx", _data_book())
    assert data["verdict"] == "material" and "many_numbers" in data["signals"]
    template = form_profile.profile("표.xltx", _data_book())
    assert template["verdict"] == "form" and "template_file" in template["signals"]


def test_a_form_workbook_attached_is_followed_and_a_data_one_is_material():
    form = StoredFile(user_id="u", name="견적서 양식.xlsx")
    form.form_profile = form_profile.profile(form.name, _form())
    data = StoredFile(user_id="u", name="월별 매출.xlsx")
    data.form_profile = form_profile.profile(data.name, _data_book())
    turn = form_files.choose([form, data], None, "이 자료로 견적서를 만들어 줘")
    assert turn.form_for("xlsx").file_id == form.id and turn.material == [data.id]
    # the chip decides over the look
    turn = form_files.choose([form], {form.id: "material"}, "요약해 줘")
    assert not turn.forms and turn.material == [form.id]


def test_a_workbook_that_is_not_one_is_unreadable():
    assert form_profile.profile("표.xlsx", b"x")["error"]
    assert form_profile.profile("표.xlsx", b"PK\x03\x04x")["error"]


def test_a_macro_or_old_workbook_is_refused_in_plain_words():
    for name, code in (("매크로.xlsm", "form_xlsm_refused"), ("옛날.xls", "form_xls_needs_xlsx")):
        row = StoredFile(user_id="u", name=name)
        row.form_profile = form_profile.profile(name, b"x")
        assert form_files.refusal(row) == code
    row = StoredFile(user_id="u", name="위장.xlsx")
    row.form_profile = {**form_profile.profile("a.xlsx", _form()), "macro": True}
    assert form_files.refusal(row) == "form_xlsm_refused"
    ok = StoredFile(user_id="u", name="견적서.xlsx")
    ok.form_profile = form_profile.profile(ok.name, _form())
    assert form_files.refusal(ok) is None


async def test_the_forms_route_refuses_a_macro_workbook():
    with pytest.raises(HTTPException) as refused:
        await forms_router.upload_form(
            SimpleNamespace(id="u"), None,
            file=UploadFile(file=io.BytesIO(b"PK\x03\x04"), filename="매크로.xlsm"))
    assert refused.value.detail == "form_xlsm_refused"


def test_a_new_workbook_starts_from_the_form_without_its_examples():
    title, data = sheet_form.start(_resolved(_form()))
    assert title == "견적서"
    book = openpyxl.load_workbook(io.BytesIO(data))
    sheet = book["견적"]
    # the form's own: labels, formula, merge, width, number format, fill, defined name, sheets
    assert sheet["A1"].value == "견적서" and sheet["B3"].value == "수량"
    assert sheet["D4"].value == "=B4*C4"
    assert "A1:D1" in {str(r) for r in sheet.merged_cells.ranges}
    assert sheet.column_dimensions["A"].width == 30
    assert sheet["C4"].number_format == "#,##0"
    assert sheet["A3"].fill.fgColor.rgb.endswith("DDDDDD")
    assert "메모" in book.sheetnames and "세율" in book.defined_names
    # the examples are not facts
    assert sheet["A2"].value is None and sheet["A4"].value is None
    assert sheet["B4"].value is None and sheet["C4"].value is None
    mark = sheet_form.mark_of(data)
    assert mark["name"] == "견적서.xlsx" and mark["source"] == "project"
    assert mark["cleared"] == ["견적!A2", "견적!A4:C4"] and mark["followed"] == {"cleared": 4}


def test_the_first_read_tells_the_form_its_inputs_and_what_was_cleared():
    _, data = sheet_form.start(_resolved(_form()))
    text = sheet_edit.outline(data)
    assert text.startswith("이 시트는 사람의 엑셀 양식 「견적서.xlsx」") and "[매출액]" in text
    assert "예시를 지운 칸" in text and "서식만 있는 빈 칸" in text and "A5" in text
    assert "시트 2개" in text and "A3=품목" in text


def test_the_form_mark_survives_the_sheet_ops():
    _, data = sheet_form.start(_resolved(_form()))
    done = sheet_edit.apply(data, [{"op": "set_cells", "sheet": "견적", "at": "A4",
                                    "values": [["볼펜", 3, 1200]]}])
    assert form_marks.read(done.data)["kind"] == "xlsx"
    shown = {c["r"]: c for c in sheet_model.to_model(done.data)["sheets"][0]["cells"]
             if c["c"] == 3}
    assert shown[3]["v"] == 3600


def test_a_workbook_without_a_form_reads_as_before():
    data = openpyxl.Workbook()
    out = io.BytesIO()
    data.save(out)
    assert sheet_edit.outline(out.getvalue()).startswith("시트 1개")


async def test_xlsx_write_in_a_conversation_with_a_form_opens_the_form(monkeypatch):
    form = _resolved(_form())

    async def found(_ctx):
        return form

    monkeypatch.setattr(sheet_tools, "_form_of", found)
    args = await sheet_tools._prepare({"title": "견적", "sheets": [{"name": "x", "rows": [[1]]}]},
                                      SimpleNamespace(user_id="u", session_id="s"))
    assert args["__form__"] is form and args["__notes__"]
    title, data = sheet_form.start(args["__form__"])
    assert title == "견적서" and sheet_form.mark_of(data)


def test_the_forms_own_structure_stays_and_only_examples_are_cleared():
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "계획"
    sheet.append(["연도", 2026])
    sheet.append(["세율", 0.1])
    sheet.append(["순번", "항목", "금액"])
    for number in (1, 2, 3):
        sheet.append([number, "예시 항목", 5000 * number])
    sheet["D4"] = "=C4*B2"
    sheet.append(["합계", None, "=SUM(C4:C6)"])
    out = io.BytesIO()
    book.save(out)
    _, data = sheet_form.start(_resolved(out.getvalue()))
    got = openpyxl.load_workbook(io.BytesIO(data))["계획"]
    assert got["B1"].value == 2026 or got["B1"].value is None
    assert got["B2"].value == 0.1  # a rate the formula points at, with its label beside it
    assert [got.cell(r, 1).value for r in (4, 5, 6)] == [1, 2, 3]  # the running numbers
    assert [got.cell(r, 3).value for r in (4, 5, 6)] == [None, None, None]  # the examples
    assert got["D4"].value == "=C4*B2" and got["C7"].value == "=SUM(C4:C6)"


def test_the_first_read_says_what_stays():
    _, data = sheet_form.start(_resolved(_form()))
    assert "순번·연도 머리글" in sheet_edit.outline(data)


def test_a_sheet_made_from_a_form_carries_it_for_the_followed_line():
    from app.services import sheet_doc

    _, data = sheet_form.start(_resolved(_form()))
    described = sheet_doc.describe(data, "xlsx")
    assert described["form"]["name"] == "견적서.xlsx" and described["form"]["followed"] == {
        "cleared": 4}
    assert "cleared" not in described["form"]
