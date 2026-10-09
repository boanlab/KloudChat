"""Pipelines people compose (`app.pipeline.compose`): edit, share through approval, run.

- `GET /user-pipelines` — mine; `GET /user-pipelines/store` — approved ones others shared;
  `GET /user-pipelines/catalogue` — what a step can be (skills, checks, review models …);
- `POST /user-pipelines` — a new one, cloned from an agent's shipped flow (`fromAgentId`),
  from another pipeline (`fromId`: mine, or an approved shared one — 「복제」), or from steps;
- `PUT /user-pipelines/{id}` — name, description and steps, refused with every problem in
  Korean when the order breaks a step's contract; `POST /user-pipelines/validate` checks a
  draft without saving it (the editor's live messages);
- `POST /user-pipelines/{id}/submit` — asks for organisation sharing; an administrator
  approves or rejects it (`/admin/user-pipelines…`); an edit takes an approved one back;
- `PUT /agents/{id}/user-pipeline` — an agent's turns run the pipeline instead of its own flow;
- `POST /user-pipelines/{id}/start` — a new conversation that runs it (「이 흐름으로 시작」).

A turn of a conversation bound to one (`sessions.send_message` → `turn_response`) is a run of
flow `compose` (pipeline `user:<id>`): kept, rerunnable from any step, waiting at a human step
until the answer or the approved outline comes back as the next message.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from fastapi import Request as HttpRequest
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlmodel import col, select

from app.core.deps import AdminUser, CurrentUser, DbSession, client_ip
from app.models.chat import ChatSession, Message, PipelineRun, Role, SessionKind
from app.models.user import AuditEvent, User, utcnow
from app.models.workspace import Agent, UserPipeline, Visibility
from app.pipeline import compose
from app.pipeline import runs as pipeline_runs
from app.pipeline.bundle import Bundle, Request
from app.pipeline.runner import Runner
from app.pipeline.stage import Context
from app.schemas.auth import Wire
from app.services import chat as chat_service
from app.services import check_skills, design_templates
from app.services import models as model_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["user-pipelines"])

_REVIEWS = ("", "pending", "approved", "rejected")


class PipelineIn(Wire):
    name: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=400)
    stages: list[dict[str, Any]] | None = None
    #: Clone sources (create only): an agent's shipped flow, or another pipeline.
    from_agent_id: str | None = Field(default=None, max_length=64)
    from_id: str | None = Field(default=None, max_length=64)


class ValidateIn(Wire):
    stages: list[dict[str, Any]] = Field(default_factory=list)
    id: str | None = Field(default=None, max_length=64)


class RejectIn(Wire):
    note: str = Field(default="", max_length=400)


class BindIn(Wire):
    pipeline_id: str | None = Field(default=None, max_length=64)


# ── what a person may put in a step ─────────────────────────────────────


def _output(agent: Agent) -> str:
    surface = agent.surface or next(iter(agent.kinds or []), None) or "chat"
    return {"report": "document", "chat": "answer"}.get(surface, "")


async def agents_for(db: DbSession, user: User) -> dict[str, compose.AgentInfo]:
    """The agents `user` may run as a step: their own and the organisation's."""
    rows = list((await db.exec(
        select(Agent).where((Agent.owner_id == user.id) | (Agent.visibility == Visibility.org))
    )).all())
    by_id = {a.id: a for a in rows}
    origins = {a.origin_id for a in rows if a.origin_id and a.origin_id not in by_id}
    if origins:
        for row in (await db.exec(select(Agent).where(col(Agent.id).in_(origins)))).all():
            by_id.setdefault(row.id, row)
    out: dict[str, compose.AgentInfo] = {}
    for agent in rows:
        origin = by_id.get(agent.origin_id or "")
        key = agent.catalog_key or (origin.catalog_key if origin is not None else None) or ""
        # A sealed copy runs on its original's prompt.
        prompt = (origin.system_prompt if agent.sealed and origin is not None
                  else agent.system_prompt)
        template = design_templates.get(agent.render_template_id) \
            if agent.render_template_id else None
        # A sealed copy runs with its original's tools too.
        own = origin if agent.sealed and origin is not None else agent
        out[agent.id] = compose.AgentInfo(
            id=agent.id, name=agent.name, output=_output(agent),
            doc_format=agent.doc_format or "", template_id=agent.render_template_id or "",
            catalog_key=key, system_prompt=prompt or "", pipeline_id=agent.pipeline_id or "",
            enabled=agent.enabled,
            tools=tuple(own.tools) if isinstance(own.tools, list) else None,
            temperature=own.temperature if isinstance(own.temperature, (int, float)) else None,
            checks=check_skills.attached(
                agent_key=key or None,
                template_checks=template.check_skills if template is not None else ()),
            shared=bool(key) or agent.visibility is Visibility.org,
            mine=agent.owner_id == user.id)
    return out


