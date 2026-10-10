"""The connector catalogue as migration 0062 seeds it, for tests without a database."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_MIGRATION = next((Path(__file__).resolve().parents[1] / "alembic" / "versions").glob(
    "0062_*.py"))


def seed() -> list[dict[str, Any]]:
    spec = importlib.util.spec_from_file_location("migration_0062", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return [dict(row) for row in module.SEED]


def entries() -> list[dict[str, Any]]:
    """The seed rows as `catalog.use` holds them."""
    return [{**row, "id": row["slug"], "env": dict(row["env"] or {}), "required_env": [],
             "auth": "none", "enabled": True, "position": n, "updated_at": None,
             "tools_key_for": (row["tools_key_var"], row["tools_key_feature"])
             if row["tools_key_var"] else None}
            for n, row in enumerate(seed())]
