"""Revision stages: route, widen, rewrite, consistency over the document on screen."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.core import logs
from app.pipeline import registry
from app.pipeline.bundle import Bundle, Document, Material, Part, Plan, Request
from app.pipeline.stage import Base, Context, Stop
from app.services import (
    consistency,
    doc_formats,
    internal_words,
    revise,
    revised_tables,
    richtext,
)
from app.services import deck as deck_service

log = logging.getLogger("app.routers.sessions")


def _report():
    from app.services import report

    return report


def _names(bundle: Bundle) -> list[str]:
    key = "title" if bundle.request.is_deck else "heading"
    return [str(p.get(key) or "") for p in bundle.document.sections]


def _part_event(part: dict, is_deck: bool) -> dict:
    return ({"type": "slide", "slide": part, "done": True} if is_deck else {
        "type": "section", "sectionId": part.get("id"), "heading": part.get("heading"),
        "content": part.get("content"), "done": True,
    })


def _stopped(bundle: Bundle, text: str = "", *, error: str = "") -> list[dict]:
    """Events for an early-ending revision: message, usage, done."""
    events = []
    if text:
        events.append({"type": "delta", "text": text})
    if error:
        events.append({"type": "error", "message": error})
    events.append({"type": "usage", **bundle.usage, "credits": 0})
    events.append({"type": "done"})
    return events


def _listed(names: list[str], closed: str, opened: str) -> str:
    """「표 5, 그림 3은」: names joined, the particle after the last read aloud."""
    return ", ".join(names) + revise._particle(names[-1] if names else "", closed, opened)


def _typed(bundle: Bundle) -> str:
    """The instruction as the rewrite reads it: minus what route did by hand (a title)."""
    plan = bundle.plan.revision
    return (plan.typed if plan is not None and plan.typed else "") or bundle.request.instruction


def _tidy_tables(bundle: Bundle) -> list[dict]:
    """Section events for parts the table tidy changed: one copy of each table (the revised
    part's), captions over tables, 〔표〕 resolved, block tokens gone."""
    if bundle.request.is_deck:
        return []
    parts = bundle.document.sections
    plan = bundle.plan.revision
    targets = set(plan.targets) if plan is not None else set()
    rank = {i: 2 if i in targets else 1 for i in bundle.plan.order}
    before = [str(p.get("content") or "") for p in parts]
    report = _report()
    unfenced = [p if str(p.get("format") or "markdown") != "markdown"
                else {**p, "content": report.unfence_pictures(str(p.get("content") or ""))}
                for p in parts]
    tidied = report.caption_figures_and_tables(revised_tables.tidy(unfenced, rank))
    if [str(p.get("content") or "") for p in tidied] == before:
        return []
    bundle.document.sections = tidied
    return [_part_event(part, False) for i, part in enumerate(tidied)
            if str(part.get("content") or "") != before[i]]


def strings(value: Any) -> list[str]:
    """All strings inside a slide."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


def _policy_marked(body: str, request: str, material: list[str], data: dict) -> str:
    """`body` with 「(확인 필요)」 on what states a limit, approval or rule no material does."""
    from app.services import stated_policy

    given = "\n".join([request, *material,
                       *(str(src.get("quote") or "") for src in data.get("sources") or [])])
    for piece in stated_policy.pieces(body):
        if said := stated_policy.untraced(piece, given):
            body = stated_policy.mark(body, piece, said)
    return body


class Route(Base):
    """Revision target: swaps and term replacements by hand; else the planner."""

    name = "revise-route"
    kind = "model"
    reads = ("request", "document.sections", "plan.title")
    writes = ("document.sections", "plan.title", "plan.revision", "document.changed",
              "document.by_hand")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req, doc = _report(), bundle.request, bundle.document
        is_deck, instruction, parts = req.is_deck, req.instruction, doc.sections
        names = _names(bundle)
        plan = bundle.plan.revision
        changed = 0
        typed = instruction
        rename = None if is_deck else revise.title_rename(instruction, names)
        if rename is not None and rename[1] is None:
            # the document's title, by hand; the rest of the sentence goes on without it
            bundle.plan.title = rename[0]
            typed = revise.without_title_clause(instruction)
            yield {"type": "step", "id": "route", "status": "done",
                   "label": f"제목 바꾸는 중: {rename[0]}"}
            changed = 1
            if not typed:
                plan = revise.Plan(scope="parts", targets=[], note=f"제목 → {rename[0]}")
        swap = revise.term_swap(typed) if typed else None
        reorder = revise.order_swap(typed, names) if swap is None and typed else None
        if reorder is not None:
            # swap by hand: model would redraw the deck (and add a 목차)
            first, second = reorder
            parts[first], parts[second] = parts[second], parts[first]
            if not is_deck:
                # conclusion moved to front: no re-listing what follows
                parts = report.trim_leading_conclusion(parts)
            yield {"type": "step", "id": "route", "status": "done",
                   "label": f"순서 바꾸는 중: {names[first]} ↔ {names[second]}"}
            for part in parts:
                yield _part_event(part, is_deck)
            plan = revise.Plan(scope="parts", targets=[],
                               note=f"{names[first]} ↔ {names[second]}")
            changed = 2
        elif swap is not None:
            # term swap by hand: exact, instant, nothing else moves
            old_term, new_term = swap
            parts, hits = revise.replace_term(parts, old_term, new_term)
            bundle.plan.title, title_hits = revise.replace_term(bundle.plan.title, old_term,
                                                                new_term)
            hits += title_hits
            yield {"type": "step", "id": "route",
                   "label": f"용어 바꾸는 중: {old_term} → {new_term}",
                   "status": "done" if hits else "error"}
            if not hits:
                doc.sections = parts
                for event in _stopped(bundle, f"문서에서 「{old_term}」을 찾지 못했습니다."):
                    yield event
                raise Stop("term not found")
            for part in parts:
                yield _part_event(part, is_deck)
            plan = revise.Plan(scope="parts", targets=[], note=f"{old_term} → {new_term}")
            changed += hits
        elif typed:
            yield {"type": "step", "id": "route", "label": "무엇을 고칠지 보는 중",
                   "status": "running"}
        removals = (revise.numbered_removals(typed) if typed and not is_deck and swap is None
                    and reorder is None else [])
        if removals:
            # 「표 2와 그림 1은 빼고」: those blocks and the prose citing them, by hand
            before = [str(p.get("content") or "") for p in parts]
            parts, taken, absent = revise.drop_numbered(parts, removals)
            doc.removed += taken
            doc.missed += absent
            changed += len(taken)
            rest = revise.without_numbered_removals(typed)
            yield {"type": "step", "id": "route", "status": "done" if taken else "error",
                   "label": "빼는 중: " + ", ".join(f"{k} {n}" for k, n in removals)}
            for i, part in enumerate(parts):
                if str(part.get("content") or "") != before[i]:
                    yield _part_event(part, False)
            if not changed and not rest:
                doc.sections = parts
                for event in _stopped(bundle, f"요청한 {_listed(absent, '은', '는')} 문서에서 "
                                              "찾지 못해 바꾼 것이 없습니다."):
                    yield event
                raise Stop("nothing numbered to remove")
            if rest != typed:
                if plan is not None:
                    # the planner read the whole sentence; the rest is planned again
                    bundle.usage["inputTokens"] += plan.usage["inputTokens"]
                    bundle.usage["outputTokens"] += plan.usage["outputTokens"]
                typed = rest
                plan = None if typed else revise.Plan(scope="parts", targets=[], note=", ".join(
                    taken))
                if typed and (asks := revise.section_asks(
                        typed, names, [str(p.get("content") or "") for p in parts])):
                    plan = revise.Plan(scope="parts", targets=sorted(asks[0]), note=typed,
                                       clauses=asks[0], summary=asks[1])
        visual = (is_deck and swap is None and reorder is None
                  and not (plan is not None and (plan.inserts or plan.restructures))
                  and (revise.only_removes_picture(typed) or revise.only_removes_chart(typed)))
        if visual:
            # 「그림 빼줘」 / 「차트 빼줘」: the slides that carry one, by hand, whatever the
            # planner guessed
            chart = revise.only_removes_chart(typed)
            named = revise.named_slides(typed)
            within = [i for i in range(len(parts)) if not named or i + 1 in named]
            carrying = [i for i in within if (deck_service.chart_of(parts[i]) if chart
                                              else deck_service.picture_of(parts[i]))]
            if not carrying:
                where = ("·".join(f"{n}장" for n in sorted(named)) + "에는" if named
                         else "슬라이드에")
                said = f"{where} 뺄 {'차트가' if chart else '그림이'} 없어 바꾼 것이 없습니다."
                charts = [] if chart else [i for i in within if deck_service.chart_of(parts[i])]
                if charts:
                    # a chart may be what 「그림」 means: asked, not guessed
                    said += (" 차트는 " + ", ".join(
                        f"{i + 1}장 「{parts[i].get('title') or ''}」" for i in charts)
                        + f"에 있습니다. 차트를 빼려면 「{charts[0] + 1}장 차트 빼줘」처럼 "
                        "알려 주세요.")
                yield {"type": "step", "id": "route", "status": "done",
                       "label": "뺄 차트 없음" if chart else "뺄 그림 없음"}
                for event in _stopped(bundle, said):
                    yield event
                raise Stop("nothing to remove")
            if plan is not None:
                bundle.usage["inputTokens"] += plan.usage["inputTokens"]
                bundle.usage["outputTokens"] += plan.usage["outputTokens"]
            plan = revise.Plan(scope="parts", targets=carrying, note=typed)
        plan = plan or await revise.plan(
            message=typed, title=bundle.plan.title, parts=names,
            model=req.outline_model or req.model, api_key=req.api_key,
        )
        if typed != instruction:
            plan.typed = typed
        if typed and swap is None and reorder is None and not is_deck:
            # a table edit goes where that table is, whatever the planner guessed
            plan = revise.table_target(plan, names, [str(p.get("content") or "") for p in parts],
                                       typed)
            asks = (revise.section_asks(typed, names, [str(p.get("content") or "") for p in parts])
                    if plan.scope in ("parts", "whole") else None)
            if asks is not None:
                # every ask names its part: those parts only, each with its own asks
                plan = revise.Plan(scope="parts", targets=sorted(asks[0]), note=plan.note,
                                   usage=plan.usage, typed=plan.typed, clauses=asks[0],
                                   summary=asks[1])
        bundle.usage["inputTokens"] += plan.usage["inputTokens"]
        bundle.usage["outputTokens"] += plan.usage["outputTokens"]
        if plan.inserts:
            # new parts written one by one and slotted in
            position = plan.after + 1
            for offset, name in enumerate(plan.names):
                if is_deck:
                    neighbour = parts[min(max(position - 1, 0), len(parts) - 1)]
                    parts.insert(position + offset, {
                        "id": f"sl{position + offset}_{uuid.uuid4().hex[:6]}",
                        "title": name,
                        "layout": deck_service.requested_layout(instruction)
                        or deck_service.requested_layout(plan.note)
                        or "bullets",
                        "accent": neighbour.get("accent"),
                    })
                else:
                    parts.insert(position + offset, {
                        "id": uuid.uuid4().hex,
                        "heading": name,
                        "content": "",
                        "format": "markdown",
                        "status": "done",
                    })
            plan = revise.Plan(
                scope="parts",
                targets=list(range(position, position + len(plan.names))),
                note=plan.note,
                usage=plan.usage,
                typed=plan.typed,
            )
        doc.sections = parts
        by_hand = swap is not None or reorder is not None or not typed
        doc.by_hand, bundle.plan.revision = by_hand, plan
        if not by_hand and not plan.revises:
            # new-document request: said, not acted on
            yield {"type": "step", "id": "route", "label": "새 문서 요청", "status": "done"}
            for event in _stopped(bundle, "이건 지금 문서를 고치는 요청으로 보이지 않습니다. "
                                          "새로 쓰려면 '새로 써 줘' 라고 알려 주세요."):
                yield event
            raise Stop("not a revision")
        if not by_hand:
            yield {"type": "step", "id": "route", "label": revise.label(plan, _names(bundle)),
                   "status": "done"}
        doc.changed = changed


class Widen(Base):
    """Changed fact revised wherever it's stated or relied on; pre-pass rerun for settled values."""

    name = "revise-widen"
    kind = "model"
    reads = ("request", "document", "material.parts", "plan.revision")
    writes = ("plan.order", "plan.widened", "computed.prepass")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req, doc = _report(), bundle.request, bundle.document
        plan = bundle.plan.revision
        bundle.plan.order = list(plan.targets)
        typed = _typed(bundle)
        if plan.clauses:
            # asks that each name their part: only one stating a new value reaches others
            typed = " ".join(c for asks in plan.clauses.values() for c in asks
                             if consistency._STATED_VALUE.search(c) and not revise.rewords(c))
        if doc.by_hand or req.is_deck or not consistency.changes_content(typed):
            return
        names = _names(bundle)
        widened = consistency.dependents(
            typed, names, [str(p.get("content") or "") for p in doc.sections],
            bundle.plan.order)
        bundle.plan.widened = widened
        bundle.plan.order += widened
        given = "\n\n".join(
            x for x in (report._request_material(req.text), *bundle.material.document)
            if x.strip())
        pre = await consistency.settle_again(
            bundle.spec.format_id, req.text, req.instruction, given, model=req.model,
            api_key=req.api_key)
        bundle.computed.prepass = pre
        if pre is not None and pre.runs:
            ran = [r["skill"] for r in pre.runs if not r.get("skipped")]
            yield {
                "type": "step", "id": "prepass", "status": "done",
                "label": "사전 계산(수정 반영): " + ", ".join(dict.fromkeys(ran)) if ran
                else "사전 계산(수정 반영) 해당 없음",
                "detail": " · ".join(f"{r['skill']}: {r['summary']}" for r in pre.runs)[:1500],
            }
        if widened:
            yield {
                "type": "step", "id": "route", "status": "done",
                "label": "함께 고칠 곳: " + " · ".join(names[i] for i in widened),
            }


class Rewrite(Base):
    """Per-part rewrite with context, held to material by code; one bad part ≠ failed turn."""

    name = "revise-rewrite"
    kind = "model"
    reads = ("request", "document", "material.parts", "computed.prepass", "plan")
    writes = ("document.sections", "document.changed", "document.missed", "document.failed")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req, doc = _report(), bundle.request, bundle.document
        is_deck, instruction, request = req.is_deck, req.instruction, req.text
        parts, plan, data = doc.sections, bundle.plan.revision, doc.data
        material = bundle.material.document
        names, widened = _names(bundle), bundle.plan.widened
        settled_blocks = consistency.blocks_for_rewrite(bundle.computed.prepass)
        doc_format = bundle.spec.format_id
        for index in bundle.plan.order:
            part = parts[index]
            label = names[index] or f"{index + 1}"
            # this part's own asks; an asked summary is written by code after the loop
            asks = [c for c in plan.clauses.get(index, []) if not revise.summary_lines_asked(c)]
            if not is_deck and plan.clauses and not asks and index not in widened:
                continue
            yield {"type": "step", "id": f"r{index}", "label": label, "status": "running"}
            try:
                if is_deck:
                    # a many-slide instruction: each slide reads its own ask (「3장을 표로…
                    # 8장 그림은 빼줘」 does not turn 8장 into a table)
                    asked = revise.slide_clause(instruction, index)
                    taken = []
                    if revise.removes_picture(asked) and (what := deck_service.picture_of(part)):
                        part = parts[index] = deck_service.without_picture(part)
                        taken.append(what)
                    if revise.removes_chart(asked) and deck_service.chart_of(part):
                        part = parts[index] = deck_service.without_chart(part)
                        taken.append("차트")
                    doc.removed += [f"{index + 1}장의 {what}" for what in taken]
                    if taken and (revise.only_removes_picture(asked)
                                  or revise.only_removes_chart(asked)):
                        # by hand: nothing else to write
                        yield {"type": "slide", "slide": part, "done": True}
                        doc.changed += 1
                        yield {"type": "step", "id": f"r{index}", "label": label,
                               "status": "done"}
                        continue
                if is_deck and deck_service.asks_slide_figure(asked):
                    # 「구조도로 바꿔 줘」: the slide drawn as that diagram, not rewritten as words
                    written, spent = await deck_service.figure_slide(
                        request=request,
                        slides=parts,
                        target_id=str(part.get("id") or ""),
                        instruction=asked,
                        model=req.model,
                        api_key=req.api_key,
                    )
                    parts[index] = written
                    yield {"type": "slide", "slide": written, "done": True}
                elif is_deck:
                    written, spent = await deck_service.rewrite_slide(
                        request=request,
                        slides=parts,
                        target_id=str(part.get("id") or ""),
                        model=req.model,
                        api_key=req.api_key,
                        note=plan.note if asked == instruction else asked,
                        material=material,
                        typed=asked,
                        notes_only=revise.notes_only(asked),
                    )
                    if revise.meta_talk(" ".join(strings(written))):
                        raise ValueError("본문 대신 대화 답이 나옴")
                    if isinstance(written.get("notes"), str):
                        # what the speaker says, not how the writer made it; nothing the slide
                        # shows said to be absent
                        written["notes"] = deck_service.notes_agree(
                            written, internal_words.strip_work_sentences(written["notes"]))
                    parts[index] = written
                    yield {"type": "slide", "slide": written, "done": True}
                else:
                    before = str(part.get("content") or "")
                    spent = {"inputTokens": 0, "outputTokens": 0}
                    rejected = ""
                    note = (
                        consistency.dependent_note(instruction)
                        if index in widened
                        else "Change only what the request below asks of this section; leave "
                        "every other sentence, table and figure of it as written.\n"
                        f"사용자가 이 절에 요청한 것(그대로 반영): {' / '.join(asks)}"
                        if asks
                        else f"{plan.note}\n사용자가 입력한 문장(그대로 반영): "
                        f"{(plan.typed or instruction).strip()[:400]}"
                    )
                    for _attempt in range(2):
                        body, used = await report.rewrite_section(
                            request=request,
                            heading=label,
                            sections=richtext.normalise(parts),
                            target_id=str(part.get("id") or ""),
                            model=req.model,
                            api_key=req.api_key,
                            # planner paraphrase + typed words, so dropped details still land
                            note=note + (
                                f"\nThe last version of this section was rejected: {rejected}. "
                                "Write only the section's text for its reader: no plan of the "
                                "rewrite, no notes on how it is written, no instructions."
                                if rejected else ""),
                            sources=list(data.get("sources") or []),
                            material=[*material, *settled_blocks],
                            doc_format=doc_format,
                        )
                        for key in spent:
                            spent[key] += used[key]
                        if not body.strip():
                            raise ValueError("빈 결과")
                        if revise.meta_talk(body) or report.error_text(body, request):
                            # reply to the person or an error message, not a part: keep it
                            raise ValueError("본문 대신 대화 답이 나옴")
                        # existing figure kept unless redrawn or removal asked
                        body = revise.keep_figure(before, body, instruction)
                        body = richtext.detach_tables(body)
                        # arithmetic checked, not trusted
                        body = report.trim_table_echo(
                            report.drop_kpi(
                                report.fix_point_units(
                                    report.undouble_words(
                                        report.fix_percent_formulas(
                                            report.fix_ledger_magnitudes(
                                                report.fix_products(body), request
                                            )
                                        )
                                    )
                                )
                            )
                        )
                        # no repeats of earlier sections, except requested additions
                        if not re.search(r"추가|넣어|덧붙|더\s*써|보태", instruction):
                            earlier = [
                                re.sub(r"<[^<>]{0,2000}>", " ", str(p.get("content") or ""))
                                for p in parts[:index]
                                if not is_deck
                            ]
                            gaejo = any(
                                f.id == (data.get("titleBlock") or {}).get("format")
                                and f.lists == "gaejo"
                                for f in doc_formats.FORMATS
                            )
                            body = report.trim_restatements(
                                body, earlier, keep_items=gaejo,
                                question=bool(report.QUESTION_HEADING.search(label)))[0]
                        # the writer's plan and notes, not the text; spacing as written
                        noted = internal_words.strip_work_notes(body, request=request,
                                                                document=True)
                        if noted.strip() and re.sub(r"\s", "", noted) != re.sub(r"\s", "", body):
                            body = noted
                        # a P0 the rewrite brought in: sent back once, then the part stays
                        faults = revise.new_faults(before, body, heading=label,
                                                   instruction=instruction)
                        if not faults:
                            break
                        rejected = " / ".join(faults)[:400]
                        log.warning("revision of %r failed the checks: %s", logs.safe(label),
                                    logs.safe(rejected))
                    if faults:
                        bundle.usage["inputTokens"] += spent["inputTokens"]
                        bundle.usage["outputTokens"] += spent["outputTokens"]
                        doc.failed.append(label)
                        raise ValueError(f"검사를 통과하지 못함: {rejected}")
                    body = internal_words.scrub(body)
                    # a limit, approval or rule the material only asks about: marked
                    body = _policy_marked(body, request, material, data)
                    # a part's own rename only; the document's title was set by route
                    rename = revise.title_rename(instruction, names)
                    renamed = (rename[0] if rename is not None and index not in widened and (
                        rename[1] == index or rename[1] == -1 and len(plan.targets) == 1)
                        else None)
                    parts[index] = {
                        **part,
                        **({"heading": renamed} if renamed else {}),
                        "content": richtext.tidy_tables(body),
                        "format": "markdown",
                        "status": "done",
                    }
                    parts[index].pop("factCheck", None)
                    if renamed:
                        label = renamed
                    yield {
                        "type": "section",
                        "sectionId": part.get("id"),
                        "heading": label,
                        "content": body,
                        "done": True,
                    }
            except Exception as exc:  # noqa: BLE001 — one bad part is not a failed turn
                log.warning("revision of %r failed: %s", logs.safe(label), logs.safe(exc))
                yield {"type": "step", "id": f"r{index}", "label": label, "status": "error"}
                continue
            bundle.usage["inputTokens"] += spent["inputTokens"]
            bundle.usage["outputTokens"] += spent["outputTokens"]
            doc.changed += 1
            doc.touched.append(index)
            yield {"type": "step", "id": f"r{index}", "label": label, "status": "done"}
        if not is_deck and plan.summary and parts:
            # the asked summary: its lines by the model, the block and its place by code
            yield {"type": "step", "id": "summary", "label": f"{plan.summary}줄 요약",
                   "status": "running"}
            block = await _summary(bundle, plan.summary)
            if block is not None:
                first = str(parts[0].get("content") or "")
                # an earlier 「핵심」 block replaced, not stacked
                first = re.sub(r"^\s*```callout\n핵심\n[\s\S]*?\n```\s*", "", first)
                parts[0] = {**parts[0], "content": f"{block}\n\n{first.lstrip()}",
                            "format": "markdown", "status": "done"}
                parts[0].pop("factCheck", None)
                yield {"type": "section", "sectionId": parts[0].get("id"),
                       "heading": names[0], "content": parts[0]["content"], "done": True}
                doc.changed += 1
            yield {"type": "step", "id": "summary", "label": f"{plan.summary}줄 요약",
                   "status": "done" if block is not None else "error"}
        if is_deck:
            # said only when the slide as drawn shows it (a table, the picture gone)
            doc.unmet = [f"{i + 1}장 {what}" for i in bundle.plan.order if i < len(parts)
                         for what in deck_service.unmet_asks(
                             parts[i], revise.slide_clause(instruction, i))]
        if not doc.changed:
            said = ("고친 글이 검사를 통과하지 못해 이전 버전을 그대로 두었습니다. "
                    "다시 요청해 주세요." if doc.failed else "고치지 못했습니다.")
            for event in _stopped(bundle, error=said):
                yield event
            raise Stop("nothing changed")
        for event in _tidy_tables(bundle):
            yield event
        if not is_deck:
            # said only when the asked table shape is really there
            doc.missed += revise.missing_shapes(
                _typed(bundle), [str(p.get("content") or "") for p in doc.sections])
            if plan.summary and not _summary_made(doc.sections):
                doc.missed.append(f"{plan.summary}줄 요약")


def _summary_made(parts: list[dict]) -> bool:
    return bool(parts) and str(parts[0].get("content") or "").startswith("```callout\n핵심\n")


async def _summary(bundle: Bundle, lines: int) -> str | None:
    """The summary asked at the document's start, `lines` lines; None when it did not come."""
    report, req, doc = _report(), bundle.request, bundle.document
    document = "\n\n".join(
        f"## {p.get('heading') or ''}\n"
        + re.sub(r"!\[[^\]]*\]\([^)]*\)|```callout\n핵심\n[\s\S]*?\n```", "",
                 str(p.get("content") or ""))
        for p in doc.sections)[:8000]
    head = doc.data.get("titleBlock") or {}
    fields = [str(f[1]) for f in head.get("fields") or [] if isinstance(f, list) and len(f) > 1]
    try:
        text, used = await report._complete(
            req.model,
            [{"role": "user", "content": revise.SUMMARY_PROMPT.format(
                n=lines, title=bundle.plan.title, document=document)}],
            req.api_key,
            600,
        )
    except Exception as exc:  # noqa: BLE001 — the rest of the edit stands
        log.warning("summary for the edit failed: %s", logs.safe(exc))
        return None
    bundle.usage["inputTokens"] += used.get("inputTokens", 0)
    bundle.usage["outputTokens"] += used.get("outputTokens", 0)
    return revise.summary_block(text, lines, bundle.plan.title, fields)


class Consistency(Base):
    """Cross-part inconsistencies found by code, repaired once, rechecked."""

    name = "consistency"
    kind = "check"
    reads = ("request", "document.sections", "material.parts", "computed.prepass", "plan")
    writes = ("document.sections", "findings.aligned")

    def skip(self, bundle: Bundle) -> str:
        if not bundle.plan.order or bundle.request.is_deck:
            return "nothing rewritten to align" if not bundle.plan.order else "a deck"
        if not consistency.changes_content(_typed(bundle)):
            return "the instruction changes no fact"
        return ""

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        req, doc = bundle.request, bundle.document
        parts = doc.sections
        before_parts = [str(p.get("content") or "") for p in parts]
        try:
            parts, aligned, spent = await consistency.align(
                parts, changed=set(bundle.plan.revision.targets), instruction=req.instruction,
                request=req.text, material="\n\n".join(bundle.material.document),
                pre=bundle.computed.prepass, model=req.model, api_key=req.api_key)
            bundle.usage["inputTokens"] += int(spent.get("inputTokens", 0))
            bundle.usage["outputTokens"] += int(spent.get("outputTokens", 0))
        except Exception:  # noqa: BLE001 — the revision stands; the step says it was not read
            log.exception("revision consistency check failed")
            aligned = {"type": "step", "id": "consistency", "status": "error",
                       "label": "앞뒤 맞춤 검사 실패"}
        doc.sections = parts
        for index, part in enumerate(parts):
            if str(part.get("content") or "") != before_parts[index]:
                yield {
                    "type": "section", "sectionId": part.get("id"), "heading": part.get("heading"),
                    "content": part.get("content"), "done": True,
                }
        for event in _tidy_tables(bundle):
            yield event
        bundle.findings.aligned = aligned
        yield aligned


for _stage in (Route(), Widen(), Rewrite(), Consistency()):
    registry.register(_stage)


def bundle_for(*, request: str, instruction: str, model: dict, planner: dict | None,
               api_key: str, is_deck: bool, title: str, data: dict, parts: list[dict],
               material: list[str], plan: revise.Plan | None, format_id: str,
               usage: dict, session_id: str = "", turn: dict | None = None) -> Bundle:
    """Revision bundle: loaded document, instruction, material."""
    from app.pipeline.bundle import Spec

    req = Request(text=request, model=model["id"], api_key=api_key,
                  outline_model=(planner or {}).get("id", ""), instruction=instruction,
                  is_deck=is_deck, session_id=session_id, turn=dict(turn or {}))
    return Bundle(
        request=req, spec=Spec(format_id=format_id),
        material=Material(parts=[Part(text, "attachment") for text in material]),
        plan=Plan(title=title, revision=plan),
        document=Document(sections=parts, data=data),
        usage=usage,
    )


__all__ = ["bundle_for", "strings"]
