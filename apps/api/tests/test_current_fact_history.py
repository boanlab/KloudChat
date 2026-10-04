"""Earlier answers stay in the conversation; the current-fact rule says they are not evidence."""

from copy import deepcopy

import pytest

from app.services.freshness import CURRENT_FACT_INSTRUCTION, with_answer_policy


@pytest.mark.parametrize("question", [
    "현재 대한민국 대통령", "현재 Acme의 CEO는 누구야?", "최신 Python 버전은?",
])
def test_current_fact_rechecks_keep_the_earlier_answer_and_add_the_rule(question):
    messages = [
        {"role": "system", "content": "Workspace rules"},
        {"role": "user", "content": "1+1은?"},
        {"role": "assistant", "content": "1+1은 2입니다."},
        {"role": "user", "content": question},
        {"role": "assistant", "content": "CONFIDENT_UNVERIFIED_OLD_ANSWER"},
        {"role": "user", "content": question},
    ]
    before = deepcopy(messages)
    wire = with_answer_policy(messages, {"id": "synthetic/model"})
    assert messages == before
    assert wire[2]["content"] == "1+1은 2입니다."
    assert wire[3] == messages[3] and wire[5] == messages[5]
    # The earlier answer is part of the conversation and stays; the instruction
    # appended to the system turn is what tells the model it is not evidence.
    assert wire[4]["content"] == "CONFIDENT_UNVERIFIED_OLD_ANSWER"
    assert wire[0]["content"].endswith(CURRENT_FACT_INSTRUCTION)
    assert "Earlier assistant answers are not evidence" in wire[0]["content"]
    assert wire[-1] == messages[-1]
    assert all(message["role"] != "system" for message in wire[1:])


@pytest.mark.parametrize("question", ["1+1은?", "0+0은?", "한국어로 인사해줘"])
def test_stable_tasks_keep_history_and_do_not_get_current_fact_warning(question):
    messages = [
        {"role": "user", "content": "현재 대한민국 대통령"},
        {"role": "assistant", "content": "Old answer"},
        {"role": "user", "content": question},
    ]
    wire = with_answer_policy(messages, {"id": "synthetic/model"})
    assert wire[1:] == messages
    assert not any(message["content"] == CURRENT_FACT_INSTRUCTION for message in wire)


def test_tool_call_envelopes_and_evidence_remain_intact():
    messages = [
        {"role": "user", "content": "현재 대한민국 대통령"},
        {"role": "assistant", "content": "Searching", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "Retrieved source", "tool_call_id": "1"},
    ]
    wire = with_answer_policy(messages, {"id": "synthetic/model"})
    assert wire[1:4] == messages


@pytest.mark.parametrize("latest", [
    "그래도 알려줘",
    [{"type": "text", "text": "현재 대한민국 대통령"}],
])
def test_followups_and_multimodal_input_keep_the_thread_and_the_rule(latest):
    messages = [
        {"role": "user", "content": "현재 대한민국 대통령"},
        {"role": "assistant", "content": "UNVERIFIED_OLD_NAME"},
        {"role": "user", "content": latest},
    ]
    wire = with_answer_policy(messages, {"id": "model"}, current_fact=True)
    assert wire[2]["content"] == "UNVERIFIED_OLD_NAME"
    assert wire[-1] == messages[-1]
    assert CURRENT_FACT_INSTRUCTION in wire[0]["content"]


def test_repeated_bare_followups_keep_every_earlier_answer():
    messages = [
        {"role": "user", "content": "현재 대한민국 대통령"},
        {"role": "assistant", "content": "UNVERIFIED_OLD_NAME"},
        {"role": "user", "content": "그래도 알려줘"},
        {"role": "assistant", "content": "UNVERIFIED_FOLLOWUP_NAME"},
        {"role": "user", "content": "알려줘"},
    ]
    wire = with_answer_policy(messages, {"id": "model"}, current_fact=True)
    assert [m["content"] for m in wire[1:]] == [m["content"] for m in messages]
    assert CURRENT_FACT_INSTRUCTION in wire[0]["content"]
