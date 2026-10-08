"""Pipeline runs of a session: read them, and run one again from a stage (「이 단계부터 다시」).

A run (`PipelineRun`) keeps the work bundle after each stage (`app.pipeline.runs`). A rerun
restores the bundle as it stood before the chosen stage, adds the answers given with it as
material, and runs that stage and every one after it again; the stages before it are not
run and their records are carried over. The result is stored the way the turn stored it —
a new version of the same artifact, a message, the bill — by the flow's own save stage.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlmodel import col, select

from app.core.deps import CurrentUser, DbSession
from app.models.chat import PipelineRun, SessionKind
from app.pipeline import registry as pipeline_registry
from app.pipeline import runs as pipeline_runs
from app.pipeline.bundle import Part
from app.pipeline.runner import Runner
from app.pipeline.stage import Context, StepRecord
from app.routers import sessions
from app.schemas.auth import Wire
from app.services import chat as chat_service
from app.services import check_skills, governance
from app.services import litellm as litellm_service
from app.services import models as model_service
from app.services.credits import has_headroom

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["runs"])

#: Flows whose runs keep snapshots and store their own result.
RERUNNABLE = ("write", "revise", "make")
_MAX_ANSWER = 2000


class RerunIn(Wire):
    from_stage: str = Field(min_length=1, max_length=80)
    #: Answers to a gap card, by question id (`gap:<fact>`), merged as material.
    answers: dict[str, str] | None = None


def _out(run: PipelineRun, *, evidence: bool) -> dict[str, Any]:
    stages = [StepRecord.from_dict(row) for row in run.stages or []]
    for record in stages:
        record.run_id = record.run_id or run.id
    can = set(pipeline_runs.rerunnable_from(run.snapshots)) if run.flow in RERUNNABLE else set()
    for record in stages:
        record.rerunnable = record.stage in can
    return {
        "id": run.id, "sessionId": run.session_id, "messageId": run.message_id,
        "artifactId": run.artifact_id, "artifactVersion": run.artifact_version,
        "pipeline": run.pipeline, "flow": run.flow, "status": run.status,
        "parentId": run.parent_id, "fromStage": run.from_stage,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
        "updatedAt": run.updated_at.isoformat() if run.updated_at else None,
        "stages": [r.stage_wire(evidence=evidence) for r in stages],
    }


async def _owned_run(db: DbSession, user: Any, session_id: str, run_id: str):
    session = await sessions._owned(db, user, session_id)
    run = await db.get(PipelineRun, run_id)
    # Same answer for "missing" and "someone else's".
    if run is None or run.session_id != session.id or run.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run_not_found")
    return session, run


@router.get("/{session_id}/runs")
async def list_runs(session_id: str, user: CurrentUser, db: DbSession, limit: int = 20):
    session = await sessions._owned(db, user, session_id)
    rows = (await db.exec(
        select(PipelineRun)
        .where(PipelineRun.session_id == session.id, PipelineRun.user_id == user.id)
        .order_by(col(PipelineRun.created_at).desc())
        .limit(max(1, min(limit, 50)))
    )).all()
    return [_out(row, evidence=False) for row in rows]


@router.get("/{session_id}/runs/{run_id}")
async def get_run(session_id: str, run_id: str, user: CurrentUser, db: DbSession):
    _, run = await _owned_run(db, user, session_id, run_id)
    return _out(run, evidence=True)


def answers_part(answers: dict[str, str], gaps: list[dict]) -> Part | None:
    """The answers given with a rerun, as material the writer and the checks read:
    「회의 일시: 10월 7일」 by the gap card's own labels."""
    asked = [{"id": f"{check_skills.GAP_PREFIX}{g.get('fact')}"} for g in gaps or []
             if isinstance(g, dict)]
    labels = {f"{check_skills.GAP_PREFIX}{g.get('fact')}": str(g.get("label") or "")
              for g in gaps or [] if isinstance(g, dict)}
    shown = check_skills.labelled(answers, asked)
    lines = []
    for qid, text in shown.items():
        text = str(text or "").strip()
        if not text:
            continue
        if labels.get(qid) and not text.startswith(f"{labels[qid]}:"):
            text = f"{labels[qid]}: {text}"
        lines.append(f"- {text}")
    if not lines:
        return None
    return Part("# 확인 질문에 대한 답\n" + "\n".join(lines), "answer", "answers")


