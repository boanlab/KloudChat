"""Shipped work skills: 번역, 보도자료, a chart artifact, 회의 안건. (An Excel file is made by the
sheet tools, not a skill: `test_a_sheet_tools_write_read_and_change_the_workbook`.)"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import design_templates, doc_formats, skill_packages, starter
from app.services.tools import builtin, registry
from app.services.tools.base import ToolContext

SKILLS = Path(skill_packages.__file__).resolve().parent.parent / "skills"


# ── the catalogue ──────────────────────────────────────────────────────


def test_the_three_are_published_listed_and_seeded_like_the_others():
    specs = {spec["key"]: spec for spec in skill_packages.catalog_specs()}
    expected = {
        "translation": ("번역", ["chat", "report"], []),
        "press-release": ("보도자료", ["chat", "report"], []),
        "chart-make": ("차트 만들기", ["chat"], ["create_chart"]),
    }
    for key, (name, kinds, tools) in expected.items():
        spec = specs[key]
        assert (spec["name"], spec["kinds"], spec["required_tools"]) == (name, kinds, tools)
        assert spec["draft"] is False and spec["listed"] is True
        # found by its name, as 「…」 in a request or an agent's skill list
        assert skill_packages.find(f"「{name}」").key == key
    # seeded into the catalogue owner's account on sign-in, shared to the workspace
    assert set(expected) <= {spec["key"] for spec in starter._SKILLS}


def test_the_press_release_checks_its_figures_and_keeps_to_the_material():
    package = skill_packages.package_for("press-release")
    assert package.checks == ("grounding-check", "generic-language-killer")
    assert package.process == {"search": False, "draws": "when-asked"}
    body = package.body
    # quotes from the material; a named spokesperson without words gets one marked draft
    assert "words the material attributes to a named person are quoted" in body
    assert "「(가안, 본인 확인 필요)」" in body and "never written as labels or headings" in body
    assert "[담당 부서] [담당자 이름] [전화] [이메일]" in body
    assert "[보고서에서]" in body and "[채팅에서]" in body


def test_translation_keeps_form_and_adds_nothing():
    body = skill_packages.package_for("translation").body
    for rule in ("never summarise", "tables (same rows and columns", "Glossary",
                 "「직역」", "「의역」", "「격식체」", "번역 메모"):
        assert rule in body


def test_an_excel_file_is_no_skill_any_more():
    assert "xlsx-build" not in {spec["key"] for spec in skill_packages.catalog_specs()}
    assert not (SKILLS / "xlsx-build").exists()


async def test_create_chart_is_on_every_chat_turn_strict_local_included(monkeypatch):
    async def no_backends():
        return SimpleNamespace(fetch=False, search=False, exec=False)

    monkeypatch.setattr(builtin.settings_store, "tools_config", no_backends)
    assert "create_chart" in {t.name for t in await builtin.available_builtins(False)}
    strict = await registry.build_tools(None, None, web_search=False, strict_local=True,
                                        include_connectors=False)
    assert "create_chart" in {t.name for t in strict}


# ── 차트 만들기 ─────────────────────────────────────────────────────────


async def test_the_chart_skill_s_call_makes_a_chart_artifact():
    body = skill_packages.package_for("chart-make").body
    assert "create_chart" in body and "never write an image link" in body
    ctx = ToolContext(user_id="u", session_id="s")
    result = await builtin.create_chart({
        "chartType": "bar", "title": "지점별 3분기 매출", "yLabel": "만 원",
        "series": [{"name": "7월", "points": [{"x": "강남", "y": 1200}, {"x": "부산", "y": 900}]},
                   {"name": "8월", "points": [{"x": "강남", "y": 1300}, {"x": "부산", "y": 850}]}],
    }, ctx)
    assert not result.failed
    artifact = ctx.pending_artifacts[0]
    assert artifact["kind"] == "chart" and len(artifact["data"]["series"]) == 2


# ── 회의 안건 템플릿 ────────────────────────────────────────────────────


def test_the_agenda_template_is_listed_bound_to_its_format_and_asks_like_the_minutes():
    template = design_templates.get("doc-agenda")
    assert template is not None and template.kind == "document"
    assert template.name == "회의 안건" and template.doc_format == "agenda"
    assert "doc-agenda" in {t.id for t in design_templates.listed()}
    assert template.form_file.endswith("form.docx") and template.docx_template
    # the same head as the minutes, so the two read as one set
    assert doc_formats.BY_ID["agenda"].fields == doc_formats.BY_ID["minutes"].fields
    assert {q["id"] for q in template.questions} == {"meeting_date", "attendees"}
    assert template.process["search"] is False and "date-calc" in template.process["prepass"]
    # never minutes: the genre forbids writing the meeting's outcome
    assert "This is not minutes" in template.genre
    assert "brackets only for what the organiser fills in" in template.genre


@pytest.mark.parametrize(("request_text", "expected"), [
    ("다음 주 운영위원회 회의 안건지를 만들어 줘", "agenda"),
    ("월요일 어젠다 정리해 줘", "agenda"),
    ("Draft the agenda for Monday's sync", "agenda"),
    ("회의록 써 줘. 회의 안건은 세 개였어", "minutes"),
    ("회의 안건별 결정 사항 정리", None),
    ("안건 표결 결과를 정리해 줘", None),
])
def test_the_agenda_is_named_by_its_own_words(request_text, expected):
    found = doc_formats.detect(request_text)
    assert (found.id if found else None) == expected
