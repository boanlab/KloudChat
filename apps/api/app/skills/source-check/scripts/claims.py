"""경쟁사 비교표·배틀카드의 칸마다 출처가 있는지, 아니면 「확인 필요」「자료 없음」으로 표시됐는지 보고, 근거 없는 우위 판정과 막연한 평가어를 찾습니다.

인자(JSON 하나):
  {"text": "<마크다운: 표와 목록>",
   "material": "<사용자 자료 그대로>",          # 이 안에 그대로 있는 칸 내용·숫자는 자료 출처로 봅니다
   "label_columns": 1,                        # 왼쪽에서 몇 열이 항목 이름인지(검사하지 않음)
   "sections": "강점|약점|이기는|지는|우위|열위|가격|기능|반론|대응|차별",  # 이 제목 아래 글머리표도 검사
   "fix": true}                               # 출처 없는 칸 → 「확인 필요」, 빈칸 → 「자료 없음」, 근거 없는 판정 → 「판단 보류」

칸의 출처로 보는 것: [n] 인용, URL, 「출처:」, 「(자사 자료)」「(제공 자료)」, 같은 행의 출처·근거 열, 자료에 그대로 있는 내용.
표시로 보는 것: 「확인 필요」「출처 미확인」「자료 없음」「가정」「판단 보류」.
finding 의 kind: unsourced_cell · empty_cell · vague_cell(「우수」「강력」처럼 사실 없는 평가) ·
verdict_without_evidence(한쪽이라도 확인 필요인데 우위를 판정) · unsourced_bullet.
결과는 목록을 출력하고 마지막 줄에 결과 JSON을 냅니다. 파일은 만들지 않습니다.
"""

from __future__ import annotations

import json
import re
import sys

CITED = re.compile(
    r"\[\d{1,3}\]|https?://|출처\s*[:：]|자료\s*[:：]|\((?:자사|제공|사용자|내부)\s?자료[^)]*\)|\[자료\]"
)
OPEN = re.compile(
    r"확인\s?필요|출처\s?미확인|자료\s?없음|판단\s?보류|가정\s*[:：)]|\(가정|해당\s?없음|N/?A", re.I
)
EMPTY = {"", "-", "—", "–", "?", "??", "tbd", "TBD"}
VAGUE = re.compile(
    r"^(?:매우\s?|아주\s?|상대적으로\s?)?(?:우수|뛰어남|뛰어나다|강력|강력함|강함|좋음|좋다|보통|양호|높음|낮음|다양|다양함|"
    r"풍부|풍부함|편리|편리함|간편|탁월|최고|최상|업계\s?최고|부족|미흡|약함|열세|우세|강점|약점)[.!]?$"
)
SOURCE_HEADER = re.compile(r"출처|근거|source", re.I)
VERDICT_HEADER = re.compile(r"우위|승자|판정|winner|결과|유리", re.I)
VERDICT_HOLD = re.compile(r"판단\s?보류|확인\s?필요|동등|비교\s?불가|^\s*[-—]?\s*$")
DEFAULT_SECTIONS = r"강점|약점|이기는|지는|우위|열위|가격|기능|반론|대응|차별|위협"


