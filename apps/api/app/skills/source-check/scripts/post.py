"""시장·경쟁 조사 답을 서버가 마지막에 점검합니다: 출처 없는 숫자·근거 없는 일반론·자료에 없는 인용·비교표의 출처 없는 칸."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _sibling(name: str):
    key = f"_source_check_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, _HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _row(f: dict) -> dict:
    return {"kind": f["kind"], "text": str(f.get("excerpt") or f.get("text") or "")[:160],
            "why": f.get("why", "")}


_CHECKLIST = re.compile(r"확인할\s?것|확인\s?사항|확인이\s?필요|보내기\s?전|채울\s?것|질문|물어볼|민감도")
_HEADING = re.compile(r"^\s{0,3}(?:#{1,6}\s|\*\*[^*\n]{1,40}\*\*\s*:?\s*$)")


def _skipped(text: str, line: int | None) -> bool:
    """Whether a line is an equation, under a to-check heading, or in the sensitivity table."""
    if line is None:
        return False
    lines = text.split("\n")
    if "=" in lines[line] or lines[line].lstrip().startswith("※ 계산을 다시 해"):
        return True
    heading = next((lines[k] for k in range(line, -1, -1) if _HEADING.match(lines[k])), "")
    return bool(_CHECKLIST.search(heading))


#: The analysis's own row call (「직접」) or a pointer to table evidence: judgement, not fact.
_CATEGORY = re.compile(r"^(?:직접|간접|대체|잠재|미확정|해외|국내)(?:\s?[/·(]\s?\S{1,6}\)?)?$")
_POINTER = re.compile(r"근거\s?[:：]\s?(?:비교표|표|위\s?표)")


def _judgement(finding: dict) -> bool:
    text = str(finding.get("text") or "").strip()
    return bool(_CATEGORY.match(text) or re.search(r"구분|유형|분류", str(finding.get("column") or ""))
                or _POINTER.search(text))


def _rates(values: set[float]) -> list[str]:
    """Changes and shares in percent between the material's amounts of 100 or more."""
    base = sorted(v for v in values if v >= 100)[:40]
    out = []
    for a in base:
        for b in base:
            if a != b:
                for rate in ((a - b) / b * 100, a / b * 100):
                    out.append(f"{abs(rate):.1f}")
    return out


def _scaled(values: set[float]) -> list[str]:
    out = []
    for value in values:
        for scale in (1, 1e4, 1e8, 1e12):
            if value >= scale:
                for places in (1, 2):
                    shown = f"{value / scale:,.{places}f}".rstrip("0").rstrip(".")
                    out += [shown, shown.replace(",", "")]
                out.append(str(round(value / scale)))
        if value >= 1e8:
            # 「40억 3,200만 원」: the 억 part and the 만 part.
            out += [str(int(value // 1e8)), str(round(value % 1e8 / 1e4))]
    return out


_SOURCE_LINE = re.compile(r"^\s{0,3}(?:[-*]\s{1,3})?\[\[?\d{1,2}\]\]?\s")


def post(text: str, args: dict) -> dict:
    sources, claims, frame = _sibling("sources"), _sibling("claims"), _sibling("frame")
    out: dict = {"text": text, "fixed": [], "rewrite": [], "flags": []}
    material = str(args.get("material") or "")
    final = bool(args.get("final"))
    # Code output, the answer's own written equations and to-check lists are sources.
    worked = "\n".join(line for line in text.split("\n") if "=" in line)
    known = "\n".join([material, *[str(c) for c in args.get("computed") or []], worked,
                       # Rates worked out from the material's own amounts (「16.5% 늘었다」).
                       " ".join(_rates(sources._values(material)))])
    # The same worked value restated in another unit (「162,000,000,000원 (약 1,620억원)」).
    known += "\n" + " ".join(_scaled(sources._values(worked)))
    # A figure the material yields in a step or two (1,800만원 ÷ 100명 ÷ 12개월 = 15,000원).
    known += "\n" + " ".join(sorted(claims.derived_numbers(material)))
    found = [f for f in sources.check(text, known, {"today": args.get("today")})
             if not (f["kind"] == "unsourced" and _skipped(text, f.get("line")))]
    unsourced = [f for f in found if f["kind"] == "unsourced"]
    if unsourced:
        text = sources.fix(text, unsourced)
        out["fixed"] += [{**_row(f), "why": "출처 없는 숫자 → 「출처 미확인」"} for f in unsourced]
    lines = text.split("\n")
    generic_lines = {f["line"] for f in found if f["kind"] == "generic"}
    # frame.py's list of generalities, for the lines sources.py did not already catch.
    for index, line in enumerate(lines):
        hit = frame.GENERIC.search(line)
        # a source-list line (「- [6] [제목](url)」) is a title, not a claim
        if _SOURCE_LINE.match(line):
            continue
        if hit and index not in generic_lines and not line.lstrip().startswith("#") \
                and hit.group(0) not in material and not sources.MARKED.search(line):
            found.append({"kind": "generic", "line": index, "text": hit.group(0),
                          "excerpt": line.strip()[:120], "why": "근거 없는 일반론입니다"})
    mismatched = [f for f in found if f["kind"] == "cite_mismatch"]
    if final and mismatched:
        text = sources.fix_cites(text, mismatched)
        out["fixed"] += [{**_row(f), "why": f"{f['why']} → 「출처 미확인」"} for f in mismatched]
    elif mismatched:
        out["rewrite"] += [_row(f) for f in mismatched]
    for f in found:
        if f["kind"] == "generic":
            (out["flags"] if final else out["rewrite"]).append(
                {**_row(f), "why": f"{f['why']} — 수치·사례와 출처로 바꾸거나 지우세요"})
        elif f["kind"] in ("undefined_ref", "stale", "ref_no_date"):
            out["flags"].append(_row(f))
    # Server flag: a turn skill answers in comparison tables (`tables: true`).
    if args.get("tables"):
        unknown = [f for f in claims.check(text, material, {})
                   if f["kind"] == "opportunity_on_unknown"]
        if final and unknown:
            # what is left after the rewrite is labelled a guess, never cut
            text = claims.fix(text, unknown)
            out["fixed"] += [{"kind": f["kind"], "text": str(f["text"])[:80],
                              "why": f"{f['why']} → 「(가설)」"} for f in unknown]
        else:
            out["rewrite"] += [{"kind": f["kind"], "text": str(f["text"])[:80],
                                "why": f"{f['why']} — {f['suggestion']}"} for f in unknown]
        cells = [f for f in claims.check(text, material, {})
                 if f["kind"] in ("unsourced_cell", "empty_cell", "verdict_without_evidence",
                                  "unsourced_bullet") and not _judgement(f)]
        if cells:
            text = claims.fix(text, cells)
            out["fixed"] += [{"kind": f["kind"], "text": str(f["text"])[:80], "why": f["why"]}
                             for f in cells]
    out["text"] = text
    return out


def main(argv: list[str]) -> int:
    try:
        args = json.loads(argv[1]) if len(argv) > 1 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    result = post(str(args.get("text") or ""), args if isinstance(args, dict) else {})
    for key in ("fixed", "rewrite", "flags"):
        for row in result[key]:
            print(f"[{key}] {row['text'][:60]} — {row['why']}")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
