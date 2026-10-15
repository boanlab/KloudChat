"""Report writer stages cut from `report.write`; helpers looked up in `app.services.report` at call time (test patches)."""

from __future__ import annotations

import asyncio
import dataclasses
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
    date_rules,
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
    lint,
    model_retry,
    placeholders,
    process_settings,
    quality_gate,
    report_checks,
    research,
    richtext,
    table_integrity,
    table_mend,
    units,
    value_guard,
    verify,
)
from app.services import deck as deck_rules
from app.services import outline as plan_rules
from app.services.context import (
    build_document_messages,
    instruction_part,
    lead_instruction,
    pasted_material,
    prompt_request,
)

log = logging.getLogger("app.services.report")

#: Beside an earlier step's product in a composed run.
DERIVED_NOTE = (
    "# 작성 메모(내부)\nThe 「앞 단계 결과」 blocks were written by an earlier step from the same "
    "files. Use them for what to cover and how to word it, but take every figure from the "
    "attached files or the 「코드 실행 결과」 blocks; a figure only an earlier step wrote is not "
    "a source — leave it out."
)

_CAUGHT = (httpx.HTTPError, KeyError, IndexError, ValueError)

#: Proposal key holding the outline pass's web findings; stored with the plan, never sent.
PLAN_RESEARCH = "research"


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """The proposal as the browser sees it: without the carried findings."""
    return {k: v for k, v in plan.items() if k != PLAN_RESEARCH}


def _report():
    # late import: `report` imports this module's runner
    from app.services import report

    return report


async def _written(*args, **kwargs) -> tuple[str, dict]:
    """`report._complete` for the draft and a section, asked once more when the connection
    dropped (r3: a section otherwise ends 「이 섹션을 쓰지 못했습니다」)."""
    return await model_retry.once_more(lambda: _report()._complete(*args, **kwargs),
                                       "report call")


def _searched(mat) -> bool:
    """Whether this turn's web search ran (its material is the search's, not an upload)."""
    return bool(mat.web_search and mat.research is not None and mat.research.searched)


