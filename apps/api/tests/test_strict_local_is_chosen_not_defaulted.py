"""`fallback_order`: a strict-local model is never the default when an ordinary one exists."""

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
    """The choice is independent of catalogue order."""
    catalogue = [
        _model("local/qwen3.6-35b"),
        _model("strict-local/qwen3.6-35b", strict=True),
    ]

    assert sorted(catalogue, key=model_service.fallback_order)[0]["id"] == "local/qwen3.6-35b"


def test_price_still_decides_between_two_ordinary_models() -> None:
    catalogue = [_model("pricey", cost=40), _model("cheap", cost=1)]

    assert sorted(catalogue, key=model_service.fallback_order)[0]["id"] == "cheap"


def test_a_restricted_model_is_still_used_when_it_is_the_only_one() -> None:
    """A strict-local model is still chosen when it is the only one."""
    catalogue = [_model("strict-local/only", strict=True)]

    assert sorted(catalogue, key=model_service.fallback_order)[0]["id"] == "strict-local/only"


def test_a_cheaper_ordinary_model_still_beats_a_free_restricted_one() -> None:
    catalogue = [_model("strict-local/free", strict=True), _model("paid", cost=12)]

    assert sorted(catalogue, key=model_service.fallback_order)[0]["id"] == "paid"
