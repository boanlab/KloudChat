"""A 서식 carries its own behaviour as data: the document format it fixes (`doc_format` in
`template.toml`) and that format's genre rule (`genre.md`), read the way a person's template
will be — no map in code says which 서식 is which format."""

from __future__ import annotations

from pathlib import Path

from app.services import design_templates, doc_formats, report

ROOT = Path(design_templates.__file__).resolve().parent.parent / "design_templates"


def test_the_format_map_is_read_from_each_template_toml():
    bound = {t.id: t.doc_format for t in design_templates.all_templates() if t.doc_format}
    assert doc_formats.TEMPLATE_FORMATS == bound
    assert bound == {
        "doc-minutes": "minutes", "doc-gaejo-report": "gaejo",
        "doc-internal-draft": "internal_draft", "doc-event-plan": "event_plan",
        "doc-result-report": "result_report", "doc-client-proposal": "client_proposal",
        "doc-comparison": "comparison", "doc-career-history": "career_history",
        "doc-agenda": "agenda",
    }


def test_each_bound_format_s_genre_lives_in_its_template():
    for template in design_templates.all_templates():
        genre = (ROOT / template.id / "genre.md")
        if template.doc_format and template.doc_format != "minutes":
            assert genre.is_file(), template.id
            assert report._FORMAT_GENRES[template.doc_format] == template.genre
            assert template.genre.startswith("Genre: ")
        elif not template.doc_format:
            assert not genre.is_file(), template.id
    # The 공문서 genres keep the marker `_shaped` reads.
    for fmt in ("gaejo", "internal_draft", "event_plan", "result_report"):
        assert report._GAEJO_STYLE in report._FORMAT_GENRES[fmt]
    fmt = doc_formats.BY_ID["comparison"]
    assert report._genre_rule("아무 요청", fmt) == design_templates.get("doc-comparison").genre
