"""Every default `docs/configuration.md` prints is the one the code uses.

That table is what an operator reads before changing a value, and a wrong
number there is worse than no number: it sends somebody to the right file to
set the wrong thing. The drift this was written against had
`LITELLM_TIMEOUT_SEC` documented as the model-call timeout at 900 seconds when
it is the master-key client's at 20 — a person whose long generations were
being cut off would have raised a setting that has nothing to do with them.

Read out of the file rather than restated here, so the test cannot agree with a
copy of the documentation instead of with the documentation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import Settings

_DOCS = Path(__file__).resolve().parents[3] / "docs" / "configuration.md"

#: A settings name, which is how a row of one of these tables is recognised.
_IS_SETTING = re.compile(r"[A-Z][A-Z0-9_]*")

#: Rows whose "default" cell is prose rather than a value — "derived", an em
#: dash for "no default", or a pointer at another table. Nothing to compare.
_NOT_A_VALUE = {"", "—", "-", "derived", "the private ranges plus loopback"}


def _documented() -> list[tuple[str, str]]:
    """`(NAME, default)` for every settings row in the file.

    Two table shapes. The integration tables put the `.env` name and the
    container name in the first two columns and the default in the third; the
    advanced table has the name first and the default second. Which one a row
    is decided by whether the second cell is itself a setting name.

    A row may also name several settings and give their defaults in the same
    order — `ARGON2_TIME_COST / ARGON2_MEMORY_COST / ARGON2_PARALLELISM`
    against `3 / 65536 / 4`. Only then is the value cell split on `/`, because
    a single value is often a path or a model id with slashes in it.
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
        # Container column, so the default is one further along.
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
    """A parser that matched nothing would make every assertion below vacuous."""
    assert len(DOCUMENTED) >= 15, [n for n, _ in DOCUMENTED]


@pytest.mark.parametrize("name,documented", DOCUMENTED, ids=[n for n, _ in DOCUMENTED])
def test_a_documented_default_is_the_real_one(name: str, documented: str):
    actual = _code_default(name)
    # Written for a reader: `24000` for `24_000`, `20` for `20.0`, and an
    # unquoted string for a quoted one. Compared as the value, not as the text.
    if isinstance(actual, bool):
        assert documented.lower() == str(actual).lower()
    elif isinstance(actual, (int, float)):
        assert float(documented.replace(",", "").replace("_", "")) == float(actual)
    else:
        assert documented == str(actual)
