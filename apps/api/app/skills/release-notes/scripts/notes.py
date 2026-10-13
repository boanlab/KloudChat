"""변경 목록·커밋 로그·이슈 목록을 항목으로 나누고, 릴리스 노트가 모든 항목을 담았거나 뺀 이유를 밝혔는지, 버전·날짜·호환성 주의가 맞는지 점검합니다."""

from __future__ import annotations

import datetime as dt
import json
import re
import sys

SECTIONS = ("새 기능", "개선", "수정", "알려진 문제", "주의", "지원 종료")
_BREAKING_SECTIONS = ("주의", "호환성", "breaking", "지원 종료", "중요")

_CONVENTIONAL = re.compile(r"^(feat|fix|perf|refactor|docs|chore|test|tests|ci|build|style|revert|deps)"
                           r"(?:\(([^)]*)\))?(!)?\s*:\s*", re.I)
_ID = re.compile(r"(?<![\w-])(#\d+|[A-Z][A-Z0-9]+-\d+|GH-\d+)(?![\w-])")
_SHA = re.compile(r"^\s*([0-9a-f]{7,40})\b\s+")
_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.)])\s+")
_BREAKING = re.compile(
    r"BREAKING|breaking change|호환\s*(?:되지|안\s*됨|불가|성\s*깨)|하위\s*호환|마이그레이션|migration"
    r"|(?:API|필드|파라미터|매개변수|엔드포인트|옵션|설정|기능|지원|요금제)[^\n]{0,20}?(?:제거|삭제|폐지|중단|종료)"
    r"|(?:removed?|drop(?:ped)?|deprecat\w*)\b|이름\s*변경|rename",
    re.I,
)
_KIND_WORDS = (
    ("internal", r"^merge\b|^bump\b|의존성|리팩터|refactor|테스트\s*(?:추가|수정)|CI\b|빌드\s*설정|lint|오타 수정|typo"),
    ("fix", r"수정|고침|해결|버그|오류|에러|fix|bug|crash|깨짐|안\s*되던"),
    ("removal", r"제거|삭제|폐지|중단|지원\s*종료|remove|drop|deprecat"),
    ("feature", r"추가|신규|새\s*기능|도입|지원\s*(?:시작|추가)|add|new|introduce|support"),
    ("improvement", r"개선|향상|빨라|최적화|변경|업데이트|improve|enhance|optimi[sz]e|update|perf"),
)
_CONV_KIND = {"feat": "feature", "fix": "fix", "perf": "improvement", "revert": "fix"}
_SECTION_FOR = {"feature": "새 기능", "improvement": "개선", "fix": "수정", "removal": "주의", "internal": None}

_NUM = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)*)\s*(%|배|x|×|초|분|시간|일|개|건|명|MB|GB|KB|ms)?", re.I)
_VERSION = re.compile(r"(?<![\w.])[vV]?(\d+\.\d+(?:\.\d+)?(?:-[\w.]+)?)(?![\w.]*\d)")
_DATE = re.compile(r"(?<!\d)(20\d{2})\s*(?:[.\-/]|년)\s*(\d{1,2})\s*(?:[.\-/]|월)\s*(\d{1,2})")
_JARGON = re.compile(r"(?<![\w-])(#\d+|[A-Z][A-Z0-9]+-\d+)(?![\w-])|\b[0-9a-f]{7,40}\b|`[^`]+`"
                     r"|\b[a-z]+_[a-z_]+\b|\b[a-z]+[A-Z]\w+\b")
#: Product names spelled like camelCase code (r7 A16-T1: 「iOS 18」 is not jargon).
_PRODUCT_NAMES = {"iOS", "iPadOS", "macOS", "watchOS", "tvOS", "visionOS", "iPhone", "iPad", "iCloud", "eSIM"}
#: A deprecation notice: the thing still works and goes later (r7 A16-T1: 「2027-03-31 종료 예정」).
_NOTICE = re.compile(r"deprecat\w*|(?:종료|중단|폐지|제거|삭제)\s*예정|지원\s*종료\s*(?:예고|안내)|sunset", re.I)
_HARD_BREAK = re.compile(r"BREAKING|breaking change|호환\s*(?:되지|안\s*됨|불가|성\s*깨)", re.I)
#: 「매월 3일」: a policy day the first-apply date is worked out from (SKILL.md Procedure 3).
_MONTHLY = re.compile(r"매월\s*(\d{1,2})\s*일")


