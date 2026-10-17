"""Model roles as the administrator's choice in `system_settings`, seeded from the environment."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException

from app.core.config import settings
from app.services import models as model_service
from app.services import settings_store
from app.services.tools import sandbox_tools

CATALOGUE = {
    "litellmAvailable": True,
    "models": [
        # The gateway marks `local/` hybrid (it may fall back outside); its twins are self-hosted.
        {"id": "local/qwen3.8-27b", "label": "27B", "kinds": ["chat", "report", "slides"],
         "modality": "chat", "dataBoundary": "hybrid", "strictLocal": False},
        {"id": "local/qwen3.8-flash", "label": "Flash", "kinds": ["chat", "report", "slides"],
         "modality": "chat", "dataBoundary": "hybrid", "strictLocal": False},
        {"id": "strict-local/qwen3.8-flash", "label": "Flash (strict)", "kinds": ["chat"],
         "modality": "chat", "dataBoundary": "self_hosted", "strictLocal": True},
        {"id": "strict-local/qwen3.8-27b", "label": "27B (strict)", "kinds": ["chat"],
         "modality": "chat", "dataBoundary": "self_hosted", "strictLocal": True},
        # A hybrid model with no strict-local twin is not one this instance runs.
        {"id": "local/borrowed", "label": "Borrowed", "kinds": ["chat"],
         "modality": "chat", "dataBoundary": "hybrid", "strictLocal": False},
        {"id": "openai/gpt-x", "label": "GPT", "kinds": ["chat", "report", "slides"],
         "modality": "chat", "dataBoundary": "external", "strictLocal": False},
        {"id": "google/img", "label": "Img", "kinds": ["image"], "modality": "image",
         "dataBoundary": "external", "strictLocal": False},
        {"id": "openai/voice", "label": "Voice", "kinds": ["av"], "modality": "audio",
         "dataBoundary": "external", "strictLocal": False},
        {"id": "google/film", "label": "Film", "kinds": ["av"], "modality": "video",
         "dataBoundary": "external", "strictLocal": False},
    ],
}
for _model in CATALOGUE["models"]:
    _model["provider"] = _model["id"].split("/", 1)[0]


@pytest.fixture
def stored(monkeypatch):
    values: dict[str, str] = {}

    async def all_values(force: bool = False):
        return dict(values)

    async def catalogue(force: bool = False):
        return CATALOGUE

    monkeypatch.setattr(settings_store, "all_values", all_values)
    monkeypatch.setattr(model_service, "list_models", catalogue)
    return values


def test_a_stored_choice_wins_and_an_absent_one_is_the_environment(monkeypatch):
    monkeypatch.setattr(settings, "title_model", "local/env-title")
    monkeypatch.setattr(settings, "default_report_model", "local/env-report")
    roles = settings_store.model_roles_from({})
    assert (roles.title, roles.report) == ("local/env-title", "local/env-report")
    # A stored "" is a choice: the session's own model, the chat default.
    roles = settings_store.model_roles_from({"models.title": "", "models.report": ""})
    assert (roles.title, roles.report) == ("", "")
    roles = settings_store.model_roles_from({"models.title": " local/qwen3.8-flash "})
    assert roles.title == "local/qwen3.8-flash"


async def test_the_new_account_allowance_is_stored(stored, monkeypatch):
    monkeypatch.setattr(settings, "default_monthly_credits", 500_000)
    assert await settings_store.default_monthly_credits() == 500_000
    stored["credits.default_monthly"] = "250000"
    assert await settings_store.default_monthly_credits() == 250_000


async def test_the_catalogue_defaults_follow_the_stored_roles(monkeypatch):
    from app.services import litellm

    values = {"models.chat": "local/qwen3.8-27b", "models.report": "local/qwen3.8-flash",
              "models.slides": "", "models.image": "google/img", "models.audio": "openai/voice",
              "models.video": "google/film"}

    async def all_values(force: bool = False):
        return dict(values)

    async def model_info():
        return [
            {"model_name": m["id"], "litellm_params": {"model": m["id"]},
             "model_info": {"mode": "chat"}}
            for m in CATALOGUE["models"][:2]
        ]

    shaped = {m["id"]: m for m in CATALOGUE["models"]}
    monkeypatch.setattr(settings_store, "all_values", all_values)
    monkeypatch.setattr(litellm, "model_info", model_info)
    monkeypatch.setattr(model_service, "_shape", lambda entry: dict(shaped[entry["model_name"]]))
    monkeypatch.setattr(model_service, "_adapter_entries", lambda: [
        dict(shaped[i]) for i in ("google/img", "openai/voice", "google/film")
    ])
    model_service.invalidate_cache()
    try:
        catalogue = await model_service.list_models(force=True)
    finally:
        model_service.invalidate_cache()
    assert catalogue["defaultChatModel"] == "local/qwen3.8-27b"
    assert catalogue["defaultModelByKind"]["report"] == "local/qwen3.8-flash"
    # "" follows the chat default.
    assert catalogue["defaultModelByKind"]["slides"] == "local/qwen3.8-27b"
    assert catalogue["defaultModelByKind"]["image"] == "google/img"
    assert catalogue["defaultAvModelByMode"] == {"audio": "openai/voice", "video": "google/film"}


def test_the_code_models_are_the_stored_ones(monkeypatch):
    monkeypatch.setitem(settings_store._cache, "values", {
        "models.code": "local/qwen3.8-27b", "models.code_fallback": "local/qwen3.8-flash",
    })
    assert sandbox_tools.code_models(False) == ["local/qwen3.8-27b", "local/qwen3.8-flash"]
    assert sandbox_tools.code_models(True) == [
        "strict-local/qwen3.8-27b", "strict-local/qwen3.8-flash",
    ]
    monkeypatch.setitem(settings_store._cache, "values", {"models.code_fallback": ""})
    monkeypatch.setattr(settings, "code_model", "local/qwen3.8-27b")
    assert sandbox_tools.code_models(False) == ["local/qwen3.8-27b"]


@pytest.mark.parametrize(
    ("field", "value", "detail"),
    [
        ("model_chat", "", "model_chat_required"),
        ("model_chat", "google/img", "model_chat_invalid"),
        ("model_report", "local/unknown", "model_report_invalid"),
        ("model_image", "", "model_image_required"),
        ("model_image", "local/qwen3.8-27b", "model_image_invalid"),
        ("model_audio", "google/film", "model_audio_invalid"),
        # Side work that reads the conversation stays on self-hosted models.
        ("model_title", "openai/gpt-x", "model_title_invalid"),
        ("model_code", "openai/gpt-x", "model_code_invalid"),
        ("model_code_fallback", "local/borrowed", "model_code_fallback_invalid"),
        ("thinking_self_hosted", "yes", "thinking_self_hosted_invalid"),
        ("default_monthly_credits", "-5", "default_monthly_credits_invalid"),
        ("default_monthly_credits", "9999999999", "default_monthly_credits_invalid"),
    ],
)
async def test_a_model_a_job_may_not_use_is_refused(stored, field, value, detail):
    from app.routers.admin import _check_model_roles
    from app.schemas.admin import SystemSettingsIn

    with pytest.raises(HTTPException) as refused:
        await _check_model_roles(SystemSettingsIn(**{field: value}))
    assert refused.value.status_code == 422 and refused.value.detail == detail


async def test_fitting_choices_and_the_model_in_force_are_accepted(stored):
    from app.routers.admin import _check_model_roles, _model_role_settings
    from app.schemas.admin import SystemSettingsIn

    await _check_model_roles(SystemSettingsIn(
        model_chat="local/qwen3.8-27b", model_report="", model_slides="openai/gpt-x",
        model_image="google/img", model_audio="openai/voice", model_video="google/film",
        model_title="local/qwen3.8-flash", model_code="local/qwen3.8-27b",
        model_code_fallback="strict-local/qwen3.8-flash", thinking_self_hosted="0",
        thinking_external="1", default_monthly_credits="1000000",
    ))
    # A model the gateway stopped serving can be saved again as it is.
    stored["models.title"] = "local/retired"
    await _check_model_roles(SystemSettingsIn(model_title="local/retired"))
    shown = await _model_role_settings()
    assert shown["title"] == "local/retired"
    assert [m["id"] for m in shown["choices"]["helper"]] == [
        "local/qwen3.8-27b", "local/qwen3.8-flash", "strict-local/qwen3.8-flash",
        "strict-local/qwen3.8-27b",
    ]
    assert [m["id"] for m in shown["choices"]["video"]] == ["google/film"]


# ── migration 0068 ──────────────────────────────────────────────────────


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0068_model_roles.py"
    spec = importlib.util.spec_from_file_location("model_roles_0068", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _database(conn, *, classifier, outline, economy, audited=()):
    conn.execute(sa.text(
        "CREATE TABLE system_settings (key VARCHAR PRIMARY KEY, value VARCHAR, "
        "secret BOOLEAN, updated_at DATETIME, updated_by VARCHAR)"
    ))
    conn.execute(sa.text(
        "CREATE TABLE governance (id VARCHAR PRIMARY KEY, adaptive_classifier_model_id VARCHAR, "
        "outline_model_id VARCHAR, adaptive_economy_model_ids JSON, "
        "adaptive_quality_model_ids JSON)"
    ))
    conn.execute(sa.text("CREATE TABLE audit_events (action VARCHAR, metadata JSON)"))
    conn.execute(
        sa.text("INSERT INTO governance VALUES ('default', :c, :o, :e, '[]')"),
        {"c": classifier, "o": outline, "e": json.dumps(economy)},
    )
    for field in audited:
        conn.execute(
            sa.text("INSERT INTO audit_events VALUES ('governance.update', :m)"),
            {"m": json.dumps({"changes": {field: ["a", "b"]}})},
        )


def _rows(conn) -> dict[str, str]:
    return dict(conn.execute(sa.text("SELECT key, value FROM system_settings")).all())


def test_the_migration_seeds_the_environment_once_and_moves_old_defaults(monkeypatch):
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0068", "0067")
    # What production's environment holds: the 27B named for all three surfaces.
    for field, value in {
        "default_chat_model": "local/qwen3.8-27b",
        "default_report_model": "local/qwen3.8-27b",
        "default_slides_model": "local/qwen3.8-27b",
        "title_model": "local/qwen3.8-27b",  # the earlier code default
        "default_monthly_credits": 700_000,
        "self_hosted_chat_thinking": False,
    }.items():
        monkeypatch.setattr(settings, field, value)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _database(
            conn,
            classifier="strict-local/qwen3.8-27b",
            outline="local/qwen3.8-flash-next",
            economy=["local/qwen3.8-flash-next", "local/other"],
        )
        # Saved by an administrator before: left alone.
        conn.execute(sa.text(
            "INSERT INTO system_settings VALUES ('models.code', 'local/mine', 0, NULL, 'admin'),"
            " ('ocr.vision_model', 'strict-local/qwen3.8-flash-next', 0, NULL, 'admin')"
        ))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        rows = _rows(conn)
        assert rows["models.chat"] == rows["models.report"] == rows["models.slides"] == (
            "local/qwen3.8-27b"
        )
        assert rows["models.title"] == "local/qwen3.8-flash"
        assert rows["models.code"] == "local/mine"
        assert rows["models.code_fallback"] == settings.code_fallback_model
        assert rows["thinking.self_hosted"] == "0"
        assert rows["credits.default_monthly"] == "700000"
        assert rows["ocr.vision_model"] == "strict-local/qwen3.8-flash"
        policy = conn.execute(sa.text("SELECT * FROM governance")).mappings().one()
        assert policy["adaptive_classifier_model_id"] == "strict-local/qwen3.8-flash"
        assert policy["outline_model_id"] == "local/qwen3.8-flash"
        assert json.loads(policy["adaptive_economy_model_ids"]) == [
            "local/qwen3.8-flash", "local/other",
        ]
        # A second run (or a later boot) writes nothing over a stored value.
        conn.execute(sa.text("UPDATE system_settings SET value = 'x' WHERE key = 'models.chat'"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert _rows(conn)["models.chat"] == "x"
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        # The seeded rows go, the administrator's own choice of code model with them.
        assert set(_rows(conn)) == {"ocr.vision_model"}


def test_a_classifier_the_administrator_chose_is_kept(monkeypatch):
    migration = _migration()
    monkeypatch.setattr(settings, "title_model", "local/custom-titler")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _database(
            conn,
            classifier="strict-local/qwen3.8-27b",
            outline="local/qwen3.5-122b",
            economy=[],
            audited=("adaptive_classifier_model_id", "outline_model_id"),
        )
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        policy = conn.execute(sa.text("SELECT * FROM governance")).mappings().one()
        assert policy["adaptive_classifier_model_id"] == "strict-local/qwen3.8-27b"
        assert policy["outline_model_id"] == "local/qwen3.5-122b"
        # A title model someone named on purpose is seeded as it is.
        assert _rows(conn)["models.title"] == "local/custom-titler"


def test_new_account_credits_default_is_one_number_everywhere():
    """Code default, compose fallback and both .env examples: 500,000."""
    import re
    from pathlib import Path

    from app.core.config import Settings

    root = Path(__file__).resolve().parents[3]
    assert Settings.model_fields["default_monthly_credits"].default == 500_000
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"\$\{KCHAT_DEFAULT_MONTHLY_CREDITS:-500000\}", compose)
    root_env = (root / ".env.example").read_text(encoding="utf-8")
    api_env = (root / "apps/api/.env.example").read_text(encoding="utf-8")
    assert "KCHAT_DEFAULT_MONTHLY_CREDITS=500000" in root_env
    assert "DEFAULT_MONTHLY_CREDITS=500000" in api_env
