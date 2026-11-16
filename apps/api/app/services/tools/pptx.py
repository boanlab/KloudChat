"""PowerPoint document tools: read the conversation's `.pptx`, edit it in place, or write a
new deck (`office.py`), against the saved file (services/pptx_edit.py)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlmodel import or_, select

from app.core.db import SessionLocal
from app.models.workspace import StoredFile
from app.services import deck_export, deck_numbers, pptx_doc, pptx_ops
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


log = logging.getLogger(__name__)

def _placeholder_picture() -> bytes:
    """A 4:3 grey picture standing in for a slide's figure while its layout is checked."""
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (400, 300), "#cccccc").save(out, format="PNG")
    return out.getvalue()


#: what `_prepare` tells the model beside the saved file (`office.write`)
NOTES = "__notes__"


def _slides_of(args: dict[str, Any]) -> list[dict[str, Any]]:
    """`slides` as a list of objects; some models send the array as a JSON string."""
    raw = args.get("slides")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []


def _number(value: Any) -> float | None:
    """A chart value as a number: 1820, 「1,820」, 「2,080백만」 → 2080.0; 「집계 중」, null →
    None (a gap). Models send numbers as strings as often as not."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    found = re.fullmatch(r"\s*(-?[\d,]+(?:\.\d+)?)\s*[^\d\s]{0,6}\s*", str(value or ""))
    if not found:
        return None
    try:
        return float(found.group(1).replace(",", ""))
    except ValueError:
        return None


def _chart_values(chart: dict) -> dict:
    """`chart` with every value a number or None, so what is drawn is what was checked."""
    series = [{**item, "values": [_number(v) for v in item.get("values") or []]}
              for item in chart.get("series") or [] if isinstance(item, dict)]
    return {**chart, "series": series}


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
        numbers = [v for v in item["values"] if _number(v) is not None]
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


def draft_args(args: dict[str, Any]) -> dict[str, Any] | None:
    """The arguments of a draft made from a `pptx_write` call the model could not get right
    (`Tool.draft`): every slide that has a title kept, a chart that cannot be drawn or a
    table that does not fit written as lines of text, a missing title filled with the slide's
    number. Nothing is invented: a slide the arguments do not have stays missing. None when
    the call holds no slide at all."""
    slides = _slides_of(args)
    if not slides:
        return None
    width, height = deck_export.table_room()
    fixed: list[dict[str, Any]] = []
    for index, slide in enumerate(slides[:pptx_doc.MAX_WRITE], 1):
        slide = dict(slide)
        bullets = slide.get("bullets")
        if isinstance(bullets, str):
            bullets = [line.strip(" -•\t") for line in bullets.splitlines() if line.strip()]
        slide["bullets"] = [str(b) for b in bullets] if isinstance(bullets, list) else []
        if not str(slide.get("title") or "").strip():
            slide["title"] = f"슬라이드 {index}"
        if slide.get("chart") is not None and _chart_problem(index, slide["chart"]):
            slide.pop("chart", None)
        rows = slide.get("rows")
        if rows is not None:
            usable = isinstance(rows, list) and all(isinstance(r, list) for r in rows)
            cells = [[str(c) for c in row] for row in rows if row] if usable else []
            if not cells or slide.get("chart"):
                slide.pop("rows", None)
            elif not deck_export.table_fit(cells, width, height)[2]:
                # a table too long for the slide even at the smallest type: its lines
                slide.pop("rows", None)
                slide["bullets"] += [" | ".join(row) for row in cells][:6]
        if not isinstance(slide.get("image"), dict):
            slide.pop("image", None)
        fixed.append(slide)
    title = " ".join(str(args.get("title") or "").split())[:120] or fixed[0]["title"]
    return {"title": title, "subtitle": str(args.get("subtitle") or ""),
            "closing": str(args.get("closing") or ""), "slides": fixed}


def _overflow_problem(args: dict[str, Any]) -> str:
    """Which slides' text runs past its place even at the smallest type, laid out as the file
    will be (`deck_export.overflowing_slides`), or ``: a slide of long bullets beside a
    figure, or five long questions, otherwise ends over the footer."""
    found: list[dict] = []
    slides = [{key: value for key, value in slide.items() if key != "image"}
              | ({"image": {"data": slide["image"].get("data") or _placeholder_picture()}}
                 if isinstance(slide.get("image"), dict) else {})
              for slide in args["slides"]]
    try:
        pptx_doc.deck(str(args.get("title") or "t"), str(args.get("subtitle") or ""), slides,
                      str(args.get("closing") or ""), overflow=found)
    except Exception:  # noqa: BLE001 — the layout check never stops a write; _build reports
        return ""
    if not found:
        return ""
    numbers = sorted({int(item.get("index", 0)) + 1 for item in found})
    return ("슬라이드 " + ", ".join(f"{n}장" for n in numbers) + "의 글이 가장 작은 글자로도 "
            "자리를 넘칩니다: 줄마다 짧게 줄이거나(한 줄 40자 안팎) 줄 수를 줄이고, 그림 옆 "
            "글은 세 줄 이하로, 많으면 두 장으로 나누세요(요청한 장 수 안에서).")


def _total(args: dict[str, Any]) -> int:
    """Slides the file will have: the cover, one per entry, and the closing slide if any."""
    closing = 1 if str(args.get("closing") or "").strip() else 0
    return 1 + len(_slides_of(args)) + closing


def count_problem(request: str, args: dict[str, Any], *, slack: int = 0) -> str:
    """Why the deck the arguments make is not the size the person asked (「8장」, 「10~12장」,
    「5장 정도」), counting every slide in the file (cover and closing too, as the person and
    PowerPoint count them), or ``."""
    from app.services import deck

    total = _total(args)
    closing = 1 if str(args.get("closing") or "").strip() else 0
    exact = deck.requested_slides(request)
    span = None if exact else deck.slide_range(request)
    about = None if exact or span else deck.about_slides(request)
    if exact and abs(total - exact) > slack:
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


async def _source_texts(ctx: Any) -> list[str]:
    """The material a deck's numbers are held to: the text of the conversation's and the
    project's files, and what the turn's tools returned (figure captions, passages)."""
    texts = [str(item) for item in [*getattr(ctx, "outputs", []), *getattr(ctx, "searched", [])]
             if isinstance(item, str)]
    texts.append(str(getattr(ctx, "request", "") or ""))
    scope = []
    if getattr(ctx, "session_id", ""):
        scope.append(StoredFile.session_id == ctx.session_id)
    if getattr(ctx, "project_id", ""):
        scope.append(StoredFile.project_id == ctx.project_id)
    if scope:
        try:
            async with SessionLocal() as db:
                rows = (await db.exec(
                    select(StoredFile.text).where(StoredFile.user_id == ctx.user_id, or_(*scope))
                )).all()
        except Exception:  # noqa: BLE001 — a check on numbers never stops a write
            log.info("pptx_write: source texts not read", exc_info=True)
            rows = []
        texts += [str(row) for row in rows if row]
    return texts


def _promote_numbers(slides: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[int]]:
    """A picture slide whose figures (numbers with a unit) are only in its speaker notes gets
    up to two of those short note lines on the slide: a result shown as a picture alone
    leaves the audience without its numbers. The notes keep them."""
    out: list[dict[str, Any]] = []
    promoted: list[int] = []
    for index, slide in enumerate(slides, 1):
        notes = str(slide.get("notes") or "")
        bullets = [str(b) for b in slide.get("bullets") or [] if str(b).strip()]
        if (isinstance(slide.get("image"), dict) and notes and not slide.get("rows")
                and not slide.get("chart") and not deck_numbers.claims([{"bullets": bullets}])):
            lines = [seg.strip(" -•\t") for seg in re.split(r"(?<=[.!?。다요])\s+|\n+", notes)]
            lines = [seg for seg in lines if 0 < len(seg) <= 90 and deck_numbers.claims(
                [{"bullets": [seg]}])]
            if lines:
                slide = {**slide, "bullets": [*bullets, *lines[:2]]}
                promoted.append(index)
        out.append(slide)
    return out, promoted


async def _prepare(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """The arguments checked against the turn and completed: the slide count the person
    asked, and each slide's `image.file` (a picture the person attached, or a figure
    `pdf_figures` cut from a project paper) read into the slide. A draft the server saves
    (`__draft__`) is only completed, never sent back."""
    draft = args.get("__draft__") is True
    slides = _slides_of(args)
    args = {**args, "slides": [
        {**slide, "chart": _chart_values(slide["chart"])}
        if isinstance(slide.get("chart"), dict) else slide for slide in slides]}
    if not args["slides"]:
        raw = args.get("slides")
        log.info("pptx_write slides unreadable: %s of %d chars; arguments %s", type(raw).__name__,
                 len(str(raw or "")), {key: (type(value).__name__, len(str(value)))
                                       for key, value in args.items()})
        # an empty list is what a tool-call parser hands over when the model's array was not
        # valid JSON (an unquoted 「집계 중」 among numbers, a trailing comma)
        raise ValueError("slides가 빈 배열로 왔습니다. 보낸 배열이 올바른 JSON이 아니면 이렇게 "
                         "됩니다: 글자는 모두 큰따옴표로 감싸고, 차트의 없는 값은 null로(「집계 "
                         "중」 같은 글자를 수 자리에 쓰지 말고), 끝에 쉼표를 남기지 말고 "
                         "[{title, bullets: [...]}, …] 꼴로 다시 보내세요. 글이 아주 길면 줄이세요"
                         "(슬라이드당 줄 3~5개).")
    record = getattr(ctx, "pptx", None)
    record = record if isinstance(record, dict) else {}
    asked = getattr(ctx, "asked", "") or getattr(ctx, "request", "") or ""
    # the person's own words come first; material the turn appended after them can hide the
    # count from the instruction scope, so the opening paragraph is read on its own as well
    opening = asked.strip().split("\n\n", 1)[0]
    notes: list[str] = []
    # numbers against the material (once refused, then said): range ends and numbers no
    # source text has
    found = deck_numbers.Findings([], [])
    if not draft:
        found = deck_numbers.check(args["slides"], await _source_texts(ctx))
    number_lines = found.lines()
    if count_problem(asked, args) or (
            count_problem(opening, args) if opening != asked.strip() else ""):
        # one slide off (a closing slide added) is said, not sent back: each send-back costs
        # a small model a turn, and a long paper deck ran out of turns with no file at all
        notes.append(f"장 수가 요청과 한 장 다릅니다: 파일은 {_total(args)}장입니다. 답에 "
                     "실제 장 수를 그대로 말하세요.")
    if problem := count_problem(asked, args, slack=1) or (
            count_problem(opening, args, slack=1) if opening != asked.strip() else ""):
        notes = [note for note in notes if not note.startswith("장 수가 요청과 한 장")]
        if draft or record.get("count_refused"):
            # asked once already (or a draft): a deck of another size beats no deck; the
            # reply says the size
            notes.append(f"장 수가 요청과 다릅니다: 파일은 {_total(args)}장입니다. 답에 실제 장 "
                         "수를 그대로 말하세요.")
        else:
            record["count_refused"] = True
            if number_lines and not record.get("numbers_refused"):
                # one send-back for both, not two
                record["numbers_refused"] = True
                problem += "\n" + "\n".join(number_lines)
            raise ValueError(problem)
    if number_lines:
        if not record.get("numbers_refused") and not record.get("count_refused"):
            record["numbers_refused"] = True
            raise ValueError("\n".join(number_lines) + "\n고친 값으로 pptx_write를 다시 보내세요"
                             "(자료에 있는 수치만; 그대로 두려면 같은 인자를 한 번 더 보내면 "
                             "저장됩니다).")
        notes.append("저장은 했지만 " + " ".join(number_lines) + " 고치려면 pptx_read 뒤 "
                     "pptx_edit로 그 줄만 고치거나, 답에 그 수치가 자료에 없다고 밝히세요.")
    if problem := _overflow_problem(args):
        # said, not refused: a long deck sent back for its length as well as its count ran a
        # small model out of turns with no file at all; the writer may rewrite (replace: true)
        notes.append(problem + " 다시 쓰려면 replace: true로 고쳐 쓰고, 아니면 답에 그 "
                     "슬라이드를 줄여야 한다고 말하세요.")
    args["slides"], promoted = _promote_numbers(args["slides"])
    if promoted:
        notes.append("그림만 있던 슬라이드 " + ", ".join(f"{n}장" for n in promoted) + "에 메모의 "
                     "수치 줄을 슬라이드 본문으로 올렸습니다: 결과의 수치는 bullets에 써야 "
                     "합니다(메모에만 두지 마세요).")
    wanted = [str((slide.get("image") or {}).get("file") or "").strip()
              for slide in args["slides"] if isinstance(slide.get("image"), dict)]
    wanted = [name for name in wanted if name]
    if not wanted:
        return {**args, NOTES: notes}
    from app.services.tools.office import _pictures

    async with SessionLocal() as db:
        found_pictures = await _pictures(db, ctx, [{"op": "insert_picture", "file": name}
                                                   for name in wanted])
    slides = []
    missing = []
    for index, slide in enumerate(args["slides"], 1):
        image = slide.get("image")
        if isinstance(image, dict) and (name := str(image.get("file") or "").strip()):
            if name not in found_pictures:
                # the slide is written without it (one unknown picture must not lose the
                # deck), and the result says which picture is not in the file
                missing.append(f"slides[{index}] 「{name}」")
                slide = {key: value for key, value in slide.items() if key != "image"}
            else:
                slide = {**slide, "image": {**image, "data": found_pictures[name][1]}}
        slides.append(slide)
    if missing:
        notes.append(f"그림을 찾지 못해 넣지 않은 곳: {', '.join(missing)}. 이 그림들은 파일에 "
                     "없으니 넣었다고 말하지 마세요(pdf_figures로 잘라 온 그림 이름만 쓸 수 "
                     "있습니다).")
    # a wide figure keeps three lines under it; lines with figures that did not fit are
    # in the speaker notes only: said, so the writer splits the slide
    for index, slide in enumerate(slides, 1):
        data = (slide.get("image") or {}).get("data") if isinstance(slide.get("image"), dict) \
            else None
        bullets = [str(b) for b in slide.get("bullets") or [] if str(b).strip()]
        if data and bullets and pptx_doc._wide(data):
            _kept, moved = pptx_doc.strip_split(bullets)
            if lost := [line for line in moved if deck_numbers.claims([{"bullets": [line]}])]:
                notes.append(f"슬라이드 {index}: 넓은 그림 아래에는 세 줄까지 들어가 수치 줄 "
                             f"{len(lost)}개가 메모로만 갔습니다: 수치 줄을 세 줄 안으로 줄이거나 "
                             "그림 슬라이드를 둘로 나누세요.")
    return {**args, "slides": slides, NOTES: notes}


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
        "with the source's own caption (「Figure 2. … (Kwon et al., 2023)」). A slide with a "
        "picture keeps its key text on the slide: put the result's numbers in `bullets` (two or "
        "three short lines, each with its number and what it compares, e.g. 「처리량 2.2배 "
        "(Orca Oracle 대비)」); under a wide figure up to three lines stay, beside a tall one they "
        "sit in a column. `notes` are the speaker's words, never the only place a result or a "
        "number is. Every number with a unit (%, 배, ×, GB, ms, tokens) must be one the "
        "material has, and a range stays a range (「20.4–38.2%」, not 「최대 20.4%」). "
        "Send a long deck compactly (three to five short lines a slide, notes of a sentence or "
        "two): one call carrying very long text can be cut off before it ends. "
        "The slide count the person asked is the whole file: the cover and the closing slide "
        "count (「8장」 = cover + 7, or cover + 6 + closing). Tables: at most about six rows of "
        "short cells a slide; split a longer one over two slides. The tool reports what the "
        "saved file holds (slides, charts, tables, pictures): say only that. Never invent names, "
        "dates, places or numbers: write a bracket blank named for what goes there, such as "
        "「[담당자]」, 「[YYYY. MM. DD.]」, 「[매출액]」, instead; never invent results, workloads "
        "or figures a paper does not have; and add no slides the request does not call for. A "
        "sum, difference, ratio or running total of numbers the material gives is not an "
        "invention: work it out (code, or by hand with the working shown in notes) and write "
        "the result (누적 합계, 달성률, 증감); only a value the material cannot yield stays a "
        "bracket blank. Write at most about six slides in one call; if the deck is longer, "
        "write the first part and add the rest with pptx_edit add_slide after pptx_read."
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
                                            # no item type: a forced call is decoded
                                            # against this schema, and a type list there
                                            # left some models writing `slides: []`
                                            "values": {
                                                "type": "array",
                                                "description": "수, 자료에 아직 없는 값은 null",
                                            },
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
    draft=draft_args,
    contents=edits.contents,
))
PPTX_READ, PPTX_EDIT, PPTX_WRITE, PPTX_RESTORE = TOOLS
pptx_read, pptx_edit, pptx_write, pptx_restore = (tool.run for tool in TOOLS)
NAMES = frozenset(tool.name for tool in TOOLS)