def _notice(text: str) -> bool:
    """A deprecation announced ahead (「… 종료 예정」), not a change that breaks this release."""
    match = _CONVENTIONAL.match(text)
    return bool(_NOTICE.search(text)) and not _HARD_BREAK.search(text) and not (match and match.group(3))


def _kind(text: str) -> tuple[str, bool]:
    match = _CONVENTIONAL.match(text)
    breaking = (bool(_BREAKING.search(text)) or bool(match and match.group(3))) and not _notice(text)
    if match:
        kind = _CONV_KIND.get(match.group(1).lower(), "internal")
        rest = text[match.end():]
        if kind == "internal" and re.search(_KIND_WORDS[2][1], rest, re.I) and match.group(3):
            kind = "removal"
        return kind, breaking
    for kind, pattern in _KIND_WORDS:
        if re.search(pattern, text, re.I):
            return kind, breaking
    return "improvement", breaking


def parse(log: str) -> list[dict]:
    """One item per non-empty line of a change list / commit log / issue list."""
    items: list[dict] = []
    seen: set[str] = set()
    for number, raw in enumerate(log.split("\n"), start=1):
        line = raw.strip()
        if not line or re.match(r"^#{1,6}\s|^(?:commit|Author|Date)[:\s]", line):
            continue
        sha = _SHA.match(line)
        text = _BULLET.sub("", line[sha.end():] if sha else line).strip()
        if not text:
            continue
        ids = _ID.findall(text)
        ident = ids[0] if ids else (sha.group(1)[:7] if sha else f"L{number}")
        if ident in seen:
            ident = f"{ident}@L{number}"
        seen.add(ident)
        kind, breaking = _kind(text)
        notice = _notice(text)
        items.append({"id": ident, "text": text, "kind": kind, "breaking": breaking, "notice": notice,
                      "section": "주의" if breaking or notice else _SECTION_FOR[kind], "line": number})
    return items


def _semver(value: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"[vV]?(\d+)\.(\d+)(?:\.(\d+))?(?:-[\w.]+)?", value.strip())
    return tuple(int(g or 0) for g in match.groups()) if match else None


def _numbers(text: str) -> set[str]:
    """Numbers as written, commas and leading zeros dropped (「2026-11-04」 gives 「4」)."""
    out: set[str] = set()
    # 「22:00」 is 「22시」 written another way: its 「00」 is no new figure (r7 A16-T2).
    text = re.sub(r"(?<=\d):00(?!\d)", "", text)
    for match in _NUM.finditer(text):
        raw = match.group(1).replace(",", "")
        out.add(raw)
        if raw.isdigit():
            out.add(str(int(raw)))
    return out


def _first_apply(date: str | None, basis: str) -> list[dt.date]:
    """First day each 「매월 N일」 in `basis` falls on from the release date on
    (배포 11/4, 매월 3일 → 12/3): the date SKILL.md Procedure 3 asks to write (r7 A16-T2)."""
    try:
        release = dt.date.fromisoformat(date or "")
    except ValueError:
        return []
    out = []
    for match in _MONTHLY.finditer(basis):
        day = int(match.group(1))
        month = release.month + (day < release.day)
        year, month = release.year + (month - 1) // 12, (month - 1) % 12 + 1
        try:
            out.append(dt.date(year, month, day))
        except ValueError:
            continue
    return out


def _finding(severity: str, kind: str, text: str, why: str, fix: str, **extra) -> dict:
    return {"severity": severity, "type": kind, "text": text, "why": why, "fix": fix, **extra}


