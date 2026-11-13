"""A Word cell edit changes the cell's text and keeps the rest of the cell: its other
paragraphs, each run's format, and a nested table; only an asked-for whole replacement clears
the cell."""

from __future__ import annotations

import io

from docx import Document

from app.services import docx_edit


def _document() -> bytes:
    doc = Document()
    doc.add_paragraph("안내")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "구분"
    table.cell(0, 1).text = "내용"
    table.cell(1, 0).text = "일정"
    cell = table.cell(1, 1)
    first = cell.paragraphs[0]
    first.add_run("담당: ").bold = True
    first.add_run("김민수")
    cell.add_paragraph("둘째 줄")
    inner = cell.add_table(rows=1, cols=2)
    inner.cell(0, 0).text = "안쪽"
    inner.cell(0, 1).text = "표"
    cell.add_paragraph("")
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _cell(data: bytes):
    return Document(io.BytesIO(data)).tables[0].cell(1, 1)


def test_editing_one_line_of_a_cell_keeps_its_runs_paragraphs_and_nested_table():
    data = _document()
    edited = docx_edit.apply(data, [{"op": "set_cell", "table": "t1", "row": 1, "col": 1,
                                     "text": "담당: 이영희\n둘째 줄\n"}]).data
    cell = _cell(edited)
    runs = [(r.text, bool(r.bold)) for r in cell.paragraphs[0].runs]
    assert runs == [("담당: ", True), ("이영희", False)]
    assert [p.text for p in cell.paragraphs][:2] == ["담당: 이영희", "둘째 줄"]
    assert len(cell.tables) == 1 and cell.tables[0].cell(0, 0).text == "안쪽"


def test_only_a_whole_replacement_clears_the_cell():
    data = _document()
    edited = docx_edit.apply(data, [{"op": "set_cell", "table": "t1", "row": 1, "col": 1,
                                     "text": "새 칸", "whole": True}]).data
    cell = _cell(edited)
    assert [p.text for p in cell.paragraphs] == ["새 칸"] and not cell.tables
