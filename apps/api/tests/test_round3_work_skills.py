"""What agents built only from shipped skills could not do (round 3): 번역, 보도자료, an Excel
file built by code in a chat turn, a chart artifact. Each is a shipped skill people pick or an
administrator shares; the Excel file comes from the sandbox script and is a download."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import sandbox, skill_packages, starter
from app.services.tools import builtin, registry, sandbox_tools
from app.services.tools.base import ToolContext

SKILLS = Path(skill_packages.__file__).resolve().parent.parent / "skills"
BUILD = SKILLS / "xlsx-build" / "scripts" / "build.py"


def _build(args: dict, cwd: Path, ok: bool = True) -> dict:
    done = subprocess.run([sys.executable, str(BUILD), json.dumps(args, ensure_ascii=False)],
                          cwd=cwd, capture_output=True, text=True, timeout=60)
    assert (done.returncode == 0) is ok, done.stderr + done.stdout
    return json.loads(done.stdout.strip().splitlines()[-1])


# ── the catalogue ──────────────────────────────────────────────────────


def test_the_four_are_published_listed_and_seeded_like_the_others():
    specs = {spec["key"]: spec for spec in skill_packages.catalog_specs()}
    expected = {
        "translation": ("번역", ["chat", "report"], []),
        "press-release": ("보도자료", ["chat", "report"], []),
        "xlsx-build": ("엑셀 파일 만들기", ["chat"], ["run_skill_script"]),
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
    # quotes only from the material, contact as the person's own brackets
    assert "only words the material attributes to a named person" in body
    assert "[담당 부서] [담당자 이름] [전화] [이메일]" in body
    assert "[보고서에서]" in body and "[채팅에서]" in body


def test_translation_keeps_form_and_adds_nothing():
    body = skill_packages.package_for("translation").body
    for rule in ("never summarise", "tables (same rows and columns", "Glossary",
                 "「직역」", "「의역」", "「격식체」", "번역 메모"):
        assert rule in body


def test_the_chat_turn_offers_the_tools_the_two_file_skills_need():
    tool = sandbox_tools.RUN_SKILL_SCRIPT
    assert "xlsx-build" in tool.parameters["properties"]["skill"]["enum"]
    assert "xlsx-build 「엑셀 파일 만들기」: build(" in tool.description


async def test_create_chart_is_on_every_chat_turn_strict_local_included(monkeypatch):
    async def no_backends():
        return SimpleNamespace(fetch=False, search=False, exec=False)

    monkeypatch.setattr(builtin.settings_store, "tools_config", no_backends)
    assert "create_chart" in {t.name for t in await builtin.available_builtins(False)}
    strict = await registry.build_tools(None, None, web_search=False, strict_local=True,
                                        include_connectors=False)
    assert "create_chart" in {t.name for t in strict}


# ── 엑셀 파일 만들기 ────────────────────────────────────────────────────


def test_a_pasted_table_becomes_a_workbook_with_formulas_a_total_row_and_a_chart(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    summary = _build({
        "output": "지점 실적",
        "sheets": [{
            "name": "실적", "title": "3분기 지점별 실적",
            "columns": ["지점", "7월", "8월"],
            "rows": [["강남", "1,200", 1300], ["부산", 900, "850"], ["대전", 400, 520]],
            "formulas": {"합계": "=SUM({7월}:{8월})", "증감률": "=({8월}-{7월})/{7월}"},
            "formats": {"증감률": "0.0%"},
            "total_row": True,
            "chart": {"type": "bar", "x": "지점", "y": ["7월", "8월"], "title": "월별 실적"},
        }],
        "recalc": False,
    }, tmp_path)
    assert summary["outputs"] == ["outputs/지점 실적.xlsx"]
    sheet = summary["sheets"][0]
    assert sheet["formula_columns"] == ["합계", "증감률"] and sheet["total_row"] == 7
    book = openpyxl.load_workbook(tmp_path / summary["outputs"][0])
    ws = book["실적"]
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][0] == "3분기 지점별 실적"
    assert rows[2] == ("지점", "7월", "8월", "합계", "증감률")
    # 「1,200」 and 「850」 are numbers, not text
    assert rows[3] == ("강남", 1200, 1300, "=SUM(B4:C4)", "=(C4-B4)/B4")
    assert rows[4][1:3] == (900, 850)
    # totals are formulas; the rate on the total row is total ÷ total
    assert rows[6] == ("합계", "=SUM(B4:B6)", "=SUM(C4:C6)", "=SUM(B7:C7)", "=(C7-B7)/B7")
    assert ws["E4"].number_format == "0.0%" and ws["B4"].number_format == "#,##0"
    assert ws["D4"].number_format == "#,##0"
    assert len(ws._charts) == 1


def test_a_csv_with_a_rate_column_gets_its_formula_from_the_top_level(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    (tmp_path / "집행.csv").write_bytes(
        "부서,예산,집행,부서코드\n기획,\"1,000\",800,007\n총무,500,510,012\n".encode("cp949"))
    summary = _build({"file": "집행.csv", "formulas": {"집행률": "={집행}/{예산}"},
                      "formats": {"집행률": "0.0%"}, "total_row": True, "recalc": False},
                     tmp_path)
    assert summary["outputs"] == ["outputs/집행.xlsx"]
    rows = list(openpyxl.load_workbook(tmp_path / "outputs/집행.xlsx").worksheets[0]
                .iter_rows(values_only=True))
    assert rows[0] == ("부서", "예산", "집행", "부서코드", "집행률")
    # a code with a leading zero stays text and is never summed
    assert rows[1] == ("기획", 1000, 800, "007", "=C2/B2")
    assert rows[3] == ("합계", "=SUM(B2:B3)", "=SUM(C2:C3)", None, "=C4/B4")


def test_percent_text_becomes_a_rate_and_is_not_summed(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    summary = _build({"sheets": [{"name": "만족도", "columns": ["문항", "응답", "비율"],
                                  "rows": [["1번", 40, "12.5%"], ["2번", 60, "87.5%"]],
                                  "total_row": True}], "recalc": False}, tmp_path)
    ws = openpyxl.load_workbook(tmp_path / summary["outputs"][0])["만족도"]
    assert ws["C2"].value == pytest.approx(0.125) and ws["C2"].number_format == "0.0%"
    assert ws["B4"].value == "=SUM(B2:B3)" and ws["C4"].value is None


def test_a_formula_naming_a_missing_column_fails_with_its_name(tmp_path):
    pytest.importorskip("openpyxl")
    summary = _build({"sheets": [{"columns": ["a"], "rows": [[1]],
                                  "formulas": {"b": "={c}*2"}}]}, tmp_path, ok=False)
    assert summary["ok"] is False and "{c}" in summary["error"]
    empty = _build({}, tmp_path, ok=False)
    assert "sheets" in empty["error"]


def test_libreoffice_caches_the_values_when_it_is_there(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    import shutil

    if not (shutil.which("soffice") or shutil.which("libreoffice")):
        pytest.skip("LibreOffice is in the sandbox image, not every test runner")
    summary = _build({"sheets": [{"name": "표", "columns": ["항목", "금액"],
                                  "rows": [["가", 100], ["나", 250]], "total_row": True}]},
                     tmp_path)
    if not summary.get("recalculated"):
        pytest.skip(f"LibreOffice could not run here: {summary.get('note')}")
    assert summary["sheets"][0]["totals"] == {"금액": 350}
    values = openpyxl.load_workbook(tmp_path / summary["outputs"][0], data_only=True)["표"]
    assert values["B4"].value == 350


class _Sandbox:
    """Runs nothing; returns the built file as an output."""

    def __init__(self, stdout: str, blob: bytes):
        self.stdout, self.blob = stdout, blob
        self.uploads: list[list[str]] = []
        self.codes: list[str] = []

    async def upload(self, items, *, kind=None, read_only=False):
        self.uploads.append([n for n, _ in items])
        return [{"id": f"f-{n}", "name": n, "session_id": "up"} for n, _ in items]

    async def exec(self, code, *, lang="py", files=None, session_id=None, timeout_ms=30_000):
        self.codes.append(code)
        return sandbox.interpret({"session_id": "exec-1", "stdout": self.stdout, "files": [
            {"id": "out-1", "name": "outputs/지점 실적.xlsx", "session_id": "exec-1"}]})

    async def download(self, session_id, file_id):
        return self.blob


async def test_a_chat_turn_runs_the_build_and_the_file_is_a_download(monkeypatch, tmp_path):
    pytest.importorskip("openpyxl")
    args = {"output": "지점 실적.xlsx",
            "sheets": [{"columns": ["지점", "매출"], "rows": [["강남", 10]], "total_row": True}],
            "recalc": False}
    summary = _build(args, tmp_path)
    blob = (tmp_path / summary["outputs"][0]).read_bytes()
    fake = _Sandbox(json.dumps(summary, ensure_ascii=False) + "\n__KLOUDCHAT_EXIT__ 0\n", blob)
    stored: list[dict] = []

    async def client():
        return fake

    async def session_files(user_id, session_id):
        return []

    async def store_output(user_id, session_id, name, data):
        stored.append({"id": "stored-0", "name": name, "size": len(data)})
        return stored[-1]

    monkeypatch.setattr(sandbox, "client", client)
    monkeypatch.setattr(sandbox, "session_files", session_files)
    monkeypatch.setattr(sandbox, "store_output", store_output)
    monkeypatch.setattr(sandbox, "_SKILL_REFS", {})
    result = await sandbox_tools.run_skill_script(
        {"skill": "엑셀 파일 만들기", "script": "build", "args": args},
        ToolContext(user_id="u", session_id="s"))
    assert not result.failed, result.content
    assert "skills/xlsx-build/scripts/build.py" in fake.uploads[0]
    assert fake.codes[0].startswith("python3 skills/xlsx-build/scripts/build.py ")
    # the step carries the file: the chat shows it as a download
    assert result.outputs == [{"id": "stored-0", "name": "지점 실적.xlsx", "size": len(blob)}]


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
