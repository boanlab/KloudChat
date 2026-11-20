"""`sandbox_tools.unfenced`: the code in a code model's answer, fenced or not, closed or cut."""

from __future__ import annotations

import pytest

from app.services.tools import sandbox_tools

CODE = "import pandas as pd\n\ndf = pd.read_csv('a.csv')\nprint(df.describe())"


def test_a_fenced_block_is_its_code() -> None:
    assert sandbox_tools.unfenced(f"```python\n{CODE}\n```") == CODE
    assert sandbox_tools.unfenced(f"```py\n{CODE}\n```\n") == CODE
    assert sandbox_tools.unfenced(f"```\n{CODE}\n```") == CODE


def test_prose_around_a_fenced_block_is_left_out() -> None:
    answer = f"아래 코드를 실행하면 됩니다.\n\n```python\n{CODE}\n```\n\n결과는 표로 나옵니다."
    assert sandbox_tools.unfenced(answer) == CODE


def test_an_answer_without_a_fence_is_the_code_as_it_stands() -> None:
    assert sandbox_tools.unfenced(f"\n{CODE}\n") == CODE


def test_a_block_without_its_closing_fence_is_taken_to_the_end() -> None:
    assert sandbox_tools.unfenced(f"```python\n{CODE}") == CODE
    assert sandbox_tools.unfenced(f"```python\n{CODE}\n") == CODE
    assert sandbox_tools.unfenced(f"설명입니다.\n```python\n{CODE}\n") == CODE


@pytest.mark.parametrize("tail", ["`", "``", "\n``", " ``"])
def test_a_closing_fence_that_is_only_partly_there_is_dropped(tail: str) -> None:
    answer = f"```python\n{CODE}\n{tail.strip()}" if tail.strip() else f"```python\n{CODE}"
    assert sandbox_tools.unfenced(answer) == CODE


def test_two_blocks_give_the_first() -> None:
    first, second = "print('one')", "print('two')"
    answer = f"```python\n{first}\n```\n그리고\n```python\n{second}\n```"
    assert sandbox_tools.unfenced(answer) == first


def test_a_closed_block_followed_by_an_unclosed_one_gives_the_closed_one() -> None:
    answer = "```python\nprint('one')\n```\n다음은\n```python\nprint('two')"
    assert sandbox_tools.unfenced(answer) == "print('one')"
