"""Issue-report session data: content-free context; snapshot only with consent."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import col, select

from app.core import logs
from app.models.chat import ChatSession, Message, PipelineRun
from app.models.workspace import Agent, Artifact, ArtifactVersion, Project, StoredFile, UserPipeline
from app.schemas.chat import MessageOut
from app.services import files as file_service

log = logging.getLogger(__name__)

#: serialized snapshot cap; cut in steps (capped)
MAX_BYTES = 2_000_000
#: blob copy caps per file and report; larger listed only
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_COPY_BYTES = 30 * 1024 * 1024
MAX_ARTIFACTS = 30
MAX_RUNS = 20
MAX_FILES = 40
#: per-message text when clipping; pinned keeps more
_CLIP = 20_000
_CLIP_PINNED = 60_000
#: stage records in context (no evidence)
_CONTEXT_RUNS = 10

#: routing actions meaning strict-local
STRICT_ACTIONS = frozenset({"strict_local", "route_strict_local", "internal_strict_local"})

_CLIENT_KEYS = ("userAgent", "appBuild", "path", "viewport", "lang", "timezone")


@dataclass(slots=True)
class Loaded:
    """One session's rows, read once for context and snapshot."""

    session: ChatSession
    messages: list[Message]
    runs: list[PipelineRun]
    artifacts: list[Artifact]
    versions: dict[str, list[ArtifactVersion]] = field(default_factory=dict)
    files: list[StoredFile] = field(default_factory=list)
    agent: str | None = None
    project: str | None = None
    pipeline: str | None = None


async def load(db, session: ChatSession) -> Loaded:
    messages = list((await db.exec(
        select(Message).where(Message.session_id == session.id)
        .order_by(col(Message.created_at), col(Message.id))
    )).all())
    runs = list((await db.exec(
        select(PipelineRun)
        .where(PipelineRun.session_id == session.id, PipelineRun.user_id == session.user_id)
        .order_by(col(PipelineRun.created_at).desc()).limit(MAX_RUNS)
    )).all())
    wanted: list[str] = []
    for artifact_id in (
        [session.artifact_id]
        + [a for m in messages for a in (m.artifact_ids or []) if isinstance(a, str)]
        + [r.artifact_id for r in runs]
    ):
        if artifact_id and artifact_id not in wanted:
            wanted.append(artifact_id)
    query = select(Artifact).where(Artifact.user_id == session.user_id)
    made_here = (await db.exec(query.where(Artifact.session_id == session.id))).all()
    named = (await db.exec(query.where(col(Artifact.id).in_(wanted)))).all() if wanted else []
    by_id = {a.id: a for a in [*named, *made_here]}
    order = wanted + [a.id for a in made_here if a.id not in wanted]
    artifacts = [by_id[i] for i in order if i in by_id][:MAX_ARTIFACTS]
    versions: dict[str, list[ArtifactVersion]] = {}
    if artifacts:
        for row in (await db.exec(
            select(ArtifactVersion)
            .where(col(ArtifactVersion.artifact_id).in_([a.id for a in artifacts]))
            .order_by(col(ArtifactVersion.version))
        )).all():
            versions.setdefault(row.artifact_id, []).append(row)
    files = list((await db.exec(
        select(StoredFile)
        .where(StoredFile.session_id == session.id, StoredFile.user_id == session.user_id)
        .order_by(col(StoredFile.created_at)).limit(MAX_FILES)
    )).all())
    # names only: agent prompts and project instructions never leave
    agent = await db.get(Agent, session.agent_id) if session.agent_id else None
    project = await db.get(Project, session.project_id) if session.project_id else None
    pipeline = await db.get(UserPipeline, session.pipeline_id) if session.pipeline_id else None
    return Loaded(
        session=session, messages=messages, runs=runs, artifacts=artifacts, versions=versions,
        files=files,
        agent=agent.name if agent is not None else None,
        project=(project.name if project is not None and project.user_id == session.user_id
                 else None),
        pipeline=pipeline.name if pipeline is not None else None,
    )


