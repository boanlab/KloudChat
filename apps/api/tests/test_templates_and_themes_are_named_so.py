"""What the person reads says 템플릿 for a document's structure and 테마 for its look.

「서식」 is left to text formatting (bold, italics); 「양식」 and 「디자인」 are not shown.
"""

from __future__ import annotations

import asyncio

import pytest

from app.pipeline import labels
from app.services import design_extract, design_templates, report_checks


def test_stage_labels_say_template() -> None:
    assert labels.label("format-find") == "템플릿 검사"
    assert labels.label("format-amend") == "템플릿대로 고치기"
    assert labels.label("format-step") == "템플릿 점검 결과"
    old_words = ("서식", "양식", "디자인")
    assert not any(word in text for text in labels.LABELS.values() for word in old_words)


def test_the_plain_seed_is_named_template() -> None:
    seed = design_templates.rendering(design_templates.GENERIC_ID)
    assert seed is not None
    assert seed.name == "기본 템플릿"


def test_structure_findings_name_the_template() -> None:
    for code in ("section_missing", "section_order", "table_missing"):
        assert "템플릿" in report_checks.MODEL_LABELS[code]
        assert "서식" not in report_checks.MODEL_LABELS[code]


@pytest.mark.parametrize("reply", ["not json at all", '{"name": ""}'])
def test_an_unreadable_document_names_a_theme(monkeypatch: pytest.MonkeyPatch, reply: str) -> None:
    async def fake_complete(model: str, prompt: str, api_key: str) -> tuple[str, dict[str, int]]:
        return reply, {}

    monkeypatch.setattr(design_extract, "_complete", fake_complete)
    with pytest.raises(design_extract.ExtractError) as caught:
        asyncio.run(design_extract.extract(source="공문 " * 40, model="m", api_key="k"))
    assert str(caught.value) == "문서에서 테마를 읽어내지 못했습니다."
