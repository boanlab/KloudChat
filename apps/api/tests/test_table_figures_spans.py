"""Figure-column marking skips only tables with real merged cells."""

from app.services import table_figures

_BODY = (
    "<tr><td>강사비</td><td>1,200,000</td></tr>"
    "<tr><td>재료비</td><td>350,000</td></tr>"
)


def _table(cell_attrs: str) -> str:
    head = f"<tr><td{cell_attrs}>항목</td><td>금액</td></tr>"
    return f"<table>{head}{_BODY}</table>"


def test_default_spans_from_the_editor_still_get_figure_columns():
    marked = table_figures.mark_html(_table(' colspan="1" rowspan="1"'))
    assert 'class="fig"' in marked


def test_a_real_merge_is_left_as_is():
    html = _table(' colspan="2"')
    assert table_figures.mark_html(html) == html
