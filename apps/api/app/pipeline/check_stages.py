"""Check skills as stages of the `check` kind.

A check skill (`app/skills/*-check`, `check_skills`) names its stage in its SKILL.md: a `pre`
check finds what the material does not give before anything is written (one question card);
a `post` check traces every value the finished text states to the material or to what code
computed, blanks the rest, and checks again. Here each is a stage a pipeline file lists:

- `check:gaps` — the attached `pre` checks over the request and material;
- `gap-card` — the card those findings put up before writing, a stage the run waits at;
- `check:grounding` / `check:grounding-again` — the attached `post` checks over a report's
  sections, before the verify stage and again after its rewrites;
- `check:answer-grounding` — the same over a chat answer;
- `check:copy` — a chat answer written with a copy skill, checked as copy (`copy-check`).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.pipeline import registry
from app.pipeline.bundle import Bundle
from app.pipeline.stage import Base, Context, Wait
from app.services import check_skills, copy_check, key_figures


class GapCheck(Base):
    """What the format needs and the material does not give: the attached `pre` checks'
    findings, asked on one card before the first draft (never on a revision or a
    follow-up chat turn)."""

    name = "check:gaps"
    kind = "check"
    reads = ("request",)
    writes = ("findings.gaps",)

    def skip(self, bundle: Bundle) -> str:
        if not bundle.request.first_draft:
            return "not the first draft"
        return ""

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        req = bundle.request
        bundle.findings.gaps = check_skills.gaps(
            tuple(req.check_keys or ()), facts=req.check_facts, request=req.text,
            material=req.check_material, today=req.today)


#: Questions on one card, at most.
_CARD = 5


def card_questions(bundle: Bundle) -> list[dict]:
    """The card before writing: the router's file questions, then one per missing fact
    (its fact key, label, why and the blank it stays as), less what the last card asked."""
    card = bundle.request.turn.get("card") or {}
    asked = set(card.get("asked") or [])
    out = [{**q, "fact": "", "label": "", "why": str(q.get("detail") or ""),
            "placeholder": "", "freeText": True} for q in card.get("files") or []]
    for gap in bundle.findings.gaps:
        wire = check_skills.questions([gap])[0].wire()
        out.append({**wire, "fact": str(gap.get("fact") or ""),
                    "label": str(gap.get("label") or ""), "why": str(gap.get("why") or ""),
                    "placeholder": str(gap.get("placeholder") or ""), "freeText": True})
    return [q for q in out if q.get("id") not in asked][:_CARD]


class GapCard(Base):
    """The question card before the first draft, as a stage the run waits at: what the
    files did not deliver and what the `pre` checks found missing, on one card. The run
    stops `waiting`; the answers come back as the next message (merged as material), or
    「있는 자료로 진행」 leaves each fact its bracket blank."""

    name = "gap-card"
    kind = "io"
    reads = ("request", "findings.gaps")
    writes = ("findings.questions",)

    def skip(self, bundle: Bundle) -> str:
        card = bundle.request.turn.get("card")
        if card is None:
            return "no card on this turn"
        if not card.get("may_ask"):
            return "a reply to the card already up"
        if not (card.get("plans_first") or bundle.findings.gaps):
            return "nothing missing"
        if not card_questions(bundle):
            return "nothing left to ask"
        return ""

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        questions = card_questions(bundle)
        bundle.findings.questions = questions
        raise Wait("waiting for answers", summary=f"질문 {len(questions)}개",
                   evidence={"questions": questions})
        yield {}  # an async generator


def _trace(bundle: Bundle) -> tuple[str, list[str], dict]:
    from app.services import report

    prepass, settled = bundle.computed.prepass, bundle.computed.settled
    material = "\n".join([bundle.request.text, bundle.material.given, *bundle.material.document])
    computed = [
        *(prepass.blocks if prepass is not None else []), key_figures.block(settled),
        bundle.computed.values or "",
        *(report._source_text(row) for row in (bundle.material.sources or [])),
    ]
    options = check_skills.options_for(
        bundle.spec.format_id, dt.datetime.now(ZoneInfo(settings.timezone)).date())
    return material, computed, options


class GroundCheck(Base):
    """The attached `post` check skills (grounding-check) over every section: check, fix
    once, check again — a value no source holds becomes a bracket blank.

    `again` runs after the verify stage, which rewrote sentences: what it brought in is
    traced once more and what stays is counted as left."""

    kind = "check"
    reads = ("request", "material", "computed", "spec.format_id", "findings.check_keys",
             "document.sections")
    writes = ("document.sections", "findings.traced", "findings.before", "findings.after")

    def __init__(self, name: str, *, again: bool) -> None:
        self.name, self.again = name, again

    def skip(self, bundle: Bundle) -> str:
        if not bundle.material.given.strip():
            return "no material to trace against"
        if not bundle.spec.format_id:
            return "no document format"
        return ""

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        found = bundle.findings
        material, computed, options = _trace(bundle)
        bundle.document.sections, outcome = check_skills.ground_sections(
            found.check_keys, bundle.document.sections, material=material, computed=computed,
            options=options)
        found.before += ["ungrounded_value"] * len(outcome.found)
        if not self.again:
            found.traced = outcome
        else:
            found.traced.found += outcome.found
            found.traced.left = outcome.left
            found.after += ["ungrounded_value"] * len(outcome.left)



class AnswerGrounding(Base):
    """Every value the answer states traces to the person's words and files or to what the
    tools computed this turn and earlier; the rest become bracket blanks."""

    name = "check:answer-grounding"
    kind = "check"
    reads = ("request", "material.given", "document.answer")
    writes = ("document.answer", "document.kept_steps")

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.request.check_keys else "no check skills attached"

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        req = bundle.request
        traced = check_skills.ground(
            tuple(req.check_keys or ()), bundle.document.answer,
            material=f"{bundle.material.given}\n{req.check_material}",
            computed=[*req.tool_outputs, *(str(m.get("content") or "") for m in req.messages
                                           if m.get("role") == "assistant")],
            options=check_skills.options_for(req.check_facts, req.today),
        )
        if not traced.found:
            return
        bundle.document.answer = traced.text
        ground_step = {
            "category": "thinking", "id": "grounding",
            "label": f"근거 없는 값 {len(traced.found)}개 → 빈칸",
            "status": "error" if traced.left else "done",
            "detail": traced.summary[:1500],
        }
        bundle.document.kept_steps = [row for row in bundle.document.kept_steps
                                      if row.get("id") != "grounding"] + [ground_step]
        yield {"type": "step", **ground_step}


class CopyCheck(Base):
    """A chat answer written with a copy skill, checked as copy (`copy-check`): unsupported
    numbers, offers, testimonials and qualifiers fixed by code; risky claims, statements no
    source holds and pieces over their channel limit rewritten once by the writer, then
    checked again (`services/copy_check.py`)."""

    name = "check:copy"
    kind = "check"
    reads = ("request", "material.given", "document.answer")
    writes = ("document.answer", "document.kept_steps")

    def skip(self, bundle: Bundle) -> str:
        req = bundle.request
        if not copy_check.active((*req.skill_keys, *(req.check_keys or ())), req.tool_outputs):
            return "no copy skill on this turn"
        return ""

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        from app.services import report

        req, doc = bundle.request, bundle.document
        yield {"type": "step", "category": "thinking", "id": "copy_check",
               "label": "카피 점검 중", "status": "running"}
        outcome = await copy_check.run(
            doc.answer, request=req.text,
            material=f"{bundle.material.given}\n{req.check_material}",
            complete=report._complete, model=req.model, judge=req.judge, api_key=req.api_key,
            usage=bundle.usage,
            skills=copy_check.on_turn((*req.skill_keys, *(req.check_keys or ())), req.tool_outputs))
        doc.answer = outcome.text
        still = [r for r in outcome.rows if r.get("hard") and (r["over"] or r["under"])]
        step = {
            "category": "thinking", "id": "copy_check",
            "label": (f"카피 점검: 근거 없는 표현 {len(outcome.found)}개"
                      + (f" · 한도 초과 {len(outcome.over)}개" if outcome.over else "")
                      if outcome.changed else "카피 점검: 고칠 것 없음"),
            "status": "error" if still else "done",
            **({"detail": outcome.detail[:1500]} if outcome.detail else {}),
        }
        doc.kept_steps = [row for row in doc.kept_steps if row.get("id") != "copy_check"] + [step]
        yield {"type": "step", **step}


for _stage in (GapCheck(), GapCard(), GroundCheck("check:grounding", again=False),
               GroundCheck("check:grounding-again", again=True), AnswerGrounding(), CopyCheck()):
    registry.register(_stage)
