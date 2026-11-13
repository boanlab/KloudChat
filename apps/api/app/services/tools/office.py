"""Office document tools, one set per kind (`hwp.py`, `docx.py`, `pptx.py`): read the conversation's
document, edit it in place, or write a new one. They run on the server against the saved
file; the editor beside the chat reloads each version they save. No network: strict-local
safe.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from app.services import doc_formats, office_doc
from app.services.office_edit import OPS
from app.services.tools.base import Tool, ToolContext, ToolResult

log = logging.getLogger(__name__)

#: formats `*_write` takes: id -> the head fields the model may fill
FORMATS = ("official", "plain", "internal_draft", "result_report", "event_plan", "minutes")


@dataclass(frozen=True)
class Toolset:
    #: tool name prefix and `ToolContext` field: `hwp`, `docx`
    prefix: str
    #: the kind as the model and the person call it: 「HWP」, 「Word」
    label: str
    #: `hwp_doc`, `docx_doc`
    doc: ModuleType
    #: `hwpx_edit`, `docx_edit`
    edit: ModuleType
    error: type[ValueError]
    #: a database session, looked up when a tool runs
    session: Callable[[], Any]
    #: what `*_read` adds about this kind's outline
    read_note: str = ""
    #: said when the stored file is not yet in the editable format
    unconverted: str = ""
    #: A kind whose file is not paragraphs and tables (PowerPoint) says its own: the ops,
    #: their one-line help, the three tools' descriptions and parameters, the ops after
    #: which the ids moved, and how `*_write` makes its file from the arguments (a
    #: `ValueError` goes back to the model). None: the paragraph kinds' own.
    ops: tuple[str, ...] = OPS
    op_help: str = ""
    read_text: str = ""
    edit_text: str = ""
    edit_items: dict[str, Any] | None = None
    renumbers: frozenset[str] = frozenset({"insert_paragraph", "delete_paragraph",
                                           "insert_table", "move_paragraphs",
                                           "insert_picture"})
    write_text: str = ""
    write_params: dict[str, Any] | None = None
    build: Callable[[dict[str, Any]], tuple[str, bytes]] | None = None


#: ops that put a picture in: their `file` names one of the person's image files
PICTURE_OPS = frozenset({"insert_picture", "replace_picture"})
#: a picture put into a document: the formats every editor draws, and a size cap
_PICTURE_MIMES = ("image/png", "image/jpeg", "image/gif")
MAX_PICTURE = 15 * 1024 * 1024


async def _pictures(db: Any, ctx: ToolContext, ops: list) -> dict[str, tuple[str, bytes]]:
    """`file` of each picture op -> (mime, bytes): an image the person attached or a tool made
    in this conversation (its id, or its name), only theirs; a name that matches several is
    the newest. One the person cannot reach is left out, and the edit says so."""
    from sqlmodel import col, select

    from app.models.workspace import StoredFile
    from app.services import files

    wanted = {
        str(op.get("file") or "").strip()
        for op in ops
        if isinstance(op, dict) and op.get("op") in PICTURE_OPS and op.get("file")
    }
    wanted.discard("")
    if not wanted:
        return {}
    rows = (
        await db.exec(
            select(StoredFile)
            .where(StoredFile.user_id == ctx.user_id,
                   (col(StoredFile.id).in_(wanted)) | (col(StoredFile.name).in_(wanted)))
            .order_by(col(StoredFile.created_at).desc())
        )
    ).all()
    out: dict[str, tuple[str, bytes]] = {}
    for row in rows:
        # the conversation's own files first: a name shared with another chat's file
        if ctx.session_id and row.session_id not in (None, ctx.session_id):
            continue
        mime = (row.mime or "").split(";")[0].strip().lower()
        if mime not in _PICTURE_MIMES or not row.storage_key or row.size > MAX_PICTURE:
            continue
        for key in (row.id, row.name):
            if key in wanted and key not in out:
                try:
                    out[key] = (mime, files.read_blob(row.storage_key))
                except OSError:
                    continue
    return out


def _trace(artifact_id: str, version: int) -> dict[str, Any]:
    # the step carries the version, so the open editor reloads at once
    return {"artifactId": artifact_id, "artifactVersion": version}


_OP_HELP = (
    "replace_text {id, text} · fill_blank {blank, value} · set_cell {table, row, col, text, "
    "whole?} · insert_paragraph {after, text} · delete_paragraph {id} · insert_table_row "
    "{table, after} · delete_table_row {table, row} · insert_table {after, rows} · set_style "
    "{id, style} · format_text {id, find?, bold?, italic?, underline?, color?, size?} · align "
    "{id, align} · move_paragraphs {from, to, after} · insert_table_column {table, after} · "
    "delete_table_column {table, col} · merge_cells {table, row, col, span_rows, span_cols} · "
    "split_cell {table, row, col} · insert_picture {after, file} · replace_picture {id, file} · "
    "page_setup {orientation?, margins_mm?, header?, footer?, page_numbers?}"
)

_HEADS = ", ".join(
    f"{fid}({'·'.join(doc_formats.BY_ID[fid].fields)})" for fid in FORMATS if fid != "plain"
)


#: which document a tool works on, said in every read tool
_WHICH = (
    " It reads the document open in the panel; with none open and several of this kind in "
    "the conversation it says so: ask the person which one, then read it with `document`."
)
#: `*_write` makes a new document unless a rewrite of the open one was asked
_REPLACE = (
    " The new document is made beside the conversation's other documents, which stay as "
    "they are (the person's hand edits included). Set replace:true only when the person "
    "asked to rewrite the open document from scratch; it then becomes that document's next "
    "version and needs a {p}_read of it first."
)


def _with_replace(params: dict[str, Any]) -> dict[str, Any]:
    """A write tool's parameters with `replace`, the explicit whole-rewrite flag."""
    return {**params, "properties": {**params.get("properties", {}), "replace": {
        "type": "boolean",
        "description": "true only when the person asked to rewrite the open document",
    }}}


