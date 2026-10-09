"""The report writer's stages: `report.write`, cut along its steps.

Each stage is the code that stood at that point of `report.write`, unchanged in what it
asks, computes and emits; it now reads and writes the bundle instead of the function's
locals. Helpers stay in `app.services.report` and are looked up there at call time, so a
test that replaces one (`report._complete`, `report._long_form`) reaches the stage.

Which stages a report runs, and the format's options (`search`, `fixed_outline`, `draws`,
`prepass`), come from its pipeline file (`app/pipelines/*.toml`).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.core import logs
from app.core.config import settings
from app.models.chat import SessionKind
from app.pipeline import registry, runs
from app.pipeline.bundle import Bundle, Part
from app.pipeline.stage import Base, Context, Stop
from app.services import (
    calc,
    check_skills,
    checks,
    design,
    design_templates,
    diagrams,
    doc_formats,
    fact_check,
    figures,
    grounding,
    hangul,
    internal_words,
    key_figures,
    placeholders,
    quality_gate,
    report_checks,
    research,
    richtext,
    units,
    verify,
)
from app.services import deck as deck_rules
from app.services import outline as plan_rules
from app.services.context import (
    build_document_messages,
    instruction_part,
    pasted_material,
    prompt_request,
)

log = logging.getLogger("app.services.report")

#: Beside an earlier step's product in a composed run.
DERIVED_NOTE = (
    "# 작성 메모(내부)\nThe 「앞 단계 결과」 blocks were written by an earlier step from the same "
    "files. Use them for what to cover and how to word it, but take every figure from the "
    "attached files or the 「코드 실행 결과」 blocks; a figure only an earlier step wrote is not "
    "a source — leave it out or write [수치]."
)

_CAUGHT = (httpx.HTTPError, KeyError, IndexError, ValueError)


def _report():
    # Imported late: `report` imports this module's runner from `write`.
    from app.services import report

    return report


def _messages(bundle: Bundle, prompt: str) -> list[dict[str, str]]:
    """The writer's messages: the prompt under the material, as every writing call has."""
    return build_document_messages(
        SessionKind.report,
        prompt,
        request=bundle.request.text,
        trusted_context=bundle.material.trusted_texts,
        untrusted_context=bundle.material.document,
        research_rule=bundle.material.research_rule,
    )


def _count(bundle: Bundle, spent: dict, *, planner: bool = False) -> None:
    if planner:
        plan_rules.count(bundle.usage, spent, planned_apart=bool(bundle.request.outline_model))
        return
    bundle.usage["inputTokens"] += spent.get("inputTokens", 0)
    bundle.usage["outputTokens"] += spent.get("outputTokens", 0)


def _usage(bundle: Bundle) -> dict[str, Any]:
    return {"type": "usage", **bundle.usage}


def _material_text(bundle: Bundle) -> str:
    # What an earlier pipeline step derived is no source for a check.
    return "\n".join([bundle.request.text, *bundle.material.grounds])


# ── before the outline ───────────────────────────────────────────────────


class Research(Base):
    """Searches before planning, so the outline is planned on the sources; asks when a
    document that needs the web found nothing."""

    name = "research"
    kind = "io"
    reads = ("request", "spec.format_id")
    writes = ("material.research", "material.web_search", "material.web_selected")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req = _report(), bundle.request
        findings = research.Findings()
        web_search = req.web_search
        # A document on the person's own material is not searched; a 경력기술서 never is.
        if web_search and (report._own_material(req.text) or not ctx.options.get("search", True)):
            web_search = False
        if web_search and await research.available():
            yield {"type": "step", "id": "sources", "label": "자료 찾는 중", "status": "running"}
            planner = req.outline_model or req.model
            findings = await research.run(req.text, model=planner, api_key=req.api_key,
                                          scholarly=report.scholarly_document(req.text))
            # A comparison of products reads each product's own pages too.
            findings = await research.deepen_for_products(
                req.text, findings, model=planner, api_key=req.api_key)
            apart = bool(req.outline_model)
            bundle.usage["outlineInputTokens" if apart else "inputTokens"] += findings.usage[
                "inputTokens"]
            bundle.usage["outlineOutputTokens" if apart else "outputTokens"] += findings.usage[
                "outputTokens"]
            yield {
                "type": "step",
                "id": "sources",
                "label": f"자료 {len(findings.sources)}건" if findings.sources else "참고할 자료 없음",
                "status": "done",
                "detail": findings.detail,
            }
        # Copied: project citations must not be appended into the caller's list.
        findings.sources = list(findings.sources)
        bundle.material.research = findings
        bundle.material.web_search = web_search
        bundle.material.web_selected = len(findings.sources)
        if (
            req.may_ask
            and req.approved_plan is None
            and web_search
            and findings.searched
            and bundle.material.web_selected == 0
            and report._from_the_web(req.text)
            and not report._carries_material(req.text)
            and not any(block.strip() for block in req.untrusted)
        ):
            # A trend or literature document with no search results asks instead of writing.
            yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
            yield {
                "type": "needs",
                "questions": [
                    grounding.Question(
                        id="sources",
                        question=(
                            "웹 검색이 쓸 만한 자료를 찾지 못했습니다(검색 엔진이 응답하지 "
                            "않거나 무관한 결과뿐). 참고할 자료를 붙이거나 잠시 뒤 다시 시도해 "
                            "주세요. 「있는 자료로 진행」이면 기억으로 쓰되 확인이 필요한 "
                            "항목을 그렇게 표시합니다."
                        ),
                        options=[],
                    ).wire()
                ],
            }
            yield _usage(bundle)
            raise Stop("needs: no sources")


class Assemble(Base):
    """The material the writer reads: attachments, pasted material, project citations and
    the research pages, each part with its origin."""

    name = "material"
    reads = ("request", "material.research", "material.web_search", "material.web_selected")
    writes = ("material.parts", "material.trusted", "material.sources",
              "material.research_rule", "material.grounded")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        req, mat = bundle.request, bundle.material
        findings = mat.research
        project_selected = 0
        project_excluded = 0
        project_reference_lines: list[str] = []
        for item in req.project_sources or []:
            if item.get("state") not in ("included", "truncated"):
                project_excluded += 1
                continue
            ordinal = len(findings.sources) + 1
            title = str(item.get("name") or "프로젝트 자료")[:200]
            url = str(item.get("sourceUrl") or "")
            findings.sources.append(
                {
                    "id": str(item.get("id") or f"project-{ordinal}"),
                    "ordinal": ordinal,
                    "title": title,
                    "publisher": research._publisher(url) if url else "프로젝트 파일",
                    "url": url,
                    "origin": "web" if url else "file",
                    "originLabel": "프로젝트 웹 자료" if url else "프로젝트 파일",
                    "quote": (
                        " · ".join(str(v) for v in (item.get("locations") or []))
                        or (
                            "전체 내용 전달됨"
                            if item.get("state") == "included"
                            else "일부 내용만 전달됨"
                        )
                    ),
                }
            )
            project_reference_lines.append(f"- [{ordinal}] {title}")
            project_selected += 1
        mat.sources = findings.sources

        # Stored by the report artifact as its research log.
        yield {
            "type": "research",
            "research": {
                "enabled": mat.web_search,
                "searched": findings.searched,
                "queries": findings.queries,
                "selected": len(findings.sources),
                "excluded": findings.dropped,
                "webSelected": mat.web_selected,
                "projectSelected": project_selected,
                "projectExcluded": project_excluded,
            },
        }
        # Switched off needs no disclaimer; could not run and found nothing each get one.
        if mat.web_search and not findings.searched:
            mat.research_rule = research.UNRESEARCHED_RULE
        elif mat.web_search and mat.web_selected == 0:
            mat.research_rule = research.EMPTY_RULE
        # Research pages go after the attached files, as their own labelled block.
        mat.parts = [Part(text, "attachment") for text in req.untrusted]
        # Material pasted under the instruction is read whole, as a reference block.
        if pasted := pasted_material(req.text):
            mat.parts.append(Part(pasted, "pasted"))
        # A composed run: what an earlier step's code printed is a source; what an earlier
        # step wrote is read for its shape, its figures taken from the files and the code.
        mat.parts += [Part(f"# 코드 실행 결과(앞 단계)\n{text}", "computed", "tool")
                      for text in req.tool_outputs if text.strip()]
        if derived := [text for text in req.derived if text.strip()]:
            mat.parts += [Part(text, "derived") for text in derived]
            mat.parts.append(Part(DERIVED_NOTE, "note"))
        mat.trusted = [Part(text, "trusted") for text in req.trusted]
        if project_reference_lines:
            mat.trusted.append(Part(
                "# 프로젝트 자료 인용 번호\n"
                "End each sentence that uses a fact from the project material with its exact "
                "number below. Do not make up numbers that are not on the list.\n" + "\n".join(project_reference_lines),
                "project"))
        mat.grounded = deck_rules.has_numbers(req.text, mat.document)
        if block := research.context_block(findings):
            mat.parts.append(Part(block, "research"))


