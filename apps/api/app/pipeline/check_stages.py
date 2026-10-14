"""Check skills as `check`-kind stages (`pre` gap checks, `post` grounding/copy/script checks)."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.pipeline import registry
from app.pipeline.bundle import Bundle
from app.pipeline.stage import Base, Context, Wait
from app.services import (
    check_skills,
    copy_check,
    gap_questions,
    key_figures,
    post_checks,
    skill_specs,
    units,
    value_guard,
)


def chat_evidence(bundle: Bundle, before: str) -> value_guard.Evidence:
    """What a chat answer may say: request, files, answers, tool and search results."""
    req = bundle.request
    asked = [str(m.get("content") or "") for m in req.messages if m.get("role") == "user"]
    return value_guard.Evidence.of(
        [bundle.material.given, req.check_material, *req.tool_outputs, *req.evidence, *asked],
        answer=before, request=req.text,
        today=req.today if isinstance(req.today, dt.date) else None)


class GapCheck(Base):
    """Missing facts from `pre` checks, asked on one card before the first draft only."""

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
        judged = None
        if gap_questions.needs_judge(req.gap_questions) \
                and check_skills.of_stage(tuple(req.check_keys or ()), "pre", req.skill_specs):
            from app.services import report

            judged = await gap_questions.judged(
                req.gap_questions, request=req.text, material=req.check_material,
                complete=report._complete, model=req.judge, api_key=req.api_key,
                usage=bundle.usage)
        bundle.findings.gaps = check_skills.gaps(
            tuple(req.check_keys or ()), questions=req.gap_questions, request=req.text,
            material=req.check_material, today=req.today, specs=req.skill_specs, judged=judged)


#: max questions per card
_CARD = 5


def card_questions(bundle: Bundle) -> list[dict]:
    """Pre-writing card: router file questions, then missing facts, minus last card's."""
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
    """Pre-draft question card; the run stops `waiting` for answers or 「있는 자료로 진행」."""

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
    material = "\n".join([bundle.request.text, bundle.material.given, *bundle.material.grounds])
    worked = [
        *(prepass.blocks if prepass is not None else []), key_figures.block(settled),
        bundle.computed.values or "", *bundle.request.tool_outputs,
    ]
    computed = [*worked, *(report._source_text(row) for row in (bundle.material.sources or []))]
    options = check_skills.options_for(
        dt.datetime.now(ZoneInfo(settings.timezone)).date(), bundle.request.check_options,
        surface="report")
    # person's own words/files apart from search pages: given days copied, not traced to a page
    options["person"] = "\n".join([bundle.request.text, bundle.material.given, *worked])
    # 「…을 지참·제출」 for an item the material never names, as in chat and decks
    options["requirements"] = True
    if bundle.request.strong:
        options["strong"] = True
    return material, computed, options


class GroundCheck(Base):
    """`post` grounding checks per section: check, fix once, check; unsourced values → blanks."""

    kind = "check"
    reads = ("request", "material", "computed", "spec.format_id", "findings.check_keys",
             "document.sections")
    writes = ("document.sections", "findings.traced", "findings.before", "findings.after")

    def __init__(self, name: str, *, again: bool) -> None:
        self.name, self.again = name, again

    def skip(self, bundle: Bundle) -> str:
        if not bundle.material.given.strip():
            return "no material to trace against"
        if not bundle.spec.format_id and not check_skills.of_stage(
                tuple(bundle.findings.check_keys), "post", bundle.request.skill_specs):
            return "no document format"
        return ""

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        found = bundle.findings
        material, computed, options = _trace(bundle)
        bundle.document.sections, outcome = check_skills.ground_sections(
            found.check_keys, bundle.document.sections, material=material, computed=computed,
            options=options, specs=bundle.request.skill_specs)
        found.before += ["ungrounded_value"] * len(outcome.found)
        if not self.again:
            found.traced = outcome
        else:
            found.traced.found += outcome.found
            found.traced.left = outcome.left
            found.after += ["ungrounded_value"] * len(outcome.left)



#: Check every chat turn on the person's material runs, agent or not.
TRACE = "grounding-check"


def answer_checks(req) -> tuple[str, ...]:
    """Attached `post` checks; on the person's material the grounding check too."""
    keys = tuple(req.check_keys or ())
    return (*keys, TRACE) if req.traced and TRACE not in keys else keys


#: an advice answer keeps up to this many 「(확인 필요)」 inline; more are listed once below
ADVICE_INLINE_MARKS = 3
_ADVICE_MARK = re.compile(r" ?" + re.escape(value_guard.MARK))


