"""Which artifact (and which version of it) each assistant message produced.

Media turns store their artifact ids on the message. Document turns (reports, decks,
pages, revisions) do not: the pipeline run that settled the turn records the message,
the artifact and the version it wrote (`PipelineRun`). Read here so every transcript —
old ones included — can show the result under the answer that made it.
"""

from __future__ import annotations

from sqlmodel import col, select

from app.models.chat import Message, PipelineRun
from app.schemas.chat import MessageOut


async def run_links(db, session_id: str) -> dict[str, tuple[str, int | None]]:
    """`{message_id: (artifact_id, version)}` from the session's settled runs; latest wins."""
    rows = (
        await db.exec(
            select(PipelineRun.message_id, PipelineRun.artifact_id, PipelineRun.artifact_version)
            .where(
                PipelineRun.session_id == session_id,
                col(PipelineRun.message_id).is_not(None),
                col(PipelineRun.artifact_id).is_not(None),
            )
            .order_by(col(PipelineRun.created_at))
        )
    ).all()
    return {str(message): (str(artifact), version) for message, artifact, version in rows}


def linked(m: Message, links: dict[str, tuple[str, int | None]] | None) -> MessageOut:
    """The message on the wire, with the artifact its run wrote when it names none itself."""
    out = MessageOut.of(m)
    link = (links or {}).get(m.id)
    if link is None:
        return out
    artifact_id, version = link
    if not out.artifact_ids:
        out.artifact_ids = [artifact_id]
    if artifact_id in (out.artifact_ids or []):
        out.artifact_version = version
    return out