def check(items: list[dict], entries: list[dict], omitted: list[dict] | None = None,
          version: str | None = None, previous_version: str | None = None, date: str | None = None,
          notes: str | None = None) -> dict:
    omitted = omitted or []
    findings: list[dict] = []
    by_id = {str(item["id"]): item for item in items}
    for item in items:
        if "breaking" not in item:
            item["breaking"] = _kind(str(item.get("text", "")))[1]
        if "notice" not in item:
            item["notice"] = _notice(str(item.get("text", "")))
    used: dict[str, list[int]] = {}
    for index, entry in enumerate(entries):
        sources = [str(s) for s in entry.get("sources") or []]
        text = str(entry.get("text", ""))
        label = text[:40]
        if not sources:
            findings.append(_finding("block", "no_source", label, "어느 입력 항목에서 온 내용인지 밝히지 않았습니다.",
                                     "근거 항목 id를 sources 에 넣거나, 입력에 없는 내용이면 지우세요.", entry=index))
        for source in sources:
            if source not in by_id:
                findings.append(_finding("block", "unknown_source", f"{label} ← {source}",
                                         "입력에 없는 항목 id입니다.", "id를 고치거나 항목을 지우세요.", entry=index))
            used.setdefault(source, []).append(index)
        basis = " ".join(str(by_id[s].get("text", "")) for s in sources if s in by_id)
        # A worked-out first-apply date (entry `derived`, or 매월 N일 from the release date) is known.
        worked = " ".join(f"{d.year} {d.month} {d.day}" for d in _first_apply(date, basis))
        worked += " " + " ".join(str(d) for d in entry.get("derived") or [])
        extra = (_numbers(text) - _numbers(basis) - _numbers(version or "") - _numbers(date or "")
                 - _numbers(worked))
        if sources and extra:
            findings.append(_finding("block", "invented_figure", f"{label}: {', '.join(sorted(extra))}",
                                     "근거 항목에 없는 숫자입니다(속도 배수·비율·건수를 지어내지 않음).",
                                     "숫자를 빼고 사실만 쓰세요.", entry=index))
        section = str(entry.get("section", ""))
        if section and section not in SECTIONS and not any(w in section.lower() for w in _BREAKING_SECTIONS):
            findings.append(_finding("info", "section", section, "권장 절 이름이 아닙니다.",
                                     f"{'·'.join(SECTIONS)} 가운데 고르세요.", entry=index))
        jargon = sorted({m.group(0) for m in _JARGON.finditer(text)} - _PRODUCT_NAMES)
        if jargon:
            findings.append(_finding("check", "jargon", f"{label}: {', '.join(jargon[:4])}",
                                     "사용자용 문장에 티켓 번호·커밋 해시·코드 이름이 남았습니다.",
                                     "사용자가 보는 화면·동작으로 바꿔 쓰세요(개발자용 노트면 그대로 둬도 됨).", entry=index))
    omitted_ids = {}
    for row in omitted:
        ident = str(row.get("id"))
        omitted_ids[ident] = str(row.get("reason") or "").strip()
        if ident not in by_id:
            findings.append(_finding("check", "unknown_source", ident, "omitted 에 입력에 없는 id가 있습니다.", "id를 고치세요."))
        elif not omitted_ids[ident]:
            findings.append(_finding("block", "omitted_without_reason", ident, "뺀 이유가 없습니다.",
                                     "「내부 작업」「사용자 영향 없음」처럼 이유를 적으세요."))
    for ident, item in by_id.items():
        in_notes, dropped = ident in used, ident in omitted_ids
        if not in_notes and not dropped:
            findings.append(_finding("block", "missing", f"{ident} {str(item.get('text', ''))[:40]}",
                                     "릴리스 노트에 없고 뺀 이유도 없습니다.", "넣거나 omitted 에 이유와 함께 적으세요.", id=ident))
        if in_notes and dropped:
            findings.append(_finding("check", "both", ident, "노트에도 있고 뺀 목록에도 있습니다.", "한쪽만 남기세요.", id=ident))
        if item.get("breaking") or item.get("notice"):
            if dropped and not in_notes:
                findings.append(_finding("block", "breaking_omitted", ident,
                                         "호환성이 깨지거나 기능이 없어지는 변경은 뺄 수 없습니다.",
                                         "「주의」 절에 무엇을 해야 하는지와 함께 넣으세요.", id=ident))
            for index in used.get(ident, []):
                entry = entries[index]
                section = str(entry.get("section", "")).lower()
                action = re.search(r"해야|세요|십시오|바랍니다|바꿔|변경해|옮기|옮겨|업데이트해|필요|대신|\[할 일\]",
                                   str(entry.get("text", "")))
                if not any(w in section for w in _BREAKING_SECTIONS) or not action:
                    findings.append(_finding("block", "breaking_unflagged", f"{ident} → {str(entry.get('text', ''))[:30]}",
                                             "호환성 깨짐 후보가 「주의」 절에 할 일과 함께 있지 않습니다.",
                                             "「주의」 절로 옮기고 사용자가 할 일(또는 [할 일])을 적으세요. "
                                             "깨지지 않는 변경이면 items 에 \"breaking\": false 로 밝히세요.",
                                             id=ident, entry=index))
    current = _semver(version) if version else None
    if version and current is None:
        findings.append(_finding("block", "version", version, "버전 형식을 읽지 못했습니다.", "2.4.0 같은 형식으로 쓰세요."))
    if not version:
        findings.append(_finding("check", "version", "", "버전이 없습니다.", "자료에 있으면 넣고, 없으면 [버전]으로 두고 물으세요."))
    previous = _semver(previous_version) if previous_version else None
    if current and previous:
        if current <= previous:
            findings.append(_finding("block", "version_order", f"{previous_version} → {version}",
                                     "새 버전이 이전 버전보다 크지 않습니다.", "버전을 확인하세요."))
        elif any(i.get("breaking") for i in items) and previous[0] >= 1 and current[0] == previous[0]:
            findings.append(_finding("check", "version_bump", f"{previous_version} → {version}",
                                     "호환성 깨짐 후보가 있는데 주 버전이 그대로입니다.",
                                     "정말 깨지는 변경인지, 버전 정책이 어떤지 확인하세요."))
    if date:
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            findings.append(_finding("block", "date", date, "날짜 형식을 읽지 못했습니다.", "YYYY-MM-DD로 주세요."))
            date = None
    else:
        findings.append(_finding("check", "date", "", "배포 날짜가 없습니다.", "자료에 있으면 넣고, 없으면 [배포일]."))
    if notes:
        allowed_versions = {v for v in (version, previous_version) if v}
        allowed_versions |= {m.group(1) for i in items for m in _VERSION.finditer(str(i.get("text", "")))}
        for match in _VERSION.finditer(notes):
            if _DATE.search(notes[max(0, match.start() - 5):match.end() + 3]):
                continue
            if match.group(1) not in {v.lstrip("vV") for v in allowed_versions}:
                findings.append(_finding("block", "version_mismatch", match.group(0),
                                         f"노트 속 버전이 주어진 버전({version})과 다릅니다.", "버전을 맞추세요."))
        item_text = " ".join(str(i.get("text", "")) for i in items)
        item_dates = {tuple(int(g) for g in m.groups()) for m in _DATE.finditer(item_text)}
        item_dates |= {(d.year, d.month, d.day) for d in _first_apply(date, item_text)}
        for entry in entries:
            item_dates |= {tuple(int(g) for g in m.groups())
                           for m in _DATE.finditer(" ".join(str(d) for d in entry.get("derived") or []))}
        target = tuple(int(p) for p in date.split("-")) if date else None
        for match in _DATE.finditer(notes):
            value = tuple(int(g) for g in match.groups())
            if value != target and value not in item_dates:
                findings.append(_finding("block", "date_mismatch", match.group(0),
                                         f"노트 속 날짜가 배포일({date or '없음'})과 다르고 입력 항목에도 없습니다.",
                                         "날짜를 맞추거나 [날짜]로 두세요."))
    counts = {level: sum(1 for f in findings if f["severity"] == level) for level in ("block", "check", "info")}
    coverage = {"items": len(items), "in_notes": sum(1 for i in by_id if i in used),
                "omitted": sum(1 for i in by_id if i in omitted_ids and i not in used),
                "missing": sum(1 for i in by_id if i not in used and i not in omitted_ids),
                "breaking": [i for i, item in by_id.items() if item.get("breaking")],
                "notices": [i for i, item in by_id.items() if item.get("notice")]}
    return {"findings": findings, "coverage": coverage, "counts": counts}


