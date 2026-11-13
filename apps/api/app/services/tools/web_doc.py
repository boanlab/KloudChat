"""Web report and web deck tools: read the document open beside the chat, and edit it in place
with targeted ops (`services/web_doc_edit.py`), as the office tools do their files. Only what
an op names changes; a whole rewrite is the report or deck writer's, when asked for. No
network: strict-local safe."""

from __future__ import annotations

import logging
from typing import Any

from app.core.db import SessionLocal
from app.models.user import utcnow
from app.models.workspace import ArtifactKind
from app.services import artifact_versions, office_doc, web_doc_edit
from app.services.tools.base import Tool, ToolContext, ToolResult

log = logging.getLogger(__name__)

__all__ = ["DECK_TOOLS", "NAMES", "REPORT_TOOLS"]


def _kind(kind: ArtifactKind, label: str) -> office_doc.Kind:
    # only `kind` is read by `office_doc.target`
    return office_doc.Kind(kind, "", label, lambda _b: "", lambda _b, _f: {})


def _derive(data: dict, deck: bool) -> dict:
    """Keys the editors derive from the parts: word count and lint."""
    from app.pipeline import marks as doc_marks
    from app.services import lint
    from app.services import report as report_service

    if deck:
        data["lint"] = lint.wire(lint.check(lint.from_slides(list(data.get("slides") or [])),
                                            slides=True))
        return data
    sections = list(data.get("sections") or [])
    data["wordCount"] = report_service.word_count(sections)
    data["lint"] = lint.wire(lint.check(lint.from_sections(sections)))
    if marks := doc_marks.relocate(data.get("marks"), sections):
        data["marks"] = marks
    else:
        data.pop("marks", None)
    return data


_REPORT_EDIT = (
    "Edit the web report open beside the chat in place: only what an op names changes, the "
    "rest stays as the person left it. All ops apply together or none do, against the numbers "
    "of the last report_read. Sections are s1, s2 … in order (「3장」 is s3). Ops: "
    "replace_text {section?, find, text} replaces a span exactly as read (the smallest edit; "
    "use it for a word, a number, a sentence); fill_blank {blank:'[담당자]', value, at?}; "
    "set_section {section, text} rewrites one section's Markdown body (when the person asks "
    "to rewrite or shorten that section; keep its tables and lists unless asked to remove "
    "them — 「절반으로」 means half, never deleting blocks); rename_section {section, "
    "heading}; insert_section {after:'s3' or 's0', heading, text, level?}; delete_section "
    "{section}; move_section {section, after} (after:'s0' to the front); tables by their "
    "number in the section: set_cell {section, table, row, col, text} (row 0 is the head), "
    "insert_table_row {section, table, after}, delete_table_row {section, table, row}, "
    "insert_table_column {section, table, after, text: head}, delete_table_column {section, "
    "table, col}, insert_table {section, rows} (added at the section's end); set_title "
    "{title}; set_head {fields: {'작성일': '…', '작성자': '…'}} for the head's fields; "
    "set_theme {accent:'#1F3A68', font: gothic|serif}. Write facts only from the person and "
    "the document; anything unknown stays a bracket blank like 「[담당자]」. Never delete text "
    "the person typed unless they asked for exactly that. A request to write the whole report "
    "anew is not done here: say so and ask them to ask for a new version."
)

_DECK_EDIT = (
    "Edit the web slides open beside the chat in place: only what an op names changes. All "
    "ops apply together or none do, against the numbers of the last deck_read. Slides are "
    "s1, s2 … in order (「5장」 is s5, never a printed page number). Ops: replace_text "
    "{slide?, find, text}; fill_blank {blank, value, at?}; set_text {slide, field: title | "
    "subtitle | body | notes, text} (notes are the speaker notes: a request about notes "
    "changes only them); set_list {slide, field: bullets | steps | cards | bands | timeline "
    "| tiles | metrics, items} (bullets: ['…'], others: [['이름', '내용']]); set_table "
    "{slide, rows: [['구분','값'], ['…','…']]} shows the slide as a real table (「표로 바꿔」); "
    "set_cell {slide, row, col, text}; insert_table_row {slide, after}; delete_table_row "
    "{slide, row}; insert_table_column {slide, after, text}; delete_table_column {slide, "
    "col}; insert_slide {after, title, bullets? or rows?, notes?}; delete_slide {slide}; "
    "move_slide {slide, after} (after:'s0' to the front); duplicate_slide {slide}; set_title "
    "{title}; set_theme {visual_style: " + " | ".join(web_doc_edit.VISUAL_STYLES) + ", "
    "accent:'#1F3A68', font: gothic | serif} for the deck's look (「남색 테마로」 is "
    "set_theme, never words on a slide). Charts cannot be edited here. Keep the number of "
    "slides the person asked for, and say the real count. Write facts only from the person "
    "and the deck; anything unknown stays a bracket blank like 「[담당자]」."
)


