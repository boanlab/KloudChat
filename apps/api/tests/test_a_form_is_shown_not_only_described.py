"""A 서식's form file (headings, columns, directions) reaches the model beside its rules."""

from __future__ import annotations

import pathlib

import pytest

from app.services import design_templates as templates
from app.services import page


def _documents() -> list:
    return [row for row in templates.all_templates() if row.kind in templates.HTML_KINDS]


def test_every_form_yields_the_words_it_is_made_of() -> None:
    for row in _documents():
        text = templates.form_text(row)
        assert text, f"{row.id}: 양식에서 글을 뽑지 못했습니다"
        # Headings and column names only; a long form is one carrying sample prose.
        assert len(text) < 2000, f"{row.id}: 양식에서 뽑은 글이 {len(text)}자입니다"


def test_the_form_reaches_the_model_beside_the_rules() -> None:
    row = templates.get("doc-minutes")
    guide = page._guide(row)

    assert row.instructions in guide, "지시가 그대로 남아 있어야 합니다"
    for heading in ("참석", "논의", "결정 사항", "남은 쟁점", "실행 항목"):
        assert heading in guide, f"양식의 '{heading}' 이 모델에 가지 않습니다"
    for column in ("근거", "담당", "기한"):
        assert column in guide


def test_the_form_says_which_of_its_lines_are_directions() -> None:
    """The form's direction lines are labelled as directions, not body text."""
    guide = page._guide(templates.get("doc-minutes"))
    assert "빈 양식" in guide
    assert "안내이지" in guide and "실제 내용" in guide


def test_a_template_without_a_form_is_unchanged() -> None:
    """A 서식 without a form yields its rules and markup vocabulary only."""

    import dataclasses

    row = templates.get("doc-report")
    bare = dataclasses.replace(row, form_file="")
    guide = page._guide(bare)
    assert guide == f"{row.instructions}\n\n{row.markup}"
    assert "빈 양식" not in guide
    assert templates.form_text(bare) == ""


def test_an_unreadable_form_does_not_take_the_rules_down_with_it(monkeypatch) -> None:
    """A form that fails to read leaves the instructions intact."""

    row = templates.get("doc-minutes")

    def explode(*_args, **_kwargs):
        raise RuntimeError("읽을 수 없는 형식입니다.")

    from app.services import files as file_service

    monkeypatch.setattr(file_service, "extract_text", explode)
    templates._FORM_TEXT.clear()
    try:
        guide = page._guide(row)
        assert guide == f"{row.instructions}\n\n{row.markup}"
        assert "빈 양식" not in guide
    finally:
        templates._FORM_TEXT.clear()


def test_the_form_is_read_once(monkeypatch) -> None:
    """Form text is extracted once and cached."""

    row = templates.get("doc-minutes")
    templates._FORM_TEXT.clear()
    reads = 0

    from app.services import files as file_service

    real = file_service.extract_text

    def counted(name: str, mime: str, data: bytes) -> str:
        nonlocal reads
        reads += 1
        return real(name, mime, data)

    monkeypatch.setattr(file_service, "extract_text", counted)
    try:
        first = templates.form_text(row)
        assert templates.form_text(row) == first
        assert reads == 1
    finally:
        templates._FORM_TEXT.clear()


@pytest.mark.parametrize("template_id", [row.id for row in _documents()])
def test_the_form_on_disk_is_the_one_the_catalogue_points_at(template_id: str) -> None:
    row = templates.get(template_id)
    assert pathlib.Path(row.form_file).is_file()