async def review_models(user: User) -> list[dict]:
    """The models a review step may use: the person's allowed chat and document models."""
    from app.routers import sessions

    catalogue = (await model_service.list_models_for_egress())["models"]
    seen: dict[str, dict] = {}
    for kind in ("chat", "report"):
        for model in sessions._allowed_models(user, catalogue, kind=kind):
            seen.setdefault(model["id"], model)
    return list(seen.values())


async def _graph(db: DbSession, user: User,
                 agents: dict[str, compose.AgentInfo]) -> dict[str, set[str]]:
    """Pipeline → the pipelines its agent steps' agents are bound to."""
    rows = (await db.exec(select(UserPipeline).where(UserPipeline.owner_id == user.id))).all()
    out: dict[str, set[str]] = {}
    for row in rows:
        out[row.id] = {agents[s["ref"]].pipeline_id for s in compose.normalize(row.stages)
                       if s["kind"] == "agent" and s["ref"] in agents
                       and agents[s["ref"]].pipeline_id}
    return out


async def _verdict(db: DbSession, user: User, steps: list[dict], *, self_id: str | None,
                   agents: dict[str, compose.AgentInfo] | None = None,
                   sharing: bool = False) -> compose.Verdict:
    agents = agents if agents is not None else await agents_for(db, user)
    allowed = {m["id"] for m in await review_models(user)}
    return compose.validate(steps, agents=agents, allowed_models=allowed, self_id=self_id,
                            graph=await _graph(db, user, agents), sharing=sharing)


def _remap(steps: list[dict], agents: dict[str, compose.AgentInfo]) -> list[dict]:
    """Agent steps pointed at agents the person can run: a catalogue agent the author held a
    copy of becomes the person's own copy of the same catalogue agent."""
    by_key: dict[str, str] = {}
    # Their own copy first, the organisation's original when they hold none.
    for info in sorted(agents.values(), key=lambda a: not a.mine):
        if info.catalog_key:
            by_key.setdefault(info.catalog_key, info.id)
    out = []
    for step in steps:
        key = str((step.get("settings") or {}).get("catalogKey") or "")
        if step["kind"] == "agent" and step["ref"] not in agents and key in by_key:
            step = {**step, "ref": by_key[key]}
        out.append(step)
    return out


def _steps_for(row: UserPipeline, user: User,
               agents: dict[str, compose.AgentInfo]) -> list[dict]:
    """The steps as `user` runs them: someone else's shared pipeline on the user's own agents
    (the author's copies of catalogue agents are theirs alone)."""
    steps = compose.normalize(row.stages)
    return steps if row.owner_id == user.id else _remap(steps, agents)


def _with_keys(steps: list[dict], agents: dict[str, compose.AgentInfo]) -> list[dict]:
    """Agent steps remember their agent's catalogue key, so a copy can find its own."""
    out = []
    for step in steps:
        info = agents.get(step["ref"]) if step["kind"] == "agent" else None
        if info is not None and info.catalog_key:
            step = {**step, "settings": {**step["settings"], "catalogKey": info.catalog_key}}
        out.append(step)
    return out


def _out(row: UserPipeline, agents: dict[str, compose.AgentInfo], *,
         verdict: compose.Verdict | None = None, owner_name: str = "",
         bound: list[dict] | None = None, steps: list[dict] | None = None) -> dict[str, Any]:
    steps = steps if steps is not None else compose.normalize(row.stages)
    return {
        "id": row.id, "name": row.name, "description": row.description, "base": row.base,
        "originId": row.origin_id,
        "stages": [{**s, "title": compose.step_title(s, agents),
                    **({"output": agents[s["ref"]].output}
                       if s["kind"] == "agent" and s["ref"] in agents else {})} for s in steps],
        "visibility": row.visibility, "review": row.review, "reviewNote": row.review_note,
        "reviewedAt": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "version": row.version, "installs": row.installs, "ownerId": row.owner_id,
        "ownerName": owner_name,
        **({"surface": verdict.surface, "problems": [p.wire() for p in verdict.problems],
            "warnings": [p.wire() for p in verdict.warnings]} if verdict is not None else {}),
        "boundAgents": bound or [],
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _own(db: DbSession, user: User, pipeline_id: str) -> UserPipeline:
    row = await db.get(UserPipeline, pipeline_id)
    if row is None or row.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pipeline_not_found")
    return row


def _visible(row: UserPipeline | None, user: User) -> bool:
    return row is not None and (row.owner_id == user.id or (
        row.visibility == "org" and row.review == "approved"))


async def _bound(db: DbSession, user: User, pipeline_id: str) -> list[dict]:
    rows = (await db.exec(select(Agent).where(Agent.owner_id == user.id,
                                              Agent.pipeline_id == pipeline_id))).all()
    return [{"id": a.id, "name": a.name} for a in rows]


def _invalid(verdict: compose.Verdict) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                         detail={"code": "invalid_pipeline", **verdict.wire()})