def _cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _is_rule(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*", line))


def _norm(text: str) -> str:
    return re.sub(r"[\s,·「」『』\"'*]", "", text or "")


def _in_material(cell: str, material: str, material_norm: str) -> bool:
    if not material:
        return False
    core = re.sub(r"\[\d+\]", "", cell).strip()
    if len(_norm(core)) >= 4 and _norm(core) in material_norm:
        return True
    numbers = re.findall(r"\d[\d,]*(?:\.\d+)?", core)
    if numbers and all(n.replace(",", "") in material.replace(",", "") for n in numbers):
        words = re.sub(r"\d[\d,]*(?:\.\d+)?", "", core)
        return len(_norm(words)) <= 4
    return False


def tables(lines: list[str]) -> list[dict]:
    found = []
    index = 0
    while index < len(lines):
        if (
            lines[index].strip().startswith("|")
            and index + 1 < len(lines)
            and _is_rule(lines[index + 1])
        ):
            header = _cells(lines[index])
            rows = []
            cursor = index + 2
            while cursor < len(lines) and lines[cursor].strip().startswith("|"):
                rows.append((cursor, _cells(lines[cursor])))
                cursor += 1
            found.append({"header_line": index, "header": header, "rows": rows})
            index = cursor
        else:
            index += 1
    return found


def check(text: str, material: str = "", options: dict | None = None) -> list[dict]:
    options = options or {}
    labels = int(options.get("label_columns", 1))
    material = material or ""
    material_norm = _norm(material)
    lines = (text or "").split("\n")
    findings: list[dict] = []
    for t_index, table in enumerate(tables(lines)):
        header = table["header"]
        source_col = next((i for i, c in enumerate(header) if SOURCE_HEADER.search(c)), None)
        verdict_col = next((i for i, c in enumerate(header) if VERDICT_HEADER.search(c)), None)
        for line_no, cells in table["rows"]:
            row_sourced = False
            if source_col is not None and source_col < len(cells):
                src = cells[source_col]
                row_sourced = bool(src) and src not in EMPTY and not OPEN.fullmatch(src)
            row_open = False
            label = " / ".join(cells[:labels])
            for col, cell in enumerate(cells):
                if col < labels or col in (source_col, verdict_col):
                    continue
                column = header[col] if col < len(header) else f"{col + 1}열"
                base = {
                    "table": t_index,
                    "line": line_no,
                    "column": column,
                    "row": label,
                    "text": cell,
                }
                core = re.sub(r"\[\d+\]|\([^)]*자료[^)]*\)", "", cell).strip()
                if cell.strip() in EMPTY:
                    findings.append(
                        {
                            **base,
                            "kind": "empty_cell",
                            "why": "빈칸입니다",
                            "suggestion": "「자료 없음」 또는 「확인 필요」로 적으세요",
                            "placeholder": "자료 없음",
                        }
                    )
                    row_open = True
                    continue
                if OPEN.search(cell):
                    row_open = True
                    continue
                if VAGUE.match(core):
                    findings.append(
                        {
                            **base,
                            "kind": "vague_cell",
                            "why": f"「{core}」는 사실이 아닌 평가어입니다",
                            "suggestion": "무엇이 어떻게 다른지(기능·수치·조건)와 출처를 적으세요",
                            "placeholder": "확인 필요",
                        }
                    )
                    row_open = True
                    continue
                if CITED.search(cell) or row_sourced or _in_material(cell, material, material_norm):
                    continue
                findings.append(
                    {
                        **base,
                        "kind": "unsourced_cell",
                        "why": "출처가 없습니다",
                        "suggestion": "출처 [n]을 붙이거나 「확인 필요」로 적으세요",
                        "placeholder": "확인 필요",
                    }
                )
                row_open = True
            if verdict_col is not None and verdict_col < len(cells):
                verdict = cells[verdict_col]
                if row_open and verdict and not VERDICT_HOLD.search(verdict):
                    findings.append(
                        {
                            "table": t_index,
                            "line": line_no,
                            "column": header[verdict_col],
                            "row": label,
                            "text": verdict,
                            "kind": "verdict_without_evidence",
                            "why": "근거가 비거나 확인 필요인 칸이 있는데 우위를 판정했습니다",
                            "suggestion": "「판단 보류」로 두세요",
                            "placeholder": "판단 보류",
                        }
                    )
    sections = re.compile(options.get("sections") or DEFAULT_SECTIONS)
    in_section = False
    for line_no, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or (stripped.startswith("**") and stripped.endswith("**")):
            in_section = bool(sections.search(stripped))
            continue
        if not in_section or not re.match(r"^(?:[-*•]|\d+[.)])\s+", stripped):
            continue
        body = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", stripped)
        if CITED.search(body) or OPEN.search(body) or _in_material(body, material, material_norm):
            continue
        # A question to ask the prospect is not a claim.
        if (
            body.rstrip().endswith("?")
            or body.rstrip().endswith("?」")
            or body.rstrip().endswith('?"')
        ):
            continue
        findings.append(
            {
                "table": None,
                "line": line_no,
                "column": "",
                "row": "",
                "text": body[:120],
                "kind": "unsourced_bullet",
                "why": "주장에 출처가 없습니다",
                "suggestion": "출처 [n]을 붙이거나 끝에 「(확인 필요)」",
                "placeholder": "확인 필요",
            }
        )
    return findings


def fix(text: str, findings: list[dict]) -> str:
    lines = (text or "").split("\n")
    by_line: dict[int, list[dict]] = {}
    for f in findings:
        by_line.setdefault(f["line"], []).append(f)
    for line_no, items in by_line.items():
        line = lines[line_no]
        if items[0]["table"] is None:
            if "확인 필요" not in line:
                lines[line_no] = line.rstrip() + " (확인 필요)"
            continue
        lead = line[: len(line) - len(line.lstrip())]
        cells = _cells(line)
        for f in items:
            # Locate the cell by its text and column name within this row.
            for col, cell in enumerate(cells):
                if cell != f["text"]:
                    continue
                if f["kind"] == "empty_cell":
                    cells[col] = "자료 없음"
                elif f["kind"] == "verdict_without_evidence":
                    cells[col] = "판단 보류"
                elif f["kind"] in ("unsourced_cell", "vague_cell"):
                    cells[col] = f"{cell} (확인 필요)"
                break
        lines[line_no] = lead + "| " + " | ".join(cells) + " |"
    return "\n".join(lines)


def summary(findings: list[dict]) -> str:
    if not findings:
        return "비교표 점검: 문제 없음"
    names = {
        "unsourced_cell": "출처 없는 칸",
        "empty_cell": "빈칸",
        "vague_cell": "막연한 평가",
        "verdict_without_evidence": "근거 없는 판정",
        "unsourced_bullet": "출처 없는 주장",
    }
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    return "비교표 점검: " + " · ".join(f"{names.get(k, k)} {v}" for k, v in counts.items())


def run(args: dict) -> dict:
    text = str(args.get("text") or "")
    findings = check(text, str(args.get("material") or ""), args)
    result = {"ok": not findings, "findings": findings, "summary": summary(findings)}
    if args.get("fix"):
        result["fixed_text"] = fix(text, findings)
    return result


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else {}
        result = run(options if isinstance(options, dict) else {})
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    print(result["summary"])
    for f in result["findings"]:
        print(f"[{f['kind']}] {f['line'] + 1}행 {f['row']} {f['column']}: {f['text']} — {f['why']}")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
