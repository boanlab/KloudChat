"""A Korean ask over an English paper gets an explicit Korean-output rule, and the share of
hard sections stays a setting."""

from __future__ import annotations

from app.core.config import settings
from app.services import doc_stages
from app.services.context import korean_over_foreign_material

PAPER = "High throughput serving of large language models requires batching requests. " * 12


def test_the_rule_is_for_korean_asks_over_english_material() -> None:
    ask = "아래 논문의 앞부분만 근거로 발표 슬라이드 8장을 만들어 줘."
    assert "Korean" in korean_over_foreign_material(ask, [PAPER])
    assert "Korean" in korean_over_foreign_material(f"{ask}\n\n{PAPER}", None)
    assert korean_over_foreign_material(ask, ["한국어 자료입니다. " * 50]) == ""
    assert korean_over_foreign_material("Make eight slides from the paper.", [PAPER]) == ""


def test_the_hard_share_is_a_setting(monkeypatch) -> None:
    heads = ["요약", "배경", "현황", "방안", "일정", "결론"]
    material = "자료 없음"
    assert set(doc_stages.hard_report_sections(heads, material)) == {0, 5}
    monkeypatch.setattr(settings, "document_hard_share", 0.0)
    assert doc_stages.hard_report_sections(heads, material) == {}