# ── reading ─────────────────────────────────────────────────────────────


@router.get("/user-pipelines")
async def list_mine(user: CurrentUser, db: DbSession):
    rows = (await db.exec(select(UserPipeline).where(UserPipeline.owner_id == user.id)
                          .order_by(col(UserPipeline.updated_at).desc()))).all()
    agents = await agents_for(db, user)
    out = []
    for row in rows:
        verdict = await _verdict(db, user, compose.normalize(row.stages), self_id=row.id,
                                 agents=agents)
        out.append(_out(row, agents, verdict=verdict, bound=await _bound(db, user, row.id)))
    return out


@router.get("/user-pipelines/store")
async def list_store(user: CurrentUser, db: DbSession):
    """Approved pipelines shared with the organisation, others' first."""
    rows = (await db.exec(select(UserPipeline).where(UserPipeline.visibility == "org",
                                                     UserPipeline.review == "approved")
                          .order_by(col(UserPipeline.installs).desc()))).all()
    agents = await agents_for(db, user)
    owners = {r.owner_id for r in rows}
    names = dict((await db.exec(select(User.id, User.name).where(col(User.id).in_(owners))))
                 .all()) if owners else {}
    mine = {r.origin_id for r in (await db.exec(
        select(UserPipeline).where(UserPipeline.owner_id == user.id))).all() if r.origin_id}
    return [{**_out(r, agents, owner_name=names.get(r.owner_id, ""),
                    steps=_steps_for(r, user, agents)),
             "installed": r.id in mine, "mine": r.owner_id == user.id} for r in rows]


@router.get("/user-pipelines/catalogue")
async def get_catalogue(user: CurrentUser):
    return {**compose.catalogue(),
            "reviewModels": [{"id": m["id"], "label": m.get("label") or m["id"]}
                             for m in await review_models(user)]}


@router.get("/user-pipelines/{pipeline_id}")
async def get_one(pipeline_id: str, user: CurrentUser, db: DbSession):
    row = await db.get(UserPipeline, pipeline_id)
    if not _visible(row, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pipeline_not_found")
    agents = await agents_for(db, user)
    steps = _steps_for(row, user, agents)
    verdict = await _verdict(db, user, steps, self_id=row.id, agents=agents)
    return _out(row, agents, verdict=verdict, steps=steps,
                bound=await _bound(db, user, row.id) if row.owner_id == user.id else [])


@router.post("/user-pipelines/validate")
async def validate_draft(payload: ValidateIn, user: CurrentUser, db: DbSession):
    steps = compose.normalize(payload.stages)
    agents = await agents_for(db, user)
    verdict = await _verdict(db, user, steps, self_id=payload.id, agents=agents)
    return {**verdict.wire(), "titles": [compose.step_title(s, agents) for s in steps]}


# ── writing ─────────────────────────────────────────────────────────────


@router.post("/user-pipelines", status_code=status.HTTP_201_CREATED)
async def create(payload: PipelineIn, user: CurrentUser, db: DbSession):
    agents = await agents_for(db, user)
    base: str | None = None
    origin: UserPipeline | None = None
    name = payload.name.strip()
    if payload.from_agent_id:
        info = agents.get(payload.from_agent_id)
        if info is None or info.output not in ("document", "answer"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent_not_found")
        from app.routers.pipelines import pipeline_for_agent

        pipe = pipeline_for_agent(info.catalog_key or None, info.doc_format or None,
                                  "report" if info.output == "document" else "chat")
        base = pipe.name
        steps = compose.from_shipped(info, pipe.name)
        name = name or f"{info.name} 흐름"
    elif payload.from_id:
        origin = await db.get(UserPipeline, payload.from_id)
        if not _visible(origin, user):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="pipeline_not_found")
        steps = _remap(compose.normalize(origin.stages), agents)
        base = origin.base
        name = name or (origin.name if origin.owner_id != user.id else f"{origin.name} 사본")
    else:
        steps = compose.normalize(payload.stages or [])
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="name_required")
    row = UserPipeline(owner_id=user.id, name=name[:80],
                       description=payload.description or (origin.description if origin else ""),
                       base=base, stages=_with_keys(steps, agents),
                       origin_id=origin.id if origin is not None and origin.owner_id != user.id
                       else None)
    if payload.stages is not None and not (payload.from_agent_id or payload.from_id):
        # Steps given outright are held to the contracts like an edit.
        verdict = await _verdict(db, user, steps, self_id=None, agents=agents)
        if not verdict.ok:
            raise _invalid(verdict)
    db.add(row)
    if origin is not None and origin.owner_id != user.id:
        origin.installs += 1
        db.add(origin)
    await db.commit()
    await db.refresh(row)
    verdict = await _verdict(db, user, compose.normalize(row.stages), self_id=row.id,
                             agents=agents)
    return _out(row, agents, verdict=verdict)


