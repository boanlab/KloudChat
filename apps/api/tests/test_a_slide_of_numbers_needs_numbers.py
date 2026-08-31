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


def test_attached_material_with_figures_in_it_is_enough() -> None:
    """A file, a search result, a note — read for what is in it.

    `bool(context)` was the test: material existed, therefore figures did. Two
    saved memories about who the user is were enough by that rule, and a live
    run drew six years of invented AI 채용 증가율 on a chart. Having material
    and having numbers in it are different questions.
    """
    grounded = deck._grounded_layouts(
        _PLAN, "클라우드 이관 발표 자료", ["작년 집계표: 이관 비용 1,240만 원"]
    )
    assert _layouts(grounded) == _layouts(_PLAN)


def test_material_with_no_figures_in_it_is_not_enough() -> None:
    """A memory saying what somebody's job is grounds nothing numeric."""
    grounded = deck._grounded_layouts(
        _PLAN, "클라우드 이관 발표 자료", ["사용자는 대학 교직원이다"]
    )
    assert "chart" not in _layouts(grounded)
    assert "metrics" not in _layouts(grounded)


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
        # The three paired shapes need no figures — a name beside a sentence, a
        # letter over a caption, a date beside what happened.
        "bands",
        "tiles",
        "timeline",
    ]
    # `_BODY_LAYOUTS`, not `_LAYOUTS[1:]`: the slice meant "everything but the
    # cover" only while the cover was the one layout with no content in it.
    assert deck._offered_layouts("작년 32% 줄었다", []) == list(deck._BODY_LAYOUTS)
    # Material with no figures in it is still material with no figures in it.
    # `bool(context)` was the test, and two saved memories about who the user is
    # were enough to let a deck draw six years of invented 채용 증가율.
    assert deck._offered_layouts("숫자 없는 주제", ["붙인 자료"]) != list(deck._BODY_LAYOUTS)
    assert deck._offered_layouts("숫자 없는 주제", ["수료생 1500명"]) == list(deck._BODY_LAYOUTS)


def test_a_report_refuses_the_same_figures_a_deck_does() -> None:
    """지어낸 수치는 문서에서도 지어낸 수치다.

    A 검토 보고서 on a topic with no material came back with three ```kpi
    blocks — 6개월, 80%, 90%, 4명, 30%, 100%, 0일, 8주, 40명, 80점 — every one
    invented, every one set large where a figure is read as the most factual
    thing on the page. The deck had refused exactly this for a while and the
    report had not, so the same request produced an honest deck and a confident
    document.

    The prose survives. \"짧은 주기로 운영한다\" is a claim somebody can weigh;
    `8주` beside a heading is a measurement, and there was no measuring.
    """
    from app.services.report import _grounded_figures

    body = "앞 문장.\n\n```kpi\n8주 | 한 주기\n40명 | 모집 인원\n```\n\n뒤 문장."
    assert "8주" not in _grounded_figures(body, grounded=False)
    assert "앞 문장." in _grounded_figures(body, grounded=False)
    assert "뒤 문장." in _grounded_figures(body, grounded=False)
    # With material in hand it is left exactly as written.
    assert _grounded_figures(body, grounded=True) == body
