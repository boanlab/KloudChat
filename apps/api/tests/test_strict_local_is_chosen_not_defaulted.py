"""아무도 고르지 않았을 때 어떤 모델이 뽑히는가.

strict-local is a route somebody picks on purpose. It hands the turn no web
search tool, refuses every connector, and says so on screen — 이 모델은 외부에
연결하지 않습니다. That sentence is right when it explains a choice and wrong
when it explains an accident.

It sits beside the plain local model at the same price, so every fallback that
sorted on cost alone broke the tie by catalogue order and handed a new account
the restricted route as its first model. What the person then saw was a
product whose web search did not work, for a reason they had never chosen.
"""

from __future__ import annotations

from app.services import models as model_service


def _model(model_id: str, *, cost: float = 0.0, strict: bool = False) -> dict:
    return {"id": model_id, "creditCost": cost, "strictLocal": strict, "kinds": ["chat"]}


def test_the_plain_model_wins_a_tie_with_the_restricted_one() -> None:
    catalogue = [
        _model("strict-local/qwen3.6-35b", strict=True),
        _model("local/qwen3.6-35b"),
    ]

    assert sorted(catalogue, key=model_service.fallback_order)[0]["id"] == "local/qwen3.6-35b"


def test_catalogue_order_no_longer_decides_it() -> None:
    """The bug was order-dependent, so the reversed list has to answer alike."""
    catalogue = [
        _model("local/qwen3.6-35b"),
        _model("strict-local/qwen3.6-35b", strict=True),
    ]

    assert sorted(catalogue, key=model_service.fallback_order)[0]["id"] == "local/qwen3.6-35b"


def test_price_still_decides_between_two_ordinary_models() -> None:
    catalogue = [_model("pricey", cost=40), _model("cheap", cost=1)]

    assert sorted(catalogue, key=model_service.fallback_order)[0]["id"] == "cheap"


def test_a_restricted_model_is_still_used_when_it_is_the_only_one() -> None:
    """Hiding it would leave an instance with nothing to run at all."""
    catalogue = [_model("strict-local/only", strict=True)]

    assert sorted(catalogue, key=model_service.fallback_order)[0]["id"] == "strict-local/only"


def test_a_cheaper_ordinary_model_still_beats_a_free_restricted_one() -> None:
    catalogue = [_model("strict-local/free", strict=True), _model("paid", cost=12)]

    assert sorted(catalogue, key=model_service.fallback_order)[0]["id"] == "paid"
