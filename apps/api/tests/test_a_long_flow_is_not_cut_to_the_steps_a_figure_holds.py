"""A flow longer than a fixed shape holds is drawn as a flowchart (with its loop back), not cut;
a step's own number is not written into its name; a concept is never a talk or a line."""

from __future__ import annotations

import json

import pytest

from app.services import diagram, figure_kinds

_SEVEN = ["수집", "정제", "라벨링", "분할", "학습", "평가", "배포"]


def test_seven_steps_are_not_cut_to_a_figure_of_fewer():
    assert figure_kinds.spec({"kind": "steps", "steps": _SEVEN}) is None
    assert figure_kinds.spec({"kind": "steps", "steps": _SEVEN[:6]}) is not None


def test_six_stages_are_not_cut_to_five():
    stages = [{"name": f"단계 {k}", "items": ["가", "나"]} for k in range(6)]
    assert figure_kinds.spec({"kind": "stages", "stages": stages}) is None
    assert figure_kinds.spec({"kind": "stages", "stages": stages[:5]}) is not None


def test_a_number_written_into_a_step_name_is_taken_off_it():
    got = figure_kinds.spec({"kind": "steps", "steps": [
        "1단계 데이터 수집", "2. 정제", "Step 3: Label", "4) 학습"]})
    assert [s["name"] for s in got["steps"]] == ["데이터 수집", "정제", "Label", "학습"]


def _answers(monkeypatch, kind: str, filled: str, mermaid: str) -> list[str]:
    asked: list[str] = []

    async def complete(_model, messages, _key, max_tokens):
        system = messages[0]["content"]
        step = "choose" if system.startswith("Pick") else "fill" if "fill the slots" in system \
            else "mermaid"
        asked.append(step)
        return {"choose": kind, "fill": filled, "mermaid": mermaid}[step], {}

    monkeypatch.setattr(diagram, "_complete", complete)
    return asked


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["messages", "steps"])
async def test_a_concept_figure_is_never_drawn_as_a_talk_or_a_line_of_phases(monkeypatch, kind):
    slots = {"messages": {"kind": "messages", "parties": ["질문", "검색기"], "messages": [
        {"from": "질문", "to": "검색기", "name": "q"}, {"from": "검색기", "to": "질문",
                                                       "name": "d"}]},
             "steps": {"kind": "steps", "steps": ["질문", "검색기", "문서"]}}[kind]
    filled = "```json\n" + json.dumps(slots, ensure_ascii=False) + "\n```\n그림: RAG"
    mermaid = "```mermaid\nflowchart TB\n  a[질문] --> b(검색기)\n```\n그림: RAG"
    asked = _answers(monkeypatch, kind, filled, mermaid)
    source, _caption, _usage = await diagram.draw(
        description="질문이 검색기와 벡터 저장소를 거쳐 답이 된다", figure="concept",
        model="m", api_key="k")
    assert asked == ["choose", "mermaid"]  # the slots were never asked for
    assert figure_kinds.from_source(source) is None and source.startswith("flowchart TB")
