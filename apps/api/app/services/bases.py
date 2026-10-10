"""The official processes a person's own agent can start from (진행 과정).

An agent's 진행 과정 is one of three:

- `direct` — 바로 답하기: no base; only what its ticked 묶음 add;
- `base` — 기본 과정에서 시작: an offered catalogue agent's process (its question card,
  format, template and pipeline), run as shipped, with the 묶음 a person ticks;
- `custom` — 직접 구성 (고급): the agent's own steps in the step editor.

A base is an offered catalogue agent (`app/agents/*.toml`, not retired; drafts for
administrators). Its steps, as a person reads them, are the `[summary]` lines of the pipeline
it runs (`app/pipelines/*.toml`), at most five. Its 묶음 are the ones its file names, with
their items' ticks (`services/bundles.py`): picking the base ticks them, and compiling them
gives the row exactly the check skills and strictness the official agent holds.

A pipeline of the agent's own (`UserPipeline.agent_id`) exists only when a ticked item is a
step (a chat agent's calculation, an export, a person's bundle with a review or another
agent); its `@self` step runs the agent's own flow. Shipped bundles never wait for review; a
person's bundle that exports, reviews or runs another agent does, as a 직접 구성 with those
does (`needs_review`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.pipeline import compose
from app.services import bundles as bundle_service

#: The `@self` agent step: the agent whose own process the pipeline is, on its own shipped flow.
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
    #: `(summary key, line)` pairs of the pipeline it runs, at most five.
    steps: tuple[tuple[str, str], ...]
    #: The 묶음 its file names, as stored selections.
    bundles: tuple[dict, ...] = ()
    #: The format checks its flow runs (what 형식 검사 means for it), by name.
    format_checks: tuple[str, ...] = ()
    #: Whether its flow has a fact list to ask from (빠진 정보 먼저 묻기 can ask anything).
    asks: bool = False
    draft: bool = False

    @property
    def custom(self) -> bool:
        """Whether 직접 구성 may start from it: a step can run a document or an answer."""
        return self.surface in ("report", "chat")

    def wire(self) -> dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "description": self.description,
            "surface": self.surface, "docFormat": self.doc_format, "template": self.template,
            "steps": [line for _, line in self.steps], "draft": self.draft,
            "custom": self.custom, "bundles": [dict(b) for b in self.bundles],
            "formatChecks": list(self.format_checks), "asks": self.asks,
        }


def _pipeline_for(spec: dict):
    from app.pipeline import registry
    from app.routers.pipelines import pipeline_for_agent

    if spec.get("surface") == "slides":
        return registry.pipeline("deck")
    return pipeline_for_agent(spec["key"], spec.get("doc_format"), spec.get("surface"))


def _base(spec: dict) -> Base:
    from app.routers.pipelines import check_name
    from app.services import check_skills

    surface = str(spec.get("surface") or (spec.get("kinds") or ["chat"])[0])
    pipe = _pipeline_for(spec)
    steps = tuple((k, pipe.summary[k]) for k in _SUMMARY_ORDER if pipe.summary.get(k))[:5]
    checks = tuple(check_name(k) for k in pipe.flow("checks")) if surface == "report" else ()
    return Base(
        key=spec["key"], name=spec["name"], description=spec.get("description", ""),
        surface=surface, doc_format=spec.get("doc_format") or None,
        template=spec.get("template") or None, steps=steps,
        bundles=tuple(spec.get("bundles") or ()), format_checks=checks,
        asks=bool(check_skills.fact_set(spec.get("doc_format"), spec["key"])),
        draft=bool(spec.get("draft")),
    )


def bases(*, drafts: bool = False) -> list[Base]:
    """The bases a person may pick, in catalogue order; drafts for administrators."""
    from app.services import starter

    return [_base(spec) for spec in starter.offered_agents(drafts=drafts)]


def get(key: str | None, *, drafts: bool = True) -> Base | None:
    """The base for a catalogue key; None for an unknown or retired one."""
    return next((b for b in bases(drafts=drafts) if b.key == key), None) if key else None


#: 문서 형식 → the base offered for it (migration 0060 backfills `agents.base_key` with it).
BY_FORMAT = {"gaejo": "gaejo-report", "minutes": "minutes", "comparison": "comparison-report"}


def needs_review(base: Base | None, steps: list[dict[str, Any]]) -> bool:
    """Whether others may run a 직접 구성 only after an administrator approves it: it exports
    files, has another model review, or runs another agent."""
    return any(s.get("kind") in ("export", "review")
               or (s.get("kind") == "agent" and s.get("ref") != SELF)
               for s in compose.normalize(steps))


def chain(base: Base | None, surface: str, selections: list[dict], known: dict,
          check_skills: list | None) -> list[str]:
    """What the agent does, a line each: its base's steps (no question line when it asks
    nothing) and what its 묶음 add as steps of their own."""
    asking = "gap-check" in (check_skills or [])
    if base is None:
        return ["바로 답합니다", *bundle_service.lines(surface, selections, known)]
    lines = [line for key, line in base.steps if key != "question" or asking]
    extra = []
    for row in bundle_service.clean(selections):
        bundle = known.get(row["bundle"])
        if bundle is None:
            continue
        for item in bundle.items:
            where = bundle_service.target(item, surface)
            if where in ("before", "after") and bundle_service._value(bundle, item, row["items"]):
                label = item.label or item.ref
                value = bundle_service._value(bundle, item, row["items"])
                extra.append(f"{label} ({str(value).upper()})" if item.kind == "export"
                             else label)
    return [*lines, *dict.fromkeys(extra)]


def process_of(agent, row, *, owner: bool, known: dict[str, bundle_service.Bundle],
               titles: dict[str, str] | None = None) -> dict[str, Any]:
    """An agent's 진행 과정 on the wire: `{mode, base, bundles, chain, review, needsReview,
    steps?}`. `row` is the pipeline it is bound to (None: none); `steps` go to the owner only;
    `known` is every bundle its selections may name."""
    key = agent.catalog_key or getattr(agent, "base_key", None)
    base = get(key)
    surface = agent.surface or next(iter(agent.kinds or []), None) or "chat"
    own = row is not None and getattr(row, "agent_id", None) == agent.id
    stored = getattr(agent, "bundles", None)
    if row is None or (own and stored is not None):
        selections = bundle_service.view(stored or [], known, check_skills=agent.check_skills,
                                         check_options=agent.check_options) \
            if stored is not None or agent.check_skills else []
        unapproved = bundle_service.compile(surface, selections, known).unapproved
        return {"mode": "base" if base else DIRECT, "base": base.key if base else None,
                "bundles": selections,
                "chain": chain(base, surface, selections, known, agent.check_skills),
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
