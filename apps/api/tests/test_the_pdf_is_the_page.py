"""A PDF export is the document's own HTML printed by the sidecar, with a structural fallback."""

from __future__ import annotations

import httpx
import pytest

from app.services import printing


class _Response:
    def __init__(self, content: bytes = b"%PDF-1.4 printed", status: int = 200):
        self.content = content
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


def _client(monkeypatch, on_post):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, url, json=None):
            return await on_post(url, json)

    monkeypatch.setattr(printing.httpx, "AsyncClient", lambda **_kw: _Client())


@pytest.mark.anyio
async def test_the_printer_gets_the_file_itself(monkeypatch):
    """The artifact's own bytes go to the printer, unreshaped."""
    seen: dict[str, object] = {}

    async def on_post(url, json):
        seen["url"] = url
        seen["html"] = json["html"]
        return _Response()

    _client(monkeypatch, on_post)
    monkeypatch.setattr(printing.settings, "print_base_url", "http://printer:8200/")

    html = "<!doctype html><html><body><p>본문</p></body></html>"
    assert await printing.to_pdf(html) == b"%PDF-1.4 printed"
    # A trailing slash in the setting must not become a double slash.
    assert seen["url"] == "http://printer:8200/pdf"
    assert seen["html"] == html


@pytest.mark.anyio
async def test_with_no_printer_configured_nothing_is_attempted(monkeypatch):
    """No printer configured means `None` without a request."""

    async def on_post(url, json):  # pragma: no cover - must not run
        raise AssertionError("asked a printer that was never configured")

    _client(monkeypatch, on_post)
    monkeypatch.setattr(printing.settings, "print_base_url", "   ")

    assert await printing.to_pdf("<p>x</p>") is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param(lambda: (_ for _ in ()).throw(httpx.ConnectError("down")), id="연결 실패"),
        pytest.param(lambda: _Response(status=500), id="서버 오류"),
        pytest.param(lambda: _Response(content=b""), id="빈 응답"),
    ],
)
async def test_a_broken_printer_costs_the_design_and_not_the_file(monkeypatch, outcome):
    """Every printer failure reaches the caller as `None`."""

    async def on_post(url, json):
        return outcome()

    _client(monkeypatch, on_post)
    monkeypatch.setattr(printing.settings, "print_base_url", "http://printer:8200")

    assert await printing.to_pdf("<p>x</p>") is None


def _artifact(html: str, template_id: str):
    """The artifact shape `_export_page` reads, without a database."""
    from app.models.workspace import Artifact, ArtifactKind

    return Artifact(
        id="a1",
        user_id="u1",
        kind=ArtifactKind.html,
        title="상반기 보고",
        data={"content": html, "templateId": template_id},
    )


def _written(template_id: str) -> str:
    """A real document assembled through `design_templates`, placeholders resolved."""
    from app.services import design_templates

    template = design_templates.get(template_id)
    assert template is not None, template_id
    body = design_templates.assemble(
        template,
        [
            {"layout": "cover", "title": "상반기 보고", "html": "<p>정보보호팀</p>"},
            {"layout": "section", "title": "현황", "html": "<p>탐지는 1,204건이다.</p>"},
        ],
    )
    return design_templates.render(template, title="상반기 보고", tokens={}, body=body)


@pytest.mark.anyio
@pytest.mark.parametrize("template_id", ["doc-report", "deck-editorial"])
async def test_the_export_hands_the_printer_the_whole_document(monkeypatch, template_id):
    """Both the deck and the document track hand the printer the whole file."""
    from app.routers import workspace as router

    seen: dict[str, str] = {}

    async def to_pdf(html: str) -> bytes:
        seen["html"] = html
        return b"%PDF-1.4 printed"

    monkeypatch.setattr(router.printing, "to_pdf", to_pdf)

    html = _written(template_id)
    reply = await router._export_page(_artifact(html, template_id), "pdf")

    assert reply.body == b"%PDF-1.4 printed"
    assert reply.media_type == "application/pdf"
    # The finished file, stylesheet included, not a re-render.
    assert seen["html"] == html
    assert "<style" in seen["html"]


@pytest.mark.anyio
@pytest.mark.parametrize("template_id", ["doc-report", "deck-editorial"])
async def test_without_a_printer_a_pdf_still_comes_out(monkeypatch, template_id):
    """Without a printer the structural PDF is returned."""
    from app.routers import workspace as router

    async def to_pdf(html: str) -> None:
        return None

    monkeypatch.setattr(router.printing, "to_pdf", to_pdf)

    reply = await router._export_page(_artifact(_written(template_id), template_id), "pdf")
    assert reply.media_type == "application/pdf"
    assert reply.body.startswith(b"%PDF")
    # Drawn locally, so larger than the stub above.
    assert len(reply.body) > 1000


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("template_id", "look", "dark"),
    [
        ("deck-lecture", "poster", False),
        ("deck-proposal", "mono", False),
        ("deck-signal", "dark", True),
    ],
)
async def test_editable_deck_export_keeps_the_template_face(
    monkeypatch, template_id, look, dark
):
    """PPTX receives the face that dressed the HTML, not editorial defaults."""
    from app.routers import workspace as router

    seen = {}

    def to_pptx(_title, _slides, **kwargs):
        seen.update(kwargs)
        return b"pptx"

    monkeypatch.setattr(router.deck_export, "to_pptx", to_pptx)
    reply = await router._export_page(_artifact(_written(template_id), template_id), "pptx")

    assert reply.body == b"pptx"
    assert seen["tokens"]["visualStyle"] == look
    assert seen["dark"] is dark