# ── the planning pass ────────────────────────────────────────────────────


class Outline(Base):
    """Plans the 목차, or asks first; offers the plan and stops."""

    name = "outline"
    kind = "model"
    reads = ("request", "spec", "material")
    writes = ("plan.title", "plan.headings", "plan.frame", "plan.unverified", "plan.proposal")

    def skip(self, bundle: Bundle) -> str:
        return "an approved outline was given" if bundle.request.approved_plan is not None else ""

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req, mat = _report(), bundle.request, bundle.material
        request, usage = req.text, bundle.usage
        untrusted = list(req.untrusted)
        yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "running"}
        wanted = report.requested_sections(request)

        async def ask(nudge: str = "") -> tuple[str, dict[str, int]]:
            return await report._complete(
                req.outline_model or req.model,
                _messages(bundle, report._OUTLINE_PROMPT.format(
                    ask_rule=grounding.ASK_RULE if req.may_ask else grounding.PROCEED_RULE,
                    lo=wanted or report._MIN_SECTIONS,
                    hi=wanted or report._MAX_SECTIONS,
                    genre=bundle.spec.genre,
                    request=prompt_request(request, 2000),
                ) + nudge),
                req.api_key,
                400,
            )

        try:
            text, spent = await ask()
        except _CAUGHT as exc:
            log.warning("report outline failed: %s", exc)
            yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "error"}
            yield {"type": "error", "message": "보고서 개요를 만들지 못했습니다."}
            yield _usage(bundle)
            raise Stop("outline failed") from None

        _count(bundle, spent, planner=True)
        # A question instead of a 목차 — see `grounding.ASK_RULE`.
        if req.may_ask and (asked := grounding.parse_needs(text)):
            yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
            yield {"type": "needs", "questions": [q.wire() for q in asked]}
            yield _usage(bundle)
            raise Stop("needs: the planner asked")
        if req.may_ask and report._results_without_data(request, untrusted):
            yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
            yield {
                "type": "needs",
                "questions": [
                    grounding.Question(
                        id="data",
                        question=(
                            "어떤 연구입니까? 제안 방법의 이름과 핵심 아이디어, 있으면 "
                            "수식과 결과를 적어 주세요. 없으면 「있는 자료로 진행」— 내용 "
                            "자리는 [제안 방법]처럼 비워 둔 틀을 씁니다."
                            if re.search(r"논문", request)
                            else "바탕이 될 자료(녹취, 표, 측정값, 파일)를 붙이거나 적어 "
                            "주세요. 없으면 「있는 자료로 진행」— 내용 자리는 [측정값]처럼 "
                            "비워 둔 틀을 씁니다."
                        ),
                        options=[],
                    ).wire()
                ],
            }
            yield _usage(bundle)
            raise Stop("needs: results without data")
        if req.may_ask and report._subject_missing(text, request, "\n".join(untrusted)):
            # A request with no subject is asked about.
            yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
            yield {
                "type": "needs",
                "questions": [
                    grounding.Question(
                        id="subject",
                        question="무엇에 대한 문서입니까? 결정할 사안이나 주제를 적어 주세요.",
                        options=[],
                    ).wire()
                ],
            }
            yield _usage(bundle)
            raise Stop("needs: no subject")
        # Carried on the plan so the writing pass, a separate request, writes a
        # form — see `_FRAME_RULE`.
        frame = not req.may_ask and (
            report._results_without_data(request, untrusted)
            or grounding.subject_missing(text, request, "\n".join(untrusted))
        )
        findings = mat.research
        # Written from memory; carried on the plan so the document opens by saying so.
        unverified = (
            not req.may_ask
            and mat.web_search
            and findings.searched
            and mat.web_selected == 0
            and report._from_the_web(request)
        )
        title, headings = report._parse_outline(text)
        # 「아래 개요로 써 줘」 with the outline pasted: the person's sections, not new ones.
        # 개조식 keeps its fixed shape: a planner's seven sections never fit one page.
        fixed = report.given_outline(request) or (
            list(doc_formats.GAEJO_OUTLINE)
            if ctx.options.get("fixed_outline") == "gaejo" and not wanted else []
        )
        if fixed:
            headings = fixed
        headings = [
            report.without_instruction_aside(h, request)
            for h in report.with_listed_sections(headings, request)
        ]
        if wanted and len(headings) != wanted:
            try:
                retry_text, retry_spent = await ask(
                    f"\n\nThe previous 목차 had {len(headings)} sections. The requested 목차 has "
                    f"exactly {wanted} sections. Rebuild it to that number without dropping any "
                    "requested item."
                )
            except _CAUGHT as exc:
                log.warning("report outline size retry failed: %s", exc)
            else:
                _count(bundle, retry_spent, planner=True)
                retry_title, retry_headings = report._parse_outline(retry_text)
                if len(retry_headings) == wanted:
                    title, headings = retry_title or title, retry_headings
            if len(headings) != wanted:
                yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "error"}
                yield {
                    "type": "error",
                    "message": f"요청한 {wanted}절에 맞는 목차를 만들지 못했습니다. 다시 시도해 주세요.",
                }
                yield _usage(bundle)
                raise Stop("outline: wrong size")
        if not fixed and not report.outline_is_json(text):
            # The model wrote about the outline instead of giving it; one more ask for the
            # JSON. The list read out of its prose stands only if the ask fails again.
            log.info("report outline came as prose, asking for the JSON: %r", text[:300])
            try:
                json_text, json_spent = await ask(
                    "\n\nThe previous answer was not JSON. Answer with one JSON object in the "
                    "format above only, with no explanation."
                )
            except _CAUGHT as exc:
                log.warning("report outline JSON retry failed: %s", exc)
            else:
                _count(bundle, json_spent, planner=True)
                if report.outline_is_json(json_text):
                    json_title, json_headings = report._parse_outline(json_text)
                    json_headings = [
                        report.without_instruction_aside(h, request)
                        for h in report.with_listed_sections(json_headings, request)
                    ]
                    if len(json_headings) >= (1 if wanted else report._MIN_SECTIONS):
                        title, headings, text = json_title or title, json_headings, json_text
        if len(headings) < (1 if wanted else report._MIN_SECTIONS):
            # One more ask before giving up: an outline that came back cut off or in another
            # shape is usually fine the second time.
            log.warning("report outline too short (%d), asking again: %r", len(headings),
                        text[:1500])
            try:
                again_text, again_spent = await ask(
                    "\n\nThe previous answer could not be read as a 목차. Answer again with one "
                    "JSON object in the format above only, with short section headings only."
                )
            except _CAUGHT as exc:
                log.warning("report outline retry failed: %s", exc)
            else:
                _count(bundle, again_spent, planner=True)
                again_title, again_headings = report._parse_outline(again_text)
                again_headings = [
                    report.without_instruction_aside(h, request)
                    for h in report.with_listed_sections(fixed or again_headings, request)
                ]
                if len(again_headings) > len(headings):
                    title, headings, text = again_title or title, again_headings, again_text
        if len(headings) < (1 if wanted else report._MIN_SECTIONS):
            log.warning("report outline too short (%d): %r", len(headings), text[:1500])
            yield {"type": "step", "id": "outline", "label": "개요 잡는 중", "status": "error"}
            yield {
                "type": "error",
                "message": "보고서 개요를 만들지 못했습니다. 요청을 조금 더 구체적으로 적어 주세요.",
            }
            yield _usage(bundle)
            raise Stop("outline too short")

        yield {
            "type": "step",
            "id": "outline",
            "label": f"개요 {len(headings)}개 섹션",
            "status": "done",
            "detail": " · ".join(headings),
        }
        bundle.plan.title, bundle.plan.headings = title, headings
        bundle.plan.frame, bundle.plan.unverified = bool(frame), bool(unverified)
        # Planning stops here: the caller stores the plan, shows it, and calls
        # back with it approved. Figures are proposed now and asked about on a
        # separate card — see `services.figures`.
        plan: dict[str, Any] = {
            "title": title[:200],
            "sections": headings,
            # The request's own style wins; the outline's choice fills the default.
            "visualStyle": (
                design.visual_style_for(request)
                if design.visual_style_for(request) != "editorial"
                else (report._outline_style(text) or "editorial")
            ),
        }
        if frame:
            plan["frame"] = True
        if unverified:
            plan["unverified"] = True
        if req.image_model:
            drawn = await figures.propose(
                request=request,
                title=title,
                parts=headings,
                model=req.outline_model or req.model,
                api_key=req.api_key,
                image_model=req.image_model,
            )
            apart = bool(req.outline_model)
            usage["outlineInputTokens" if apart else "inputTokens"] += drawn.usage["inputTokens"]
            usage["outlineOutputTokens" if apart else "outputTokens"] += drawn.usage[
                "outputTokens"]
            if drawn.figures:
                plan["figures"] = drawn.wire()
        bundle.plan.proposal = plan
        yield {"type": "proposal", "plan": plan}
        yield _usage(bundle)
        raise Stop("proposal offered")


