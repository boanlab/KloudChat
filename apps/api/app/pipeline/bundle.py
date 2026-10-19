"""Per-turn work bundle; typed narrow parts so stages declare dotted reads/writes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.pipeline.stage import StepRecord


@dataclass(slots=True)
class Part:
    """Material piece with its origin; `derived` is never a figure source."""

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
    #: self-hosted image model: pictures planned and drawn like diagrams, no card
    auto_image_model: dict | None = None
    web_search: bool = True
    project_sources: list[dict[str, Any]] | None = None
    template_id: str | None = None
    format_id: str | None = None
    design_tokens: dict[str, str] | None = None
    check_keys: tuple[str, ...] | None = None
    #: `check_skills.options_for` trace strictness
    check_options: dict[str, Any] = field(default_factory=dict)
    #: agent's 진행 과정 설정 over its 서식's; None = 서식's
    process: dict[str, Any] | None = None
    agent_key: str | None = None
    session_id: str = ""
    today: Any = None
    #: stored message, not the merged reference envelope
    first_message: str = ""
    messages: list[dict] = field(default_factory=list)
    #: writer catalogue entry; model that actually answered
    model_info: dict | None = None
    actual_model: str | None = None
    #: attached/carried file text; also a value source
    check_material: str = ""
    #: chat turn on the person's material: its answer is traced even without check skills
    traced: bool = False
    #: legal, tax, financial or medical chat turn (`exact_facts.advice_domain`): its values,
    #: hotlines, dosages and legal consequences are traced to the search results
    advice: str = ""
    #: `red_flags.detect` kind of a chat turn: the answer opens with the emergency action
    urgent: str = ""
    #: tool prints (composed run: earlier step's code output)
    tool_outputs: list[str] = field(default_factory=list)
    #: web search results of the turn: evidence for the value guard, never number sources
    evidence: list[str] = field(default_factory=list)
    #: earlier composed step's text: writer reads, checks never take figures
    derived: list[str] = field(default_factory=list)
    #: turn skill keys: catalogue key or `skill:<id>`
    skill_keys: tuple[str, ...] = ()
    #: server-run spec per key (`services/skill_specs.py`); missing shipped key → its package
    skill_specs: dict[str, Any] = field(default_factory=dict)
    gap_questions: list[dict] = field(default_factory=list)
    #: first draft turn; `pre` checks ask only then
    first_draft: bool = False
    instruction: str = ""
    is_deck: bool = False
    user_id: str = ""
    export_formats: list[str] = field(default_factory=list)
    #: router-settled, stage-immutable turn facts (models, project_id, routing, carry_*, run_id …)
    turn: dict[str, Any] = field(default_factory=dict)

    @property
    def judge(self) -> str:
        """Planner if set, else writer: judgements, not prose."""
        return self.outline_model or self.model

    @property
    def strong(self) -> bool:
        """A strong writer (`model_tier.strong`): checks mark or hand evidence back to it, and
        no weaker model rewrites its text."""
        from app.services import model_tier

        # the model that actually wrote the answer when known (a privacy route's local twin
        # is a small writer)
        return model_tier.strong(self.actual_model or self.model)

    @property
    def group(self) -> str:
        """The writer's model group (`model_tier.group`), from the model that actually wrote."""
        from app.services import model_tier

        return model_tier.group(self.actual_model or self.model)

    @property
    def reviser(self) -> str:
        """Who rewrites the writer's text on the checks' findings: the writer itself when it is
        strong, else the planner."""
        return self.model if self.strong else self.judge


@dataclass(slots=True)
class Spec:
    format: Any = None
    format_id: str = ""
    genre: str = ""
    #: `process_settings.Process`; None = defaults
    process: Any = None

    @property
    def settings(self):
        from app.services import process_settings

        return self.process if self.process is not None else process_settings.DEFAULT


@dataclass(slots=True)
class Material:
    trusted: list[Part] = field(default_factory=list)
    #: writer reference, in read order
    parts: list[Part] = field(default_factory=list)
    #: request material + attachments as one text
    given: str = ""
    research: Any = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    web_selected: int = 0
    web_search: bool = True
    research_rule: str = ""
    #: material has numbers
    grounded: bool = False

    @property
    def document(self) -> list[str]:
        return [p.text for p in self.parts]

    @property
    def grounds(self) -> list[str]:
        """Check trace grounds: all parts except derived."""
        return [p.text for p in self.parts if p.origin != "derived"]

    @property
    def trusted_texts(self) -> list[str]:
        return [p.text for p in self.trusted]


