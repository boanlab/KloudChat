"""An exported PDF is the document the screen shows, not a second drawing of it.

A document written into a 서식 is one self-contained HTML file: the 서식's
stylesheet, its `@page` rule, its print rules, and the model's blocks inside
them. Those rules were written, committed and never used, because for most of
this product's life nothing in the image could read CSS — so the PDF was drawn
again by reportlab, from the same words and none of the design.

What has to hold now:

  · when a printer is configured, the PDF *is* that HTML file printed
  · when one is not, or it is broken, a PDF still comes out
  · a printer failure never costs the user the download, only the design
"""

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
    """Not a re-render — the artifact's own bytes go to the browser.

    If anything reshaped the markup on the way, the PDF would be a third
    rendering of the document, which is the problem this replaced.
    """
    seen: dict[str, object] = {}

    async def on_post(url, json):
        seen["url"] = url
        seen["html"] = json["html"]
        return _Response()

    _client(monkeypatch, on_post)
    monkeypatch.setattr(printing.settings, "print_base_url", "http://printer:8200/")

    html = "<!doctype html><html><body><p>본문</p></body></html>"
    assert await printing.to_pdf(html) == b"%PDF-1.4 printed"
    # The trailing slash in the setting must not become a double slash: some
    # servers 404 on it and the fallback would then be silent and permanent.
    assert seen["url"] == "http://printer:8200/pdf"
    assert seen["html"] == html


@pytest.mark.anyio
async def test_with_no_printer_configured_nothing_is_attempted(monkeypatch):
    """`None` without a request, so an install without the sidecar is not
    waiting on a connection refused for every export."""

    async def on_post(url, json):  # pragma: no cover - must not run
        raise AssertionError("asked a printer that was never configured")

    _client(monkeypatch, on_post)
    monkeypatch.setattr(printing.settings, "print_base_url", "   ")

    assert await printing.to_pdf("<p>x</p>") is None
    assert printing.available() is False


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
    """Every way of failing reaches the caller the same way.

    The caller's answer to all of them is to draw the PDF structurally instead,
    so telling them apart would only be deciding whether somebody gets a file.
    """

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
    """A real document, assembled the way the page track assembles one.

    Built through `design_templates` rather than hand-written, so the test is
    looking at the same string an export looks at — placeholders resolved,
    seed and all.
    """
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
    """Both tracks, because a deck and a document take different branches and
    only one of them was wired the first time this was written."""
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
    # The finished file, not a re-render of its parts: the seed's own stylesheet
    # has to be in what the browser is asked to print, or the PDF is designed by
    # nobody.
    assert seen["html"] == html
    assert "<style" in seen["html"]


@pytest.mark.anyio
@pytest.mark.parametrize("template_id", ["doc-report", "deck-editorial"])
async def test_without_a_printer_a_pdf_still_comes_out(monkeypatch, template_id):
    """The fallback is the point of the whole optional return.

    An upgrade that has not added the sidecar yet, or one that never will, must
    keep exporting — a PDF with the words and not the design, rather than an
    error where a download used to be.
    """
    from app.routers import workspace as router

    async def to_pdf(html: str) -> None:
        return None

    monkeypatch.setattr(router.printing, "to_pdf", to_pdf)

    reply = await router._export_page(_artifact(_written(template_id), template_id), "pdf")
    assert reply.media_type == "application/pdf"
    assert reply.body.startswith(b"%PDF")
    # Drawn here rather than fetched, so it is bigger than the stub above and
    # unmistakably a real file.
    assert len(reply.body) > 1000
