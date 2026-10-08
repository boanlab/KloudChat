"""A revision's stages: one instruction applied to the document on screen.

The code that stood in the middle of `sessions._revise_document`, cut along its steps; the
router still loads the document and stores the result. `revise-route` decides what the
instruction lands on (a reorder or a term swap by hand, else the planner), `revise-widen`
finds the parts that rest on a changed fact and runs the format's pre-pass again with it,
`revise-rewrite` rewrites each part, and `consistency` reads the revised document for one
fact written two ways, repairs once and reads again.
"""

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
from app.services import consistency, doc_formats, internal_words, revise, richtext
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
    """The events a revision that ends early sends: what it says, its usage, done."""
    events = []
    if text:
        events.append({"type": "delta", "text": text})
    if error:
        events.append({"type": "error", "message": error})
    events.append({"type": "usage", **bundle.usage, "credits": 0})
    events.append({"type": "done"})
    return events


def strings(value: Any) -> list[str]:
    """Every string inside a slide, for a check that reads its words."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


class Route(Base):
    """What the instruction lands on: two parts trading places and a term replaced across
    the document are done by hand; anything else is the planner's call."""

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
        swap = revise.term_swap(instruction)
        reorder = revise.order_swap(instruction, names) if swap is None else None
        if reorder is not None:
            # Two parts trading places is a rearrangement done by hand: the model would
            # redraw the deck (and add a 목차) to move two slides.
            first, second = reorder
            parts[first], parts[second] = parts[second], parts[first]
            if not is_deck:
                # A conclusion now at the front does not re-list what follows it.
                parts = report.trim_leading_conclusion(parts)
            yield {"type": "step", "id": "route", "status": "done",
                   "label": f"순서 바꾸는 중: {names[first]} ↔ {names[second]}"}
            for part in parts:
                yield _part_event(part, is_deck)
            plan = revise.Plan(scope="parts", targets=[],
                               note=f"{names[first]} ↔ {names[second]}")
            changed = 2
        elif swap is not None:
            # A term replacement across the document is done by hand: exact, instant, and
            # nothing else moves. The model is not asked to retype six sections for it.
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
            changed = hits
        else:
            yield {"type": "step", "id": "route", "label": "무엇을 고칠지 보는 중",
                   "status": "running"}
        plan = plan or await revise.plan(
            message=instruction, title=bundle.plan.title, parts=names,
            model=req.outline_model or req.model, api_key=req.api_key,
        )
        bundle.usage["inputTokens"] += plan.usage["inputTokens"]
        bundle.usage["outputTokens"] += plan.usage["outputTokens"]
        if plan.inserts:
            # New parts are written one by one and slotted in; the rest is untouched.
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
            )
        doc.sections = parts
        by_hand = swap is not None or reorder is not None
        doc.by_hand, bundle.plan.revision = by_hand, plan
        if not by_hand and not plan.revises:
            # A new-document request is said, not acted on.
            yield {"type": "step", "id": "route", "label": "새 문서 요청", "status": "done"}
            for event in _stopped(bundle, "이건 지금 문서를 고치는 요청으로 보이지 않습니다. "
                                          "새로 쓰려면 '새로 써 줘' 라고 알려 주세요."):
                yield event
            raise Stop("not a revision")
        if not by_hand:
            yield {"type": "step", "id": "route", "label": revise.label(plan, _names(bundle)),
                   "status": "done"}
        doc.changed = changed if by_hand else 0


class Widen(Base):
    """A follow-up that changes a criterion, figure, date or decision is revised wherever
    the document states or rests on it, the summary and conclusion included; the format's
    pre-pass runs again with it so the rewrites get the settled values."""

    name = "revise-widen"
    kind = "model"
    reads = ("request", "document", "material.parts", "plan.revision")
    writes = ("plan.order", "plan.widened", "computed.prepass")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req, doc = _report(), bundle.request, bundle.document
        bundle.plan.order = list(bundle.plan.revision.targets)
        if doc.by_hand or req.is_deck or not consistency.changes_content(req.instruction):
            return
        names = _names(bundle)
        widened = consistency.dependents(
            req.instruction, names, [str(p.get("content") or "") for p in doc.sections],
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
                else "사전 계산(수정 반영) 건너뜀",
                "detail": " · ".join(f"{r['skill']}: {r['summary']}" for r in pre.runs)[:1500],
            }
        if widened:
            yield {
                "type": "step", "id": "route", "status": "done",
                "label": "함께 고칠 곳: " + " · ".join(names[i] for i in widened),
            }


