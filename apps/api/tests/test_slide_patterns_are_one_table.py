"""One pattern table: the panel's `patterns.ts` is the mirror of `slide_patterns.wire()`.

A pattern added to one and not the other would be offered by the writer and drawn as
bullets by the panel, or picked in the panel and refused by the writer.
"""

import json
import re
from pathlib import Path

from app.services import slide_patterns

_WEB = Path(__file__).resolve().parents[3] / "apps/web/src/components/slides/patterns.ts"


def _web_table() -> list[dict]:
    text = _WEB.read_text(encoding="utf-8")
    start = text.index("export const PATTERNS")
    block = text[text.index("= [", start) + 2 : text.index("\n]", start) + 2]
    # The literal is JSON but for unquoted keys and single quotes.
    as_json = re.sub(r"([{,]\s{0,4})([A-Za-z_]\w{0,30}):", r'\1"\2":', block.replace("'", '"'))
    return json.loads(re.sub(r",\s{0,8}([\]}])", r"\1", as_json))


def test_the_panel_and_the_writer_read_one_pattern_table() -> None:
    assert _web_table() == slide_patterns.wire()