def _advice_note(text: str, before: str, found: list[dict]) -> str:
    """The marks taken out of the text and their values named once below. The writer's own
    marks go with the check's (r8 P19 L27F: a draft that already had one kept all of them
    inline, 18 in one answer)."""
    # a value the guard found supported after all lost its mark: not listed
    values = [str(f.get("text") or "").strip() for f in found
              if f.get("kind") == "consequence"
              or f"{str(f.get('text') or '').strip()}{value_guard.MARK}" in text]
    for m in _ADVICE_MARK.finditer(text):
        written = value_guard.BEFORE_MARK.search(text[max(0, m.start() - 40):m.start()])
        if written is not None:
            values.append(written.group(0).strip())
    values = [v for v in dict.fromkeys(values) if v][:6]
    body = _ADVICE_MARK.sub("", text).rstrip()
    shown = ", ".join(f"「{v[:30]}」" for v in values)
    return (f"{body}\n\n확인 필요: {shown} — 검색 결과나 주신 내용으로 확인하지 못했으니 "
            "공식 기관이나 전문가에게 확인하세요.")


def _advice_label(domain: str) -> str:
    from app.services import exact_facts

    return exact_facts.LABELS.get(domain, domain)


#: a source number an answer cites: 「[4]」, 「[[4]](https://…)」
_CITED = re.compile(r"\[\[?\d{1,3}\]")


def _cited_sentences(text: str) -> str:
    """The sentences of an earlier answer that cite a source the search found then: evidence
    for advice that repeats them (r8 P19: 3.7% cited [4] in the first turn was 「확인하지
    못했으니」 in the second)."""
    return "\n".join(sentence for line in text.splitlines()
                     for sentence in units.SENTENCE_BREAK.split(line) if _CITED.search(sentence))


class AnswerGrounding(Base):
    """Answer values traced to person's input or tool output; the rest → bracket blanks."""

    name = "check:answer-grounding"
    kind = "check"
    reads = ("request", "material.given", "document.answer")
    writes = ("document.answer", "document.kept_steps")

    def skip(self, bundle: Bundle) -> str:
        return "" if answer_checks(bundle.request) else "no material and no check skills"

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        req = bundle.request
        before = bundle.document.answer
        # advice on no material of the person's: an earlier answer is no evidence, and the
        # documents it tells them to bring are the guidance itself
        advice_only = bool(req.advice) and not req.traced
        # advice keeps its own reading on the person's material too: a pasted profile or file
        # does not make an exercise plan's sets and rests facts to source (r7 P07)
        traced = check_skills.ground(
            answer_checks(req), before,
            material=f"{bundle.material.given}\n{req.check_material}",
            computed=[*req.tool_outputs, *req.evidence,
                      *(str(m.get("content") or "") if not advice_only
                        else _cited_sentences(str(m.get("content") or ""))
                        for m in req.messages if m.get("role") == "assistant")],
            options={**check_skills.options_for(req.today, req.check_options, surface="chat"),
                     "person": "\n".join([bundle.material.given, req.check_material,
                                          *req.tool_outputs]),
                     "requirements": not advice_only,
                     **({"advice": True} if req.advice else {}),
                     **({"strong": True} if req.strong else {})},
            specs=req.skill_specs,
            # the person's earlier messages and the request count as evidence too
            evidence=[req.text, *(str(m.get("content") or "") for m in req.messages
                                  if m.get("role") == "user")],
        )
        if not traced.found:
            if req.advice:
                step = {"category": "thinking", "id": "grounding", "status": "done",
                        "label": f"근거 확인({_advice_label(req.advice)}): 확인 필요한 값 없음"}
                bundle.document.kept_steps = [row for row in bundle.document.kept_steps
                                              if row.get("id") != "grounding"] + [step]
                yield {"type": "step", **step}
            return
        bundle.document.answer = traced.text
        marked = traced.marked
        if req.advice:
            # advice values are marked where they stand, not blanked and settled back
            marked = max(marked, traced.text.count(value_guard.MARK)
                         - before.count(value_guard.MARK))
        if req.advice and traced.text.count(value_guard.MARK) > ADVICE_INLINE_MARKS:
            # many marks read as noise: listed once under the answer
            bundle.document.answer = _advice_note(traced.text, before, traced.found)
        ground_step = {
            "category": "thinking", "id": "grounding",
            "label": (f"근거 확인({_advice_label(req.advice)}): "
                      + (f"근거 없는 값 {marked}개 → 확인 필요" if marked
                         else "확인 필요한 값 없음")
                      if req.advice and traced.traced_only
                      else f"근거 없는 값 {marked}개 → 확인 필요" if traced.traced_only and marked
                      else "답 점검: 근거 확인됨" if traced.traced_only
                      else "답 점검: " + traced.summary[:120]),
            "status": "error" if traced.left and marked else "done",
            "detail": traced.summary[:1500],
        }
        bundle.document.kept_steps = [row for row in bundle.document.kept_steps
                                      if row.get("id") != "grounding"] + [ground_step]
        yield {"type": "step", **ground_step}