def _messages(bundle: Bundle, prompt: str, model: str | None = None) -> list[dict[str, str]]:
    """Writer messages: prompt under the material; `model` = the writer, whose tier sets the
    setup (`prompt_tier`), repair calls to the writer included; none keeps the full one."""
    return build_document_messages(
        SessionKind.report,
        prompt,
        request=bundle.request.text,
        trusted_context=bundle.material.trusted_texts,
        untrusted_context=bundle.material.document,
        research_rule=bundle.material.research_rule,
        model=model,
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
    # derived material is no check source
    return "\n".join([bundle.request.text, *bundle.material.grounds])


# ── before the outline ───────────────────────────────────────────────────


class Research(Base):
    """Search before planning; asks when a web-needing document found nothing."""

    name = "research"
    kind = "io"
    reads = ("request", "spec.format_id")
    writes = ("material.research", "material.web_search", "material.web_selected")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req = _report(), bundle.request
        findings = research.Findings()
        web_search = req.web_search
        # no search: own-material docs, 경력기술서, work docs with material, unless the ask
        # names outside facts (「시장 규모(공개 자료 기준)」 in an 안건 자료 left 「[시장 규모]」)
        if web_search and not report.asks_public_sources(req.text) and (
                report._own_material(req.text) or not bundle.spec.settings.search
                or report.material_complete(req.text, bundle.spec.format_id,
                                            has_files=bool(req.untrusted))):
            web_search = False
        # writing an approved outline: the outline pass's pages carry over (r7 A07-T2: a fresh
        # search kept 7 of the 17 sources, digitalmarket.kr among the lost, and the
        # 디지털서비스몰 section came out blank); only headings they miss are searched again
        kept = (research.from_carried(req.approved_plan.get(PLAN_RESEARCH))
                if req.approved_plan is not None else None)
        if kept is not None:
            web_search = True
            findings = kept
            headings = [h for h in map(report._heading_of,
                                       req.approved_plan.get("sections") or []) if h]
            gaps = research.uncovered(headings, kept.context)
            if gaps and await research.available():
                yield {"type": "step", "id": "sources", "label": "자료 더 찾는 중",
                       "status": "running"}
                more = await research.run(
                    "자료를 더 찾을 부분: " + ", ".join(gaps) + "\n\n원래 요청:\n" + req.text,
                    model=req.outline_model or req.model, api_key=req.api_key,
                    scholarly=report.scholarly_document(req.text))
                findings = research.merge(
                    kept, more, most=max(3, research.MAX_SOURCES - len(kept.sources)))
                apart = bool(req.outline_model)
                bundle.usage["outlineInputTokens" if apart else "inputTokens"] += more.usage[
                    "inputTokens"]
                bundle.usage["outlineOutputTokens" if apart else "outputTokens"] += more.usage[
                    "outputTokens"]
            yield {
                "type": "step",
                "id": "sources",
                "label": f"자료 {len(findings.sources)}건",
                "status": "done",
                "detail": findings.detail,
            }
        elif web_search and await research.available():
            yield {"type": "step", "id": "sources", "label": "자료 찾는 중", "status": "running"}
            planner = req.outline_model or req.model
            findings = await research.run(req.text, model=planner, api_key=req.api_key,
                                          scholarly=report.scholarly_document(req.text),
                                          deep=bundle.spec.settings.deep_search)
            # product comparison: each product's own pages too
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
        # copy: keep caller's list unmodified
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
            # trend/literature with no results: ask instead of writing
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
    """Writer material (attachments, pasted, project citations, research pages) with origins."""

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

        # research log for the report artifact
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
        # disclaimer only for could-not-run / found-nothing
        if mat.web_search and not findings.searched:
            mat.research_rule = research.UNRESEARCHED_RULE
        elif mat.web_search and mat.web_selected == 0:
            mat.research_rule = research.EMPTY_RULE
        mat.parts = [Part(text, "attachment") for text in req.untrusted]
        # pasted material read whole as a reference block
        if pasted := pasted_material(req.text):
            mat.parts.append(Part(pasted, "pasted"))
        # composed run: earlier code output is a source; earlier writing for shape only
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
                ) + nudge, req.outline_model or req.model),
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
        # question instead of 목차 — see `grounding.ASK_RULE`
        if req.may_ask and (asked := grounding.parse_needs(text, req.text)):
            yield {"type": "step", "id": "outline", "label": "확인이 필요합니다", "status": "done"}
            yield {"type": "needs", "questions": [q.wire() for q in asked]}
            yield _usage(bundle)
            raise Stop("needs: the planner asked")
        if req.may_ask and report._results_without_data(request, untrusted, web=_searched(mat)):
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
        # carried on the plan for the separate writing request — see `_FRAME_RULE`
        frame = not req.may_ask and (
            report._results_without_data(request, untrusted, web=_searched(mat))
            or grounding.subject_missing(text, request, "\n".join(untrusted))
        )
        findings = mat.research
        # from memory: document opens by saying so
        unverified = (
            not req.may_ask
            and mat.web_search
            and findings.searched
            and mat.web_selected == 0
            and report._from_the_web(request)
        )
        title, headings = report._parse_outline(text)
        # pasted outline: person's sections; 개조식 keeps its fixed shape
        fixed = report.given_outline(request) or (
            list(bundle.spec.settings.outline) if not wanted else []
        )
        if fixed:
            headings = fixed
        # 1-2 section fixed outline (서식 「본문」) is the person's shape
        least = 1 if (wanted or fixed) else report._MIN_SECTIONS
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
            # model wrote prose about the outline: one more JSON ask; prose list is the fallback
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
                    if len(json_headings) >= least:
                        title, headings, text = json_title or title, json_headings, json_text
        if len(headings) < least:
            # one retry: truncated/odd-shaped outlines usually fine second time
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
        if len(headings) < least:
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
        # planning stops: caller stores, shows, re-calls with approval; figures on a separate card
        plan: dict[str, Any] = {
            "title": title[:200],
            "sections": headings,
            # request style wins; outline's choice is the default
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
        # the writing pass starts from these pages (`Research.run`); kept off the wire
        if mat.web_search and findings.searched and mat.web_selected:
            plan[PLAN_RESEARCH] = research.carried(findings, mat.web_selected)
        # pictures only where the 서식 draws or when asked
        if req.image_model and not (bundle.spec.settings.draws == "when-asked"
                                    and not diagrams.asks_for_diagrams(instruction_part(request))):
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
    """Stored design (None if none); template > project default > request, else plan picks style."""
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
    """Takes the approved 목차; emits title and planning sources."""

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
        # only when produced; caller keeps its fallback
        if plan.title:
            yield {"type": "title", "title": hangul.tidy_spacing(plan.title)[:200]}
        # look before first section, so the panel doesn't restyle on save
        req = bundle.request
        if look := report_design(req.text, approved, req.design_tokens, req.template_id):
            yield {"type": "design", "design": look}
        yield {"type": "sources", "sources": bundle.material.sources}
        plan.figures = {int(f.get("section", -1)): f for f in (bundle.request.figures_plan or [])
                        if f.get("prompt")}


def _given(bundle: Bundle) -> str:
    """Person-given material: request, files, code output."""
    req = bundle.request
    return "\n\n".join([_report()._request_material(req.text), *req.untrusted,
                         *req.tool_outputs]).strip()


#: Writer note when the code calculation gave no values.
CALC_MISSING_NOTE = (
    "# 작성 메모(내부)\nThe code calculation did not finish; at most the given figures' own "
    "arithmetic is computed above. Work out the other figures the request asks for yourself "
    "from the material's numbers: write "
    "each as its formula with the result (「9,900원 × 12 = 118,800원」). Where the request "
    "allows estimates and the material lacks a rate or share, state it as an assumption "
    "(「가정: 전환율 2%」) and compute from it; never leave a requested figure as a formula name only. "
    "No code checked these figures: write 「(추정)」 right after each result you compute "
    "yourself (「118,800원(추정)」), so the reader tells them from the material's and the "
    "code's figures."
)


class Calc(Base):
    """Derived numbers computed by code; printed values go to every section as facts."""

    name = "calc"
    kind = "model"
    reads = ("request", "material.parts")
    writes = ("computed.values", "computed.calc_status", "material.trusted")

    def skip(self, bundle: Bundle) -> str:
        # person's material only, not search pages
        if not calc.needed(bundle.request.text, [_given(bundle)],
                           always=bundle.spec.settings.code_calc):
            return "nothing to compute in the request"
        return ""

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req = _report(), bundle.request
        yield {"type": "step", "id": "calc", "label": "코드로 수치 계산 중", "status": "running"}
        # planner model: more accurate values (93% vs 84%), always closes code block
        computed, spent, status = await calc.computed_values(
            req.text, bundle.material.grounds, complete=report._complete, model=req.judge,
            api_key=req.api_key, always=bundle.spec.settings.code_calc)
        _count(bundle, spent, planner=True)
        bundle.computed.calc_status = status
        if computed:
            bundle.computed.values = computed
            bundle.material.trusted.append(Part(computed, "computed"))
        elif status in ("timeout", "unavailable", "failed"):
            # r7 A10-T2: a timeout left TAM·SAM·SOM uncomputed, only formula names. The given
            # figures' own arithmetic is done here; the writer works out the rest
            if given := calc.given_block(req.text, [_given(bundle)]):
                bundle.computed.values = given
                bundle.material.trusted.append(Part(given, "computed"))
            if status == "timeout":
                bundle.material.trusted.append(Part(CALC_MISSING_NOTE, "note"))
        yield {
            "type": "step", "id": "calc",
            "label": {
                "done": "코드로 수치 계산·검산",
                "partial": "코드로 수치 계산(검산 시간 초과 — 한 번 실행한 값)",
                "unavailable": "코드 실행 없음 — 모델이 계산",
                "disputed": "코드 계산이 서로 달라 쓰지 않음",
                "timeout": "코드 계산 시간 초과 — 모델이 계산한 값은 추정으로 표시",
            }
            .get(status, "코드 계산 실패 — 모델이 계산"),
            "status": "done" if status in ("done", "partial") else "error",
        }


class Announce(Base):
    """Announce sections up front for the panel."""

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
    """Figures settled from the material before writing; sections checked against them."""

    name = "settle"
    kind = "model"
    reads = ("request",)
    writes = ("material.given", "computed.settled", "computed.conflicts")

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        report, req = _report(), bundle.request
        # from files and code output, never `req.derived`
        given = _given(bundle)
        bundle.material.given = given
        if not given:
            return
        settled: list[key_figures.Figure] = []
        try:
            # judgement: planner model when set
            settled, bundle.computed.conflicts, spent = await key_figures.settle_all(
                given, report._complete, req.judge, req.api_key)
            _count(bundle, spent, planner=True)
        except Exception as exc:  # noqa: BLE001 — written without them
            log.info("key figures not settled: %s", exc)
        # derived values (margin, break-even, target profit) worked out by code
        bundle.computed.settled = [*settled, *key_figures.derived(settled)]


class Prepass(Base):
    """Format pre-pass: dates, quote, scores, periods settled by code, outside the settled cap."""

    name = "prepass"
    kind = "model"
    reads = ("request", "spec.format_id", "material.given", "computed.values")
    writes = ("computed.prepass", "computed.settled", "material.parts", "material.trusted")

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.spec.settings.runs_prepass else "no pre-pass calculations set"

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req = _report(), bundle.request
        format_id, given = bundle.spec.format_id, bundle.material.given
        quiet = bundle.spec.settings.prepass_quiet
        prepass: report_checks.Prepass | None = None
        if given:
            prepass_usage: dict[str, int] = {"inputTokens": 0, "outputTokens": 0}
            try:
                prepass = await report.report_checks.prepare(
                    format_id, req.text, given, complete=report._complete,
                    model=req.judge, api_key=req.api_key, usage=prepass_usage,
                    today=dt.datetime.now(ZoneInfo(settings.timezone)).date(),
                    tiebreak_model=report._tiebreak_model(req.judge),
                    process=bundle.spec.settings, deliberate=report._complete_thinking,
                    strong=req.strong,
                )
            except Exception as exc:  # noqa: BLE001 — written without it; the step says so
                log.exception("report pre-pass failed")
                yield {"type": "step", "id": "prepass", "label": "사전 계산 실패",
                       "status": "error", "detail": f"코드 계산 없이 씀 ({type(exc).__name__})"}
            _count(bundle, prepass_usage, planner=True)
        elif not quiet:
            # pre-pass that doesn't run says why
            yield {"type": "step", "id": "prepass", "label": "사전 계산 해당 없음", "status": "done",
                   "detail": "요청과 첨부에 계산할 자료가 없음"}
        if bundle.spec.settings.prepass_always and prepass is None:
            # 개조식 shape/page check even with nothing attached
            prepass = report_checks.Prepass(format_id=format_id,
                                            process=bundle.spec.settings)
        bundle.computed.prepass = prepass
        computed = bundle.computed.values
        if prepass is not None and prepass.costs_withheld and computed:
            # cost readings disagree: no totals to writer or sandbox
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
                else "사전 계산 해당 없음",
                "status": "error" if any(r.get("error") for r in prepass.runs) else "done",
                "detail": " · ".join(f"{r['skill']}: {r['summary']}" for r in prepass.runs)[:1500],
            }
        elif prepass is not None and given and not quiet:
            yield {"type": "step", "id": "prepass", "label": "사전 계산 해당 없음", "status": "done",
                   "detail": "자료에서 이 템플릿이 계산할 값을 찾지 못함"}