@router.put("/user-pipelines/{pipeline_id}")
async def update(pipeline_id: str, payload: PipelineIn, user: CurrentUser, db: DbSession):
    row = await _own(db, user, pipeline_id)
    agents = await agents_for(db, user)
    steps = compose.normalize(payload.stages if payload.stages is not None else row.stages)
    verdict = await _verdict(db, user, steps, self_id=row.id, agents=agents)
    if not verdict.ok:
        raise _invalid(verdict)
    bound = await _bound(db, user, row.id)
    if bound and verdict.surface:
        # An agent bound to it must still be able to show what it makes.
        rows = [await db.get(Agent, b["id"]) for b in bound]
        if any(a is not None and _output(a) != {"report": "document", "chat": "answer"}
               .get(verdict.surface) for a in rows):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
                "code": "invalid_pipeline", "ok": False, "surface": verdict.surface,
                "problems": [{"step": 0, "code": "bound_surface",
                              "message": ("이 흐름에 묶인 에이전트가 만드는 것과 마지막 에이전트가 "
                                          "만드는 것이 다릅니다. 묶음을 먼저 풀어 주세요.")}],
                "warnings": []})
    if payload.name.strip():
        row.name = payload.name.strip()[:80]
    row.description = payload.description
    row.stages = _with_keys(steps, agents)
    row.version += 1
    if row.review in ("pending", "approved"):
        # What was approved is not what is there now.
        row.review, row.visibility, row.review_note = "", "private", ""
    row.updated_at = utcnow()
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _out(row, agents, verdict=verdict, bound=bound)


@router.delete("/user-pipelines/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(pipeline_id: str, user: CurrentUser, db: DbSession):
    row = await _own(db, user, pipeline_id)
    for agent in (await db.exec(select(Agent).where(Agent.pipeline_id == row.id))).all():
        agent.pipeline_id = None
        db.add(agent)
    for session in (await db.exec(select(ChatSession).where(
            ChatSession.pipeline_id == row.id))).all():
        session.pipeline_id = None
        db.add(session)
    await db.delete(row)
    await db.commit()


@router.post("/user-pipelines/{pipeline_id}/submit")
async def submit(pipeline_id: str, user: CurrentUser, db: DbSession):
    row = await _own(db, user, pipeline_id)
    agents = await agents_for(db, user)
    verdict = await _verdict(db, user, compose.normalize(row.stages), self_id=row.id,
                             agents=agents, sharing=True)
    if not verdict.ok:
        raise _invalid(verdict)
    row.review, row.review_note, row.updated_at = "pending", "", utcnow()
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _out(row, agents, verdict=verdict, bound=await _bound(db, user, row.id))


@router.post("/user-pipelines/{pipeline_id}/withdraw")
async def withdraw(pipeline_id: str, user: CurrentUser, db: DbSession):
    """Takes a pipeline out of review or out of the store."""
    row = await _own(db, user, pipeline_id)
    row.review, row.visibility, row.updated_at = "", "private", utcnow()
    db.add(row)
    await db.commit()
    await db.refresh(row)
    agents = await agents_for(db, user)
    return _out(row, agents, bound=await _bound(db, user, row.id))


@router.put("/agents/{agent_id}/user-pipeline")
async def bind_agent(agent_id: str, payload: BindIn, user: CurrentUser, db: DbSession):
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if payload.pipeline_id:
        row = await _own(db, user, payload.pipeline_id)
        steps = compose.normalize(row.stages)
        agents = await agents_for(db, user)
        verdict = await _verdict(db, user, steps, self_id=row.id, agents=agents)
        problems = list(verdict.problems)
        if any(s["kind"] == "agent" and s["ref"] == agent.id for s in steps):
            problems.append(compose.Problem(0, "bind_recursion",
                                            compose.MESSAGES["bind_recursion"]))
        else:
            # Another of my pipelines runs this agent: bound here, this pipeline must not lead
            # back to it, or both stop running (each sees the other's agent as recursion).
            graph = await _graph(db, user, agents)
            holders = (await db.exec(select(UserPipeline).where(
                UserPipeline.owner_id == user.id, UserPipeline.id != row.id))).all()
            for other in holders:
                if any(s["kind"] == "agent" and s["ref"] == agent.id
                       for s in compose.normalize(other.stages)) and \
                        compose._reaches(row.id, other.id, graph):
                    problems.append(compose.Problem(0, "bind_cycle", compose.MESSAGES[
                        "bind_cycle"].format(name=other.name)))
                    break
        want = {"report": "document", "chat": "answer"}.get(verdict.surface, "")
        if want and _output(agent) != want:
            problems.append(compose.Problem(0, "bound_surface", (
                "문서를 만드는 흐름이라 보고서 에이전트에만 묶을 수 있습니다." if want == "document"
                else "답을 만드는 흐름이라 대화 에이전트에만 묶을 수 있습니다.")))
        if problems:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
                "code": "invalid_pipeline", "ok": False, "surface": verdict.surface,
                "problems": [p.wire() for p in problems], "warnings": []})
    agent.pipeline_id = payload.pipeline_id or None
    agent.updated_at = utcnow()
    db.add(agent)
    await db.commit()
    return {"agentId": agent.id, "pipelineId": agent.pipeline_id}


