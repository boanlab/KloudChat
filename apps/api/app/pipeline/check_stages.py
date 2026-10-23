"""Check skills as `check`-kind stages (`pre` gap checks, `post` grounding/copy/script checks)."""

from __future__ import annotations

import datetime as dt
import logging
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
    output_language,
    post_checks,
    skill_specs,
    units,
    value_guard,
)
from app.services import deck as deck_rules

log = logging.getLogger(__name__)


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
    marks go with the check's (else a draft with one keeps all of them inline)."""
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
    for advice that repeats them in a later turn."""
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
        # does not make an exercise plan's sets and rests facts to source
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
        doc.answer = post_checks.cited_sources_listed(doc.answer, settled.text)
        detail = " · ".join(x for x in (outcome.detail, settled.note()) if x)
        step = {"category": "thinking", "id": "skill_check", "label": outcome.label,
                "status": "error" if outcome.left else "done",
                **({"detail": detail[:1500]} if detail else {})}
        doc.kept_steps = [row for row in doc.kept_steps if row.get("id") != "skill_check"] + [step]
        yield {"type": "step", **step}


#: Slide fields holding words: a string, a list of strings, or pairs of strings.
_SLIDE_TEXT = ("title", "subtitle", "body", "bullets", "left", "right", "cards", "steps", "bands",
               "tiles", "timeline", "items", "metrics", "notes")


def _slide_leaves(slide: dict) -> list[tuple[tuple, str]]:
    """(path, text) of each word-bearing value on a slide outside its table and chart."""
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
    for i, column in enumerate(slide.get("columns") or []):
        if not isinstance(column, dict):
            continue
        if isinstance(column.get("title"), str):
            out.append((("columns", i, "title"), column["title"]))
        out += [(("columns", i, "items", j), item)
                for j, item in enumerate(column.get("items") or []) if isinstance(item, str)]
    return out


def _value_text(value: object, unit: str) -> str:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)
    # a word unit stands apart: 「3.2 days」, 「82%」, 「128억원」
    apart = unit[:1].isascii() and unit[:1].isalpha()
    return f"{number:g} {unit}" if apart else f"{number:g}{unit}"


#: a measure named with its unit: 「Success Rate (%)」, 「매출(억원)」
_NAMED_UNIT = re.compile(r"[(（]\s{0,2}([^()（）]{1,8}?)\s{0,2}[)）]\s{0,2}$")


def _chart_unit(chart: dict) -> str:
    """The chart's unit for its values: the bracketed unit of a measure name, else the unit
    with its spaces closed (「억 원」 → 「억원」)."""
    unit = str(chart.get("unit") or "")
    named = _NAMED_UNIT.search(unit)
    return (named.group(1) if named else unit).replace(" ", "")


def _point_unit(unit: str, category: int, series: int, chart: dict) -> str:
    """A point's own unit under a chart's mixed one: 「억원/%」 over the categories 매출 and
    이익률 is 억원 for the first, % for the second (per series when the series count matches);
    「분·점」 over the series 「대기 시간(분)」 and 「만족도(점)」 is the one each names."""
    parts = [p for p in re.split(r"[/·]", unit) if p]
    if len(parts) < 2 or ("·" not in unit and not {"%", "%p"} & set(parts)):
        return unit
    categories = list(chart.get("categories") or [])
    series_list = [s for s in chart.get("series") or [] if isinstance(s, dict)]
    # the unit a series or category names: 「만족도(점)」
    for names, at in ((series_list, series), (categories, category)):
        name = names[at] if at < len(names) else ""
        name = str(name.get("name") or "") if isinstance(name, dict) else str(name)
        named = re.search(r"[(（]([^()（）]{1,4})[)）]\s{0,2}$", name)
        if named and named.group(1) in parts:
            return named.group(1)
    if len(parts) == len(categories):
        return parts[category]
    if len(parts) == len(series_list):
        return parts[series]
    return ""


def _slide_tables(slide: dict) -> list[tuple[str, list[list[str]]]]:
    """A slide's table rows and its chart as rows (「| 2025 3분기 | 7.8% |」 under the series
    names), so a value is traced with its row and column as in a report's table."""
    out = []
    rows = slide.get("rows")
    if isinstance(rows, list) and rows:
        out.append(("rows", [[str(c) for c in row] if isinstance(row, list) else [str(row)]
                             for row in rows]))
    chart = slide.get("chart")
    if isinstance(chart, dict) and chart.get("series"):
        unit = _chart_unit(chart)
        series = [s for s in chart.get("series") or [] if isinstance(s, dict)]
        table = [["구분", *(str(s.get("name") or "") for s in series)]]
        for c, category in enumerate(chart.get("categories") or []):
            table.append([str(category), *(
                _value_text(values[c], _point_unit(unit, c, k, chart))
                if c < len(values := list(s.get("values") or [])) else ""
                for k, s in enumerate(series))])
        out.append(("chart", table))
    return out


