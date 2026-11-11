"""HWP document tools: read the conversation's HWP document, edit it in place, or write a
new one. They run on the server against the saved HWPX (services/hwpx_edit.py); the
editor beside the chat reloads each version they save. No network: strict-local safe.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.db import SessionLocal
from app.services import doc_formats, hwp_doc, hwpx_edit
from app.services.tools.base import Tool, ToolContext, ToolResult

log = logging.getLogger(__name__)

NAMES = frozenset({"hwp_read", "hwp_edit", "hwp_write"})

#: formats `hwp_write` takes: id -> the head fields the model may fill
FORMATS = ("official", "plain", "internal_draft", "result_report", "event_plan", "minutes")


def _trace(artifact_id: str, version: int) -> dict[str, Any]:
    # the step carries the version, so the open editor reloads at once
    return {"artifactId": artifact_id, "artifactVersion": version}


def _not_open() -> ToolResult:
    return ToolResult(
        content="이 대화에는 아직 HWP 문서가 없습니다. 새 문서는 hwp_write로 쓰세요.",
        failed=True,
    )


def _unconverted(artifact_id: str) -> ToolResult:
    return ToolResult(
        content=(
            "이 문서는 아직 .hwp 원본입니다. 편집기에서 열리면 HWPX로 바뀌어 저장되니, "
            "사람에게 문서 패널을 열어 달라고 한 뒤 다시 읽으세요."
        ),
        failed=True,
        trace={"artifactId": artifact_id},
    )


async def hwp_read(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    async with SessionLocal() as db:
        artifact = await hwp_doc.session_document(db, ctx.user_id, ctx.session_id)
        if artifact is None:
            return _not_open()
        if (artifact.data or {}).get("format") != "hwpx":
            return _unconverted(artifact.id)
        try:
            data = hwp_doc.read(artifact)
            outline = hwpx_edit.outline(data)
        except (OSError, hwpx_edit.HwpxError) as exc:
            log.warning("hwp_read %s: %s", artifact.id, exc)
            return ToolResult(content=f"문서를 읽지 못했습니다: {exc}", failed=True)
    ctx.hwp.update(id=artifact.id, version=artifact.version)
    head = f"「{artifact.title}」 v{artifact.version} (사람이 편집기에서 저장한 내용 포함)\n"
    return ToolResult(content=head + outline, detail=f"v{artifact.version}")


_OP_HELP = (
    "replace_text {id, text} · fill_blank {blank, value} · set_cell {table, row, col, text} · "
    "insert_paragraph {after, text} · delete_paragraph {id} · insert_table_row {table, after} · "
    "delete_table_row {table, row}"
)


async def hwp_edit(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    ops = args.get("ops")
    summary = " ".join(str(args.get("summary") or "").split())[:120]
    async with SessionLocal() as db:
        artifact = await hwp_doc.session_document(db, ctx.user_id, ctx.session_id)
        if artifact is None:
            return _not_open()
        if (artifact.data or {}).get("format") != "hwpx":
            return _unconverted(artifact.id)
        if ctx.hwp.get("id") != artifact.id or ctx.hwp.get("version") != artifact.version:
            # ids name positions in the version read; a hand save moves them
            return ToolResult(
                content=(
                    f"문서가 v{artifact.version}입니다. 고치기 전에 hwp_read로 지금 내용을 "
                    "읽고, 그 번호로 다시 고치세요."
                ),
                failed=True,
            )
        try:
            result = hwpx_edit.apply(hwp_doc.read(artifact), ops if isinstance(ops, list) else [])
        except hwpx_edit.HwpxError as exc:
            return ToolResult(
                content=f"고치지 않았습니다: {exc}\n쓸 수 있는 op: {_OP_HELP}", failed=True
            )
        try:
            saved = await hwp_doc.save(
                db, artifact, result.data, expected=artifact.version,
                summary=summary or "에이전트 편집",
            )
        except hwp_doc.Conflict:
            return ToolResult(
                content="그사이 사람이 문서를 저장했습니다. hwp_read로 다시 읽고 고치세요.",
                failed=True,
            )
    ctx.hwp.update(id=saved.id, version=saved.version, changed=True)
    lines = "\n".join(f"- {line}" for line in result.changes)
    content = f"v{saved.version}로 저장했습니다. 바뀐 곳:\n{lines}"
    if any(op.get("op") in ("insert_paragraph", "delete_paragraph") for op in ops):
        # numbering moved; the next edit needs the new ids
        content += "\n\n번호가 바뀌었습니다. 지금 문서:\n" + hwpx_edit.outline(result.data)
    else:
        left = hwpx_edit.blanks(result.data)
        content += "\n남은 빈칸: " + (", ".join(left) if left else "없음")
    return ToolResult(
        content=content,
        detail=f"{len(result.changes)}건",
        trace=_trace(saved.id, saved.version),
    )


async def hwp_write(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    title = " ".join(str(args.get("title") or "").split())[:120]
    body = str(args.get("body") or "").strip()
    fmt = str(args.get("format") or "official")
    raw = args.get("fields") if isinstance(args.get("fields"), dict) else {}
    if not title or not body:
        return ToolResult(content="title과 body가 모두 있어야 합니다.", failed=True)
    if fmt not in FORMATS:
        return ToolResult(content=f"format은 {', '.join(FORMATS)} 중 하나입니다.", failed=True)
    fields = {str(k): str(v) for k, v in raw.items() if str(v or "").strip()}
    try:
        data = (
            hwp_doc.plain(title, body)
            if fmt == "plain"
            else hwp_doc.official(title, body, fields, fmt)
        )
    except Exception as exc:  # noqa: BLE001 — a writer failure goes back to the model
        log.warning("hwp_write failed: %s", exc, exc_info=True)
        return ToolResult(content=f"문서를 만들지 못했습니다: {exc}", failed=True)
    async with SessionLocal() as db:
        artifact = await hwp_doc.session_document(db, ctx.user_id, ctx.session_id)
        if artifact is None:
            saved = await hwp_doc.create(
                db, user_id=ctx.user_id, title=title, data=data,
                session_id=ctx.session_id or None, project_id=ctx.project_id or None,
            )
        else:
            try:
                saved = await hwp_doc.save(
                    db, artifact, data, expected=artifact.version, summary="에이전트 작성",
                    title=title,
                )
            except hwp_doc.Conflict:
                return ToolResult(
                    content="그사이 사람이 문서를 저장했습니다. hwp_read로 읽고 다시 하세요.",
                    failed=True,
                )
    ctx.hwp.update(id=saved.id, version=saved.version, changed=True)
    left = hwpx_edit.blanks(data)
    return ToolResult(
        content=(
            f"「{title}」 v{saved.version}을 편집기에 띄웠습니다. 남은 빈칸: "
            + (", ".join(left) if left else "없음")
            + "\n고칠 때는 hwp_read로 번호를 읽은 뒤 hwp_edit을 쓰세요."
        ),
        detail=title,
        trace=_trace(saved.id, saved.version),
    )


_HEADS = ", ".join(
    f"{fid}({'·'.join(doc_formats.BY_ID[fid].fields)})" for fid in FORMATS if fid != "plain"
)

HWP_READ = Tool(
    name="hwp_read",
    description=(
        "Read the HWP document open beside this chat, as the person last saved it in the "
        "editor. Returns numbered paragraphs (p1, p2 …), tables (t1 …) with rows "
        "`r0: a | b` (row and column count from 0), and the bracket blanks still to fill "
        "(「[기관명]」). Call it before every hwp_edit: the numbers belong to this version."
    ),
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    run=hwp_read,
    label="문서 확인하는 중",
    title="문서 확인",
    wants_context=True,
)

HWP_EDIT = Tool(
    name="hwp_edit",
    description=(
        "Edit the open HWP document in place; its paragraph and character formatting stay. "
        "All ops apply together or none do, against the numbers of the last hwp_read. "
        "Ops: replace_text {id:'p5', text} replaces a whole paragraph; fill_blank "
        "{blank:'[연수명]', value:'교원연수'} fills every place that blank appears; "
        "set_cell {table:'t1', row, col, text}; insert_paragraph {after:'p7', text} (takes "
        "p7's format); delete_paragraph {id}; insert_table_row {table, after}; "
        "delete_table_row {table, row}. Prefer fill_blank and set_cell when a blank or "
        "cell is what changes. Put only facts the person gave; anything still unknown stays a "
        "bracket blank like 「[담당자]」."
    ),
    parameters={
        "type": "object",
        "properties": {
            "ops": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": [
                                "replace_text", "fill_blank", "set_cell", "insert_paragraph",
                                "delete_paragraph", "insert_table_row", "delete_table_row",
                            ],
                        },
                        "id": {"type": "string"},
                        "after": {"type": ["string", "integer"]},
                        "text": {"type": "string"},
                        "blank": {"type": "string"},
                        "value": {"type": "string"},
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
    run=hwp_edit,
    label="문서 고치는 중",
    title="문서 편집",
    read_only=False,
    wants_context=True,
)

HWP_WRITE = Tool(
    name="hwp_write",
    description=(
        "Write a new HWP document into the editor beside the chat (replacing what is there, "
        "as a new version). Use it when the person asks for a new document; to change "
        "the open one use hwp_read and hwp_edit. format: 'official' is a 공문 (head table "
        "수신·참조·발신·시행일, then 제목, numbered items 1. 가. (1), 「끝.」 added); other "
        f"heads: {_HEADS}; 'plain' has no head. fields: head values the person gave; leave "
        "out what they did not give and it shows as a blank 「[수신]」. body: markdown; the "
        "title is printed above it, so the body does not repeat a 제목 line. For 'official' "
        "write items as lines like `1. 관련: …`, `2. …`, `  가. 일시: …`, and attachments as an "
        "unnumbered last line `붙임  1. 연수 일정표 1부.` (more on following lines); 「끝.」 is "
        "added for you. Never "
        "invent names, dates, places or numbers: write a bracket blank such as 「[기관명]」, "
        "「[YYYY. MM. DD.]」, 「[장소]」 instead."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "format": {"type": "string", "enum": list(FORMATS)},
            "fields": {"type": "object", "additionalProperties": {"type": "string"}},
            "body": {"type": "string"},
        },
        "required": ["title", "body"],
    },
    run=hwp_write,
    label="문서 쓰는 중",
    title="문서 작성",
    read_only=False,
    wants_context=True,
)

TOOLS = (HWP_READ, HWP_EDIT, HWP_WRITE)
