"""Uploads whose parser hands back UTF-16 surrogates or NULs are stored, not refused with a 500."""

from __future__ import annotations

from app.services import files as file_service


def test_a_surrogate_pair_becomes_its_character() -> None:
    # 「𝐀」 (U+1D400) as the parser's raw UTF-16 halves
    raw = "교원인사규정 3-3-1～𝐀 제1조"
    clean = file_service.storable(raw)
    assert clean == "교원인사규정 3-3-1～\U0001d400 제1조"
    clean.encode("utf-8")


def test_lone_surrogates_and_nuls_are_dropped() -> None:
    raw = "학   칙 2-1-1～\ud800\n제정\x00 1990"
    clean = file_service.storable(raw)
    assert clean == "학   칙 2-1-1～\n제정 1990"
    clean.encode("utf-8")


def test_clean_text_is_left_alone() -> None:
    text = "조교임용 규정 3-3-4～\n제1조(목적)"
    assert file_service.storable(text) is text or file_service.storable(text) == text


def test_extract_text_returns_storable_text() -> None:
    data = "위원회 설치규정\x00 5-5-1".encode()
    assert file_service.extract_text("a.txt", "text/plain", data) == "위원회 설치규정 5-5-1"
