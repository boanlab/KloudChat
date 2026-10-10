"""A chat answer's stages after the agent loop: the code that stood at the end of
`sessions._run_turn`, cut along its steps.

The agent loop streams the answer; these stages normalize it, trace its values with the
agent's `post` check skills, measure a cover letter's blocks against their limits, and —
when anything changed — retract the streamed text and send the finished one. The routing
and storage that follow stay with the router.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from app.pipeline import registry
from app.pipeline.bundle import Bundle, Document, Material, Request
from app.pipeline.stage import Base, Context
from app.services import cover_letter, freshness, internal_words, units
from app.services.chat_format import normalize_raw_payload

log = logging.getLogger("app.routers.sessions")


class Normalize(Base):
    """The notice the answer policy owes, the raw payload unwrapped, the units and sums a
    model slips on mended by code, and — when skills were on — the script reports taken out."""

    name = "answer-normalize"
    reads = ("request",)
    writes = ("document.answer",)

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        req = bundle.request
        first = req.first_message
        normalized = freshness.normalize_answer_notice(
            bundle.document.streamed, req.text, req.model_info, req.actual_model)
        # This is the current stored user text, not the merged reference envelope.
        normalized = normalize_raw_payload(normalized, first)
        # A money table labelled in the wrong unit for the amounts the person gave.
        normalized = units.fix_unit_labels(normalized, first)
        # An amount the person gave, copied a power of ten off into a calculation.
        normalized = units.fix_magnitude_slips(normalized, first)
        # A written 「A × B = D」 whose D is not the product (a 만/억 slip, a dropped zero).
        normalized = units.fix_written_sums(units.fix_written_arithmetic(normalized))
        if req.skill_keys or req.check_keys:
            # A skill's script ran: its report lines are not the person's answer.
            normalized = internal_words.strip_reports(normalized)
        bundle.document.answer = normalized


class TextFit(Base):
    """A cover letter's 「[제출용 답안 n]」 blocks (or its numbered question headings)
    measured by code against the stated limits and rewritten up to twice when out of range;
    the counts go under the answer."""

    name = "text-fit"
    kind = "check"
    reads = ("request", "material.given", "document.answer")
    writes = ("document.answer", "document.kept_steps", "document.length_check")

    def skip(self, bundle: Bundle) -> str:
        if not cover_letter.blocks(bundle.document.answer, bundle.request.text,
                                   bundle.material.given):
            return "no submit blocks in the answer"
        return ""

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        from app.services import report

        req, doc = bundle.request, bundle.document
        yield {"type": "step", "category": "thinking", "id": "text_fit",
               "label": "글자 수 재는 중", "status": "running"}
        try:
            fitted = await cover_letter.post_process(
                doc.answer, request=req.text, material=bundle.material.given,
                complete=report._complete, model=req.model, api_key=req.api_key,
                usage=bundle.usage, judge=req.outline_model,
            )
        except Exception:  # noqa: BLE001 — the answer stands unmeasured; the step says so
            log.exception("cover letter post-processing failed for session %s", req.session_id)
            fitted = None
        if fitted is not None:
            doc.answer = fitted.content
            doc.length_check = cover_letter.wire(fitted)
        fit_step = {
            "category": "thinking", "id": "text_fit",
            "label": "글자 수(코드 측정)" if fitted is not None and fitted.rows
            else "글자 수 기준 없음" if fitted is None else "자료 대조",
            "status": "error" if fitted is not None and (
                fitted.flags or fitted.repeats
                or any(r.get("status") != "ok" for r in fitted.rows)
            ) else "done",
            **({"detail": fitted.detail[:1500]}
               if fitted is not None and fitted.detail else {}),
        }
        doc.kept_steps = [row for row in doc.kept_steps if row.get("id") != "text_fit"] + [
            fit_step]
        yield {"type": "step", **fit_step}


class Replace(Base):
    """The finished answer in place of the streamed one: the client retracts the first
    match, so the whole answer is replaced."""

    name = "answer-replace"
    reads = ("document.answer",)
    writes = ()

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.document.answer != bundle.document.streamed else "nothing changed"

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        yield {"type": "retract", "text": bundle.document.streamed}
        yield {"type": "delta", "text": bundle.document.answer}


for _stage in (Normalize(), TextFit(), Replace()):
    registry.register(_stage)


def bundle_for(*, answer: str, request: str, model: dict, actual_model: str | None,
               api_key: str, session_id: str, today: Any, first_message: str,
               messages: list[dict], material: str,
               check_keys: tuple[str, ...], check_material: str,
               tool_outputs: list[str], judge: str, kept_steps: list[dict],
               usage: dict[str, Any], skill_keys: tuple[str, ...] = (),
               check_options: dict | None = None,
               skill_specs: dict | None = None) -> Bundle:
    """A chat answer's bundle: the streamed answer, the turn's words and material."""
    req = Request(
        text=request, model=model["id"], api_key=api_key, outline_model=judge,
        session_id=session_id, today=today,
        check_keys=tuple(check_keys), model_info=model, actual_model=actual_model,
        first_message=first_message, messages=list(messages),
        check_material=check_material, tool_outputs=list(tool_outputs),
        skill_keys=tuple(skill_keys), check_options=dict(check_options or {}),
        skill_specs=dict(skill_specs or {}),
    )
    return Bundle(
        request=req, material=Material(given=material),
        document=Document(streamed=answer, answer=answer, kept_steps=list(kept_steps)),
        usage=usage,
    )


__all__ = ["bundle_for"]
