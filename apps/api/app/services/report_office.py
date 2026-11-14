"""A report asked for as an HWP or Word file: the report pipeline writes it, and the file is
what the person gets and works on (`office_doc`), not the HTML report.

The format comes from the person's own words (「한글 문서로」, 「Word 파일로」, and on a report
request 「한글로」). At the save the finished sections become the file through the exporters
(`report_export.to_hwpx` / `to_docx`), with what the browser would draw first drawn here: each
chart and mermaid figure as a picture, through the printer sidecar. A figure the printer could
not draw keeps the exporters' own fallback (a chart as its numbers' table).
"""

from __future__ import annotations

import base64
import io
import logging
import re
from typing import Any

import httpx

from app.core.config import settings
from app.models.workspace import ArtifactKind
from app.services import charts as chartkit
from app.services import design, design_templates, office_look, report_export, richtext

log = logging.getLogger(__name__)

#: 「한글 문서로」, 「hwp 파일」, 「아래아한글」: a request for an HWP document.
ASKS_HWP = re.compile(r"(?<![A-Za-z])hwpx?(?![A-Za-z])|한글\s{0,2}(?:파일|문서)|아래아\s{0,2}한글",
                      re.I)
#: 「Word로」, 「docx 파일」, 「워드 문서」: a request for a Word document (not 「키워드로」).
ASKS_DOCX = re.compile(
    r"(?<![A-Za-z])docx(?![A-Za-z])|(?<![A-Za-z])(?:ms\s?)?word\s{0,2}(?:로|으로|문서|파일|"
    r"documents?|files?|format)|(?<![가-힣])워드\s{0,2}(?:로|문서|파일)",
    re.I,
)
#: 「한글로 보고서 써 줘」: on a document order the word processor named by its short name. Read
#: as the language instead whenever the sentence talks about language (「영어 말고 한글로」).
_HANGUL_TO = re.compile(r"(?<![가-힣])한글\s{0,2}(?:로|으로)(?![가-힣])")
_LANGUAGE = re.compile(
    r"영어|영문|한국어|국문|외국어|일본어|중국어|번역|언어|맞춤법|english|korean|language", re.I
)

#: what each office kind's file is called by the exporters
FORMATS = {ArtifactKind.hwp: "hwpx", ArtifactKind.docx: "docx"}
#: picture rendering through the printer: whole report, a few figures each
_TIMEOUT = httpx.Timeout(90.0, connect=5.0)


def _own_words(request: str) -> str:
    """The request without pasted material or the conditions the server appended."""
    from app.services.context import instruction_part

    text = request or ""
    if (marker := text.find("\n\n덧붙인 조건:")) >= 0:
        text = text[:marker]
    return instruction_part(text)


def asked_file(request: str) -> ArtifactKind | None:
    """The office file a report request names (`hwp`, `docx`), the first one named; else None."""
    words = _own_words(request)
    found: list[tuple[int, ArtifactKind]] = []
    if match := ASKS_HWP.search(words):
        found.append((match.start(), ArtifactKind.hwp))
    elif (match := _HANGUL_TO.search(words)) and not _LANGUAGE.search(words):
        found.append((match.start(), ArtifactKind.hwp))
    if match := ASKS_DOCX.search(words):
        found.append((match.start(), ArtifactKind.docx))
    return min(found, key=lambda pair: pair[0])[1] if found else None


def _chart_deck(chart: dict, style: dict) -> bytes | None:
    """One-slide .pptx holding the chart as the documents draw it (`charts.part`)."""
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Pt

    try:
        payload = CategoryChartData()
        payload.categories = chart["categories"]
        for name, values in chart["series"]:
            payload.add_series(name or " ", values)
        deck = Presentation()
        # the text column's width at the documents' figure ratio
        deck.slide_width, deck.slide_height = Pt(470), Pt(270)
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        background = slide.background.fill
        background.solid()
        background.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        frame = slide.shapes.add_chart(
            XL_CHART_TYPE.LINE_MARKERS if chart["kind"] == "line"
            else XL_CHART_TYPE.COLUMN_CLUSTERED,
            Pt(6), Pt(6), Pt(458), Pt(258), payload,
        )
        chartkit.apply(
            frame.chart, kind=chart["kind"], unit=chart.get("unit") or "",
            accent=RGBColor.from_string(style["accent"].lstrip("#").upper()),
            muted=RGBColor.from_string(style["muted"].lstrip("#").upper()),
            faces=chartkit.FACES["serif" if style.get("font") == "serif" else "gothic"],
        )
        out = io.BytesIO()
        deck.save(out)
        return out.getvalue()
    except Exception as exc:  # noqa: BLE001 — the chart keeps its table fallback
        log.warning("chart slide not built: %s", exc)
        return None