def _paragraph_write(kit: Toolset, args: dict[str, Any]) -> tuple[str, bytes]:
    """A paragraph kind's new document: a `doc_formats` head over a markdown body."""
    title = " ".join(str(args.get("title") or "").split())[:120]
    body = str(args.get("body") or "").strip()
    fmt = str(args.get("format") or "official")
    raw = args.get("fields") if isinstance(args.get("fields"), dict) else {}
    if not title or not body:
        raise ValueError("title과 body가 모두 있어야 합니다.")
    if fmt not in FORMATS:
        raise ValueError(f"format은 {', '.join(FORMATS)} 중 하나입니다.")
    fields = {str(k): str(v) for k, v in raw.items() if str(v or "").strip()}
    data = (
        kit.doc.plain(title, body) if fmt == "plain" else kit.doc.official(title, body, fields, fmt)
    )
    return title, data


def tools_for(kit: Toolset) -> tuple[Tool, Tool, Tool]:
    p, editable = kit.prefix, kit.doc.KIND.editable
    op_help = kit.op_help or _OP_HELP

    def state(ctx: ToolContext) -> dict[str, Any]:
        return getattr(ctx, p)

    def not_open() -> ToolResult:
        return ToolResult(
            content=f"이 대화에는 아직 {kit.label} 문서가 없습니다. 새 문서는 {p}_write로 쓰세요.",
            failed=True,
        )

    def ask_which(choices: tuple[Any, ...]) -> ToolResult:
        # several documents of the kind and none open: asked, never the newest guessed
        named = ", ".join(f"「{a.title}」(v{a.version})" for a in choices[:8])
        return ToolResult(
            content=(
                f"이 대화에는 {kit.label} 문서가 {len(choices)}개 있고 옆 패널에 열린 것이 "
                f"없습니다: {named}. 어느 문서를 말하는지 사람에게 물어보세요. 사람이 고르면 "
                f"{p}_read의 document에 그 제목을 넣어 읽으세요."
            ),
            failed=True,
        )

    async def pick(db: Any, ctx: ToolContext, named: str = "") -> Any:
        return await office_doc.target(
            db, kit.doc.KIND, user_id=ctx.user_id, session_id=ctx.session_id,
            worked_on=str(state(ctx).get("id") or ""),
            opened=str((ctx.open_artifact or {}).get("id") or ""),
            named=named,
        )

    def unconverted(artifact_id: str) -> ToolResult:
        return ToolResult(content=kit.unconverted, failed=True, trace={"artifactId": artifact_id})

    def rules(ctx: ToolContext) -> str:
        # what the person set for the whole conversation reaches every edit
        if not ctx.standing:
            return ""
        lines = "\n".join(f"- {line}" for line in ctx.standing)
        return ("\n\n이 대화에서 사람이 정해 둔 규칙(고치고 쓸 때 계속 지키세요):\n" + lines)

    async def read(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        async with kit.session() as db:
            found = await pick(db, ctx, str(args.get("document") or ""))
            artifact = found.artifact
            if artifact is None:
                return ask_which(found.choices) if found.choices else not_open()
            if (artifact.data or {}).get("format") != editable:
                return unconverted(artifact.id)
            try:
                data = kit.doc.read(artifact)
                outline = kit.edit.outline(data)
            except (OSError, kit.error) as exc:
                log.warning("%s_read %s: %s", p, artifact.id, exc)
                return ToolResult(content=f"문서를 읽지 못했습니다: {exc}", failed=True)
        state(ctx).update(id=artifact.id, version=artifact.version)
        head = f"「{artifact.title}」 v{artifact.version} (사람이 편집기에서 저장한 내용 포함)\n"
        return ToolResult(content=head + outline + rules(ctx), detail=f"v{artifact.version}")

    async def edit(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        ops = args.get("ops")
        summary = " ".join(str(args.get("summary") or "").split())[:120]
        seen = state(ctx)
        if not seen.get("id"):
            # ids name positions in the version read
            return ToolResult(
                content=f"고치기 전에 {p}_read로 지금 내용을 읽고, 그 번호로 고치세요.",
                failed=True,
            )
        async with kit.session() as db:
            artifact = (await pick(db, ctx)).artifact
            if artifact is None:
                return not_open()
            if (artifact.data or {}).get("format") != editable:
                return unconverted(artifact.id)
            if seen.get("id") != artifact.id or seen.get("version") != artifact.version:
                # a hand save moved the ids
                return ToolResult(
                    content=(
                        f"문서가 v{artifact.version}입니다. 고치기 전에 {p}_read로 지금 내용을 "
                        "읽고, 그 번호로 다시 고치세요."
                    ),
                    failed=True,
                )
            ops = ops if isinstance(ops, list) else []
            try:
                # a picture op names a file of the person's; its bytes are found here
                pictures = await _pictures(db, ctx, ops)
                result = kit.edit.apply(
                    kit.doc.read(artifact), ops, **({"pictures": pictures} if pictures else {})
                )
            except kit.error as exc:
                # what the model got wrong, for tuning the tool's description
                log.info("%s_edit refused: %s", p, exc)
                return ToolResult(
                    content=f"고치지 않았습니다: {exc}\n쓸 수 있는 op: {op_help}", failed=True
                )
            try:
                saved = await kit.doc.save(
                    db, artifact, result.data, expected=artifact.version,
                    summary=summary or "에이전트 편집",
                )
            except kit.doc.Conflict:
                return ToolResult(
                    content=f"그사이 사람이 문서를 저장했습니다. {p}_read로 다시 읽고 고치세요.",
                    failed=True,
                )
        state(ctx).update(id=saved.id, version=saved.version, changed=True)
        lines = "\n".join(f"- {line}" for line in result.changes)
        content = f"v{saved.version}로 저장했습니다. 바뀐 곳:\n{lines}"
        if any(isinstance(op, dict) and op.get("op") in kit.renumbers for op in ops):
            # numbering moved; the next edit needs the new ids
            content += "\n\n번호가 바뀌었습니다. 지금 문서:\n" + kit.edit.outline(result.data)
        else:
            left = kit.edit.blanks(result.data)
            content += "\n남은 빈칸: " + (", ".join(left) if left else "없음")
        return ToolResult(
            content=content,
            detail=f"{len(result.changes)}건",
            trace=_trace(saved.id, saved.version),
        )

    async def write(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            title, data = (kit.build or (lambda a: _paragraph_write(kit, a)))(args)
        except ValueError as exc:
            return ToolResult(content=str(exc), failed=True)
        except Exception as exc:  # noqa: BLE001 — a writer failure goes back to the model
            log.warning("%s_write failed: %s", p, exc, exc_info=True)
            return ToolResult(content=f"문서를 만들지 못했습니다: {exc}", failed=True)
        replace = args.get("replace") is True
        async with kit.session() as db:
            found = await pick(db, ctx)
            artifact = found.artifact if replace else None
            if replace and artifact is None:
                return (ask_which(found.choices) if found.choices else ToolResult(
                    content=f"바꿀 {kit.label} 문서가 없습니다. replace 없이 새 문서로 쓰세요.",
                    failed=True))
            if artifact is not None:
                seen = state(ctx)
                if seen.get("id") != artifact.id or seen.get("version") != artifact.version:
                    # a whole rewrite of what the person may have edited by hand: only from
                    # the version the agent read this turn
                    return ToolResult(
                        content=(
                            f"「{artifact.title}」 v{artifact.version}을 통째로 바꾸려면 먼저 "
                            f"{p}_read로 지금 판을 읽으세요. 사람이 처음부터 다시 쓰라고 한 것이 "
                            f"아니면 replace 없이 새 문서로 쓰거나 {p}_edit으로 고치세요."
                        ),
                        failed=True,
                    )
                try:
                    saved = await kit.doc.save(
                        db, artifact, data, expected=artifact.version, summary="에이전트 다시 씀",
                        title=title,
                    )
                except kit.doc.Conflict:
                    return ToolResult(
                        content=f"그사이 사람이 문서를 저장했습니다. {p}_read로 읽고 다시 하세요.",
                        failed=True,
                    )
                said = (f"「{title}」 v{saved.version}로 문서를 처음부터 다시 썼습니다"
                        "(이전 판은 버전 기록에 있습니다).")
            else:
                # a new document beside the others: nothing the person saved is replaced
                saved = await kit.doc.create(
                    db, user_id=ctx.user_id, title=title, data=data,
                    session_id=ctx.session_id or None, project_id=ctx.project_id or None,
                )
                kept = [a for a in (found.artifact, *found.choices) if a is not None]
                said = f"「{title}」을 새 문서로 만들어 편집기에 띄웠습니다." + (
                    " 이 대화의 다른 문서는 그대로 두었습니다." if kept else "")
        state(ctx).update(id=saved.id, version=saved.version, changed=True)
        left = kit.edit.blanks(data)
        return ToolResult(
            content=(
                f"{said} 남은 빈칸: "
                + (", ".join(left) if left else "없음")
                + f"\n고칠 때는 {p}_read로 번호를 읽은 뒤 {p}_edit을 쓰세요."
            ),
            detail=title,
            trace=_trace(saved.id, saved.version),
        )

    label = kit.label
    read_tool = Tool(
        name=f"{p}_read",
        description=(kit.read_text or (
            f"Read the {label} document open beside this chat, as the person last saved it in "
            "the editor. Returns numbered paragraphs (p1, p2 …), tables (t1 …) with rows "
            "`r0: a | b` (row and column count from 0), and the bracket blanks still to fill "
            f"(「[기관명]」).{kit.read_note} Call it before every {p}_edit: the numbers belong "
            "to this version."
        )) + _WHICH,
        parameters={
            "type": "object",
            "properties": {"document": {
                "type": "string",
                "description": "문서 제목: 여러 문서 중 사람이 고른 것을 읽을 때만",
            }},
            "additionalProperties": False,
        },
        run=read,
        label="문서 확인하는 중",
        title="문서 확인",
        wants_context=True,
    )
    edit_tool = Tool(
        name=f"{p}_edit",
        description=kit.edit_text or (
            f"Edit the open {label} document in place; its paragraph and character formatting "
            f"stay. All ops apply together or none do, against the numbers of the last {p}_read. "
            "Ops: replace_text {id:'p5', text} replaces a whole paragraph; fill_blank "
            "{blank:'[연수명]', value:'교원연수'} fills every place that blank appears, or only "
            f"one place with at:'p6' or at:'t1 r3c1' ({p}_read lists where each blank stands: "
            "give `at` when the same blank means different things, such as a date in the head "
            "and another in the body); set_cell {table:'t1', row, col, text} rewrites the "
            "cell's text, one line per paragraph (\\n between), keeping its other contents; "
            "whole:true empties the cell first (only when asked to replace the whole cell); "
            "insert_paragraph {after:'p7', text} (takes p7's format); delete_paragraph {id}; "
            "insert_table_row {table, after}; delete_table_row {table, row}; insert_table "
            "{after:'p7', rows: [['구분','내용'], ['…','…']]} adds a new table after p7, its "
            "first row the head (a caption is an insert_paragraph before it). "
            "Format: set_style {id, style: title | heading 1 | heading 2 | heading 3 | body}; "
            "format_text {id:'p5' or 't1 r2c1', find:'3억 원' (leave out for the whole "
            "paragraph), bold, italic, underline (true/false), color:'#C00000', size: pt}; "
            "align {id, align: left | center | right | justify}. "
            "Move: move_paragraphs {from:'p9', to:'p14', after:'p3'} moves p9~p14 (a chapter "
            "with its body and tables) after p3. "
            "Tables: insert_table_column {table, after: col}; delete_table_column {table, col}; "
            "merge_cells {table, row, col, span_rows, span_cols}; split_cell {table, row, col}. "
            "Pictures: insert_picture {after:'p5', file, width_mm?}; replace_picture {id:'p6', "
            "file} — `file` is the name or id of an image the person attached or a tool made in "
            "this conversation. Page: page_setup {orientation: portrait | landscape, "
            "margins_mm: {top, bottom, left, right}, header, footer, page_numbers: true/false}. "
            "Ops that move or add paragraphs renumber what follows: read again before the next "
            "edit. It cannot edit charts, text boxes, footnotes or a table of contents, and "
            "cannot make a chart: say so instead of pasting one into the chat. "
            "When one line holds several blanks that one fact fills (「[YYYY. MM. DD.] [HH:MM] "
            "~ [HH:MM]」), rewrite that line with replace_text instead. Put only facts the "
            "person gave; anything still unknown stays a bracket blank like 「[담당자]」; never "
            "delete text the person typed unless they asked for that very text to go."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ops": {
                    "type": "array",
                    "items": kit.edit_items or {
                        "type": "object",
                        # where, then what: a model that writes keys in this order ends
                        # a set_cell at `col` when `text` comes first
                        "properties": {
                            "op": {"type": "string", "enum": list(kit.ops)},
                            "id": {"type": "string"},
                            "table": {"type": "string"},
                            "row": {"type": "integer"},
                            "col": {"type": "integer"},
                            "after": {"type": ["string", "integer"]},
                            "at": {"type": "string"},
                            "blank": {"type": "string"},
                            "text": {"type": "string"},
                            "value": {"type": "string"},
                            "rows": {"type": "array",
                                     "items": {"type": "array", "items": {"type": "string"}}},
                            "whole": {"type": "boolean"},
                            "style": {"type": "string"},
                            "find": {"type": "string"},
                            "bold": {"type": "boolean"},
                            "italic": {"type": "boolean"},
                            "underline": {"type": "boolean"},
                            "color": {"type": "string"},
                            "size": {"type": "number"},
                            "align": {"type": "string",
                                      "enum": ["left", "center", "right", "justify"]},
                            "from": {"type": "string"},
                            "to": {"type": "string"},
                            "span_rows": {"type": "integer"},
                            "span_cols": {"type": "integer"},
                            "file": {"type": "string"},
                            "width_mm": {"type": "number"},
                            "orientation": {"type": "string",
                                            "enum": ["portrait", "landscape"]},
                            "margins_mm": {"type": "object",
                                           "additionalProperties": {"type": "number"}},
                            "header": {"type": "string"},
                            "footer": {"type": "string"},
                            "page_numbers": {"type": "boolean"},
                        },
                        "required": ["op"],
                    },
                },
                "summary": {"type": "string", "description": "한 줄 요약: 「일시·장소 채움」"},
            },
            "required": ["ops"],
        },
        run=edit,
        label="문서 고치는 중",
        title="문서 편집",
        read_only=False,
        wants_context=True,
    )
    write_tool = Tool(
        name=f"{p}_write",
        description=(kit.write_text or (
            f"Write a new {label} document and open it in the editor beside the chat. Use it "
            f"when the person asks for a new document; to change the open one use {p}_read and "
            f"{p}_edit. format: 'official' is a 공문 "
            "(head table 수신·참조·발신·시행일, then 제목, numbered items 1. 가. (1), 「끝.」 "
            f"added); other heads: {_HEADS}; 'plain' has no head. fields: head values the "
            "person gave; leave out what they did not give and it shows as a blank 「[수신]」. "
            "body: markdown; the title is printed above it, so the body does not repeat a 제목 "
            "line. For 'official' write items as lines like `1. 관련: …`, `2. …`, "
            "`  가. 일시: …`, and attachments as an unnumbered last line "
            "`붙임  1. 연수 일정표 1부.` (more on following lines); 「끝.」 is added for you. "
            "Never invent names, dates, places or numbers: write a bracket blank named for what "
            "goes there, such as 「[기관명]」, 「[연수 일시]」, 「[장소]」, instead; and add no "
            "items the request does not call for (costs, penalties, procedures)."
        )) + _REPLACE.format(p=p),
        parameters=_with_replace(kit.write_params or {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "format": {"type": "string", "enum": list(FORMATS)},
                "fields": {"type": "object", "additionalProperties": {"type": "string"}},
                "body": {"type": "string"},
            },
            "required": ["title", "body"],
        }),
        run=write,
        label="문서 쓰는 중",
        title="문서 작성",
        read_only=False,
        wants_context=True,
    )
    return read_tool, edit_tool, write_tool
