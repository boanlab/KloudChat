"""HWP document tools: read the conversation's HWP document, edit it in place, or write a
new one (`office.py`), against the saved HWPX (services/hwpx_edit.py)."""

from __future__ import annotations

from app.core.db import SessionLocal
from app.services import hwp_doc, hwpx_edit
from app.services.office_edit import OPS
from app.services.tools.office import _OP_HELP, FORMATS, Toolset, tools_for

__all__ = ["FORMATS", "NAMES", "TOOLS", "hwp_edit", "hwp_read", "hwp_restore", "hwp_write"]

TOOLS = tools_for(Toolset(
    prefix="hwp",
    label="HWP",
    doc=hwp_doc,
    edit=hwpx_edit,
    error=hwpx_edit.HwpxError,
    ops=(*OPS, "fill_fields"),
    op_help=_OP_HELP + " · fill_fields {values:[{label, text, table?}]} (a 한글 form only)",
    edit_extra=(
        " On a 한글 form (the open document follows the person's form): fill_fields "
        "{values:[{label:'①상호', text:'…'}, …]} writes several values at once, each next to the "
        "label printed on the form (a distinctive part of it; table when a label repeats); the "
        "form's own lines stay, and what could not be written is listed in the result."
    ),
    # looked up per run, so a test can swap the database
    session=lambda: SessionLocal(),
    unconverted=(
        "이 문서는 아직 .hwp 원본입니다. 편집기에서 열리면 HWPX로 바뀌어 저장되니, "
        "사람에게 문서 패널을 열어 달라고 한 뒤 다시 읽으세요."
    ),
))
HWP_READ, HWP_EDIT, HWP_WRITE, HWP_RESTORE = TOOLS
hwp_read, hwp_edit, hwp_write, hwp_restore = (tool.run for tool in TOOLS)
NAMES = frozenset(tool.name for tool in TOOLS)
