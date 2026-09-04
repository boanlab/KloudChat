"""Video jobs: one poll task per job; jobs left running by a restart are
recovered on the next list request via `provider_job_id`."""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlmodel import col, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.deps import CurrentUser, DbSession
from app.models.chat import ChatSession
from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind, Job, JobStatus, StoredFile
from app.schemas.chat import JobOut, VideoJobRequest
from app.services import chat as chat_service
from app.services import files as file_service
from app.services import litellm as litellm_service
from app.services import models as model_service
from app.services import settings_store, videogen
from app.services.credits import settle

log = logging.getLogger(__name__)

router = APIRouter(tags=["jobs"])

_POLL_SECONDS = 6.0
#: Past this the job is marked failed rather than left on "running".
_MAX_WAIT_SECONDS = 900.0

_CREDITS_PER_USD = settings.credits_per_usd

_running: set[asyncio.Task] = set()
#: Job ids with a loop behind them, so recovery does not start a second one.
_watching: set[str] = set()


def _watch(job_id: str) -> None:
    task = asyncio.create_task(_poll_until_done(job_id))
    _running.add(task)
    _watching.add(job_id)

    def _done(finished: asyncio.Task) -> None:
        _running.discard(finished)
        _watching.discard(job_id)

    task.add_done_callback(_done)


async def _finish(job_id: str, **fields) -> None:
    async with SessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is None:
            return
        # A settled job is final; two loops can exist for one clip after a restart.
        if job.status in (JobStatus.succeeded, JobStatus.failed, JobStatus.canceled):
            return
        for key, value in fields.items():
            setattr(job, key, value)
        db.add(job)
        await db.commit()


async def _poll_until_done(job_id: str) -> None:
    # Instance key: a worker outliving the request must not hold the caller's.
    base_url, master_key = await settings_store.litellm_config()
    waited = 0.0
    while waited < _MAX_WAIT_SECONDS:
        await asyncio.sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS

        async with SessionLocal() as db:
            job = await db.get(Job, job_id)
            if job is None or job.status in (JobStatus.canceled, JobStatus.failed):
                return
            provider_id = job.provider_job_id
            user_id, session_id, prompt = job.user_id, job.session_id, job.prompt
            model_id, params = job.model, dict(job.params or {})
            estimated = job.credits_estimated
        if not provider_id:
            return

        progress = await videogen.poll(
            base_url=base_url, master_key=master_key, provider_job_id=provider_id
        )

        if progress.status in ("failed", "error", "canceled"):
            # Not charged: the upstream does not bill for an undelivered clip.
            # The job row carries the failure; the prompt message is not marked.
            await _finish(
                job_id,
                status=JobStatus.failed,
                error=progress.error or "영상을 만들지 못했습니다.",
                stage="실패",
                finished_at=_now(),
            )
            return

        if progress.status in ("completed", "succeeded", "success") and progress.url:
            try:
                data = await videogen.fetch(
                    base_url=base_url, master_key=master_key, provider_job_id=provider_id
                )
            except videogen.VideoError as exc:
                await _finish(job_id, status=JobStatus.failed, error=str(exc), finished_at=_now())
                return

            file_id = uuid.uuid4().hex
            key = file_service.write_blob(user_id, file_id, "video.mp4", data)
            async with SessionLocal() as db:
                user = await db.get(User, user_id)
                job = await db.get(Job, job_id)
                if user is None or job is None:
                    return
                db.add(
                    StoredFile(
                        id=file_id,
                        user_id=user_id,
                        session_id=session_id,
                        name=f"{prompt[:40] or 'video'}.mp4",
                        mime="video/mp4",
                        size=len(data),
                        storage_key=key,
                        tokens=0,
                    )
                )
                artifact = Artifact(
                    user_id=user_id,
                    session_id=session_id,
                    kind=ArtifactKind.video,
                    title=prompt[:200] or "영상",
                    data={
                        "kind": "video",
                        "jobId": job_id,
                        "prompt": prompt,
                        "model": model_id,
                        "aspect": params.get("aspect", "16:9"),
                        "durationSec": params.get("seconds", 0),
                        "posterSrc": "",
                        "src": f"{settings.api_prefix}/files/{file_id}/content",
                    },
                )
                db.add(artifact)
                await db.flush()
                session = await db.get(ChatSession, session_id)
                if session is not None:
                    # Linked on delivery: before that there is no artifact to point at.
                    session.artifact_id = artifact.id
                    session.updated_at = _now()
                    db.add(session)
                # Charged on delivery; the upstream's reported cost wins over the estimate.
                charged = (
                    round(progress.cost_usd * _CREDITS_PER_USD) if progress.cost_usd else estimated
                )
                settle(
                    db,
                    user,
                    charged,
                    reason="video.generate",
                    session_id=session_id,
                    model=model_id,
                    surface="av",
                )
                # The answer row is written on delivery, under the prompt that asked.
                db.add(
                    chat_service.media_answer(
                        session_id,
                        [artifact.id],
                        model=model_id,
                        credits=charged,
                    )
                )
                job.status = JobStatus.succeeded
                job.artifact_id = artifact.id
                job.credits_used = charged
                job.progress = 100
                job.stage = "완료"
                job.finished_at = _now()
                db.add(job)
                await db.commit()
            return

        await _finish(
            job_id,
            status=JobStatus.running,
            progress=max(1, progress.progress),
            stage="만드는 중",
        )

    await _finish(
        job_id,
        status=JobStatus.failed,
        error="시간 안에 끝나지 않았습니다.",
        stage="시간 초과",
        finished_at=_now(),
    )


