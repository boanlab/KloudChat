"""서식이 제 양식을 모델에게 보여 준다.

A 서식 told the model its rules and showed it nothing. The rules are prose —
"결정마다 왜 그렇게 정했는지 한 줄이라도 남긴다" — and the form beside them is
the same thing as a shape: the headings in order, the columns of each table,
the line under each heading naming what goes there.

The file already ships. Describing it a second time in prose would be a second
copy to keep in step with the first.
"""

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
        # Headings and column names, not a document. A form that came back
        # thousands of characters long would be one carrying sample prose,
        # and that prose would end up in every document written from it.
        assert len(text) < 2000, f"{row.id}: 양식에서 뽑은 글이 {len(text)}자입니다"


def test_the_form_reaches_the_model_beside_the_rules() -> None:
    row = templates.get("doc-minutes")
    guide = page._guide(row)

    assert row.instructions in guide, "지시가 그대로 남아 있어야 합니다"
    # Its own headings, in the order the form puts them.
    for heading in ("참석", "논의", "결정 사항", "남은 쟁점", "실행 항목"):
        assert heading in guide, f"양식의 '{heading}' 이 모델에 가지 않습니다"
    # And the columns, which are the part prose describes worst.
    for column in ("근거", "담당", "기한"):
        assert column in guide


def test_the_form_says_which_of_its_lines_are_directions() -> None:
    """안내문은 지시이지 본문이 아니다.

    The lines under each heading are addressed to a person filling the form in
    by hand. A model handed them without warning writes them into the document
    as though they were the text — so the form arrives labelled.
    """
    guide = page._guide(templates.get("doc-minutes"))
    assert "빈 양식" in guide
    assert "안내이지" in guide and "실제 내용" in guide


def test_a_template_without_a_form_is_unchanged() -> None:
    """A 서식 that ships no form is the rules alone, exactly as before."""

    import dataclasses

    row = templates.get("doc-report")
    bare = dataclasses.replace(row, form_file="")
    assert page._guide(bare) == row.instructions
    assert templates.form_text(bare) == ""


def test_an_unreadable_form_does_not_take_the_rules_down_with_it(monkeypatch) -> None:
    """The instructions still stand, and they are the half that says why."""

    row = templates.get("doc-minutes")

    def explode(*_args, **_kwargs):
        raise RuntimeError("읽을 수 없는 형식입니다.")

    from app.services import files as file_service

    monkeypatch.setattr(file_service, "extract_text", explode)
    templates._FORM_TEXT.clear()
    try:
        assert page._guide(row) == row.instructions
    finally:
        templates._FORM_TEXT.clear()


def test_the_form_is_read_once(monkeypatch) -> None:
    """A turn does not re-open a 38KB zip to read 250 characters."""

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
