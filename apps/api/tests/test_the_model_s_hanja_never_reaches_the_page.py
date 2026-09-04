"""`hangul` substitutes stray ideographs at the door, sparing glosses, code and JSON keys."""

from __future__ import annotations

import pytest

from app.services import lint
from app.services.hangul import read_back, tidy_spacing


@pytest.mark.parametrize(
    ("wrote", "reads"),
    [
        # All real samples from generated reports.
        ("全自動化 시스템을 도입한다", "전자동화 시스템을 도입한다"),
        ("傳統的인 방화벽으로는 막지 못한다", "전통적인 방화벽으로는 막지 못한다"),
        ("動的 엔드포인트를 사용한다", "동적 엔드포인트를 사용한다"),
    ],
)
def test_a_leaked_word_is_read_in_hangul(wrote: str, reads: str) -> None:
    assert read_back(wrote)[0] == reads


def test_the_words_that_were_changed_come_back_with_it() -> None:
    """Substituted words are reported, not swallowed."""
    _, replaced = read_back("試點 프로젝트를 연다")
    assert replaced == ["試點"]


def test_a_gloss_is_left_alone() -> None:
    """`분산(分散)` and similar glosses are left alone."""
    assert read_back("분산(分散) 처리를 도입한다") == ("분산(分散) 처리를 도입한다", [])


@pytest.mark.parametrize(
    "sample",
    [
        "코드는 `print(中)` 처럼 쓴다",
        "<code>中文</code> 는 그대로 둔다",
        "```python\nprint('中')\n```",
    ],
)
def test_code_is_not_prose(sample: str) -> None:
    assert read_back(sample) == (sample, [])


def test_ordinary_korean_is_returned_untouched() -> None:
    """Ordinary Korean is returned untouched."""
    text = "전교생이 두 과목을 듣는다."
    assert read_back(text) == (text, [])


def test_what_was_read_back_no_longer_trips_the_check() -> None:
    """`hangul` and `lint` agree on what a leak is."""
    clean, replaced = read_back("傳統的인 방화벽으로는 막지 못한다. 分散 처리를 쓴다.")
    assert replaced
    assert not lint._stray_hanja(clean)


def test_json_brackets_are_not_a_gloss() -> None:
    """JSON values are read after parsing, so array brackets are not a gloss."""
    from app.services import deck

    parsed = deck._json_object('{"title": "AI", "slides": [{"title": "대학生的 격차"}]}')
    assert parsed["slides"][0]["title"] == "대학생적 격차"


def test_a_gloss_inside_json_is_still_a_gloss() -> None:
    """A gloss inside a JSON value is still exempt."""
    from app.services import deck

    parsed = deck._json_object('{"body": "분산(分散) 처리를 도입한다"}')
    assert parsed["body"] == "분산(分散) 처리를 도입한다"


def test_the_keys_are_left_alone() -> None:
    """JSON keys are left alone."""
    from app.services import deck

    assert set(deck._json_object('{"bullets": ["가"], "notes": "나"}')) == {"bullets", "notes"}


@pytest.mark.parametrize(
    ("wrote", "reads"),
    [
        ("저녁 8 시 이후 34 석(28%)", "저녁 8시 이후 34석(28%)"),
        ("경비원 1 인 상주, 200 석 규모", "경비원 1인 상주, 200석 규모"),
        ("연간 120 만 원, 3 년 총비용 3,600 만 원", "연간 120만 원, 3년 총비용 3,600만 원"),
        ("약 12 % 향상, 2 학기, 3 주차", "약 12% 향상, 2학기, 3주차"),
        # SI 단위는 띄운 채로.
        ("R = 1.0 kΩ, 103 nF, 2.00 Vpp", "R = 1.0 kΩ, 103 nF, 2.00 Vpp"),
        ("3 일반인이 참석", "3 일반인이 참석"),
    ],
)
def test_a_counter_is_written_against_its_number(wrote: str, reads: str) -> None:
    assert tidy_spacing(wrote) == reads
