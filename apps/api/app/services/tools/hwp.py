"""HWP document tools: read the conversation's HWP document, edit it in place, or write a
new one (`office.py`), against the saved HWPX (services/hwpx_edit.py)."""

from __future__ import annotations

from app.core.db import SessionLocal
from app.services import hwp_doc, hwpx_edit
from app.services.tools.office import FORMATS, Toolset, tools_for

__all__ = ["FORMATS", "NAMES", "TOOLS", "hwp_edit", "hwp_read", "hwp_write"]

TOOLS = tools_for(Toolset(
    prefix="hwp",
    label="HWP",
    doc=hwp_doc,
    edit=hwpx_edit,
    error=hwpx_edit.HwpxError,
    # looked up per run, so a test can swap the database
    session=lambda: SessionLocal(),
    unconverted=(
        "이 문서는 아직 .hwp 원본입니다. 편집기에서 열리면 HWPX로 바뀌어 저장되니, "
        "사람에게 문서 패널을 열어 달라고 한 뒤 다시 읽으세요."
    ),
))
HWP_READ, HWP_EDIT, HWP_WRITE = TOOLS
hwp_read, hwp_edit, hwp_write = (tool.run for tool in TOOLS)
NAMES = frozenset(tool.name for tool in TOOLS)