_DATE_ANY = re.compile(r"(?<!\d)(20\d{2})\s*(?:[.\-/]|년)\s*(\d{1,2})\s*(?:[.\-/]|월)\s*(\d{1,2})\s*(?:일|\.)?")
_VALUE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)*)\s?(%|배|x|×|초|분|시간|일|개월|년|개|건|명|MB|GB|KB|ms|원)(?![\w])", re.I)
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}|[가-힣]{2,}")
_ITEM_LINE = re.compile(r"^\s*(?:[-*•·]|\d{1,2}[.)])\s+")
_YEAR = re.compile(r"(?:19|20)\d{2}")
_FENCE = re.compile(r"```[^\n]{0,20}\n[\s\S]{0,4000}?\n\s{0,8}```")
_MONTH_DAY = re.compile(r"(?<![\d.])(\d{1,2})\s*월\s*(\d{1,2})\s*일|(?<![\d./])(\d{1,2})/(\d{1,2})(?![\d/])")
#: The skill's own tally line (「항목 6개: 노트 6 · 뺌 0 · 빠짐 0」), not a claim.
_TALLY = re.compile(r"항목\s?\d{1,3}\s?개\s?[:：]|뺀\s?항목")
#: Headings of the note's own sections; items outside them are the writer's remarks.
_NOTE_HEADING = re.compile(r"^\s{0,3}(?:#{1,6}\s*|\*\*)\s*(" + "|".join(SECTIONS) + r"|호환성|breaking)", re.I)


