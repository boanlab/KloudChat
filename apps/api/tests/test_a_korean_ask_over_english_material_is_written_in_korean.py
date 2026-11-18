"""A Korean ask with an English paper pasted under it is a Korean request: the document is not
written in English because the material is (the deck came out English on the 2026-11-17
benchmark)."""

from __future__ import annotations

from app.services.context import language_rule

PAPER = ("[1] arXiv:2309.06180\n Efficient Memory Management for Large Language Model Serving "
         "with PagedAttention " + "High throughput serving of large language models requires "
         "batching sufficiently many requests at a time. " * 8)


def test_the_ask_decides_not_the_pasted_english_paper() -> None:
    ask = "아래 논문의 앞부분만 근거로 랩 세미나 발표 슬라이드 8장을 만들어 줘."
    assert language_rule(f"{ask}\n\n{PAPER}") == ""


def test_an_english_request_is_still_written_in_english() -> None:
    ask = "Make an eight slide lab seminar deck from the paper below, in plain English."
    assert "English" in language_rule(f"{ask}\n\n{PAPER}")
    assert "English" in language_rule("Write a short report on container isolation. " * 3)