def strict_local(loaded: Loaded) -> bool:
    """Whether any turn ran strict-local."""
    for message in loaded.messages:
        routing = message.routing if isinstance(message.routing, dict) else {}
        if {routing.get("action"), routing.get("initialAction")} & STRICT_ACTIONS:
            return True
        if (message.model or "").startswith("strict-local/"):
            return True
    return (loaded.session.model or "").startswith("strict-local/")


def _models(loaded: Loaded) -> list[str]:
    seen: list[str] = []
    for message in loaded.messages:
        routing = message.routing if isinstance(message.routing, dict) else {}
        actual = routing.get("actualModels")
        for model in [message.model, *(actual if isinstance(actual, list) else [])]:
            if isinstance(model, str) and model and model not in seen:
                seen.append(model)
    if loaded.session.model and loaded.session.model not in seen:
        seen.append(loaded.session.model)
    return seen[:20]


def _run_out(run: PipelineRun, *, evidence: bool) -> dict[str, Any]:
    # late import: router owns the run wire shape
    from app.routers.runs import _out

    return _out(run, evidence=evidence)


def client_info(raw: dict | None) -> dict[str, str]:
    """Browser self-description: known keys, clipped."""
    out: dict[str, str] = {}
    for key in _CLIENT_KEYS:
        value = (raw or {}).get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            out[key] = str(value).strip()[: 400 if key == "userAgent" else 200]
    return out


def context(
    loaded: Loaded | None, *, client: dict[str, str], api_version: str,
    message_id: str | None, artifact_id: str | None,
) -> dict[str, Any]:
    """Per-report context; no message text or document body."""
    out: dict[str, Any] = {"client": client, "apiVersion": api_version}
    if loaded is None:
        return out
    session = loaded.session
    out["session"] = {
        "id": session.id,
        "kind": session.kind.value if hasattr(session.kind, "value") else str(session.kind),
        "model": session.model,
        "routingMode": str(getattr(session.routing_mode, "value", session.routing_mode)),
        "agent": loaded.agent,
        "pipeline": loaded.pipeline or session.pipeline_id,
        "renderTemplateId": session.render_template_id,
        "messageCount": len(loaded.messages),
        "strictLocal": strict_local(loaded),
    }
    out["models"] = _models(loaded)
    out["failures"] = [
        {"messageId": m.id, "failure": str(getattr(m.failure, "value", m.failure))}
        for m in loaded.messages if m.failure
    ][:20]
    out["runs"] = [_run_out(r, evidence=False) for r in loaded.runs[:_CONTEXT_RUNS]]
    out["artifacts"] = [
        {
            "id": a.id, "kind": str(getattr(a.kind, "value", a.kind)), "version": a.version,
            "versionIds": [v.id for v in loaded.versions.get(a.id, [])][-20:],
        }
        for a in loaded.artifacts
    ]
    out["pinned"] = {"messageId": message_id, "artifactId": artifact_id}
    return out


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def build(
    loaded: Loaded, *, owner_id: str, report_id: str,
    message_id: str | None, artifact_id: str | None,
) -> tuple[dict[str, Any], list[str]]:
    """Snapshot and copied blob keys (for cleanup on failure)."""
    session = loaded.session
    copied: list[str] = []
    budget = MAX_COPY_BYTES

    def copy(source_key: str | None, name: str, tag: str) -> str | None:
        nonlocal budget
        if not source_key:
            return None
        try:
            size = file_service.blob_size(source_key)
        except OSError:
            return None
        if size > MAX_FILE_BYTES or size > budget:
            return None
        try:
            key = file_service.copy_blob(owner_id, f"issue-{report_id}-{tag}", name, source_key)
        except OSError as exc:
            log.warning("issue %s: blob not copied: %s", report_id, logs.safe(exc))
            return None
        budget -= size
        copied.append(key)
        return key

    referenced_versions = {
        (r.artifact_id, r.artifact_version) for r in loaded.runs if r.artifact_id
    }
    artifacts = []
    for a in loaded.artifacts:
        kind = str(getattr(a.kind, "value", a.kind))
        versions = []
        for v in loaded.versions.get(a.id, []):
            row: dict[str, Any] = {
                "id": v.id, "version": v.version, "summary": v.summary[:300],
                "createdAt": _iso(v.created_at),
            }
            # bodies only for run-written versions
            if (a.id, v.version) in referenced_versions and v.version != a.version:
                row["data"] = v.data
            versions.append(row)
        artifacts.append({
            "id": a.id, "kind": kind, "title": a.title, "version": a.version, "data": a.data,
            "updatedAt": _iso(a.updated_at), "versions": versions,
            "blob": copy(a.storage_key, f"{a.title or kind}", f"a{a.id[:12]}"),
        })
    files = []
    for f in loaded.files:
        files.append({
            "id": f.id, "name": f.name, "mime": f.mime, "size": f.size,
            "createdAt": _iso(f.created_at),
            "key": copy(f.storage_key, f.name, f"f{f.id[:12]}"),
        })
    snapshot: dict[str, Any] = {
        "session": {
            "id": session.id, "title": session.title,
            "kind": str(getattr(session.kind, "value", session.kind)),
            "model": session.model, "agent": loaded.agent, "project": loaded.project,
            "pipeline": loaded.pipeline, "artifactId": session.artifact_id,
            "createdAt": _iso(session.created_at), "updatedAt": _iso(session.updated_at),
        },
        "messages": [
            MessageOut.of(m).model_dump(mode="json", by_alias=True) for m in loaded.messages
        ],
        "runs": [_run_out(r, evidence=True) for r in loaded.runs],
        "artifacts": artifacts,
        "files": files,
        "pinned": {"messageId": message_id, "artifactId": artifact_id},
        "truncated": [],
    }
    return capped(snapshot), copied


