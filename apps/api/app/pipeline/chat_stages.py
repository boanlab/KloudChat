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
    answer_plan,
    cover_letter,
    current_evidence,
    freshness,
    guide_echo,
    hangul,
    hotlines,
    internal_words,
    output_language,
    red_flags,
    stray_han,
    superlatives,
    units,
    weekdays,
)
from app.services.chat_format import normalize_raw_payload, unlink_local_targets

log = logging.getLogger("app.routers.sessions")


def _grounded(req: Request) -> bool:
    """The person's material, or a search or page read that succeeded this turn (a model's
    own search included)."""
    return bool(req.traced or req.evidence or req.check_material.strip() or any(
        out.startswith(("http://", "https://")) for out in req.tool_outputs))


class Normalize(Base):
    """Code fixes for model slips: policy notice, raw payload, units/sums, script reports."""

    name = "answer-normalize"
    reads = ("request", "material.given")
    writes = ("document.answer", "document.kept_steps")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        before = {row.get("id") for row in bundle.document.kept_steps}
        await self.apply(bundle, ctx)
        for row in bundle.document.kept_steps:
            if row.get("id") in ("answer_checks", "hotline_fix") and row.get("id") not in before:
                yield {"type": "step", **row}

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        req = bundle.request
        # reasoning the model wrote out (「[Thinking: …]」) is never the answer
        streamed = internal_words.strip_reasoning(bundle.document.streamed)
        streamed = internal_words.strip_echoed_heading(streamed)
        grounded = _grounded(req)
        if grounded:
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
            streamed, req.text, req.model_info, req.actual_model,
            grounded=grounded,
            form_fixed=answer_plan.restricted(req.text or ""))
        # `first`: stored user text, not the merged reference envelope
        normalized = normalize_raw_payload(normalized, first)
        # wrong unit label on a money table
        normalized = units.fix_unit_labels(normalized, first)
        # given amount off by a power of ten
        normalized = units.fix_magnitude_slips(normalized, first)
        # won amount scaled twice (「10,000,000만 원」)
        normalized = units.fix_double_scale(normalized, first)
        # wrong product in 「A × B = D」, wrong sums and table totals; a result a tool printed
        # is marked, never rewritten
        normalized = units.fix_written_sums(units.fix_written_arithmetic(normalized),
                                            printed=req.tool_outputs)
        # what code finds off but cannot settle is marked where it stands, every writer: a
        # formula a unit off, a figure ten times a worked result, a profit its own figures
        # do not leave
        normalized = units.mark_profit_contradictions(units.mark_scaled_restatements(
            units.mark_slipped_formulas(normalized)))
        # 「수정: 합계는 N원」 beside the wrong total: the total corrected, the line gone
        normalized = units.settle_corrections(normalized)
        # sweeping superlatives narrowed to the table's evidence; a strong writer's claim is
        # its own reading of the table
        if not req.strong:
            normalized = superlatives.narrow_sweeping_claims(normalized)
        # a tool call written out as text, never shown
        normalized = internal_words.strip_tool_calls(normalized)
        # the writer's work notes and check verdicts; never down to nothing
        noted = internal_words.strip_work_notes(normalized, request=first or req.text)
        if noted.strip():
            normalized = noted
        if req.skill_keys or req.check_keys:
            # skill script report lines
            normalized = internal_words.strip_reports(normalized)
        normalized = internal_words.strip_tool_apologies(normalized)
        # the material's 「(가상)」 test tag stays out of the copy
        normalized = internal_words.strip_test_label(normalized, first or req.text)
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
        # Cyrillic and kana slipped into Korean words (「흥미 или 즐거움」), every writer: a
        # code repair costs nothing where there is none
        normalized, slips = hangul.repair_mixed_script(
            normalized, f"{req.text}\n{bundle.material.given}", keep_sentences=True)
        if slips:
            log.info("mixed script repaired in session %s: %s", req.session_id, slips[:6])
        # a crisis line's wrong number set to the official one (「1599-9195」 → 109)
        normalized, lines = hotlines.fix(normalized, "\n".join([
            req.text, bundle.material.given, req.check_material, *req.tool_outputs,
            *req.evidence, *(str(m.get("content") or "") for m in req.messages
                             if m.get("role") == "user")]))
        if lines:
            bundle.document.kept_steps = [
                row for row in bundle.document.kept_steps if row.get("id") != "hotline_fix"
            ] + [{"category": "thinking", "id": "hotline_fix", "status": "done",
                  "label": "위기 상담 번호를 공식 번호로 고침", "detail": ", ".join(lines)[:300]}]
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


