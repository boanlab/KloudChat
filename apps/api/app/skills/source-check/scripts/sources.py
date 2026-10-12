"""조사·분석 글에서 숫자가 든 줄마다 출처·날짜가 있는지, 아니면 가정·출처 미확인으로 표시됐는지 찾고, 오래된 출처와 근거 없는 일반론을 알립니다."""

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
#: Analysis-sounding statements with no fact; flagged only without own number/citation/source.
GENERIC = re.compile(
    r"경쟁이\s?(?:매우\s?|더욱\s?)?치열|성장\s?(?:가능성|잠재력)이\s?(?:매우\s?)?(?:크|높|풍부)"
    r"|빠르게\s?성장|급성장|고성장|(?:시장|수요)이?가?\s?(?:꾸준히\s?|지속적으로\s?)?(?:확대|증가|늘어)"
    r"|관심이\s?(?:높아|커지|증가)|경쟁력이\s?(?:높|있|강)|기회가\s?(?:많|크)|위협이\s?(?:크|높)"
    r"|진입\s?장벽이\s?(?:높|낮)|변화가\s?(?:빠르|크|급)|규제가\s?강화|니즈가\s?다양"
    r"|디지털\s?전환이?\s?(?:가속|확산)|주목받고|각광|트렌드(?:가|이다|입니다)|업계\s?선도|선도\s?기업"
    r"|시장\s?(?:지배력|영향력)이\s?(?:크|높|강)|인지도가\s?(?:높|낮)"
)
#: Clause break (「…성장 중이며 …」); a colon or dash introduces evidence, not a new clause.
CLAUSE_END = re.compile(r"(?:이며|며|하고|지만|는데|으나|고)\s|[,;]\s|[.!?](?:\s|$)")


def _clause(line: str, start: int, end: int) -> str:
    """The clause of `line` that holds [start, end)."""
    lo = max((m.end() for m in CLAUSE_END.finditer(line, 0, start)), default=0)
    after = CLAUSE_END.search(line, end)
    return line[lo: after.start() if after else len(line)]
SOURCE_HEADING = re.compile(
    r"^#{1,6}\s*.*(?:출처|참고\s?자료|참고\s?문헌|References|Sources)", re.I
)
REF_LINE = re.compile(r"^\s*(?:[-*]\s*)?\[(\d{1,3})\]\s*(.+)$")
SOURCE_COLUMN = re.compile(r"출처|근거|자료|source", re.I)
BLOCKING = ("unsourced", "undefined_ref", "cite_mismatch")


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
        generic = GENERIC.search(line) if not marked else None
        # A citation doesn't make 「경쟁이 치열하다」 a fact; a figure in the same clause does.
        own = bool(generic) and _flat(generic.group(0)) in _flat(material)
        if generic and not own and not QUANTITY.search(_clause(line, generic.start(), generic.end())):
            findings.append(
                {
                    "kind": "generic",
                    "line": index,
                    "start": generic.start(),
                    "end": generic.end(),
                    "text": generic.group(0),
                    "excerpt": stripped[:120],
                    "why": ("인용이 있어도 수치·사례 없는 일반론입니다" if cited
                            else "근거 없는 일반론입니다"),
                    "suggestion": "이 판단을 받치는 수치·사례와 출처를 붙이거나 문장을 지우세요",
                    "placeholder": "",
                }
            )
        for found in re.finditer(r"출처\s*[:：]\s*([^)\]\n|]+)", line):
            years = [int(y) for y in YEAR.findall(found.group(1))]
            if years and today.year - max(years) > max_age:
                findings.append(_stale(index, found.group(1).strip(), max(years), today, max_age))
    if material.strip() and options.get("verify_cites", True):
        findings += cite_mismatches(lines, refs, ref_lines, material)
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


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _bigrams(text: str) -> set[str]:
    flat = re.sub(r"[^0-9A-Za-z가-힣]", "", text or "").lower()
    return {flat[i:i + 2] for i in range(len(flat) - 1)}


#: A material line that is itself a cited claim (a draft under review), not a source's text.
_CLAIM_LINE = re.compile(r"\S\s*\[\d{1,3}\]")
_WORD = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")
#: Words every analysis uses; they say nothing about whether the source holds the claim.
_COMMON = {"시장", "기업", "고객", "자료", "출처", "기준", "규모", "경우", "있다", "있습", "있으", "없다",
           "한다", "합니", "된다", "됩니", "이다", "입니", "대한", "통해", "위해", "따라", "the", "and"}


def source_text(ref: str, material: str) -> str:
    """The material lines that are the source `ref` names: those sharing its name's words."""
    want = _bigrams(re.sub(r"https?://\S+|\d{4}[-.]\d{1,2}(?:[-.]\d{1,2})?", "", ref))
    if len(want) < 2:
        return ""
    picked = []
    for line in (material or "").split("\n"):
        if not line.strip() or _CLAIM_LINE.search(line):
            continue
        shared = len(want & _bigrams(line))
        if shared >= 3 or (shared >= 2 and shared >= 0.5 * len(want)):
            picked.append(line)
    return "\n".join(picked)


_SCALE = {"조": 1e12, "억": 1e8, "천만": 1e7, "백만": 1e6, "만": 1e4, "천": 1e3}
_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s?(조|억|천만|백만|만|천)?")
#: Where the claim a citation closes begins: the last clause break before it.
_CLAUSE_BREAK = re.compile(r".*(?:[.!?。;:：—–]\s|^\s*[-*•]\s|\|)")


