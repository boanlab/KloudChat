"""The thinking budget sent with a self-hosted Qwen's thinking switch, and the strict-local
column's own effort (`strictEffort`) in the Super Agent table."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import super_agent as sa

FLASH = "strict-local/qwen3.8-flash"


# -- the thinking budget ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("effort", "budget"),
    [("low", 500), ("high", 2000), (sa.HIGHEST, 2000)],
)
def test_flash_thinking_is_capped_by_effort(effort: str, budget: int) -> None:
    assert sa.reasoning_fields(FLASH, effort) == {
        "chat_template_kwargs": {"enable_thinking": True}, "thinking_token_budget": budget}
    assert sa.reasoning_fields("local/qwen3.8-flash", effort)["thinking_token_budget"] == budget


def test_a_zero_budget_sends_no_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "self_hosted_thinking_budget_low", 0)
    assert sa.reasoning_fields(FLASH, "low") == {"chat_template_kwargs": {"enable_thinking": True}}


def test_budgets_follow_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "self_hosted_thinking_budget_high", 3000)
    assert sa.reasoning_fields(FLASH, "high")["thinking_token_budget"] == 3000


def test_no_budget_where_thinking_is_off_default_or_not_flash() -> None:
    assert sa.reasoning_fields(FLASH, "none") == {
        "chat_template_kwargs": {"enable_thinking": False}}
    assert sa.reasoning_fields(FLASH, "default") == {}
    # the 27B and DeepSeek keep their own fields
    assert "thinking_token_budget" not in sa.reasoning_fields("strict-local/qwen3.8-27b", "high")
    deepseek = sa.reasoning_fields("strict-local/deepseek-v4-flash", "high")
    assert deepseek == {"chat_template_kwargs": {"thinking": True}}


# -- the strict column's effort -----------------------------------------------------------


def test_the_built_in_strict_efforts_and_strict_follows_effort_elsewhere() -> None:
    rules = sa.normalise_rules(None)
    # The strict column's own efforts, where it runs on flash for a row whose `super` model differs.
    assert {name: rule["strictEffort"] for name, rule in rules.items()
            if "strictEffort" in rule} == {
        "visual": "none", "complex_document": "high", "escalation": "high",
        "document_plan": "low", "document_hard": "low", "document_repair": "low"}
    for name, rule in rules.items():
        if "strictEffort" not in rule:
            assert sa.rule_effort(rule, strict=True) == sa.rule_effort(rule) == rule["effort"]
        assert sa.rule_effort(rule, strict=True) == rule.get("strictEffort", rule["effort"]), name


def test_a_strict_effort_applies_only_to_the_strict_column() -> None:
    rules = sa.normalise_rules({"calc": {"effort": "high", "strictEffort": "low"}})
    decision = sa.Decision("calc", "rule", "test")
    assert sa.effort_for(rules, decision) == "high"
    assert sa.effort_for(rules, decision, True) == "low"
    assert sa.steer(rules, decision, sa.AUTO) == ("calc", "high")
    assert sa.steer(rules, decision, sa.AUTO, strict=True) == ("calc", "low")


def test_a_stage_row_reads_its_strict_effort_too() -> None:
    rules = sa.normalise_rules({"document_plan": {"effort": "low", "strictEffort": "none"}})
    assert sa.stage_effort(rules, "plan", sa.AUTO) == "low"
    assert sa.stage_effort(rules, "plan", sa.AUTO, True) == "none"
    assert sa.stage_effort(rules, "plan", sa.DEEP, True) == sa.HIGHEST


def test_an_invalid_strict_effort_is_dropped_and_a_valid_one_is_stored() -> None:
    assert "strictEffort" not in sa.normalise_rules({"chat": {"strictEffort": "max"}})["chat"]
    stored = sa.stored_rules({"chat": {"strictEffort": "low"}})
    assert stored == {"chat": {"strictEffort": "low"}}


def test_the_writers_own_stage_model_with_a_strict_effort_is_a_stage_in_the_strict_column() -> None:
    writer = {"id": FLASH, "kinds": ["chat", "report"], "dataBoundary": "self_hosted",
              "strictLocal": True, "supportsTools": True, "contextWindow": 128_000,
              "inputCreditCost": 0, "creditCost": 0}
    rules = sa.normalise_rules({"document_plan": {
        "strictModel": FLASH, "effort": "default", "strictEffort": "low"}})

    def pick(strict: bool):
        return sa.stage_pick(rules, "plan", [writer], kind="report", strict=strict,
                             allowed=set(), writer=writer)

    assert pick(True) == writer
