"""The `io` stages that load a turn's document and store its result.

What stood at the head and the tail of the router's runners (`sessions._run_report`,
`sessions._revise_document`), cut out as stages so a run records them like any other and a
rerun stores its result the same way a turn does:

- `report-save` — the written report as an artifact (a new version when the session holds
  one of the same kind), the assistant message, the bill and the searches;
- `revise-load` — the document on screen, the request it was written from and its files;
- `revise-save` — the revised document as a new version, the files asked for in the same
  turn, the message and the bill.

They reach the database and the bill through the router module (`sessions.SessionLocal`,
`sessions._store_document`, `sessions.settle` …), looked up when they run, so a test that
replaces one there reaches the stage.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from app.pipeline import marks as doc_marks
from app.pipeline import registry
from app.pipeline.bundle import Bundle
from app.pipeline.stage import Base, Context

log = logging.getLogger("app.routers.sessions")


def _host():
    # Late: the router imports the pipeline.
    from app.routers import sessions

    return sessions


def rerun_note(turn: dict[str, Any]) -> str:
    """What the message of a rerun says first: 「검증」 단계부터 다시 만들었습니다."""
    from app.pipeline.labels import label

    stage = str(turn.get("rerun_from") or "")
    # A composed step names itself (`rerun_label`); a shipped stage by its registry label.
    name = str(turn.get("rerun_label") or "") or label(stage)
    return f"「{name}」 단계부터 다시 만들었습니다." if stage else ""


class ReportSave(Base):
    """The written report stored: carried parts put back (a restructure), the artifact (a
    version when the session already holds a report), the message, the bill."""

    name = "report-save"
    kind = "io"
    reads = ("request", "document.sections", "usage", "wire")
    writes = ("document.sections", "document.saved")

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.request.turn.get("user_id") else "no turn to store"

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        host, req, turn = _host(), bundle.request, bundle.request.turn
        from app.models.chat import ChatSession, Message, Role
        from app.models.user import User, utcnow
        from app.models.workspace import Artifact, ArtifactKind
        from app.pipeline.report_stages import report_design
        from app.services import design_templates, lint, revise
        from app.services import report as report_service

        model, outline_model = turn["model"], turn.get("outline_model")
        user_id, session_id = turn["user_id"], req.session_id
        request, approved_plan = req.text, req.approved_plan
        template = design_templates.get(req.template_id) if req.template_id else None
        design_tokens = req.design_tokens
        carry_from, carry_note = turn.get("carry_from"), str(turn.get("carry_note") or "")
        sections = list(bundle.document.sections)
        title_block = dict((bundle.wire.get("titleBlock") or {}).get("titleBlock") or {}) or None
        sources = list((bundle.wire.get("sources") or {}).get("sources") or [])
        research_log = ((bundle.wire.get("research") or {}).get("research"))
        research_log = dict(research_log) if research_log is not None else None
        doc_title = str((bundle.wire.get("title") or {}).get("title") or "").strip()
        usage = bundle.usage

        if carry_from and sections:
            async with host.SessionLocal() as db:
                previous = await db.get(Artifact, carry_from)
            if previous is not None and previous.data:
                sections, carried = revise.carry_parts(
                    sections, list(previous.data.get("sections") or []), is_deck=False,
                    mentioned=carry_note,
                )
                if carried:
                    yield {"type": "step", "id": "carry", "label": f"그대로 둔 절 {carried}개",
                           "status": "done"}
                    for section in sections:
                        yield {"type": "section", "sectionId": section.get("id"),
                               "heading": section.get("heading"),
                               "content": section.get("content"), "done": True}
        bundle.document.sections = sections
        written = [s for s in sections if (s.get("content") or "").strip()]
        credits = (
            0
            if not written
            else host.charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
            # The planner's own tokens at the planner's own price, when one ran.
            + host.charge_for_tokens(
                outline_model or model,
                usage.get("outlineInputTokens", 0),
                usage.get("outlineOutputTokens", 0),
            )
        )
        saved: dict[str, Any] = {"artifactId": None, "credits": credits, "messageId": None}
        async with host.SessionLocal() as db:
            session = await db.get(ChatSession, session_id)
            user = await db.get(User, user_id)
            if session is not None and user is not None:
                # Generated title first; the session title reads as the raw prompt.
                title = (doc_title or session.title or request.strip()[:60] or "보고서")[:200]
                if carry_from and not revise.requested_title(carry_note):
                    kept = await db.get(Artifact, carry_from)
                    if kept is not None and kept.title:
                        title = kept.title
                if written:
                    # The same design `approved-plan` announced to the panel.
                    artifact_design = report_design(
                        request, approved_plan, design_tokens, req.template_id
                    )
                    data = {
                        **({"templateId": template.id} if template else {}),
                        "sections": [
                            {
                                "id": s["id"],
                                "heading": s["heading"],
                                "level": s.get("level", 1),
                                "status": "done" if (s.get("content") or "").strip()
                                else "pending",
                                "content": s.get("content") or "",
                            }
                            for s in sections
                        ],
                        "sources": sources,
                        # Kept so a later rewrite is held to the same request.
                        "request": request[:4000],
                        **({"research": research_log} if research_log is not None else {}),
                        "lint": lint.wire(lint.check(lint.from_sections(sections))),
                        # Snapshot, as for the deck.
                        **({"design": artifact_design} if artifact_design else {}),
                        **({"titleBlock": title_block} if title_block else {}),
                        "citationStyle": host._CITATION_STYLES.get(
                            str((title_block or {}).get("format") or ""), "APA"
                        ),
                        "wordCount": report_service.word_count(sections),
                    }
                    if marks := doc_marks.for_sections(bundle, data["sections"]):
                        data["marks"] = marks
                    artifact_id = await host._store_document(
                        db,
                        session,
                        user_id=user_id,
                        project_id=turn.get("project_id"),
                        kind=ArtifactKind.report,
                        title=title,
                        summary=(rerun_note(turn) or host._regeneration_summary(request))[:80],
                        data=data,
                    )
                    session.artifact_id = artifact_id
                    # The proposal has become the document.
                    session.pending = None
                    message = Message(
                        session_id=session_id,
                        role=Role.assistant,
                        content=(rerun_note(turn)
                                 or _written_note(len(written), title_block)),
                        usage={**usage, "credits": credits},
                        model=model["id"],
                        steps=host._prelude_steps(turn.get("skills_event"),
                                                  turn.get("context_steps")) or None,
                        routing=turn.get("routing"),
                    )
                    db.add(message)
                    host.settle(
                        db,
                        user,
                        credits,
                        reason="report.generate",
                        session_id=session_id,
                        model=model["id"],
                    )
                    host.record_searches(
                        db,
                        user,
                        host._searches_in(research_log),
                        session_id=session_id,
                        surface=session.kind.value,
                    )
                    saved.update(artifactId=artifact_id, messageId=message.id)
                session.updated_at = utcnow()
                db.add(session)
                await db.commit()
        bundle.document.saved = saved


class ReviseLoad(Base):
    """The document on screen and what it was written from: the session's first request
    and the files its turns carried, within the writer's file budget."""

    name = "revise-load"
    kind = "io"
    reads = ("request",)
    writes = ("document.loaded",)

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.request.turn.get("user_id") else "no turn to load for"

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        host, req, turn = _host(), bundle.request, bundle.request.turn
        from sqlmodel import col, select

        from app.models.chat import ChatSession, Message, Role
        from app.models.workspace import Artifact, StoredFile

        user_id, session_id = turn["user_id"], req.session_id
        async with host.SessionLocal() as db:
            session = await db.get(ChatSession, session_id)
            artifact = await db.get(Artifact, session.artifact_id) if session else None
            if session is None or artifact is None or not artifact.data:
                bundle.document.loaded = {"missing": True}
                return
            loaded: dict[str, Any] = {"kind": artifact.kind.value, "data": dict(artifact.data),
                                      "title": artifact.title or "", "artifactId": artifact.id}
            # The request the document was written from and the files it drew on, so a
            # rewrite keeps to the material instead of inventing plausible values.
            turns = (
                await db.exec(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(col(Message.created_at))
                )
            ).all()
            asked = [m for m in turns if m.role is Role.user and (m.content or "").strip()]
            loaded["request"] = str(asked[0].content if asked else session.title or "")
            file_ids = list(
                dict.fromkeys(
                    str(a.get("id"))
                    for m in asked
                    for a in (m.attachments or [])
                    if isinstance(a, dict) and a.get("id")
                )
            )
            material: list[str] = []
            if file_ids:
                files = (
                    await db.exec(
                        select(StoredFile).where(
                            col(StoredFile.id).in_(file_ids), StoredFile.user_id == user_id
                        )
                    )
                ).all()
                budget = host.file_budget(turn["model"])
                for stored in sorted(files, key=lambda f: file_ids.index(f.id)):
                    if not stored.text or budget <= 0:
                        continue
                    text = stored.text[:budget]
                    budget -= len(text)
                    material.append(f"## {stored.name}\n{text}")
            loaded["material"] = material
            loaded["fileIds"] = file_ids
        bundle.document.loaded = loaded


