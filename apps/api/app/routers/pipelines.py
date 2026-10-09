"""Read-only views of the pipelines and of what a run left on a document.

- `GET /pipelines`, `GET /pipelines/{name}` — the pipeline files (`app/pipelines/*.toml`):
  each flow's stages with their Korean label, kind and the check skills they run;
- `GET /agents/{id}/pipeline` — the pipeline a workspace agent's turns run;
- `GET /artifacts/{id}/marks` — the spans to highlight (`app.pipeline.marks`), found again
  in the body as it stands now.

Editing a pipeline is not offered here (phase 3).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlmodel import col, select

from app.core.deps import CurrentUser, DbSession
from app.models.chat import PipelineRun
from app.models.workspace import Agent, Artifact, ArtifactKind, Visibility
from app.pipeline import marks as doc_marks
from app.pipeline import registry
from app.pipeline.labels import label
from app.routers import sessions
from app.services import check_skills, skill_packages

router = APIRouter(tags=["pipelines"])

_FLOWS = ("pre", "plan", "write", "finish", "revise", "make")
_LISTS = ("prepass", "checks", "judged")

#: People's names for the format code checks, judged checks and pre-pass steps a pipeline file
#: lists; a check skill package says its own name (SKILL.md `name`).
_CHECK_NAMES = {
    "quote-totals": "견적 합계 확인",
    "proposal-schedule": "제안 일정 확인",
    "gaejo-style": "개조식 문체 확인",
    "lead-dates": "날짜 확인",
    "minutes-rows": "할 일의 담당자·기한 확인",
    "compare-cells": "비교표 빈칸·점수 확인",
    "career-numbers": "경력 수치 확인",
    "staff-counts": "투입 인원 확인",
    "material-blanks": "자료로 채울 빈칸 찾기",
    "judge-owners": "담당자 확인",
    "judge-bindings": "수치와 대상 연결 확인",
    "judge-repeats": "되풀이된 경험 찾기",
    "proposal-rfp": "제안요청서 조건 읽기",
    "staffing": "투입 인력 읽기",
    "compare-cost": "대안별 총비용 계산",
    "compare-check": "필수 조건·점수 계산",
    "career-periods": "경력 기간 계산",
}


def check_name(key: str) -> str:
    """A check's or pre-pass step's name as a person reads it; the id when none is known."""
    package = skill_packages.package_for(key)
    return (package.name if package and package.name else "") or _CHECK_NAMES.get(key, key)


def _stage_checks(name: str, pipe: registry.Pipeline, attached: tuple[str, ...]) -> list[str]:
    """The checks a stage runs: check skills for the `check:` stages, the format's code and
    judged checks for the stages that run them."""
    if name == "check:gaps":
        return check_skills.of_stage(attached, "pre")
    if name.startswith("check:"):
        return check_skills.of_stage(attached, "post")
    if name in ("format-find", "format-amend"):
        return list(pipe.flow("checks"))
    if name == "verify":
        return [*pipe.flow("checks"), *pipe.flow("judged")]
    if name == "prepass":
        return list(pipe.flow("prepass"))
    return []


def describe(pipe: registry.Pipeline, attached: tuple[str, ...] = ()) -> dict[str, Any]:
    stages = registry.stages()
    flows = {}
    for flow in _FLOWS:
        names = pipe.flow(flow)
        if not names:
            continue
        flows[flow] = [
            {"name": n, "label": label(n), "kind": getattr(stages.get(n), "kind", ""),
             "checks": _stage_checks(n, pipe, attached)}
            for n in names
        ]
    lists = {k: list(pipe.flow(k)) for k in _LISTS if pipe.flow(k)}
    named = [*attached, *(c for rows in flows.values() for s in rows for c in s["checks"]),
             *(c for rows in lists.values() for c in rows)]
    return {
        "name": pipe.name, "formats": list(pipe.formats), "agents": list(pipe.agents),
        "options": dict(pipe.options), "checks": list(attached), "flows": flows,
        "lists": lists, "summary": dict(pipe.summary),
        "checkNames": {k: check_name(k) for k in dict.fromkeys(named)},
    }


@router.get("/pipelines")
async def list_pipelines(user: CurrentUser):
    return [{"name": p.name, "formats": list(p.formats), "agents": list(p.agents),
             "flows": [f for f in _FLOWS if p.flow(f)]}
            for p in registry.pipelines().values()]


@router.get("/pipelines/{name}")
async def get_pipeline(name: str, user: CurrentUser):
    try:
        pipe = registry.pipeline(name)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="pipeline_not_found") from None
    return describe(pipe)


def pipeline_for_agent(key: str | None, doc_format: str | None, surface: str | None
                       ) -> registry.Pipeline:
    """The pipeline an agent's turns run: its bound format's, else the one naming the agent,
    else the surface's default."""
    if doc_format:
        return registry.for_report(doc_format)
    if key:
        named = [p for p in registry.pipelines().values() if key in p.agents]
        if named:
            return next((p for p in named if p.flow("write")), named[0])
    return registry.pipeline("report" if surface == "report" else "chat")


@router.get("/agents/{agent_id}/pipeline")
async def get_agent_pipeline(agent_id: str, user: CurrentUser, db: DbSession):
    agent = await db.get(Agent, agent_id)
    if agent is None or (agent.owner_id != user.id and agent.visibility != Visibility.org):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    key = await sessions._agent_catalog_key(db, agent)
    pipe = pipeline_for_agent(key, getattr(agent, "doc_format", None),
                              getattr(agent, "surface", None))
    return {**describe(pipe, check_skills.attached(agent_key=key)), "agentId": agent.id,
            "agentKey": key}


@router.get("/artifacts/{artifact_id}/marks")
async def get_marks(artifact_id: str, user: CurrentUser, db: DbSession):
    artifact = await db.get(Artifact, artifact_id)
    if artifact is None or artifact.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    data = artifact.data or {}
    sections = list(data.get("sections") or []) if artifact.kind is ArtifactKind.report else []
    run = (await db.exec(
        select(PipelineRun.id)
        .where(PipelineRun.artifact_id == artifact.id, PipelineRun.user_id == user.id)
        .order_by(col(PipelineRun.created_at).desc())
        .limit(1)
    )).first()
    return {"artifactId": artifact.id, "version": artifact.version, "runId": run,
            "sections": doc_marks.relocate(data.get("marks"), sections)}


__all__ = ["describe", "pipeline_for_agent", "router"]
