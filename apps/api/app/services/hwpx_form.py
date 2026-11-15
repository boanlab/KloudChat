"""A document written on a 한글 (HWPX) form (양식): the form itself becomes the conversation's HWP
document, and the writer fills it in place with the HWP tools (`hwpx_edit`: `set_cell` for a
value cell, `replace_text`/`insert_paragraph` under a heading, `fill_blank` for a 「[ ]」), so
the form's tables, cells, shapes and page stay exactly as they are.

The form's value cells, read when it was uploaded (`form_profile`), are what the writer is told
to fill (`rule`); after each save the document says how many of them are filled
(`progress`, read by `hwp_doc.describe` for the 「양식을 따름」 line).
"""

from __future__ import annotations

from typing import Any

from app.services import form_marks, hwpx_edit
from app.services.form_profile import _FILL

#: value cells listed for the writer at most
_LISTED = 40


def start(form: Any) -> bytes:
    """The form (`form_files.Resolved`) as a new HWP document, marked with what it follows."""
    fields = [
        {k: f[k] for k in ("table", "row", "col", "label")}
        for f in (form.profile.get("fields") or [])[:120]
    ]
    return form_marks.mark(form.data, {
        "kind": "hwpx", "name": form.choice.name, "source": form.choice.source,
        "fileId": form.choice.file_id, "fields": fields,
    })


def progress(data: bytes, mark: dict[str, Any]) -> dict[str, Any]:
    """How many of the form's value cells hold something now, and the labels still empty."""
    fields = mark.get("fields") or []
    if not fields:
        return {"cells": 0, "cellsFilled": 0, "cellsEmpty": []}
    try:
        _, doc = hwpx_edit._load(data)
    except hwpx_edit.HwpxError:
        return {"cells": len(fields), "cellsFilled": 0, "cellsEmpty": []}
    tables = {t.id: t for t in doc.tables}
    filled, empty = 0, []
    for field in fields:
        table = tables.get(field.get("table"))
        text = ""
        if table is not None:
            for r, c, cell in hwpx_edit._cells(table.element):
                if (r, c) == (field.get("row"), field.get("col")):
                    text = hwpx_edit._cell_text(cell).strip()
                    break
        if text and not _FILL.fullmatch(text) and not hwpx_edit.BLANK.fullmatch(text):
            filled += 1
        else:
            empty.append(str(field.get("label") or ""))
    return {"cells": len(fields), "cellsFilled": filled, "cellsEmpty": empty[:20]}


def rule(mark: dict[str, Any]) -> str:
    """What the writer is told about the 한글 form open beside the chat."""
    fields = mark.get("fields") or []
    lines = [
        f"The open HWP document is the person's own form 「{mark.get('name', '')}」. Fill it in "
        "place; never replace it with hwp_write. Read it with hwp_read, then hwp_edit:",
        "- a value cell next to its label: set_cell on that cell (table, row, col); keep the "
        "label cell as it is;",
        "- text under a heading and any guidance (「※ 작성요령」, 「작성 예시」, sample lines): "
        "replace that guidance with the content (replace_text, insert_paragraph after the "
        "heading, delete_paragraph for the guidance left over);",
        "- a 「[ ]」 blank: fill_blank.",
        "Take facts only from the person's request and material, never from the form's own "
        "sample text (「홍길동」, 「2025.00.00」). A value the material does not give stays a "
        "bracket blank such as 「[담당자]」; say which ones are left.",
    ]
    if fields:
        listed = ", ".join(f"{f['label']}({f['table']} r{f['row']} c{f['col']})"
                           for f in fields[:_LISTED])
        lines.append(f"Value cells of this form: {listed}")
    return "\n".join(lines)