@router.post("/user-pipelines/{pipeline_id}/start", status_code=status.HTTP_201_CREATED)
async def start(pipeline_id: str, user: CurrentUser, db: DbSession):
    """A new conversation that runs this pipeline (the owner's, or an approved shared one)."""
    from app.routers import sessions
    from app.schemas.chat import SessionCreate

    row = await db.get(UserPipeline, pipeline_id)
    if not _visible(row, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pipeline_not_found")
    agents = await agents_for(db, user)
    verdict = await _verdict(db, user, _steps_for(row, user, agents), self_id=row.id,
                             agents=agents)
    if not verdict.ok:
        raise _invalid(verdict)
    created = await sessions.create_session(SessionCreate(kind=SessionKind(verdict.surface)),
                                            user, db)
    session = await db.get(ChatSession, created.id)
    session.pipeline_id = row.id
    db.add(session)
    await db.commit()
    return {"sessionId": session.id, "kind": verdict.surface, "pipelineId": row.id}


# ── approval ────────────────────────────────────────────────────────────


@router.get("/admin/user-pipelines")
async def admin_list(admin: AdminUser, db: DbSession, review: str = "pending"):
    query = select(UserPipeline).order_by(col(UserPipeline.updated_at).desc())
    if review in _REVIEWS and review:
        query = query.where(UserPipeline.review == review)
    rows = (await db.exec(query)).all()
    owners = {r.owner_id for r in rows}
    names = dict((await db.exec(select(User.id, User.name).where(col(User.id).in_(owners))))
                 .all()) if owners else {}
    agents = {a.id: compose.AgentInfo(id=a.id, name=a.name, output=_output(a))
              for a in (await db.exec(select(Agent).where(col(Agent.owner_id).in_(owners))))
              .all()} if owners else {}
    return [_out(r, agents, owner_name=names.get(r.owner_id, "")) for r in rows]


async def _reviewed(db: DbSession, admin: User, pipeline_id: str, *, approve: bool,
                    request: HttpRequest | None, note: str = "") -> dict[str, Any]:
    row = await db.get(UserPipeline, pipeline_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pipeline_not_found")
    if row.review != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="not_pending")
    row.review = "approved" if approve else "rejected"
    row.visibility = "org" if approve else "private"
    row.review_note = note.strip()[:400]
    row.reviewed_by, row.reviewed_at = admin.id, utcnow()
    db.add(row)
    db.add(AuditEvent(
        actor_id=admin.id, action="pipeline.approve" if approve else "pipeline.reject",
        target=row.name[:200], detail=row.review_note,
        event_metadata={"pipelineId": row.id,
                        "changes": {"review": ["pending", row.review]}},
        ip=client_ip(request) if request else "",
        user_agent=request.headers.get("User-Agent", "")[:400] if request else ""))
    await db.commit()
    await db.refresh(row)
    return _out(row, {})


@router.post("/admin/user-pipelines/{pipeline_id}/approve")
async def approve(pipeline_id: str, admin: AdminUser, db: DbSession,
                  request: HttpRequest = None):  # type: ignore[assignment]
    return await _reviewed(db, admin, pipeline_id, approve=True, request=request)


@router.post("/admin/user-pipelines/{pipeline_id}/reject")
async def reject(pipeline_id: str, payload: RejectIn, admin: AdminUser, db: DbSession,
                 request: HttpRequest = None):  # type: ignore[assignment]
    if not payload.note.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="reason_required")
    return await _reviewed(db, admin, pipeline_id, approve=False, request=request,
                           note=payload.note)


# ── running ─────────────────────────────────────────────────────────────


async def bound(db: DbSession, user: User, session: ChatSession,
                agent: Agent | None) -> UserPipeline | None:
    """The user pipeline this conversation's turns run, if any: the conversation's own, else
    its agent's."""
    pid = getattr(session, "pipeline_id", None) or (agent.pipeline_id if agent else None)
    if not pid:
        return None
    row = await db.get(UserPipeline, pid)
    return row if _visible(row, user) else None


