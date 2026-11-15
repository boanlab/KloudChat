"""PowerPoint document tools: read the conversation's `.pptx`, edit it in place, or write a
new deck (`office.py`), against the saved file (services/pptx_edit.py)."""

from __future__ import annotations

import json
from typing import Any

from app.core.db import SessionLocal
from app.services import deck_export, pptx_doc, pptx_ops
from app.services import pptx_edit as edits
from app.services.tools.office import Toolset, tools_for

__all__ = ["NAMES", "TOOLS", "pptx_edit", "pptx_read", "pptx_restore", "pptx_write"]

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


async def _look(db: Any, ctx: Any) -> tuple[dict | None, str, Any] | None:
    """The deck's theme and slides template: the conversation's pick, else the project's
    (`pptx_doc.project_look`, where the slides surface defines it); and the conversation's
    PowerPoint form (`form_files.for_session`), which a written deck follows instead."""
    project_look = getattr(pptx_doc, "project_look", None)
    if project_look is None:
        return None
    from app.models.chat import ChatSession
    from app.models.user import User

    user = await db.get(User, ctx.user_id)
    session = await db.get(ChatSession, ctx.session_id) if ctx.session_id else None
    if user is None:
        return None
    tokens, template = await project_look(db, user, session)
    from app.services import form_files

    form = await form_files.for_session(db, session, "pptx") if session is not None else None
    return tokens, template, form


def _slides_of(args: dict[str, Any]) -> list[dict[str, Any]]:
    """`slides` as a list of objects; some models send the array as a JSON string."""
    raw = args.get("slides")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []


def _chart_problem(index: int, chart: Any) -> str:
    """Why `chart` cannot be drawn, or ``."""
    if not isinstance(chart, dict):
        return f"slides[{index}].chart는 객체입니다."
    categories = chart.get("categories")
    series = chart.get("series")
    if not isinstance(categories, list) or len(categories) < 2:
        return (f"slides[{index}].chart.categories에 항목이 두 개 이상 있어야 합니다"
                "(예: 1분기, 2분기).")
    if not isinstance(series, list) or not series or not all(
            isinstance(item, dict) and isinstance(item.get("values"), list) for item in series):
        return f"slides[{index}].chart.series는 [{{name, values: [수, …]}}, …] 꼴입니다."
    for item in series:
        if len(item["values"]) > len(categories):
            return (f"slides[{index}].chart의 「{item.get('name')}」 값이 categories보다 "
                    "많습니다.")
        numbers = [v for v in item["values"] if isinstance(v, int | float)]
        if not numbers:
            return (f"slides[{index}].chart의 「{item.get('name')}」에 수가 없습니다. 자료에 "
                    "있는 값만 넣고, 아직 없는 값(집계 중)은 null로 두세요.")
    return ""


def _build(args: dict[str, Any], look: Any = None) -> tuple[str, bytes]:
    """A new deck from the arguments, in `look` (`_look`: theme tokens and template) when
    `pptx_doc.deck` takes one."""
    title = " ".join(str(args.get("title") or "").split())[:120]
    slides = _slides_of(args)
    if not title or not slides:
        raise ValueError("title과 slides(한 장 이상)가 있어야 합니다.")
    if len(slides) > pptx_doc.MAX_WRITE:
        raise ValueError(f"slides는 {pptx_doc.MAX_WRITE}장까지입니다.")
    width, height = deck_export.table_room()
    for index, slide in enumerate(slides, 1):
        if not str(slide.get("title") or "").strip():
            raise ValueError(f"slides[{index}]에 title이 없습니다.")
        rows = slide.get("rows")
        if rows is not None and not (
            isinstance(rows, list) and all(isinstance(r, list) for r in rows)
        ):
            raise ValueError(f"slides[{index}].rows는 [[머리 칸, …], [값, …]] 꼴입니다.")
        if slide.get("chart") is not None and (problem := _chart_problem(index, slide["chart"])):
            raise ValueError(problem)
        if rows and not slide.get("chart"):
            cells = [[str(cell) for cell in row] for row in rows if row]
            _size, _heights, fits = deck_export.table_fit(cells, width, height)
            if not fits:
                # a table that runs past the slide even at the smallest type: the writer
                # splits it or shortens its cells, within the slide count asked
                raise ValueError(
                    f"slides[{index}]의 표가 가장 작은 글자로도 슬라이드를 넘칩니다"
                    f"({len(cells)}행). 칸의 글을 짧게 줄이거나(한 칸 한두 줄) 행을 줄이세요.")
    subtitle = " ".join(str(args.get("subtitle") or "").split())[:200]
    closing = " ".join(str(args.get("closing") or "").split())[:80]
    if isinstance(look, tuple) and _takes_look():
        tokens, template, form = (*look, None)[:3]
        extra = {"form": form} if form is not None else {}
        return title, pptx_doc.deck(title, subtitle, slides, closing, tokens=tokens,
                                    template=template or "", **extra)
    return title, pptx_doc.deck(title, subtitle, slides, closing)


def _total(args: dict[str, Any]) -> int:
    """Slides the file will have: the cover, one per entry, and the closing slide if any."""
    closing = 1 if str(args.get("closing") or "").strip() else 0
    return 1 + len(_slides_of(args)) + closing


