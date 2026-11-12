"""A cache-ordered system prompt starts with the deployment's fixed blocks, so a provider
or vLLM prefix cache can reuse them across turns and sessions; the date and the session's
own blocks follow, and the contracts keep their precedence over those blocks."""

from __future__ import annotations

import itertools

import pytest

from app.models.chat import SessionKind
from app.services import context
from app.services.freshness import FRESHNESS_INSTRUCTION

_CASES = list(itertools.product(list(SessionKind), [False, True], [False, True], [False, True]))


def _prompt(monkeypatch, kind, light, tools, search, *, day, extra, cache_order=True):
    monkeypatch.setattr(context, "_today", lambda: f"Today is {day}.")
    return context.system_prompt(kind, with_tools=tools, web_search=search, web_search_auto=True,
                                 extra=extra, light=light, cache_order=cache_order)


@pytest.mark.parametrize("kind, light, tools, search", _CASES)
def test_the_prefix_before_the_date_is_the_same_for_every_session(
    monkeypatch, kind, light, tools, search
):
    one = _prompt(monkeypatch, kind, light, tools, search, day="2026년 11월 12일 목요일",
                  extra=["Workspace style: 짧게.", "Project: 김서연의 캡스톤."])
    two = _prompt(monkeypatch, kind, light, tools, search, day="2027년 1월 3일 일요일",
                  extra=["Agent: 다른 지시."])
    fixed_one, _, rest_one = one.partition("Today is ")
    fixed_two, _, _ = two.partition("Today is ")
    assert fixed_one == fixed_two and len(fixed_one) > 200
    # nothing that changes per day, person or session inside the shared prefix
    for volatile in ("2026", "11월", "김서연", "Workspace style", "Project:"):
        assert volatile not in fixed_one
    # the session's blocks come after the date, in the caller's order
    assert rest_one.index("Workspace style") < rest_one.index("Project:")


@pytest.mark.parametrize("kind, light, tools, search", _CASES)
def test_the_reordered_prompt_carries_every_block_of_the_old_one(
    monkeypatch, kind, light, tools, search
):
    kwargs = dict(day="2026년 11월 12일 목요일", extra=["SYNTHETIC_WORKSPACE_INSTRUCTION"])
    old = _prompt(monkeypatch, kind, light, tools, search, cache_order=False, **kwargs)
    new = _prompt(monkeypatch, kind, light, tools, search, **kwargs)
    assert sorted(old.split("\n\n")) == sorted(
        part for part in new.split("\n\n") if part != context._CONTRACTS_HOLD)
    # the contracts once placed last keep their precedence in words
    assert new.endswith(context._CONTRACTS_HOLD)
    if not light:
        assert new.index(FRESHNESS_INSTRUCTION) < new.index("SYNTHETIC_WORKSPACE_INSTRUCTION")
        if kind is SessionKind.chat:
            assert new.index("Explicit chat task contract:") < new.index("Today is ")


def test_no_session_blocks_no_closing_line(monkeypatch):
    prompt = _prompt(monkeypatch, SessionKind.chat, False, True, False, day="d", extra=[" "])
    assert prompt.endswith("Today is d.")
    assert context._CONTRACTS_HOLD not in prompt


def test_the_default_order_is_unchanged(monkeypatch):
    prompt = _prompt(monkeypatch, SessionKind.chat, False, True, True, day="d",
                     extra=["SYNTHETIC_WORKSPACE_INSTRUCTION"], cache_order=False)
    assert prompt.endswith(FRESHNESS_INSTRUCTION)
    assert prompt.index("Today is d.") < prompt.index("SYNTHETIC_WORKSPACE_INSTRUCTION")


def test_build_messages_passes_the_order_through(monkeypatch):
    monkeypatch.setattr(context, "_today", lambda: "Today is d.")
    messages = context.build_messages(
        SessionKind.chat, [{"role": "user", "content": "안녕"}], with_tools=True,
        extra=["SYNTHETIC_WORKSPACE_INSTRUCTION"], cache_order=True)
    system = messages[0]["content"]
    assert system.index(FRESHNESS_INSTRUCTION) < system.index("Today is d.")
    assert system.endswith(context._CONTRACTS_HOLD)
