"""The model picked on a surface is kept with the account, not only in one browser."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.user import User
from app.schemas.auth import Preferences


def _user(**preferences) -> User:
    return User(
        email="person@example.test", password_hash="hash", name="Person", preferences=preferences
    )


def test_a_pick_on_each_surface_is_kept_and_read_back_by_its_wire_name():
    prefs = Preferences.model_validate(
        {"modelByKind": {"chat": "local/a", "report": "local/b"}, "avModelByMode": {"audio": "x"}}
    )
    assert prefs.model_by_kind == {"chat": "local/a", "report": "local/b"}
    assert prefs.av_model_by_mode == {"audio": "x"}
    assert prefs.model_dump(by_alias=True)["modelByKind"] == {
        "chat": "local/a",
        "report": "local/b",
    }


def test_stored_picks_come_back_with_the_account():
    user = _user(modelByKind={"slides": "local/deck"}, streamResponses=False)
    prefs = Preferences.of(user)
    assert prefs.model_by_kind == {"slides": "local/deck"}
    assert prefs.stream_responses is False


def test_an_account_with_no_pick_reads_as_empty_not_missing():
    assert Preferences.of(_user()).model_by_kind == {}


def test_a_surface_the_app_does_not_have_is_refused():
    with pytest.raises(ValidationError):
        Preferences.model_validate({"modelByKind": {"spreadsheet": "local/a"}})
    with pytest.raises(ValidationError):
        Preferences.model_validate({"avModelByMode": {"chat": "local/a"}})


def test_an_empty_id_forgets_the_pick():
    prefs = Preferences.model_validate({"modelByKind": {"chat": "", "report": " local/b "}})
    assert prefs.model_by_kind == {"report": "local/b"}
