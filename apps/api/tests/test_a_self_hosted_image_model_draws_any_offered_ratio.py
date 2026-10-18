"""Self-hosted image models: every offered ratio, a longer wait, the maker shown."""

from __future__ import annotations

import httpx
import pytest

from app.services import imagegen
from app.services import models as model_service

_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_a_self_hosted_image_model_offers_every_ratio() -> None:
    for model_id in ("local/qwen-image-2512", "local/z-image-turbo", "strict-local/flux2-klein-4b"):
        assert imagegen.honours_aspect(model_id)
        assert imagegen.aspects_for(model_id) == list(imagegen.OFFERED_ASPECTS)
    assert imagegen.aspects_for("openai/gpt-5-image-mini") == ["1:1"]


def test_a_self_hosted_image_model_names_its_maker() -> None:
    entry = {
        "model_name": "local/flux2-klein-4b",
        "litellm_params": {
            "model": "openai/local/flux2-klein-4b",
            "api_base": "http://imagegen-shim:9100/v1",
        },
        "model_info": {
            "mode": "image_generation",
            "input_cost_per_token": 0,
            "output_cost_per_token": 0,
            "kchat_data_boundary": "self_hosted",
            "kchat_strict_local": True,
        },
    }
    shaped = model_service._shape(entry)
    assert shaped is not None
    assert shaped["vendor"] == "Black Forest Labs"
    assert shaped["dataBoundary"] == "self_hosted" and shaped["strictLocal"] is True
    assert shaped["creditPerImage"] == 0
    assert shaped["aspects"] == list(imagegen.OFFERED_ASPECTS)
    assert model_service._vendor("local/z-image-turbo", "openai") == "Tongyi"


@pytest.mark.asyncio
async def test_a_self_hosted_picture_gets_the_longer_wait(monkeypatch) -> None:
    seen: list[float | None] = []
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        seen.append(kwargs.get("timeout"))

        def answer(request: httpx.Request) -> httpx.Response:
            image = {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_PNG}"}}
            return httpx.Response(200, json={"choices": [{"message": {"images": [image]}}]})

        return real(transport=httpx.MockTransport(answer))

    monkeypatch.setattr(imagegen.httpx, "AsyncClient", client)
    for model in ("local/qwen-image-2512", "google/gemini-2.5-flash-image"):
        image = await imagegen.generate(
            base_url="http://llm", api_key="k", model=model, prompt="a fox", aspect="16:9"
        )
        assert (image.width, image.height) == (1, 1)
    assert seen == [imagegen._SELF_HOSTED_TIMEOUT, imagegen._TIMEOUT]

