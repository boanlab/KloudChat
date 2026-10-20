"""An exported PDF keeps accented Latin letters the Korean faces lack (「García-Montero」,
「Altıntaş」); a Chinese word glued to Korean reads as a word of its own."""

from __future__ import annotations

import io

import pytest
from pypdf import PdfReader

from app.services import fonts, hangul, report_export


def test_accented_names_in_a_reference_list_keep_every_letter():
    if not fonts.embedded("serif"):
        pytest.skip("Nanum faces not installed")
    names = "PS García-Montero, Münüre Ezgi Altıntaş, Ğ. Öztürk"
    data = report_export.to_pdf("참고", [{"id": "s1", "heading": "선행연구",
                                          "content": f"{names}의 연구가 있다 [1]."}])
    text = "".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    for name in ("García-Montero", "Münüre", "Altıntaş", "Öztürk"):
        assert name in text


def test_a_letter_no_face_draws_becomes_its_base_letter():
    assert report_export._base_letter("ş") == "s"
    assert report_export._base_letter("ő") == "o"
    assert report_export._base_letter("가") == ""


def test_a_chinese_word_glued_to_korean_is_read_apart():
    assert hangul.read_back("BM25 검색에는 한국어形态分析器를 적용한다")[0] == \
        "BM25 검색에는 한국어 형태분석기를 적용한다"
    # a short run ends the word it is glued to; a spaced word and a gloss stay as they were
    assert hangul.read_back("대학生的 격차")[0] == "대학생적 격차"
    assert hangul.read_back("한국어 形態 분석")[0] == "한국어 형태 분석"
    assert hangul.read_back("접미어(接尾語)")[0] == "접미어(接尾語)"