class CopyCheck(Base):
    """Copy-skill check (`services/copy_check.py`): code fixes, one writer rewrite, recheck."""

    name = "check:copy"
    kind = "check"
    reads = ("request", "material.given", "document.answer")
    writes = ("document.answer", "document.kept_steps")

    def skip(self, bundle: Bundle) -> str:
        req = bundle.request
        if not copy_check.active((*req.skill_keys, *(req.check_keys or ())), req.tool_outputs,
                                 req.skill_specs):
            return "no copy skill on this turn"
        return ""

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        from app.services import report

        req, doc = bundle.request, bundle.document
        yield {"type": "step", "category": "thinking", "id": "copy_check",
               "label": "카피 점검 중", "status": "running"}
        outcome = await copy_check.run(
            doc.answer, request=req.text,
            material="\n".join([bundle.material.given, req.check_material, *req.evidence]),
            complete=report._complete, model=req.model, judge=req.judge, api_key=req.api_key,
            usage=bundle.usage,
            skills=copy_check.on_turn((*req.skill_keys, *(req.check_keys or ())), req.tool_outputs,
                                      req.skill_specs),
            specs=req.skill_specs, strong=req.strong)
        settled = value_guard.settle(doc.answer, outcome.text, chat_evidence(bundle, doc.answer),
                                     writer=req.strong)
        doc.answer = settled.text
        still = [r for r in outcome.rows if r.get("hard") and (r["over"] or r["under"])]
        step = {
            "category": "thinking", "id": "copy_check",
            "label": (f"카피 점검: 근거 없는 표현 {len(outcome.found)}개"
                      + (f" · 한도 초과 {len(outcome.over)}개" if outcome.over else "")
                      if outcome.changed else "카피 점검: 고칠 것 없음"),
            "status": "error" if still else "done",
            **({"detail": " · ".join(x for x in (outcome.detail, settled.note()) if x)[:1500]}
               if outcome.detail or settled.changed else {}),
        }
        doc.kept_steps = [row for row in doc.kept_steps if row.get("id") != "copy_check"] + [step]
        yield {"type": "step", **step}


class SkillChecks(Base):
    """Turn skills' `post_check` scripts over the answer (`services/post_checks.py`)."""

    name = "check:skills"
    kind = "check"
    reads = ("request", "material.given", "document.answer")
    writes = ("document.answer", "document.kept_steps")

    def _keys(self, bundle: Bundle) -> list[str]:
        req = bundle.request
        # agent-attached `post` checks already ran in `check:answer-grounding`
        attached = [k for k in req.check_keys or ()
                    if not ((p := skill_specs.resolve(k, req.skill_specs)) and p.stage == "post")]
        return post_checks.on_turn((*req.skill_keys, *attached), req.tool_outputs,
                                   req.skill_specs)

    def skip(self, bundle: Bundle) -> str:
        return "" if self._keys(bundle) else "no post-checked skill on this turn"

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        from app.services import report

        req, doc = bundle.request, bundle.document
        yield {"type": "step", "category": "thinking", "id": "skill_check",
               "label": "스킬 점검 중", "status": "running"}
        today = req.today.isoformat() if hasattr(req.today, "isoformat") else str(req.today or "")
        outcome = await post_checks.run(
            doc.answer, keys=self._keys(bundle), request=req.text,
            material="\n".join(x for x in (req.text, bundle.material.given, req.check_material)
                               if x and x.strip()),
            attached=req.check_material, computed=[*req.tool_outputs, *req.evidence],
            today=today, complete=report._complete, model=req.model, judge=req.judge,
            api_key=req.api_key, usage=bundle.usage, specs=req.skill_specs, strong=req.strong)
        # what the judge had taken out stays out; a strong writer's revision stands as written
        settled = value_guard.settle(doc.answer, outcome.text, chat_evidence(bundle, doc.answer),
                                     keep_out=outcome.dropped, writer=req.strong)
        doc.answer = settled.text
        detail = " · ".join(x for x in (outcome.detail, settled.note()) if x)
        step = {"category": "thinking", "id": "skill_check", "label": outcome.label,
                "status": "error" if outcome.left else "done",
                **({"detail": detail[:1500]} if detail else {})}
        doc.kept_steps = [row for row in doc.kept_steps if row.get("id") != "skill_check"] + [step]
        yield {"type": "step", **step}


#: Slide fields holding words: a string, a list of strings, or rows of strings.
_SLIDE_TEXT = ("title", "subtitle", "body", "bullets", "left", "right", "cards", "steps", "bands",
               "tiles", "timeline", "rows", "metrics", "notes")


