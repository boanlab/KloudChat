"""`io` stages loading a turn's document and storing its result (router helpers via module)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from app.pipeline import marks as doc_marks
from app.pipeline import registry
from app.pipeline.bundle import Bundle
from app.pipeline.stage import Base, Context
from app.services import output_language

log = logging.getLogger("app.routers.sessions")


def _host():
    # late import: router imports the pipeline
    from app.routers import sessions

    return sessions


def rerun_note(turn: dict[str, Any]) -> str:
    """Rerun message prefix: 「검증」 단계부터 다시 만들었습니다."""
    from app.pipeline.labels import label

    stage = str(turn.get("rerun_from") or "")
    # composed step: own `rerun_label`; shipped stage: registry label
    name = str(turn.get("rerun_label") or "") or label(stage)
    return f"「{name}」 단계부터 다시 만들었습니다." if stage else ""


def last_pass(bundle: Bundle, sections: list[dict], template: Any,
              sources: list[dict]) -> list[tuple[int, dict]]:
    """(index, section) for each section the report's last pass changes: weekdays set to the
    calendar (`weekdays`) and the guidance's example values and rule lines kept out
    (`guide_echo`), after every stage that may rewrite a section."""
    import datetime as dt

    from app.pipeline.report_stages import _material_text
    from app.services import guide_echo, weekdays
    from app.services import report as report_service

    req = bundle.request
    material = _material_text(bundle)
    today = req.today if isinstance(req.today, dt.date) else None
    guide = guide_echo.guidance(req.skill_keys, req.skill_specs, template)
    seen = [(today or dt.date.today()).isoformat(), req.text, material, *req.tool_outputs,
            *req.evidence, *(report_service._source_text(row) for row in sources or [])]
    changed = []
    for i, section in enumerate(sections):
        body = str(section.get("content") or "")
        fixed, _ = weekdays.fix_weekdays(body, context=f"{req.text}\n{material}", today=today)
        if guide:
            fixed = guide_echo.scrub(fixed, guide, seen).text
        if fixed != body:
            changed.append((i, {**section, "content": fixed}))
    return changed


def removed_said(removed: list[str]) -> str:
    """「7장의 흐름도를 뺐습니다.」 for the pictures, charts and tables an edit took off."""
    from app.services import revise

    return f"{', '.join(removed)}{revise._particle(removed[-1], '을', '를')} 뺐습니다."


def _numbers(numbers: list[int]) -> str:
    """「1·4·7~9」: part numbers, a run of three or more as a range."""
    runs: list[list[int]] = []
    for n in numbers:
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    return "·".join(f"{r[0]}~{r[-1]}" if len(r) >= 3 else "·".join(map(str, r)) for r in runs)


#: an edit's reply words in English, for an ask typed in English
_EN_THINGS = {"차트": "chart", "그림": "picture", "도식": "diagram", "글머리표": "bullet list",
              "번호 목록": "numbered list", "표": "table", "지표": "metrics",
              "큰 숫자": "big number", "연표": "timeline", "단계": "steps", "카드": "cards"}


def _en_item(item: str) -> str:
    """「4장의 차트」 → 「the chart on slide 4」, 「표 5」 → 「Table 5」, 「3줄 요약」 → 「the
    3-line summary」, 「3장 표로 바꾸기」 → 「slide 3 as a table」."""
    import re

    if m := re.fullmatch(r"(\d{1,3})(장|절)의 (「[^」]{1,80}」) 상자", item):
        return f"the box {m.group(3)} from {'slide' if m.group(2) == '장' else 'section'} " \
            f"{m.group(1)}"
    if m := re.fullmatch(r"(\d{1,3})(장|절)의 (.+)", item):
        where = f"{'slide' if m.group(2) == '장' else 'section'} {m.group(1)}"
        return f"{_en_item(m.group(3))} from {where}" if m.group(3)[:1] in "표그" and re.search(
            r"\d", m.group(3)) else f"the {_EN_THINGS.get(m.group(3), 'diagram')} on {where}"
    if m := re.fullmatch(r"(표|그림)\s?(\d{1,3})", item):
        return f"{'Table' if m.group(1) == '표' else 'Figure'} {m.group(2)}"
    if m := re.fullmatch(r"(\d{1,2})줄 요약", item):
        return f"the {m.group(1)}-line summary"
    if m := re.fullmatch(r"(\d{1,3})장 (.+?)(?:으로|로) 바꾸기", item):
        return f"slide {m.group(1)} as a {_EN_THINGS.get(m.group(2), m.group(2))}"
    if m := re.fullmatch(r"(\d{1,3})장 항목 (합치기|나누기)", item):
        return f"{'joining' if m.group(2) == '합치기' else 'splitting'} the items on slide " \
            f"{m.group(1)}"
    if m := re.fullmatch(r"(\d{1,3})장 (그림|차트) 빼기", item):
        return f"removing the {_EN_THINGS[m.group(2)]} on slide {m.group(1)}"
    return item


def _english_reply(document: Any, summary: int, touched: list[int], is_deck: bool) -> str:
    unit = "slide" if is_deck else "section"
    said = []
    if document.removed:
        said.append("Removed " + ", ".join(map(_en_item, document.removed)) + ".")
    if summary:
        said.append(f"Put a {summary}-line summary at the top.")
    if touched:
        numbers = _numbers(sorted(i + 1 for i in touched)).replace("·", ", ").replace("~", "–")
        said.append(f"Revised {unit}{'s' if len(touched) > 1 else ''} {numbers}.")
    elif not document.removed and not summary and not document.unmet and document.changed:
        said.append(f"Changed {document.changed} place{'s' if document.changed > 1 else ''}.")
    if document.missed:
        said.append(f"{'However, ' if said else ''}"
                    f"{', '.join(map(_en_item, document.missed))} could not be found in the "
                    "document. Please ask again.")
    if document.failed:
        said.append(f"{', '.join(document.failed)} kept as before: the rewrite did not pass the "
                    "checks.")
    if document.unmet:
        said.append(f"{'However, ' if said else ''}{', '.join(map(_en_item, document.unmet))} "
                    "did not show in the result. Please ask again.")
    for part, asked, got in getattr(document, "counted", []):
        said.append(f"Section {part} has {got} sentence{'s' if got != 1 else ''}, not the "
                    f"{asked} asked.")
    if already := getattr(document, "already", []):
        said.append(already_said(already, english=True))
    if unlisted := getattr(document, "unlisted", []):
        said.append(unlisted_said(unlisted, english=True))
    for added in getattr(document, "added", []):
        at, _, values = added.partition(" ")
        said.append(f"Section {at[:-1]} now states {values}, which the section and the "
                    "material did not; please check.")
    for lost in document.dropped:
        at, _, values = lost.partition(" ")
        where = f"{'slide' if at.endswith('장') else 'section'} {at[:-1]}"
        said.append(f"On {where}, {values} dropped out of the rewrite; ask again to put them "
                    "back if they belong there.")
    return " ".join(said)


def already_said(already: list[tuple[int, int]], *, english: bool = False) -> str:
    """「2절은 이미 2문장이어서 그대로 두었습니다.」 for parts that had the count asked."""
    if english:
        return " ".join(f"Section {part} already has {asked} sentence{'s' if asked != 1 else ''}"
                        ", so it was kept as it was." for part, asked in already)
    return " ".join(f"{part}절은 이미 {asked}문장이어서 그대로 두었습니다."
                    for part, asked in already)


def unlisted_said(unlisted: list[tuple[int, int, str]], *, english: bool = False) -> str:
    """「5장에는 합치거나 나눌 항목 목록이 없어 그대로 두었습니다.」 / 「6장은 항목 목록 없이
    흐름도만 있어 …」 / 「7장은 이미 항목이 2개여서 …」 for slides asked to join or split items
    kept as they were."""
    if english:
        return " ".join(
            f"Slide {n} shows only a figure, no list of items to join or split, so it was kept "
            "as it was." if not held and drawn else
            f"Slide {n} has no list of items to join or split, so it was kept as it was."
            if not held else f"Slide {n} already has {held} item{'s' if held != 1 else ''}, "
            "so it was kept as it was." for n, held, drawn in unlisted)
    return " ".join(f"{n}장은 항목 목록 없이 {drawn}만 있어 그대로 두었습니다."
                    if not held and drawn else
                    f"{n}장에는 합치거나 나눌 항목 목록이 없어 그대로 두었습니다." if not held
                    else f"{n}장은 이미 항목이 {held}개여서 그대로 두었습니다."
                    for n, held, drawn in unlisted)


def revision_reply(document: Any, plan: Any, *, is_deck: bool, english: bool = False) -> str:
    """What an edit did, ask by ask: what went (and where), the summary put in, the parts
    rewritten by number, what was asked but not found or not shown, and figures a slide's
    rewrite left out; in English for an ask typed in English."""
    from app.services import revise

    unit = "장" if is_deck else "절"
    removed, missed = list(document.removed), list(document.missed)
    summary = getattr(plan, "summary", 0) if plan is not None else 0
    summary = summary if summary and f"{summary}줄 요약" not in missed else 0
    unmet_slides = {u.split()[0] for u in document.unmet}
    touched = [i for i in dict.fromkeys(document.touched)
               if f"{i + 1}{unit}" not in unmet_slides]
    if english:
        return _english_reply(document, summary, touched, is_deck)
    said = []
    if removed:
        said.append(removed_said(removed))
    if summary:
        said.append(f"맨 앞에 {summary}줄 요약을 넣었습니다.")
    if touched:
        said.append(_numbers(sorted(i + 1 for i in touched)) + f"{unit}을 고쳤습니다.")
    elif not removed and not summary and not document.unmet and document.changed:
        # by hand (a term or an order): the count is what changed
        said.append(f"{document.changed}곳을 고쳤습니다.")
    if missed:
        said.append(f"{'다만 ' if said else ''}요청한 {', '.join(missed)}"
                    f"{revise._particle(missed[-1], '은', '는')} 문서에서 찾지 못했습니다. "
                    "다시 요청해 주세요.")
    if failed := document.failed:
        said.append(f"{', '.join(failed)}은 고친 글이 검사를 통과하지 못해 이전 내용을 그대로 "
                    "두었습니다.")
    if unmet := document.unmet:
        # an asked change the new slides do not show is said, not passed off
        said.append(f"다만 요청한 {', '.join(unmet)}는 반영되지 않았습니다. 다시 요청해 주세요."
                    if said else f"요청한 {', '.join(unmet)}는 반영하지 못했습니다. "
                    "다시 요청해 주세요.")
    for part, asked, got in getattr(document, "counted", []):
        said.append(f"{part}절은 요청한 {asked}문장이 아니라 {got}문장입니다.")
    if already := getattr(document, "already", []):
        said.append(already_said(already))
    if unlisted := getattr(document, "unlisted", []):
        said.append(unlisted_said(unlisted))
    for added in getattr(document, "added", []):
        at, _, values = added.partition(" ")
        said.append(f"{at}에 원래 없던 {values}{revise._particle(values, '이', '가')} "
                    "들어갔습니다. 확인해 주세요.")
    for lost in document.dropped:
        # figures the slide showed that its rewrite left out: said, not passed over; a mark
        # left off a figure still there said in its own clause
        at, _, values = lost.partition(" ")
        items = values.split(", ")
        marks = ", ".join(x for x in items if x.endswith("」 표시"))
        plain = ", ".join(x for x in items if not x.endswith("」 표시"))
        gone = (f"{plain}{revise._particle(plain, '이', '가')} 빠졌고, {marks}도" if plain and marks
                else f"{plain or marks}{revise._particle(plain or marks, '이', '가')}")
        said.append(f"{at}에서 {gone} 빠졌습니다. 필요하면 다시 넣어 달라고 해 주세요.")
    return " ".join(said)


class ReportSave(Base):
    """Stores the report: carried parts, artifact (new version if one exists), message, bill."""

    name = "report-save"
    kind = "io"
    reads = ("request", "document.sections", "usage", "wire", "material.research")
    writes = ("document.sections", "document.saved")

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.request.turn.get("user_id") else "no turn to store"

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        host, req, turn = _host(), bundle.request, bundle.request.turn
        from app.models.chat import ChatSession, Message, Role
        from app.models.user import User, utcnow
        from app.models.workspace import Artifact, ArtifactKind
        from app.pipeline.report_stages import report_design
        from app.services import citations, design_templates, lint, revise
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
        # an attached file's figures credited to it, not to a web result the writer named
        research_found = bundle.material.research
        sections = citations.credit_attachments(sections, sources, citations.source_texts(
            sources, research_found.context if research_found is not None else "",
            list(req.untrusted)))
        # references numbered by first citation; venue-as-author replaced by the work's name
        sections, sources = citations.tidy(sections, sources)
        # last pass after every repair: weekdays to the calendar, the guidance's example
        # values and rule lines out (every model group)
        for i, section in last_pass(bundle, sections, template, sources):
            sections[i] = section
            yield {"type": "section", "sectionId": section.get("id"),
                   "heading": section.get("heading"), "content": section.get("content"),
                   "done": True}
        if output_language.reads_english("\n".join(s.get("content") or "" for s in sections)):
            # a document in English keeps no Korean 「(확인 필요)」 the checks put in
            for i, section in enumerate(sections):
                body = str(section.get("content") or "")
                if (english := output_language.localize_marks(body)) != body:
                    sections[i] = {**section, "content": english}
                    yield {"type": "section", "sectionId": section.get("id"),
                           "heading": section.get("heading"), "content": english, "done": True}
        bundle.document.sections = sections
        written = [s for s in sections if (s.get("content") or "").strip()]
        credits = (
            0
            if not written
            else host.charge_for_tokens(model, usage["inputTokens"], usage["outputTokens"])
            # planner tokens at planner price
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
                # generated title first; session title is the raw prompt
                title = (doc_title or session.title or request.strip()[:60] or "보고서")[:200]
                if carry_from and not revise.requested_title(carry_note):
                    kept = await db.get(Artifact, carry_from)
                    if kept is not None and kept.title:
                        title = kept.title
                if written:
                    # same design `approved-plan` announced
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
                        # later rewrites held to the same request
                        "request": request[:4000],
                        **({"research": research_log} if research_log is not None else {}),
                        "lint": lint.wire(lint.check(lint.from_sections(sections))),
                        # design snapshot, as for decks
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
                    # proposal became the document
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
    """Document on screen plus its first request and carried files, within the file budget."""

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
            # source request and files, so rewrites keep to material instead of inventing values
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
    """Stores the revision as a new version, makes requested files, message, bill."""

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
        parts, title = bundle.document.sections, bundle.plan.title
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
                    # existing marks re-found in the revised body
                    if marks := doc_marks.relocate(data.get("marks"), parts):
                        data["marks"] = marks
                    else:
                        data.pop("marks", None)
                # `_store_document` keeps the previous body as a version
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
                reply = revision_reply(bundle.document, bundle.plan.revision, is_deck=is_deck,
                                       english=output_language.typed_in_english(req.instruction))
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
    """Finish line in the document's genre word (「5개 절로 회의록 작성을 마쳤습니다.」)."""
    from app.services import doc_formats

    found = doc_formats.BY_ID.get(str((title_block or {}).get("format") or ""))
    genre = found.label if found else ""
    if not genre or genre == "보고서":
        return f"{count}개 섹션으로 보고서를 작성했습니다."
    return f"{count}개 절로 {genre} 작성을 마쳤습니다."


__all__ = ["removed_said", "rerun_note", "revision_reply"]