async def _slide_png(blob: bytes) -> bytes | None:
    """The one slide as a PNG, by the printer (LibreOffice), cropped to its drawing."""
    from app.services.diagram_render import _trimmed

    base = settings.print_base_url.strip().rstrip("/")
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                f"{base}/pptx/png", json={"pptx": base64.b64encode(blob).decode(), "dpi": 192}
            )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.info("chart not rendered by the printer, keeping its table: %s", exc)
        return None
    return _trimmed(response.content, pad=8) if response.content else None


def _fences(content: str) -> list[tuple[str, str]]:
    """`(language, source)` of each chart or mermaid fence, as the exporters read them."""
    out: list[tuple[str, str]] = []
    language, lines = None, []
    for line in (content or "").splitlines():
        stripped = line.strip()
        if language is None:
            if stripped.startswith("```"):
                language, lines = stripped[3:].strip().lower(), []
            continue
        if stripped.startswith("```"):
            if language in ("chart", "mermaid"):
                out.append((language, "\n".join(lines)))
            language = None
            continue
        lines.append(line)
    return out


async def drawn(sections: list[dict], tokens: dict | None) -> list[dict]:
    """Sections with each chart and mermaid figure drawn as a picture into `diagrams`, where
    the exporters look for the browser's raster (`report_export._diagram_picture`)."""
    from app.services import diagram_render

    style = design.normalise_tokens(tokens)
    out: list[dict] = []
    for section in sections:
        store = dict(section.get("diagrams") or {})
        for language, source in _fences(str(section.get("content") or "")):
            key = report_export.diagram_key(source)
            if store.get(key):
                continue
            png: bytes | None = None
            if language == "chart":
                chart = report_export._chart_block(source)
                deck = _chart_deck(chart, style) if chart else None
                png = await _slide_png(deck) if deck else None
            else:
                png = await diagram_render.render_png(source, style["accent"])
            if png:
                store[key] = diagram_render.data_uri(png)
        out.append({**section, "diagrams": store} if store else section)
    return out


def _sections(data: dict) -> list[dict]:
    """The report's sections as the exporters read them, references last."""
    sections = richtext.normalise(list(data.get("sections") or []))
    return report_export.with_references(
        sections, list(data.get("sources") or []), str(data.get("citationStyle") or "APA")
    )


def file_bytes(kind: ArtifactKind, title: str, data: dict[str, Any],
               sections: list[dict] | None = None) -> bytes:
    """The report (`Artifact.data` of a report) as an HWPX or Word file, as its export makes it."""
    sections = _sections(data) if sections is None else sections
    tokens = data.get("design") or None
    page_settings = data.get("pageSettings") or None
    title_block = data.get("titleBlock") or None
    if kind is ArtifactKind.hwp:
        # the report's template as HWPX can carry it (margins, body face, heading colour),
        # under the report's own theme and page settings (`office_look`)
        tokens, page_settings = office_look.hwpx(office_look.Look(
            tokens=tokens, template_id=str(data.get("templateId") or ""),
            page_settings=dict(page_settings or {})))
        return report_export.to_hwpx(
            title, sections, tokens=tokens, page_settings=page_settings, title_block=title_block
        )
    chosen = design_templates.rendering(str(data.get("templateId") or ""))
    return report_export.to_docx(
        title,
        sections,
        tokens=tokens,
        template=chosen.docx_template if chosen else "",
        page_settings=page_settings,
        title_block=title_block,
        body_from_design=bool(chosen and chosen.body_face_from_design),
        charts_as_pictures=True,
        static_toc=True,
    )


async def write(kind: ArtifactKind, title: str, data: dict[str, Any]) -> bytes:
    """`file_bytes` with the figures drawn first: nothing in the file waits for a browser."""
    return file_bytes(kind, title, data, await drawn(_sections(data), data.get("design")))


__all__ = ["ASKS_DOCX", "ASKS_HWP", "FORMATS", "asked_file", "drawn", "file_bytes", "write"]
