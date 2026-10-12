"""What a shipped 서식 does, a person sets the same way, and the recorded runs come out the same.

Each recorded lineup turn (`fixtures/replay/`) is replayed with the shipped 서식's behaviour
coming from what a person can set instead of from the shipped file:

- 진행 과정 설정 on the agent (`Agent.process_settings`) in place of the 서식's `[process]`;
- a person's template (`UserTemplate`) made from the editor's fields in place of the 서식.

The events must be the ones recorded (a person's template's own format id aside: it names the
template, `ut-<id>`).
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from types import SimpleNamespace

import pytest
import replay_harness as harness

from app.services import design_templates, process_settings, user_templates


@pytest.fixture
def replay(monkeypatch):
    from app.services import settings_store

    async def litellm_config():
        return "http://replay-litellm", "unused"

    monkeypatch.setattr(settings_store, "litellm_config", litellm_config)
    real = settings_store.tools_config

    async def without_search():
        return dataclasses.replace(await real(), search="")

    monkeypatch.setattr(settings_store, "tools_config", without_search)
    harness.pin_recorded_defaults(monkeypatch.setattr)
    return monkeypatch


async def _replay(replay, name: str, **override) -> tuple[list, list, harness.Gateway]:
    case, calls, golden = harness.load(name)
    gateway = harness.Gateway(calls=calls)
    gateway.install(replay.setattr)
    harness.freeze(replay.setattr, case["now"])
    case = {**case, "write": {**case["write"], **override}}
    got = json.loads(json.dumps(await harness.run_report(case), ensure_ascii=False))
    return got, golden, gateway


def _without_process(replay, template_id: str) -> dict:
    """The shipped 서식 with its `[process]` taken away; what it declared."""
    template = design_templates.get(template_id)
    declared = dict(template.process)
    stripped = dataclasses.replace(template, process={})
    replay.setitem(design_templates._TEMPLATES, template_id, stripped)
    return declared


REPORTS = ["minutes", "gaejo", "comparison", "client_proposal", "career_history"]
_TEMPLATE = {"minutes": "doc-minutes", "gaejo": "doc-gaejo-report",
             "comparison": "doc-comparison", "client_proposal": "doc-client-proposal",
             "career_history": "doc-career-history"}


@pytest.mark.parametrize("name", REPORTS)
async def test_an_agents_own_settings_run_the_recorded_turn_as_the_template_did(replay, name):
    template_id = _TEMPLATE[name]
    declared = _without_process(replay, template_id)
    assert declared, template_id
    # Without them the run is not the recorded one (the settings matter) ...
    assert process_settings.resolve(template_id=template_id, format_id=None) \
        == process_settings.DEFAULT
    # ... and set on the agent, as the form stores them, it is.
    own = process_settings.stored_form(process_settings.clean(
        json.loads(json.dumps(process_settings.stored_form(declared)))))
    got, golden, gateway = await _replay(replay, name, process=own)
    assert not gateway.misses, gateway.misses[:1]
    assert got == golden


def _row_from(template_id: str) -> SimpleNamespace:
    """A person's template made from the editor's fields as `draft_of` fills them, with the
    form uploaded (its text)."""
    shipped = design_templates.get(template_id)
    fields = user_templates.clean(user_templates.draft_of(shipped))
    return SimpleNamespace(id=str(uuid.UUID(int=7)), owner_id="u1", form_name="form.docx",
                           form_text=design_templates.form_text(shipped),
                           updated_at=None, **fields)


def _same_but_the_format_id(got: list, golden: list, made: str, shipped: str) -> None:
    text = json.dumps(got, ensure_ascii=False).replace(f'"format": "{made}"',
                                                       f'"format": "{shipped}"')
    assert json.loads(text) == golden


@pytest.mark.parametrize("name", ["minutes", "gaejo"])
async def test_a_template_made_in_the_editor_runs_the_recorded_turn_as_the_shipped_one(
        replay, name):
    shipped = design_templates.get(_TEMPLATE[name])
    made = user_templates.build(_row_from(shipped.id))
    replay.setitem(design_templates._USER, made.id, made)
    # The same format, questions, settings and outline.
    fmt, own = shipped.doc_format, made.doc_format_def
    from app.services import doc_formats

    want = doc_formats.BY_ID[fmt]
    assert (own.label, own.fields, own.numbering, own.head, own.lists, own.closing,
            own.subtitle) == (want.label, want.fields, want.numbering, want.head, want.lists,
                              want.closing, want.subtitle)
    assert made.questions == shipped.questions
    assert process_settings.resolve(template_id=made.id, format_id=None) \
        == process_settings.resolve(template_id=shipped.id, format_id=None)
    assert (made.check_skills, made.check_options, made.cover_page,
            made.body_face_from_design) == (shipped.check_skills, shipped.check_options,
                                            shipped.cover_page, shipped.body_face_from_design)
    assert (made.instructions, made.checklist, made.genre) == (
        shipped.instructions, shipped.checklist, shipped.genre)
    got, golden, gateway = await _replay(replay, name, template_id=made.id, format_id=None)
    assert not gateway.misses, gateway.misses[:1]
    _same_but_the_format_id(got, golden, made.id, fmt)


# ── 검토 규칙, structure rules and attachable checks ─────────────────


async def test_a_rule_in_words_is_judged_quoted_and_never_rewritten():
    from app.services import report_checks, verify

    sections = [{"heading": "현황", "content": "예산은 충분하다고 판단됨.\n담당 부서가 확인함."}]
    asked = []

    async def complete(model, messages, api_key, max_tokens):
        asked.append(messages[0]["content"])
        return json.dumps({"items": [{"sentence": "예산은 충분하다고 판단됨.", "rule": 1,
                                      "why": "근거 수치 없음"}]}), {}

    check = report_checks.judge_rules(["판단에는 근거 수치를 붙인다."], complete, "judge", "k")
    found, _ = await check(verify.Doc(sections, "자료"))
    assert "1. 판단에는 근거 수치를 붙인다." in asked[0]
    assert [(f.code, f.sentence, f.auto) for f in found] == [
        ("rule_broken", "예산은 충분하다고 판단됨.", False)]
    assert "검토 규칙 1" in found[0].why
    # Shown, never rewritten: the repair leaves a non-auto finding alone.
    out, left, _ = await verify.repair(verify.Doc(sections, "자료"), found, complete=complete,
                                       model="m", api_key="k")
    assert out == sections and left == found


async def test_structure_rules_are_checked_by_code():
    from app.services import report_checks, verify

    process = process_settings.apply(process_settings.DEFAULT, process_settings.clean({
        "structure": {"sections": ["개요", "결정 사항", "할 일"],
                      "tables": [{"columns": ["할 일", "담당", "기한"], "heading": "할 일"}]}}))
    checks = report_checks.structure_checks(process)
    ok = [{"heading": "개요", "content": "a"}, {"heading": "결정 사항", "content": "b"},
          {"heading": "할 일", "content": "| 할 일 | 담당 | 기한 |\n|---|---|---|\n| x | y | z |"}]
    bad = [{"heading": "할 일", "content": "- x"}, {"heading": "개요", "content": "a"}]
    async def found(sections):
        out = []
        for check in checks:
            out += (await check(verify.Doc(sections)))[0]
        return out

    assert await found(ok) == []
    codes = sorted(f.code for f in await found(bad))
    assert codes == ["section_missing", "section_order", "table_missing"]
    # A fixed outline is set by code: its order is not checked again.
    fixed = process_settings.apply(process, {"fixed_outline": True})
    assert len(report_checks.structure_checks(fixed)) == 1


def test_a_summary_callout_and_a_page_limit_are_structure_data():
    from app.services import report_checks

    settings = process_settings.apply(process_settings.DEFAULT, process_settings.clean({
        "structure": {"summary_lines": 3, "page_limit": 2}}))
    pre = report_checks.Prepass(format_id="", process=settings)
    sections = [{"heading": "현황", "content": "- 내용"}]
    amended = report_checks.amendments(pre, sections, "", "", [])
    assert [a.code for a in amended] == ["summary_missing"]
    assert "exactly 3 plain lines" in amended[0].instruction
    assert settings.has_stage("page-limit")
    assert report_checks.page_limit(settings, "정리해 줘") == 2
    # Two lines is the 개조식 report's own instruction, word for word.
    gaejo = report_checks.Prepass(format_id="gaejo")
    assert report_checks.amendments(gaejo, sections, "", "", [])[0].instruction \
        == report_checks._summary_instruction(2)


def test_every_named_check_and_calculation_can_be_attached_by_name():
    from app.services import report_checks

    every = process_settings.clean({
        "prepass": list(process_settings.PREPASS)[:5], "checks": list(process_settings.CHECKS),
        "judged": list(process_settings.JUDGED), "amend": list(process_settings.AMENDS),
        "head": list(process_settings.HEADS), "stages": list(process_settings.STAGES)})
    settings = process_settings.apply(process_settings.DEFAULT, every)
    pre = report_checks.Prepass(format_id="", process=settings)
    assert len(report_checks.checks(pre)) >= 2  # those with nothing to check build none
    assert len(report_checks.model_checks(pre, complete=lambda *a: None, judge="j",
                                          api_key="k")) == len(process_settings.JUDGED)
    with pytest.raises(process_settings.ProcessError):
        process_settings.clean({"checks": ["no-such-check"]})
    with pytest.raises(process_settings.ProcessError):
        process_settings.clean({"rules": ["x"] * 9})