# ── the writing pass ─────────────────────────────────────────────────────


def report_design(
    request: str,
    approved_plan: dict[str, Any] | None,
    design_tokens: dict[str, str] | None,
    template_id: str | None,
) -> dict[str, str] | None:
    """The design the written report is stored with; `None` when it carries none.

    One decision for both ends of the run: `approved-plan` announces it before a word is
    written, so the panel draws the document in the look it will keep, and `report-save`
    stores it. A template's look outranks the project default and the request; with
    neither, the approved plan (or the request) picks the style and the subject the colour.
    """
    template = design_templates.get(template_id) if template_id else None
    if template is not None and template.look:
        return design.normalise_tokens({**(design_tokens or {}), "visualStyle": template.look})
    if design_tokens:
        return design_tokens
    requested_style = str((approved_plan or {}).get("visualStyle") or "")
    accent = str((approved_plan or {}).get("accent") or deck_rules.topic_accent(request))
    if requested_style and requested_style != "editorial":
        return design.normalise_tokens(
            {
                "visualStyle": requested_style,
                "accent": accent,
                "font": design.font_for(request, requested_style),
            }
        )
    return design.report_look_for(request, accent)



class ApprovedPlan(Base):
    """Takes the approved 목차; the title and the sources it was planned on go out."""

    name = "approved-plan"
    reads = ("request", "material.sources")
    writes = ("plan.title", "plan.headings", "plan.frame", "plan.unverified", "plan.figures")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, approved = _report(), bundle.request.approved_plan or {}
        plan = bundle.plan
        plan.title = str(approved.get("title") or "")
        plan.headings = [h for h in map(report._heading_of, approved.get("sections") or []) if h]
        plan.frame = bool(approved.get("frame"))
        plan.unverified = bool(approved.get("unverified"))
        if not plan.headings:
            yield {"type": "error", "message": "승인된 개요가 비어 있습니다."}
            yield _usage(bundle)
            raise Stop("approved outline is empty")
        # Emitted only when the model produced one, so the caller keeps its fallback.
        if plan.title:
            yield {"type": "title", "title": hangul.tidy_spacing(plan.title)[:200]}
        # The look the document is stored with, before the first section: a draft drawn
        # in the panel's default look would change under the reader when it is saved.
        req = bundle.request
        if look := report_design(req.text, approved, req.design_tokens, req.template_id):
            yield {"type": "design", "design": look}
        # The same sources the outline was planned on.
        yield {"type": "sources", "sources": bundle.material.sources}
        plan.figures = {int(f.get("section", -1)): f for f in (bundle.request.figures_plan or [])
                        if f.get("prompt")}


class Calc(Base):
    """Derived numbers (gains in dB, errors, totals) computed by code, not the writer; the
    printed values reach every section as given facts."""

    name = "calc"
    kind = "model"
    reads = ("request", "material.parts")
    writes = ("computed.values", "material.trusted")

    def skip(self, bundle: Bundle) -> str:
        if not calc.needed(bundle.request.text, bundle.material.grounds):
            return "nothing to compute in the request"
        return ""

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req = _report(), bundle.request
        yield {"type": "step", "id": "calc", "label": "코드로 수치 계산 중", "status": "running"}
        computed, spent, status = await calc.computed_values(
            req.text, bundle.material.grounds, complete=report._complete, model=req.model,
            api_key=req.api_key)
        _count(bundle, spent)
        if computed:
            bundle.computed.values = computed
            bundle.material.trusted.append(Part(computed, "computed"))
        yield {
            "type": "step", "id": "calc",
            "label": {
                "done": "코드로 수치 계산·검산",
                "unavailable": "코드 실행 없음 — 모델이 계산",
                "disputed": "코드 계산이 서로 달라 쓰지 않음",
            }
            .get(status, "코드 계산 실패 — 모델이 계산"),
            "status": "done" if status == "done" else "error",
        }


class Announce(Base):
    """The sections, announced up front so the panel can show the whole shape."""

    name = "announce"
    reads = ("plan.headings",)
    writes = ("document.sections",)

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        bundle.document.sections = [
            {"id": f"s{i}_{uuid.uuid4().hex[:6]}", "heading": h, "level": 1}
            for i, h in enumerate(bundle.plan.headings)
        ]
        for section in bundle.document.sections:
            yield {
                "type": "section",
                "sectionId": section["id"],
                "heading": section["heading"],
                "content": "",
                "done": False,
            }


class Settle(Base):
    """The figures the material settles, fixed before a word is written: every section is
    told them, and what it wrote is read against them at the end."""

    name = "settle"
    kind = "model"
    reads = ("request",)
    writes = ("material.given", "computed.settled", "computed.conflicts")

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        report, req = _report(), bundle.request
        # The figures settle from the person's files and the code's output, never from what an
        # earlier step of a composed run wrote (`req.derived`).
        given = "\n\n".join([report._request_material(req.text), *req.untrusted,
                              *req.tool_outputs]).strip()
        bundle.material.given = given
        if not given:
            return
        settled: list[key_figures.Figure] = []
        try:
            # A judgement, not prose: the planner's, when one is set.
            settled, bundle.computed.conflicts, spent = await key_figures.settle_all(
                given, report._complete, req.judge, req.api_key)
            _count(bundle, spent, planner=True)
        except Exception as exc:  # noqa: BLE001 — written without them
            log.info("key figures not settled: %s", exc)
        # What the inputs give (margin, break-even, the target's profit) is worked out by
        # code and settled with them.
        bundle.computed.settled = [*settled, *key_figures.derived(settled)]


