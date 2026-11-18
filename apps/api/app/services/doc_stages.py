"""Staged model routing of a report or a deck: no one model writes the whole document.

The `document` row's model (the self-hosted 27B by default) writes the ordinary sections and
slides. Three stages go to the upper model of the Super Agent table (`document_plan`,
`document_hard`, `document_repair`; strict-local in the strict column):

- plan: the outline of a report, the storyline of a deck (and its slide-count retries);
- hard: the sections or slides the objective signals below mark as hard;
- repair: only what the document's existing checks (error text, an unfinished or empty
  section, table gaps, odd words, a language slip, an unwritten or empty slide, a slide
  count that differs from the request) send back to be written again.

A stage whose row names no usable model stays with the writer, and a call to the upper model
that fails is made again on the writer: a document turn never fails for a missing upper model.
Every call's tokens are kept per stage (`usage["stageUsage"]`), billed at their own model's
price, and shown under the answer as the turn's self-hosted share (`run_info`).
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings
from app.services import model_health, thinking, usage_fields

log = logging.getLogger(__name__)

#: The stages, in the order a document passes them.
STAGES: tuple[str, ...] = ("plan", "hard", "repair")

#: At most this share of a report's sections (or a deck's content slides) is marked hard: the
#: writer keeps the rest, so the upper model never writes a whole document either.
HARD_MAX_SHARE = 0.5  # the default of `settings.document_hard_share`
#: A source carries numbers worth tracing when this many distinct figures stand in it.
SOURCE_NUMBERS_MIN = 6

#: Headings of a section that analyses or compares (a subset of the words that already give a
#: section a table in `report._section_role`).
ANALYSIS_WORDS: tuple[str, ...] = ("비교", "분석", "대안", "옵션", "검토", "평가")
#: Reasons a section or slide is hard, strongest first (the order the cap keeps).
HARD_REASONS: tuple[str, ...] = ("summary", "conclusion", "analysis", "figures", "data",
                                 "body")

#: Deck layouts that carry numbers, and so are hard when the source has figures.
DATA_LAYOUTS = frozenset({"chart", "metrics", "big-number", "table"})
#: Deck layouts written without a model call (from the outline).
_STRUCTURAL = frozenset({"title", "section", "agenda", "closing", "divider"})

_FIGURE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


# --- which model --------------------------------------------------------------------------


@dataclass(slots=True)
class StageModels:
    """The models of one turn's stages: `ids` maps a stage to the model that runs it (a stage
    that stays with the writer is absent); `rows` holds those models' catalogue rows, for
    pricing and the data boundary. `down` collects models that failed this turn: later calls
    go straight to the writer."""

    ids: dict[str, str] = field(default_factory=dict)
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: the reasoning fields each stage's call is sent with (its row's effort, the chip steering
    #: it); `{}` leaves thinking off where it can be turned off
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    down: set[str] = field(default_factory=set)
    #: every stage call of the whole turn, as `usage` keeps them per pass: a plan written straight
    #: on (no card) is a pass of its own whose usage the writing pass never sees
    ledger: dict[str, Any] = field(default_factory=dict)

    def model(self, stage: str | None) -> str:
        """The upper model of `stage`, or "" for the writer."""
        found = self.ids.get(stage or "", "")
        return "" if found in self.down else found

    def wire(self) -> dict[str, str]:
        return dict(self.ids)

    def __bool__(self) -> bool:
        return bool(self.ids)


#: The running turn's stage models (set where the writer is picked, read by the report and
#: deck writers, like `thinking.TURN_REASONING`); None: nothing staged, the writer does it all.
CURRENT: ContextVar[StageModels | None] = ContextVar("doc_stage_models", default=None)


def from_ids(ids: dict[str, str] | None, catalogue: list[dict[str, Any]],
             *, strict: bool = False) -> StageModels:
    """The `StageModels` of a route's stage ids; in a strict turn only strict-local rows stay
    (defence in depth: the picks were already made in the strict column)."""
    by_id = {str(m.get("id")): m for m in catalogue}
    out = StageModels()
    for stage, model_id in (ids or {}).items():
        row = by_id.get(model_id)
        if stage not in STAGES or row is None:
            continue
        if strict and not (row.get("dataBoundary") == "self_hosted"
                           and row.get("strictLocal") is True):
            continue
        out.ids[stage] = model_id
        out.rows[model_id] = row
    return out


def self_hosted(model_id: str, rows: dict[str, dict[str, Any]] | None = None) -> bool:
    """Whether `model_id` runs on the house's own servers (`local/` first and the strict twin;
    a catalogue row that says so)."""
    row = (rows or {}).get(model_id)
    if row is not None and row.get("dataBoundary"):
        return row.get("dataBoundary") == "self_hosted"
    return model_id.startswith(("local/", "strict-local/"))


# --- tokens, price and the answer's line ---------------------------------------------------


def _both(usage: dict[str, Any]) -> list[dict[str, Any]]:
    """`usage` and the turn's ledger (when the turn has one and `usage` is not it)."""
    stages = CURRENT.get()
    return [usage] if stages is None or usage is stages.ledger else [usage, stages.ledger]


