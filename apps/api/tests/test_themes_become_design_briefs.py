"""A project's or agent's look is design-brief text read by the writers; theme rows are not read."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from app.models.chat import SessionKind
from app.models.user import User
from app.routers import workspace as ws
from app.services import design, design_brief, style_spec


def test_a_brief_that_names_a_look_gives_tokens_and_one_that_does_not_gives_none():
    brief = design_brief.of("남색 강조색 #1F3A6B. 본문은 명조.\n그림 스타일: soft watercolour")
    assert brief.tokens["accent"] == "#1f3a6b" and brief.tokens["font"] == "serif"
    assert brief.image_style == "soft watercolour"
    words_only = design_brief.of("말투는 공문체로. 결론을 먼저 쓴다.")
    assert words_only is not None and words_only.tokens is None
    assert design_brief.of("", "  ") is None


def test_the_agents_brief_comes_before_the_projects():
    brief = design_brief.of("강조색 #C0392B.", "강조색 #1F3A6B. 본문은 명조.")
    assert brief.tokens["accent"] == "#c0392b" and brief.tokens["font"] == "serif"
    assert brief.body.index("#C0392B") < brief.body.index("#1F3A6B")


def test_the_writer_reads_the_brief_as_written():
    brief = design_brief.of("표는 세로선 없이. 말투는 공문체.")
    block = design.prompt_block(brief, SessionKind.report)
    assert block.startswith("# 디자인 지침\n") and "세로선 없이" in block
    assert design.image_clause(design_brief.of("그림 스타일: flat icons")) == "flat icons"


def test_a_theme_written_out_reads_back_to_the_same_look():
    text = design_brief.render({"accent": "#334155", "ink": "#111827", "font": "serif",
                                "visualStyle": "minimal"},
                               body="제목은 명사구로 쓴다.", image_style="muted photography",
                               craft=["restraint"])
    spec = style_spec.parse(text)
    assert (spec.accent, spec.ink, spec.body_face, spec.visual_style) == (
        "#334155", "#111827", "serif", "minimal")
    assert "제목은 명사구로 쓴다." in text and "이모지를 쓰지 않는다" in text
    assert design_brief.of(text).image_style == "muted photography"


async def test_themes_are_no_longer_made_or_changed():
    me = User(email="me@example.com", name="나", password_hash="x")
    for call in (ws.create_design(None, me), ws.patch_design("d1", None, me),
                 ws.extract_design(None, me)):
        with pytest.raises(HTTPException) as refused:
            await call
        assert refused.value.status_code == 410 and refused.value.detail == "themes_retired"


# ── the move ───────────────────────────────────────────────────────────


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / \
        "0091_themes_and_templates_retire.py"
    spec = importlib.util.spec_from_file_location("retire_0091", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_move_writes_themes_out_and_gives_agents_their_templates_settings():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0091", "0090")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE design_systems (id TEXT PRIMARY KEY, tokens TEXT, "
                             "body TEXT, image_style TEXT, craft TEXT)"))
        for table in ("projects", "agents"):
            extra = (", render_template_id TEXT, doc_format TEXT, gap_questions TEXT, "
                     "process_settings TEXT, check_skills TEXT") if table == "agents" else ""
            conn.execute(sa.text(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, "
                                 f"design_system_id TEXT, design_brief TEXT DEFAULT ''{extra})"))
        conn.execute(sa.text("CREATE TABLE user_templates (id TEXT PRIMARY KEY, questions TEXT, "
                             "process TEXT, check_skills TEXT)"))
        conn.execute(sa.text("INSERT INTO design_systems VALUES ('d1', :t, '공문체로 쓴다.', "
                             "'muted photo', :c)"),
                     {"t": json.dumps({"accent": "#334155", "font": "serif"}),
                      "c": json.dumps(["restraint"])})
        conn.execute(sa.text("INSERT INTO projects (id, design_system_id) VALUES ('p1', 'd1')"))
        conn.execute(sa.text("INSERT INTO projects (id, design_system_id, design_brief) "
                             "VALUES ('p2', 'd1', '내가 쓴 지침')"))
        conn.execute(sa.text("INSERT INTO agents (id, design_system_id, render_template_id) "
                             "VALUES ('a1', 'd1', 'doc-minutes')"))
        conn.execute(sa.text("INSERT INTO agents (id, render_template_id) VALUES ('a2', 'ut-u1')"))
        conn.execute(sa.text("INSERT INTO user_templates VALUES ('u1', :q, :p, :c)"),
                     {"q": json.dumps([{"id": "when"}]), "p": json.dumps({"search": False}),
                      "c": json.dumps(["gap-check"])})
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        briefs = dict(conn.execute(sa.text("SELECT id, design_brief FROM projects")).all())
        assert "강조색 #334155." in briefs["p1"] and "공문체로 쓴다." in briefs["p1"]
        assert briefs["p1"].endswith(migration.MARK)
        assert briefs["p2"] == "내가 쓴 지침"
        a1 = conn.execute(sa.text(
            "SELECT doc_format, design_brief FROM agents WHERE id='a1'")).one()
        assert a1[0] == "minutes" and "본문은 명조." in a1[1]
        a2 = conn.execute(sa.text(
            "SELECT gap_questions, process_settings, check_skills FROM agents WHERE id='a2'")).one()
        assert [json.loads(v) for v in a2] == [[{"id": "when"}], {"search": False}, ["gap-check"]]
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        briefs = dict(conn.execute(sa.text("SELECT id, design_brief FROM projects")).all())
        assert briefs == {"p1": "", "p2": "내가 쓴 지침"}