class Prepass(Base):
    """The format's own pre-pass: dates, the quote, scores and periods settled by code, in
    blocks of their own beside the settled figures (not under their cap)."""

    name = "prepass"
    kind = "model"
    reads = ("request", "spec.format_id", "material.given", "computed.values")
    writes = ("computed.prepass", "computed.settled", "material.parts", "material.trusted")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req = _report(), bundle.request
        format_id, given = bundle.spec.format_id, bundle.material.given
        quiet = bool(ctx.options.get("prepass_quiet"))
        prepass: report_checks.Prepass | None = None
        if given:
            prepass_usage: dict[str, int] = {"inputTokens": 0, "outputTokens": 0}
            try:
                prepass = await report.report_checks.prepare(
                    format_id, req.text, given, complete=report._complete,
                    model=req.judge, api_key=req.api_key, usage=prepass_usage,
                    today=dt.datetime.now(ZoneInfo(settings.timezone)).date(),
                    tiebreak_model=report._tiebreak_model(req.judge),
                )
            except Exception as exc:  # noqa: BLE001 — written without it; the step says so
                log.exception("report pre-pass failed")
                yield {"type": "step", "id": "prepass", "label": "사전 계산 실패",
                       "status": "error", "detail": f"코드 계산 없이 씀 ({type(exc).__name__})"}
            _count(bundle, prepass_usage, planner=True)
        elif not quiet:
            # A pre-pass that does not run says why.
            yield {"type": "step", "id": "prepass", "label": "사전 계산 건너뜀", "status": "done",
                   "detail": "요청과 첨부에 계산할 자료가 없음"}
        if ctx.options.get("prepass_always") and prepass is None:
            # 개조식 is checked for its shape and pages even with nothing attached.
            prepass = report_checks.Prepass(format_id=format_id)
        bundle.computed.prepass = prepass
        computed = bundle.computed.values
        if prepass is not None and prepass.costs_withheld and computed:
            # Readings of the costs never agreed: no totals reach the writer, the sandbox's
            # neither.
            bundle.material.trusted = [p for p in bundle.material.trusted if p.text != computed]
        if prepass is not None and prepass.runs:
            settled = bundle.computed.settled
            bundle.computed.settled = [*settled, *[f for f in prepass.figures
                                                   if f.name not in {s.name for s in settled}]]
            bundle.material.parts = [*(Part(b, "prepass") for b in prepass.blocks),
                                     *bundle.material.parts]
            ran = [r["skill"] for r in prepass.runs if not r.get("skipped")]
            yield {
                "type": "step", "id": "prepass",
                "label": ("사전 계산: " + ", ".join(dict.fromkeys(ran))) if ran
                else "사전 계산 건너뜀",
                "status": "error" if any(r.get("error") for r in prepass.runs) else "done",
                "detail": " · ".join(f"{r['skill']}: {r['summary']}" for r in prepass.runs)[:1500],
            }
        elif prepass is not None and given and not quiet:
            yield {"type": "step", "id": "prepass", "label": "사전 계산 건너뜀", "status": "done",
                   "detail": "자료에서 이 서식이 계산할 값을 찾지 못함"}


class WriterNotes(Base):
    """The settled figures and the conflicting ones, put in front of the writer."""

    name = "writer-notes"
    reads = ("computed.settled", "computed.conflicts", "computed.prepass", "spec.format_id")
    writes = ("material.parts",)

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        mat, com = bundle.material, bundle.computed
        if com.settled:
            mat.parts = [Part(key_figures.block(com.settled), "settled"), *mat.parts]
        if com.settled or (com.prepass is not None and com.prepass.blocks):
            # The blocks are the writer's notes; their names never reach the page.
            mat.parts = [*mat.parts, Part(internal_words.WRITER_NOTE, "note")]
            log.info("settled figures: %s", [(f.name, f.value) for f in com.settled])
        if com.conflicts:
            # Both values, with where and when: the writer shows them, nothing merges them.
            mat.parts = [Part(key_figures.conflict_block(com.conflicts, bundle.spec.format_id),
                              "conflicts"), *mat.parts]


class Draft(Base):
    """The whole document drafted in one call so numbers and conclusions stay consistent
    across sections, then cut along its headings; a long document is written section by
    section instead (`report._long_form`)."""

    name = "draft"
    kind = "model"
    reads = ("request", "spec.genre", "plan", "material")
    writes = ("document.long_form", "document.drafted")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req, plan = _report(), bundle.request, bundle.plan
        long_form = report._long_form(req.text)
        bundle.document.long_form = long_form
        if long_form:
            # Long documents are written section by section — see `_long_form`.
            yield {"type": "step", "id": "draft", "label": "절마다 길게 쓰는 중", "status": "done"}
            return
        yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "running"}
        genre, sources = bundle.spec.genre, bundle.material.sources
        try:
            draft_text, spent = await report._complete(
                req.model,
                _messages(bundle, report._shaped(
                    report._DRAFT_PROMPT.format(
                        outline="\n".join(f"## {h}" for h in plan.headings),
                        refs=report._refs_block(sources),
                        facts=report._FRAME_RULE if plan.frame
                        else report._facts_line(req.text, sources),
                        genre=genre,
                        request=prompt_request(req.text, 1500),
                    ),
                    genre,
                )),
                req.api_key,
                max_tokens=min(11000, 1400 * len(plan.headings)),
            )
            _count(bundle, spent)
            bundle.document.drafted = report._carry_table(
                req.text, plan.headings, report._split_draft(draft_text, plan.headings))
            yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "done"}
        except _CAUGHT as exc:
            log.warning("report draft failed, writing section by section: %s", exc)
            yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "error"}


