"""Image-only gateway models are asked for the image alone, keep the asked ratio and are billed
what the gateway reports; a strict-local document draws its figures on a strict-local model."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.routers.sessions import _document_image_model
from app.services import imagegen
from app.services.credits import charge_for_image

_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_an_image_only_model_is_asked_for_the_image_alone() -> None:
    # OpenRouter answers 404 when an image-only model is asked for text as well.
    for model_id in (
        "bytedance-seed/seedream-5-0-flash",
        "x-ai/grok-imagine-image-2.0",
        "recraft/recraft-v4.1",
    ):
        assert imagegen.modalities_for(model_id) == ["image"]
        assert imagegen.aspects_for(model_id) == list(imagegen.OFFERED_ASPECTS)
    for model_id in (
        "google/gemini-3-pro-image",
        "openai/gpt-5-image",
        "strict-local/z-image-turbo",
    ):
        assert imagegen.modalities_for(model_id) == ["image", "text"]


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