def _sse(event: dict) -> str:
    return chat_service.sse(event)


def _stream(events) -> StreamingResponse:
    from app.routers import sessions

    return StreamingResponse(
        sessions._survive_disconnect(events), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"})


async def _refused(message: str, code: str, *, session_id: str, chat: bool):
    from app.routers import sessions

    try:
        yield _sse({"type": "error", "code": code, "message": message})
        yield _sse({"type": "done"})
    finally:
        if chat:
            await sessions._release_turn(session_id)


def _definition(row: UserPipeline, steps: list[dict]) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "version": row.version, "stages": steps}


async def turn_response(
    db: DbSession, user: User, session: ChatSession, row: UserPipeline, *, pending: dict,
    approved_plan: dict | None, proceed_as_is: bool, request: str, model: dict,
    outline_model: dict | None, api_key: str, routing: dict | None, trusted: list[str],
    untrusted: list[str], design_tokens: dict | None, skills_event: dict | None,
    context_steps: list[dict] | None, web_search: bool, attachments: list[str],
    catalogue: list[dict],
) -> StreamingResponse:
    """A turn of a conversation bound to a user pipeline: a fresh run, or the waiting one
    resumed with the card's answers or the approved outline."""
    from app.routers import sessions

    chat = session.kind is SessionKind.chat
    agents = await agents_for(db, user)
    resume = pending.get("compose") if isinstance(pending.get("compose"), dict) else None
    parent: PipelineRun | None = None
    if resume:
        parent = await db.get(PipelineRun, str(resume.get("runId") or ""))
        if parent is None or parent.user_id != user.id or not parent.snapshots:
            resume, parent = None, None
    if parent is not None:
        stored = pipeline_runs.decode((parent.params["request"].get("f") or {}).get("turn")) or {}
        definition = dict(stored.get("compose") or {})
        steps = compose.normalize(definition.get("stages"))
    else:
        definition = _definition(row, _steps_for(row, user, agents))
        steps = definition["stages"]
    verdict = compose.validate(steps, agents=agents, self_id=row.id)
    if not verdict.ok:
        said = f"흐름 「{row.name}」을 돌릴 수 없습니다: {verdict.problems[0].message}"
        return _stream(_refused(said,
                                "pipeline_invalid", session_id=session.id, chat=chat))
    if verdict.surface != session.kind.value:
        said = f"흐름 「{row.name}」이 만드는 것을 이 화면에서 보여 줄 수 없습니다."
        return _stream(_refused(said,
                                "pipeline_surface", session_id=session.id, chat=chat))
    used = {s["ref"] for s in steps if s["kind"] == "agent"}
    models = {m["id"]: m for m in catalogue}
    review_ids = {str((s.get("settings") or {}).get("model") or "")
                  for s in steps if s["kind"] == "review"} - {""}
    turn = {
        **sessions._report_turn(user_id=user.id, model=model, outline_model=outline_model,
                                project_id=session.project_id, routing=routing,
                                skills_event=skills_event, context_steps=context_steps,
                                carry_from=None, carry_note=""),
        "compose": definition, "agents": {a: agents[a].wire() for a in used},
        "models": {i: models[i] for i in review_ids if i in models}, "web_search": web_search,
        "attachments": list(attachments),
    }
    recorder = pipeline_runs.Recorder(run_id=uuid4().hex)
    start = carried = attempts = None
    if parent is not None:
        start = str(resume.get("stage") or "")
        try:
            work = pipeline_runs.restore(parent.params["request"], parent.snapshots,
                                         before=start, api_key=api_key)
        except KeyError:
            return _stream(_refused("멈춘 단계부터 이어 가지 못했습니다. 다시 요청해 주세요.",
                                    "run_not_resumable", session_id=session.id, chat=chat))
        work.usage = {"inputTokens": 0, "outputTokens": 0}
        work.request.api_key = api_key
        work.request.model = model["id"]
        work.request.turn = {**work.request.turn, **turn, "compose": definition,
                             "resume": start, "run_id": recorder.run_id,
                             "approved_plan": approved_plan}
        if not approved_plan and resume.get("kind") == "outline":
            # A note on the outline: planned again with it.
            work.request.text = request
        work.answers = {} if proceed_as_is else {
            str(k): str(v) for k, v in (pending.get("answers") or {}).items()}
        carried = pipeline_runs.carried(parent.snapshots, before=start)
        names = [s.name for s in compose.compile_steps(steps, agents)]
        earlier = {str(r.get("stage")): int(r.get("attempt") or 1) for r in parent.stages or []}
        attempts = {n: earlier.get(n, 1) for n in names}
        await _close_wait(parent.id, approved=bool(approved_plan))
    else:
        work = Bundle(request=Request(
            text=request, model=model["id"], api_key=api_key,
            outline_model=(outline_model or {}).get("id", ""), trusted=list(trusted),
            untrusted=list(untrusted), design_tokens=design_tokens, session_id=session.id,
            today=sessions._today(), web_search=web_search, user_id=user.id,
            turn={**turn, "run_id": recorder.run_id}))
        work.material.given = "\n".join([request, *untrusted])
    return _stream(compose_pass(
        work, steps, agents, recorder, session_id=session.id, user_id=user.id,
        pipeline=f"{compose.PREFIX}{row.id}", chat=chat, start=start, carried=carried,
        attempts=attempts, parent=parent, opening=None if parent is not None else {
            "routing": routing, "skills_event": skills_event, "context_steps": context_steps}))