class StrayHan(Base):
    """Chinese words slipped into a Korean answer: their sentence said again in Korean once."""

    name = "answer-han"
    kind = "model"
    reads = ("request", "material.given", "document.answer")
    writes = ("document.answer",)

    def skip(self, bundle: Bundle) -> str:
        given = f"{bundle.request.text}\n{bundle.material.given}"
        if not stray_han.strays(bundle.document.answer, given):
            return "no Chinese words in the answer"
        return ""

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        from app.services import report

        req = bundle.request
        bundle.document.answer, changed = await stray_han.mend(
            bundle.document.answer, given=f"{req.text}\n{bundle.material.given}",
            complete=report._complete, model=req.model, api_key=req.api_key,
            usage=bundle.usage)
        if changed:
            log.info("stray Chinese rephrased in session %s: %s", req.session_id, changed[:6])


class UrgentLead(Base):
    """A red-flag turn's answer opens with the emergency action (`red_flags`): when the
    writer put it lower or left it out, one line in the answer's language goes first."""

    name = "answer-urgent"
    reads = ("request", "document.answer")
    writes = ("document.answer", "document.kept_steps")

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.request.urgent in red_flags.KINDS else "no red flag on this turn"

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        req, doc = bundle.request, bundle.document
        flag = red_flags.Flag(kind=req.urgent, signs=())
        led = red_flags.lead(doc.answer, flag)
        if led == doc.answer:
            return
        doc.answer = led
        step = {"category": "thinking", "id": "urgent_lead", "status": "done",
                "label": "응급 안내를 답 맨 앞에 둠"}
        doc.kept_steps = [row for row in doc.kept_steps if row.get("id") != "urgent_lead"] + [
            step]
        yield {"type": "step", **step}


# registered here, beside its class: the finish list in `pipelines/chat.toml` orders it
registry.register(UrgentLead())


def _copy_turn(req: Request) -> bool:
    from app.services import copy_check

    return copy_check.active((*req.skill_keys, *(req.check_keys or ())), req.tool_outputs,
                             req.skill_specs)


class CopyMarks(Base):
    """Copy written with a copy skill keeps no 「(확인 필요)」 inside it: the marks, from any
    check before, are listed once below as 「보내기 전 확인」. A small external writer's answer
    gets the same end box, 「확인 필요: …」, in place of inline marks (inline marks make its
    answers unusable); self-hosted and strong external writers keep marks where they stand
    (docs/architecture.md, "Mechanisms by model group")."""

    name = "copy-marks"
    reads = ("request", "document.answer")
    writes = ("document.answer",)

    def skip(self, bundle: Bundle) -> str:
        from app.services import model_tier

        req = bundle.request
        if not _copy_turn(req) and req.group != model_tier.SMALL_EXTERNAL:
            return "no copy skill on this turn"
        return "" if "확인" in bundle.document.answer else "no marks"

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        from app.services import copy_check

        bundle.document.answer = copy_check.lift_marks(
            bundle.document.answer, answer=not _copy_turn(bundle.request))


class MarkLanguage(Base):
    """An answer in English keeps no Korean mark: the checks' 「(확인 필요)」 as 「(needs
    checking)」."""

    name = "answer-marks"
    reads = ("document.answer",)
    writes = ("document.answer",)

    def skip(self, bundle: Bundle) -> str:
        if "확인" not in bundle.document.answer:
            return "no marks"
        return "" if output_language.reads_english(bundle.document.answer) else "a Korean answer"

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        bundle.document.answer = output_language.localize_marks(bundle.document.answer)


