"""A stage on a self-hosted DeepSeek must not think away its short answer budget: the default
level of the row is sent as `low`, which the server takes as a level of its own."""

from __future__ import annotations

from app.services import super_agent as sa

DS = "strict-local/deepseek-v4-flash"


def test_low_is_a_level_of_its_own_on_a_self_hosted_deepseek() -> None:
    assert sa.reasoning_fields(DS, "low") == {
        "chat_template_kwargs": {"thinking": True, "reasoning_effort": "low"}}
    # the other levels keep the template's switch, as the complex document row always did
    assert sa.reasoning_fields(DS, "high") == {"chat_template_kwargs": {"thinking": True}}
    assert sa.reasoning_fields(DS, "none") == {"chat_template_kwargs": {"thinking": False}}
    assert sa.reasoning_fields(DS, "default") == {}
