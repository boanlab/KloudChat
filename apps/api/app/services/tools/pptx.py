"""PowerPoint document tools: read the conversation's `.pptx`, edit it in place, or write a
new deck (`office.py`), against the saved file (services/pptx_edit.py)."""

from __future__ import annotations

from typing import Any

from app.core.db import SessionLocal
from app.services import pptx_doc, pptx_ops
from app.services import pptx_edit as edits
from app.services.tools.office import Toolset, tools_for

__all__ = ["NAMES", "TOOLS", "pptx_edit", "pptx_read", "pptx_write"]

_OP_HELP = (
    "replace_text {id:'s2.3', text} · fill_blank {blank, value, at?} · "
    "set_cell {table:'s3.2', row, col, text} · set_notes {slide:'s2', text} · "
    "insert_table_row {table, after} · delete_table_row {table, row} · "
    "add_slide {after:'s3', title, body, like?} · duplicate_slide {id:'s2', after?} · "
    "delete_slide {id} · move_slide {id, after} ('s0' = to the front) · "
    "insert_table {slide, rows, replace?} · insert_table_column {table, after} · "
    "delete_table_column {table, col} · insert_picture {slide, file, replace?} · "
    "replace_picture {id, file} · change_layout {slide, layout}"
)


def _build(args: dict[str, Any]) -> tuple[str, bytes]:
    title = " ".join(str(args.get("title") or "").split())[:120]
    raw = args.get("slides")
    slides = [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []
    if not title or not slides:
        raise ValueError("title과 slides(한 장 이상)가 있어야 합니다.")
    if len(slides) > pptx_doc.MAX_WRITE:
        raise ValueError(f"slides는 {pptx_doc.MAX_WRITE}장까지입니다.")
    for index, slide in enumerate(slides, 1):
        if not str(slide.get("title") or "").strip():
            raise ValueError(f"slides[{index}]에 title이 없습니다.")
        rows = slide.get("rows")
        if rows is not None and not (
            isinstance(rows, list) and all(isinstance(r, list) for r in rows)
        ):
            raise ValueError(f"slides[{index}].rows는 [[머리 칸, …], [값, …]] 꼴입니다.")
    subtitle = " ".join(str(args.get("subtitle") or "").split())[:200]
    closing = " ".join(str(args.get("closing") or "").split())[:80]
    return title, pptx_doc.deck(title, subtitle, slides, closing)


TOOLS = tools_for(Toolset(
    prefix="pptx",
    label="PowerPoint",
    doc=pptx_doc,
    edit=edits,
    error=edits.PptxError,
    # looked up per run, so a test can swap the database
    session=lambda: SessionLocal(),
    ops=edits.OPS + pptx_ops.OPS,
    op_help=_OP_HELP,
    renumbers=edits.RENUMBERS | pptx_ops.RENUMBERS,
    read_text=(
        "Read the PowerPoint deck open beside this chat, as the person last saved it in the "
        "editor. Returns each slide `── s2 (레이아웃: …) ──` and its shapes `s2.1 (제목): …`, "
        "`s2.2 (본문), 3줄:` with one indented line per paragraph, tables `s3.2 (표 4×3)` with "
        "rows `r0: a | b` (row and column count from 0), charts, pictures and SmartArt (read "
        "only), the speaker notes `s2 메모: …`, and the bracket blanks still to fill "
        "(「[담당자]」). Hidden shapes are not listed. Call it before every pptx_edit: the "
        "numbers belong to this version."
    ),
    edit_text=(
        "Edit the open PowerPoint deck in place; each paragraph's level and bullets and each "
        "run's font, size and colour stay where the text stays. All ops apply together or none "
        "do, against the numbers of the last pptx_read. Ops: replace_text {id:'s2.2', text} "
        "replaces a shape's whole text, one line per paragraph (lines that stay keep their "
        "format; give every line you keep); fill_blank {blank:'[담당자]', value:'김민수'} fills "
        "every place that blank appears, or only at:'s2', 's2.3', 's3.2 r1c2' or 's2 메모'; "
        "set_cell {table:'s3.2', row, col, text}; set_notes {slide:'s2', text} replaces the "
        "speaker notes; insert_table_row {table, after}; delete_table_row {table, row}; "
        "add_slide {after:'s3', title, body} adds a slide shaped like slide `after` (or "
        "like:'s2'), body one line per bullet; duplicate_slide {id, after?}; delete_slide "
        "{id}; move_slide {id, after} (after:'s0' moves it to the front). "
        "「5장」 means s5, the fifth slide in order, not a number printed on a slide. "
        "insert_table {slide:'s4', rows: [['구분','값'], ['…','…']], replace:'s4.2'} puts a "
        "real table on the slide (in place of shape s4.2 when given; a request to show a "
        "slide 「as a table」 is this, never table-like lines of text); insert_table_column "
        "{table:'s3.2', after: col}; delete_table_column {table, col}; insert_picture "
        "{slide, file, replace?} and replace_picture {id:'s2.4', file} with `file` an image "
        "the person attached or a tool made in this conversation (its name or id); "
        "change_layout {slide, layout} moves a slide to another layout pptx_read lists, its "
        "content kept. Charts and SmartArt cannot be edited, and a chart cannot be made: say "
        "so. Put only facts the person gave; anything still unknown stays a bracket blank "
        "like 「[담당자]」."
    ),
    edit_items={
        "type": "object",
        # where an op acts, then what it puts there: a model that writes the keys in this
        # order (DeepSeek) otherwise ends a set_cell at `col`, without its text
        "properties": {
            "op": {"type": "string", "enum": list(edits.OPS + pptx_ops.OPS)},
            "id": {"type": "string"},
            "slide": {"type": "string"},
            "table": {"type": "string"},
            "row": {"type": "integer"},
            "col": {"type": "integer"},
            "after": {"type": "string"},
            "like": {"type": "string"},
            "at": {"type": "string"},
            "blank": {"type": "string"},
            "text": {"type": "string"},
            "value": {"type": "string"},
            "title": {"type": "string"},
            "body": {"type": "string"},
            "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
            "replace": {"type": "string"},
            "file": {"type": "string"},
            "layout": {"type": "string"},
        },
        "required": ["op"],
    },
    write_text=(
        "Write a new PowerPoint deck and open it in the editor beside the chat: a cover with "
        "title and subtitle, then one slide per entry of `slides`, each {title, bullets: [...]} "
        "or {title, rows: [[header, …], [value, …]]} for a table, with optional notes "
        "(speaker notes); `closing` adds a last slide with that line. Use it only when the "
        "person asks for a new PowerPoint file; to change the open deck use pptx_read and "
        "pptx_edit. Bullets are short lines, three to five a slide. Never invent names, dates, "
        "places or numbers: write a bracket blank named for what goes there, such as "
        "「[담당자]」, 「[YYYY. MM. DD.]」, 「[매출액]」, instead; and add no slides the request "
        "does not call for."
    ),
    write_params={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "subtitle": {"type": "string", "description": "부서·발표자·날짜 줄; 모르면 빈칸"},
            "slides": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "bullets": {"type": "array", "items": {"type": "string"}},
                        "rows": {
                            "type": "array",
                            "items": {"type": "array", "items": {"type": "string"}},
                        },
                        "notes": {"type": "string"},
                    },
                    "required": ["title"],
                },
            },
            "closing": {"type": "string"},
        },
        "required": ["title", "slides"],
    },
    build=_build,
))
PPTX_READ, PPTX_EDIT, PPTX_WRITE = TOOLS
pptx_read, pptx_edit, pptx_write = (tool.run for tool in TOOLS)
NAMES = frozenset(tool.name for tool in TOOLS)
