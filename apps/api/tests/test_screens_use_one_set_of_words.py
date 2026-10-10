"""Screens and server messages use one set of words for one thing.

A document's structure is a 템플릿 and its look a 테마; an agent's steps are its 진행 과정.
The words those replaced (양식, 디자인, 워크플로, 작동 단계) and the hub's old 추천 do not come
back on a screen, in the dictionary, in a step label or check result, or in the in-app guide.
「서식」 alone stays: it is text formatting (bold, italics).

An allowed use is listed with the reason; a new one goes into `_ALLOWED` the same way.
"""

import json
import re
import tomllib
from pathlib import Path

import pytest

from app.pipeline import labels
from app.services import report_checks
from tests.test_the_app_does_not_narrate_its_own_changes import _without_comments

_REPO = Path(__file__).resolve().parents[3]
_WEB = _REPO / "apps/web/src"
_APP = Path(__file__).resolve().parents[1] / "app"

_OLD = re.compile(r"양식|디자인|워크플로|작동 단계|아티팩트|추천|서식 보정|새로 만들기")

#: (file suffix, text the line carries) → why the word may stand there.
_ALLOWED = {
    # Matches a person's own item names to an icon; it reads names, it shows no word.
    ("components/customize/itemIcons.ts", "/디자인|서식|양식|"): "matcher over item names",
    # Recognises a document request such as 「추천서 써 줘」; not shown.
    ("lib/documentRequest.ts", "추천서"): "matcher over the request",
    # A bot's suggested questions, as assistants call them.
    ("components/publishing/PublicationSettingsForm.tsx", "추천 질문"): "suggested questions",
    ("lib/i18n.ts", "'추천 질문'"): "suggested questions",
}


def _allowed(path: str, line: str) -> bool:
    return any(path.endswith(suffix) and text in line for (suffix, text) in _ALLOWED)


def test_no_screen_uses_a_replaced_word():
    if not _WEB.is_dir():
        pytest.skip("the web app is not beside the API here")
    hits = []
    for path in sorted(_WEB.rglob("*.ts*")):
        rel = str(path.relative_to(_REPO))
        code = _without_comments(path.read_text(encoding="utf-8"))
        for no, line in enumerate(code.splitlines(), 1):
            if _OLD.search(line) and not _allowed(rel, line):
                hits.append(f"{rel}:{no}: {line.strip()[:120]}")
    assert not hits, "\n".join(hits)


def test_step_labels_and_check_results_use_the_current_words():
    shown = [
        *labels.LABELS.values(),
        *report_checks.LABELS.values(),
        *report_checks.MODEL_LABELS.values(),
    ]
    assert not [text for text in shown if _OLD.search(text)]


def test_catalogue_names_and_descriptions_use_the_current_words():
    hits = []
    for path in sorted(_APP.rglob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        for key in ("name", "description", "summary"):
            text = data.get(key)
            # 「별지 제2호서식」 is the form's legal name, not the app's word.
            if isinstance(text, str) and _OLD.search(text):
                hits.append(f"{path.relative_to(_APP)}: {key} = {text[:80]}")
    assert not hits, "\n".join(hits)


def test_the_in_app_guide_uses_the_current_words():
    guide = json.loads((_APP / "help/docs.json").read_text(encoding="utf-8"))
    hits = [
        f"{row['lang']}/{row['path']}#{row['heading']}: {m.group(0)}"
        for row in guide
        for m in _OLD.finditer(row["title"] + "\n" + row["text"])
    ]
    assert not hits, "\n".join(hits)


def test_the_check_catches_a_replaced_word():
    assert _OLD.search("{t('양식 고르기')}")
    assert _OLD.search("{t('이 프로젝트에서 새로 만들기')}")
    assert not _OLD.search("{t('서식')}")
    assert not _OLD.search(_without_comments("x() // 예전 양식"))