def _now():
    from app.models.user import utcnow

    return utcnow()


@router.post("/sessions/{session_id}/jobs", response_model=JobOut)
async def create_job(session_id: str, payload: VideoJobRequest, user: CurrentUser, db: DbSession):
    """Starts a video and returns immediately. The row is committed before the poll loop starts."""
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")

    catalogue = await model_service.list_models()
    model = model_service.find(catalogue["models"], payload.model or "")
    model_id = (model or {}).get("id") or payload.model or ""
    cost = videogen.price_usd(
        model_id,
        resolution=payload.resolution,
        seconds=payload.seconds,
        audio=payload.audio,
    )
    if cost is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported_video_combo"
        )
    estimated = round(cost * _CREDITS_PER_USD)
    if user.credits_remaining < estimated:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="insufficient_credits"
        )

    await litellm_service.ensure_key(user)
    if db.is_modified(user):
        db.add(user)
        await db.commit()
    base_url, api_key = await litellm_service.credentials_for(user)

    try:
        submitted = await videogen.submit(
            base_url=base_url,
            api_key=api_key,
            user_id=user.id,
            model=model_id,
            prompt=payload.prompt,
            resolution=payload.resolution,
            seconds=payload.seconds,
            audio=payload.audio,
            aspect=payload.aspect,
        )
    except videogen.VideoError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    if not session.title:
        session.title = chat_service.provisional_title(payload.prompt)
    # Title and prompt are written at submission; the answer arrives on delivery.
    db.add(chat_service.media_prompt(session.id, payload.prompt))
    session.updated_at = _now()
    db.add(session)

    job = Job(
        user_id=user.id,
        session_id=session.id,
        kind="av",
        status=JobStatus.running,
        prompt=payload.prompt,
        model=model_id,
        params={
            "resolution": payload.resolution,
            "seconds": payload.seconds,
            "audio": payload.audio,
            "aspect": payload.aspect,
        },
        provider_job_id=submitted.provider_job_id,
        credits_estimated=estimated,
        stage="만드는 중",
        progress=1,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    _watch(job.id)
    return JobOut.of(job)


@router.get("/sessions/{session_id}/jobs", response_model=list[JobOut])
async def list_jobs(session_id: str, user: CurrentUser, db: DbSession):
    """This session's jobs, newest first. Also restarts poll loops lost to a restart."""
    rows = (
        await db.exec(
            select(Job)
            .where(Job.session_id == session_id, Job.user_id == user.id)
            .order_by(col(Job.created_at).desc())
        )
    ).all()

    for job in rows:
        if job.status == JobStatus.running and job.provider_job_id and job.id not in _watching:
            _watch(job.id)
    return [JobOut.of(j) for j in rows]


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str, user: CurrentUser, db: DbSession):
    """Stops watching locally; the upstream is not cancelled, only the on-delivery charge."""
    job = await db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if job.status in (JobStatus.succeeded, JobStatus.failed, JobStatus.canceled):
        return JobOut.of(job)
    job.status = JobStatus.canceled
    job.stage = "취소됨"
    job.finished_at = _now()
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return JobOut.of(job)
