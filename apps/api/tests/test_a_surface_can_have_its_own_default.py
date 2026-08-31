"""보고서와 챗은 같은 일이 아니다.

One instance default served every surface. That is right while one model is
the best answer everywhere and wrong as soon as it is not: a conversation is a
turn every few seconds, read as it arrives, so decode speed is most of what the
person feels. A 보고서 is one long run they wait for once, and what they feel
is whether it needed rewriting.

Measured on the same prompt, three runs each, the larger model wrote 20 of 21
slides against 35 of 37 and picked the shape the title asked for 5 times out of
6 against 2 out of 10. Three times the decode cost is the wrong trade for chat
and the right one for a document.

Empty falls back to the chat default, which is what every install had before
these existed.
"""

from __future__ import annotations

import pytest

from app.services import models as model_service


def _catalogue(*ids: str) -> list[dict]:
    return [
        {
            "id": model_id,
            "kinds": ["chat", "report", "slides"],
            "creditCost": 0.0,
            "modality": "text",
            "provider": "local",
        }
        for model_id in ids
    ]


@pytest.fixture
def served(monkeypatch):
    def use(*ids: str) -> list[dict]:
        rows = _catalogue(*ids)
        monkeypatch.setattr(model_service, "_adapter_entries", lambda: rows)
        return rows

    return use


def test_a_surface_default_is_published_for_the_surfaces_that_set_one() -> None:
    """The client reads this to decide what a new 보고서 runs on."""
    from app.core.config import settings

    assert hasattr(settings, "default_report_model")
    assert hasattr(settings, "default_slides_model")
    # Empty by default: an install that never sets them behaves as before.
    assert model_service.settings.default_report_model in ("", settings.default_report_model)


def test_an_unset_surface_falls_back_to_the_chat_default(monkeypatch) -> None:
    rows = _catalogue("local/small", "local/big")
    monkeypatch.setattr(model_service.settings, "default_chat_model", "local/small")
    monkeypatch.setattr(model_service.settings, "default_report_model", "")
    monkeypatch.setattr(model_service.settings, "default_slides_model", "local/big")

    def served(model_id: str, kind: str) -> str:
        return model_id if any(m["id"] == model_id and kind in m["kinds"] for m in rows) else ""

    default_chat = served(model_service.settings.default_chat_model, "chat")
    by_kind = {
        kind: served(chosen, kind) or default_chat
        for kind, chosen in (
            ("report", model_service.settings.default_report_model),
            ("slides", model_service.settings.default_slides_model),
        )
    }
    assert by_kind == {"report": "local/small", "slides": "local/big"}


def test_a_default_naming_a_model_the_install_does_not_serve_is_dropped(monkeypatch) -> None:
    """Worse than none: the surface would offer it, the call would 404, and the
    setting that caused it is in a file nobody reads while somebody waits."""
    rows = _catalogue("local/small")
    monkeypatch.setattr(model_service.settings, "default_chat_model", "local/small")
    monkeypatch.setattr(model_service.settings, "default_slides_model", "local/gone")

    def served(model_id: str, kind: str) -> str:
        return model_id if any(m["id"] == model_id and kind in m["kinds"] for m in rows) else ""

    assert served("local/gone", "slides") == ""
    assert (served("local/gone", "slides") or served("local/small", "chat")) == "local/small"