def _row_line(cells: list[str]) -> tuple[str, list[tuple[int, int]]]:
    """A markdown table row and each cell's span in it."""
    line, spans = "|", []
    for cell in cells:
        line += " "
        spans.append((len(line), len(line) + len(cell)))
        line += cell.replace("\n", " ") + " |"
    return line, spans


def _set(slide: dict, path: tuple, value: object) -> None:
    """`slide` with the value at `path` replaced, each list or dict on the way copied."""
    node: object = slide
    for key in path[:-1]:
        child = node[key]  # type: ignore[index]
        child = list(child) if isinstance(child, list) else dict(child)
        node[key] = child  # type: ignore[index]
        node = child
    node[path[-1]] = value  # type: ignore[index]


def _chart_fixed(slide: dict, gone: set[tuple[int, int]], strong: bool) -> None:
    """A chart plotting only traced values: a category holding an untraced value left out
    (a strong writer's kept, its label marked); a chart left with under two points becomes
    the values as bullets, an untraced one marked as `value_guard.settle` marks it."""
    chart = dict(slide["chart"])
    categories = list(chart.get("categories") or [])
    bad = {c for c, _ in gone}
    if strong:
        chart["categories"] = [f"{label} {value_guard.MARK}" if c in bad else label
                               for c, label in enumerate(categories)]
        slide["chart"] = chart
        return
    keep = [c for c in range(len(categories)) if c not in bad]
    series = [dict(s) for s in chart.get("series") or [] if isinstance(s, dict)]
    if len(keep) >= 2:
        chart["categories"] = [categories[c] for c in keep]
        for s in series:
            values = list(s.get("values") or [])
            s["values"] = [values[c] for c in keep if c < len(values)]
        chart["series"] = series
        slide["chart"] = chart
        return
    unit = _chart_unit(chart)
    bullets = []
    for c, label in enumerate(categories):
        for k, s in enumerate(series):
            values = list(s.get("values") or [])
            if c >= len(values):
                continue
            value = _value_text(values[c], _point_unit(unit, c, k, chart))
            if (c, k) in gone:
                value = f"{value} {value_guard.MARK}"
            bullets.append(f"{label} {s.get('name') or ''}: {value}".replace("  ", " "))
    slide.pop("chart", None)
    slide["layout"] = "bullets"
    slide["bullets"] = bullets[:5]


def trace_slides(slides: list[dict], *, material: str, computed: list[str],
                 options: dict) -> dict[int, list[str]]:
    """Per slide, the values no material, source or code result gives, as written; and a chart
    title naming a measure the chart does not plot."""
    _, found, points = _traced(slides, material=material, computed=computed, options=options)
    out: dict[int, list[str]] = {}
    for n, f in found:
        out.setdefault(n, []).append(str(f.get("text") or ""))
    for n, slide in enumerate(slides):
        if deck_rules.chart_title_mismatch(slide):
            out.setdefault(n, []).append(f"title 「{slide.get('title')}」 does not name what "
                                         "the chart plots")
    for n, slide in enumerate(slides):
        out_of_place = misplaced_cells(slide, "\n".join([material, *computed]))
        if out_of_place:
            out.setdefault(n, []).extend(out_of_place)
    for n, _ in points:
        out.setdefault(n, [])
    return out