class Sections(Base):
    """Each section taken in order: from the draft, or written on its own (four at a time
    for a long document, the assumptions section first), then cleaned by code."""

    name = "sections"
    kind = "model"
    reads = ("request", "spec", "plan", "material", "computed", "document.drafted",
             "document.long_form")
    writes = ("document.sections", "document.written")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req, plan = _report(), bundle.request, bundle.plan
        request, sections, drafted = req.text, bundle.document.sections, bundle.document.drafted
        long_form, written = bundle.document.long_form, bundle.document.written
        genre, sources, headings = bundle.spec.genre, bundle.material.sources, plan.headings
        wanted_figures, refs = plan.figures, report._refs_block(sources)
        outline_text = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(headings))
        document_context = bundle.material.document

        #: The assumptions section's text once written, and where it stands.
        assumptions: dict = {"text": "", "index": -1}

        async def write_section(index: int, section: dict, written: list[str]):
            """One section's call, as the loop below makes it."""
            role = report._section_role(section["heading"], index, len(sections),
                                        "\n".join(written))
            return await report._complete(
                req.model,
                _messages(bundle, report._shaped(report._SECTION_PROMPT.format(
                    heading=section["heading"],
                    outline=outline_text,
                    # Tail only: the whole document would crowd out the
                    # instruction by section six.
                    written="\n\n".join(written)[-4000:] or "(nothing yet)",
                    refs=refs,
                    request=prompt_request(request, 1500),
                    role=role[0],
                    blocks=role[1],
                    others=report._others_line(headings, index),
                    facts=report._FRAME_RULE if plan.frame
                    else report._facts_line(request, sources or []),
                    genre=genre,
                ), genre)
                    + (
                    # Told before writing so the section can refer to its figure.
                    "\n\n"
                    + figures.note_for(
                        figures.Figure(
                            section=index,
                            caption=str(wanted_figures[index].get("caption") or ""),
                            prompt="",
                        )
                    )
                    if index in wanted_figures
                    else ""
                )
                    + (
                    # The assumptions section was written first; every other section
                    # computes from it rather than settling the same inputs again.
                    "\n\nThese are this document's settled key assumptions. For values such as "
                    "price, unit cost, fixed cost and churn, use only those below, and never set "
                    "a new value for the same assumption. Do not rebuild the assumptions table; "
                    "if needed, point to the 「핵심 가정」 section.\n"
                    + assumptions["text"][:3000]
                    if assumptions["text"] and index != assumptions["index"]
                    else ""
                )
                    + (
                    "\n\nThis document was requested at long length. Write this section in "
                    "five to eight paragraphs, 1,500 characters or more — develop it from the "
                    "material's figures, and do not pad it by repeating yourself."
                    if long_form
                    else ""
                )),
                req.api_key,
                2400 if long_form else 1200,
            )

        # A section that settles the document's assumptions (「핵심 가정」, 「전제」) is written
        # before the others, which are then told its values: written side by side, each
        # section would settle its own price and fixed cost and the document would carry
        # four different ones.
        assumption_at = next(
            (
                i for i, sec in enumerate(sections)
                if re.search(r"가정|전제|assumption", str(sec["heading"]), re.I)
                and sec["heading"] not in drafted
            ),
            None,
        )
        early: dict[int, tuple[str, dict]] = {}
        if long_form and assumption_at is not None:
            try:
                early[assumption_at] = await write_section(assumption_at,
                                                           sections[assumption_at], [])
                assumptions["text"] = early[assumption_at][0]
                assumptions["index"] = assumption_at
            except _CAUGHT as exc:
                log.info("assumptions section not written first: %s", exc)

        # A long document's sections are started together (four at a time) and taken in
        # order below; each knows from its role line what the others cover, and the
        # restatement trim still runs in order. A short one is drafted in one call.
        gate = asyncio.Semaphore(4)

        async def gated(index: int, section: dict):
            async with gate:
                return await write_section(index, section, [])

        prefetched = (
            {
                index: asyncio.ensure_future(gated(index, section))
                for index, section in enumerate(sections)
                if section["heading"] not in drafted and index not in early
            }
            if long_form
            else {}
        )

        for index, section in enumerate(sections):
            # The position lives in `progress` only; the surface renders it.
            label = str(section["heading"])
            progress = {"current": index + 1, "total": len(sections)}
            yield {
                "type": "step",
                "id": section["id"],
                "label": label,
                "status": "running",
                "progress": progress,
            }
            try:
                if section["heading"] in drafted:
                    body, spent = drafted[section["heading"]], {"inputTokens": 0,
                                                                "outputTokens": 0}
                elif index in early:
                    body, spent = early.pop(index)
                else:
                    body, spent = await (
                        prefetched.pop(index)
                        if index in prefetched
                        else write_section(index, section, written)
                    )
            except _CAUGHT as exc:
                log.warning("report section %r failed: %s", section["heading"], exc)
                yield {
                    "type": "step",
                    "id": section["id"],
                    "label": label,
                    "status": "error",
                    "progress": progress,
                }
                yield {
                    "type": "section",
                    "sectionId": section["id"],
                    "heading": section["heading"],
                    "content": "_이 섹션을 쓰지 못했습니다._",
                    "done": True,
                }
                section["content"] = ""
                continue

            _count(bundle, spent)
            # Stray ideographs are read back into Hangul and table gaps closed once,
            # before storing, so every reader sees the same text.
            body = richtext.detach_tables(report.unwrap_json_prose(body))
            if report.placeholder_heavy(body) and any(re.search(r"\d", m or "")
                                                      for m in document_context):
                # 「[측정값]」 cells under an attached table of numbers: the model wrote the
                # frame and left the data out. Asked once more, with the data in front of it.
                try:
                    refilled, more = await report._complete(
                        req.model,
                        _messages(bundle, report._FILL_PROMPT.format(
                            heading=section["heading"], body=body[:6000])),
                        req.api_key,
                        1600,
                    )
                except _CAUGHT as exc:
                    log.info("placeholder refill of %r failed: %s", section["heading"], exc)
                else:
                    _count(bundle, more)
                    refilled = richtext.detach_tables(report.unwrap_json_prose(refilled))
                    if refilled.strip() and not report.placeholder_heavy(refilled):
                        body = refilled
            section["content"] = _cleaned(bundle, index, section, body)

            # Drawn after the prose so a failed drawing leaves no dangling reference.
            if (drawing := wanted_figures.get(index)) is not None:
                yield {
                    "type": "step",
                    "id": f"fig{index}",
                    "label": drawing.get("caption") or "그림 그리는 중",
                    "status": "running",
                    "progress": progress,
                }
                picture = await report._draw(drawing, req.image_model, req.api_key)
                if picture is None:
                    yield {
                        "type": "step",
                        "id": f"fig{index}",
                        "label": drawing.get("caption") or "그림",
                        "status": "error",
                        "progress": progress,
                    }
                else:
                    bundle.usage["inputTokens"] += picture.pop("_in", 0)
                    bundle.usage["outputTokens"] += picture.pop("_out", 0)
                    # Appended to the body as Markdown: the panel, the page view and
                    # the exporters all read the body.
                    caption = str(picture.get("caption") or "").replace("]", " ")
                    section["content"] = (
                        f"{section['content'].rstrip()}\n\n![{caption}]({picture['src']})"
                    )
                    yield {
                        "type": "step",
                        "id": f"fig{index}",
                        "label": drawing.get("caption") or "그림",
                        "status": "done",
                        "progress": progress,
                    }
            written.append(f"## {section['heading']}\n{body}")
            yield {
                "type": "step",
                "id": section["id"],
                "label": label,
                "status": "done",
                "progress": progress,
            }
            yield {
                "type": "section",
                "sectionId": section["id"],
                "heading": section["heading"],
                "content": report.shown_while_writing(body),
                "done": True,
            }


def _cleaned(bundle: Bundle, index: int, section: dict, body: str) -> str:
    """A written section as stored: restatements trimmed, blocks repaired, numbers and
    owners held to the material — code, never a rewrite."""
    report, request = _report(), bundle.request.text
    sections, doc_format = bundle.document.sections, bundle.spec.format
    prepass, sources = bundle.computed.prepass, bundle.material.sources
    document_context = bundle.material.document
    # Restatements of earlier numbers and filler sentences are cut by code; a model
    # asked to reword them fills the gap with invented content.
    earlier = [str(s.get("content") or "") for s in sections[:index]]
    # A reference list repeats the years the text cites and carries its own dates and
    # publishers: the number and owner guards below are for prose, not for it.
    is_reference = bool(report._REFERENCE_HEADING.search(str(section["heading"] or "")))
    trimmed: list[str] = []
    if not is_reference:
        body, trimmed = report.trim_restatements(
            body, earlier, keep_items=doc_format is not None and doc_format.lists == "gaejo"
        )
    if trimmed:
        log.info("trimmed %d sentence(s) from %r: %s", len(trimmed), section["heading"],
                 trimmed[0][:80])
    clean, _ = hangul.read_back(report._without_own_heading(body, section["heading"]))
    # The section is the document's level-two heading: its own subheadings sit below.
    clean = report._FIGURE_MARK.sub(lambda m: f"\n\n{m.group(0)}\n\n", clean)
    clean = report.split_long_paragraphs(
        report.demote_subheadings(
            # Fences back on their own lines first, then counted and paired.
            report.balance_fences(report.unglue_blocks(report.repair_pipe_tables(
                report.split_glued_tables(clean))))
        )
    )
    clean = hangul.tidy_spacing(clean)
    if is_reference:
        return clean
    given_text = "\n".join([request, *document_context])
    clean = units.fix_magnitude_slips(units.fix_unit_labels(clean, given_text), given_text)
    clean = report.drop_redundant_kpi(report.drop_repeated_figures(clean))
    if not bundle.material.grounded:
        clean = report._without_invented_money(clean)
    # Owners and dates come from the request, the material or a source — never the pen.
    clean = report._unsourced_owner_dates(
        clean,
        "\n".join(
            [request, *document_context, *[str(s.get("quote") or "") for s in sources or []]]
        ),
        prepass.bound_dates if prepass is not None and prepass.bound_dates else None,
    )
    if bundle.plan.unverified and index == 0:
        # Stated at the head of the first section.
        clean = report._UNVERIFIED_NOTE + "\n\n" + clean
    # Written 「A × B = C」 checked by arithmetic before it is kept.
    clean = report.fix_restated_amounts(
        report.fix_table_formulas(units.fix_written_sums(units.fix_written_arithmetic(clean))),
        given_text,
    )
    return richtext.tidy_tables(
        report._grounded_figures(
            report.trim_table_echo(
                report.fix_point_units(
                    report.undouble_words(
                        report.fix_percent_formulas(
                            report.fix_ledger_magnitudes(
                                report.fix_value_then_product(report.fix_products(clean)),
                                request,
                            )
                        )
                    )
                )
            ),
            bundle.material.grounded,
        )
    )