class ReviseSave(Base):
    """The revised document stored as a new version (the old body kept), the files the
    same turn asked for made from it, the message and the bill."""

    name = "revise-save"
    kind = "io"
    reads = ("request", "document", "plan.title", "findings.aligned", "usage")
    writes = ("document.saved", "document.data")

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.request.turn.get("user_id") else "no turn to store"

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        host, req, turn = _host(), bundle.request, bundle.request.turn
        from app.models.chat import ChatSession, Message, Role
        from app.models.user import User, utcnow
        from app.models.workspace import Artifact, ArtifactKind
        from app.services import lint
        from app.services import report as report_service

        model, usage = turn["model"], bundle.usage
        user_id, session_id = turn["user_id"], req.session_id
        export_after = turn.get("export_after")
        is_deck = req.is_deck
        kind = ArtifactKind.deck if is_deck else ArtifactKind.report
        parts, title, changed = bundle.document.sections, bundle.plan.title, bundle.document.changed
        aligned_step = bundle.findings.aligned
        data = dict(bundle.document.data)
        credits = host.charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
        saved: dict[str, Any] = {"artifactId": None, "credits": credits, "messageId": None,
                                 "reply": "", "handed": None}
        async with host.SessionLocal() as db:
            session = await db.get(ChatSession, session_id)
            user = await db.get(User, user_id)
            if session is not None and user is not None:
                data["slides" if is_deck else "sections"] = parts
                if not is_deck:
                    data["wordCount"] = report_service.word_count(parts)
                    data["lint"] = lint.wire(lint.check(lint.from_sections(parts)))
                    # The marks the document had, found again in the revised body.
                    if marks := doc_marks.relocate(data.get("marks"), parts):
                        data["marks"] = marks
                    else:
                        data.pop("marks", None)
                # `_store_document` snapshots the previous body as a version.
                artifact_id = await host._store_document(
                    db,
                    session,
                    user_id=user_id,
                    project_id=session.project_id,
                    kind=kind,
                    title=title or "문서",
                    summary=(rerun_note(turn) or req.instruction.strip()[:80] or "문서 수정")[:80],
                    data=data,
                )
                session.artifact_id = artifact_id
                reply = f"{changed}곳을 고쳤습니다."
                if note := rerun_note(turn):
                    reply = f"{note} {reply}"
                handed = None
                if export_after is not None:
                    await db.flush()
                    revised = await db.get(Artifact, artifact_id)
                    if revised is not None:
                        handed, said = await host._export_files(
                            revised, export_after, user_id=user_id, session_id=session_id
                        )
                        reply = f"{reply} {said}"
                message = Message(
                    session_id=session_id,
                    role=Role.assistant,
                    content=reply,
                    usage={**usage, "credits": credits},
                    model=model["id"],
                    routing=turn.get("routing"),
                    steps=[x for x in (
                        {k: v for k, v in (aligned_step or {}).items() if k != "type"}, handed
                    ) if x] or None,
                )
                db.add(message)
                host.settle(
                    db,
                    user,
                    credits,
                    reason="document.revise",
                    session_id=session_id,
                    model=model["id"],
                    surface=session.kind.value,
                )
                session.updated_at = utcnow()
                db.add(session)
                await db.commit()
                saved.update(artifactId=artifact_id, messageId=message.id, reply=reply,
                             handed=handed)
        bundle.document.data = data
        bundle.document.saved = saved


for _stage in (ReportSave(), ReviseLoad(), ReviseSave()):
    registry.register(_stage)


def _written_note(count: int, title_block: dict | None) -> str:
    """The turn's line under a finished document, in the document's own genre word:
    「5개 절로 회의록 작성을 마쳤습니다.」; a plain report keeps 「5개 섹션으로 보고서를
    작성했습니다.」."""
    from app.services import doc_formats

    found = doc_formats.BY_ID.get(str((title_block or {}).get("format") or ""))
    genre = found.label if found else ""
    if not genre or genre == "보고서":
        return f"{count}개 섹션으로 보고서를 작성했습니다."
    return f"{count}개 절로 {genre} 작성을 마쳤습니다."


__all__ = ["rerun_note"]