def count_problem(request: str, args: dict[str, Any]) -> str:
    """Why the deck the arguments make is not the size the person asked (「8장」, 「10~12장」,
    「5장 정도」), counting every slide in the file (cover and closing too, as the person and
    PowerPoint count them), or ``."""
    from app.services import deck

    total = _total(args)
    closing = 1 if str(args.get("closing") or "").strip() else 0
    exact = deck.requested_slides(request)
    span = None if exact else deck.slide_range(request)
    about = None if exact or span else deck.about_slides(request)
    if exact and total != exact:
        want = f"{exact}장"
        body = exact - 1 - closing
    elif span and not span[0] <= total <= span[1]:
        want = f"{span[0]}~{span[1]}장"
        body = max(span[0], min(total, span[1])) - 1 - closing
    elif about and abs(total - about) > 1:
        want = f"{about}장 정도"
        body = about - 1 - closing
    else:
        return ""
    return (f"요청은 슬라이드 {want}인데 이 인자로는 파일이 {total}장(표지 1"
            + (f" + 본문 {total - 1 - closing} + 마지막 장 1" if closing
               else f" + 본문 {total - 1}")
            + f")이 됩니다. 표지와 마지막 장도 장 수에 들어갑니다: slides를 {max(body, 1)}개로 "
            "맞춰 다시 쓰세요.")


async def _prepare(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """The arguments checked against the turn and completed: the slide count the person
    asked, and each slide's `image.file` (a picture the person attached, or a figure
    `pdf_figures` cut from a project paper) read into the slide."""
    args = {**args, "slides": _slides_of(args)}
    if problem := count_problem(getattr(ctx, "request", "") or "", args):
        raise ValueError(problem)
    wanted = [str((slide.get("image") or {}).get("file") or "").strip()
              for slide in args["slides"] if isinstance(slide.get("image"), dict)]
    wanted = [name for name in wanted if name]
    if not wanted:
        return args
    from app.services.tools.office import _pictures

    async with SessionLocal() as db:
        found = await _pictures(db, ctx, [{"op": "insert_picture", "file": name}
                                          for name in wanted])
    slides = []
    for index, slide in enumerate(args["slides"], 1):
        image = slide.get("image")
        if isinstance(image, dict) and (name := str(image.get("file") or "").strip()):
            if name not in found:
                raise ValueError(
                    f"slides[{index}].image.file 「{name}」 그림을 찾지 못했습니다. 이 대화에 "
                    "올린 그림이나 pdf_figures가 만든 그림의 이름(또는 id)을 쓰세요.")
            slide = {**slide, "image": {**image, "data": found[name][1]}}
        slides.append(slide)
    return {**args, "slides": slides}


def _takes_look() -> bool:
    import inspect

    return "tokens" in inspect.signature(pptx_doc.deck).parameters


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
        "content kept. Charts and SmartArt cannot be edited here; a new chart is made by "
        "writing the deck again with pptx_write (replace: true) with a slide's `chart`. Put "
        "only facts the person gave; anything still unknown stays a bracket blank "
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
        "pptx_edit. Bullets are short lines, three to five a slide. "
        "A chart goes IN the file as {title, chart: {kind: 'bar'|'line'|'stacked'|'hbar'|"
        "'pie', unit, categories: ['1분기', …], series: [{name, values: [1820, …]}]}, "
        "bullets?}: a native, editable PowerPoint chart. When the person asks for a chart and "
        "gave data (a spreadsheet, a CSV, a table in the material), build it from those exact "
        "numbers; a value the source does not have yet (「집계 중」, 미확정) is null, never 0 or "
        "an estimate, and a bullet says why it is missing. create_chart draws a separate chart "
        "beside the chat, NOT in this file. A picture goes in as {title, image: {file, "
        "caption}, bullets?} with `file` the name or id of an image in this conversation (one "
        "the person attached, or a figure pdf_figures cut from a project paper); caption it "
        "with the source's own caption (「Figure 2. … (Kwon et al., 2023)」). "
        "The slide count the person asked is the whole file: the cover and the closing slide "
        "count (「8장」 = cover + 7, or cover + 6 + closing). Tables: at most about six rows of "
        "short cells a slide; split a longer one over two slides. The tool reports what the "
        "saved file holds (slides, charts, tables, pictures): say only that. Never invent names, "
        "dates, places or numbers: write a bracket blank named for what goes there, such as "
        "「[담당자]」, 「[YYYY. MM. DD.]」, 「[매출액]」, instead; never invent results, workloads "
        "or figures a paper does not have; and add no slides the request does not call for."
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
                        "chart": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string",
                                         "enum": ["bar", "line", "stacked", "hbar", "pie"]},
                                "unit": {"type": "string"},
                                "categories": {"type": "array", "items": {"type": "string"}},
                                "series": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "values": {"type": "array",
                                                       "items": {"type": ["number", "null"]}},
                                        },
                                        "required": ["name", "values"],
                                    },
                                },
                            },
                            "required": ["categories", "series"],
                        },
                        "image": {
                            "type": "object",
                            "properties": {"file": {"type": "string"},
                                           "caption": {"type": "string"}},
                            "required": ["file"],
                        },
                    },
                    "required": ["title"],
                },
            },
            "closing": {"type": "string"},
        },
        "required": ["title", "slides"],
    },
    build=_build,
    look_for=_look,
    prepare=_prepare,
    contents=edits.contents,
))
PPTX_READ, PPTX_EDIT, PPTX_WRITE, PPTX_RESTORE = TOOLS
pptx_read, pptx_edit, pptx_write, pptx_restore = (tool.run for tool in TOOLS)
NAMES = frozenset(tool.name for tool in TOOLS)
