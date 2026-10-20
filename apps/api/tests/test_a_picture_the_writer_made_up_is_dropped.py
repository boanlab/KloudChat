"""A `data:` picture the writer typed out that does not open is dropped with its 「(그림 N)」."""

from __future__ import annotations

import base64
import io

from PIL import Image

from app.services import pictures


def _png() -> str:
    out = io.BytesIO()
    Image.new("RGB", (4, 3), (200, 40, 90)).save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


def test_an_invented_picture_and_its_mention_are_dropped():
    text = (
        "시장 반응이 흐릿하다(그림 1).\n\n"
        "![그림 1. 흐릿한 시장 상황](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAABIAAAANgCAIAA"
        "DbKYQBAAEAAElEQVR4AQAKh/V4AXWFk/wAAgEB)\n\n다음 문단."
    )
    assert pictures.drop_broken(text) == "시장 반응이 흐릿하다.\n\n다음 문단."


def test_an_unclosed_picture_is_dropped_to_the_end_of_its_line():
    text = "앞 문단.\n\n![그림 2. 잘린 그림](data:image/png;base64,iVBORw0KGgoAAAA\n\n뒤 문단."
    assert pictures.drop_broken(text) == "앞 문단.\n\n뒤 문단."


def test_a_real_picture_stays():
    text = f"설명 (그림 1).\n\n![그림 1. 실제 그림](data:image/png;base64,{_png()})"
    assert pictures.drop_broken(text) == text
    assert pictures.opens(f"data:image/png;base64,{_png()}")
    assert not pictures.opens("data:image/png;base64,iVBORw0KGgo=")
