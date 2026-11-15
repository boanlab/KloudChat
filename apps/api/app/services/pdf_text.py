"""Text layer of a PDF, page by page, with the word spacing the page shows.

pypdf rebuilds spaces from glyph gaps and, for Korean set in many CID fonts, drops most of
them (「온프레미스에서는서버를사기위한초기투자」); every later step (search, citations, the
studio's spelling check) then copies that run-together text. PDFium (pypdfium2, Apache-2.0 /
BSD-3-Clause) reads the same pages with the spacing a viewer shows, so it is tried first and
pypdf stays as the fallback for files PDFium refuses."""

from __future__ import annotations

import io
import logging
import re

log = logging.getLogger(__name__)

#: PDFium marks a hyphen it took for a line-end break with U+FFFE ("How￾\never")
_SOFT_BREAK = re.compile("￾\r?\n?")
#: blanks before closing punctuation PDFium leaves between runs ("반납한다 ." → "반납한다.")
_SPACE_BEFORE = re.compile(r"[ \t]+(?=[.,;:!?)\]」』])")


def _tidy(text: str) -> str:
    text = _SOFT_BREAK.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = _SPACE_BEFORE.sub("", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def _pdfium_pages(data: bytes, limit: int | None) -> list[str]:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    try:
        count = len(document)
        if limit is not None and count > limit:
            raise ValueError("too many pages")
        pages: list[str] = []
        for index in range(count):
            try:
                page = document[index]
                textpage = page.get_textpage()
                pages.append(_tidy(textpage.get_text_range()))
                textpage.close()
                page.close()
            except Exception as exc:  # noqa: BLE001 — one bad page must not lose the rest
                log.info("pdf page %d unreadable: %s", index, exc)
                pages.append("")
        return pages
    finally:
        document.close()


def _pypdf_pages(data: bytes, limit: int | None) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if limit is not None and len(reader.pages) > limit:
        raise ValueError("too many pages")
    pages: list[str] = []
    for index, page in enumerate(reader.pages):
        try:
            pages.append((page.extract_text() or "").strip())
        except Exception as exc:  # noqa: BLE001 — one bad page must not lose the rest
            log.info("pdf page %d unreadable: %s", index, exc)
            pages.append("")
    return pages


def page_texts(data: bytes, *, limit: int | None = None) -> list[str]:
    """Each page's text ("" for a page with no text layer). Raises when the file is no PDF
    either reader opens, and `ValueError` past `limit` pages."""
    try:
        return _pdfium_pages(data, limit)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 — PDFium refuses some damaged files pypdf reads
        log.info("pdfium could not open the pdf, trying pypdf: %s", exc)
    return _pypdf_pages(data, limit)