def _slide_leaves(slide: dict) -> list[tuple[tuple, str]]:
    """(path, text) of each word-bearing value on a slide."""
    out: list[tuple[tuple, str]] = []
    for key in _SLIDE_TEXT:
        value = slide.get(key)
        if isinstance(value, str):
            out.append(((key,), value))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, str):
                    out.append(((key, i), item))
                elif isinstance(item, list):
                    out += [((key, i, j), cell) for j, cell in enumerate(item)
                            if isinstance(cell, str)]
    return out


def ground_slides(slides: list[dict], *, material: str, computed: list[str],
                  options: dict) -> tuple[list[dict], list[dict]]:
    """Slides with unsourced values blanked (one trace over the deck); the findings."""
    module = check_skills.entry(TRACE)
    leaves = [(n, path, text) for n, slide in enumerate(slides)
              for path, text in _slide_leaves(slide)]
    lines: list[str] = []
    spans: list[tuple[int, int]] = []
    for _, _, text in leaves:
        parts = text.split("\n")
        spans.append((len(lines), len(parts)))
        lines += parts
    found = module.check("\n".join(lines), material, computed, options)
    if not found:
        return slides, []
    out = [dict(slide) for slide in slides]
    evidence = value_guard.Evidence.of([material, *computed])
    dropped: dict[tuple[int, str], set[int]] = {}
    for (n, path, text), (first, count) in zip(leaves, spans, strict=True):
        mine = [{**f, "line": f["line"] - first} for f in found
                if first <= f["line"] < first + count]
        if not mine:
            continue
        fixed = value_guard.settle(text, module.fix(text, mine), evidence).text
        slide, key = out[n], path[0]
        if len(path) == 1:
            # a title or body emptied whole keeps its words; its values are blanked in place
            slide[key] = fixed if fixed.strip() else text
        elif len(path) == 2:
            items = slide[key] = list(slide[key])
            items[path[1]] = fixed
            if not fixed.strip():
                dropped.setdefault((n, key), set()).add(path[1])
        else:
            rows = slide[key] = [list(row) if isinstance(row, list) else row for row in slide[key]]
            rows[path[1]][path[2]] = fixed
    for (n, key), gone in dropped.items():
        out[n][key] = [item for i, item in enumerate(out[n][key]) if i not in gone]
    return out, found


#: a figure in the person's words
_FIGURES = re.compile(r"(?<![\w.])\d[\d,]{0,15}(?:\.\d{1,6})?")


def _material(bundle: Bundle) -> str:
    """The request and the person's files, as the writer read them."""
    writer = bundle.request.turn.get("writer") or {}
    return "\n".join([bundle.request.text, *(writer.get("untrusted_context") or [])])


class DeckGrounding(Base):
    """Slide values traced to the person's material and code results; the rest → blanks."""

    name = "deck-ground"
    kind = "check"
    reads = ("request", "computed.values", "material.sources", "document.sections")
    writes = ("document.sections",)

    def skip(self, bundle: Bundle) -> str:
        turn = bundle.request.turn
        if not turn.get("traced"):
            if TRACE not in (turn.get("check_keys") or ()):
                return "no material of the person's on this turn"
            # the grounding skill on: the request's own figures are the material
            if len(_FIGURES.findall(_material(bundle))) < 4:
                return "no material to trace against"
        if not bundle.document.sections:
            return "no slides"
        return ""

    async def run(self, bundle: Bundle, ctx: Context) -> AsyncIterator[dict]:
        from app.services import report

        material = _material(bundle)
        today = dt.datetime.now(ZoneInfo(settings.timezone)).date()
        options = {**check_skills.options_for(today, surface="deck"), "person": material,
                   "requirements": True, **({"strong": True} if bundle.request.strong else {})}
        before = bundle.document.sections
        computed = [bundle.computed.values or "",
                    *(report._source_text(row) for row in bundle.material.sources or [])]
        slides, found = ground_slides(
            before, material=material, computed=computed, options=options)
        if not found:
            return
        bundle.document.sections = slides
        step = {"category": "thinking", "id": "grounding",
                "label": f"근거 없는 값 {len(found)}개 → 확인 필요", "status": "done",
                "detail": check_skills.entry(TRACE).summary(found)[:1500]}
        yield {"type": "step", **step}
        for old, new in zip(before, slides, strict=True):
            if old != new:
                yield {"type": "slide", "slide": new, "done": True}


for _stage in (GapCheck(), GapCard(), GroundCheck("check:grounding", again=False),
               GroundCheck("check:grounding-again", again=True), AnswerGrounding(), CopyCheck(),
               SkillChecks(), DeckGrounding()):
    registry.register(_stage)