def _terms(text: str) -> set[str]:
    """Words of a line, Korean ones cut to their first two syllables (particles drop off)."""
    return {w.lower() if w[0].isascii() else w[:2] for w in _TOKEN.findall(text or "")}


def post(text: str, args: dict) -> dict:
    """Unlisted dates/versions blanked (code); unlisted numbers and unsupported items to writer."""
    out: dict = {"text": text, "fixed": [], "rewrite": [], "flags": []}
    material = str(args.get("material") or "")
    if not material.strip():
        return out
    final = bool(args.get("final"))
    dates = {(int(m.group(1)), int(m.group(2)), int(m.group(3))) for m in _DATE_ANY.finditer(material)}
    # A month and day the list gives without the year (「10월 27일 배포」).
    days = {(int(m.group(1) or m.group(3)), int(m.group(2) or m.group(4)))
            for m in _MONTH_DAY.finditer(material)}
    days |= {(m, d) for _, m, d in dates}
    versions = {m.group(1) for m in _VERSION.finditer(material)}
    known = _numbers(material)
    items = parse(material)
    lines = text.split("\n")
    in_notes = False
    for k, line in enumerate(lines):
        if re.match(r"^\s{0,3}(?:#{1,6}\s|\*\*[^*\n]{1,30}\*\*\s*:?\s*$|---\s*$)", line):
            in_notes = bool(_NOTE_HEADING.match(line))
        if _TALLY.search(line):
            continue

        def date(m: re.Match, line: str = line) -> str:
            key = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if key in dates or key[1:] in days:
                return m.group(0)
            blank = "[배포일]" if re.search(r"배포|출시|릴리스|^#", line) else "[날짜]"
            out["fixed"].append({"kind": "date", "text": m.group(0).strip(), "why": f"변경 목록에 없는 날짜 → {blank}"})
            return blank + (" " if m.group(0).endswith(" ") else "")

        line = _DATE_ANY.sub(date, line)

        def version(m: re.Match) -> str:
            if m.group(1) in versions or _DATE_ANY.match(m.group(0)):
                return m.group(0)
            out["fixed"].append({"kind": "version", "text": m.group(0), "why": "변경 목록에 없는 버전 → [버전]"})
            return "[버전]"

        line = _VERSION.sub(version, line)
        for m in _VALUE.finditer(line):
            if m.group(1).replace(",", "") in known or (m.group(2) == "년" and _YEAR.fullmatch(m.group(1))):
                continue
            # a listed day in another notation (「11월 4일」 for 「2026-11-04」)
            month = re.search(r"(\d{1,2})\s*(?:월|/|\.)\s*$", line[:m.start()])
            if m.group(2) == "일" and month and (int(month.group(1)), int(m.group(1))) in days:
                continue
            # the list's own items counted (「항목 7개」)
            if m.group(2) in ("개", "건") and m.group(1).isdigit() and 0 < int(m.group(1)) <= len(items):
                continue
            if final:
                line = line.replace(m.group(0), "[수치]", 1)
                out["fixed"].append({"kind": "number", "text": m.group(0), "why": "변경 목록에 없는 수치 → [수치]"})
            else:
                out["rewrite"].append({"kind": "number", "text": m.group(0),
                                       "why": "변경 목록에 없는 수치입니다. 빼거나 값 뒤에 「(확인 필요)」를 붙이세요."})
        lines[k] = line
        if items and in_notes and _ITEM_LINE.match(line):
            mine = _terms(re.sub(r"\([^)]*\)|`[^`]*`", "", line))
            best = max((len(mine & _terms(i["text"])) / max(1, len(_terms(i["text"]))) for i in items),
                       default=0.0)
            if mine and best < 0.2:
                item = {"kind": "unsupported_item", "text": _ITEM_LINE.sub("", line).strip()[:160],
                        "why": "변경 목록의 어느 항목에도 없는 내용입니다. 빼세요."}
                (out["flags"] if final else out["rewrite"]).append(item)
    text = "\n".join(lines)
    # A code sample the change list does not hold (an endpoint, a payload) is made up.
    if "```" not in material:
        for block in _FENCE.findall(text):
            item = {"kind": "code", "text": block.strip()[:160],
                    "why": "변경 목록에 없는 코드 예시입니다. 빼세요."}
            if final:
                text = text.replace(block, "", 1)
                out["fixed"].append({**item, "why": "변경 목록에 없는 코드 예시 → 뺌"})
            else:
                out["rewrite"].append(item)
        text = re.sub(r"\n{3,}", "\n\n", re.sub(r"\n[ \t]+(?=\n)", "\n", text))
    out["text"] = text
    return out