@router.post("/{session_id}/runs/{run_id}/rerun")
async def rerun(session_id: str, run_id: str, payload: RerunIn, user: CurrentUser,
                db: DbSession):
    session, run = await _owned_run(db, user, session_id, run_id)
    if run.flow not in RERUNNABLE or not run.snapshots or not (run.params or {}).get("request"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_not_rerunnable")
    start = payload.from_stage
    if start not in pipeline_runs.rerunnable_from(run.snapshots):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="stage_not_rerunnable")
    try:
        pipe = pipeline_registry.pipeline(run.pipeline)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="run_not_rerunnable") from None
    if session.kind not in (SessionKind.report, SessionKind.slides):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_not_rerunnable")
    if run.artifact_id and session.artifact_id and run.artifact_id != session.artifact_id:
        # The document on screen is another one now; a rerun would land on it.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run_not_current")
    if any(not signal.is_set() for signal in sessions._STOPPING.get(session.id, set())):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session_busy")

    stored_turn = pipeline_runs.decode((run.params["request"].get("f") or {}).get("turn")) or {}
    model_id = str((stored_turn.get("model") or {}).get("id") or "")
    catalogue = (await model_service.list_models_for_egress())["models"]
    usable = sessions._allowed_models(user, catalogue, kind=session.kind.value)
    model = next((m for m in usable if m["id"] == model_id), None)
    if model is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="model_unavailable")
    if not has_headroom(user, model):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="insufficient_credits")

    answers = {str(k)[:120]: str(v or "")[:_MAX_ANSWER]
               for k, v in (payload.answers or {}).items()}
    policy = await sessions._require_egress_policy()
    said = "\n".join(answers.values())
    if said and policy.intent_filter and (
            hit := governance.blocked_by(said, policy.blocked_categories)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"blocked_category:{hit}")
    if said and policy.pii_masking:
        # Report and slides always mask, as a typed answer is masked on the card.
        answers = {k: governance.mask_legacy(v)[0] for k, v in answers.items()}

    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    _, api_key = await litellm_service.credentials_for(user)
    try:
        work = pipeline_runs.restore(run.params["request"], run.snapshots, before=start,
                                     api_key=api_key)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="stage_not_rerunnable") from None

    recorder = pipeline_runs.Recorder(run_id=uuid4().hex)
    recorder.pipeline, recorder.flow = run.pipeline, run.flow
    # Only this pass's tokens are billed.
    work.usage = {k: 0 for k in work.usage} or {"inputTokens": 0, "outputTokens": 0}
    work.usage.setdefault("inputTokens", 0)
    work.usage.setdefault("outputTokens", 0)
    work.request.turn = {**stored_turn, "user_id": user.id, "model": model,
                         "run_id": recorder.run_id, "rerun_from": start}
    work.request.model = model["id"]
    work.request.session_id = session.id
    if answers:
        work.answers = {**work.answers, **answers}
        if part := answers_part(answers, work.findings.gaps):
            work.material.parts = [*work.material.parts, part]
    carried = pipeline_runs.carried(run.snapshots, before=start)
    names = [s.name for s in (sessions._revise_stages(pipe) if run.flow == "revise"
                              else pipe.stages(run.flow))]
    earlier = {str(r.get("stage")): int(r.get("attempt") or 1) for r in run.stages or []}
    after = names[names.index(start):] if start in names else []
    attempts = {n: earlier.get(n, 1) + (1 if n in after else 0) for n in names}

    if run.flow == "make":
        stream = sessions._make_pass(work, pipe, crashed="문서를 만들지 못했습니다.", start=start,
                                     recorder=recorder, carried=carried, attempts=attempts,
                                     parent=run)
    elif run.flow == "revise":
        stream = sessions._revise_pass(work, pipe, recorder, session_id=session.id,
                                       user_id=user.id, start=start, carried=carried,
                                       attempts=attempts, parent=run)
    else:
        stream = _write_pass(work, pipe, recorder, session_id=session.id, user_id=user.id,
                             start=start, carried=carried, attempts=attempts, parent=run)
    return StreamingResponse(
        sessions._survive_disconnect(_announced(stream, recorder, run, start)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


async def _announced(stream, recorder: pipeline_runs.Recorder, parent: PipelineRun, start: str):
    if event := sessions._run_event(recorder.run_id, recorder.pipeline, recorder.flow,
                                    parent_id=parent.id, from_stage=start):
        yield chat_service.sse(event)
    routing = (recorder.request.get("f") or {}).get("turn", {}).get("routing")
    if isinstance(routing, dict) and routing:
        yield chat_service.sse({"type": "privacy_route", **routing})
    async for chunk in stream:
        yield chunk


async def _write_pass(work, pipe, recorder: pipeline_runs.Recorder, *, session_id: str,
                      user_id: str, start: str, carried: dict, attempts: dict,
                      parent: PipelineRun):
    """A report's `write` flow from `start`, then what the stored report says."""
    runner = Runner(pipe.stages("write"), name=f"{pipe.name}.write", recorder=recorder)
    runner.attempts = attempts
    recorder.begin(work, carried=carried)
    usage = work.usage
    try:
        async for event in runner.run(work, Context(options=dict(pipe.options),
                                                    run_id=recorder.run_id), start=start):
            if event.get("type") == "report":
                continue
            if event.get("type") == "usage":
                usage = {k: v for k, v in event.items() if k != "type"}
                continue
            yield chat_service.sse(event)
    except Exception as exc:  # noqa: BLE001 — the turn must still settle
        log.exception("report rerun crashed for session %s", session_id)
        yield chat_service.sse(sessions._error_event("보고서를 만들지 못했습니다.", exc))
    finally:
        sessions._carry_records(work, parent, start)
    async for chunk in sessions._report_tail(work, recorder=recorder, session_id=session_id,
                                             user_id=user_id, usage=usage, parent=parent,
                                             from_stage=start):
        yield chunk


__all__ = ["RERUNNABLE", "answers_part", "router"]
