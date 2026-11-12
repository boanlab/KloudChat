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

from app.services import doc_formats
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
    renumbers: frozenset[str] = frozenset({"insert_paragraph", "delete_paragraph"})
    write_text: str = ""
    write_params: dict[str, Any] | None = None
    build: Callable[[dict[str, Any]], tuple[str, bytes]] | None = None


def _trace(artifact_id: str, version: int) -> dict[str, Any]:
    # the step carries the version, so the open editor reloads at once
    return {"artifactId": artifact_id, "artifactVersion": version}


_OP_HELP = (
    "replace_text {id, text} · fill_blank {blank, value} · set_cell {table, row, col, text} · "
    "insert_paragraph {after, text} · delete_paragraph {id} · insert_table_row {table, after} · "
    "delete_table_row {table, row}"
)

_HEADS = ", ".join(
    f"{fid}({'·'.join(doc_formats.BY_ID[fid].fields)})" for fid in FORMATS if fid != "plain"
)


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

    def unconverted(artifact_id: str) -> ToolResult:
        return ToolResult(content=kit.unconverted, failed=True, trace={"artifactId": artifact_id})

    async def read(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        async with kit.session() as db:
            artifact = await kit.doc.session_document(db, ctx.user_id, ctx.session_id)
            if artifact is None:
                return not_open()
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
        return ToolResult(content=head + outline, detail=f"v{artifact.version}")

    async def edit(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        ops = args.get("ops")
        summary = " ".join(str(args.get("summary") or "").split())[:120]
        async with kit.session() as db:
            artifact = await kit.doc.session_document(db, ctx.user_id, ctx.session_id)
            if artifact is None:
                return not_open()
            if (artifact.data or {}).get("format") != editable:
                return unconverted(artifact.id)
            seen = state(ctx)
            if seen.get("id") != artifact.id or seen.get("version") != artifact.version:
                # ids name positions in the version read; a hand save moves them
                return ToolResult(
                    content=(
                        f"문서가 v{artifact.version}입니다. 고치기 전에 {p}_read로 지금 내용을 "
                        "읽고, 그 번호로 다시 고치세요."
                    ),
                    failed=True,
                )
            try:
                result = kit.edit.apply(
                    kit.doc.read(artifact), ops if isinstance(ops, list) else []
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
        async with kit.session() as db:
            artifact = await kit.doc.session_document(db, ctx.user_id, ctx.session_id)
            if artifact is None:
                saved = await kit.doc.create(
                    db, user_id=ctx.user_id, title=title, data=data,
                    session_id=ctx.session_id or None, project_id=ctx.project_id or None,
                )
            else:
                try:
                    saved = await kit.doc.save(
                        db, artifact, data, expected=artifact.version, summary="에이전트 작성",
                        title=title,
                    )
                except kit.doc.Conflict:
                    return ToolResult(
                        content=f"그사이 사람이 문서를 저장했습니다. {p}_read로 읽고 다시 하세요.",
                        failed=True,
                    )
        state(ctx).update(id=saved.id, version=saved.version, changed=True)
        left = kit.edit.blanks(data)
        return ToolResult(
            content=(
                f"「{title}」 v{saved.version}을 편집기에 띄웠습니다. 남은 빈칸: "
                + (", ".join(left) if left else "없음")
                + f"\n고칠 때는 {p}_read로 번호를 읽은 뒤 {p}_edit을 쓰세요."
            ),
            detail=title,
            trace=_trace(saved.id, saved.version),
        )

    label = kit.label
    read_tool = Tool(
        name=f"{p}_read",
        description=kit.read_text or (
            f"Read the {label} document open beside this chat, as the person last saved it in "
            "the editor. Returns numbered paragraphs (p1, p2 …), tables (t1 …) with rows "
            "`r0: a | b` (row and column count from 0), and the bracket blanks still to fill "
            f"(「[기관명]」).{kit.read_note} Call it before every {p}_edit: the numbers belong "
            "to this version."
        ),
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
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
            "and another in the body); set_cell {table:'t1', row, col, text}; insert_paragraph "
            "{after:'p7', text} (takes p7's format); delete_paragraph {id}; insert_table_row "
            "{table, after}; delete_table_row {table, row}. When one line holds several blanks "
            "that one fact fills (「[YYYY. MM. DD.] [HH:MM] ~ [HH:MM]」), rewrite that line with "
            "replace_text instead. Put only facts the person gave; anything still unknown stays "
            "a bracket blank like 「[담당자]」."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ops": {
                    "type": "array",
                    "items": kit.edit_items or {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string", "enum": list(kit.ops)},
                            "id": {"type": "string"},
                            "after": {"type": ["string", "integer"]},
                            "text": {"type": "string"},
                            "blank": {"type": "string"},
                            "value": {"type": "string"},
                            "at": {"type": "string"},
                            "table": {"type": "string"},
                            "row": {"type": "integer"},
                            "col": {"type": "integer"},
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
        description=kit.write_text or (
            f"Write a new {label} document into the editor beside the chat (replacing what is "
            "there, as a new version). Use it when the person asks for a new document; to "
            f"change the open one use {p}_read and {p}_edit. format: 'official' is a 공문 "
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
        ),
        parameters=kit.write_params or {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "format": {"type": "string", "enum": list(FORMATS)},
                "fields": {"type": "object", "additionalProperties": {"type": "string"}},
                "body": {"type": "string"},
            },
            "required": ["title", "body"],
        },
        run=write,
        label="문서 쓰는 중",
        title="문서 작성",
        read_only=False,
        wants_context=True,
    )
    return read_tool, edit_tool, write_tool
