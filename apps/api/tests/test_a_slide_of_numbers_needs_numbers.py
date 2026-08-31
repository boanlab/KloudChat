"""숫자만 있는 장은 숫자가 어디선가 와야 한다.

A `chart` or `metrics` slide is nothing but figures. Asked for one with no
material attached, no search results and no numbers in the request itself, the
model has exactly one place to get them and it takes them: a live run asked for
a quarterly trend and came back with eight quarters of tidy invented data.

Both prompts say not to. One of the two obeyed, which is why this is not a
prompt: a rule the writer keeps most of the time is not a rule for something
that reads as fact the moment it is projected.
"""

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


def test_attached_material_is_enough() -> None:
    """Anything in the context window is a place a figure could have come from
    — a file, a search result, a note."""
    grounded = deck._grounded_layouts(_PLAN, "클라우드 이관 발표 자료", ["작년 집계표: …"])
    assert _layouts(grounded) == _layouts(_PLAN)


def test_the_request_counts_as_material() -> None:
    """Somebody who writes the figure has given it.

    Refusing to chart a number the reader typed would be refusing the thing
    they asked for.
    """
    grounded = deck._grounded_layouts(_PLAN, "저장 비용이 32% 줄었다. 발표 자료로 만들어줘", [])
    assert _layouts(grounded) == _layouts(_PLAN)


def test_nothing_else_is_touched() -> None:
    # Prose can be written from a topic. Only the two layouts that are nothing
    # but figures are refused.
    plan = [{"title": "가", "layout": "table"}, {"title": "나", "layout": "quote"}]
    assert _layouts(deck._grounded_layouts(plan, "주제만 있는 요청", [])) == ["table", "quote"]


def test_every_way_into_the_writer_is_guarded() -> None:
    """Three paths reach the slide writer and each has to be checked.

    A plan approved days after it was proposed, a plan edited before it was,
    and a plan the outline call just produced. Counted rather than named
    because a fourth path is exactly the kind of thing that gets added without
    the check — but not pinned to a number, which is what this asserted first
    and what broke the moment a fourth call site was right.
    """
    import inspect

    source = inspect.getsource(deck.write)
    assert source.count("_grounded_layouts(") >= 2
    # And the variety check is judged against the same narrowed list, or the
    # plan is asked to use layouts it will then have stripped out of it.
    assert "flat_layouts(plan, offered)" in source
    assert "_offered_layouts(" in source


def test_variety_is_judged_against_what_the_request_can_reach() -> None:
    """Asking for a layout that is about to be stripped costs a model call.

    A deck about a topic with no figures anywhere near it was told its plan was
    flat and named `metrics` and `chart` as the missing ones — so it paid for a
    second outline call, produced them, and had them turned straight back into
    bullets. It came back as flat as it started, one call poorer.
    """
    assert deck._offered_layouts("숫자 없는 주제", []) == [
        "bullets",
        "quote",
        "two-column",
        "table",
    ]
    assert deck._offered_layouts("작년 32% 줄었다", []) == list(deck._LAYOUTS[1:])
    assert deck._offered_layouts("숫자 없는 주제", ["붙인 자료"]) == list(deck._LAYOUTS[1:])
