"""The computed-values block tells the writer to work out a value it lacks with its formula
written out (which the grounding check then confirms), never to leave 「(계산 필요)」 in the
document.

Rounds 1 and 3 r1d03, r3r06 (KC-SA): 「| 총비용 | 8,100,000원 | (계산 필요) | 29,700,000원 |」 —
the Wi-Fi 6E total the calc stage did not print was left as the placeholder its block asked for."""

from __future__ import annotations

import inspect

from app.services import calc


def test_the_block_never_asks_for_a_placeholder():
    source = inspect.getsource(calc)
    assert "「(계산 필요)」로 남겨라" not in source
    assert "계산식을 함께 적어라" in source