async def _close_wait(run_id: str, *, approved: bool) -> None:
    """The waiting run answered: its waiting record done (the card's own closing says how)."""
    from app.routers import sessions

    if not approved:
        return  # a card is closed by `sessions._close_waiting_run`; a note re-plans
    try:
        async with sessions.SessionLocal() as db:
            run = await db.get(PipelineRun, run_id)
            if run is None or run.status != "waiting":
                return
            run.stages = [{**r, "status": "done", "summary": "개요 승인", "reason": ""}
                          if r.get("status") == "waiting" else r for r in run.stages or []]
            run.status, run.updated_at = "done", utcnow()
            db.add(run)
            await db.commit()
    except Exception:  # noqa: BLE001
        log.exception("waiting run %s not closed", run_id)


async def compose_pass(work: Bundle, steps: list[dict], agents: dict[str, compose.AgentInfo],
                       recorder: pipeline_runs.Recorder, *, session_id: str, user_id: str,
                       pipeline: str, chat: bool, start: str | None = None,
                       carried: dict | None = None, attempts: dict | None = None,
                       parent: PipelineRun | None = None, opening: dict | None = None):
    """One pass of a user pipeline from `start`: the records, then what it stored (or the
    card or outline it waits on), the usage and `done`."""
    from app.routers import sessions

    try:
        runner = Runner(compose.compile_steps(steps, agents), name=f"{pipeline}.{compose.FLOW}",
                        recorder=recorder)
        if attempts is not None:
            names = [s.name for s in runner.stages]
            again = names[names.index(start):] if start in names else []
            runner.attempts = {n: attempts.get(n, 1) + (n in again) for n in names}
        recorder.pipeline, recorder.flow = pipeline, compose.FLOW
        recorder.begin(work, carried=carried)
        if event := sessions._run_event(recorder.run_id, pipeline, compose.FLOW,
                                        parent_id=parent.id if parent else None,
                                        from_stage=start):
            yield _sse(event)
        if opening:
            if opening.get("routing"):
                yield _sse({"type": "privacy_route", **opening["routing"]})
            if opening.get("skills_event"):
                yield _sse(opening["skills_event"])
            for step in opening.get("context_steps") or ():
                yield _sse(sessions._step_event(step))
        try:
            async for event in runner.run(work, Context(run_id=recorder.run_id), start=start):
                if event.get("type") in ("usage", "report"):
                    continue
                yield _sse(event)
        except Exception as exc:  # noqa: BLE001 — the turn must still settle
            log.exception("pipeline %s crashed for session %s", pipeline, session_id)
            yield _sse(sessions._error_event("흐름을 끝까지 돌리지 못했습니다.", exc))
        finally:
            if parent is not None:
                sessions._carry_records(work, parent, start)
        waiting = next((r for r in work.steps if r.status == "waiting"), None)
        if waiting is not None:
            async for chunk in _settle_wait(work, recorder, waiting, session_id=session_id,
                                            user_id=user_id, parent=parent, start=start):
                yield chunk
            return
        saved = work.document.saved or {}
        await sessions._persist_run(work, recorder, session_id=session_id, user_id=user_id,
                                    saved=saved, parent=parent, from_stage=start)
        if saved.get("artifactId"):
            yield _sse({"type": "artifact", "artifactId": saved["artifactId"]})
        elif not saved.get("messageId"):
            yield _sse(sessions._error_event("흐름이 문서나 답을 남기지 못했습니다.",
                                             code="empty_pipeline"))
        # Every counter, planner's and reviewer's included: the line under the answer adds
        # them up (`web/src/lib/usage.ts`).
        yield _sse({"type": "usage", **compose.usage_counts(work.usage),
                    "credits": int(saved.get("credits") or 0)})
        yield _sse({"type": "done", **({"messageId": saved["messageId"]}
                                       if saved.get("messageId") and chat else {})})
    finally:
        if chat:
            await sessions._release_turn(session_id)


