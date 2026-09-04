"""Defaults in docs/configuration.md match app.core.config.Settings."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import Settings

_DOCS = Path(__file__).resolve().parents[3] / "docs" / "configuration.md"

#: A settings name marks a table row worth comparing.
_IS_SETTING = re.compile(r"[A-Z][A-Z0-9_]*")

#: Default cells that are prose rather than a value.
_NOT_A_VALUE = {"", "—", "-", "derived", "the private ranges plus loopback"}


def _documented() -> list[tuple[str, str]]:
    """`(NAME, default)` for every settings row in the file.

    Tables are `name | container | default` or `name | default`; a row naming
    several settings with `/` gives their defaults in the same order.
    """
    pairs: list[tuple[str, str]] = []
    for line in _DOCS.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        names = [n.strip(" `") for n in cells[0].split("/") if n.strip(" `")]
        if not names or not all(_IS_SETTING.fullmatch(n) for n in names):
            continue
        at = 2 if len(cells) > 2 and _IS_SETTING.fullmatch(cells[1].strip(" `")) else 1
        if at >= len(cells):
            continue
        cell = cells[at]
        values = [v.strip(" `") for v in cell.split("/")] if len(names) > 1 else [cell.strip(" `")]
        if len(names) != len(values):
            continue
        pairs.extend(zip(names, values, strict=True))
    return pairs


def _code_default(name: str):
    field = Settings.model_fields.get(name.lower())
    return field.default if field else None


DOCUMENTED = [
    (name, value)
    for name, value in _documented()
    if value.lower() not in _NOT_A_VALUE and _code_default(name) is not None
]


def test_the_table_was_actually_read():
    """The parser finds enough rows for the parametrised check to mean anything."""
    assert len(DOCUMENTED) >= 15, [n for n, _ in DOCUMENTED]


@pytest.mark.parametrize("name,documented", DOCUMENTED, ids=[n for n, _ in DOCUMENTED])
def test_a_documented_default_is_the_real_one(name: str, documented: str):
    actual = _code_default(name)
    # Compared as values: `24000` vs `24_000`, `20` vs `20.0`.
    if isinstance(actual, bool):
        assert documented.lower() == str(actual).lower()
    elif isinstance(actual, (int, float)):
        assert float(documented.replace(",", "").replace("_", "")) == float(actual)
    else:
        assert documented == str(actual)
