"""Turns workspace rows into system-prompt blocks.

Assembly order, and why each block sits where it does:

1. **agent** — the persona; everything after is context it works within.
2. **project instructions** — standing orders for this body of work.
3. **memories** — durable facts, pinned first. Project-scoped ones only inside
   their project.
4. **skills** — procedures, injected only where they apply to the surface.
5. **project knowledge** — file excerpts, last: bulkiest and least
   instruction-like.

Retrieval is deliberately plain — in-scope memories in full, file text up to a
character budget. Semantic ranking earns its place when a user has more
memories than fit.
"""

from __future__ import annotations

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.chat import ChatSession
from app.models.user import User
from app.models.workspace import Agent, Memory, Project, Skill, StoredFile

#: Above this, pinned wins and the rest are named but not carried.
_MAX_MEMORIES = 40

_MEMORY_TYPE_LABEL = {
    "user": "사용자",
    "feedback": "피드백",
    "project": "프로젝트",
    "reference": "참고",
}


async def _agent_block(db: AsyncSession, session: ChatSession) -> str:
    if not session.agent_id:
        return ""
    agent = await db.get(Agent, session.agent_id)
    if agent is None or not agent.enabled or not agent.system_prompt.strip():
        return ""
    return f"# 역할\n{agent.system_prompt.strip()}"


async def _project_blocks(
    db: AsyncSession, session: ChatSession
) -> tuple[str, str]:
    """Returns `(instructions, knowledge)`."""
    if not session.project_id:
        return "", ""
    project = await db.get(Project, session.project_id)
    if project is None:
        return "", ""

    instructions = ""
    if project.instructions.strip():
        instructions = (
            f"# 프로젝트 지침 — {project.name}\n{project.instructions.strip()}"
        )

    files = (
        await db.exec(
            select(StoredFile)
            .where(StoredFile.project_id == project.id)
            .order_by(col(StoredFile.created_at))
        )
    ).all()
    knowledge = _knowledge_block([f for f in files if f.text])
    return instructions, knowledge


def _knowledge_block(files: list[StoredFile], header: str = "# 프로젝트 지식") -> str:
    """Packs file text into a character budget.

    Whole files first, then one excerpt, then names only. Naming the rest is
    what lets the model say a document was not included rather than answering
    as though it had read it.
    """
    if not files:
        return ""

    budget = settings.file_context_chars
    # Said once, under the heading: the text below *is* the file, already
    # extracted. Without it a model with no file-reading tool answers that it
    # cannot open attachments — with their contents in front of it.
    parts: list[str] = [f"{header}\n아래는 이미 읽어 둔 파일 본문입니다. 그대로 참고하세요."]
    omitted: list[str] = []
    for stored in files:
        if budget <= 0:
            omitted.append(stored.name)
            continue
        text = stored.text
        if len(text) > budget:
            text = text[:budget] + f"\n…(이하 {len(stored.text) - budget:,}자 생략)"
        budget -= len(text)
        parts.append(f"## {stored.name}\n{text}")

    if omitted:
        parts.append(
            "## 포함되지 않은 파일\n"
            + ", ".join(omitted)
            + "\n이 파일들은 분량 때문에 이번 요청에 포함되지 않았습니다. "
            "내용이 필요하면 사용자에게 물어보세요."
        )
    return "\n\n".join(parts)


async def _memory_block(db: AsyncSession, user: User, session: ChatSession) -> str:
    scopes = ["global"]
    if session.project_id:
        scopes.append(session.project_id)

    rows = (
        await db.exec(
            select(Memory).where(
                Memory.user_id == user.id,
                col(Memory.scope).in_(scopes),
            )
        )
    ).all()
    if not rows:
        return ""

    # Pinned first, then most recently touched. Not by name: past the cap, an
    # alphabetical order decides which memories are used, and one dropped for
    # that reason is indistinguishable from a broken feature.
    ordered = sorted(rows, key=lambda m: (not m.pinned, -m.updated_at.timestamp(), m.name))
    ordered = ordered[:_MAX_MEMORIES]

    lines = ["# 기억하고 있는 사실"]
    for memory in ordered:
        label = _MEMORY_TYPE_LABEL.get(memory.type.value, memory.type.value)
        lines.append(f"- ({label}) {memory.name}: {memory.body.strip() or memory.description}")
    if len(rows) > _MAX_MEMORIES:
        lines.append(
            f"(고정된 항목과 최근 항목 우선으로 {_MAX_MEMORIES}개만 실었습니다. "
            f"전체 {len(rows)}개 중 일부입니다.)"
        )
    return "\n".join(lines)


async def _skill_block(db: AsyncSession, user: User, session: ChatSession) -> str:
    rows = (
        await db.exec(
            select(Skill).where(Skill.owner_id == user.id, Skill.enabled == True)  # noqa: E712
        )
    ).all()

    kind = session.kind.value
    applicable = [s for s in rows if not s.kinds or kind in s.kinds]

    # An agent may narrow further: its `skill_ids`, when set, are the only ones
    # it runs with.
    if session.agent_id:
        agent = await db.get(Agent, session.agent_id)
        if agent is not None and agent.skill_ids:
            allowed = set(agent.skill_ids)
            applicable = [s for s in applicable if s.id in allowed]

    if not applicable:
        return ""

    parts = ["# 사용할 수 있는 절차"]
    for skill in applicable:
        head = f"## {skill.name}"
        if skill.when_to_use.strip():
            head += f"\n적용 시점: {skill.when_to_use.strip()}"
        body = skill.body.strip() or skill.description.strip()
        parts.append(f"{head}\n{body}" if body else head)
    return "\n\n".join(parts)


async def assemble(
    db: AsyncSession,
    user: User,
    session: ChatSession,
    *,
    attachment_ids: list[str] | None = None,
) -> list[str]:
    """The workspace blocks, in order, ready to join into the system turn."""
    agent = await _agent_block(db, session)
    instructions, knowledge = await _project_blocks(db, session)
    memories = await _memory_block(db, user, session)
    skills = await _skill_block(db, user, session)

    attached = ""
    if attachment_ids:
        rows = (
            await db.exec(
                select(StoredFile).where(
                    StoredFile.user_id == user.id,
                    col(StoredFile.id).in_(attachment_ids),
                )
            )
        ).all()
        readable = [f for f in rows if f.text]
        unreadable = [f for f in rows if not f.text]
        attached = _knowledge_block(readable, header="# 이번 요청에 첨부된 파일")
        if unreadable:
            names = ", ".join(f"{f.name}({f.error or '내용 없음'})" for f in unreadable)
            # Named even when unreadable: the user can see they attached it.
            attached = (attached + "\n\n" if attached else "") + (
                f"# 읽지 못한 첨부\n{names}\n이 파일들은 내용을 읽지 못했습니다. "
                "사용자에게 형식을 바꿔 다시 올려 달라고 안내하세요."
            )

    return [b for b in (agent, instructions, memories, skills, attached, knowledge) if b]


async def agent_settings(db: AsyncSession, session: ChatSession) -> tuple[str | None, list[str]]:
    """`(model_override, tool_allowlist)` for the session's agent, if any."""
    if not session.agent_id:
        return None, []
    agent = await db.get(Agent, session.agent_id)
    if agent is None or not agent.enabled:
        return None, []
    return (agent.model or None), list(agent.tools or [])