def _seen(req: Request, material: Material) -> list[str]:
    """What the person and the turn's tools gave: the only sources a value may come from."""
    today = req.today if isinstance(req.today, dt.date) else dt.date.today()
    return [today.isoformat(), req.text, req.first_message, material.given, req.check_material,
            *req.tool_outputs, *req.evidence,
            *(str(m.get("content") or "") for m in req.messages if m.get("role") == "user")]


class GuideEcho(Base):
    """The guidance's example names and dates and its rule lines kept out of the answer
    (`services/guide_echo.py`); every model group. Runs after every check that may rewrite."""

    name = "answer-echo"
    reads = ("request", "material.given", "document.answer")
    writes = ("document.answer", "document.kept_steps")

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.request.skill_keys else "no skill on this turn"

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        req, doc = bundle.request, bundle.document
        guide = guide_echo.guidance(req.skill_keys, req.skill_specs)
        echoes = guide_echo.scrub(doc.answer, guide, _seen(req, bundle.material))
        if not echoes.changed:
            return
        doc.answer = echoes.text
        step = {"category": "thinking", "id": "guide_echo", "status": "done",
                "label": "스킬 예시·지시문을 답에서 뺌", "detail": echoes.note()[:300]}
        doc.kept_steps = [row for row in doc.kept_steps if row.get("id") != "guide_echo"] + [step]
        yield {"type": "step", **step}


class Weekdays(Base):
    """「M월 D일(요일)」 set to the calendar once more, after every stage that may rewrite the
    answer (a later rewrite can bring wrong weekdays back); every model group."""

    name = "answer-weekdays"
    reads = ("request", "material.given", "document.answer")
    writes = ("document.answer",)

    def skip(self, bundle: Bundle) -> str:
        return "" if weekdays.WEEKDAYS_RX.search(bundle.document.answer) else "no weekday"

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        req = bundle.request
        bundle.document.answer, fixes = weekdays.fix_weekdays(
            bundle.document.answer, context=f"{req.text}\n{bundle.material.given}",
            today=req.today if isinstance(req.today, dt.date) else None)
        if fixes:
            log.info("weekdays fixed in session %s: %s", req.session_id,
                     [f.fixed for f in fixes][:6])


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


for _stage in (Normalize(), StrayHan(), TextFit(), CopyMarks(), MarkLanguage(), GuideEcho(),
               Weekdays(), Replace()):
    registry.register(_stage)


def bundle_for(*, answer: str, request: str, model: dict, actual_model: str | None,
               api_key: str, session_id: str, today: Any, first_message: str,
               messages: list[dict], material: str,
               check_keys: tuple[str, ...], check_material: str,
               tool_outputs: list[str], judge: str, kept_steps: list[dict],
               usage: dict[str, Any], skill_keys: tuple[str, ...] = (),
               check_options: dict | None = None,
               skill_specs: dict | None = None, traced: bool = False,
               evidence: list[str] | None = None, advice: str = "",
               urgent: str = "") -> Bundle:
    """Chat answer bundle: streamed answer, turn text, material."""
    req = Request(
        text=request, model=model["id"], api_key=api_key, outline_model=judge,
        session_id=session_id, today=today,
        check_keys=tuple(check_keys), model_info=model, actual_model=actual_model,
        first_message=first_message, messages=list(messages),
        check_material=check_material, tool_outputs=list(tool_outputs),
        skill_keys=tuple(skill_keys), check_options=dict(check_options or {}),
        skill_specs=dict(skill_specs or {}), traced=traced, evidence=list(evidence or []),
        advice=advice, urgent=urgent,
    )
    return Bundle(
        request=req, material=Material(given=material),
        document=Document(streamed=answer, answer=answer, kept_steps=list(kept_steps)),
        usage=usage,
    )


__all__ = ["bundle_for"]