async def _settle_wait(work: Bundle, recorder: pipeline_runs.Recorder, waiting, *,
                       session_id: str, user_id: str, parent: PipelineRun | None,
                       start: str | None):
    """A human step waits: what the run spent so far billed, the card or the outline put up
    as the session's pending turn (the next message resumes the run), the run kept."""
    from app.routers import sessions

    turn = work.request.turn
    model, outline = turn["model"], turn.get("outline_model")
    usage = work.usage
    credits = sessions.charge_for_tokens(model, usage.get("inputTokens", 0),
                                         usage.get("outputTokens", 0))
    credits += sessions.charge_for_tokens(outline or model, usage.get("outlineInputTokens", 0),
                                          usage.get("outlineOutputTokens", 0))
    questions = list(waiting.evidence.get("questions") or [])
    plan = waiting.evidence.get("plan")
    kind = "outline" if plan else "clarify"
    said = ("이렇게 구성하려고 합니다. 확인해 주세요." if kind == "outline"
            else "시작하기 전에 확인할 것이 있습니다.")
    saved: dict[str, Any] = {"messageId": None, "credits": credits}
    async with sessions.SessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        user = await db.get(User, user_id)
        if session is not None and user is not None:
            session.pending = {
                "stage": kind, "request": work.request.text,
                "attachments": list(turn.get("attachments") or []), "answers": {},
                **({"questions": questions} if kind == "clarify" else {"plan": plan}),
                "runId": recorder.run_id,
                "compose": {"runId": recorder.run_id, "stage": waiting.stage, "kind": kind},
            }
            session.updated_at = utcnow()
            db.add(session)
            message = Message(session_id=session_id, role=Role.assistant, content=said,
                              usage={**compose.usage_counts(usage), "credits": credits},
                              model=model["id"],
                              routing={"askedCard": {"ids": [q.get("id") for q in questions],
                                                     "attachments": sorted(
                                                         turn.get("attachments") or [])}}
                              if kind == "clarify" else None)
            db.add(message)
            if credits:
                sessions.settle(db, user, credits, reason="document.plan", session_id=session_id,
                                model=model["id"], surface=session.kind.value)
            await db.commit()
            saved["messageId"] = message.id
    await compose._bill_other_models(work, saved)
    await sessions._persist_run(work, recorder, session_id=session_id, user_id=user_id,
                                saved=saved, parent=parent, from_stage=start)
    if kind == "outline":
        yield _sse({"type": "proposal", "plan": plan})
    else:
        yield _sse({"type": "needs", "questions": questions})
    yield _sse({"type": "usage", **compose.usage_counts(usage),
                "credits": int(saved.get("credits") or 0)})
    yield _sse({"type": "done"})


async def rerun(session: ChatSession, run: PipelineRun, *, start: str,
                answers: dict[str, str], user: User, db: DbSession, model: dict,
                api_key: str):
    """「이 단계부터 다시」 on a user pipeline's run: the steps it ran, from `start`."""
    stored = pipeline_runs.decode((run.params["request"].get("f") or {}).get("turn")) or {}
    definition = dict(stored.get("compose") or {})
    steps = compose.normalize(definition.get("stages"))
    agents = await agents_for(db, user)
    if any(s["kind"] == "agent" and s["ref"] not in agents for s in steps) or \
            not compose.validate(steps, agents=agents).ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_not_rerunnable")
    names = [s.name for s in compose.compile_steps(steps, agents)]
    if start not in names:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="stage_not_rerunnable")
    try:
        work = pipeline_runs.restore(run.params["request"], run.snapshots, before=start,
                                     api_key=api_key)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="stage_not_rerunnable") from None
    recorder = pipeline_runs.Recorder(run_id=uuid4().hex)
    work.usage = {"inputTokens": 0, "outputTokens": 0}
    titles = {s.name: getattr(s, "title", "") for s in compose.compile_steps(steps, agents)}
    work.request.turn = {**stored, "user_id": user.id, "model": model,
                         "run_id": recorder.run_id, "rerun_from": start,
                         "rerun_label": titles.get(start, ""),
                         **({"resume": start} if answers else {})}
    work.request.model = model["id"]
    work.request.session_id = session.id
    if answers:
        work.answers = {**work.answers, **answers}
    earlier = {str(r.get("stage")): int(r.get("attempt") or 1) for r in run.stages or []}
    return compose_pass(work, steps, agents, recorder, session_id=session.id, user_id=user.id,
                        pipeline=run.pipeline, chat=False, start=start,
                        carried=pipeline_runs.carried(run.snapshots, before=start),
                        attempts={n: earlier.get(n, 1) for n in names}, parent=run)


__all__ = ["agents_for", "bound", "compose_pass", "rerun", "review_models", "router",
           "turn_response"]

