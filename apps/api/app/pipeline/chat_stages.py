"""Chat answer stages after the agent loop: normalize, trace, fit, replace."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.pipeline import registry
from app.pipeline.bundle import Bundle, Document, Material, Request
from app.pipeline.stage import Base, Context
from app.services import (
    cover_letter,
    current_evidence,
    freshness,
    internal_words,
    superlatives,
    units,
    weekdays,
)
from app.services.chat_format import normalize_raw_payload, unlink_local_targets

log = logging.getLogger("app.routers.sessions")


class Normalize(Base):
    """Code fixes for model slips: policy notice, raw payload, units/sums, script reports."""

    name = "answer-normalize"
    reads = ("request", "material.given")
    writes = ("document.answer", "document.kept_steps")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        before = {row.get("id") for row in bundle.document.kept_steps}
        await self.apply(bundle, ctx)
        for row in bundle.document.kept_steps:
            if row.get("id") == "answer_checks" and "answer_checks" not in before:
                yield {"type": "step", **row}

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        req = bundle.request
        streamed = internal_words.strip_echoed_heading(bundle.document.streamed)
        if req.traced or req.evidence or req.check_material.strip():
            # grounded in the person's material or this turn's search: no training-time caveat
            streamed = current_evidence.strip_caveat(streamed)
        # verification notes are the steps' to show, not the body's
        streamed, checked = internal_words.strip_check_block(streamed)
        if checked:
            bundle.document.kept_steps = [
                row for row in bundle.document.kept_steps if row.get("id") != "answer_checks"
            ] + [{"category": "thinking", "id": "answer_checks", "label": "작성 중 점검 결과",
                  "status": "done", "detail": checked[:1500]}]
        first = req.first_message
        normalized = freshness.normalize_answer_notice(
            streamed, req.text, req.model_info, req.actual_model)
        # `first`: stored user text, not the merged reference envelope
        normalized = normalize_raw_payload(normalized, first)
        # wrong unit label on a money table
        normalized = units.fix_unit_labels(normalized, first)
        # given amount off by a power of ten
        normalized = units.fix_magnitude_slips(normalized, first)
        # won amount scaled twice (「10,000,000만 원」)
        normalized = units.fix_double_scale(normalized, first)
        # wrong product in 「A × B = D」
        normalized = units.fix_written_sums(units.fix_written_arithmetic(normalized))
        # sweeping superlatives narrowed to the table's evidence
        normalized = superlatives.narrow_sweeping_claims(normalized)
        # a tool call written out as text, never shown
        normalized = internal_words.strip_tool_calls(normalized)
        if req.skill_keys or req.check_keys:
            # skill script report lines
            normalized = internal_words.strip_reports(normalized)
        normalized = internal_words.strip_tool_apologies(normalized)
        # sandbox links open nothing; files are in the chips
        made = {str(out.get("name") or "") for step in bundle.document.kept_steps
                for out in (step.get("outputs") or []) if isinstance(out, dict)}
        normalized = unlink_local_targets(normalized, made - {""})
        # offset dates fixed first so the weekday fix doesn't bend to a wrong day
        normalized = _dates_from_given(normalized, req, bundle.material.given)
        # head-counted weekday set to the calendar's
        normalized, _ = weekdays.fix_weekdays(
            normalized, context=f"{req.text}\n{bundle.material.given}",
            today=req.today if isinstance(req.today, dt.date) else None)
        bundle.document.answer = normalized


def _dates_from_given(answer: str, req: Request, material: str) -> str:
    """Dates offset from the person's own date recomputed by the date-calc script."""
    from app.services import skill_scripts

    today = req.today if isinstance(req.today, dt.date) else dt.date.today()
    try:
        checked = skill_scripts.module("date-calc", "dates").post(answer, {
            "request": req.text, "material": material, "today": today.isoformat(),
            "anchored_only": True,
        })
    except Exception:  # noqa: BLE001 — the answer stands as written
        log.exception("date check failed for session %s", req.session_id)
        return answer
    return str(checked.get("text") or answer)


class TextFit(Base):
    """Cover-letter blocks measured against stated limits; rewritten up to twice."""

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
    """Finished answer replaces the streamed one (client retracts the first match)."""

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
               skill_specs: dict | None = None, traced: bool = False,
               evidence: list[str] | None = None) -> Bundle:
    """Chat answer bundle: streamed answer, turn text, material."""
    req = Request(
        text=request, model=model["id"], api_key=api_key, outline_model=judge,
        session_id=session_id, today=today,
        check_keys=tuple(check_keys), model_info=model, actual_model=actual_model,
        first_message=first_message, messages=list(messages),
        check_material=check_material, tool_outputs=list(tool_outputs),
        skill_keys=tuple(skill_keys), check_options=dict(check_options or {}),
        skill_specs=dict(skill_specs or {}), traced=traced, evidence=list(evidence or []),
    )
    return Bundle(
        request=req, material=Material(given=material),
        document=Document(streamed=answer, answer=answer, kept_steps=list(kept_steps)),
        usage=usage,
    )


__all__ = ["bundle_for"]
