"""`governance.outline_model_id` may not send further than the writer already does."""

from __future__ import annotations

import pytest

from app.routers.sessions import _widens_boundary

LOCAL = {"id": "local/a", "dataBoundary": "self_hosted"}
STRICT = {"id": "local/strict", "dataBoundary": "self_hosted", "strictLocal": True}
EXTERNAL = {"id": "vendor/b", "dataBoundary": "external"}
HYBRID = {"id": "vendor/c", "dataBoundary": "hybrid"}
UNKNOWN = {"id": "vendor/d", "dataBoundary": "unknown"}


@pytest.mark.parametrize("planner", [EXTERNAL, HYBRID, UNKNOWN])
def test_a_contained_turn_refuses_a_planner_that_leaves(planner):
    assert _widens_boundary(planner, LOCAL) is True


@pytest.mark.parametrize("planner", [LOCAL, STRICT])
def test_a_planner_that_stays_inside_is_allowed(planner):
    assert _widens_boundary(planner, LOCAL) is False


def test_strict_local_is_a_stronger_claim_than_self_hosted():
    """A strict-local turn accepts no self-hosted planner with an external fallback."""
    assert _widens_boundary(LOCAL, STRICT) is True
    assert _widens_boundary(STRICT, STRICT) is False


def test_an_external_turn_may_plan_anywhere():
    """An external turn may plan on any model."""
    assert _widens_boundary(LOCAL, EXTERNAL) is False
    assert _widens_boundary(HYBRID, EXTERNAL) is False


def test_a_boundary_nobody_established_counts_as_the_far_side():
    assert _widens_boundary(UNKNOWN, LOCAL) is True
    assert _widens_boundary({"id": "x"}, LOCAL) is True


# ── the decision itself ────────────────────────────────────────────────


class _User:
    """Only what `_allowed_models` reads."""

    def __init__(self, allowed: list[str] | None = None):
        self.allowed_models = allowed


CATALOGUE = [
    {**LOCAL, "kinds": ["chat", "slides", "report"]},
    {**STRICT, "kinds": ["chat", "slides", "report"]},
    {**EXTERNAL, "kinds": ["chat", "slides", "report"]},
    {"id": "vendor/chat-only", "dataBoundary": "external", "kinds": ["chat"]},
]


def planner(wanted, *, allowed=None, kind="slides", writer=EXTERNAL, strict_local=False):
    """The chosen planner's id, or `""`."""
    from app.routers.sessions import _planner_model

    chosen = _planner_model(
        wanted,
        user=_User(allowed),
        catalogue=CATALOGUE,
        kind=kind,
        writer=writer,
        strict_local=strict_local,
    )
    return chosen["id"] if chosen else ""


def test_unset_means_the_writer_plans():
    assert planner(None) == ""
    assert planner("") == ""


def test_a_named_planner_is_used():
    assert planner("local/a") == "local/a"


def test_a_privacy_routed_turn_gets_no_planner_at_all():
    """A privacy-routed turn gets no separate planner."""
    assert planner("vendor/b", writer=STRICT, strict_local=True) == ""


def test_a_model_this_account_may_not_use_is_refused():
    assert planner("vendor/b", allowed=["local/a"]) == ""
    assert planner("local/a", allowed=["local/a"]) == "local/a"


def test_a_model_that_cannot_write_this_surface_is_refused():
    assert planner("vendor/chat-only") == ""


def test_a_planner_that_would_widen_the_turn_is_refused():
    assert planner("vendor/b", writer=LOCAL) == ""
    assert planner("local/a", writer=LOCAL) == "local/a"


def test_a_name_that_is_not_in_the_catalogue_is_refused():
    assert planner("vendor/gone") == ""
