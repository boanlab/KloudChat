"""Two migrations branching from one parent leave `alembic upgrade head` ambiguous and the
deploy's migrate step failing; this fails the branch that adds the second head."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

API = Path(__file__).resolve().parents[1]


def test_the_migrations_have_one_head():
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"alembic has {len(heads)} heads {sorted(heads)}: renumber the newer"