def tools_for(prefix: str, deck: bool) -> tuple[Tool, Tool]:
    kind = _kind(ArtifactKind.deck if deck else ArtifactKind.report,
                 "웹 슬라이드" if deck else "웹 보고서")
    label = "웹 슬라이드" if deck else "웹 보고서"

    def state(ctx: ToolContext) -> dict[str, Any]:
        return ctx.sandbox.setdefault(f"_{prefix}", {})

    async def pick(db: Any, ctx: ToolContext) -> Any:
        return await office_doc.target(
            db, kind, user_id=ctx.user_id, session_id=ctx.session_id,
            worked_on=str(state(ctx).get("id") or ""),
            opened=str((ctx.open_artifact or {}).get("id") or ""))

    async def read(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        async with SessionLocal() as db:
            found = await pick(db, ctx)
            artifact = found.artifact
            if artifact is None:
                said = (f"이 대화에는 {label}이 여러 개 있고 열린 것이 없습니다. 사람에게 "
                        "어느 것인지 물어보세요." if found.choices
                        else f"이 대화에는 {label}이 없습니다.")
                return ToolResult(content=said, failed=True)
            text = web_doc_edit.outline(dict(artifact.data or {}), artifact.title or "",
                                        deck=deck)
        state(ctx).update(id=artifact.id, version=artifact.version)
        rules = ("\n\n이 대화에서 사람이 정해 둔 규칙(고칠 때 계속 지키세요):\n"
                 + "\n".join(f"- {r}" for r in ctx.standing)) if ctx.standing else ""
        return ToolResult(content=f"「{artifact.title}」 v{artifact.version}\n{text}{rules}",
                          detail=f"v{artifact.version}")

    async def edit(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        ops = args.get("ops") if isinstance(args.get("ops"), list) else []
        summary = " ".join(str(args.get("summary") or "").split())[:120] or "에이전트 편집"
        seen = state(ctx)
        if not seen.get("id"):
            return ToolResult(content=f"고치기 전에 {prefix}_read로 지금 내용을 읽으세요.",
                              failed=True)
        async with SessionLocal() as db:
            artifact = (await pick(db, ctx)).artifact
            if artifact is None or artifact.id != seen["id"]:
                return ToolResult(content=f"{prefix}_read로 다시 읽으세요.", failed=True)
            if artifact.version != seen.get("version"):
                return ToolResult(content=(f"문서가 v{artifact.version}입니다(사람이 저장함). "
                                           f"{prefix}_read로 다시 읽고 고치세요."), failed=True)
            try:
                result = web_doc_edit.apply(dict(artifact.data or {}), artifact.title or "",
                                            ops, deck=deck, request=ctx.request)
            except web_doc_edit.WebDocError as exc:
                log.info("%s_edit refused: %s", prefix, exc)
                return ToolResult(content=f"고치지 않았습니다: {exc}", failed=True)
            if not await artifact_versions.advance(db, artifact, seen["version"], summary):
                await db.rollback()
                return ToolResult(content=(f"그사이 사람이 문서를 저장했습니다. {prefix}_read로 "
                                           "다시 읽고 고치세요."), failed=True)
            artifact.data = _derive(result.data, deck)
            artifact.title = result.title or artifact.title
            artifact.updated_at = utcnow()
            db.add(artifact)
            await db.commit()
            await db.refresh(artifact)
        seen.update(id=artifact.id, version=artifact.version)
        # the reply is held to what was saved (`office_claims`), as for the office files
        seen.setdefault("saved", []).append(
            {"version": artifact.version, "changes": list(result.changes)})
        lines = "\n".join(f"- {line}" for line in result.changes)
        content = f"v{artifact.version}로 저장했습니다. 바뀐 곳:\n{lines}"
        parts = result.data.get("slides" if deck else "sections") or []
        content += f"\n지금 {'슬라이드' if deck else '절'} {len(parts)}개."
        if any(isinstance(op, dict) and op.get("op") in web_doc_edit.RENUMBERS for op in ops):
            content += "\n번호가 바뀌었습니다. 다음 편집 전에 다시 읽으세요."
        return ToolResult(content=content, detail=f"{len(result.changes)}건",
                          trace={"artifactId": artifact.id, "artifactVersion": artifact.version})

    ops = web_doc_edit.DECK_OPS if deck else web_doc_edit.REPORT_OPS
    read_tool = Tool(
        name=f"{prefix}_read",
        description=(
            f"Read the {'web slides' if deck else 'web report'} open beside this chat as the "
            "person last saved it: its title, " + ("each slide's fields and notes" if deck else
                                                   "head fields and each section's Markdown")
            + f", and the bracket blanks still to fill. Call it before every {prefix}_edit: "
            "the numbers belong to this version. Answer questions about the document from it."
        ),
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        run=read, label="문서 확인하는 중", title="문서 확인", wants_context=True,
    )
    item = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": list(ops)},
            "section": {"type": "string"}, "slide": {"type": "string"},
            "after": {"type": ["string", "integer"]}, "at": {"type": "string"},
            "table": {"type": "integer"}, "row": {"type": "integer"},
            "col": {"type": "integer"}, "field": {"type": "string"},
            "find": {"type": "string"}, "blank": {"type": "string"},
            "heading": {"type": "string"}, "title": {"type": "string"},
            "level": {"type": "integer"}, "layout": {"type": "string"},
            "text": {"type": "string"}, "value": {"type": "string"},
            "notes": {"type": "string"},
            "items": {"type": "array"},
            "bullets": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
            "fields": {"type": "object", "additionalProperties": {"type": "string"}},
            "visual_style": {"type": "string"}, "accent": {"type": "string"},
            "font": {"type": "string"},
        },
        "required": ["op"],
    }
    edit_tool = Tool(
        name=f"{prefix}_edit",
        description=_DECK_EDIT if deck else _REPORT_EDIT,
        parameters={
            "type": "object",
            "properties": {"ops": {"type": "array", "items": item},
                           "summary": {"type": "string", "description": "한 줄 요약"}},
            "required": ["ops"],
        },
        run=edit, label="문서 고치는 중", title="문서 편집", read_only=False,
        wants_context=True,
    )
    return read_tool, edit_tool


REPORT_TOOLS = tools_for("report", deck=False)
DECK_TOOLS = tools_for("deck", deck=True)
report_read, report_edit = (t.run for t in REPORT_TOOLS)
deck_read, deck_edit = (t.run for t in DECK_TOOLS)
NAMES = frozenset(t.name for t in (*REPORT_TOOLS, *DECK_TOOLS))
