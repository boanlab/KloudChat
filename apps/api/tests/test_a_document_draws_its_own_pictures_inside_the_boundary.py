"""With an image model served inside the data boundary, decks and reports plan and draw their
own pictures after writing, as they do diagrams; an outside model is never used for that."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.pipeline import report_stages
from app.routers import sessions as sessions_router
from app.services import deck, illustrations, imagegen

_LOCAL = {"id": "strict-local/z-image-turbo", "dataBoundary": "self_hosted", "kinds": ["image"],
          "creditCost": 0}
_OUTSIDE = {"id": "bytedance-seed/seedream-5-0-flash", "dataBoundary": "external", "creditCost": 4,
            "kinds": ["image"]}
_PICK = "where a picture shows what the words cannot"


def _drawn(calls: list[dict]):
    async def generate(**kwargs):
        calls.append(kwargs)
        return imagegen.GeneratedImage(data=b"\x89PNG", mime="image/png", input_tokens=0,
                                       output_tokens=0, width=1024, height=768)
    return generate


# ── planning ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pictures_are_planned_only_on_eligible_parts_and_capped():
    seen: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        seen.append(messages[-1]["content"])
        answer = [{"part": 1, "prompt": "an office", "caption": "사무실"},
                  {"part": 2, "prompt": "a table", "caption": "표"},
                  {"part": 3, "prompt": "colleagues sharing one desk", "caption": "함께 쓰는 자리"},
                  {"part": 3, "prompt": "again", "caption": "또"}]
        return "물론입니다:\n" + json.dumps(answer, ensure_ascii=False), {"inputTokens": 4,
                                                                      "outputTokens": 2}

    parts = [("표지", "도입"), ("비교", "| a | b |"), ("함께 쓰기", "모든 대화가 한곳에 모인다")]
    planned, spent = await illustrations.plan(
        parts=parts, eligible=[0, 2], request="발표", model="m", api_key="k",
        complete=complete, slide=True, limit=2)
    assert [(p.index, p.caption) for p in planned] == [(0, "사무실"), (2, "함께 쓰는 자리")]
    assert spent == {"inputTokens": 4, "outputTokens": 2}
    # Only the eligible parts are shown to the planner.
    assert "모든 대화가 한곳에" in seen[0] and "| a | b |" not in seen[0]


@pytest.mark.asyncio
async def test_no_eligible_part_means_no_call_and_a_failure_means_no_pictures():
    async def never(*args):
        raise AssertionError("called")

    assert await illustrations.plan(parts=[("a", "b")], eligible=[], request="", model="m",
                                    api_key="k", complete=never, slide=False) == (
        [], {"inputTokens": 0, "outputTokens": 0})

    async def broken(*args):
        raise ValueError("model down")

    planned, _ = await illustrations.plan(parts=[("a", "b")], eligible=[0], request="",
                                          model="m", api_key="k", complete=broken, slide=False)
    assert planned == []


@pytest.mark.parametrize("request_text", ["그림 없이 정리해 줘", "이미지는 빼고 만들어 줘",
                                          "삽화 넣지 마"])
def test_a_request_without_pictures_is_heard(request_text):
    assert illustrations.declined(request_text)


def test_a_request_that_mentions_pictures_otherwise_is_not_a_refusal():
    assert not illustrations.declined("그림을 넣어서 발표 자료를 만들어 줘")


# ── drawing ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_picture_is_drawn_only_with_a_self_hosted_model(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(illustrations.imagegen, "generate", _drawn(calls))
    row = illustrations.Planned(index=0, prompt="colleagues around one table", caption="협업")
    assert await illustrations.draw(row, image_model=_OUTSIDE, api_key="k", aspect="4:3") is None
    assert calls == []
    picture = await illustrations.draw(row, image_model=_LOCAL, api_key="k", aspect="4:3")
    assert picture["src"].startswith("data:image/png;base64,") and picture["caption"] == "협업"
    assert calls[0]["model"] == _LOCAL["id"] and calls[0]["aspect"] == "4:3"


# ── a deck ────────────────────────────────────────────────────────────


def _deck_model(picks: list[dict]):
    prompts: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        text = messages[-1]["content"]
        prompts.append(text)
        if _PICK in text:
            return json.dumps(picks, ensure_ascii=False), {"inputTokens": 3, "outputTokens": 3}
        if "도식의 종류" in text:
            return "[]", {"inputTokens": 1, "outputTokens": 1}
        if max_tokens > 2000:
            raise ValueError("no draft this time")
        return json.dumps({"bullets": ["모든 대화가 한곳에 모인다", "문서를 함께 고친다"],
                           "notes": ""}, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}
    return complete, prompts


async def _write(monkeypatch, auto, picks):
    complete, prompts = _deck_model(picks)
    calls: list[dict] = []
    monkeypatch.setattr(deck, "_complete", complete)
    monkeypatch.setattr(illustrations.imagegen, "generate", _drawn(calls))
    plan = [{"title": "표지", "layout": "title"}, {"title": "함께 쓰는 공간", "layout": "bullets"},
            {"title": "마무리", "layout": "closing"}]
    events = [e async for e in deck._write_slides(
        plan=plan, title="발표", subtitle="", accent="#5b5bd6", request="협업 도구 소개",
        model="m", api_key="k", trusted_context=None, untrusted_context=None,
        usage={"inputTokens": 0, "outputTokens": 0}, auto_image_model=auto)]
    return events, prompts, calls


@pytest.mark.asyncio
async def test_a_deck_draws_its_own_picture_beside_the_words(monkeypatch):
    events, prompts, calls = await _write(
        monkeypatch, _LOCAL, [{"part": 2, "prompt": "colleagues at one table", "caption": "협업"}])
    slides = next(e for e in events if e["type"] == "deck")["slides"]
    assert slides[1]["image"] == {"src": "data:image/png;base64,iVBORw==", "caption": "협업"}
    assert len(calls) == 1 and sum(_PICK in p for p in prompts) == 1
    # Only text slides are offered; the cover and closing are not.
    picking = next(p for p in prompts if _PICK in p)
    assert "[2] 함께 쓰는 공간" in picking and "[1] 표지" not in picking
    assert any(e["type"] == "step" and e.get("id") == "fig1" and e["status"] == "done"
               for e in events)


def test_pictures_go_only_on_slides_whose_words_make_room():
    # Bands and cards keep the full width beside a picture; the picture would cover them.
    assert set(deck._PICTURE_LAYOUTS) == {"bullets", "statement"}


@pytest.mark.asyncio
async def test_without_a_self_hosted_model_a_deck_plans_no_picture(monkeypatch):
    events, prompts, calls = await _write(monkeypatch, None, [{"part": 2, "prompt": "x"}])
    assert not any(_PICK in p for p in prompts) and calls == []
    slides = next(e for e in events if e["type"] == "deck")["slides"]
    assert "image" not in slides[1]


# ── a report ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_report_appends_its_own_picture_to_the_part(monkeypatch):
    calls: list[dict] = []

    async def complete(model, messages, api_key, max_tokens):
        assert _PICK in messages[-1]["content"]
        return json.dumps([{"part": 2, "prompt": "an open office", "caption": "열린 사무실"}],
                          ensure_ascii=False), {"inputTokens": 2, "outputTokens": 2}

    monkeypatch.setattr(report_stages._report(), "_complete", complete)
    monkeypatch.setattr(illustrations.imagegen, "generate", _drawn(calls))
    bundle = report_stages.bundle_for(request="협업 도구 보고서", model="m", api_key="k",
                                      auto_image_model=_LOCAL)
    sections = [
        {"id": "a", "heading": "개요", "content": "![그림](data:image/png;base64,AAAA)"},
        {"id": "b", "heading": "일하는 공간", "content": "모든 대화가 한곳에 모인다."},
    ]
    events = [e async for e in report_stages._illustrate(bundle, sections)]
    assert sections[1]["content"].endswith("![열린 사무실](data:image/png;base64,iVBORw==)")
    assert calls[0]["aspect"] == "4:3" and any(e["type"] == "section" for e in events)


# ── which model ───────────────────────────────────────────────────────


def test_the_document_draws_with_a_self_hosted_model_the_account_may_use():
    user = SimpleNamespace(allowed_models=[], preferences={})
    catalogue = {"models": [_OUTSIDE, _LOCAL]}
    assert sessions_router._auto_image_model(user, catalogue)["id"] == _LOCAL["id"]
    assert sessions_router._auto_image_model(user, {"models": [_OUTSIDE]}) is None
    narrowed = SimpleNamespace(allowed_models=[_OUTSIDE["id"]], preferences={})
    assert sessions_router._auto_image_model(narrowed, catalogue) is None
