"""The work bundle a pipeline's stages read and write.

One bundle per turn. Its parts are typed and narrow so a stage names exactly what it reads
and writes (`Stage.reads` / `Stage.writes`, dotted: `computed.settled`):

- `request` — what the turn was asked, as the caller gave it; never written by a stage;
- `spec` — the document's format and genre, settled once from the request;
- `material` — the parts the writer reads, each with where it came from (`Part.origin`);
- `answers` — what the person answered on a question card, by question id;
- `computed` — what code worked out before writing: computed values, settled figures and
  their conflicts, the format's pre-pass;
- `plan` — the outline: title, headings and how the document is framed;
- `document` — the sections as written, the head block;
- `findings` — what the checks found before and after their repair, the quality record;
- `files` — files handed out (exports);
- `steps` — one `StepRecord` per stage, written by the runner;
- `usage` — tokens spent, by the writer and by the planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.pipeline.stage import StepRecord


@dataclass(slots=True)
class Part:
    """One piece of material, with where it came from.

    origin: `attachment` (a file or carried context), `pasted` (under the instruction),
    `project` (project sources' citation list), `research` (search pages), `computed`
    (code-computed values), `settled` (the settled figures block), `conflicts`, `prepass`
    (a pre-pass block), `note` (a writer's note), `trusted` (the caller's trusted context)."""

    text: str
    origin: str
    label: str = ""


@dataclass(slots=True)
class Request:
    text: str
    model: str
    api_key: str
    outline_model: str = ""
    trusted: list[str] = field(default_factory=list)
    untrusted: list[str] = field(default_factory=list)
    approved_plan: dict[str, Any] | None = None
    may_ask: bool = True
    figures_plan: list[dict] | None = None
    image_model: dict | None = None
    web_search: bool = True
    project_sources: list[dict[str, Any]] | None = None
    template_id: str | None = None
    format_id: str | None = None
    design_tokens: dict[str, str] | None = None
    check_keys: tuple[str, ...] | None = None
    agent_key: str | None = None
    session_id: str = ""
    #: The day the turn runs on, where a check needs it.
    today: Any = None
    # A chat turn's finish reads these too.
    #: The person's message as stored (not the merged reference envelope).
    first_message: str = ""
    messages: list[dict] = field(default_factory=list)
    #: The catalogue entry of the writer, and the model that actually answered.
    model_info: dict | None = None
    actual_model: str | None = None
    #: The fact set that sets how strict the trace is (`check_skills.fact_set`).
    check_facts: str = ""
    #: Attached and carried files' text: what the answer's values may come from too.
    check_material: str = ""
    #: What the turn's tools printed.
    tool_outputs: list[str] = field(default_factory=list)
    #: Whether this turn writes the first draft (the `pre` checks ask only then).
    first_draft: bool = False
    # A revision reads these.
    #: The follow-up as typed.
    instruction: str = ""
    is_deck: bool = False
    # Handing over files reads these.
    user_id: str = ""
    export_formats: list[str] = field(default_factory=list)

    @property
    def judge(self) -> str:
        """The planner when one is set, else the writer: judgements, not prose."""
        return self.outline_model or self.model


@dataclass(slots=True)
class Spec:
    #: `doc_formats.DocFormat` or None.
    format: Any = None
    format_id: str = ""
    genre: str = ""


@dataclass(slots=True)
class Material:
    #: The caller's trusted context, with what stages add to it.
    trusted: list[Part] = field(default_factory=list)
    #: What the writer reads as reference, in the order it reads it.
    parts: list[Part] = field(default_factory=list)
    #: The request's own material and the attachments, as one text.
    given: str = ""
    #: `research.Findings` of this pass.
    research: Any = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    web_selected: int = 0
    web_search: bool = True
    research_rule: str = ""
    #: Whether the material carries any numbers.
    grounded: bool = False

    @property
    def document(self) -> list[str]:
        return [p.text for p in self.parts]

    @property
    def trusted_texts(self) -> list[str]:
        return [p.text for p in self.trusted]


@dataclass(slots=True)
class Computed:
    #: The code-computed values block (`calc.computed_values`); "" when none.
    values: str = ""
    #: `key_figures.Figure` list, the derived ones included.
    settled: list[Any] = field(default_factory=list)
    conflicts: list[Any] = field(default_factory=list)
    #: `report_checks.Prepass` or None.
    prepass: Any = None


@dataclass(slots=True)
class Plan:
    title: str = ""
    headings: list[str] = field(default_factory=list)
    frame: bool = False
    unverified: bool = False
    #: The approved pictures by section index.
    figures: dict[int, dict] = field(default_factory=dict)
    #: The planning pass's offer, before approval.
    proposal: dict[str, Any] | None = None
    #: A revision: `revise.Plan`, the parts it rewrites in order, and those widened to.
    revision: Any = None
    order: list[int] = field(default_factory=list)
    widened: list[int] = field(default_factory=list)


@dataclass(slots=True)
class Document:
    sections: list[dict[str, Any]] = field(default_factory=list)
    #: Bodies cut from the one-shot draft, by heading.
    drafted: dict[str, str] = field(default_factory=dict)
    #: `## heading\nbody` of each section in order, as the next one is told.
    written: list[str] = field(default_factory=list)
    long_form: bool = False
    head_block: dict | None = None
    #: Chat: the answer as streamed, and as finished.
    streamed: str = ""
    answer: str = ""
    #: Chat: the step rows kept with the stored answer.
    kept_steps: list[dict] = field(default_factory=list)
    #: Chat: the length check stored on the answer's routing.
    length_check: dict | None = None
    #: A revision: the artifact's data as loaded, the parts changed, and whether by hand.
    data: dict[str, Any] = field(default_factory=dict)
    changed: int = 0
    by_hand: bool = False
    #: Handing over: the stored artifact, the step with the files made, what the reply says.
    artifact: Any = None
    handed: dict | None = None
    reply: str = ""


@dataclass(slots=True)
class Findings:
    #: The verify stage's checks, in order.
    checks: list[Any] = field(default_factory=list)
    #: The format's own code checks (found before the repair, then repaired).
    format_checks: list[Any] = field(default_factory=list)
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    added_before: list[str] = field(default_factory=list)
    added_after: list[str] = field(default_factory=list)
    #: `verify.Finding`s the repair left.
    still: list[Any] = field(default_factory=list)
    #: `check_skills.Outcome` of the grounding checks.
    traced: Any = None
    pages_note: str = ""
    client_problem: str = ""
    head_fixed: dict | None = None
    #: The check skills attached (post stage).
    check_keys: tuple[str, ...] = ()
    #: The quality gate's findings.
    quality: list[dict] = field(default_factory=list)
    #: What the `pre` checks found the material does not give.
    gaps: list[dict] = field(default_factory=list)
    #: A revision's consistency step (`consistency.align`), when it ran.
    aligned: dict | None = None


@dataclass(slots=True)
class Bundle:
    request: Request
    spec: Spec = field(default_factory=Spec)
    material: Material = field(default_factory=Material)
    answers: dict[str, str] = field(default_factory=dict)
    computed: Computed = field(default_factory=Computed)
    plan: Plan = field(default_factory=Plan)
    document: Document = field(default_factory=Document)
    findings: Findings = field(default_factory=Findings)
    files: list[dict[str, Any]] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)

    #: Fields the runner's strict mode watches.
    FIELDS = (
        "spec.format_id", "spec.genre",
        "material.trusted", "material.parts", "material.given", "material.sources",
        "material.web_selected", "material.web_search", "material.research_rule",
        "material.grounded",
        "answers",
        "computed.values", "computed.settled", "computed.conflicts", "computed.prepass",
        "plan.title", "plan.headings", "plan.frame", "plan.unverified", "plan.figures",
        "plan.proposal", "plan.revision", "plan.order", "plan.widened",
        "document.sections", "document.drafted", "document.written", "document.long_form",
        "document.head_block", "document.streamed", "document.answer", "document.kept_steps",
        "document.length_check", "document.data", "document.changed", "document.by_hand",
        "document.handed", "document.reply",
        "findings.checks", "findings.format_checks", "findings.before", "findings.after",
        "findings.added_before", "findings.added_after", "findings.still", "findings.traced",
        "findings.pages_note", "findings.client_problem", "findings.head_fixed",
        "findings.check_keys", "findings.quality", "findings.gaps", "findings.aligned",
        "files",
    )

    @property
    def records(self) -> list[dict[str, Any]]:
        return [r.as_dict() for r in self.steps]


__all__ = ["Bundle", "Computed", "Document", "Findings", "Material", "Part", "Plan",
           "Request", "Spec"]
