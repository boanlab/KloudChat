"""Image-only gateway models: image-only asks, kept ratio, gateway billing."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.routers.sessions import _document_image_model
from app.services import imagegen, models
from app.services.credits import charge_for_image

_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_an_image_only_model_is_asked_for_the_image_alone() -> None:
    # OpenRouter answers 404 when an image-only model is asked for text as well.
    for model_id in (
        "bytedance-seed/seedream-5-0-flash",
        "x-ai/grok-imagine-image-2.0",
        "bytedance-seed/seedream-5-5",
    ):
        assert imagegen.modalities_for(model_id) == ["image"]
        assert imagegen.aspects_for(model_id) == list(imagegen.OFFERED_ASPECTS)
    for model_id in (
        "google/gemini-3-pro-image",
        "google/gemini-nano-banana-2.1",
        "openai/gpt-5-image",
        "openai/gpt-5.4-image-2",
        "strict-local/z-image-turbo",
    ):
        assert imagegen.modalities_for(model_id) == ["image", "text"]
    # GPT-5.4 Image 2 keeps the asked ratio (1280x720 for 16:9 in a test draw)
    assert imagegen.honours_aspect("openai/gpt-5.4-image-2")
    assert not imagegen.modalities_for("openai/gpt-5.4") == ["image", "text"]
    assert imagegen.honours_aspect("bytedance-seed/seedream-5-5")
    assert imagegen.honours_aspect("google/gemini-nano-banana-2.1")


@pytest.mark.asyncio
async def test_a_picture_is_billed_what_the_gateway_reports(monkeypatch) -> None:
    real = httpx.AsyncClient
    sent: list[dict] = []

    def client(*args, **kwargs):
        def answer(request: httpx.Request) -> httpx.Response:
            sent.append(json.loads(request.content))
            image = {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_PNG}"}}
            usage = {"prompt_tokens": 114, "completion_tokens": 16224, "cost": 0.018}
            return httpx.Response(
                200, json={"choices": [{"message": {"images": [image]}}], "usage": usage}
            )

        return real(transport=httpx.MockTransport(answer))

    monkeypatch.setattr(imagegen.httpx, "AsyncClient", client)
    image = await imagegen.generate(
        base_url="http://llm",
        api_key="k",
        model="bytedance-seed/seedream-5-0-flash",
        prompt="a fox",
        aspect="3:4",
    )
    assert sent[0]["modalities"] == ["image"] and sent[0]["image_config"] == {"aspect_ratio": "3:4"}
    assert image.cost_usd == 0.018
    # A flat per-image price, not 16,224 tokens at the per-token rate.
    priced = {"inputCreditCost": 1, "creditCost": 431}
    assert charge_for_image(priced, image.input_tokens, image.output_tokens, image.cost_usd) == 1800
    assert charge_for_image(priced, 0, 1000, None) == 431
    assert charge_for_image({"inputCreditCost": 0, "creditCost": 0}, 0, 1000, 0.5) == 0


def test_a_strict_local_document_draws_only_on_a_strict_local_model() -> None:
    external = {
        "id": "bytedance-seed/seedream-5-0-flash",
        "kinds": ["image"],
        "dataBoundary": "external",
    }
    other = {"id": "x-ai/grok-imagine-image-2.0", "kinds": ["image"], "dataBoundary": "external"}
    strict = {
        "id": "strict-local/z-image-turbo",
        "kinds": ["image"],
        "dataBoundary": "self_hosted",
        "strictLocal": True,
        "creditCost": 0,
    }
    catalogue = {
        "models": [other, external, strict],
        "defaultModelByKind": {"image": external["id"]},
    }
    user = SimpleNamespace(allowed_models=[], preferences=None)
    assert _document_image_model(user, catalogue, strict_local=True) is strict
    assert _document_image_model(user, catalogue, strict_local=False) is external
    # No strict-local picture model: the strict-local document carries no figure.
    bare = {"models": [other, external], "defaultModelByKind": {"image": external["id"]}}
    assert _document_image_model(user, bare, strict_local=True) is None
    # The default not allowed for this account: the first allowed one.
    assert (
        _document_image_model(
            SimpleNamespace(allowed_models=[other["id"]], preferences=None),
            catalogue,
            strict_local=False,
        )
        is other
    )


def test_a_version_split_by_a_dash_reads_as_one_number() -> None:
    assert models._label("bytedance-seed/seedream-5-5") == "Seedream 5.5"
    assert models._label("bytedance-seed/seed-2-1-turbo") == "Seed 2.1 Turbo"
    assert models._label("google/gemini-4-argon") == "Gemini 4 Argon"
    assert models._label("openai/gpt-6.1-terra") == "GPT 6.1 Terra"


def test_a_shown_name_drops_build_numbers_active_params_and_it_but_keeps_the_id() -> None:
    for model_id, shown in {
        "qwen/qwen3.8-max-0902": "Qwen3.8 Max",
        "deepseek/deepseek-v4-pro-0813": "DeepSeek V4 Pro",
        "google/gemma-4-31b-it": "Gemma 4 31B",
        "nvidia/nemotron-3-ultra-550b-a55b": "Nemotron 3 Ultra 550B",
        "nvidia/nemotron-3-nano-30b-a3b": "Nemotron 3 Nano 30B",
        "google/gemini-3.1-pro-preview": "Gemini 3.1 Pro Preview",
    }.items():
        assert models._label(model_id) == shown


def _row(model_id: str, **info) -> dict:
    return models._shape({"model_name": model_id, "model_info": {
        "mode": "chat", "input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6, **info,
    }, "litellm_params": {"model": f"openrouter/{model_id}"}})


def test_models_never_share_a_shown_name_and_the_gateway_name_wins() -> None:
    ids = [
        "qwen/qwen3.8-max-0902", "qwen/qwen3.8-max-0915", "qwen/qwen3.8-max",
        "deepseek/deepseek-v4-pro-0813", "google/gemma-4-31b-it", "google/gemma-4-31b",
        "nvidia/nemotron-3-ultra-550b-a55b", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "google/gemini-3.1-pro-preview", "google/gemini-4-argon", "openai/gpt-6.1-terra",
        "bytedance-seed/seedream-5-5", "bytedance-seed/seed-2-1-turbo",
    ]
    rows = [_row(i) for i in ids]
    models._keep_names_apart(rows)
    labels = [r["label"] for r in rows]
    assert len(set(labels)) == len(labels)
    assert [r["id"] for r in rows] == ids
    by_id = {r["id"]: r["name"] for r in rows}
    assert by_id["deepseek/deepseek-v4-pro-0813"] == "DeepSeek V4 Pro"
    assert by_id["google/gemma-4-31b"] != by_id["google/gemma-4-31b-it"]
    assert _row("qwen/qwen3.8-max-0902", kchat_label="Qwen Max")["name"] == "Qwen Max"