def _line(finding: dict) -> str:
    return f"[{finding['severity']}] {finding['type']}: {finding['text']} — {finding['why']} → {finding['fix']}"


def main(argv: list[str]) -> int:
    try:
        args = json.loads(argv[1]) if len(argv) > 1 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"JSON 인자를 읽지 못했습니다: {exc}"}, ensure_ascii=False))
        return 1
    op = args.get("op") if isinstance(args, dict) else None
    if op == "parse":
        items = parse(str(args.get("log") or ""))
        for item in items:
            flag = " ⚠호환성" if item["breaking"] else ""
            print(f"{item['id']}\t{item['kind']}\t{item['section'] or '(내부: 기본으로 뺌)'}{flag}\t{item['text'][:60]}")
        print(json.dumps({"items": items, "count": len(items)}, ensure_ascii=False))
        return 0
    if op == "check":
        items = args.get("items") or (parse(str(args["log"])) if args.get("log") else [])
        if not items:
            print(json.dumps({"error": "items 또는 log 가 필요합니다."}, ensure_ascii=False))
            return 1
        result = check([dict(i) for i in items], list(args.get("entries") or []), list(args.get("omitted") or []),
                       args.get("version"), args.get("previous_version"), args.get("date"), args.get("notes"))
        for finding in result["findings"]:
            print(_line(finding))
        c = result["coverage"]
        print(f"항목 {c['items']}개: 노트 {c['in_notes']} · 뺌 {c['omitted']} · 빠짐 {c['missing']}")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    print(json.dumps({"error": "op 는 parse 또는 check 입니다."}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
