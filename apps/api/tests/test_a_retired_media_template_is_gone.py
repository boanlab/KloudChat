"""Retired image, audio and video 서식: the catalogue has none, a request or saved choice that
still names one is read as naming none, and what was made with one still opens."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException

from app.models.chat import SessionKind
from app.models.workspace import Agent, Artifact, ArtifactKind
from app.routers import sessions as sessions_router
from app.routers import workspace as ws
from app.schemas.chat import ImageRequest
from app.schemas.workspace import ArtifactOut
from app.services import design_templates as dt

# ── gone from the catalogue ────────────────────────────────────────────


def test_the_catalogue_has_no_image_audio_or_video_template():
    assert {t.kind for t in dt.all_templates()} == {"deck", "document"}
    assert set(dt.SURFACE.values()) == {SessionKind.report, SessionKind.slides}
    assert not dt.for_surface(SessionKind.image) and not dt.for_surface(SessionKind.av)
    assert not any((dt._ROOT / tid).exists() for tid in dt.RETIRED_MEDIA)
    assert not dt.RETIRED_MEDIA & {t.id for t in dt.all_templates()}


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", [None, "image", "av"])
async def test_the_gallery_lists_no_media_template(surface):
    rows = await ws.list_design_templates(user=None, surface=surface)
    assert all(row.kind in ("deck", "document") for row in rows)
    if surface:
        assert rows == []
    else:
        assert {row.surface for row in rows} == {"report", "slides"}
    # The card carries no media blanks or chip defaults.
    assert all("arguments" not in row.model_dump(by_alias=True) for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("template_id", ["image-poster", "video-product", "audio-narration"])
async def test_a_retired_media_id_has_no_preview(template_id):
    assert dt.get(template_id) is None and dt.rendering(template_id) is None
    assert dt.is_retired_media(template_id)
    with pytest.raises(HTTPException) as raised:
        await ws.design_template_preview(template_id)
    assert raised.value.status_code == 404


# ── a stale id is read as none ─────────────────────────────────────────


def test_an_agent_still_bound_to_a_media_template_brings_none():
    agent = Agent(name="포스터", owner_id="u1", render_template_id="image-poster")
    assert sessions_router.agent_template(agent, SessionKind.image) is None


def test_a_picture_request_that_still_names_a_retired_template_is_accepted():
    request = ImageRequest(prompt="행사 포스터", template_id="image-poster")
    # The endpoint's gate: a retired id passes as a plain picture, anything else is refused.
    assert dt.is_retired_media(request.template_id)
    assert not dt.is_retired_media("image-nope")


# ── what was made still opens ──────────────────────────────────────────


def test_a_picture_made_with_a_retired_template_still_opens():
    picture = Artifact(
        id="i1",
        user_id="u1",
        kind=ArtifactKind.image,
        title="행사 포스터",
        data={
            "kind": "image",
            "prompt": "행사 포스터",
            "aspect": "9:16",
            "style": "자동",
            "src": "/api/files/f1/content",
            "templateId": "image-poster",
        },
    )
    out = ArtifactOut.model_validate(picture, from_attributes=True)
    assert out.kind == ArtifactKind.image and out.data["src"] == "/api/files/f1/content"


# ── the migration ──────────────────────────────────────────────────────


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0058_retire_media_templates.py"
    spec = importlib.util.spec_from_file_location("retire_media_0058", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_names_the_same_retired_templates():
    assert set(_migration().RETIRED) == dt.RETIRED_MEDIA


def test_the_migration_clears_the_choices_and_keeps_the_rest_on_sqlite():
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0058", "0057")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in (
            "CREATE TABLE agents (id VARCHAR PRIMARY KEY, render_template_id VARCHAR)",
            "CREATE TABLE templates (id VARCHAR PRIMARY KEY, render_template_id VARCHAR)",
            "CREATE TABLE sessions (id VARCHAR PRIMARY KEY, render_template_id VARCHAR)",
            "CREATE TABLE projects (id VARCHAR PRIMARY KEY, render_templates JSON)",
            "INSERT INTO agents VALUES ('a1', 'image-poster'), ('a2', 'doc-minutes')",
            "INSERT INTO templates VALUES ('t1', 'video-product'), ('t2', 'deck-case')",
            "INSERT INTO sessions VALUES ('s1', 'image-method'), ('s2', 'doc-brief'), "
            "('s3', NULL)",
        ):
            conn.execute(sa.text(statement))
        conn.execute(
            sa.text("INSERT INTO projects VALUES (:a, :ra), (:b, :rb), ('p3', NULL)"),
            {
                "a": "p1",
                "ra": json.dumps({"image": "image-scene", "slides": "deck-proposal"}),
                "b": "p2",
                "rb": json.dumps({"report": "doc-minutes"}),
            },
        )
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        agents = dict(conn.execute(sa.text("SELECT id, render_template_id FROM agents")).all())
        assert agents == {"a1": None, "a2": "doc-minutes"}
        kept = dict(conn.execute(sa.text("SELECT id, render_template_id FROM templates")).all())
        assert kept == {"t1": "", "t2": "deck-case"}
        # A document's retired id stays for `rendering`; a media id is cleared.
        sessions = dict(conn.execute(sa.text("SELECT id, render_template_id FROM sessions")).all())
        assert sessions == {"s1": None, "s2": "doc-brief", "s3": None}
        projects = {
            row_id: json.loads(value) if value else None
            for row_id, value in conn.execute(sa.text("SELECT id, render_templates FROM projects"))
        }
        assert projects == {
            "p1": {"slides": "deck-proposal"},
            "p2": {"report": "doc-minutes"},
            "p3": None,
        }
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
