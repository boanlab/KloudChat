"""What the person reads says 양식 for a document template and 디자인 for a look.

「서식」 is left to text formatting (bold, italics); 「디자인 시스템」 is just 「디자인」.
"""

from __future__ import annotations

import asyncio

import pytest

from app.pipeline import labels
from app.services import design_extract, design_templates


def test_stage_labels_say_yangsik() -> None:
    assert labels.label("format-find") == "양식 검사"
    assert labels.label("format-amend") == "양식 고치기"
    assert labels.label("format-step") == "양식 점검 결과"
    assert not any("서식" in text for text in labels.LABELS.values())


def test_the_plain_seed_is_named_yangsik() -> None:
    seed = design_templates.rendering(design_templates.GENERIC_ID)
    assert seed is not None
    assert seed.name == "기본 양식"


@pytest.mark.parametrize("reply", ["not json at all", '{"name": ""}'])
def test_an_unreadable_document_names_a_design(monkeypatch: pytest.MonkeyPatch, reply: str) -> None:
    async def fake_complete(model: str, prompt: str, api_key: str) -> tuple[str, dict[str, int]]:
        return reply, {}

    monkeypatch.setattr(design_extract, "_complete", fake_complete)
    with pytest.raises(design_extract.ExtractError) as caught:
        asyncio.run(design_extract.extract(source="공문 " * 40, model="m", api_key="k"))
    assert str(caught.value) == "문서에서 디자인을 읽어내지 못했습니다."
    assert "디자인 시스템" not in str(caught.value)
