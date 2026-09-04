"""A `sealed` copy runs on the original's prompt without ever holding it; `open` carries it."""

from __future__ import annotations

from app.models.workspace import Agent, Visibility
from app.schemas.workspace import AgentOut


def _agent(**kw) -> Agent:
    base = dict(
        owner_id="author",
        name="비밀 튜터",
        slug="secret-tutor",
        system_prompt="You are the secret tutor.",
        visibility=Visibility.org,
    )
    base.update(kw)
    return Agent(**base)


def test_the_author_sees_a_sealed_prompt_and_nobody_else_does():
    row = _agent(share_mode="sealed")
    assert AgentOut.of(row, viewer_id="author").system_prompt == "You are the secret tutor."
    seen = AgentOut.of(row, viewer_id="someone")
    assert seen.system_prompt == "" and seen.sealed and seen.share_mode == "sealed"


def test_an_open_share_shows_its_prompt_to_everyone():
    row = _agent(share_mode="open")
    seen = AgentOut.of(row, viewer_id="someone")
    assert seen.system_prompt == "You are the secret tutor." and not seen.sealed


def test_a_sealed_copy_never_shows_a_prompt_even_to_its_owner():
    copy = _agent(owner_id="taker", sealed=True, system_prompt="", origin_id="orig")
    seen = AgentOut.of(copy, viewer_id="taker")
    assert seen.sealed and seen.system_prompt == ""
