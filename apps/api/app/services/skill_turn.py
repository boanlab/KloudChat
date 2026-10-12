"""What the skills on a turn bring besides their instructions: a 템플릿, check skills, trace
strictness, 빠진 정보 질문 and 진행 과정 설정 (`skill_specs.DOCUMENT_FIELDS`).

The skills are the person's picks for the turn (or the conversation's standing set) and the
agent's always-on ones. What they bring sits under what the agent and the picked 템플릿 set:
a template only when none is picked, checks and questions added, settings under the agent's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession
from app.models.user import User
from app.models.workspace import Agent, Skill
from app.services import design_templates, skill_specs, user_templates
from app.services.workspace_context import agent_skill_rows

#: 템플릿 kind each document surface writes into
_TEMPLATE_KIND = {"report": "document", "slides": "deck"}


@dataclass(frozen=True, slots=True)
class Brought:
    template: str = ""
    checks: tuple[str, ...] = ()
    check_options: dict[str, bool] = field(default_factory=dict)
    questions: tuple[dict[str, Any], ...] = ()
    process: dict[str, Any] = field(default_factory=dict)

    def checks_with(self, agent_checks: list | None) -> list[str]:
        """The agent's check skills, then the skills' (each once)."""
        return list(dict.fromkeys([*(agent_checks or []), *self.checks]))

    def options_with(self, agent_options: dict) -> dict:
        return {**self.check_options, **(agent_options or {})}

    def questions_with(self, asking: list[dict]) -> list[dict]:
        seen = {q.get("id") for q in asking}
        return [*asking, *(dict(q) for q in self.questions if q.get("id") not in seen)]

    def process_with(self, own: dict | None) -> dict | None:
        """Agent's own settings over the skills'; None when neither sets any."""
        merged = {**self.process, **(own or {})}
        return merged or None


def merge(specs: list[skill_specs.Spec]) -> Brought:
    """First skill wins on the template and on each setting; checks and questions add up."""
    template = next((s.template for s in specs if s.template), "")
    checks = tuple(dict.fromkeys(k for s in specs for k in s.checks))
    options = {k: True for s in specs for k in s.check_options}
    questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for spec in specs:
        for q in spec.questions:
            if q.get("id") and q["id"] not in seen:
                seen.add(q["id"])
                questions.append(dict(q))
    process: dict[str, Any] = {}
    for spec in reversed(specs):
        process.update(spec.process or {})
    return Brought(template=template, checks=checks, check_options=options,
                   questions=tuple(questions), process=process)


async def brought(db: AsyncSession, user: User, session: ChatSession, agent: Agent | None,
                  active_ids: list[str]) -> Brought:
    """What this turn's skills bring (picked first, then the agent's)."""
    picked: list[Skill] = []
    if active_ids:
        rows = (await db.exec(select(Skill).where(
            Skill.owner_id == user.id, col(Skill.id).in_(active_ids)))).all()
        by_id = {row.id: row for row in rows}
        picked = [by_id[i] for i in active_ids if i in by_id]
    rows = [*picked, *await agent_skill_rows(db, user.id, agent, session.kind.value)]
    return merge([skill_specs.of_row(row) for row in rows])


async def template_for(db: AsyncSession, user: User, session: ChatSession,
                       found: Brought) -> str | None:
    """The skills' 템플릿 for a document conversation that has none, if usable here."""
    kind = _TEMPLATE_KIND.get(session.kind.value)
    if not kind or session.render_template_id or not found.template:
        return None
    if not await user_templates.usable(db, user.id, found.template):
        return None
    await user_templates.load(db, user.id, [found.template])
    template = design_templates.choosable(found.template)
    if template is None or template.kind != kind:
        return None
    return template.id


__all__ = ["Brought", "brought", "merge", "template_for"]
