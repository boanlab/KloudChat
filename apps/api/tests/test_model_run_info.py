"""The picker's run info (declared by the gateway) and what an answer says it ran on."""

from __future__ import annotations

from app.services import agent, models, run_info


def _entry(info=None, model_id="local/deepseek-v4-flash"):
    return {
        "model_name": model_id,
        "model_info": {
            "mode": "chat",
            "litellm_provider": "hosted_vllm",
            "supports_function_calling": True,
            "max_input_tokens": 127000,
            "kchat_data_boundary": "self_hosted",
            "kchat_strict_local": True,
            **(info or {}),
        },
    }


def test_a_model_with_no_declaration_has_no_run_info():
    assert models._shape(_entry())["runInfo"] is None


def test_a_self_hosted_model_declares_its_weights_cache_and_engine():
    shaped = models._shape(_entry({
        "kchat_quantization": "NVFP4", "kchat_kv_cache": "fp8", "kchat_engine": "vLLM 0.33.0"}))
    assert shaped["runInfo"] == {"quantization": "NVFP4", "kvCache": "fp8", "engine": "vLLM 0.33.0"}


def test_an_external_model_declares_its_pin_quantization_and_zdr():
    shaped = models._shape(_entry({
        "kchat_data_boundary": "external", "kchat_strict_local": False,
        "kchat_provider_pinned": "Wafer", "kchat_quantization": "unknown",
        "kchat_zdr": True, "kchat_run_policy": "open_weight_strict"}, "moonshotai/kimi-k3"))
    assert shaped["runInfo"] == {"quantization": "unknown", "providerPinned": "Wafer", "zdr": True,
                                 "policy": "open_weight_strict"}


def test_malformed_declarations_are_dropped():
    shaped = models._shape(_entry({"kchat_quantization": 4, "kchat_zdr": "yes",
                                   "kchat_engine": "x" * 200}))
    assert shaped["runInfo"] == {"engine": "x" * 60}


def test_call_settings_keep_what_was_sent_and_hash_the_system_prompt():
    payload = {"model": "m", "temperature": 0.4, "top_p": 0.9, "seed": 0, "max_tokens": 2000,
               "reasoning": {"effort": "high"},
               "messages": [{"role": "system", "content": "secret rules"},
                            {"role": "user", "content": "hi"}]}
    out = run_info.call_settings(payload)
    assert out["temperature"] == 0.4 and out["topP"] == 0.9 and out["seed"] == 0
    assert out["maxTokens"] == 2000 and out["reasoning"] == "high"
    assert len(out["systemPromptHash"]) == 12 and "secret" not in str(out)
    assert run_info.call_settings({"messages": []}) == {}


def test_thinking_switches_read_as_effort():
    assert run_info.call_settings(
        {"chat_template_kwargs": {"enable_thinking": False}})["reasoning"] == "off"
    assert run_info.call_settings({"reasoning_effort": "low"})["reasoning"] == "low"


def test_the_stream_keeps_the_provider_the_gateway_reports():
    acc = agent._Accumulator()
    acc.add_chunk({"model": "moonshotai/kimi-k3", "provider": "Wafer", "choices": []})
    assert acc.provider == "Wafer"
    acc.add_chunk({"choices": [], "provider": 5})
    assert acc.provider == "Wafer"


def test_record_joins_the_call_with_the_models_declared_facts():
    catalogue = [{"id": "moonshotai/kimi-k3",
                  "runInfo": {"quantization": "fp8", "providerPinned": "Wafer", "zdr": True,
                              "policy": "open_weight_strict"}}]
    event = {"provider": "Wafer", "call": {"temperature": 0.4}}
    out = run_info.record({"id": "moonshotai/kimi-k3"}, event, catalogue)
    assert out["provider"] == "Wafer" and out["quantization"] == "fp8"
    assert out["fallback"] is False and out["call"] == {"temperature": 0.4}
    # another provider answered: the pinned endpoint's quantization is not claimed
    other = run_info.record({"id": "moonshotai/kimi-k3"}, {"provider": "Together"}, catalogue)
    assert "quantization" not in other and other["provider"] == "Together"


def test_a_fallback_answer_reports_the_answering_models_facts():
    catalogue = [{"id": "local/a", "runInfo": {"quantization": "NVFP4"}},
                 {"id": "qwen/b", "runInfo": {"policy": "open_weight_strict", "zdr": True}}]
    out = run_info.record({"id": "local/a"}, {"provider": "X"}, catalogue, "qwen/b")
    assert out["model"] == "qwen/b" and out["fallback"] is True and "quantization" not in out
    assert out["zdr"] is True