@dataclass(slots=True)
class Computed:
    #: `calc.computed_values` block; "" when none
    values: str = ""
    #: `key_figures.Figure`s incl. derived
    settled: list[Any] = field(default_factory=list)
    conflicts: list[Any] = field(default_factory=list)
    prepass: Any = None
    #: how the code calculation ended (`calc.computed_values` status); "" when it did not run
    calc_status: str = ""


@dataclass(slots=True)
class Plan:
    title: str = ""
    headings: list[str] = field(default_factory=list)
    frame: bool = False
    unverified: bool = False
    #: approved figures by section index
    figures: dict[int, dict] = field(default_factory=dict)
    #: planning offer, pre-approval
    proposal: dict[str, Any] | None = None
    #: revision: `revise.Plan`, rewrite order, widened parts
    revision: Any = None
    order: list[int] = field(default_factory=list)
    widened: list[int] = field(default_factory=list)
    #: page · handover · revise · report · deck · chat
    route: str = ""


@dataclass(slots=True)
class Document:
    sections: list[dict[str, Any]] = field(default_factory=list)
    #: one-shot draft bodies by heading
    drafted: dict[str, str] = field(default_factory=dict)
    #: `## heading\nbody` per section, fed to the next
    written: list[str] = field(default_factory=list)
    long_form: bool = False
    head_block: dict | None = None
    #: chat: streamed and finished answer
    streamed: str = ""
    answer: str = ""
    #: chat: step rows stored with the answer
    kept_steps: list[dict] = field(default_factory=list)
    #: chat: length check on routing
    length_check: dict | None = None
    #: revision: loaded data, changed count, by hand
    data: dict[str, Any] = field(default_factory=dict)
    changed: int = 0
    by_hand: bool = False
    #: revision: asked table shapes found nowhere in the document after it
    missed: list[str] = field(default_factory=list)
    #: revision: parts whose rewrite failed the checks twice, kept as they were
    failed: list[str] = field(default_factory=list)
    #: revision: asked changes the new slides do not show (「3장 표」 still drawn as tiles)
    unmet: list[str] = field(default_factory=list)
    #: handover: artifact, files step, reply
    artifact: Any = None
    handed: dict | None = None
    reply: str = ""
    #: revision: `revise-load` output
    loaded: dict[str, Any] | None = None
    #: `{"artifactId", "credits", "messageId", "version"}`
    saved: dict[str, Any] | None = None


@dataclass(slots=True)
class Findings:
    checks: list[Any] = field(default_factory=list)
    #: format code checks (pre-repair, then repaired)
    format_checks: list[Any] = field(default_factory=list)
    before: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    added_before: list[str] = field(default_factory=list)
    added_after: list[str] = field(default_factory=list)
    #: `verify.Finding`s left after repair
    still: list[Any] = field(default_factory=list)
    #: grounding `check_skills.Outcome`
    traced: Any = None
    pages_note: str = ""
    client_problem: str = ""
    head_fixed: dict | None = None
    check_keys: tuple[str, ...] = ()
    quality: list[dict] = field(default_factory=list)
    #: facts the material lacks (`pre` checks)
    gaps: list[dict] = field(default_factory=list)
    #: `gap-card` questions: fact, label, why, placeholder
    questions: list[dict] = field(default_factory=list)
    #: `consistency.align` result
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
    #: last event per kind, for the save stage
    wire: dict[str, dict[str, Any]] = field(default_factory=dict)

    #: strict-mode watched fields
    FIELDS = (
        "spec.format_id", "spec.genre",
        "material.trusted", "material.parts", "material.given", "material.sources",
        "material.web_selected", "material.web_search", "material.research_rule",
        "material.grounded",
        "answers",
        "computed.values", "computed.settled", "computed.conflicts", "computed.prepass",
        "computed.calc_status",
        "plan.title", "plan.headings", "plan.frame", "plan.unverified", "plan.figures",
        "plan.proposal", "plan.revision", "plan.order", "plan.widened", "plan.route",
        "document.sections", "document.drafted", "document.written", "document.long_form",
        "document.head_block", "document.streamed", "document.answer", "document.kept_steps",
        "document.length_check", "document.data", "document.changed", "document.by_hand",
        "document.handed", "document.reply", "document.loaded", "document.saved",
        "findings.checks", "findings.format_checks", "findings.before", "findings.after",
        "findings.added_before", "findings.added_after", "findings.still", "findings.traced",
        "findings.pages_note", "findings.client_problem", "findings.head_fixed",
        "findings.check_keys", "findings.quality", "findings.gaps", "findings.aligned",
        "findings.questions",
        "files",
    )

    @property
    def records(self) -> list[dict[str, Any]]:
        return [r.as_dict() for r in self.steps]


__all__ = ["Bundle", "Computed", "Document", "Findings", "Material", "Part", "Plan",
           "Request", "Spec"]
