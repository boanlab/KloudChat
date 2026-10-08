"""PESTEL·5 Forces·시장 세분화 틀을 채운 JSON 을 검사해, 요인마다 근거 사실·출처가 있거나 「자료 없음」으로 밝혀졌는지 보고 표로 그립니다.

인자(JSON 하나):
  {"template": "pestel" | "five_forces" | "segments"}   # 빈 틀(JSON)과 규칙만 받기
  또는
  {"framework": "pestel", "subject": "국내 반려동물 사료 시장", "today": "2026-10-08", "max_age_years": 3,
   "factors": {"정치": [{"claim": "수입 사료 검역 강화로 원료 조달 기간이 늘어난다",
                         "evidence": "농림축산검역본부 2026-03 고시: 검역 기간 5일→10일",
                         "source": "농림축산검역본부 고시 제2026-12호", "url": "https://...", "date": "2026-03-02",
                         "impact": "위협"}],
               "경제": [{"status": "자료 없음", "to_find": "통계청 KOSIS 반려동물 관련 지출 통계"}], ...}}
  {"framework": "five_forces", "forces": {"기존 경쟁": {"rating": "높음", "items": [...]}, "공급자 교섭력": {...},
   "구매자 교섭력": {...}, "대체재 위협": {...}, "신규 진입 위협": {...}}}
  {"framework": "segments", "segments": [{"name": "...", "criteria": "...",
   "size": {"value": 12000, "unit": "개사", "source": "...", "date": "..."} | {"status": "자료 없음", "to_find": "..."},
   "needs": [item...], "pains": [item...], "alternatives": [item...], "fit": "...", "reach": "..."}]}

항목(item) 규칙: claim(판단) + evidence(사실) + source(출처, 사용자 자료면 「사용자 자료」) + date,
  또는 "assumption": true + "basis", 또는 {"status": "자료 없음", "to_find": "어디서 무엇을 확인할지"}.
  근거 없는 일반론(「경쟁이 치열하다」「성장 가능성이 크다」)은 generic 으로 막습니다.
5 Forces 의 강도(높음·중간·낮음)는 출처 있는 항목이 하나 이상일 때만 쓰고, 없으면 「판단 보류」.
결과는 표(markdown)와 문제 목록을 출력하고 마지막 줄에 결과 JSON을 냅니다. 파일은 만들지 않습니다.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sys

PESTEL = ("정치", "경제", "사회", "기술", "환경", "법률")
PESTEL_ALIASES = {
    "political": "정치",
    "politics": "정치",
    "economic": "경제",
    "economy": "경제",
    "social": "사회",
    "sociocultural": "사회",
    "technological": "기술",
    "technology": "기술",
    "environmental": "환경",
    "environment": "환경",
    "legal": "법률",
    "law": "법률",
    "정치적": "정치",
    "경제적": "경제",
    "사회적": "사회",
    "사회문화": "사회",
    "기술적": "기술",
    "환경적": "환경",
    "법적": "법률",
    "법·제도": "법률",
    "법제도": "법률",
    "규제": "법률",
}
FORCES = ("기존 경쟁", "공급자 교섭력", "구매자 교섭력", "대체재 위협", "신규 진입 위협")
FORCE_ALIASES = {
    "rivalry": "기존 경쟁",
    "competitive rivalry": "기존 경쟁",
    "산업 내 경쟁": "기존 경쟁",
    "경쟁 강도": "기존 경쟁",
    "supplier power": "공급자 교섭력",
    "공급자": "공급자 교섭력",
    "buyer power": "구매자 교섭력",
    "구매자": "구매자 교섭력",
    "고객 교섭력": "구매자 교섭력",
    "substitutes": "대체재 위협",
    "threat of substitutes": "대체재 위협",
    "대체재": "대체재 위협",
    "new entrants": "신규 진입 위협",
    "threat of new entrants": "신규 진입 위협",
    "신규 진입": "신규 진입 위협",
    "진입 위협": "신규 진입 위협",
}
RATINGS = ("높음", "중간", "낮음", "판단 보류")
IMPACTS = ("기회", "위협", "중립", "기회·위협")
NO_DATA = "자료 없음"
GENERIC = re.compile(
    r"경쟁이\s?(?:매우\s?|더욱\s?)?치열|성장\s?(?:가능성|잠재력)이\s?(?:매우\s?)?(?:크|높|풍부)"
    r"|빠르게\s?성장|급성장|고성장|(?:시장|수요)이?가?\s?(?:꾸준히\s?|지속적으로\s?)?(?:확대|증가|늘어)"
    r"|관심이\s?(?:높아|커지|증가)|경쟁력이\s?(?:높|있|강)|기회가\s?(?:많|크)|위협이\s?(?:크|높)"
    r"|진입\s?장벽이\s?(?:높|낮)|변화가\s?(?:빠르|크|급)|규제가\s?강화|니즈가\s?다양"
    r"|디지털\s?전환이?\s?(?:가속|확산)|주목받고|각광|트렌드(?:가|이다|입니다)|업계\s?선도|선도\s?기업"
    r"|시장\s?(?:지배력|영향력)이\s?(?:크|높|강)|인지도가\s?(?:높|낮)"
)
CONCRETE = re.compile(r"\d|「[^」]+」|\"[^\"]+\"|“[^”]+”")
YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)")
BLOCKING = {
    "missing_factor",
    "no_evidence",
    "unsourced",
    "assumption_without_basis",
    "generic",
    "empty_claim",
    "bad_rating",
    "rating_without_evidence",
    "duplicate_segment",
    "no_criteria",
    "no_size",
    "unknown_framework",
}

TEMPLATES = {
    "pestel": {
        "framework": "pestel",
        "subject": "",
        "today": "",
        "factors": {
            name: [
                {
                    "claim": "",
                    "evidence": "",
                    "source": "",
                    "url": "",
                    "date": "",
                    "impact": "기회|위협|중립",
                },
                {"status": NO_DATA, "to_find": "어디서 무엇을 확인할지"},
            ]
            for name in PESTEL
        },
    },
    "five_forces": {
        "framework": "five_forces",
        "subject": "",
        "today": "",
        "forces": {
            name: {
                "rating": "높음|중간|낮음|판단 보류",
                "items": [{"claim": "", "evidence": "", "source": "", "url": "", "date": ""}],
            }
            for name in FORCES
        },
    },
    "segments": {
        "framework": "segments",
        "subject": "",
        "today": "",
        "segments": [
            {
                "name": "",
                "criteria": "구분 기준(업종·규모·행동 등)",
                "size": {"value": None, "unit": "", "source": "", "date": ""},
                "needs": [{"claim": "", "evidence": "", "source": "", "date": ""}],
                "pains": [{"status": NO_DATA, "to_find": "인터뷰·설문·VOC"}],
                "alternatives": [{"claim": "", "evidence": "", "source": "", "date": ""}],
                "fit": "",
                "reach": "",
            }
        ],
    },
}
RULES = (
    "항목마다 claim(판단)·evidence(사실: 수치·사건·인용)·source(출처, 사용자 자료면 「사용자 자료」)·date 를 채웁니다. "
    '자료가 없으면 지어내지 말고 {"status": "자료 없음", "to_find": "확인할 곳"} 하나를 둡니다. '
    '추정이면 "assumption": true 와 "basis" 를 씁니다. 근거 없는 일반론은 막힙니다.'
)


def _today(value) -> _dt.date:
    if value:
        try:
            return _dt.date.fromisoformat(str(value)[:10])
        except ValueError:
            pass
    return _dt.date.today()


def _text(value) -> str:
    return " ".join(str(value or "").split())


def _no_data(item) -> bool:
    return isinstance(item, dict) and _text(item.get("status")).replace(" ", "") == NO_DATA.replace(
        " ", ""
    )


class Checker:
    def __init__(self, args: dict):
        self.today = _today(args.get("today"))
        self.max_age = int(args.get("max_age_years", 3))
        self.findings: list[dict] = []

    def add(self, kind: str, where: str, why: str, text: str = "") -> None:
        self.findings.append({"kind": kind, "where": where, "why": why, "text": text})

    def item(self, item, where: str, impact: bool = False) -> dict:
        """One evidence item → its display row; findings recorded."""
        if isinstance(item, str):
            item = {"claim": item}
        if not isinstance(item, dict):
            self.add("empty_claim", where, "항목이 객체가 아닙니다")
            return {"claim": "", "evidence": "", "source": "", "sourced": False}
        if _no_data(item):
            to_find = _text(item.get("to_find"))
            if not to_find:
                self.add(
                    "no_to_find",
                    where,
                    "「자료 없음」에 무엇을 어디서 확인할지(to_find)가 없습니다",
                )
            return {
                "claim": NO_DATA,
                "evidence": f"확인할 자료: {to_find}" if to_find else "-",
                "source": "-",
                "impact": "-",
                "sourced": False,
                "no_data": True,
            }
        claim, evidence = _text(item.get("claim")), _text(item.get("evidence"))
        source, date, url = (
            _text(item.get("source")),
            _text(item.get("date")),
            _text(item.get("url")),
        )
        assumption = bool(item.get("assumption"))
        if not claim:
            self.add("empty_claim", where, "판단(claim)이 비었습니다", evidence[:60])
        if not evidence:
            self.add("no_evidence", where, "판단을 받치는 사실(evidence)이 없습니다", claim[:60])
        elif GENERIC.search(evidence) and not CONCRETE.search(evidence):
            self.add(
                "generic", where, "근거도 일반론입니다 — 수치·사건·인용을 적으세요", evidence[:60]
            )
        if claim and GENERIC.search(claim) and not evidence:
            self.add("generic", where, "근거 없는 일반론입니다", claim[:60])
        if assumption:
            basis = _text(item.get("basis"))
            if not basis:
                self.add(
                    "assumption_without_basis", where, "가정에 근거(basis)가 없습니다", claim[:60]
                )
            shown_source = f"가정: {basis or '근거 없음'}"
        elif source:
            shown_source = source + (f" ({url})" if url else "")
            own = re.search(r"사용자|제공|자사|내부|첨부", source)
            years = [int(y) for y in YEAR.findall(date or source)]
            if not date and not years and not own:
                self.add("source_without_date", where, "출처에 날짜가 없습니다", source[:60])
            if years and self.today.year - max(years) > self.max_age:
                self.add(
                    "stale",
                    where,
                    f"{max(years)}년 자료로 {self.today.year - max(years)}년 지났습니다",
                    source[:60],
                )
        else:
            shown_source = "출처 미확인"
            self.add(
                "unsourced",
                where,
                "출처가 없습니다 — 출처를 달거나 「자료 없음」으로 두세요",
                claim[:60],
            )
        row = {
            "claim": claim,
            "evidence": evidence or "-",
            "source": shown_source + (f", {date}" if date else ""),
            "sourced": bool(source or assumption) and bool(evidence),
        }
        if impact:
            value = _text(item.get("impact"))
            if value not in IMPACTS:
                self.add("no_impact", where, "영향(기회·위협·중립)이 없습니다", claim[:60])
            row["impact"] = value or "-"
        return row

    def items(self, values, where: str, impact: bool = False) -> list[dict]:
        if isinstance(values, dict) and not _no_data(values):
            values = [values]
        elif _no_data(values):
            values = [values]
        if not values:
            self.add(
                "missing_factor",
                where,
                "비어 있습니다 — 근거를 넣거나 「자료 없음」 항목 하나를 두세요",
            )
            return [
                {
                    "claim": NO_DATA,
                    "evidence": "-",
                    "source": "-",
                    "impact": "-",
                    "sourced": False,
                    "no_data": True,
                }
            ]
        return [self.item(v, where, impact) for v in values]


def _canon(key: str, names: tuple, aliases: dict) -> str | None:
    text = _text(key)
    if text in names:
        return text
    low = text.lower()
    if low in aliases:
        return aliases[low]
    if text in aliases:
        return aliases[text]
    return next((name for name in names if name.replace(" ", "") == text.replace(" ", "")), None)


def _esc(text: str) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def pestel(args: dict, ck: Checker) -> str:
    given = {}
    for key, value in (args.get("factors") or {}).items():
        name = _canon(key, PESTEL, PESTEL_ALIASES)
        if name is None:
            ck.add("unknown_factor", key, "PESTEL 요인이 아닙니다(정치·경제·사회·기술·환경·법률)")
            continue
        given.setdefault(name, []).extend(value if isinstance(value, list) else [value])
    lines = ["| 요인 | 판단 | 근거(사실) | 출처·날짜 | 영향 |", "|---|---|---|---|---|"]
    for name in PESTEL:
        rows = ck.items(given.get(name), name, impact=True)
        for i, row in enumerate(rows):
            lines.append(
                f"| {name if i == 0 else ''} | {_esc(row['claim'])} | {_esc(row['evidence'])} | "
                f"{_esc(row['source'])} | {row.get('impact', '-')} |"
            )
    return "\n".join(lines)


def five_forces(args: dict, ck: Checker) -> str:
    given = {}
    for key, value in (args.get("forces") or {}).items():
        name = _canon(key, FORCES, FORCE_ALIASES)
        if name is None:
            ck.add("unknown_factor", key, "5 Forces 의 힘이 아닙니다")
            continue
        given[name] = value if isinstance(value, dict) else {"items": value}
    summary = ["| 힘 | 강도 | 핵심 근거 | 출처·날짜 |", "|---|---|---|---|"]
    detail = ["| 힘 | 판단 | 근거(사실) | 출처·날짜 |", "|---|---|---|---|"]
    for name in FORCES:
        force = given.get(name) or {}
        if _no_data(force):
            force = {"rating": "판단 보류", "items": [force]}
        rows = ck.items(force.get("items"), name)
        rating = _text(force.get("rating")) or ("" if force else "판단 보류")
        sourced = [r for r in rows if r.get("sourced")]
        if rating not in RATINGS:
            ck.add("bad_rating", name, f"강도는 {'·'.join(RATINGS)} 중 하나여야 합니다", rating)
            rating = "판단 보류"
        elif rating != "판단 보류" and not sourced:
            ck.add(
                "rating_without_evidence",
                name,
                "출처 있는 근거 없이 강도를 매겼습니다 — 「판단 보류」로 두세요",
                rating,
            )
            rating = "판단 보류"
        top = sourced[0] if sourced else rows[0]
        summary.append(
            f"| {name} | {rating} | {_esc(top['evidence'] if sourced else top['claim'])} | {_esc(top['source'])} |"
        )
        for i, row in enumerate(rows):
            detail.append(
                f"| {name if i == 0 else ''} | {_esc(row['claim'])} | {_esc(row['evidence'])} | {_esc(row['source'])} |"
            )
    return "\n".join(summary) + "\n\n" + "\n".join(detail)


def segments(args: dict, ck: Checker) -> str:
    segs = args.get("segments") or []
    if not isinstance(segs, list) or not segs:
        ck.add("missing_factor", "segments", "세그먼트가 없습니다")
        return ""
    if not 3 <= len(segs) <= 5:
        ck.add(
            "segment_count",
            "segments",
            f"세그먼트가 {len(segs)}개입니다 — 보통 3~5개(자료가 받치는 만큼만)",
        )
    seen = set()
    overview = [
        "| 세그먼트 | 구분 기준 | 규모 | 규모 출처 | 핵심 니즈 | 현재 대안 |",
        "|---|---|---:|---|---|---|",
    ]
    details = []
    for index, seg in enumerate(segs):
        seg = seg if isinstance(seg, dict) else {"name": _text(seg)}
        name = _text(seg.get("name")) or f"세그먼트 {index + 1}"
        if name in seen:
            ck.add("duplicate_segment", name, "같은 이름의 세그먼트가 있습니다")
        seen.add(name)
        criteria = _text(seg.get("criteria"))
        if not criteria:
            ck.add(
                "no_criteria",
                name,
                "구분 기준(criteria)이 없습니다 — 누가 이 세그먼트에 드는지 판별할 수 있어야 합니다",
            )
        size = seg.get("size")
        if not size:
            ck.add("no_size", name, "규모가 없습니다 — 출처 있는 값이나 「자료 없음」")
            size_text, size_src = NO_DATA, "-"
        elif _no_data(size):
            to_find = _text(size.get("to_find"))
            if not to_find:
                ck.add(
                    "no_to_find", f"{name} 규모", "「자료 없음」에 확인할 곳(to_find)이 없습니다"
                )
            size_text, size_src = NO_DATA, f"확인할 자료: {to_find}" if to_find else "-"
        else:
            value = size.get("value")
            size_text = (
                f"{value:,}{_text(size.get('unit'))}"
                if isinstance(value, (int, float))
                else _text(value)
            )
            fake = {
                "claim": "규모",
                "evidence": size_text,
                **{k: size.get(k) for k in ("source", "date", "url", "assumption", "basis")},
            }
            row = ck.item(fake, f"{name} 규모")
            size_src = row["source"]
        needs = ck.items(seg.get("needs"), f"{name} 니즈")
        pains = ck.items(seg.get("pains"), f"{name} 불편")
        alts = ck.items(seg.get("alternatives"), f"{name} 현재 대안")
        overview.append(
            f"| {_esc(name)} | {_esc(criteria or '-')} | {_esc(size_text)} | {_esc(size_src)} | "
            f"{_esc(needs[0]['claim'])} | {_esc(alts[0]['claim'])} |"
        )
        block = [f"### {name}", f"- 구분 기준: {criteria or '-'}"]
        for label, rows in (("니즈", needs), ("불편", pains), ("현재 대안", alts)):
            for row in rows:
                if row.get("no_data"):
                    hint = row["evidence"] if row["evidence"] != "-" else ""
                    block.append(f"- {label}: {NO_DATA}" + (f" ({hint})" if hint else ""))
                else:
                    block.append(
                        f"- {label}: {row['claim']} — 근거: {row['evidence']} ({row['source']})"
                    )
        if _text(seg.get("fit")):
            block.append(f"- 제품 적합성: {_text(seg.get('fit'))}")
        if _text(seg.get("reach")):
            block.append(f"- 도달 경로: {_text(seg.get('reach'))}")
        details.append("\n".join(block))
    return "\n".join(overview) + "\n\n" + "\n\n".join(details)


RENDER = {"pestel": pestel, "five_forces": five_forces, "segments": segments}
KIND_NAMES = {
    "missing_factor": "빈 요인",
    "no_evidence": "근거 없음",
    "unsourced": "출처 없음",
    "assumption_without_basis": "근거 없는 가정",
    "generic": "일반론",
    "empty_claim": "빈 판단",
    "bad_rating": "잘못된 강도",
    "rating_without_evidence": "근거 없는 강도",
    "duplicate_segment": "중복 세그먼트",
    "no_criteria": "구분 기준 없음",
    "no_size": "규모 없음",
    "no_to_find": "확인할 곳 없음",
    "no_impact": "영향 없음",
    "stale": "오래된 출처",
    "source_without_date": "날짜 없는 출처",
    "segment_count": "세그먼트 수",
    "unknown_factor": "모르는 요인",
}
NAMES = {"pestel": "PESTEL", "five_forces": "5 Forces", "segments": "시장 세분화"}


def run(args: dict) -> dict:
    if args.get("template"):
        key = _text(args["template"]).lower().replace("-", "_").replace(" ", "_")
        if key not in TEMPLATES:
            return {"ok": False, "error": f"틀은 {', '.join(TEMPLATES)} 중 하나입니다"}
        return {"ok": True, "template": TEMPLATES[key], "rules": RULES}
    key = _text(args.get("framework")).lower().replace("-", "_").replace(" ", "_")
    key = {
        "porter": "five_forces",
        "porters_five_forces": "five_forces",
        "5forces": "five_forces",
        "pestle": "pestel",
        "pestel_analysis": "pestel",
        "market_segments": "segments",
    }.get(key, key)
    ck = Checker(args)
    if key not in RENDER:
        ck.add("unknown_framework", key, "framework 는 pestel·five_forces·segments 중 하나입니다")
        return {
            "ok": False,
            "findings": ck.findings,
            "markdown": "",
            "summary": "틀을 알 수 없습니다",
        }
    markdown = RENDER[key](args, ck)
    blocking = [f for f in ck.findings if f["kind"] in BLOCKING]
    counts: dict[str, int] = {}
    for f in ck.findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    summary = f"{NAMES[key]} 점검: " + (
        " · ".join(f"{KIND_NAMES.get(k, k)} {v}" for k, v in counts.items()) or "문제 없음"
    )
    return {
        "ok": not blocking,
        "framework": key,
        "markdown": markdown,
        "findings": ck.findings,
        "summary": summary,
    }


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else {}
        result = run(options if isinstance(options, dict) else {})
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    if result.get("markdown"):
        print(result["markdown"])
        print()
    if result.get("summary"):
        print(result["summary"])
    for f in result.get("findings") or []:
        print(
            f"[{f['kind']}] {f['where']}: {f['why']}" + (f" — {f['text']}" if f.get("text") else "")
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
