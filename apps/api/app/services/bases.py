"""Official bases (진행 과정) for a person's agent: `direct`, `base`, `custom`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.pipeline import compose
from app.services import bundles as bundle_service

SELF = compose.SELF

DIRECT = "direct"
MODES = ("direct", "base", "custom")
_SUMMARY_ORDER = ("material", "question", "compute", "write", "check")


@dataclass(frozen=True, slots=True)
class Base:
    key: str
    name: str
    description: str
    surface: str
    doc_format: str | None
    template: str | None
    #: `(summary key, line)` pairs, at most five
    steps: tuple[tuple[str, str], ...]
    #: 묶음 named by its file, as stored selections
    bundles: tuple[dict, ...] = ()
    #: format checks its flow runs
    format_checks: tuple[str, ...] = ()
    #: ships 빠진 정보 질문 (own or 서식's)
    asks: bool = False
    draft: bool = False
    #: official `[process]` settings over its 서식's; inherited by agents without their own
    process: dict | None = None

    @property
    def custom(self) -> bool:
        """Whether 직접 구성 may start from it."""
        return self.surface in ("report", "chat")

    def wire(self) -> dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "description": self.description,
            "surface": self.surface, "docFormat": self.doc_format, "template": self.template,
            "steps": [line for _, line in self.steps], "draft": self.draft,
            "custom": self.custom, "bundles": [dict(b) for b in self.bundles],
            "formatChecks": list(self.format_checks), "asks": self.asks,
            "templateName": self.template_name,
        }

    @property
    def template_name(self) -> str:
        """Kind label from the template list (「회의록」)."""
        from app.services import design_templates

        found = design_templates.get(self.template) if self.template else None
        return found.name if found is not None else ""


def _pipeline_for(spec: dict):
    from app.pipeline import registry
    from app.routers.pipelines import pipeline_for_agent

    if spec.get("surface") == "slides":
        return registry.pipeline("deck")
    return pipeline_for_agent(spec["key"], spec.get("doc_format"), spec.get("surface"))


def _base(spec: dict) -> Base:
    from app.routers.pipelines import check_name
    from app.services import gap_questions, process_settings

    surface = str(spec.get("surface") or (spec.get("kinds") or ["chat"])[0])
    # agent file's `[summary]`, else its pipeline's
    summary = dict(spec.get("summary") or {}) or _pipeline_for(spec).summary
    steps = tuple((k, summary[k]) for k in _SUMMARY_ORDER if summary.get(k))[:5]
    process = process_settings.resolve(template_id=spec.get("template"),
                                       format_id=spec.get("doc_format"),
                                       own=spec.get("process"))
    checks = tuple(check_name(k) for k in process.code_checks) if surface == "report" else ()
    return Base(
        key=spec["key"], name=spec["name"], description=spec.get("description", ""),
        surface=surface, doc_format=spec.get("doc_format") or None,
        template=spec.get("template") or None, steps=steps,
        bundles=tuple(spec.get("bundles") or ()), format_checks=checks,
        asks=bool(gap_questions.resolve(own=None, base_key=spec["key"],
                                        template_id=spec.get("template"),
                                        doc_format=spec.get("doc_format"))),
        draft=bool(spec.get("draft")),
        process=dict(spec["process"]) if spec.get("process") else None,
    )


def bases(*, drafts: bool = False, withdrawn: bool = False) -> list[Base]:
    """Pickable bases in catalogue order; drafts for admins; withdrawn ones only on request
    (an agent already built on one keeps its base)."""
    from app.services import starter

    return [_base(spec) for spec in starter.offered_agents(drafts=drafts, withdrawn=withdrawn)]


def get(key: str | None, *, drafts: bool = True) -> Base | None:
    """Base for a catalogue key (withdrawn ones included); None if unknown or retired."""
    return next((b for b in bases(drafts=drafts, withdrawn=True) if b.key == key),
                None) if key else None


#: 문서 형식 → base (also backfilled into `agents.base_key` by a migration)
BY_FORMAT = {"gaejo": "gaejo-report", "minutes": "minutes", "comparison": "comparison-report"}


def needs_review(base: Base | None, steps: list[dict[str, Any]]) -> bool:
    """Whether a 직접 구성 needs admin approval: exports, model review, or another agent."""
    return any(s.get("kind") in ("export", "review")
               or (s.get("kind") == "agent" and s.get("ref") != SELF)
               for s in compose.normalize(steps))


def chain(base: Base | None, surface: str, selections: list[dict], known: dict,
          check_skills: list | None, *, doc_format: str | None = None) -> list[str]:
    """Agent's step lines: base steps plus 묶음 steps."""
    asking = "gap-check" in (check_skills or [])
    if base is None:
        return ["바로 답합니다", *bundle_service.lines(surface, selections, known,
                                                         doc_format=doc_format)]
    lines = [line for key, line in base.steps if key != "question" or asking]
    extra = []
    for row in bundle_service.clean(selections):
        bundle = known.get(row["bundle"])
        if bundle is None:
            continue
        for item in bundle.items:
            where = bundle_service.target(item, surface)
            value = bundle_service._value(item, row["items"], doc_format)
            if where in ("before", "after") and value:
                label = item.label or item.ref
                extra.append(f"{label} ({str(value).upper()})" if item.kind == "export"
                             else label)
    return [*lines, *dict.fromkeys(extra)]


def process_of(agent, row, *, owner: bool, known: dict[str, bundle_service.Bundle],
               titles: dict[str, str] | None = None) -> dict[str, Any]:
    """Agent 진행 과정 on the wire; `steps` owner-only."""
    key = agent.catalog_key or getattr(agent, "base_key", None)
    base = get(key)
    surface = agent.surface or next(iter(agent.kinds or []), None) or "chat"
    own = row is not None and getattr(row, "agent_id", None) == agent.id
    stored = getattr(agent, "bundles", None)
    doc_format = getattr(agent, "doc_format", None) or (base.doc_format if base else None)
    if row is None or (own and stored is not None):
        selections = bundle_service.view(stored or [], known, check_skills=agent.check_skills,
                                         check_options=agent.check_options,
                                         doc_format=doc_format) \
            if stored is not None or agent.check_skills else []
        unapproved = bundle_service.compile(surface, selections, known,
                                            doc_format=doc_format).unapproved
        return {"mode": "base" if base else DIRECT, "base": base.key if base else None,
                "bundles": selections,
                "chain": chain(base, surface, selections, known, agent.check_skills,
                               doc_format=doc_format),
                "review": row.review if row is not None else "",
                "needsReview": bool(unapproved),
                **({"pipelineId": row.id} if owner and row is not None else {})}
    steps = compose.normalize(row.stages)
    names = titles or {}
    out: dict[str, Any] = {
        "mode": "custom", "base": base.key if base else None, "bundles": [],
        "chain": [names.get(s["id"]) or compose.KIND_WORDS.get(s["kind"], "단계") for s in steps],
        "review": row.review, "needsReview": needs_review(base, steps),
    }
    if owner:
        out["steps"] = steps
        out["pipelineId"] = row.id
    return out


__all__ = ["BY_FORMAT", "DIRECT", "MODES", "SELF", "Base", "bases", "chain", "get",
           "needs_review", "process_of"]
