"""모델이 흘린 한자가 화면에 닿지 않는다.

`lint` has caught these for a while, and catching is not fixing. The badge said
P0, the person pressed 모두 고치기, and that spends a model call per section to
rewrite prose that was fine apart from one word. Nobody finishing a 계획서 at
midnight wants a homework assignment about ideographs.

So the reading is substituted at the door the model's text comes in by, for
free and before anything is stored. What these hold it to is the two places it
must not go: a gloss, which belongs in exactly the documents this product is
for, and code, where a Chinese identifier is the sample.
"""

from __future__ import annotations

import pytest

from app.services import lint
from app.services.hangul import read_back


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
    """The machine fixes the script; it cannot know a wrong word from a right
    one. `試點` reads 시점, which is a real Korean word meaning something else —
    the leak was a mistranslation before it was a script problem. So what was
    substituted is reported rather than swallowed."""
    _, replaced = read_back("試點 프로젝트를 연다")
    assert replaced == ["試點"]


def test_a_gloss_is_left_alone() -> None:
    """`분산(分散)` in a paper, a legal term, a name in its original script.
    Banning the block outright would be its own kind of wrong in exactly the
    documents this product is for, and `분산(분산)` is worse than what it
    replaced."""
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
    """The common case, and the one that must cost nothing."""
    text = "전교생이 두 과목을 듣는다."
    assert read_back(text) == (text, [])


def test_what_was_read_back_no_longer_trips_the_check() -> None:
    """The two halves have to agree, or the person sees a P0 about a word that
    is no longer on the page. `hangul` imports `lint`'s own judgement of what a
    leak is rather than copying it, so they cannot drift apart."""
    clean, replaced = read_back("傳統的인 방화벽으로는 막지 못한다. 分散 처리를 쓴다.")
    assert replaced
    assert not lint._stray_hanja(clean)
