from app.services import model_tier


def test_light_writers_env_forces_listed_models_only(monkeypatch) -> None:
    monkeypatch.delenv("KCHAT_LIGHT_WRITERS", raising=False)
    assert not model_tier.strong("local/qwen3.8-27b")
    monkeypatch.setenv("KCHAT_LIGHT_WRITERS", "local/qwen3.8-27b, local/qwen3.8-flash")
    assert model_tier.strong("local/qwen3.8-27b")
    assert model_tier.strong("Local/Qwen3.8-Flash")
    assert not model_tier.strong("local/other")
    monkeypatch.setenv("KCHAT_LIGHT_WRITERS", "")
    assert not model_tier.strong("local/qwen3.8-27b")
