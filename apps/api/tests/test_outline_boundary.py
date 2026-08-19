"""The planner may not send further than the writer already does.

`governance.outline_model_id` redirects one call per document. That call
carries the same request and the same context the body does, so a policy row
naming an external model would otherwise widen the egress of every document —
including turns that privacy had deliberately routed inward.
"""

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
    """No external fallback exists for a strict-local model, and a planner
    that merely runs on our own hardware may still have one."""
    assert _widens_boundary(LOCAL, STRICT) is True
    assert _widens_boundary(STRICT, STRICT) is False


def test_an_external_turn_may_plan_anywhere():
    """The text is already going out; where it plans changes nothing."""
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
    from app.routers.sessions import _planner_model

    return _planner_model(
        wanted,
        user=_User(allowed),
        catalogue=CATALOGUE,
        kind=kind,
        writer=writer,
        strict_local=strict_local,
    )


def test_unset_means_the_writer_plans():
    assert planner(None) == ""
    assert planner("") == ""


def test_a_named_planner_is_used():
    assert planner("local/a") == "local/a"


def test_a_privacy_routed_turn_gets_no_planner_at_all():
    """The route exists so the text does not leave; planning is text leaving."""
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