class WriterNotes(Base):
    """Settled and conflicting figures in front of the writer."""

    name = "writer-notes"
    reads = ("request", "material.given", "computed.settled", "computed.conflicts",
             "computed.prepass", "spec.format_id")
    writes = ("material.parts",)

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        mat, com = bundle.material, bundle.computed
        if com.settled:
            mat.parts = [Part(key_figures.block(com.settled), "settled"), *mat.parts]
        # dates the request's own rules settle (「개시 30일 전」, 「개강일부터 5일간」), by code
        dated = date_rules.block(bundle.request.text, mat.given,
                                 dt.datetime.now(ZoneInfo(settings.timezone)).date())
        if dated:
            mat.parts = [Part(dated, "prepass"), *mat.parts]
        if com.settled or dated or (com.prepass is not None and com.prepass.blocks):
            # writer notes; block names never reach the page
            mat.parts = [*mat.parts, Part(internal_words.WRITER_NOTE, "note")]
            log.info("settled figures: %s", [(f.name, f.value) for f in com.settled])
        if com.conflicts:
            # both values with where/when; never merged
            mat.parts = [Part(key_figures.conflict_block(com.conflicts,
                                                              bundle.spec.settings.conflict_rule),
                              "conflicts"), *mat.parts]


class Draft(Base):
    """One-call draft for consistency, cut by headings; long docs section by section (`report._long_form`)."""

    name = "draft"
    kind = "model"
    reads = ("request", "spec.genre", "plan", "material")
    writes = ("document.long_form", "document.drafted")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req, plan = _report(), bundle.request, bundle.plan
        long_form = report._long_form(req.text)
        bundle.document.long_form = long_form
        if long_form:
            yield {"type": "step", "id": "draft", "label": "절마다 길게 쓰는 중", "status": "done"}
            return
        yield {"type": "step", "id": "draft", "label": "초안 쓰는 중", "status": "running"}
        genre, sources = bundle.spec.genre, bundle.material.sources
        try:
            draft_text, spent = await _written(
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
                ), req.model),
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
    """Sections in order: from draft or written alone (4 at a time, assumptions first), code-cleaned."""

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

        #: Assumptions section text once written, and its position.
        assumptions: dict = {"text": "", "index": -1}

        async def write_section(index: int, section: dict, written: list[str],
                                tokens: int = 0):
            """One section's call; `tokens` overrides its budget."""
            role = report._section_role(section["heading"], index, len(sections),
                                        "\n".join(written))
            return await _written(
                req.model,
                _messages(bundle, report._shaped(report._SECTION_PROMPT.format(
                    heading=section["heading"],
                    outline=outline_text,
                    # tail only: whole doc crowds out the instruction
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
                    # told before writing so the section can reference its figure
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
                    # others compute from the assumptions section, not re-settle inputs
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
                ), req.model),
                req.api_key,
                tokens or (2400 if long_form else 1200),
            )

        #: sections no call wrote, shown as such (never a blank heading)
        failed: set[int] = set()

        # assumptions section (「핵심 가정」, 「전제」) first: parallel sections would settle conflicting inputs
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

        # long: sections started 4 at a time, taken in order (trim runs in order); short: one draft
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
            # position in `progress` only; surface renders it
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
                if report.error_text(body, req.text):
                    # a tool or error message in place of the section: written once more
                    log.warning("section %r came back as an error message; written again",
                                section["heading"])
                    _count(bundle, spent)
                    body, spent = await write_section(index, section, written)
                    if report.error_text(body, req.text):
                        raise ValueError("error message in place of the section")
                if internal_words.has_tool_call(body):
                    # a tool call written out (no tools while writing): written once more
                    log.warning("section %r came back as a tool call; written again",
                                section["heading"])
                    _count(bundle, spent)
                    body, spent = await write_section(index, section, written)
                    body = internal_words.strip_tool_calls(body)
                    if not body.strip():
                        raise ValueError("tool call in place of the section")
                noted = internal_words.strip_work_notes(body, request=req.text, document=True)
                if noted != body:
                    log.warning("section %r carried the writer's work notes; removed",
                                section["heading"])
                    body = noted
                if internal_words.cut_mid_sentence(body, min_chars=150):
                    # stops mid-sentence (r8 A01-T1: 「이수진 차장이 온라인 동시 송출」 and the
                    # rest of the agenda missing): written once more with more room, kept when
                    # the new one ends whole
                    log.warning("section %r stops mid-sentence; written again",
                                section["heading"])
                    _count(bundle, spent)
                    again, spent = await write_section(index, section, written,
                                                       4800 if long_form else 2400)
                    again = internal_words.strip_work_notes(
                        internal_words.strip_tool_calls(again), request=req.text, document=True)
                    if (again.strip() and not report.error_text(again, req.text)
                            and not internal_words.cut_mid_sentence(again, min_chars=150)):
                        body = again
                if not body.strip():
                    # empty, or notes only: written once more, never shipped empty
                    log.warning("section %r came back empty; written again", section["heading"])
                    _count(bundle, spent)
                    body, spent = await write_section(index, section, written)
                    body = internal_words.strip_work_notes(
                        internal_words.strip_tool_calls(body), request=req.text, document=True)
                    if not body.strip():
                        raise ValueError("empty section")
            except _CAUGHT as exc:
                log.warning("report section %r failed: %s; asked once more with more room",
                            section["heading"], exc)
                # one more call with a much larger budget (r7 GLM-5.3: thinking it could not
                # turn off took the section's whole budget, twice)
                body = ""
                try:
                    body, spent = await write_section(index, section, written,
                                                      4800 if long_form else 3000)
                    body = internal_words.strip_work_notes(
                        internal_words.strip_tool_calls(body), request=req.text, document=True)
                    if report.error_text(body, req.text):
                        body = ""
                except _CAUGHT as again:
                    log.warning("report section %r failed again: %s", section["heading"], again)
                if not body.strip():
                    failed.add(index)
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
                        "content": FAILED_SECTION,
                        "done": True,
                    }
                    # said in the document, not left as a blank heading
                    section["content"] = FAILED_SECTION
                    continue

            _count(bundle, spent)
            # a section cut off mid-sentence ends at its last whole sentence
            body = report.cut_unfinished_tail(body)
            # stray ideographs to Hangul, table gaps closed once before storing
            body = richtext.detach_tables(report.unwrap_json_prose(body))
            if report.placeholder_heavy(body) and any(re.search(r"\d", m or "")
                                                      for m in document_context):
                # 「[측정값]」 cells under an attached numeric table: asked once more with the data
                try:
                    refilled, more = await report._complete(
                        req.model,
                        _messages(bundle, report._FILL_PROMPT.format(
                            heading=section["heading"], body=body[:6000]), req.model),
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
            material = _material_text(bundle)
            if gaps := table_integrity.problems(section["content"], material):
                # rows dropped or not adding up: written once more with the defects named
                try:
                    redone, more = await report._complete(
                        req.model,
                        _messages(bundle, report._TABLE_FIX_PROMPT.format(
                            heading=section["heading"], problems="\n".join(f"- {g}" for g in gaps),
                            body=section["content"][:6000]), req.model),
                        req.api_key,
                        1600,
                    )
                except _CAUGHT as exc:
                    log.info("table rewrite of %r failed: %s", section["heading"], exc)
                else:
                    _count(bundle, more)
                    redone = _cleaned(bundle, index, section, richtext.detach_tables(
                        report.unwrap_json_prose(redone)))
                    if redone.strip() and len(table_integrity.problems(redone, material)) < len(gaps):
                        section["content"] = redone
                section["content"] = table_integrity.mend(section["content"], material)

            # drawn after prose: failed drawing leaves no dangling reference
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
                    # appended as Markdown: panel, page view, exporters all read the body
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
        if sections and len(failed) == len(sections):
            # nothing written: no document to check, save and call finished (r7 A04-T2 GLP:
            # six empty sections went on through every check and were saved as 「1개 절로 …
            # 작성을 마쳤습니다」 holding only a code-added line)
            yield {"type": "error",
                   "message": "보고서의 어느 절도 쓰지 못했습니다. 잠시 뒤 다시 시도해 주세요."}
            yield _usage(bundle)
            raise Stop("every section failed")


#: what a section no call could write says, in place of a blank heading
FAILED_SECTION = "_이 절은 쓰지 못했습니다. 이 절만 다시 써 달라고 요청해 주세요._"


def _cleaned(bundle: Bundle, index: int, section: dict, body: str) -> str:
    """Section as stored: trims, block repair, number/owner guards — code, never a rewrite."""
    report, request = _report(), bundle.request.text
    sections, doc_format = bundle.document.sections, bundle.spec.format
    prepass, sources = bundle.computed.prepass, bundle.material.sources
    document_context = bundle.material.document
    # cut by code: model rewording invents content
    earlier = [str(s.get("content") or "") for s in sections[:index]]
    if doc_format is not None and doc_format.id == "minutes":
        # minutes state a decision, its discussion and the next meeting each in its own part
        earlier = []
    # reference lists exempt from prose number/owner guards
    is_reference = bool(report._REFERENCE_HEADING.search(str(section["heading"] or "")))
    trimmed: list[str] = []
    # a strong writer's sentences are its own: no code cut on a reading of 「restated」 (r7
    # A10-T1: a strength's supporting sentence went as a repeat of an earlier figure)
    if not is_reference and not bundle.request.strong:
        body, trimmed = report.trim_restatements(
            body, earlier, keep_items=doc_format is not None and doc_format.lists == "gaejo",
            question=bool(report.QUESTION_HEADING.search(str(section["heading"] or ""))),
        )
    if trimmed:
        log.info("trimmed %d sentence(s) from %r: %s", len(trimmed), section["heading"],
                 trimmed[0][:80])
    clean, _ = hangul.read_back(report._without_own_heading(body, section["heading"]))
    # section = level-two heading; subheadings below
    clean = report._FIGURE_MARK.sub(lambda m: f"\n\n{m.group(0)}\n\n", clean)
    clean = report.split_long_paragraphs(
        report.demote_subheadings(
            # fences onto own lines, then counted and paired
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
    # owners and dates from request, material or source only
    clean = report._unsourced_owner_dates(
        clean,
        "\n".join(
            [request, *document_context, *[str(s.get("quote") or "") for s in sources or []]]
        ),
        prepass.bound_dates if prepass is not None and prepass.bound_dates else None,
    )
    today = report.local_today()
    computed = {str(d.get("date") or "") for d in (prepass.dates if prepass is not None else [])}
    if (doc_format is not None and doc_format.id == "minutes"
            and today.isoformat() not in computed):
        # minutes carry the memo's dates; the writer's today is none of them
        clean = report.blank_today(clean, "\n".join([request, *document_context]), today)
    if bundle.plan.unverified and index == 0:
        # stated at the head of the first section
        clean = report._UNVERIFIED_NOTE + "\n\n" + clean
    # 「A × B = C」 checked by arithmetic
    clean = report.fix_restated_amounts(
        report.fix_table_formulas(units.fix_written_sums(
            units.fix_written_arithmetic(clean), printed=[bundle.computed.values])),
        given_text,
    )
    # a formula a unit off and a profit its own figures do not leave: marked (r9 A10-T1)
    clean = units.mark_profit_contradictions(units.mark_slipped_formulas(clean))
    clean = report._grounded_figures(
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
    if not bundle.material.grounded:
        # money the sources state stays; the rest is marked, never blanked — after the
        # arithmetic fixes, so a mark never splits a formula they read
        clean = report._without_invented_money(clean, [
            given_text, *(report._source_text(row) for row in sources or [])])
    return richtext.tidy_tables(clean)


class Figures(Base):
    """Mermaid figures: writer-placed first, then the planner's."""

    name = "figures"
    kind = "model"
    reads = ("request", "plan.figures", "material", "document.sections")
    writes = ("document.sections",)

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, req = _report(), bundle.request
        request, sections = req.text, bundle.document.sections
        # planned on written sections; skipped for peer review, one-page 개조식, 회의록; approved pictures kept
        draws_nothing = report._genre_rule(request).startswith("Genre: 학회 피어리뷰") or (
            (bundle.spec.settings.draws == "when-asked" or report.form_document(request))
            and not diagrams.asks_for_diagrams(instruction_part(request))
        )
        figure_cap = min(5, max(diagrams.MAX_FIGURES, len(sections) // 2 + 1))
        # writer-placed (〔그림〕) first; planner only looks at sections with none
        drawn = 0
        # drawn flowcharts: one figure per procedure; a numbered procedure shows every step
        flows: list[str] = []
        steps = diagrams.given_steps(instruction_part(request))
        for index, section in enumerate(sections):
            marks = report.figure_marks(str(section.get("content") or ""))
            for mark in marks:
                content = str(section.get("content") or "")
                again = mark["kind"] == "flow" and not diagrams.drop_repeated_flows(
                    [diagrams.Planned(index, "flow", mark["description"], mark["caption"])],
                    flows)
                if not mark["kind"] or draws_nothing or drawn >= figure_cap or again:
                    section["content"] = report.drop_figure(content, mark["mark"])
                    continue
                name = mark["caption"] or diagrams.FIGURES[mark["kind"]]
                progress = {"current": index + 1, "total": len(sections)}
                yield {"type": "step", "id": f"dia{index}", "label": f"{name} 그리는 중",
                       "status": "running", "progress": progress}
                try:
                    made, spent, png = await diagrams.make(
                        diagrams.Planned(index, mark["kind"], mark["description"],
                                         mark["caption"], context=content,
                                         document="\n\n".join(
                                             str(s.get("content") or "") for s in sections),
                                         steps=steps),
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
                if made.get("figure") == "flow":
                    flows.append(str(made.get("source") or ""))
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
            # ~1 per 2 sections, 3–5, writer's counted
            limit=max(1, figure_cap - drawn),
            # 2 for 5+ sections, else 1
            at_least=max(0, (2 if len(sections) >= 5 else 1) - drawn),
            wrap=lambda prompt: _messages(bundle, prompt),
        )
        _count(bundle, spent)
        # one figure per procedure: the writer's own flow is not drawn again
        planned = [dataclasses.replace(row, steps=steps) if row.figure == "flow" else row
                   for row in diagrams.drop_repeated_flows(planned, flows)]
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
    """Additions the draft left out (citations, endpoints, question set, tables, open comparison cells), never rewrites."""

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
        # references looked up when search is on; unfound marked, not removed
        if findings.searched:
            sections = await report._mark_unverified_references(sections)
        sections = report.add_missing_endpoints(sections, material_text)
        # person's material only, not search pages
        sections = report.carry_question_set(sections, bundle.material.given, req.text)
        # writer-rebuilt computed table replaced by the computed one
        if computed := bundle.computed.values:
            sections, replaced = calc.replace_tables(sections, computed)
            if replaced:
                log.info("replaced %d table(s) with the computed values", replaced)
        # open comparison cell (「배포 방식 명시 없음」) gets its own search
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
    """All format mends in fixed order (verify repeats it), then list shape."""

    name = "normalize"
    reads = ("request", "material", "computed.settled", "computed.values",
             "computed.calc_status", "spec.format", "document.sections")
    writes = ("document.sections",)

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        report = _report()
        sections = report.normalize_document(
            bundle.document.sections, settled=bundle.computed.settled,
            sources=bundle.material.sources, request=bundle.request.text,
            context=_material_text(bundle))
        if not bundle.material.grounded:
            sections = report.unmark_worked(sections, [
                bundle.request.text, *bundle.material.document,
                *(report._source_text(row) for row in bundle.material.sources or [])])
        sections = report.gaejo_body(sections, bundle.spec.format)
        sections = _figures_marked(bundle, sections)
        if bundle.spec.format_id in report.PLAIN_FORMATS or report.form_document(
                bundle.request.text):
            whole = "\n".join(str(s.get("content") or "") for s in sections)
            sections = [{**s, "content": report.plain_blocks(str(s.get("content") or ""), whole)}
                        for s in sections]
        bundle.document.sections = sections


#: calculation outcomes after which the writer worked figures out itself
UNCHECKED_CALC = ("timeout", "partial", "unavailable", "failed", "disputed")


def _figures_marked(bundle: Bundle, sections: list[dict]) -> list[dict]:
    """What code finds in the document's figures but cannot settle, marked where it stands
    (every writer): a figure ten times a worked result elsewhere in the document, the writer's
    own results when the code calculation did not finish (「(추정)」), and a company-assumption
    label on figures the person never gave (r9 A10-T2)."""
    person = "\n".join([bundle.request.text, bundle.material.given])
    known = "\n".join([person, bundle.computed.values or ""])
    unchecked = bundle.computed.calc_status in UNCHECKED_CALC
    whole = "\n".join(str(s.get("content") or "") for s in sections)
    report = _report()
    out = []
    for sec in sections:
        content = str(sec.get("content") or "")
        if not content or report._REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            out.append(sec)
            continue
        marked = units.label_assumptions(units.mark_scaled_restatements(content, whole), person)
        if unchecked:
            marked = units.label_estimates(marked, known)
        out.append({**sec, "content": marked} if marked != content else sec)
    return out


class TitleBlock(Base):
    """Purpose-shaped document head, filled only from the request."""

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
        if prepass is not None and prepass.rfp and "rfp-client" in prepass.settings.head:
            # cover names the RFP's client, never a guess or the proposer
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
    """Verify and format code checks as functions, rebuilt per request (`runs.REBUILT`)."""
    report, req = _report(), bundle.request
    settled, prepass = bundle.computed.settled, bundle.computed.prepass
    judge = req.judge
    run_checks = list(checks.CODE_CHECKS)
    # judge reads figure sentences only when not the writer (writer over-flags itself)
    if req.outline_model and settled:
        run_checks.append(checks.judge_figures(req.outline_model, req.api_key))
    # web check of writer knowledge; judge works each assessment item
    if bundle.material.web_search:
        run_checks.append(fact_check.web_facts(judge, req.api_key))
    run_checks.append(fact_check.solve_questions(judge, req.api_key))
    format_checks = report_checks.checks(prepass)
    run_checks.extend(format_checks)
    # judged format checks: one call each, shown, never rewritten
    run_checks.extend(report_checks.model_checks(
        prepass, complete=report._complete, judge=judge, api_key=req.api_key))
    if prepass is None and bundle.spec.settings.rules and judge:
        # 검토 규칙 judged with or without a pre-pass
        run_checks.append(report_checks.judge_rules(bundle.spec.settings.rules,
                                                    report._complete, judge, req.api_key))
    # 서식 structure rules: shown, never rewritten
    run_checks.extend(report_checks.structure_checks(bundle.spec.settings))
    return run_checks, format_checks


def _rebuild_checks(bundle: Bundle) -> None:
    bundle.findings.checks, bundle.findings.format_checks = checks_of(bundle)


runs.REBUILT["checks-plan"] = _rebuild_checks


class ChecksPlan(Base):
    """Verify-stage checks, attached check skills, settled rows mended."""

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
        # every value traces to given/computed, else blank; skills before and after verify
        keys = req.check_keys
        if keys is None:
            template = design_templates.get(req.template_id)
            keys = check_skills.attached(
                template_checks=template.check_skills if template else (),
                specs=req.skill_specs)
        found.check_keys = tuple(keys)
        found.traced = check_skills.Outcome("")


class MinutesRepair(Base):
    """Minutes repair: 출처/비고 on action rows, bound dues, no dates without a meeting date, owners/quantities."""

    name = "minutes-repair"
    reads = ("request", "material.given", "computed.prepass", "document.sections")
    writes = ("document.sections", "findings.before")

    def skip(self, bundle: Bundle) -> str:
        if not bundle.spec.settings.has_stage(self.name):
            return "not set for this document"
        return "" if bundle.computed.prepass is not None else "no pre-pass"

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        prepass, before = bundle.computed.prepass, bundle.findings.before
        known = f"{bundle.request.text}\n{bundle.material.given}"
        sections, widened = report_checks.minutes_columns(bundle.document.sections,
                                                          prepass.dates)
        before += ["minutes_columns"] * widened
        # one meeting, no mail: a 출처 column of 「회의」 on every row says nothing
        sections, _ = report_checks.minutes_one_source(sections, prepass.dates)
        # 「미정(사유)」: reason to 비고 by code, so no rewrite reads it as an owner
        sections, _ = report_checks.minutes_deferred(sections)
        sections, filled = report_checks.minutes_due_fill(sections, prepass.dates)
        before += ["minutes_due"] * filled
        if prepass.undated:
            # no meeting date: nothing dated as the meeting's
            sections, undated = report_checks.minutes_undated(sections, prepass.dates, known)
            before += undated
        # owners/quantities repaired by code, rechecked after verify rewrites
        repaired, ungrounded = report_checks.minutes_grounding(sections, known)
        before += ungrounded
        bundle.document.sections = settled_sections(sections, repaired, [known])


def settled_sections(before: list[dict], after: list[dict], evidence: list[str]) -> list[dict]:
    """Sections a code guard repaired, its value blanks and emptied blocks undone."""
    from app.services import value_guard

    out = []
    whole = "\n".join(str(row.get("content") or "") for row in before)
    for old, new in zip(before, after, strict=True):
        a, b = str(old.get("content") or ""), str(new.get("content") or "")
        out.append({**new, "content": value_guard.settle(
            a, b, value_guard.Evidence.of(evidence, answer=a, worked=whole)).text}
            if a != b else new)
    return out


def _doc(bundle: Bundle) -> verify.Doc:
    return verify.Doc(bundle.document.sections, _material_text(bundle), bundle.request.text,
                      list(bundle.material.sources or []), bundle.computed.settled,
                      bundle.computed.conflicts)


class FormatFind(Base):
    """Format code checks found once before repair (for the record)."""

    name = "format-find"
    kind = "check"
    reads = ("findings.format_checks", "document.sections", "material", "computed")
    writes = ("findings.before",)

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.findings.format_checks else "the format has no code checks"

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        first, _ = await verify.find(_doc(bundle), bundle.findings.format_checks)
        bundle.findings.before += [f.code for f in first]


def keep_blanks(before: list[dict], after: list[dict], traced: Any) -> list[dict]:
    """`after` with values grounding blanked kept blank where a repair wrote them back."""
    blanked = {(str(f.get("text") or "").strip(), str(f.get("placeholder") or ""))
               for f in (getattr(traced, "found", None) or [])}
    blanked = {(text, mark) for text, mark in blanked
               if mark and len(text) >= 2 and re.search(r"\d", text)}
    if not blanked:
        return after
    old = {str(row.get("heading") or ""): str(row.get("content") or "") for row in before}
    out = []
    for row in after:
        content = str(row.get("content") or "")
        was = old.get(str(row.get("heading") or ""), "")
        mended = content
        for text, mark in blanked:
            if text in mended and text not in was:
                mended = mended.replace(text, mark)
        if mended != content:
            log.info("verify wrote back blanked values in %r", logs.safe(row.get("heading")))
        out.append({**row, "content": mended} if mended != content else row)
    return out


#: a request asking for unconfirmed points to be marked: 「확인 못 한 건 확인 필요로 표시해 줘」
_MARKS_ASKED = re.compile(
    r"확인\s{0,2}필요[^\n]{0,12}(?:표시|표기|적어|달아|붙여)|(?:표시|표기)[^\n]{0,12}확인\s{0,2}필요"
    r"|(?:미확인|확인\s{0,2}(?:못|안)\s{0,2}(?:한|된))[^\n]{0,24}(?:표시|표기)")
_MARK = "(확인 필요)"


def mark_unsettled(sections: list[dict], still: list) -> list[dict]:
    """Sections with 「(확인 필요)」 after each sentence a model check left unsettled (a web
    claim, a judged figure), once each; table rows and marked sentences stay as they are."""
    out = [dict(sec) for sec in sections]
    for f in still:
        if getattr(f, "auto", True) or not f.sentence or not 0 <= f.section < len(out):
            continue
        sentence = f.sentence.rstrip()
        content = str(out[f.section].get("content") or "")
        if sentence.lstrip().startswith("|") or sentence not in content or _MARK in sentence:
            continue
        at = content.find(sentence) + len(sentence)
        if content[at:at + len(_MARK) + 2].lstrip().startswith(_MARK):
            continue
        body = sentence
        tail = ""
        while body and body[-1] in ".!?。":
            tail = body[-1] + tail
            body = body[:-1]
        content = content.replace(sentence, f"{body} {_MARK}{tail}", 1)
        out[f.section]["content"] = content
    return out


#: a number-binding finding's own words: 「「14달」은 자료에서 「납기 2주」의 값일 수 있다」
_BOUND = re.compile(r"^「(?P<n>[^」]{1,24})」은 자료에서 「(?P<b>[^」]{0,80})」")


def bound_token(finding: Any, evidence: str) -> str:
    """The value with its unit a number-binding finding names (spaces out), when its sentence
    has it and code confirms it is in none of the evidence; else "" (the judge's reading is
    then shown, never acted on)."""
    m = _BOUND.match(str(getattr(finding, "why", "") or ""))
    digits = re.search(r"\d[\d,.]*", m.group("n")) if m else None
    if not m or not digits:
        return ""
    number = digits.group(0).rstrip(",.")
    unit = m.group("n")[digits.end():].strip()
    tail = re.escape(unit) if unit else r"[가-힣%]{1,2}"
    hit = re.search(rf"(?<![\d,.]){re.escape(number)}\s?{tail}", finding.sentence or "")
    if not hit:
        return ""
    token = re.sub(r"\s+", "", hit.group(0))
    squeezed = re.sub(r"\s+", "", evidence or "").replace(",", "")
    # the shortest reading of the value: 「14달」 of 「14달은」
    if any(token[:k].replace(",", "") in squeezed for k in range(len(number) + 1, len(token) + 1)):
        return ""
    return token if unit else token[:len(number) + 1]


def bound_repair(finding: Any, token: str) -> Any:
    """A confirmed number-binding finding as a repair item: wrong while `token` stands, the
    source's own value for that place as evidence."""
    m = _BOUND.match(str(getattr(finding, "why", "") or ""))
    return dataclasses.replace(
        finding, auto=True, still=lambda s: token in re.sub(r"\s+", "", s),
        evidence=f"자료에서 이 숫자 자리의 값: 「{m.group('b')}」" if m else "")


class Verify(Base):
    """Find, repair once, find again; leftovers reported, never passed off."""

    name = "verify"
    kind = "check"
    reads = ("request", "material", "computed", "findings.checks", "document.sections")
    writes = ("document.sections", "findings.still", "findings.after")

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        report, req = _report(), bundle.request
        settled, sources = bundle.computed.settled, bundle.material.sources
        context_text = _material_text(bundle)
        still: list[verify.Finding] = []
        before = list(bundle.document.sections)
        try:
            bundle.document.sections, still, spent = await verify.verify_and_repair(
                _doc(bundle),
                bundle.findings.checks,
                complete=report._complete,
                # a strong writer's sentences are repaired by the writer, never a weaker model
                model=req.reviser,
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
        bundle.document.sections = keep_blanks(before, bundle.document.sections,
                                               bundle.findings.traced)
        disputed = [f for f in still if f.code == "fact" and not f.auto]
        if disputed and req.strong:
            # the web check's contradictions go back to the strong writer once, evidence
            # attached; it fixes the sentence or keeps it when the page is about something else
            # (r8 A07·A10: flagged in the step detail, left in the text unmarked)
            try:
                bundle.document.sections, unfixed, spent = await verify.repair(
                    _doc(bundle), [dataclasses.replace(f, auto=True) for f in disputed],
                    complete=report._complete, model=req.reviser, api_key=req.api_key)
                _count(bundle, spent, planner=True)
                kept = {(f.section, f.sentence) for f in unfixed}
                still = [f for f in still if f not in disputed
                         or (f.section, f.sentence) in kept]
            except Exception:  # noqa: BLE001 — the sentences stay; they are marked below
                log.exception("hand-back of web disputes failed")
        # a judged figure code still finds at odds with a settled figure is fixed once, by the
        # writer when strong and by the planner otherwise, and marked if it stands (r8 L27F·LFLF
        # A10-T2, A03-T2: found in the check, shipped in the text unmarked)
        confirmed = [f for f in still if not f.auto and f.code == "judged_figure"
                     and f.still is not None and f.still(f.sentence)]
        # a number a judge read as another item's whose value with its unit is in none of the
        # evidence goes the same way (r9 LFLF R3-A04-T2: 「납기 14달」 flagged with the
        # source's 「납기 2주」, shipped as written)
        evidence = "\n".join([context_text, bundle.computed.values,
                              *(str(getattr(x, "value", "")) for x in settled or [])])
        bound = {id(f): token for f in still if not f.auto and f.code == "number_binding"
                 and (token := bound_token(f, evidence))}
        confirmed += [f for f in still if id(f) in bound]
        if confirmed:
            try:
                bundle.document.sections, unfixed, spent = await verify.repair(
                    _doc(bundle), [bound_repair(f, bound[id(f)]) if id(f) in bound
                                   else dataclasses.replace(f, auto=True) for f in confirmed],
                    complete=report._complete, model=req.reviser, api_key=req.api_key,
                    clean=lambda s: units.fix_written_sums(units.fix_written_arithmetic(s)))
                _count(bundle, spent, planner=True)
                kept = {(f.section, f.sentence) for f in unfixed}
                still = [f for f in still if f not in confirmed
                         or (f.section, f.sentence) in kept]
            except Exception:  # noqa: BLE001 — the sentences stay; they are marked below
                log.exception("repair of confirmed judged figures failed")
            confirmed = [f for f in confirmed if f in still]
        if (pre := bundle.computed.prepass) is not None and pre.musts and not req.strong:
            # a recommendation still against the must-have matrix: the code's verdict instead;
            # a strong writer's recommendation stands, the finding reported (r7 A04-T2)
            bundle.document.sections, still = report_checks.settle_verdict(
                bundle.document.sections, still, pre.musts, pre.scores)
        if _MARKS_ASKED.search(req.text or ""):
            # the person asked for unconfirmed points to be marked: a sentence a check found at
            # odds with the sources and no rule could settle is marked where it stands (r7 A07-T2)
            bundle.document.sections = mark_unsettled(bundle.document.sections, still)
        elif disputed or confirmed:
            # a sentence a web page contradicts, or a figure code still finds wrong, standing,
            # is marked for the reader whatever the request said (r8 A07·A10: left unmarked, in
            # the step detail only)
            bundle.document.sections = mark_unsettled(
                bundle.document.sections, [f for f in still if f.code == "fact"] + confirmed)
        bundle.findings.still = still
        format_codes = set(report_checks.LABELS)
        bundle.findings.after += [f.code for f in still if f.code in format_codes]


class MinutesReground(Base):
    """Minutes owner/quantity repair after verify rewrites; second-pass leftovers stay."""

    name = "minutes-reground"
    kind = "check"
    reads = ("request", "material.given", "computed.prepass", "document.sections")
    writes = ("document.sections", "findings.after")

    def skip(self, bundle: Bundle) -> str:
        if not bundle.spec.settings.has_stage(self.name):
            return "not set for this document"
        return "" if bundle.computed.prepass is not None else "no pre-pass"

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        known = f"{bundle.request.text}\n{bundle.material.given}"
        # a due the checks blanked to 「[기한]」 gets the material's date back (r5 A01-T1: a
        # wrong 11/16 was blanked, the memo's 10/16 never restored)
        sections, _ = report_checks.minutes_due_fill(bundle.document.sections,
                                                     bundle.computed.prepass.dates, blanked=True)
        sections, again = report_checks.minutes_grounding(sections, known)
        if again:
            sections, left = report_checks.minutes_grounding(sections, known)
            bundle.findings.after += left
        bundle.document.sections = settled_sections(bundle.document.sections, sections, [known])


class FormatAmend(Base):
    """Required format content added (one amendment each), rechecked; missing RFP client counted."""

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
    """Periods in one format, for readers and ATS."""

    name = "career-periods-tidy"
    reads = ("document.sections",)
    writes = ("document.sections",)

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.spec.settings.has_stage(self.name) else "not set for this document"

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        bundle.document.sections, tidied = report_checks.tidy_periods(bundle.document.sections)
        if tidied:
            log.info("career periods written one way: %d", tidied)


class PageLimit(Base):
    """Page limit on rendered body: 개조식 over limit condensed, proposal over limit reported."""

    name = "page-limit"
    kind = "model"
    reads = ("request", "spec", "plan.title", "material.given", "computed.prepass",
             "document")
    writes = ("document.sections", "findings.added_before", "findings.added_after",
              "findings.pages_note")

    def skip(self, bundle: Bundle) -> str:
        return "" if bundle.spec.settings.has_stage(self.name) else "not set for this document"

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        report, req, found = _report(), bundle.request, bundle.findings
        doc_format, process = bundle.spec.format, bundle.spec.settings
        limit = (report_checks.page_limit(process, req.text, bundle.material.given)
                 if bundle.computed.prepass is not None or process.structure.page_limit
                 else None)
        if not limit:
            return
        title, sections = bundle.plan.title, bundle.document.sections
        shown_block, design_tokens = bundle.document.head_block, req.design_tokens
        # 개조식: both exported files through 붙임 and 「끝.」; longer counts
        by_hwpx = bool(process.structure.page_limit)

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
            # over RFP limit: reported, not cut — proposer's call
            found.added_before.append("page_over")
            found.added_after.append("page_over")
            found.pages_note = f"본문 쪽수({counts}) / 제한 {limit}쪽"
        elif pages and pages > limit:
            found.added_before.append("page_over")
            # ≤2 rounds; round not shortening (or losing a number) dropped
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
    """Format check record: found before repair → left after."""

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
            "label": "템플릿 검사: 문제 없음" if not before and not f.added_before
            else f"템플릿 검사 {len(before) + len(f.added_before)}건 → 수리 후 "
                 f"{len(after) + len(f.added_after)}건",
            "status": "error" if after or f.added_after else "done",
            "detail": " · ".join(x for x in (record, f.pages_note) if x)[:1500],
        }


#: the request asking unconfirmed points to be marked in the text
_ASKS_MARKS = re.compile(
    r"확인\s{0,2}(?:이\s{0,2})?필요[^\n]{0,20}(?:표시|적|달|남겨|밝혀)"
    r"|(?:확인|검증)(?:하지\s{0,2}못한|\s{0,2}못\s{0,2}한|되지\s{0,2}않은|\s{0,2}안\s{0,2}된)"
    r"[^\n]{0,30}(?:표시|구분|밝혀|적어)")


def mark_web_disputes(sections: list[dict], still: list, request: str) -> int:
    """Sentences the web check found contradicted, still standing after the repair, marked
    「(확인 필요)」 in the text when the request asked unconfirmed points to be marked (r7
    A07-T2: three such sentences went out unmarked; they were in the step detail only).
    Returns how many were marked."""
    if not _ASKS_MARKS.search(instruction_part(request or "")):
        return 0
    marked = 0
    for x in still:
        if x.code != "fact" or not 0 <= x.section < len(sections):
            continue
        content = sections[x.section].get("content")
        sentence = x.sentence.strip()
        if not isinstance(content, str) or not sentence or sentence not in content:
            continue
        at = content.index(sentence) + len(sentence)
        if value_guard.MARK in sentence or content[at:at + 12].lstrip().startswith(
                value_guard.MARK):
            continue
        body = sentence.rstrip(".。 ")
        end = content.index(sentence) + len(body)
        sections[x.section] = {**sections[x.section],
                               "content": f"{content[:end]} {value_guard.MARK}{content[end:]}"}
        marked += 1
    return marked


#: quality findings on a table's or figure's shape, mended by code and read again
_SHAPE_CODES = frozenset({"pipe_rows_not_a_table", "stray_caption", "table_without_caption",
                          "table_rows", "unmentioned"})
#: a header-only table still standing after the mend, marked under the table
_EMPTY_TABLE_MARK = "(확인 필요: 이 표에는 값이 없습니다)"


def _markdown(sec: dict) -> bool:
    return (isinstance(sec.get("content"), str)
            and str(sec.get("format") or "markdown") == "markdown"
            and not _report()._REFERENCE_HEADING.search(str(sec.get("heading") or "")))


def _rows_marked(body: str) -> str:
    """A header-only table marked once under its rule (r9 LFLF R3-A01-T1: it shipped bare)."""
    lines = body.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        out.append(line)
        header = i >= 1 and lines[i - 1].lstrip().startswith("|")
        rule = re.match(r"^\s*\|?\s*:?-{2,}", line) and header
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        after = next((ln.strip() for ln in lines[i + 1:] if ln.strip()), "")
        if rule and not nxt.startswith("|") and after != _EMPTY_TABLE_MARK:
            out += ["", _EMPTY_TABLE_MARK]
    return "\n".join(out)


def mend_shapes(sections: list[dict], findings: list[dict], *, gaejo: bool = False) -> list[dict]:
    """Sections with the table and figure shape the quality read found broken mended by code:
    broken pipe tables rebuilt (`table_mend.rebuild`), uncaptioned tables captioned, a table or
    figure the text never names pointed at, pipe lines no table takes written as items, and a
    header with no rows marked. Never a model call; `Quality` reads the result again."""
    report = _report()
    codes = {x["code"] for x in findings}
    if not codes & _SHAPE_CODES:
        return sections
    out = [{**sec, "content": table_mend.rebuild(sec["content"])} if _markdown(sec) else sec
           for sec in sections]
    if out != sections or codes & {"table_without_caption", "stray_caption"}:
        out = report.caption_figures_and_tables(out)
    again = quality_gate.report_findings(out)
    for x in again:
        if x["code"] != "unmentioned":
            continue
        for index, sec in enumerate(out):
            if _markdown(sec) and (cited := table_mend.cite(
                    sec["content"], x["where"], str(sec.get("heading") or ""),
                    gaejo=gaejo)) != sec["content"]:
                out[index] = {**sec, "content": report.fix_number_particles(cited)}
                break
    loose = any(x["code"] == "pipe_rows_not_a_table" for x in again)
    for index, sec in enumerate(out):
        if not _markdown(sec):
            continue
        body = table_mend.flatten(sec["content"]) if loose else sec["content"]
        if any("만 있고 행이 없다" in gap for gap in table_integrity.problems(body, "")):
            body = _rows_marked(body)
        if body != sec["content"]:
            out[index] = {**sec, "content": body}
    return out


class Quality(Base):
    """Final read for reader-facing defects, reported in steps and log; then the document goes out."""

    name = "quality"
    reads = ("material", "computed.settled", "document", "findings", "spec.format")
    writes = ("findings.quality", "document.sections")

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        report, f = _report(), bundle.findings
        # verbatim repeat of an earlier section's line dropped from the later one
        sections, repeated = lint.drop_repeats(bundle.document.sections)
        if repeated:
            log.info("dropped %d repeated line(s) across sections", len(repeated))
            bundle.document.sections = sections
        material = _material_text(bundle)
        # amounts scaled twice go back to the material's form before the read
        restored = [
            {**s, "content": units.restore_material_scale(str(s.get("content") or ""), material)}
            if isinstance(s.get("content"), str) else s
            for s in sections
        ]
        if restored != sections:
            log.info("restored double-scaled amounts to the material's form")
            sections = bundle.document.sections = restored
        if mark_web_disputes(sections, f.still, bundle.request.text):
            bundle.document.sections = sections
        format_codes = set(report_checks.LABELS)

        def read(secs: list[dict]) -> list[dict]:
            found = quality_gate.report_findings(secs, material, bundle.computed.settled)
            for sec in secs:
                for gap in table_integrity.problems(str(sec.get("content") or ""), material):
                    found.append({"code": "table_rows", "where": str(sec.get("heading") or ""),
                                  "detail": gap})
            return found

        findings = read(sections)
        # a broken table or an unnamed one is mended by code and read again, once; kept only
        # when it adds no finding nor a new kind (r9 LFLF R3-A01-T1, A03-T2: found here,
        # shipped as found)
        mended_count = 0
        gaejo = getattr(bundle.spec.format, "lists", "") == "gaejo"
        if (mended := mend_shapes(sections, findings, gaejo=gaejo)) != sections:
            again = read(mended)
            if (len(again) <= len(findings)
                    and {x["code"] for x in again} <= {x["code"] for x in findings}):
                log.info("quality read: %d finding(s) mended by code", len(findings) - len(again))
                mended_count = len(findings) - len(again)
                sections = bundle.document.sections = mended
                findings = again
        for x in [x for x in f.still if x.code in format_codes][:12]:
            findings.append({"code": x.code,
                             "where": str(sections[x.section].get("heading") or ""),
                             "detail": f"{x.sentence[:80]} — {report.reader_note(x.code, x.why)}"})
        for code in f.added_after:
            findings.append({"code": code, "where": "document",
                             "detail": f.pages_note if code == "page_over" else ""})
        # model findings no rule confirmed: listed for the reader
        for x in [x for x in f.still if not x.auto][:12]:  # web, question, judged figures
            findings.append({"code": x.code,
                             "where": str(sections[x.section].get("heading") or ""),
                             "detail": f"{x.sentence[:80]} — {report.reader_note(x.code, x.why)}"})
        if findings:
            log.warning("report quality findings: %s",
                        logs.safe([(x["code"], x["where"]) for x in findings][:12], 2000))
        f.quality = findings
        # bracket blanks (「[담당자]」) counted as gaps, not defects
        blanks, named = placeholders.summary([str(s.get("content") or "") for s in sections],
                                             bundle.document.head_block)
        # places to check: web/question findings no rule could confirm
        detail = " · ".join([f"채울 빈칸: {named}"] * bool(blanks)
                            + [x["detail"] for x in findings
                               if x["code"] in report._TO_CHECK])[:1500]
        yield {
            "type": "step", "id": "quality",
            "label": ("품질 점검: 문제 없음" if not findings else f"품질 점검 {len(findings)}건: "
                      + ", ".join(dict.fromkeys(report._QUALITY_LABELS.get(x["code"], x["code"])
                                                for x in findings)))
            + (f" · 고침 {mended_count}곳" if mended_count else "")
            + (f" · 빈칸 {blanks}개" if blanks else ""),
            "status": "done" if not findings else "error",
            **({"detail": detail} if detail else {}),
        }
        # list shape reset after repair rewrites
        bundle.document.sections = report.untitled_body(
            report.gaejo_body(sections, bundle.spec.format), bundle.spec.settings.outline)
        yield {"type": "report", "sections": bundle.document.sections}
        yield _usage(bundle)


for _stage in (
    Research(), Assemble(), Outline(), ApprovedPlan(), Calc(), Announce(), Settle(), Prepass(),
    WriterNotes(), Draft(), Sections(), Figures(), Additions(), Normalize(), TitleBlock(),
    ChecksPlan(), MinutesRepair(), FormatFind(), Verify(), MinutesReground(), FormatAmend(),
    CareerPeriods(), PageLimit(), FormatStep(), Quality(),
):
    registry.register(_stage)


def sibling_note(bound: doc_formats.DocFormat, asked: doc_formats.DocFormat) -> str:
    """Writer note when the request asked for the bound format's sibling."""
    note = (
        f"# 작성 메모(내부)\nThis document is a {asked.label}, not a {bound.label}. Instructions "
        f"above written for a {bound.label} apply only where the two share a part; never turn "
        f"this document into a {bound.label} or name it one."
    )
    if asked.id == "agenda":
        note += (
            " An agenda is written before the meeting: no decisions or action items yet. "
            "Within the meeting length the request gives, propose a time for each item and "
            "mark the split as a proposal (「시간 배분(안)」). A presenter the material names "
            "goes beside the item; with none named, leave the presenter column out rather "
            "than fill it with 「미정」."
        )
    return note


def bundle_for(*, request: str, model: str, api_key: str, **kwargs: Any) -> Bundle:
    """Report bundle from `report.write`'s arguments, format and genre settled."""
    from app.pipeline.bundle import Request, Spec

    report = _report()
    req = Request(text=request, model=model, api_key=api_key,
                  trusted=list(kwargs.pop("trusted_context", None) or []),
                  untrusted=list(kwargs.pop("untrusted_context", None) or []), **kwargs)
    # explicit 서식 > agent's bound format > instruction words (first sentence first)
    doc_format = doc_formats.detect(lead_instruction(request), req.template_id, req.format_id)
    if doc_format is None:
        doc_format = doc_formats.detect(instruction_part(request), req.template_id, req.format_id)
    format_id = doc_format.id if doc_format is not None else ""
    bound = doc_formats.TEMPLATE_FORMATS.get(req.template_id or "")
    if bound and format_id and bound != format_id:
        # the request asked for the bound format's sibling (안건 under 회의록): that format's
        # own 서식 governs its settings, questions and head
        own_template = design_templates.for_format(format_id)
        req.template_id = own_template.id if own_template is not None else None
        # the agent's skill was written for the bound format; its rules for that one's parts
        # (회의록's 「미정」 owners and 확인 필요 line) would empty the asked document's cells
        req.trusted = [*req.trusted, sibling_note(doc_formats.BY_ID[bound], doc_format)]
    # 서식 settings, then the agent's own; repair stages of one format stay on it
    process = process_settings.only_for_format(
        process_settings.resolve(template_id=req.template_id, format_id=format_id,
                                 own=req.process), format_id)
    governing = process_settings.governing_template(req.template_id, format_id)
    if governing is not None and governing.check_options:
        # 서식 strictness joins the agent's
        req.check_options = {**{k: True for k in governing.check_options},
                             **dict(req.check_options or {})}
    spec = Spec(format=doc_format, format_id=format_id,
                genre=report._genre_rule(request, doc_format, req.template_id),
                process=process)
    # outline tokens apart: planning may use another model
    usage = {"inputTokens": 0, "outputTokens": 0, "outlineInputTokens": 0,
             "outlineOutputTokens": 0}
    return Bundle(request=req, spec=spec, usage=usage)


__all__ = ["bundle_for"]
