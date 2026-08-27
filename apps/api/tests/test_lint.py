"""What the linter catches, and — more carefully — what it leaves alone.

A check that fires on ordinary writing is worse than no check: people learn to
ignore the badge, and then it protects nothing. So every rule here has a
companion test for the sentence it must stay silent on.
"""

from __future__ import annotations

import pytest

from app.services import design_templates as dt
from app.services import lint


def _one(title: str, *lines: str) -> list[lint.Part]:
    return [lint.Part(title, list(lines))]


def _rules(findings: list[lint.Finding]) -> list[str]:
    return [f.rule for f in findings]


# ── what it catches ────────────────────────────────────────────────────


def test_markdown_that_never_rendered_is_a_regression():
    """`**bold**` in an HTML block prints its own asterisks on the slide.

    The document surfaces ask for markup and a small model sometimes answers in
    Markdown. `sanitise` cannot help — there is no tag to drop, only characters
    — so the asterisks travel all the way to the screen behind a speaker. This
    is the one place left that can see them.
    """
    findings = lint.check(_one("측정 환경", "**발표 노트** 환경 통제에서 노이즈를 제거한다."))
    assert _rules(findings) == ["markup"]
    assert findings[0].severity == "P0"


def test_the_envelope_the_model_was_shown_is_not_content():
    """What survives when the answer was JSON and the JSON was truncated.

    `sanitise` unwraps one that parses; one that does not arrives whole, and a
    slide then opens with `{"layout":"bullets","body":"…`. This is what says so.
    """
    findings = lint.check(
        _one("승인 규칙", '{"layout": "bullets", "body": "  승인 규칙의 구조 확인')
    )
    assert "envelope" in _rules(findings)
    assert findings[0].severity == "P0"


def test_a_line_that_merely_contains_a_brace_is_not_an_envelope():
    assert "envelope" not in _rules(
        _rules_for('설정은 {"retries": 3} 처럼 쓴다.')
    )


def _rules_for(line: str):
    return lint.check(_one("표기", line))


@pytest.mark.parametrize(
    "line",
    [
        "곱셈 기호는 2*3 처럼 쓴다.",
        "각주 표시(*)는 문단 끝에 둔다.",
        "정규식 `a**b` 를 설명하는 줄이다.",
    ],
)
def test_ordinary_asterisks_are_not_markdown(line):
    """Paired, with words between them, and not inside code — anything else is
    somebody's sentence."""
    assert "markup" not in _rules(lint.check(_one("표기", line)))



def test_a_placeholder_nobody_replaced_is_a_regression():
    findings = lint.check(_one("배경", "여기에 내용을 입력하세요.", "두 번째 줄입니다."))
    assert _rules(findings) == ["placeholder"]
    assert findings[0].severity == "P0"
    assert findings[0].where == "배경"


@pytest.mark.parametrize(
    "line",
    [
        "도입하면 10배 빠르다.",
        "가용성 99.9% 를 보장한다.",
        "24/7 대응을 제공한다.",
        "업계 최초로 도입한 방식이다.",
    ],
)
def test_a_figure_nobody_could_have_sourced_is_flagged(line):
    findings = lint.check(_one("효과", line, "다른 문장도 함께 있다."))
    assert "invented-metric" in _rules(findings)


def test_filler_is_an_advisory_rather_than_a_regression():
    findings = lint.check(_one("요약", "혁신적인 접근으로 문제를 해결한다."))
    assert _rules(findings) == ["filler"]
    assert findings[0].severity == "P1"


def test_a_block_that_never_got_written_is_reported_once():
    findings = lint.check(_one("결론", "…"))
    assert _rules(findings) == ["empty"]
    # And nothing else: an empty block cannot also be crowded or repetitive.
    assert len(findings) == 1


def test_an_emoji_leading_a_line_is_the_icon_tell():
    assert "emoji" in _rules(lint.check(_one("현황", "🚀 빠르게 성장하고 있습니다.")))
    assert "emoji" in _rules(lint.check(_one("✨ 요약", "본문이 여기에 있습니다.")))


def test_the_same_line_twice_across_two_parts_is_a_repeat():
    parts = [
        lint.Part("배경", ["점검 이력이 남아 있지 않다."]),
        lint.Part("문제", ["점검 이력이 남아 있지 않다."]),
    ]
    findings = lint.check(parts)
    assert _rules(findings) == ["repeat"]
    assert findings[0].where == "문제"


def test_slide_shape_rules_apply_only_to_slides():
    crowded = _one("현황", *[f"항목 번호 {n} 입니다" for n in range(9)])
    assert "crowded" in _rules(lint.check(crowded, slides=True))
    assert "crowded" not in _rules(lint.check(crowded))


def test_a_line_too_long_for_a_screen_is_flagged():
    # 45 is where the check sits; the deck prompt itself asks for 40.
    long_line = (
        "이 문장은 화면 한 줄에 담기에는 확실히 너무 길어서 "
        "뒤로 갈수록 두 행이 되고 마는 종류의 문장이다."
    )
    assert "long-line" in _rules(lint.check(_one("설명", long_line), slides=True))


def test_findings_come_worst_first():
    parts = _one("요약", "혁신적인 방식이다.", "여기에 내용을 입력하세요.")
    assert [f.severity for f in lint.check(parts)] == ["P0", "P1"]


