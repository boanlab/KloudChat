"""The report's draft leaves the hard sections out; the upper model writes them one by one,
and a section whose check fails is written again by the repair stage."""

from __future__ import annotations

import pytest
from test_documents_are_written_in_stages import CATALOGUE, GLM, LOCAL_27B, TABLE

from app.pipeline import report_stages
from app.pipeline.bundle import Bundle, Request
from app.pipeline.stage import Context
from app.services import doc_stages, model_health, report


@pytest.fixture(autouse=True)
def _clean():
    model_health.reset()
    doc_stages.CURRENT.set(None)
    yield
    doc_stages.CURRENT.set(None)


def _bundle() -> Bundle:
    bundle = Bundle(request=Request(text="분기 실적 보고서 " + TABLE, model=LOCAL_27B["id"],
                                    api_key="k"))
    bundle.plan.headings = ["요약", "배경", "결론"]
    bundle.document.sections = [{"id": f"s{i}", "heading": h, "content": ""}
                                for i, h in enumerate(bundle.plan.headings)]
    return bundle


@pytest.mark.asyncio
async def test_the_draft_leaves_hard_sections_out(monkeypatch) -> None:
    doc_stages.CURRENT.set(doc_stages.from_ids({"hard": GLM["id"]}, CATALOGUE))
    prompts: list[str] = []

    async def fake(model, messages, api_key, max_tokens):
        prompts.append(str(messages[-1]["content"]))
        return "## 배경\n배경 본문입니다. 둘째 문장입니다.\n", {"inputTokens": 5,
                                                              "outputTokens": 5}

    monkeypatch.setattr(report, "_complete", fake)
    bundle = _bundle()
    async for _ in report_stages.Draft().run(bundle, Context(options={})):
        pass
    assert len(bundle.document.hard) == 1
    assert set(bundle.document.drafted) <= {"배경"}
    assert "written separately by another writer" in prompts[0]
    assert bundle.usage["stageTriggers"]["hard"]


@pytest.mark.asyncio
async def test_without_an_upper_model_the_draft_is_what_it_was(monkeypatch) -> None:
    prompts: list[str] = []

    async def fake(model, messages, api_key, max_tokens):
        prompts.append(str(messages[-1]["content"]))
        return "## 요약\n가\n## 배경\n나\n## 결론\n다\n", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", fake)
    bundle = _bundle()
    async for _ in report_stages.Draft().run(bundle, Context(options={})):
        pass
    assert bundle.document.hard == {} and "written separately" not in prompts[0]


@pytest.mark.asyncio
async def test_a_repair_call_goes_up_and_falls_back_to_the_writer(monkeypatch) -> None:
    stages = doc_stages.from_ids({"repair": GLM["id"]}, CATALOGUE)
    doc_stages.CURRENT.set(stages)
    asked: list[str] = []

    async def fake(model, messages, api_key, max_tokens):
        asked.append(model)
        return "고친 글", {"inputTokens": 4, "outputTokens": 2}

    monkeypatch.setattr(report, "_complete", fake)
    bundle = _bundle()
    text, spent = await report_stages._staged(
        bundle, "repair", lambda model: [{"role": "user", "content": model}], 100)
    assert text == "고친 글" and asked == [GLM["id"]] and spent["inputTokens"] == 0
    assert bundle.usage["stageUsage"]["repair"]["inputTokens"] == 4
    stages.down.add(GLM["id"])
    await report_stages._staged(bundle, "repair", lambda model: [], 100)
    assert asked[-1] == LOCAL_27B["id"]