_FOR_THAT_ROW = "put there only what the material gives for that row"
#: label words a value's place in the material is judged by
_LABEL_WORD = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")
#: material read around a value for its label
_LABEL_REACH = 60


def misplaced_cells(slide: dict, material: str) -> list[str]:
    """Table values the material states fewer times than the table writes them, in the rows
    whose label the material does not put beside the value (「Quality score 91%」 when 91% is
    given only for long documents)."""
    rows = slide.get("rows") or []
    return [f"「{rows[r][j]}」 in row 「{rows[r][0]}」 is the material's figure for another "
            f"item; {_FOR_THAT_ROW}" for r, j in _misplaced(slide, material)]


def _misplaced(slide: dict, material: str) -> list[tuple[int, int]]:
    """`misplaced_cells` as (row, column) places."""
    rows = slide.get("rows")
    if not isinstance(rows, list) or len(rows) < 3:
        return []
    at: dict[tuple[float, str], list[tuple[int, int]]] = {}
    for r, row in enumerate(rows[1:], start=1):
        if not isinstance(row, list) or len(row) < 2:
            continue
        for j, cell in enumerate(row[1:], start=1):
            mine = _figures_of(str(cell))
            if len(mine) == 1:
                at.setdefault(mine[0], []).append((r, j))
    if not any(len({r for r, _ in uses}) > 1 for uses in at.values()):
        return []
    spots: dict[tuple[float, str], list[str]] = {}
    for m in value_guard._NUMBER.finditer(material or ""):
        for key in _figures_of(m.group(0)):
            spots.setdefault(key, []).append(
                material[max(0, m.start() - _LABEL_REACH):m.end() + _LABEL_REACH].lower())
    out = []
    for key, uses in at.items():
        written = spots.get(key, [])
        if not written or len({r for r, _ in uses}) <= len(written):
            continue
        bound = {r: any(any(w.lower() in near for w in _LABEL_WORD.findall(str(rows[r][0])))
                        for near in written) for r, _ in uses}
        if any(bound.values()) and not all(bound.values()):
            out += [(r, j) for r, j in uses if not bound[r]]
    return out


def mark_misplaced(slides: list[dict], material: str) -> tuple[list[dict], int]:
    """`misplaced_cells` left after the hand-back, marked in place."""
    mark = value_guard.MARK
    out, count = [], 0
    for slide in slides:
        places = _misplaced(slide, material)
        if places:
            rows = [list(row) if isinstance(row, list) else row for row in slide["rows"]]
            for r, j in places:
                if mark not in str(rows[r][j]):
                    rows[r][j] = f"{rows[r][j]} {mark}"
                    count += 1
            slide = {**slide, "rows": rows}
        out.append(slide)
    return out, count


def _marked(cell: str) -> bool:
    return value_guard.MARK in cell or output_language.EN_MARK in cell


