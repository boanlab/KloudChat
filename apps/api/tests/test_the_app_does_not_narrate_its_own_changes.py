"""Screens and the in-app guide that describe what is there, not how it got there."""

import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_WEB = _REPO / "apps/web/src"
_GUIDE = _REPO / "apps/api/app/help/docs.json"

#: Phrases that only make sense to someone who knew an earlier version.
_NARRATION = re.compile(
    r"옮겼습니다|옮겼어요|새로워진|예전 이름|예전 주소|예전에 만든"
    r"|내려갔습니다|내려간 템플릿|이름이 바뀌었"
    r"|\bformerly\b|used to be called|earlier name|has moved to|moved to Settings"
    r"|were retired|was retired"
)


def _without_comments(src: str) -> str:
    """The source with `//` and `/* */` comments blanked; string literals are kept whole."""
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", src[i:j]))
            i = j
            continue
        elif src[i] in "'\"`":
            quote, j = src[i], i + 1
            while j < n and src[j] != quote and not (quote != "`" and src[j] == "\n"):
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
            continue
        else:
            out.append(src[i])
            i += 1
            continue
        out.append(" " * (j - i))
        i = j
    return "".join(out)


def test_no_screen_explains_where_something_moved():
    if not _WEB.is_dir():
        pytest.skip("the web app is not beside the API here")
    hits = []
    for path in sorted(_WEB.rglob("*.ts*")):
        code = _without_comments(path.read_text(encoding="utf-8"))
        for no, line in enumerate(code.splitlines(), 1):
            if _NARRATION.search(line):
                hits.append(f"{path.relative_to(_REPO)}:{no}: {line.strip()[:120]}")
    assert not hits, "\n".join(hits)


def test_the_in_app_guide_does_not_explain_its_history():
    hits = [
        f"{row['lang']}/{row['path']}#{row['heading']}: {m.group(0)}"
        for row in json.loads(_GUIDE.read_text(encoding="utf-8"))
        for m in _NARRATION.finditer(row["title"] + "\n" + row["text"])
    ]
    assert not hits, "\n".join(hits)


def test_the_check_catches_the_notice_it_was_written_for():
    line = "{t('메모리는 설정 › 메모리로 옮겼습니다.')} // 메모리 옮겼습니다"
    assert _NARRATION.search(_without_comments(line))
    assert not _NARRATION.search(_without_comments("x() // memory moved to Settings"))