class Figures(Base):
    """Structure, flow, comparison and concept figures the report draws for itself as
    mermaid: the ones the writer placed in its text first, then the planner's."""

    name = "figures"
    kind = "model"
    reads = ("request", "plan.figures", "material", "document.sections")
    writes = ("document.sections",)

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req = _report(), bundle.request
        request, sections = req.text, bundle.document.sections
        # Planned on the written sections, so the planner reads what is actually there; a
        # section with an approved picture keeps it. A peer review evaluates someone else's
        # paper; it draws nothing of its own. A one-page 개조식 memo has no room for
        # pictures it was not asked for either, and a 회의록 records the meeting, not figures.
        draws_nothing = report._genre_rule(request).startswith("Genre: 학회 피어리뷰") or (
            ctx.options.get("draws") == "when-asked"
            and not diagrams.asks_for_diagrams(instruction_part(request))
        )
        figure_cap = min(5, max(diagrams.MAX_FIGURES, len(sections) // 2 + 1))
        # The figures the writer placed in its text come first: drawn where the text put
        # them, and the prose already points at them (〔그림〕). The planner below only
        # looks at the sections that have none.
        drawn = 0
        for index, section in enumerate(sections):
            marks = report.figure_marks(str(section.get("content") or ""))
            for mark in marks:
                content = str(section.get("content") or "")
                if not mark["kind"] or draws_nothing or drawn >= figure_cap:
                    section["content"] = report.drop_figure(content, mark["mark"])
                    continue
                name = mark["caption"] or diagrams.FIGURES[mark["kind"]]
                progress = {"current": index + 1, "total": len(sections)}
                yield {"type": "step", "id": f"dia{index}", "label": f"{name} 그리는 중",
                       "status": "running", "progress": progress}
                try:
                    made, spent, png = await diagrams.make(
                        diagrams.Planned(index, mark["kind"], mark["description"],
                                         mark["caption"]),
                        model=req.model, api_key=req.api_key, slide=False,
                    )
                except Exception as exc:  # noqa: BLE001 — the text stands without its figure
                    log.warning("placed figure %r not drawn: %s", name, exc)
                    section["content"] = report.drop_figure(content, mark["mark"])
                    yield {"type": "step", "id": f"dia{index}", "label": name,
                           "status": "error", "progress": progress}
                    continue
                _count(bundle, spent)
                section["content"] = content.replace(
                    mark["mark"], report._figure_markdown(made, png), 1)
                drawn += 1
                yield {"type": "step", "id": f"dia{index}", "label": name, "status": "done",
                       "progress": progress}
            if marks:
                yield {"type": "section", "sectionId": section["id"],
                       "heading": section["heading"], "content": section["content"],
                       "done": True}
        has_figure = {
            i for i, section in enumerate(sections)
            if re.search(r"data:image/png|```mermaid", str(section.get("content") or ""))
        }
        eligible = [
            i
            for i, section in enumerate(sections)
            if i not in bundle.plan.figures and i not in has_figure
            and str(section.get("content") or "").strip() and not draws_nothing
        ]
        if drawn >= figure_cap:
            eligible = []
        planned, spent = await diagrams.plan(
            parts=[(str(s["heading"]), str(s.get("content") or "")) for s in sections],
            eligible=eligible,
            request=request,
            model=req.model,
            api_key=req.api_key,
            complete=report._complete,
            slide=False,
            # About one figure for every two sections, between three and five, the writer's
            # own figures counted.
            limit=max(1, figure_cap - drawn),
            # A report reads better with a few figures: two for five sections or more, else
            # one.
            at_least=max(0, (2 if len(sections) >= 5 else 1) - drawn),
            wrap=lambda prompt: _messages(bundle, prompt),
        )
        _count(bundle, spent)
        if not planned:
            log.info("report figures: none planned for %d eligible sections", len(eligible))
        for row in planned:
            section = sections[row.index]
            name = row.caption or diagrams.FIGURES[row.figure]
            progress = {"current": row.index + 1, "total": len(sections)}
            yield {
                "type": "step",
                "id": f"dia{row.index}",
                "label": f"{name} 그리는 중",
                "status": "running",
                "progress": progress,
            }
            try:
                made, spent, png = await diagrams.make(
                    row, model=req.model, api_key=req.api_key, slide=False)
            except Exception as exc:  # noqa: BLE001 — a section without its figure is still one
                log.warning("report figure %r not drawn: %s", name, exc)
                yield {
                    "type": "step",
                    "id": f"dia{row.index}",
                    "label": name,
                    "status": "error",
                    "progress": progress,
                }
                continue
            _count(bundle, spent)
            section["content"] = (f"{section['content'].rstrip()}\n\n"
                                  f"{report._figure_markdown(made, png)}")
            yield {
                "type": "step",
                "id": f"dia{row.index}",
                "label": name,
                "status": "done",
                "progress": progress,
            }
            yield {
                "type": "section",
                "sectionId": section["id"],
                "heading": section["heading"],
                "content": section["content"],
                "done": True,
            }


class Additions(Base):
    """What the instruction asked for and the draft left out, added and never rewritten:
    author-year citations, the API endpoints the material listed, the question set, the
    computed tables, and a search for each comparison cell left open."""

    name = "additions"
    kind = "model"
    reads = ("request", "material", "computed.values", "document.sections")
    writes = ("document.sections",)

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req = _report(), bundle.request
        sections = bundle.document.sections
        material_text = _material_text(bundle)
        for index, addition in report.additions_needed(sections, req.text, material_text):
            try:
                amended, spent = await report._complete(
                    req.model,
                    [{"role": "user", "content": report._AMEND_PROMPT.format(
                        instruction=addition, content=sections[index]["content"]
                    )}],
                    req.api_key,
                    max_tokens=min(9000, 2 * len(sections[index]["content"]) + 1500),
                )
            except _CAUGHT as exc:
                log.info("amending %r failed: %s", sections[index]["heading"], exc)
                continue
            _count(bundle, spent)
            amended = richtext.detach_tables(report.unwrap_json_prose(amended)).strip()
            if report.only_added(sections[index]["content"], amended):
                sections[index] = {**sections[index], "content": amended}
            else:
                log.info("amendment of %r changed the text; kept the draft",
                         sections[index]["heading"])
        findings, sources = bundle.material.research, bundle.material.sources
        # Reference entries are looked up when the turn could search: a work no search finds
        # is marked, not removed — the reader decides, knowing it was not confirmed.
        if findings.searched:
            sections = await report._mark_unverified_references(sections)
        sections = report.add_missing_endpoints(sections, material_text)
        # The person's material only: the search pages are not questions to carry.
        sections = report.carry_question_set(sections, bundle.material.given, req.text)
        # A table the writer rebuilt from the computed rows is replaced by the computed one:
        # the code's values, not the writer's copy of them.
        if computed := bundle.computed.values:
            sections, replaced = calc.replace_tables(sections, computed)
            if replaced:
                log.info("replaced %d table(s) with the computed values", replaced)
        # A comparison cell left open (「배포 방식 명시 없음」) gets a search of its own.
        if findings.searched and sources is not None:
            try:
                sections, spent = await report.fill_gaps_from_search(
                    sections, sources, model=req.judge, api_key=req.api_key
                )
                _count(bundle, spent, planner=True)
                yield {"type": "sources", "sources": sources}
            except Exception:  # noqa: BLE001 — the cells stay as written
                log.exception("gap search failed")
        bundle.document.sections = sections


class Normalize(Base):
    """Every format mend, in one fixed order; the verify stage runs the same after its
    repair. Then the list shape the format sets."""

    name = "normalize"
    reads = ("request", "material", "computed.settled", "spec.format", "document.sections")
    writes = ("document.sections",)

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        report = _report()
        sections = report.normalize_document(
            bundle.document.sections, settled=bundle.computed.settled,
            sources=bundle.material.sources, request=bundle.request.text,
            context=_material_text(bundle))
        bundle.document.sections = report.gaejo_body(sections, bundle.spec.format)


class TitleBlock(Base):
    """The head of the document as its purpose shapes it — the lab's date and people, the
    minutes' time and place, a paper's authors — filled only from the request."""

    name = "title-block"
    kind = "model"
    reads = ("request", "spec.format", "plan.title", "material.given", "computed.prepass")
    writes = ("document.head_block", "findings.head_fixed", "findings.client_problem")

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.spec.format is not None else "no document format"

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req, prepass = _report(), bundle.request, bundle.computed.prepass
        given = bundle.material.given
        block, spent = await report.title_block_for(
            bundle.spec.format, req.text, bundle.plan.title, req.judge, req.api_key, given)
        bundle.usage["inputTokens"] += spent.get("inputTokens", 0)
        bundle.usage["outputTokens"] += spent.get("outputTokens", 0)
        if prepass is not None and prepass.rfp:
            # The cover names the client the RFP names, not a guess or the proposer.
            head_fixed, client_problem = report_checks.client_head(
                block, prepass.rfp, f"{req.text}\n{given}")
            bundle.findings.head_fixed = head_fixed
            bundle.findings.client_problem = client_problem
            block = head_fixed or block
        block = report_checks.minutes_head(block, prepass) or block
        bundle.document.head_block = block
        yield {"type": "titleBlock", "titleBlock": block}


# ── checks: find, repair, find again ─────────────────────────────────────


def checks_of(bundle: Bundle) -> tuple[list, list]:
    """The verify stage's checks and the format's code checks, from the bundle: functions,
    so a run kept across requests builds them again (`runs.REBUILT`)."""
    report, req = _report(), bundle.request
    settled, prepass = bundle.computed.settled, bundle.computed.prepass
    judge = req.judge
    run_checks = list(checks.CODE_CHECKS)
    # The judge reads the figure sentences only when it is not the writer, which
    # over-flags its own sentences.
    if req.outline_model and settled:
        run_checks.append(checks.judge_figures(req.outline_model, req.api_key))
    # The writer's knowledge is checked against the web, and every assessment item is
    # worked through by the judge: what no rule can find.
    if bundle.material.web_search:
        run_checks.append(fact_check.web_facts(judge, req.api_key))
    run_checks.append(fact_check.solve_questions(judge, req.api_key))
    # The format's code checks join the same find → repair → find again.
    format_checks = report_checks.checks(prepass)
    run_checks.extend(format_checks)
    # The format's judged checks: one judge call each, shown to the reader, never
    # rewritten.
    run_checks.extend(report_checks.model_checks(
        prepass, complete=report._complete, judge=judge, api_key=req.api_key))
    return run_checks, format_checks


def _rebuild_checks(bundle: Bundle) -> None:
    bundle.findings.checks, bundle.findings.format_checks = checks_of(bundle)


runs.REBUILT["checks-plan"] = _rebuild_checks


class ChecksPlan(Base):
    """The checks the verify stage runs, the check skills attached, the settled rows mended."""

    name = "checks-plan"
    reads = ("request", "computed", "material.web_search", "findings.client_problem",
             "document.sections")
    writes = ("document.sections", "findings.checks", "findings.format_checks",
              "findings.before", "findings.check_keys", "findings.traced")

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        report, req, found = _report(), bundle.request, bundle.findings
        settled = bundle.computed.settled
        bundle.document.sections = [
            sec if report._REFERENCE_HEADING.search(str(sec.get("heading") or ""))
            else {**sec, "content": key_figures.mend_rows(str(sec.get("content") or ""),
                                                          settled)}
            for sec in bundle.document.sections
        ]
        found.checks, found.format_checks = checks_of(bundle)
        if found.client_problem:
            found.before.append("client_name")
        # Every value the document states traces to what was given or computed, else it is a
        # blank: the attached check skills, before the verify stage and again after it.
        keys = req.check_keys
        if keys is None:
            template = design_templates.get(req.template_id)
            keys = check_skills.attached(
                template_checks=template.check_skills if template else ())
        found.check_keys = tuple(keys)
        found.traced = check_skills.Outcome("")


class MinutesRepair(Base):
    """Every action row carries 출처 and 비고; dues the material binds are filled; with no
    meeting date nothing is dated; owners and quantities the material lacks are repaired."""

    name = "minutes-repair"
    reads = ("request", "material.given", "computed.prepass", "document.sections")
    writes = ("document.sections", "findings.before")

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.computed.prepass is not None else "no pre-pass"

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        prepass, before = bundle.computed.prepass, bundle.findings.before
        known = f"{bundle.request.text}\n{bundle.material.given}"
        sections, widened = report_checks.minutes_columns(bundle.document.sections,
                                                          prepass.dates)
        before += ["minutes_columns"] * widened
        sections, filled = report_checks.minutes_due_fill(sections, prepass.dates)
        before += ["minutes_due"] * filled
        if prepass.undated:
            # No meeting date anywhere: no day is written as the meeting's or worked out.
            sections, undated = report_checks.minutes_undated(sections, prepass.dates, known)
            before += undated
        # Owners and quantities the material does not hold are repaired by code, then
        # looked for again after the verify stage's own rewrites.
        sections, ungrounded = report_checks.minutes_grounding(sections, known)
        before += ungrounded
        bundle.document.sections = sections


def _doc(bundle: Bundle) -> verify.Doc:
    return verify.Doc(bundle.document.sections, _material_text(bundle), bundle.request.text,
                      list(bundle.material.sources or []), bundle.computed.settled,
                      bundle.computed.conflicts)


class FormatFind(Base):
    """The format's code checks, found once before the repair (for the record)."""

    name = "format-find"
    kind = "check"
    reads = ("findings.format_checks", "document.sections", "material", "computed")
    writes = ("findings.before",)

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.findings.format_checks else "the format has no code checks"

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        first, _ = await verify.find(_doc(bundle), bundle.findings.format_checks)
        bundle.findings.before += [f.code for f in first]


class Verify(Base):
    """Every check names what it finds, one repair rewrites those sentences, the checks run
    again, and what is still found is kept to be said — never passed off."""

    name = "verify"
    kind = "check"
    reads = ("request", "material", "computed", "findings.checks", "document.sections")
    writes = ("document.sections", "findings.still", "findings.after")

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        report, req = _report(), bundle.request
        settled, sources = bundle.computed.settled, bundle.material.sources
        context_text = _material_text(bundle)
        still: list[verify.Finding] = []
        try:
            bundle.document.sections, still, spent = await verify.verify_and_repair(
                _doc(bundle),
                bundle.findings.checks,
                complete=report._complete,
                model=req.judge,
                api_key=req.api_key,
                clean=lambda s: units.fix_written_sums(units.fix_written_arithmetic(s)),
                normalize=lambda secs: report.normalize_document(
                    secs, settled=settled, sources=sources, request=req.text,
                    context=context_text
                ),
            )
            _count(bundle, spent, planner=True)
        except Exception:  # noqa: BLE001 — the sentences stay as written; the gate reports them
            log.exception("verify stage failed")
        bundle.findings.still = still
        format_codes = set(report_checks.LABELS)
        bundle.findings.after += [f.code for f in still if f.code in format_codes]


class MinutesReground(Base):
    """The minutes' owner and quantity repair again, after the verify stage's rewrites;
    what a second pass still finds is what stays."""

    name = "minutes-reground"
    kind = "check"
    reads = ("request", "material.given", "computed.prepass", "document.sections")
    writes = ("document.sections", "findings.after")

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.computed.prepass is not None else "no pre-pass"

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        known = f"{bundle.request.text}\n{bundle.material.given}"
        sections, again = report_checks.minutes_grounding(bundle.document.sections, known)
        if again:
            sections, left = report_checks.minutes_grounding(sections, known)
            bundle.findings.after += left
        bundle.document.sections = sections


class FormatAmend(Base):
    """What the format must carry and the draft left out: added (one amendment each), then
    looked for again; a cover that still misses the RFP's client is counted."""

    name = "format-amend"
    kind = "model"
    reads = ("request", "material.given", "computed", "document", "findings.client_problem",
             "findings.head_fixed")
    writes = ("document.sections", "findings.after", "findings.added_before",
              "findings.added_after")

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        report, req, found = _report(), bundle.request, bundle.findings
        if found.client_problem and found.head_fixed is None:
            found.after.append("client_name")
        prepass, sections = bundle.computed.prepass, bundle.document.sections
        if prepass is None:
            return
        for amendment in report_checks.amendments(
            prepass, sections, bundle.material.given, req.text, bundle.computed.conflicts,
            bundle.document.head_block
        ):
            found.added_before.append(amendment.code)
            index = amendment.section
            content = str(sections[index].get("content") or "")
            if amendment.report_only:
                pass
            elif amendment.append:
                sections[index] = {**sections[index],
                                   "content": content.rstrip() + "\n\n" + amendment.append}
            else:
                try:
                    amended, spent = await report._complete(
                        req.model,
                        [{"role": "user", "content": report._AMEND_PROMPT.format(
                            instruction=amendment.instruction, content=content)}],
                        req.api_key,
                        max_tokens=min(9000, 2 * len(content) + 1500),
                    )
                    _count(bundle, spent)
                    amended = richtext.detach_tables(report.unwrap_json_prose(amended)).strip()
                    if report.only_added(content, amended):
                        sections[index] = {**sections[index], "content": amended}
                except _CAUGHT as exc:
                    log.info("amendment %s failed: %s", amendment.code, exc)
            if amendment.missing(sections):
                found.added_after.append(amendment.code)


class CareerPeriods(Base):
    """Periods written one way, so the reader (and an ATS) reads them alike."""

    name = "career-periods-tidy"
    reads = ("document.sections",)
    writes = ("document.sections",)

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        bundle.document.sections, tidied = report_checks.tidy_periods(bundle.document.sections)
        if tidied:
            log.info("career periods written one way: %d", tidied)


class PageLimit(Base):
    """A page limit (개조식: one page; a proposal: the RFP's) counted on the rendered body;
    a 개조식 body that runs over is condensed and counted again, a proposal's is said."""

    name = "page-limit"
    kind = "model"
    reads = ("request", "spec", "plan.title", "material.given", "computed.prepass",
             "document")
    writes = ("document.sections", "findings.added_before", "findings.added_after",
              "findings.pages_note")

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        report, req, found = _report(), bundle.request, bundle.findings
        format_id, doc_format = bundle.spec.format_id, bundle.spec.format
        limit = (report_checks.page_limit(format_id, req.text, bundle.material.given)
                 if bundle.computed.prepass is not None else None)
        if not (limit and format_id in report_checks.PAGE_FORMATS):
            return
        title, sections = bundle.plan.title, bundle.document.sections
        shown_block, design_tokens = bundle.document.head_block, req.design_tokens
        # 개조식 is judged on both files people get, laid out as exported: the body through
        # the 붙임 list and its 「끝.」; the longer of the two counts.
        by_hwpx = format_id in report_checks.PAGE_LIMITS

        async def counted(rows: list[dict]) -> tuple[int | None, str]:
            body = report.gaejo_body(rows, doc_format)
            if by_hwpx:
                hwpx, pdf = await report_checks.body_pages(title, body, shown_block,
                                                           design_tokens)
            else:
                hwpx, pdf = None, await report_checks.page_count(
                    title, body, shown_block, design_tokens)
            note = " · ".join(f"{name} {n}쪽" for name, n in (("HWPX", hwpx), ("PDF", pdf))
                              if n)
            return max(hwpx or 0, pdf or 0) or None, note

        pages, counts = await counted(sections)
        if pages and pages > limit and not by_hwpx:
            # Over the RFP's limit: said, not cut by code — what to drop is the proposer's
            # call.
            found.added_before.append("page_over")
            found.added_after.append("page_over")
            found.pages_note = f"본문 쪽수({counts}) / 제한 {limit}쪽"
        elif pages and pages > limit:
            found.added_before.append("page_over")
            # Up to two rounds, each told the pages it is at; a round that does not shorten
            # the measured body (or lost a number) is dropped.
            for _ in range(report_checks.CONDENSE_ROUNDS):
                condensed = await report_checks.condense(
                    sections, pages=pages, limit=limit, complete=report._complete,
                    model=req.model, api_key=req.api_key, usage=bundle.usage,
                )
                again, again_counts = await counted(condensed)
                if again and again < pages:
                    sections = condensed
                    pages, counts = again, again_counts
                if pages <= limit:
                    break
            if pages > limit:
                found.added_after.append("page_over")
            found.pages_note = f"본문 쪽수({counts}) / 요청 {limit}쪽"
        elif pages:
            found.pages_note = f"본문 쪽수({counts}) / 요청 {limit}쪽"
        bundle.document.sections = sections


class FormatStep(Base):
    """The format checks' record: found before the repair → left after it."""

    name = "format-step"
    reads = ("findings",)
    writes = ()

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        f = bundle.findings
        traced = f.traced if f.traced is not None else check_skills.Outcome("")
        if not (f.format_checks or f.added_before or f.pages_note or traced.found):
            return
        before, after = f.before, f.after
        record = report_checks.summary(before + f.added_before, after + f.added_after)
        if traced.found:
            record = " · ".join(x for x in (traced.summary, record) if x)
        yield {
            "type": "step", "id": "format_checks",
            "label": "서식 검사: 문제 없음" if not before and not f.added_before
            else f"서식 검사 {len(before) + len(f.added_before)}건 → 수리 후 "
                 f"{len(after) + len(f.added_after)}건",
            "status": "error" if after or f.added_after else "done",
            "detail": " · ".join(x for x in (record, f.pages_note) if x)[:1500],
        }


class Quality(Base):
    """The finished document read once more for what a reader trips on; said in the steps
    (and the log) so a defect is never passed off as done. Then the document goes out."""

    name = "quality"
    reads = ("material", "computed.settled", "document", "findings", "spec.format")
    writes = ("findings.quality", "document.sections")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, f = _report(), bundle.findings
        sections = bundle.document.sections
        format_codes = set(report_checks.LABELS)
        findings = quality_gate.report_findings(sections, _material_text(bundle),
                                                bundle.computed.settled)
        for x in [x for x in f.still if x.code in format_codes][:12]:
            findings.append({"code": x.code,
                             "where": str(sections[x.section].get("heading") or ""),
                             "detail": f"{x.sentence[:80]} — {report.reader_note(x.code, x.why)}"})
        for code in f.added_after:
            findings.append({"code": code, "where": "document",
                             "detail": f.pages_note if code == "page_over" else ""})
        # What the model checks found and no rule could confirm: listed for the reader.
        for x in [x for x in f.still if not x.auto][:12]:  # web, question, judged figures
            findings.append({"code": x.code,
                             "where": str(sections[x.section].get("heading") or ""),
                             "detail": f"{x.sentence[:80]} — {report.reader_note(x.code, x.why)}"})
        if findings:
            log.warning("report quality findings: %s",
                        logs.safe([(x["code"], x["where"]) for x in findings][:12], 2000))
        f.quality = findings
        # Bracket blanks (「[담당자]」) are gaps left for the person, not defects: counted
        # and named.
        blanks, named = placeholders.summary([str(s.get("content") or "") for s in sections],
                                             bundle.document.head_block)
        # The places to check, for the reader: what the web check and the question check
        # found that no rule could confirm or mend.
        detail = " · ".join([f"채울 빈칸: {named}"] * bool(blanks)
                            + [x["detail"] for x in findings
                               if x["code"] in report._TO_CHECK])[:1500]
        yield {
            "type": "step", "id": "quality",
            "label": ("품질 점검: 문제 없음" if not findings else f"품질 점검 {len(findings)}건: "
                      + ", ".join(dict.fromkeys(report._QUALITY_LABELS.get(x["code"], x["code"])
                                                for x in findings)))
            + (f" · 빈칸 {blanks}개" if blanks else ""),
            "status": "done" if not findings else "error",
            **({"detail": detail} if detail else {}),
        }
        # The repair pass rewrites sentences; the list shape is set again after it.
        bundle.document.sections = report.gaejo_body(sections, bundle.spec.format)
        yield {"type": "report", "sections": bundle.document.sections}
        yield _usage(bundle)


for _stage in (
    Research(), Assemble(), Outline(), ApprovedPlan(), Calc(), Announce(), Settle(), Prepass(),
    WriterNotes(), Draft(), Sections(), Figures(), Additions(), Normalize(), TitleBlock(),
    ChecksPlan(), MinutesRepair(), FormatFind(), Verify(), MinutesReground(), FormatAmend(),
    CareerPeriods(), PageLimit(), FormatStep(), Quality(),
):
    registry.register(_stage)


def bundle_for(*, request: str, model: str, api_key: str, **kwargs: Any) -> Bundle:
    """A report bundle from `report.write`'s arguments, its format and genre settled."""
    from app.pipeline.bundle import Request, Spec

    report = _report()
    req = Request(text=request, model=model, api_key=api_key,
                  trusted=list(kwargs.pop("trusted_context", None) or []),
                  untrusted=list(kwargs.pop("untrusted_context", None) or []), **kwargs)
    # The explicit 서식 wins, then the agent's bound format, then the words of the instruction.
    doc_format = doc_formats.detect(instruction_part(request), req.template_id, req.format_id)
    spec = Spec(format=doc_format, format_id=doc_format.id if doc_format is not None else "",
                genre=report._genre_rule(request, doc_format))
    # Outline tokens are counted apart: planning may run on another model.
    usage = {"inputTokens": 0, "outputTokens": 0, "outlineInputTokens": 0,
             "outlineOutputTokens": 0}
    return Bundle(request=req, spec=spec, usage=usage)


__all__ = ["bundle_for"]