def mark_unbacked_percents(slides: list[dict], texts: list[str], *,
                           strong: bool = False) -> tuple[list[dict], int]:
    """Slides with each percentage `texts` neither give nor work out marked in its words and
    table cells, a chart's such point handled as an untraced one; the count marked."""
    basis = value_guard.percent_basis(texts)
    out, count = [], 0
    for slide in slides:
        slide = dict(slide)
        for path, text in _slide_leaves(slide):
            fixed, n = value_guard.mark_unbacked_percents(text, basis)
            if n:
                _set(slide, path, fixed)
                count += n
        rows = slide.get("rows")
        if isinstance(rows, list):
            for r, row in enumerate(rows):
                for j, cell in enumerate(row if isinstance(row, list) else []):
                    line = " | ".join(str(c) for c in row)
                    for m, value, unit in value_guard.percents_in(str(cell)):
                        about = bool(value_guard.ABOUT.search(str(cell)[:m.start()]))
                        if not _marked(str(cell)) and not basis.backs(
                                value, m.group("num"), line, unit, about):
                            _set(slide, ("rows", r, j), f"{cell} {value_guard.MARK}")
                            count += 1
                            break
        chart = slide.get("chart")
        if isinstance(chart, dict) and "%" in str(chart.get("unit") or ""):
            gone = set()
            for k, series in enumerate(chart.get("series") or []):
                for c, value in enumerate(series.get("values") or []
                                          if isinstance(series, dict) else []):
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        continue
                    unit = _point_unit(_chart_unit(chart), c, k, chart)
                    if "%" in unit and not basis.backs(number, f"{number:g}"):
                        gone.add((c, k))
            if gone:
                _chart_fixed(slide, gone, strong)
                count += len(gone)
        out.append(slide)
    return out, count


def edited_percents(slides: list[dict], edited: list[int], *, request: str,
                    material: list[str], before: str, strong: bool = False) -> list[int]:
    """The edited slides with percentages marked that neither the deck before the edit nor the
    person's material gives or works out, in place; the slides changed. A deck made from no
    figures of the person's is not checked, as when it was made."""
    if _given_figures(request) < _GIVEN_DATA and not any(t.strip() for t in material):
        return []
    texts = [request, *material, before]
    changed = []
    for n in edited:
        if not 0 <= n < len(slides):
            continue
        marked, count = mark_unbacked_percents([slides[n]], texts, strong=strong)
        if count:
            slide = marked[0]
            slides[n] = (output_language.english_marks(slide)
                         if deck_rules.deck_language(slides) == "en" else slide)
            changed.append(n)
    return changed


def _traced(slides: list[dict], *, material: str, computed: list[str], options: dict):
    """The trace over a deck: the check's findings with each one's place, untraced chart
    points by slide."""
    module = check_skills.entry(TRACE)
    places: list[tuple] = []
    lines: list[str] = []
    for n, slide in enumerate(slides):
        for path, text in _slide_leaves(slide):
            parts = text.split("\n")
            places.append(("leaf", n, path, text, len(lines), len(parts)))
            lines += parts
        for key, table in _slide_tables(slide):
            for r, cells in enumerate(table):
                line, spans = _row_line(cells)
                places.append(("cell", n, (key, r), spans, len(lines), 1))
                lines.append(line)
    found = module.check("\n".join(lines), material, computed, options) if lines else []
    located = []
    texts: list[tuple[int, dict]] = []
    points: set[tuple[int, tuple[int, int]]] = set()
    for place in places:
        first, count = place[4], place[5]
        mine = [{**f, "line": f["line"] - first} for f in found
                if first <= f["line"] < first + count]
        if not mine:
            continue
        kind, n = place[0], place[1]
        if kind == "leaf":
            located.append((place, mine))
            texts += [(n, f) for f in mine]
            continue
        (key, r), spans = place[2], place[3]
        for f in mine:
            j = next((j for j, (a, b) in enumerate(spans) if a <= int(f["start"]) < b), None)
            if j is None:
                continue
            if key == "chart":
                # labels and series names are not plotted values
                if r and j:
                    points.add((n, (r - 1, j - 1)))
                    texts.append((n, f))
                continue
            a, _ = spans[j]
            located.append((("cell", n, ("rows", r, j), slides[n]["rows"][r][j], 0, 1),
                            [{**f, "line": 0, "start": int(f["start"]) - a,
                              "end": int(f["end"]) - a}]))
            texts.append((n, f))
    given = _figures_of("\n".join([material, *computed]))
    flat = re.sub(r"\s+", "", "\n".join([material, *computed]))
    for n, slide in enumerate(slides):
        for key, table in _slide_tables(slide):
            if key != "chart":
                continue
            for r, cells in enumerate(table[1:], start=1):
                for j, cell in enumerate(cells[1:], start=1):
                    if (n, (r - 1, j - 1)) in points or not cell or _stated(cell, given) \
                            or _bound(cells[0], flat):
                        continue
                    # worked out of figures for another period or item: not this point's value
                    points.add((n, (r - 1, j - 1)))
                    texts.append((n, {"kind": "number", "text": cell, "line": 0, "start": 0,
                                      "end": len(cell), "placeholder": "[수치]"}))
    return located, texts, points