def whole_turn(usage: dict[str, Any]) -> dict[str, Any]:
    """`usage` with the stage tokens and triggers of the whole turn (every pass), in place."""
    stages = CURRENT.get()
    if stages is not None:
        for key in ("stageUsage", "stageTriggers"):
            if key in stages.ledger:
                usage[key] = stages.ledger[key]
    return usage


def add_usage(usage: dict[str, Any], stage: str, model: str, spent: dict[str, Any]) -> None:
    """One stage call's tokens on the upper model (never on the writer's own counts)."""
    for one in _both(usage):
        _add_usage(one, stage, model, spent)


def _add_usage(usage: dict[str, Any], stage: str, model: str, spent: dict[str, Any]) -> None:
    rows = usage.setdefault("stageUsage", {})
    row = rows.setdefault(stage, {"model": model, "inputTokens": 0, "outputTokens": 0,
                                  "calls": 0, "fallbacks": 0})
    row["inputTokens"] += int(spent.get("inputTokens", 0) or 0)
    row["outputTokens"] += int(spent.get("outputTokens", 0) or 0)
    usage_fields.add_cached(row, spent)
    row["calls"] += 1


def note_fallback(usage: dict[str, Any], stage: str, model: str) -> None:
    """The stage's upper model failed and the writer took the call."""
    for one in _both(usage):
        rows = one.setdefault("stageUsage", {})
        row = rows.setdefault(stage, {"model": model, "inputTokens": 0, "outputTokens": 0,
                                      "calls": 0, "fallbacks": 0})
        row["fallbacks"] += 1


def note_trigger(usage: dict[str, Any], stage: str, why: str) -> None:
    """A check sent something to `stage` (`hard`: a hard part; `repair`: a failed check)."""
    for one in _both(usage):
        row = one.setdefault("stageTriggers", {}).setdefault(stage, {})
        row[why] = int(row.get(why, 0)) + 1


def credits(usage: dict[str, Any], rows: dict[str, dict[str, Any]] | None,
            charge: Callable[[dict[str, Any], int, int], int]) -> int:
    """Credits of the upper models' calls, each at its own model's price. A stage whose row is
    unknown here costs nothing (the writer's own accounting never held its tokens)."""
    total = 0
    for entry in (usage.get("stageUsage") or {}).values():
        row = (rows or {}).get(str(entry.get("model") or ""))
        if row is not None:
            total += charge(row, int(entry.get("inputTokens", 0)),
                            int(entry.get("outputTokens", 0)))
    return total


def spent_models(usage: dict[str, Any], writer: str, outline: str,
                 rows: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, int]]:
    """Tokens of the turn by model: the writer's, the planner's (`outline*`) and each stage's."""
    out: dict[str, dict[str, int]] = {}

    def add(model: str, i: int, o: int) -> None:
        if not model or not (i or o):
            return
        row = out.setdefault(model, {"inputTokens": 0, "outputTokens": 0})
        row["inputTokens"] += int(i)
        row["outputTokens"] += int(o)

    add(writer, usage.get("inputTokens", 0), usage.get("outputTokens", 0))
    add(outline or writer, usage.get("outlineInputTokens", 0),
        usage.get("outlineOutputTokens", 0))
    for entry in (usage.get("stageUsage") or {}).values():
        add(str(entry.get("model") or ""), entry.get("inputTokens", 0),
            entry.get("outputTokens", 0))
    return out