def _values(text: str) -> set[float]:
    """Every amount in `text` as a number, 「1.2만」 and 「12,000」 alike."""
    out = set()
    for m in _AMOUNT.finditer(text or ""):
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        out.add(round(value * _SCALE.get(m.group(2) or "", 1), 6))
        out.add(round(value, 6))
    return out


def _derived(values: set[float]) -> set[float]:
    """Simple arithmetic on the source's values: sums, differences, ratios, percent changes."""
    base = sorted(v for v in values if v)[:60]
    out = set(values)
    for a in base:
        for b in base:
            # Sums and differences of small counts would make any small number derivable.
            if a >= 100 and b >= 100:
                out.update({round(a + b, 6), round(abs(a - b), 6)})
            # A price with 10% VAT added or taken off.
            out.update({round(a * 1.1, 6), round(a / 1.1, 6)})
            for ratio in (a / b * 100, (a - b) / b * 100):
                out.update({round(ratio, 1), round(abs(ratio), 1), round(ratio), round(abs(ratio))})
    return out


OWN_LINE = re.compile(r"자사|우리\)|\(우리|내부\s?자료|사용자\s?자료")
OWN_SOURCE = re.compile(r"^\s*\[(?:자사|내부|우리)[^\]\n]{0,20}\]|\(우리\)|^\s*\[사업\s?계획")
#: A run of citations closing one claim: 「[1][3]」「[1], [3]」.
CITE_RUN = re.compile(r"\[\d{1,3}\](?:\s?,?\s?\[\d{1,3}\])*")
#: The writer's own material named as the source of a claim: 「(자사 자료)」.
OWN_MARK = re.compile(r"\((?:자사|제공|사용자|내부)\s?자료[^)\n]{0,20}\)")
CLAIM_MARK = re.compile(r"(?P<own>" + OWN_MARK.pattern + r")|" + CITE_RUN.pattern)


def cite_mismatches(lines: list[str], refs: dict, ref_lines: set[int], material: str) -> list[dict]:
    """Cited claims the cited sources don't hold: an underivable figure or no shared words."""
    out = []
    sources = {n: source_text(ref["text"], material) for n, ref in refs.items()}
    pool = _values(material)
    # The writer's own figures (자사 자료) may be set against a cited source's.
    own = _values("\n".join(line for line in (material or "").split("\n") if OWN_LINE.search(line)))
    # What the writer's own material says of its own product: its 「[자사 자료]」 lines.
    own_text = "\n".join(line for line in (material or "").split("\n") if OWN_SOURCE.search(line))
    for index, line in enumerate(lines):
        if index in ref_lines or line.strip().startswith("#"):
            continue
        for match in CLAIM_MARK.finditer(line):
            numbers_cited = CITE.findall(match.group(0))
            # 「(자사 자료)」 is a citation of the writer's own material.
            source = "\n".join([own_text if match.group("own") else "",
                                *(sources.get(n) or "" for n in numbers_cited)]).strip()
            head = line[: match.start()]
            # 「출처 [1]을 붙였습니다」 talks about the citation; it is not a claim.
            if not source or re.search(r"출처\s?$", head):
                continue
            head = OWN_MARK.sub("", CITE.sub("", head))
            found = _CLAUSE_BREAK.match(head)
            claim = (head[found.end():] if found else head).strip()[-120:]
            # An open item (「연동 지원 여부」「확인 필요」) claims nothing.
            if not claim or re.search(r"여부|확인\s?필요|미확인|자료\s?없음|\?\s*$", claim):
                continue
            have = _derived(_values(source) | own) | pool
            numbers = [m.group(0).strip() for m in QUANTITY.finditer(claim)]
            missing = [x for x in numbers if not ({round(v, 1) for v in _values(x)} | _values(x)) & have]
            words = [w for w in _WORD.findall(claim) if w[:2] not in _COMMON]
            low = source.lower()
            hits = sum(1 for w in words if (w[:2] if w[0] >= "가" else w.lower()) in low)
            if missing or (not numbers and len(words) >= 4 and hits < 0.25 * len(words)):
                label = match.group(0)
                out.append({
                    "kind": "cite_mismatch", "line": index, "start": match.start(),
                    "end": match.end(), "text": label, "excerpt": claim[:120],
                    "why": (f"인용한 {label}의 자료에 {', '.join(missing[:3])}이(가) 없습니다" if missing
                            else f"인용한 {label}의 자료에 이 내용이 없습니다"),
                    "suggestion": "자료에 있는 대로 고치거나, 인용을 빼고 「출처 미확인」으로 표시하세요",
                    "placeholder": "「출처 미확인」",
                })
    return out


def fix_cites(text: str, findings: list[dict]) -> str:
    """Each mismatched [n] replaced by 「출처 미확인」 on its line."""
    lines = (text or "").split("\n")
    for f in sorted((f for f in findings if f["kind"] == "cite_mismatch"),
                    key=lambda f: (f["line"], f["start"]), reverse=True):
        line = lines[f["line"]]
        if line[f["start"]:f["end"]] == f["text"]:
            lines[f["line"]] = line[:f["start"]] + "「출처 미확인」" + line[f["end"]:]
    return "\n".join(lines)


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
        "cite_mismatch": "자료에 없는 인용",
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
