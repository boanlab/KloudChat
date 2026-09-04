"""Figure-only layouts are offered only when the request or its material carries numbers."""

from __future__ import annotations

from app.services import deck

_PLAN = [
    {"title": "표지", "layout": "title"},
    {"title": "성과", "layout": "metrics"},
    {"title": "추이", "layout": "chart"},
    {"title": "권고", "layout": "bullets"},
]


def _layouts(plan: list[dict[str, str]]) -> list[str]:
    return [item["layout"] for item in plan]


def test_with_nothing_to_draw_numbers_from_they_become_prose() -> None:
    grounded = deck._grounded_layouts(_PLAN, "클라우드 이관 발표 자료 만들어줘", [])
    assert _layouts(grounded) == ["title", "bullets", "bullets", "bullets"]


def test_attached_material_with_figures_in_it_is_enough() -> None:
    """Material containing figures grounds the figure-only layouts."""
    grounded = deck._grounded_layouts(
        _PLAN, "클라우드 이관 발표 자료", ["작년 집계표: 이관 비용 1,240만 원"]
    )
    assert _layouts(grounded) == _layouts(_PLAN)


def test_material_with_no_figures_in_it_is_not_enough() -> None:
    """Material without figures does not ground chart or metrics slides."""
    grounded = deck._grounded_layouts(
        _PLAN, "클라우드 이관 발표 자료", ["사용자는 대학 교직원이다"]
    )
    assert "chart" not in _layouts(grounded)
    assert "metrics" not in _layouts(grounded)


def test_the_request_counts_as_material() -> None:
    """A figure in the request itself counts as material."""
    grounded = deck._grounded_layouts(_PLAN, "저장 비용이 32% 줄었다. 발표 자료로 만들어줘", [])
    assert _layouts(grounded) == _layouts(_PLAN)


def test_nothing_else_is_touched() -> None:
    # Only the figure-only layouts are refused.
    plan = [{"title": "가", "layout": "table"}, {"title": "나", "layout": "quote"}]
    assert _layouts(deck._grounded_layouts(plan, "주제만 있는 요청", [])) == ["table", "quote"]


def test_every_way_into_the_writer_is_guarded() -> None:
    """Every path into the slide writer narrows layouts; variety is judged on that list."""
    import inspect

    source = inspect.getsource(deck.write)
    assert source.count("_grounded_layouts(") >= 2
    assert "flat_layouts(plan, offered)" in source
    assert "_offered_layouts(" in source


def test_variety_is_judged_against_what_the_request_can_reach() -> None:
    """Offered layouts exclude the figure-only ones unless request or material has numbers."""
    assert deck._offered_layouts("숫자 없는 주제", []) == [
        "bullets",
        "quote",
        "statement",
        "two-column",
        "table",
        # Paired shapes need no figures.
        "bands",
        "tiles",
        "timeline",
        "steps",
        "cards",
    ]
    assert deck._offered_layouts("작년 32% 줄었다", []) == list(deck._BODY_LAYOUTS)
    # Material must contain figures, not merely exist.
    assert deck._offered_layouts("숫자 없는 주제", ["붙인 자료"]) != list(deck._BODY_LAYOUTS)
    assert deck._offered_layouts("숫자 없는 주제", ["수료생 1500명"]) == list(deck._BODY_LAYOUTS)


def test_a_report_refuses_the_same_figures_a_deck_does() -> None:
    """An ungrounded report loses its kpi blocks but keeps its prose."""
    from app.services.report import _grounded_figures

    body = "앞 문장.\n\n```kpi\n8주 | 한 주기\n40명 | 모집 인원\n```\n\n뒤 문장."
    assert "8주" not in _grounded_figures(body, grounded=False)
    assert "앞 문장." in _grounded_figures(body, grounded=False)
    assert "뒤 문장." in _grounded_figures(body, grounded=False)
    assert _grounded_figures(body, grounded=True) == body
