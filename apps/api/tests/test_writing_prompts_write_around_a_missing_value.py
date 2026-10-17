"""Writing prompts that write around a missing value instead of a bracket blank."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from app.services import deck, report, skill_packages

APP = Path(report.__file__).resolve().parents[1]

#: the blanks a writer was told to leave for values; a person's own slots ([작성자]) are fine
VALUE_BLANK = re.compile(
    r"\[(?:담당자|기한|수치|금액|일자|일시|단가|날짜|구체적 수치|고객 수|가격)\]")

#: these describe what server code does (the grounding guard, date-calc's inaudible deadline)
CODE_DESCRIBED = {"grounding-check", "date-calc", "copy-check", "pii-mask"}


def _writing_texts() -> list[tuple[str, str]]:
    out = [(f"skill {key}", package.body) for key, package in skill_packages.packages().items()
           if key not in CODE_DESCRIBED]
    for path in sorted((APP / "agents").glob("*.toml")):
        spec = tomllib.loads(path.read_text(encoding="utf-8"))
        out.append((f"agent {path.stem}", spec.get("system_prompt") or ""))
        out.append((f"agent guide {path.stem}", spec.get("guide") or ""))
    for path in sorted((APP / "design_templates").glob("doc-*/*.md")):
        if path.parent.name in ("doc-career-history", "doc-client-proposal"):
            continue  # the person's own record: their figures are theirs to fill in
        out.append((f"template {path.parent.name}/{path.name}", path.read_text(encoding="utf-8")))
    for genre in tomllib.loads((APP / "genres.toml").read_text(encoding="utf-8"))["genres"]:
        if "rule" in genre:  # the others carry a template's genre.md, read above
            out.append((f"genre {genre['id']}", genre["rule"]))
    out += [("report section", report._SECTION_PROMPT), ("report draft", report._DRAFT_PROMPT),
            ("report table fix", report._TABLE_FIX_PROMPT), ("report amend", report._AMEND_PROMPT)]
    return out


@pytest.mark.parametrize(("where", "text"), _writing_texts(), ids=lambda v: str(v)[:40])
def test_no_writing_prompt_asks_for_a_value_blank(where, text):
    if where.split()[-1] in ("career-docs", "client-proposal"):
        pytest.skip("the person's own record: their figures are theirs to fill in")
    found = [m.group(0) for m in VALUE_BLANK.finditer(text)]
    assert not found, f"{where} still asks for {found}"


def test_the_report_prompts_keep_never_inventing_and_say_how_to_write_around():
    for prompt in (report._SECTION_PROMPT, report._DRAFT_PROMPT):
        assert "never made up" in prompt or "Never invent" in prompt
        assert "미정" in prompt and "확인 필요" in prompt
        assert "fills in themselves" in prompt


def test_the_skills_people_judged_carry_the_claude_like_rules():
    comparison = skill_packages.package_for("option-comparison").body
    assert "× 1.1" in comparison and "추천: ○○, 단 △△ 확인" in comparison
    minutes = skill_packages.package_for("minutes-writing").body
    assert "‘미정’" in minutes and "확인이 필요합니다" in minutes
    regulation = skill_packages.package_for("regulation-answer").body
    assert "2박 3일" in regulation and "calculate" in regulation
    slides = skill_packages.package_for("deck-writing").body
    assert "No quiz" in slides and "Every slide carries body text" in slides
    drafting = skill_packages.package_for("document-drafting").body
    assert "끝.」" in drafting and "발신 명의" in drafting
    # the deck writer's own rules too
    assert "No quiz" in deck._DRAFT_PROMPT and not VALUE_BLANK.search(deck._DRAFT_PROMPT)