# ── what it leaves alone ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "전년 대비 12% 증가했다.",
        "표본은 340명이며 응답률은 68% 였다.",
        "장비 42대 가운데 5대가 점검을 넘겼다.",
        "처리 시간이 3.2초에서 1.8초로 줄었다.",
        "2026년 2분기에 두 배로 늘었다고 보고서는 적고 있다.",
    ],
)
def test_ordinary_figures_are_not_touched(line):
    """A report is *for* numbers. A check that fires on them is noise."""
    assert lint.check(_one("결과", line, "이어지는 문장이 하나 더 있다.")) == []


def test_a_short_but_finished_line_is_not_empty():
    assert lint.check(_one("결론", "도입을 권고한다. 예산은 2분기에 상신한다.")) == []


def test_a_repeated_short_phrase_is_not_a_repeat():
    """Headings and stock phrases recur; only substantial lines count."""
    parts = [
        lint.Part("가", ["없음", "이 절은 배경을 설명한다."]),
        lint.Part("나", ["없음", "이 절은 대안을 견준다."]),
    ]
    assert lint.check(parts) == []


def test_the_same_line_twice_inside_one_part_is_not_a_repeat():
    """A list that echoes itself is one part's problem, and a rewrite fixes it."""
    parts = [lint.Part("배경", ["점검 이력이 남아 있지 않다.", "점검 이력이 남아 있지 않다."])]
    assert "repeat" not in _rules(lint.check(parts))


# ── reading each surface ───────────────────────────────────────────────


def test_a_markdown_report_becomes_parts_without_its_markers():
    parts = lint.from_sections(
        [{"heading": "배경", "content": "## 소제목\n- 첫째 항목\n\n1. 둘째 항목"}]
    )
    assert parts[0].title == "배경"
    assert parts[0].lines == ["소제목", "첫째 항목", "둘째 항목"]


def test_a_deck_slide_becomes_its_bullets_and_body():
    parts = lint.from_slides([{"title": "표지", "body": "부제", "bullets": ["가", "나"]}])
    assert parts[0].lines == ["가", "나", "부제"]


def test_an_html_block_is_read_as_lines_not_as_markup():
    parts = lint.from_blocks(
        [{"title": "현황", "html": "<ul><li>보유 42대</li><li>점검 5대</li></ul><p>본문</p>"}]
    )
    assert parts[0].lines == ["보유 42대", "점검 5대", "본문"]
    # The tags themselves must not become words the rules then read.
    assert not any("<" in line for line in parts[0].lines)


def test_a_column_label_is_not_one_of_the_items_under_it():
    """A `split` slide opens each column with an `<h3>` the seed sets small.

    Counted as lines, two signposts over four items made a slide of six and
    tripped `crowded` on a slide the template itself calls the right size.
    """
    block = {
        "title": "비교",
        "html": (
            '<div class="cols">'
            "<div><h3>유지</h3><ul><li>비용이 들지 않는다</li>"
            "<li>복구 경로가 없다</li></ul></div>"
            "<div><h3>교체</h3><ul><li>예산이 필요하다</li>"
            "<li>일정은 세 달이다</li></ul></div>"
            "</div>"
        ),
    }
    part = lint.from_blocks([block])[0]

    assert part.labels == ["유지", "교체"]
    assert len(part.lines) == 4
    assert "crowded" not in _rules(
        lint.check([part], slides=True, limits={"max_bullets": 5})
    )


def test_a_label_is_still_read_for_the_words_in_it():
    """Not counted is not unread — a placeholder in a column heading is one."""
    part = lint.from_blocks(
        [{"title": "비교", "html": "<h3>여기에 제목을 입력</h3><p>본문이 여기 있다.</p>"}]
    )[0]

    assert part.lines == ["본문이 여기 있다."]
    assert "placeholder" in _rules(lint.check([part]))


def test_a_label_does_not_run_into_the_line_after_it():
    part = lint.from_blocks([{"title": "ㄱ", "html": "<h3>이름표</h3><p>본문</p>"}])[0]
    assert part.lines == ["본문"]


def test_the_wire_shape_is_flat_strings():
    findings = lint.check(_one("배경", "여기에 내용을 입력하세요.", "한 줄 더."))
    assert lint.wire(findings) == [
        {
            "severity": "P0",
            "rule": "placeholder",
            "message": "채우지 않은 자리가 남았습니다 — “여기에 내용을 입력”.",
            "where": "배경",
        }
    ]


# ── a template's own promise ───────────────────────────────────────────


def test_a_template_can_tighten_the_bounds_the_checker_uses():
    """The lecture deck asks for 25 characters; the general bound is 45.

    Without the template's own numbers the checker passes a line the template
    itself calls too long — two rules about the same slide, disagreeing.
    """
    line = "가" * 30
    parts = [lint.Part("한 장", [line, "짧은 줄"])]

    assert not [f for f in lint.check(parts, slides=True) if f.rule == "long-line"]
    tightened = lint.check(parts, slides=True, limits={"max_bullet_chars": 25})
    assert [f for f in tightened if f.rule == "long-line"]


def test_the_shipped_limits_are_the_ones_the_instructions_state():
    """`deck-lecture` says 3~4개, 25자 in prose; the manifest has to agree."""
    template = dt.get("deck-lecture")
    assert template is not None
    assert template.limits == {"max_bullets": 4, "max_bullet_chars": 25}
    assert "25자" in template.instructions
    # A template that says nothing keeps the general bounds.
    assert dt.get("doc-report").limits == {}
