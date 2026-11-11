"""Word document tools: read the conversation's Word document, edit it in place, or write a
new one (`office.py`), against the saved `.docx` (services/docx_edit.py)."""

from __future__ import annotations

from app.core.db import SessionLocal
from app.services import docx_doc
from app.services import docx_edit as edits
from app.services.tools.office import Toolset, tools_for

__all__ = ["NAMES", "TOOLS", "docx_edit", "docx_read", "docx_write"]

TOOLS = tools_for(Toolset(
    prefix="docx",
    label="Word",
    doc=docx_doc,
    edit=edits,
    error=edits.DocxError,
    # looked up per run, so a test can swap the database
    session=lambda: SessionLocal(),
    read_note=(
        " Headings and lists are marked, `p4 (heading 1): …`, `p6 (list): …`; numbers "
        "written into the text (「1.<tab>」) are text. A table with merged cells names each "
        "cell by its column, `c2~3: …`, and that start column is the `col` to give. Header and "
        "footer text (머리글, 바닥글) follows the body; fill_blank without `at` reaches it."
    ),
))
DOCX_READ, DOCX_EDIT, DOCX_WRITE = TOOLS
docx_read, docx_edit, docx_write = (tool.run for tool in TOOLS)
NAMES = frozenset(tool.name for tool in TOOLS)
