"""The pipeline canvas e2e answers with `apps/web/e2e/fixtures/chain-canvas.json`; it must be what
this API sends: the catalogue, the graph problem sentences and a run read per step."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from app.models.chat import PipelineRun
from app.pipeline import compose
from app.pipeline import runs as pipeline_runs
from app.routers import user_pipelines as up

_FIXTURE = Path(__file__).resolve().parents[3] / "apps/web/e2e/fixtures/chain-canvas.json"
_CODES = ("unreachable_step", "branch_dead_end", "condition_incomplete", "parallel_next", "cycle",
          "needs_agent_after", "needs_product")
_AT = dt.datetime(2026, 10, 20, 5, 0, tzinfo=dt.UTC)

#: The canvas spec's saved chain: 회의록 정리 → 근거 확인 → 후속 메일 초안.
_STEPS = [
    {"id": "s1", "kind": "agent", "ref": "c1111111111111111111111111111111"},
    {"id": "s2", "kind": "check", "ref": "grounding-check"},
    {"id": "s3", "kind": "agent", "ref": "c2222222222222222222222222222222"},
]


def _record(stage: str, label: str, status: str, summary: str = "", reason: str = "") -> dict:
    return {"stage": stage, "label": label, "status": status, "summary": summary, "reason": reason,
            "startedAt": _AT.isoformat(), "finishedAt": _AT.isoformat()}


def _run(run_id: str, number: int, status: str, text: str, records: list[dict]) -> dict:
    turn = {"compose": {"stages": compose.normalize(_STEPS)}, "attachments": [{"name": "메모.txt"}]}
    row = PipelineRun(id=run_id, session_id="a5555555555555555555555555555555", user_id="u",
                      pipeline="user:p1", status=status, stages=records,
                      params={"request": {"f": {"text": text, "turn": pipeline_runs.encode(turn)}}},
                      created_at=_AT, updated_at=_AT)
    return up.run_out(row, number)


def _fixture() -> dict:
    return {
        "catalogue": compose.catalogue(),
        "kindWords": compose.KIND_WORDS,
        "messages": {code: compose.MESSAGES[code] for code in _CODES},
        "productFixes": compose._PRODUCT_FIX,
        "runs": [
            _run("r2", 2, "waiting", "주간회의 메모로 회의록을 정리하고 후속 메일까지 써 줘", [
                _record("1:agent", "회의록 정리", "done", "회의록 1부"),
                _record("2:check", "근거 확인", "done", "메모에 없는 문장 2곳 표시"),
                _record("3:agent", "후속 메일 초안", "skipped", reason="stopped at 3:agent"),
            ]),
            _run("r1", 1, "failed", "기획회의 메모 정리", [
                _record("1:agent", "회의록 정리", "done", "회의록 1부"),
                _record("2:check", "근거 확인", "failed"),
            ]),
        ],
    }


def test_the_canvas_e2e_fixture_is_what_the_api_sends():
    want = _fixture()
    if os.environ.get("WRITE_CHAIN_FIXTURE"):
        _FIXTURE.write_text(json.dumps(want, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert json.loads(_FIXTURE.read_text(encoding="utf-8")) == json.loads(json.dumps(want))