def _figures_of(text: str) -> list[tuple[float, str]]:
    """Every figure `text` writes with its unit family (「7.5%」 → (7.5, "%"))."""
    out = []
    for m in value_guard._NUMBER.finditer(text or ""):
        try:
            value = value_guard.ko_number(m.group("num"))
        except ValueError:
            continue
        unit = m.group("unit") or ""
        out.append((value, {"퍼센트": "%"}.get(unit, unit)))
    return out


def _stated(cell: str, given: list[tuple[float, str]]) -> bool:
    """Whether a chart value is written in the material or a code result as it stands."""
    mine = _figures_of(cell)
    if not mine:
        return False
    value, family = mine[0]
    raw = value_guard._NUMBER.search(cell).group("num")  # type: ignore[union-attr]
    places = len(raw.split(".", 1)[1].rstrip("조억만천 ")) if "." in raw else 0
    tol = abs(value) * 1e-9 + 0.5 * 10 ** -places
    return any(abs(k - value) <= tol and (not family or not f or f == family)
               for k, f in given)


def _bound(label: str, flat: str) -> bool:
    """Whether every name and year of a chart category is in the material (「수도권」, 「2026」)."""
    words = re.findall(r"[가-힣A-Za-z]{2,}|\d{4}", label)
    return bool(words) and all(w in flat for w in words)


def ground_slides(slides: list[dict], *, material: str, computed: list[str],
                  options: dict) -> tuple[list[dict], list[dict]]:
    """Slides with unsourced values marked (one trace over the deck), charts plotting only
    traced values, a chart's title naming what it plots; the findings."""
    module = check_skills.entry(TRACE)
    located, texts, points = _traced(slides, material=material, computed=computed,
                                     options=options)
    strong = bool(options.get("strong"))
    retitle = [n for n, slide in enumerate(slides) if deck_rules.chart_title_mismatch(slide)]
    if not (located or points or retitle):
        return slides, []
    out = [dict(slide) for slide in slides]
    evidence = value_guard.Evidence.of([material, *computed])
    dropped: dict[tuple[int, str], set[int]] = {}
    for place, mine in located:
        _, n, path, text = place[:4]
        fixed = value_guard.settle(text, module.fix(text, mine), evidence).text
        if len(path) == 1:
            # a title or body emptied whole keeps its words; its values are blanked in place
            _set(out[n], path, fixed if fixed.strip() else text)
        elif not fixed.strip() and isinstance(path[-1], int) and len(path) in (2, 4):
            dropped.setdefault((n, path[:-1]), set()).add(path[-1])
        else:
            _set(out[n], path, fixed)
    for (n, parent), gone in dropped.items():
        node: object = out[n]
        for key in parent:
            node = node[key]  # type: ignore[index]
        _set(out[n], parent, [item for i, item in enumerate(node)  # type: ignore[arg-type]
                              if i not in gone])
    for n in sorted({n for n, _ in points}):
        _chart_fixed(out[n], {p for m, p in points if m == n}, strong)
    for n in retitle:
        if not strong and "chart" in out[n]:
            # a weak writer's chart titled by what it plots; a strong writer's title stands
            out[n]["title"] = deck_rules.plotted_title(out[n])
    findings = [f for _, f in texts]
    findings += [{"kind": "claim", "text": str(slides[n].get("title") or ""),
                  "placeholder": ""} for n in retitle]
    return out, findings