def run_info(usage: dict[str, Any], writer: str, outline: str,
             rows: dict[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """`routing.runInfo` of a staged document turn: each stage's model and tokens, and the
    share of the turn's tokens that stayed on the house's own servers. None when no stage
    went up (nothing staged to show)."""
    stages = usage.get("stageUsage") or {}
    if not stages:
        return None
    rows = rows or {}
    by_model = spent_models(usage, writer, outline, rows)
    total = sum(r["inputTokens"] + r["outputTokens"] for r in by_model.values())
    out_total = sum(r["outputTokens"] for r in by_model.values())
    own = sum(r["inputTokens"] + r["outputTokens"] for m, r in by_model.items()
              if self_hosted(m, rows))
    own_out = sum(r["outputTokens"] for m, r in by_model.items() if self_hosted(m, rows))
    listed = [{"stage": stage, "model": str(e.get("model") or ""),
               "inputTokens": int(e.get("inputTokens", 0)),
               "outputTokens": int(e.get("outputTokens", 0)),
               "calls": int(e.get("calls", 0)), "fallbacks": int(e.get("fallbacks", 0))}
              for stage, e in stages.items()]
    return {
        "stages": listed,
        "triggers": dict(usage.get("stageTriggers") or {}),
        "selfHostedShare": round(own / total, 3) if total else None,
        "selfHostedOutputShare": round(own_out / out_total, 3) if out_total else None,
    }


def with_run_info(routing: dict[str, Any] | None, usage: dict[str, Any], writer: str,
                  outline: str) -> dict[str, Any] | None:
    """`routing` of the stored message with the staged turn's `runInfo` (each stage's model
    and tokens, the self-hosted share); `routing` itself when nothing went up."""
    stages = CURRENT.get()
    info = run_info(usage, writer, outline, stages.rows if stages is not None else None)
    if info is None:
        return routing
    routing = routing or {}
    return {**routing, "runInfo": {**(routing.get("runInfo") or {}), **info}}


# --- the call ---------------------------------------------------------------------------------


def _failed(exc: BaseException) -> bool:
    """A call the upper model did not answer or answered unusably; the writer takes it."""
    return isinstance(exc, (httpx.HTTPError, TimeoutError, KeyError, IndexError, ValueError))


async def staged(
    complete: Callable[[str, list[dict], str, int], Awaitable[tuple[str, dict]]],
    stage: str | None,
    writer: str,
    build: Callable[[str], list[dict]],
    api_key: str,
    max_tokens: int,
    usage: dict[str, Any],
) -> tuple[str, dict]:
    """`(text, spent)` of one call of `stage`. On the stage's upper model its tokens go to
    `usage["stageUsage"]` and `spent` comes back empty (the caller's own count adds nothing);
    when that model fails or answers nothing, the writer is asked in its place and its tokens
    are the caller's to count as usual. `build(model)` makes the messages for the model
    asked (the prompt setup follows the model's tier)."""
    stages = CURRENT.get()
    model = stages.model(stage) if stages is not None and stage else ""
    if stages is None or not stage or not model or model == writer:
        return await complete(writer, build(writer), api_key, max_tokens)
    # this stage's own reasoning level for the model, for this call only
    token = thinking.TURN_REASONING.set(
        {**(thinking.TURN_REASONING.get() or {}), model: stages.fields.get(stage) or {}})
    try:
        text, spent = await complete(model, build(model), api_key, max_tokens)
    except Exception as exc:  # noqa: BLE001 — only a failed call goes to the writer
        if not _failed(exc):
            raise
        error = str(exc)
        if isinstance(exc, httpx.HTTPStatusError):
            error = f"upstream_{exc.response.status_code} {exc.response.text[:200]}"
        if isinstance(exc, (httpx.HTTPStatusError, TimeoutError, httpx.TimeoutException)):
            model_health.mark_failed(model, error)
        stages.down.add(model)
        note_fallback(usage, stage, model)
        log.warning("%s stage on %s failed (%s: %s); %s writes it", stage, model,
                    type(exc).__name__, error[:120], writer)
    else:
        add_usage(usage, stage, model, spent)
        if (text or "").strip():
            return text, {"inputTokens": 0, "outputTokens": 0}
        # billed, but nothing to use: the writer is asked in its place
        note_fallback(usage, stage, model)
        log.warning("%s stage on %s answered nothing; %s writes it", stage, model, writer)
    finally:
        thinking.TURN_REASONING.reset(token)
    return await complete(writer, build(writer), api_key, max_tokens)


# --- what is hard -----------------------------------------------------------------------------


def _distinct_figures(text: str) -> int:
    return len({m.group(0).replace(",", "") for m in _FIGURE.finditer(text or "")
                if len(m.group(0).replace(",", "")) >= 2})


def source_has_figures(material: str) -> bool:
    """Whether the given material carries figures to trace: at least `SOURCE_NUMBERS_MIN`
    distinct numbers, or a table."""
    text = material or ""
    return _distinct_figures(text) >= SOURCE_NUMBERS_MIN or bool(
        re.search(r"(?m)^\s*\|.+\|\s*$", text))


def _cap(count: int) -> int:
    share = min(1.0, max(0.0, settings.document_hard_share))
    if share <= 0:
        return 0
    return max(1, min(count - 1, math.floor(count * share + 1e-9))) if count > 1 else 0


def hard_report_sections(headings: list[str], material: str) -> dict[int, str]:
    """Sections the upper model writes, by index with the reason (`HARD_REASONS`), read from
    their roles and the material alone: the summary that opens the document, the conclusion
    that closes it, sections that analyse or compare, and sections that carry figures
    (a comparison, results or trend role) when the material holds figures to trace. At most
    `HARD_MAX_SHARE` of the sections, strongest reason first."""
    from app.services import report

    total = len(headings)
    figures = source_has_figures(material)
    found: dict[int, str] = {}
    for index, heading in enumerate(headings):
        name = heading.lower()

        def has(words: tuple[str, ...], name: str = name) -> bool:
            return any(w in name for w in words)

        if has(report._SUMMARY_WORDS) and index == 0:
            found[index] = "summary"
        elif has(report._SUMMARY_WORDS) and index == total - 1:
            found[index] = "conclusion"
        elif has(ANALYSIS_WORDS):
            found[index] = "analysis"
        elif figures and (has(report._COMPARE_WORDS) or has(report._TREND_WORDS)):
            found[index] = "figures"
        elif settings.document_hard_share > HARD_MAX_SHARE:
            # a share above the default takes plain sections too, last
            found[index] = "body"
    order = sorted(found, key=lambda i: (HARD_REASONS.index(found[i]), i))
    keep = set(order[:_cap(total)])
    return {i: r for i, r in sorted(found.items()) if i in keep}


def hard_slides(slides: list[dict[str, Any]], material: str) -> dict[int, str]:
    """Slides the upper model writes, by index with the reason: data slides (a chart,
    metrics, a big number or a table) when the material holds figures, and the summary or
    decision slide (by its title's words). Structural slides take no model call. At most
    `HARD_MAX_SHARE` of the content slides."""
    from app.services import report

    figures = source_has_figures(material)
    content = [i for i, s in enumerate(slides) if str(s.get("layout")) not in _STRUCTURAL]
    found: dict[int, str] = {}
    for index in content:
        slide = slides[index]
        title = str(slide.get("title") or "").lower()
        if any(w in title for w in report._SUMMARY_WORDS):
            found[index] = "summary"
        elif figures and str(slide.get("layout")) in DATA_LAYOUTS:
            found[index] = "data"
        elif settings.document_hard_share > HARD_MAX_SHARE:
            found[index] = "body"
    order = sorted(found, key=lambda i: (HARD_REASONS.index(found[i]), i))
    keep = set(order[:_cap(len(content))])
    return {i: r for i, r in sorted(found.items()) if i in keep}