class Rewrite(Base):
    """Each part the revision lands on, rewritten with the rest as context and held to the
    material by code; one bad part is not a failed turn."""

    name = "revise-rewrite"
    kind = "model"
    reads = ("request", "document", "material.parts", "computed.prepass", "plan")
    writes = ("document.sections", "document.changed")

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
            yield {"type": "step", "id": f"r{index}", "label": label, "status": "running"}
            try:
                if is_deck:
                    written, spent = await deck_service.rewrite_slide(
                        request=request,
                        slides=parts,
                        target_id=str(part.get("id") or ""),
                        model=req.model,
                        api_key=req.api_key,
                        note=plan.note,
                        material=material,
                        typed=instruction,
                        notes_only=revise.notes_only(instruction),
                    )
                    if revise.meta_talk(" ".join(strings(written))):
                        raise ValueError("본문 대신 대화 답이 나옴")
                    parts[index] = written
                    yield {"type": "slide", "slide": written, "done": True}
                else:
                    body, spent = await report.rewrite_section(
                        request=request,
                        heading=label,
                        sections=richtext.normalise(parts),
                        target_id=str(part.get("id") or ""),
                        model=req.model,
                        api_key=req.api_key,
                        # The planner's paraphrase first, then the words as typed, so a
                        # detail the paraphrase dropped still lands.
                        note=(
                            consistency.dependent_note(instruction)
                            if index in widened
                            else f"{plan.note}\n사용자가 입력한 문장(그대로 반영): "
                            f"{instruction.strip()[:400]}"
                        ),
                        sources=list(data.get("sources") or []),
                        material=[*material, *settled_blocks],
                        doc_format=doc_format,
                    )
                    if not body.strip():
                        raise ValueError("빈 결과")
                    if revise.meta_talk(body):
                        # A reply to the person is not the part; the part stays as it was.
                        raise ValueError("본문 대신 대화 답이 나옴")
                    # A figure the section had stays unless the rewrite drew a new one or the
                    # person asked for it to go.
                    body = revise.keep_figure(str(part.get("content") or ""), body, instruction)
                    body = richtext.detach_tables(body)
                    # 「470만 원 × 36개월 = …」 is checked by arithmetic, not trusted.
                    body = report.trim_table_echo(
                        report.drop_redundant_kpi(
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
                    # The rewrite does not repeat what earlier sections already said, except
                    # on an add: a sentence the person asked for is kept even when it repeats.
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
                        body = report.trim_restatements(body, earlier, keep_items=gaejo)[0]
                    body = internal_words.scrub(body)
                    # The rewrite is Markdown; an old `html` flag would show literal asterisks.
                    renamed = (revise.requested_title(instruction)
                               if len(plan.targets) == 1 and index not in widened else None)
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
            yield {"type": "step", "id": f"r{index}", "label": label, "status": "done"}
        if not doc.changed:
            for event in _stopped(bundle, error="고치지 못했습니다."):
                yield event
            raise Stop("nothing changed")


class Consistency(Base):
    """One named figure, total, date or recommendation written two ways across parts is
    found by code, repaired once and read again; what stays is said in the step."""

    name = "consistency"
    kind = "check"
    reads = ("request", "document.sections", "material.parts", "computed.prepass", "plan")
    writes = ("document.sections", "findings.aligned")

    def skip(self, bundle: Bundle) -> str:
        if not bundle.plan.order or bundle.request.is_deck:
            return "nothing rewritten to align" if not bundle.plan.order else "a deck"
        if not consistency.changes_content(bundle.request.instruction):
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
        bundle.findings.aligned = aligned
        yield aligned


for _stage in (Route(), Widen(), Rewrite(), Consistency()):
    registry.register(_stage)


def bundle_for(*, request: str, instruction: str, model: dict, planner: dict | None,
               api_key: str, is_deck: bool, title: str, data: dict, parts: list[dict],
               material: list[str], plan: revise.Plan | None, format_id: str,
               usage: dict) -> Bundle:
    """A revision's bundle: the document as loaded, the instruction, the material."""
    from app.pipeline.bundle import Spec

    req = Request(text=request, model=model["id"], api_key=api_key,
                  outline_model=(planner or {}).get("id", ""), instruction=instruction,
                  is_deck=is_deck)
    return Bundle(
        request=req, spec=Spec(format_id=format_id),
        material=Material(parts=[Part(text, "attachment") for text in material]),
        plan=Plan(title=title, revision=plan),
        document=Document(sections=parts, data=data),
        usage=usage,
    )


__all__ = ["bundle_for", "strings"]