#: a figure in the person's words
_FIGURES = re.compile(r"(?<![\w.])\d[\d,]{0,15}(?:\.\d{1,6})?")


def _material(bundle: Bundle) -> str:
    """The request and the person's files, as the writer read them."""
    writer = bundle.request.turn.get("writer") or {}
    return "\n".join([bundle.request.text, *(writer.get("untrusted_context") or [])])


class DeckGrounding(Base):
    """Slide values traced to the person's material, sources and code results: the slides
    holding others go back to the writer once with the list, what still has none is marked,
    and a chart plots only traced values under a title naming them."""

    name = "deck-ground"
    kind = "check"
    reads = ("request", "computed.values", "material.sources", "document.sections")
    writes = ("document.sections",)

    def skip(self, bundle: Bundle) -> str:
        turn = bundle.request.turn
        if not turn.get("traced") and not _no_invented_figures(bundle):
            given = _given_figures(bundle.request.text)
            if TRACE not in (turn.get("check_keys") or ()) and given < _GIVEN_DATA:
                return "no material of the person's on this turn"
            # the grounding skill on, or figures given in the request: they are the material
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
                   "requirements": True, **({"strong": True} if bundle.request.strong else {}),
                   **({"estimates": False} if _no_invented_figures(bundle) else {})}
        before = bundle.document.sections
        computed = [bundle.computed.values or "",
                    *(report._source_text(row) for row in bundle.material.sources or [])]
        handed = await _hand_back(bundle, material=material, computed=computed, options=options)
        for slide in handed:
            yield {"type": "slide", "slide": slide, "done": True}
        current = bundle.document.sections
        slides, found = ground_slides(
            current, material=material, computed=computed, options=options)
        slides, placed = mark_misplaced(slides, "\n".join([material, *computed]))
        # a percentage the material neither gives nor works out (「82% of frontier」 → cloud
        # 「100%」): marked, whatever the trace's arithmetic found for it
        slides, unbacked = mark_unbacked_percents(slides, [material, *computed],
                                                  strong=bool(bundle.request.strong))
        placed += unbacked
        if deck_rules.deck_language(slides) == "en":
            # an English deck's marks in English
            slides = output_language.english_marks(slides)
        if not found and not handed and not placed:
            return
        bundle.document.sections = slides
        step = {"category": "thinking", "id": "grounding", "status": "done",
                "label": (f"근거 없는 값 {len(found) + placed}개 → 확인 필요" if found or placed
                          else "근거 없는 값 → 다시 써서 고침"),
                "detail": " · ".join(x for x in (
                    f"다시 쓴 장 {len(handed)}개" if handed else "",
                    check_skills.entry(TRACE).summary(found)) if x)[:1500]}
        yield {"type": "step", **step}
        for old, new in zip(before, slides, strict=True):
            if old != new and new not in handed:
                yield {"type": "slide", "slide": new, "done": True}


#: slides sent back to the writer at most
_HAND_BACK = 4
#: a request giving its own data: this many figures with a measure (「128억」, 「7.5%」, 「3곳」)
_GIVEN_DATA = 4
_GIVEN_FIGURE = re.compile(
    r"(?<![\w.])\d[\d,]{0,15}(?:\.\d{1,6})?\s?(?:%|퍼센트|%p|조|억|만|천|원|달러|명|건|곳|대|개|배|점"
    r"|톤|일|개월|ms|초|s|pages?|tasks?|reviewers?|documents?|docs|users?|days?|hours?|months?)"
    r"(?![가-힣A-Za-z])|(?<![\w.])\$\s?\d[\d,]{0,15}(?:\.\d{1,6})?")
