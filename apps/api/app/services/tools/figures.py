"""`pdf_figures`: a paper's own figures and tables, cut from its PDF (`pdf_figures`) and kept
as pictures of this conversation, so a deck about the paper shows them (`pptx_write` slide
`image.file`) with their captions instead of a redrawn guess. In-process, no network:
strict-local safe."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from sqlmodel import col, or_, select

from app.core.db import SessionLocal
from app.models.workspace import StoredFile
from app.services import files as file_service
from app.services import pdf_figures as cuts
from app.services.tools.base import Tool, ToolContext, ToolResult

log = logging.getLogger(__name__)

#: 「Figure 12」, 「fig 3」, 「Table 1」, 「그림 2」, 「표 1」 in the `figures` argument
_WANTED = re.compile(r"(figure|fig\.?|table|그림|표)\s{0,2}(\d{1,3})", re.I)


def _wanted(raw: Any) -> set[tuple[str, int]]:
    items = raw if isinstance(raw, list) else [raw] if raw else []
    out = set()
    for item in items:
        for match in _WANTED.finditer(str(item)):
            word = match.group(1).lower()
            out.add(("table" if word in ("table", "표") else "figure", int(match.group(2))))
    return out


async def _pdf(ctx: ToolContext, name: str) -> StoredFile | None:
    """The PDF `name` (or id) among the project's files and this conversation's own."""
    scope = [StoredFile.session_id == ctx.session_id] if ctx.session_id else []
    if ctx.project_id:
        scope.append(StoredFile.project_id == ctx.project_id)
    if not scope:
        return None
    async with SessionLocal() as db:
        rows = (await db.exec(
            select(StoredFile)
            .where(StoredFile.user_id == ctx.user_id, or_(*scope))
            .order_by(col(StoredFile.created_at).desc())
        )).all()
    pdfs = [row for row in rows if row.storage_key and (
        (row.mime or "").startswith("application/pdf") or row.name.lower().endswith(".pdf"))]
    wanted = name.strip().lower()
    for row in pdfs:
        if wanted in (row.id.lower(), row.name.lower()):
            return row
    # 「2309.06180」 for 「2309.06180.pdf」, or a word of the paper's title
    return next((row for row in pdfs if wanted and (
        wanted in row.name.lower() or wanted in (row.text or "")[:400].lower())), None)


async def pdf_figures(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    name = str(args.get("file") or "").strip()
    if not name:
        return ToolResult(content="file(논문 PDF의 이름)이 있어야 합니다.", failed=True)
    stored = await _pdf(ctx, name)
    if stored is None:
        return ToolResult(content=f"「{name}」 PDF를 이 프로젝트나 대화에서 찾지 못했습니다.",
                          failed=True)
    data = await asyncio.to_thread(file_service.read_blob, stored.storage_key)
    try:
        found = await asyncio.to_thread(cuts.find, data)
    except Exception as exc:  # noqa: BLE001 — an unreadable PDF is said, not raised
        log.info("pdf figures not found in %s: %s", stored.id, exc)
        return ToolResult(content=f"「{stored.name}」에서 그림을 읽지 못했습니다.", failed=True)
    if not found:
        return ToolResult(content=(
            f"「{stored.name}」에서 캡션으로 위치를 찾을 수 있는 그림·표가 없습니다. 그림을 "
            "넣었다고 말하지 말고, 필요하면 수치를 자료 그대로 표로 옮기세요."), empty=True)
    wanted = _wanted(args.get("figures"))
    picked = [f for f in found if (f.kind, f.number) in wanted] if wanted else found
    picked = picked[:cuts.MAX_CUTS]
    stem = re.sub(r"\.pdf$", "", stored.name, flags=re.I)
    lines = [f"「{stored.name}」의 그림·표 {len(found)}개 (캡션은 논문 그대로):"]
    from app.services import sandbox

    for figure in found:
        line = f"- {figure.label} (p.{figure.page}): {figure.caption}"
        if figure in picked:
            png = await asyncio.to_thread(cuts.cut, data, figure)
            saved = await sandbox.store_output(
                ctx.user_id, ctx.session_id, f"{stem}-{figure.label.replace(' ', '')}.png", png)
            line += f"\n  → 그림 파일: {saved['name']}"
        lines.append(line)
    missing = sorted(wanted - {(f.kind, f.number) for f in found})
    if missing:
        lines.append("위치를 찾지 못한 것: " + ", ".join(
            f"{'Table' if k == 'table' else 'Figure'} {n}" for k, n in missing)
            + " (넣었다고 말하지 마세요).")
    lines.append(
        "슬라이드에는 pptx_write의 slides[].image {file: 그림 파일 이름, caption}으로 넣고, "
        "caption은 위 캡션을 줄여 쓰되 번호와 출처(저자, 연도)를 남기세요. 그림이 보여 주는 "
        "것만 말하고, 캡션과 본문에 없는 수치나 실험은 쓰지 마세요.")
    return ToolResult(content="\n".join(lines), detail=f"{stored.name} · {len(picked)}개")


PDF_FIGURES = Tool(
    name="pdf_figures",
    description=(
        "List the figures and tables of a paper PDF in this project or conversation (by its "
        "captions) and cut them out as pictures you can put on PowerPoint slides with "
        "pptx_write slides[].image {file, caption}. Use it when the person asks for a paper's "
        "figures or tables in a deck (「논문 그림이나 표도 넣어 줘」). file: the PDF's name "
        "(「2309.06180.pdf」) or id; figures: which ones (「Figure 2」, 「Table 1」), or leave it "
        "out for the first few. Only figures it returns exist: never say a figure was "
        "inserted that it did not return."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file": {"type": "string"},
            "figures": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["file"],
    },
    run=pdf_figures,
    label="논문 그림 찾는 중",
    title="논문 그림 가져오기",
    read_only=False,
    wants_context=True,
)
