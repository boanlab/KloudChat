"""A report's reference list keeps every letter of accented author names in the PDF
(「García-Montero」「Altıntaş」「Łukasz」「Dvořák」), in each body face, as the export draws it."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.services import design, fonts, report_export

#: drawn by the Nanum faces or reportlab's Vera
_COMMON = ("García-Montero", "Münüre Ezgi Altıntaş", "Łukasz Wróbel", "Søren Kierkegaard")
#: Latin Extended-A/B and Vietnamese letters (ř, ć, ễ) only the wide face draws
_WIDE = ("Antonín Dvořák", "Đorđe Šćepanović", "Hoàng Nguyễn", "Erdős Pál")
_DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def _pdf_text(names: tuple[str, ...], font: str) -> str:
    sources = [{"ordinal": i, "title": f"Study {i} on retrieval", "author": name, "year": "2025",
                "publisher": "Journal of Tests", "origin": "web"}
               for i, name in enumerate(names, 1)]
    body = "선행연구가 있다 " + "".join(f"[{i}]" for i in range(1, len(names) + 1)) + "."
    sections = report_export.with_references(
        [{"id": "s1", "heading": "선행연구", "content": body}], sources, "APA")
    data = report_export.to_pdf("참고 문헌 시험", sections,
                                tokens={**design.DEFAULT_TOKENS, "font": font})
    return "".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


def _kept(names: tuple[str, ...], font: str) -> None:
    text = _pdf_text(names, font)
    missing = [word for name in names for word in name.split() if word not in text]
    assert not missing, (font, missing)


@pytest.mark.parametrize("font", ["gothic", "serif"])
def test_accented_author_names_in_the_reference_list_keep_every_letter(font):
    if not fonts.embedded(font):
        pytest.skip("Nanum faces not installed")
    _kept(_COMMON, font)


@pytest.mark.parametrize("font", ["gothic", "serif"])
def test_czech_polish_and_vietnamese_letters_are_drawn_by_the_wide_latin_face(font):
    if not fonts.embedded(font) or not _DEJAVU.is_file():
        pytest.skip("Nanum or DejaVu faces not installed")
    _kept(_COMMON + _WIDE, font)


def test_every_accented_letter_has_a_face_in_the_export_chain():
    if not fonts.embedded("gothic") or not _DEJAVU.is_file():
        pytest.skip("Nanum or DejaVu faces not installed")
    letters = {ch for name in _COMMON + _WIDE for ch in name if ord(ch) >= 0x80}
    for bold in (False, True):
        faces = [fonts.korean("gothic", bold=bold), fonts.latin(bold=bold)]
        have = set().union(*(report_export._glyphs(f) or () for f in faces if f))
        assert not sorted(ch for ch in letters if ord(ch) not in have), bold
