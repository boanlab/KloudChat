"""Reports, decks and the image surface draw every figure with the .pptx export's shapes.

A source the shapes cannot read goes back to the writer once; still unreadable, or no
renderer answering, the figure is left out. No mermaid fence and no browser drawing stand in.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models.chat import ChatSession, SessionKind
from app.models.governance import Governance
from app.routers import sessions as sessions_router
from app.schemas.chat import DiagramRequest
from app.services import diagram, diagram_shapes, diagrams

_FLOW = "flowchart LR\n  a(수집) --> b(분석) --> c(집행)"
_SEQUENCE = "sequenceDiagram\n  A->>B: 요청"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (640).to_bytes(4, "big") + (
    360
).to_bytes(4, "big")


def _writer(monkeypatch, answers: list[str]) -> list[dict]:
    calls: list[dict] = []

    async def draw(**kwargs):
        calls.append(kwargs)
        return answers[len(calls) - 1], "수집·분석·집행", {"inputTokens": 5, "outputTokens": 7}

    monkeypatch.setattr(diagram, "draw", draw)
    return calls


def _renderer(monkeypatch, png: bytes | None) -> list[str]:
    seen: list[str] = []

    async def render_png(source, accent="#2563EB"):
        seen.append(accent)
        return png

    monkeypatch.setattr(diagram.diagram_render, "render_png", render_png)
    return seen


# ── one figure ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_drawable_figure_is_rendered_from_its_shapes(monkeypatch):
    calls = _writer(monkeypatch, [_FLOW])
    accents = _renderer(monkeypatch, _PNG)
    source, caption, usage, png = await diagram.draw_native(
        description="수집해 분석하고 집행한다", figure="flow", model="m", api_key="k",
        accent="#7a1f3d",
    )
    assert source == _FLOW and png == _PNG and caption == "수집·분석·집행"
    assert len(calls) == 1 and accents == ["#7a1f3d"]
    assert usage == {"inputTokens": 5, "outputTokens": 7}


@pytest.mark.asyncio
async def test_an_undrawable_source_is_asked_for_once_as_a_flowchart(monkeypatch):
    calls = _writer(monkeypatch, [_SEQUENCE, _FLOW])
    _renderer(monkeypatch, _PNG)
    source, _caption, usage, _png = await diagram.draw_native(
        description="요청이 오간다", figure="flow", model="m", api_key="k"
    )
    assert source == _FLOW and len(calls) == 2
    # The rewrite carries the refused source and says why.
    assert calls[1]["broken"] == _SEQUENCE and "flowchart" in calls[1]["error"]
    assert str(diagram_shapes.MAX_NODES) in calls[1]["error"]
    # Both rounds are spent.
    assert usage == {"inputTokens": 10, "outputTokens": 14}


@pytest.mark.asyncio
async def test_still_undrawable_after_the_rewrite_is_left_out(monkeypatch):
    calls = _writer(monkeypatch, [_SEQUENCE, _SEQUENCE])
    accents = _renderer(monkeypatch, _PNG)
    with pytest.raises(diagram.Undrawable) as raised:
        await diagram.draw_native(description="…", figure="flow", model="m", api_key="k")
    assert raised.value.reason == "not_drawable" and len(calls) == 2
    assert raised.value.usage == {"inputTokens": 10, "outputTokens": 14}
    # Never rendered, never handed to a browser.
    assert accents == []


@pytest.mark.asyncio
async def test_too_many_nodes_is_undrawable_too(monkeypatch):
    many = "flowchart LR\n" + "\n".join(
        f"  n{i}(단계{i}) --> n{i + 1}(단계{i + 1})" for i in range(diagram_shapes.MAX_NODES + 2)
    )
    assert diagram_shapes.parse(many) is None
    _writer(monkeypatch, [many, many])
    _renderer(monkeypatch, _PNG)
    with pytest.raises(diagram.Undrawable):
        await diagram.draw_native(description="…", figure="flow", model="m", api_key="k")


@pytest.mark.asyncio
async def test_no_renderer_is_the_same_as_undrawable(monkeypatch, caplog):
    _writer(monkeypatch, [_FLOW])
    _renderer(monkeypatch, None)
    with caplog.at_level("WARNING"), pytest.raises(diagram.Undrawable) as raised:
        await diagram.draw_native(description="…", figure="flow", model="m", api_key="k")
    assert raised.value.reason == "renderer_unavailable"
    assert any("renderer did not answer" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_the_print_sidecar_unset_renders_nothing(monkeypatch):
    monkeypatch.setattr(diagram.diagram_render.settings, "print_base_url", "")
    assert await diagram.diagram_render.render_png(_FLOW) is None


@pytest.mark.asyncio
async def test_a_document_figure_that_cannot_be_drawn_raises_for_its_caller(monkeypatch):
    """The report and deck writers catch this and leave the part as words."""
    _writer(monkeypatch, [_SEQUENCE, _SEQUENCE])
    _renderer(monkeypatch, _PNG)
    planned = diagrams.Planned(index=0, figure="flow", description="요청이 오간다", caption="")
    with pytest.raises(diagram.Undrawable):
        await diagrams.make(planned, model="m", api_key="k", slide=False)


def test_the_writer_is_told_flowchart_only_within_the_node_limit():
    system = diagram._messages("설명", "flow", "ko")[0]["content"]
    assert "flowchart LR" in system and "sequenceDiagram" in system
    assert f"{diagram_shapes.MAX_NODES}개를 넘는" in system and "{max_nodes}" not in system


# ── the manual insert: POST /sessions/{id}/diagrams ────────────────────


class _Db:
    def __init__(self) -> None:
        self.added: list = []
        self.commits = 0

    def add(self, row) -> None:
        self.added.append(row)

    def is_modified(self, _row) -> bool:
        return False

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _row) -> None:
        return None


class _User:
    id = "u1"
    allowed_models: list[str] = []


@pytest.fixture
def endpoint(monkeypatch):
    session = ChatSession(id="s1", user_id="u1", kind=SessionKind.report, title="보고")

    async def owned(db, user, session_id):
        return session

    async def models():
        return {"models": [{"id": "m", "kinds": ["chat"], "creditCost": 0}]}

    async def nothing(*_a, **_k):
        return None

    async def credentials(_user):
        return "http://x", "k"

    async def policy():
        return Governance()

    async def no_history(db, session_id):
        return []

    monkeypatch.setattr(sessions_router, "_owned", owned)
    monkeypatch.setattr(sessions_router, "_require_egress_policy", policy)
    monkeypatch.setattr(sessions_router, "_history", no_history)
    monkeypatch.setattr(sessions_router.model_service, "list_models", models)
    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_a: True)
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", nothing)
    monkeypatch.setattr(sessions_router.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions_router.file_service, "write_blob", lambda *_a: "key")
    return session


@pytest.mark.asyncio
async def test_the_insert_returns_the_figure_drawn_as_shapes(monkeypatch, endpoint):
    _writer(monkeypatch, [_FLOW])
    _renderer(monkeypatch, _PNG)
    db = _Db()
    out = await sessions_router.write_diagram(
        "s1", DiagramRequest(description="수집해 분석하고 집행한다", figure="flow"), _User(), db,
        None,
    )
    assert out.source == _FLOW and out.artifact is not None
    data = out.artifact.data
    assert data["renderer"] == "pptx" and data["source"] == _FLOW and data["style"] == "figure"
    assert (data["width"], data["height"]) == (640, 360)
    assert db.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answers", "png", "detail"),
    [
        ([_SEQUENCE, _SEQUENCE], _PNG, "diagram_not_drawable"),
        ([_FLOW], None, "diagram_unrendered"),
    ],
)
async def test_an_insert_that_cannot_be_drawn_is_refused_without_a_picture(
    monkeypatch, endpoint, answers, png, detail
):
    _writer(monkeypatch, answers)
    _renderer(monkeypatch, png)
    db = _Db()
    with pytest.raises(HTTPException) as raised:
        await sessions_router.write_diagram(
            "s1", DiagramRequest(description="…", figure="flow"), _User(), db, None
        )
    assert raised.value.status_code == 422 and raised.value.detail == detail
    # No artifact, no stored file.
    assert not db.added


def test_there_is_no_browser_store_route_any_more():
    paths = {route.path for route in sessions_router.router.routes}
    assert "/sessions/{session_id}/diagrams" in paths or "/{session_id}/diagrams" in paths
    assert not any(path.endswith("/diagrams/store") for path in paths)


# ── a drawn figure placed on a slide ───────────────────────────────────


@pytest.mark.asyncio
async def test_a_figure_placed_on_a_slide_keeps_its_source_and_the_slide_s_accent(monkeypatch):
    from app.models.workspace import Artifact, ArtifactKind
    from app.routers import workspace
    from app.schemas.workspace import SlideImage
    from app.services.report_export import diagram_key

    accents: list[str] = []

    async def render_png(source, accent="#2563EB"):
        accents.append(accent)
        return _PNG

    async def picture_bytes(db, user, artifact_id):
        return "image/png", _PNG

    figure = Artifact(
        id="f1", user_id="u1", kind=ArtifactKind.image, title="흐름",
        data={"style": "figure", "figure": "flow", "source": _FLOW, "caption": "처리 흐름",
              "prompt": "수집해 분석하고 집행한다", "renderer": "pptx"},
    )
    deck_row = Artifact(
        id="d1", user_id="u1", kind=ArtifactKind.deck, title="덱",
        data={"slides": [{"id": "s1", "layout": "bullets", "accent": "#7a1f3d"}]},
    )

    class Db(_Db):
        async def get(self, _model, item_id):
            return {"f1": figure, "d1": deck_row}.get(item_id)

    monkeypatch.setattr(workspace, "_picture_bytes", picture_bytes)
    monkeypatch.setattr(workspace.diagram_render, "render_png", render_png)
    await workspace.add_slide_image(
        "d1", SlideImage(slide_id="s1", artifact_id="f1", caption=""), _User(), Db()
    )
    slide = deck_row.data["slides"][0]
    assert accents == ["#7a1f3d"]
    assert slide["diagram"]["source"] == _FLOW and slide["diagram"]["key"] == diagram_key(_FLOW)
    assert slide["image"]["renderer"] == "pptx" and slide["image"]["diagram"] is True
    assert slide["image"]["caption"] == "처리 흐름"
