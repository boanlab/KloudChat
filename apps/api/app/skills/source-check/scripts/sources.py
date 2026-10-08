"""조사·분석 글에서 숫자가 든 줄마다 출처·날짜가 있는지, 아니면 가정·출처 미확인으로 표시됐는지 찾고, 오래된 출처와 근거 없는 일반론을 알립니다.

인자(JSON 하나):
  {"text": "<다 쓴 글(마크다운)>",
   "material": "<사용자가 준 자료 그대로>",   # 이 안에 있는 숫자는 자료 출처로 봅니다
   "today": "2026-10-08",                    # 오래된 출처 기준일(없으면 오늘)
   "max_age_years": 3,                       # 이보다 오래된 출처는 stale
   "fix": true}                              # 출처 없는 줄 끝에 「출처 미확인」을 붙인 fixed_text 를 함께 냄

출처로 보는 것: 줄 안의 [n] 인용(아래 출처 목록에 정의돼 있어야 함), URL, 「출처:」「자료:」, 표의 출처·근거 열,
자료에 있는 숫자. 표시로 보는 것: 「가정」「출처 미확인」「확인 필요」「자료 없음」「[수치]」 같은 빈칸.
finding 의 kind: unsourced(출처 없는 숫자) · undefined_ref(정의 안 된 [n]) · ref_no_date · ref_no_link ·
stale(오래된 출처) · generic(근거 없는 일반론: 「경쟁이 치열하다」 등).
결과는 목록을 출력하고 마지막 줄에 결과 JSON을 냅니다. 파일은 만들지 않습니다.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sys

#: A number that carries a business quantity: money, share, count, rank, multiple.
QUANTITY = re.compile(
    r"(?:US\$|\$|₩|€)\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:억|조|만|천|million|billion|bn|m|k))?"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:%p|%|퍼센트|포인트|배(?!경|포|송|치|달|정)"
    r"|조\s?원?|억\s?원?|천만\s?원?|백만\s?원?|만\s?원?|천\s?원|원(?!칙|인|가|료|리|본|천)|달러|USD|유로|엔(?![가-힣])|위안"
    r"|만\s?명|천\s?명|명(?!칭|확|시|령)|개사|개국|개소|개(?!월|선|요|념|발|편|정|별|인)|곳|社|건(?!강|물|설|의|전|축)"
    r"|가구|위(?!원|해|험|치|기|반|협)|MAU|DAU|GB|TB)",
    re.I,
)
CITE = re.compile(r"\[(\d{1,3})\]")
LINK = re.compile(r"https?://\S+")
SOURCE_WORD = re.compile(
    r"출처\s*[:：]|자료\s*[:：]|\(자료\)|제공\s?자료|사용자\s?자료|\[자료\]|\(자사\s?자료\)"
)
MARKED = re.compile(
    r"가정\s*[:：)]|로\s?가정|가정(?:한|하면|하여|해|치|값)|출처\s?미확인|확인\s?필요|자료\s?없음|추정치?\s*\(|\[(?:수치|금액|비율|시장\s?규모|점유율|매출|가격|날짜|연도|출처)[^\]]*\]"
)
YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)")
#: Statements that sound like analysis but carry no fact. Flagged only when the line has no
#: number, citation or source of its own.
GENERIC = re.compile(
    r"경쟁이\s?(?:매우\s?|더욱\s?)?치열|성장\s?(?:가능성|잠재력)이\s?(?:매우\s?)?(?:크|높|풍부)"
    r"|빠르게\s?성장|급성장|고성장|(?:시장|수요)이?가?\s?(?:꾸준히\s?|지속적으로\s?)?(?:확대|증가|늘어)"
    r"|관심이\s?(?:높아|커지|증가)|경쟁력이\s?(?:높|있|강)|기회가\s?(?:많|크)|위협이\s?(?:크|높)"
    r"|진입\s?장벽이\s?(?:높|낮)|변화가\s?(?:빠르|크|급)|규제가\s?강화|니즈가\s?다양"
    r"|디지털\s?전환이?\s?(?:가속|확산)|주목받고|각광|트렌드(?:가|이다|입니다)|업계\s?선도|선도\s?기업"
    r"|시장\s?(?:지배력|영향력)이\s?(?:크|높|강)|인지도가\s?(?:높|낮)"
)
SOURCE_HEADING = re.compile(
    r"^#{1,6}\s*.*(?:출처|참고\s?자료|참고\s?문헌|References|Sources)", re.I
)
REF_LINE = re.compile(r"^\s*(?:[-*]\s*)?\[(\d{1,3})\]\s*(.+)$")
SOURCE_COLUMN = re.compile(r"출처|근거|자료|source", re.I)
BLOCKING = ("unsourced", "undefined_ref")


def _digits(token: str) -> str:
    found = re.search(r"\d[\d,]*(?:\.\d+)?", token)
    return found.group(0).replace(",", "") if found else ""


def _material_numbers(material: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", material or "")}


def _cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _is_rule(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*", line))


def _today(value) -> _dt.date:
    if value:
        try:
            return _dt.date.fromisoformat(str(value)[:10])
        except ValueError:
            pass
    return _dt.date.today()


def references(lines: list[str]) -> tuple[dict[str, dict], set[int]]:
    """[n] entries under a 출처 heading (or anywhere as 「[n] …」 list lines), and their line indexes."""
    refs: dict[str, dict] = {}
    ref_lines: set[int] = set()
    in_section = False
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            in_section = bool(SOURCE_HEADING.match(line.strip()))
            if in_section:
                ref_lines.add(index)
            continue
        found = REF_LINE.match(line)
        if found and (in_section or LINK.search(line) or YEAR.search(found.group(2))):
            refs[found.group(1)] = {"line": index, "text": found.group(2).strip()}
            ref_lines.add(index)
        elif in_section and line.strip():
            ref_lines.add(index)
    return refs, ref_lines


def check(text: str, material: str = "", options: dict | None = None) -> list[dict]:
    options = options or {}
    today = _today(options.get("today"))
    max_age = int(options.get("max_age_years", 3))
    lines = (text or "").split("\n")
    known = _material_numbers(material)
    refs, ref_lines = references(lines)
    findings: list[dict] = []
    used: set[str] = set()
    header: list[str] | None = None
    source_col: int | None = None
    fenced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not stripped or index in ref_lines:
            header = None if not stripped else header
            continue
        is_row = stripped.startswith("|")
        if is_row:
            if header is None:
                header = _cells(stripped)
                source_col = next(
                    (i for i, c in enumerate(header) if SOURCE_COLUMN.search(c)), None
                )
                continue
            if _is_rule(stripped):
                continue
        else:
            header, source_col = None, None
        if stripped.startswith("#"):
            continue
        for cite in CITE.findall(line):
            used.add(cite)
        cited = bool(CITE.search(line) or LINK.search(line) or SOURCE_WORD.search(line))
        if is_row and source_col is not None:
            cells = _cells(stripped)
            cell = cells[source_col] if source_col < len(cells) else ""
            if cell and cell not in ("-", "—", "?"):
                cited = True
        marked = bool(MARKED.search(line))
        tokens = [m for m in QUANTITY.finditer(line) if not re.fullmatch(r"\d{1,2}\.", m.group(0))]
        loose = [m for m in tokens if _digits(m.group(0)) not in known]
        if loose and not cited and not marked:
            first = loose[0]
            findings.append(
                {
                    "kind": "unsourced",
                    "line": index,
                    "start": first.start(),
                    "end": first.end(),
                    "text": first.group(0).strip(),
                    "excerpt": stripped[:120],
                    "why": "숫자에 출처·날짜가 없고 가정·출처 미확인 표시도 없습니다",
                    "suggestion": "출처 [n]과 날짜를 붙이거나, 가정이면 「가정: 근거」, 모르면 「출처 미확인」",
                    "placeholder": "「출처 미확인」",
                }
            )
        if not tokens and not cited and not marked:
            generic = GENERIC.search(line)
            if generic:
                findings.append(
                    {
                        "kind": "generic",
                        "line": index,
                        "start": generic.start(),
                        "end": generic.end(),
                        "text": generic.group(0),
                        "excerpt": stripped[:120],
                        "why": "근거 없는 일반론입니다",
                        "suggestion": "이 판단을 받치는 수치·사례와 출처를 붙이거나 문장을 지우세요",
                        "placeholder": "",
                    }
                )
        for found in re.finditer(r"출처\s*[:：]\s*([^)\]\n|]+)", line):
            years = [int(y) for y in YEAR.findall(found.group(1))]
            if years and today.year - max(years) > max_age:
                findings.append(_stale(index, found.group(1).strip(), max(years), today, max_age))
    for cite in sorted(used, key=int):
        if cite not in refs:
            findings.append(
                {
                    "kind": "undefined_ref",
                    "line": None,
                    "start": 0,
                    "end": 0,
                    "text": f"[{cite}]",
                    "excerpt": "",
                    "why": f"[{cite}] 이 출처 목록에 없습니다",
                    "suggestion": "출처 목록에 [n] 자료명·URL·날짜를 넣으세요",
                    "placeholder": "",
                }
            )
    for number, ref in sorted(refs.items(), key=lambda kv: int(kv[0])):
        body = ref["text"]
        years = [int(y) for y in YEAR.findall(body)]
        own = bool(SOURCE_WORD.search(body) or re.search(r"사용자|제공|내부|자사|첨부", body))
        if not years and not own:
            findings.append(
                {
                    "kind": "ref_no_date",
                    "line": ref["line"],
                    "start": 0,
                    "end": 0,
                    "text": f"[{number}]",
                    "excerpt": body[:120],
                    "why": "출처에 날짜(발행일·접속일)가 없습니다",
                    "suggestion": "발행일 또는 확인한 날짜를 적으세요",
                    "placeholder": "",
                }
            )
        if (
            not LINK.search(body)
            and not own
            and not re.search(r"DART|공시|보고서|통계|KOSIS|ECOS|보도", body)
        ):
            findings.append(
                {
                    "kind": "ref_no_link",
                    "line": ref["line"],
                    "start": 0,
                    "end": 0,
                    "text": f"[{number}]",
                    "excerpt": body[:120],
                    "why": "URL이나 문서명이 없어 다시 찾을 수 없습니다",
                    "suggestion": "URL 또는 발행 기관·문서명을 적으세요",
                    "placeholder": "",
                }
            )
        if years and today.year - max(years) > max_age:
            findings.append(
                _stale(ref["line"], body[:80], max(years), today, max_age, f"[{number}]")
            )
    return findings


def _stale(line, text, year, today, max_age, label=None) -> dict:
    return {
        "kind": "stale",
        "line": line,
        "start": 0,
        "end": 0,
        "text": label or text,
        "excerpt": text,
        "why": f"{year}년 자료로 {today.year - year}년 지났습니다(기준 {max_age}년)",
        "suggestion": "최신 자료로 바꾸거나 「(○○년 기준)」을 숫자 옆에 밝히세요",
        "placeholder": "",
    }


def fix(text: str, findings: list[dict]) -> str:
    """Mark each unsourced line 「출처 미확인」 (in the table's 출처 cell when there is one)."""
    lines = (text or "").split("\n")
    for finding in findings:
        if finding["kind"] != "unsourced" or finding.get("line") is None:
            continue
        line = lines[finding["line"]]
        if "출처 미확인" in line:
            continue
        stripped = line.rstrip()
        if stripped.lstrip().startswith("|") and stripped.endswith("|"):
            cells = stripped.split("|")
            # The last real cell (before the closing pipe) takes the mark.
            cells[-2] = (cells[-2].rstrip() + " 「출처 미확인」 ").replace("  「", " 「")
            lines[finding["line"]] = "|".join(cells)
        else:
            lines[finding["line"]] = stripped + " 「출처 미확인」"
    return "\n".join(lines)


def summary(findings: list[dict]) -> str:
    if not findings:
        return "출처 점검: 문제 없음"
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    names = {
        "unsourced": "출처 없는 숫자",
        "undefined_ref": "정의 안 된 인용",
        "ref_no_date": "날짜 없는 출처",
        "ref_no_link": "찾을 수 없는 출처",
        "stale": "오래된 출처",
        "generic": "근거 없는 일반론",
    }
    return "출처 점검: " + " · ".join(f"{names.get(k, k)} {v}" for k, v in counts.items())


def run(args: dict) -> dict:
    findings = check(str(args.get("text") or ""), str(args.get("material") or ""), args)
    result = {
        "ok": not any(f["kind"] in BLOCKING for f in findings),
        "findings": findings,
        "summary": summary(findings),
        "counts": {
            k: sum(1 for f in findings if f["kind"] == k) for k in {f["kind"] for f in findings}
        },
    }
    if args.get("fix"):
        result["fixed_text"] = fix(str(args.get("text") or ""), findings)
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
        where = f"{f['line'] + 1}행" if isinstance(f.get("line"), int) else "-"
        print(f"[{f['kind']}] {where} {f['text']}: {f['why']}")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