#: the person or their agent forbids figures the material does not hold
_NO_INVENTION = re.compile(
    r"근거\s?없는\s?(?:수치|숫자|값)|(?:자료|근거)에\s?없는\s?(?:수치|숫자|값)"
    r"|(?:수치|숫자)(?:를|는)?\s?(?:지어|만들어\s?내|임의로|추정하지)|(?:세부\s?)?수치\s?(?:는\s?)?없(?:음|다|어|습)"
    r"|no\s+(?:made[- ]up|invented|unsourced)\s+(?:figures|numbers)", re.I)


def _given_figures(request: str) -> int:
    """Distinct figures with a measure the request writes."""
    return len({re.sub(r"\s", "", m.group(0)) for m in _GIVEN_FIGURE.finditer(request or "")})


def _no_invented_figures(bundle: Bundle) -> bool:
    """Whether the request or the agent's own instructions forbid figures with no basis."""
    writer = bundle.request.turn.get("writer") or {}
    agent = [t for t in writer.get("trusted_context") or [] if str(t).startswith("# 역할")]
    return any(_NO_INVENTION.search(str(t)) for t in [bundle.request.text, *agent])


async def _hand_back(bundle: Bundle, *, material: str, computed: list[str],
                     options: dict) -> list[dict]:
    """Slides holding untraced values rewritten once by the writer, the values listed; a
    rewrite kept only when it holds fewer of them. The kept rewrites."""
    req, turn = bundle.request, bundle.request.turn
    if not turn.get("writer") or not req.model:
        return []
    slides = bundle.document.sections
    try:
        pending = trace_slides(slides, material=material, computed=computed, options=options)
    except Exception:  # noqa: BLE001 — the code marks below
        log.exception("deck trace before hand-back failed")
        return []
    worst = sorted(pending, key=lambda n: -len(pending[n]))[:_HAND_BACK]
    kept: list[dict] = []
    for n in worst:
        slide = slides[n]
        placed = [v for v in pending[n] if v.endswith(_FOR_THAT_ROW)]
        listed = ", ".join(f"「{v[:60]}」" for v in pending[n] if v not in placed) or (
            "" if placed else "the chart's values")
        said = [f"These on this slide are in none of the material, the sources or the code's "
                f"results: {listed}."] if listed else []
        note = " ".join([*said, *(f"{v}." for v in placed)]) + (
            " Write the slide again with only figures the material gives "
            "or works out from its numbers. Leave a figure out, or write the item without "
            "it, rather than guess; a chart plots only given values, and its title names "
            "what the chart plots.")
        try:
            written, spent = await deck_rules.rewrite_slide(
                request=req.text, slides=slides, target_id=str(slide.get("id") or ""),
                model=req.model, api_key=req.api_key, note=note,
                material=[material, *[c for c in computed if c]], keep_layout=True)
        except Exception as exc:  # noqa: BLE001 — the slide stays; the code marks it
            log.info("hand-back of slide %d failed: %s", n, exc)
            continue
        bundle.usage["inputTokens"] = bundle.usage.get("inputTokens", 0) + int(
            spent.get("inputTokens", 0))
        bundle.usage["outputTokens"] = bundle.usage.get("outputTokens", 0) + int(
            spent.get("outputTokens", 0))
        trial = [*slides[:n], written, *slides[n + 1:]]
        left = trace_slides(trial, material=material, computed=computed,
                                options=options).get(n, [])
        if len(left) < len(pending[n]) and deck_rules.has_content(written):
            slides = trial
            kept.append(written)
    bundle.document.sections = slides
    return kept


for _stage in (GapCheck(), GapCard(), GroundCheck("check:grounding", again=False),
               GroundCheck("check:grounding-again", again=True), AnswerGrounding(), CopyCheck(),
               SkillChecks(), DeckGrounding()):
    registry.register(_stage)