def size_of(snapshot: dict[str, Any]) -> int:
    return len(json.dumps(snapshot, ensure_ascii=False, default=str).encode())


def capped(snapshot: dict[str, Any], limit: int = MAX_BYTES) -> dict[str, Any]:
    """snapshot cut to limit bytes, least useful first; cuts noted."""
    notes: list[dict[str, Any]] = snapshot.setdefault("truncated", [])
    pinned = snapshot.get("pinned") or {}
    keep_artifacts = {pinned.get("artifactId"), (snapshot.get("session") or {}).get("artifactId")}

    def fits() -> bool:
        return size_of(snapshot) <= limit

    if fits():
        return snapshot
    for run in snapshot.get("runs", []):
        for stage in run.get("stages", []):
            stage.pop("evidence", None)
    notes.append({"code": "evidence"})
    if fits():
        return snapshot
    for artifact in snapshot.get("artifacts", []):
        for version in artifact.get("versions", []):
            version.pop("data", None)
    notes.append({"code": "versions"})
    if fits():
        return snapshot
    for message in snapshot.get("messages", []):
        message["steps"] = None
        message["superseded"] = None
    notes.append({"code": "steps"})
    if fits():
        return snapshot
    clipped = 0
    for message in snapshot.get("messages", []):
        cap = _CLIP_PINNED if message.get("id") == pinned.get("messageId") else _CLIP
        text = message.get("content") or ""
        if len(text) > cap:
            message["content"] = text[:cap] + "\n\n…"
            clipped += 1
    if clipped:
        notes.append({"code": "content", "count": clipped})
    if fits():
        return snapshot
    dropped = 0
    for artifact in snapshot.get("artifacts", []):
        if artifact["id"] not in keep_artifacts and artifact.get("data") is not None:
            artifact["data"] = None
            dropped += 1
    if dropped:
        notes.append({"code": "artifacts", "count": dropped})
    messages = snapshot.get("messages", [])
    removed = 0
    # oldest first, never the pinned one
    while not fits() and len(messages) > 1:
        at = next((i for i, m in enumerate(messages) if m.get("id") != pinned.get("messageId")),
                  None)
        if at is None:
            break
        messages.pop(at)
        removed += 1
    if removed:
        notes.append({"code": "messages", "count": removed})
    if not fits():
        # still too big: keep the frame, drop bodies
        for artifact in snapshot.get("artifacts", []):
            artifact["data"] = None
        for message in messages:
            message["content"] = (message.get("content") or "")[:2000]
        notes.append({"code": "bodies"})
    return snapshot
