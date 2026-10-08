"""광고·홍보 문구에서 근거가 필요한 표현(최상급·비교·보장·의약 효능·후기·긴급성)과 자료에 없는 숫자를 찾습니다.

인자(JSON 하나):
  {"items": [{"label": "A안 제목", "text": "..."}, ...],      # 또는 "text": "..." 하나
   "material": "사용자가 준 자료 원문(선택)",                 # 또는 "material_file": "첨부 이름.txt"
   "category": "general" | "food" | "health_food" | "cosmetics" | "medical_device"}
찾는 것:
  superlative   최고·최초·유일·1위·No.1·최저가·가장 ~ 처럼 객관적 근거가 있어야 하는 표현
  comparison    타사·경쟁사·「~보다 저렴」·「대비 2배」 같은 비교 표현
  guarantee     보장·무조건·100%·부작용 없음 같은 단정
  medical       치료·예방·완치·면역력·혈당·체지방 감소 같은 질병·효능 표현(식품·화장품이면 금지에 가깝다)
  testimonial   인용 후기·「실제 고객」·만족도 수치
  urgency       오늘만·마감 임박·선착순·한정 수량
  offer         무료 체험·상담 예약·데모·환불 보장·사은품처럼 자료에 없는 혜택·서비스
  number        자료에 없는 숫자·금액·비율·날짜
  placeholder   [제품명] 같은 빈칸(오류 아님, 채울 목록)
  style         「단순한 ~가 아닙니다」·혁신적·게임 체인저·줄표(—)·느낌표 겹침 같은 상투 표현
같은 표현이 자료에 있으면 grounded=true 로 표시하고 심각도를 낮춥니다(근거 출처는 여전히 밝혀야 함).
심각도: block(근거·자료 없이 내보내면 안 됨) · check(사람이 확인) · info.
마지막 줄에 요약 JSON을 냅니다. 파일은 만들지 않습니다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CATEGORIES = ("general", "food", "health_food", "cosmetics", "medical_device")

#: (kind, pattern, why, fix). Patterns are matched on the copy as written.
RULES: list[tuple[str, str, str, str]] = [
    ("superlative",
     r"(?:국내|업계|세계|국내외|아시아|전국)\s*(?:최초|최고|유일|최대|1위|최다|최저)"
     r"|최고(?!\s*(?:경영|기술|재무|운영|책임|마케팅|정보|보안|인사)(?:자|책임자|임원)?)"
     r"|최상(?:급|의)?|최강|최다|최저가?|최초(?!\s*(?:1회|가입|구매|등록|결제|주문|로그인|접속))"
     r"|유일(?:한|무이)?|독보적|압도적|타의\s*추종|넘버\s*원|No\.?\s*1|NO\.?\s*1|[1１]\s*위|[1１]\s*등(?!급)"
     r"|가장\s+[가-힣]+[한은는운]\b|완벽(?:한|하게|히)?|단연",
     "최상급·순위 표현은 객관적 근거(조사기관·기간·대상)가 있어야 한다(표시광고법, 부당 표시·광고 고시).",
     "자료에 근거가 없으면 빼고 구체적 사실로 바꾸거나, 근거가 있으면 출처를 함께 적는다: 「[근거: 조사기관·기간]」."),
    ("comparison",
     r"타사|경쟁사|경쟁\s*제품|기존\s*제품\s*대비|업계\s*평균|시중\s*제품"
     r"|대비\s*\d+(?:[.,]\d+)?\s*(?:배|%|퍼센트)"
     r"|[가-힣A-Za-z0-9]+(?:보다|에\s*비해)\s*(?:더\s*)?(?:저렴|싸|빠르|빠른|좋|우수|뛰어|강력|낫|많|가볍|오래)",
     "비교 광고는 같은 기준·객관적 자료로 비교하고 비교 대상·기준을 밝혀야 한다(비교표시·광고 심사지침).",
     "자료에 비교 근거가 없으면 비교를 빼고 자사 사실만 쓴다."),
    ("guarantee",
     r"보장(?:합니다|해\s*드|된|됩니다)?|무조건|반드시\s+[가-힣]+(?:됩|집|합)니다|절대(?:로)?\s|100\s*%|백\s*퍼센트"
     r"|부작용\s*(?:없|제로|0)|무해|리스크\s*(?:없|제로)|평생\s*(?:무료|보증|보장)|영구(?:적)?",
     "효과·결과를 단정하거나 보장하는 표현은 실증 자료가 없으면 기만·과장 광고가 될 수 있다.",
     "자료에 있는 보장 조건(기간·범위·방법)만 그대로 옮기고, 없으면 단정을 뺀다."),
    ("medical",
     r"치료|완치|예방|특효|만병|약효|처방|질병|질환|염증|혈압|혈당|콜레스테롤|항암|(?<![가-힣])암(?:\s*예방|\s*세포)"
     r"|면역력|해독|디톡스|독소\s*배출|지방\s*(?:분해|연소|감소)|체지방|체중\s*감량|살\s*(?:이\s*)?빠|다이어트\s*효과"
     r"|노화\s*(?:방지|억제)|주름\s*(?:제거|개선)|탈모\s*(?:치료|방지|예방)|발모|미백\s*효과|의사\s*추천|약사\s*추천"
     r"|식약처\s*(?:인증|승인|인정)|FDA\s*(?:인증|승인)",
     "질병의 예방·치료 효능이나 의약품으로 오인될 표현은 식품·건강기능식품·화장품 광고에서 금지되거나 심의 대상이다.",
     "건강기능식품은 식약처가 인정한 기능성 문구(「~에 도움을 줄 수 있음」)를 자료에서 그대로 옮기고, 그 밖은 뺀다."),
    ("testimonial",
     r"[\"“][^\"”\n]{4,120}[\"”]\s*(?:[-–—]\s*|[(（]\s*)?[가-힣A-Za-z]{1,4}(?:○○|OO|\*\*|님|\s*고객|\s*대표|\s*팀장|\s*과장|\s*씨)"
     r"|실제\s*(?:고객|사용자|이용자)|고객\s*후기|사용\s*후기|만족도\s*\d+(?:\.\d+)?\s*%|재구매율\s*\d+",
     "후기·추천·만족도는 실제로 받은 것만 쓸 수 있다(추천·보증 심사지침). 지어낸 후기는 기만 광고다.",
     "자료에 있는 후기만 원문 그대로 옮기고, 없으면 「[고객 사례]」 빈칸으로 둔다."),
    ("urgency",
     r"오늘만|지금만|단\s*\d+\s*(?:일|시간)|마감\s*임박|품절\s*임박|선착순|한정\s*(?:수량|판매|기간)|마지막\s*기회|곧\s*종료|놓치면\s*후회",
     "기간·수량 한정은 실제 조건일 때만 쓸 수 있다. 없는 긴급성은 기만 광고다.",
     "자료의 실제 기간·수량을 그대로 적거나 뺀다."),
    ("offer",
     r"무료\s*(?:체험|상담|진단|컨설팅|배송|설치|시범|샘플|데모)|체험판|\d+\s*일\s*(?:무료|체험)|(?:도입|구매|무료)?\s*상담\s*(?:예약|신청|받기)"
     r"|데모\s*(?:신청|예약|요청|보기)|환불\s*(?:보장|보증|가능)|교환\s*(?:보장|가능)|설치\s*지원|1\s*\+\s*1|사은품|쿠폰",
     "자료에 없는 혜택·서비스(무료 체험·상담·데모·환불·사은품)를 약속하면 기만 광고가 되고 회사가 지킬 수 없다.",
     "자료에 있는 혜택만 쓴다. 없으면 실제로 할 수 있는 행동(가입·문의·자료 받기)으로 CTA를 바꾸거나 「[혜택]」으로 비운다."),
    ("style",
     r"단순한\s*[가-힣A-Za-z ]{1,20}(?:이|가)\s*아닙니다|[가-힣]+(?:이|가)\s*아닌,\s|그\s*이상의|혁신적(?:인)?|획기적(?:인)?"
     r"|차원이\s*다른|새로운\s*패러다임|게임\s*체인저|한\s*단계\s*업그레이드|지금\s*바로\s*경험하세요"
     r"|더\s*이상\s*[가-힣 ]{1,12}(?:하지\s*마세요|필요\s*없습니다)|—|!{2,}|\?{2,}",
     "AI 투나 상투 표현은 광고를 흔하게 만들고 신뢰를 떨어뜨린다.",
     "구체적인 사실(무엇을, 얼마나, 누구에게)로 바꾼다. 줄표는 마침표나 쉼표로."),
]

#: Why and how to fix, once per kind in the summary.
GUIDE: dict[str, dict[str, str]] = {kind: {"why": why, "fix": fix} for kind, _, why, fix in RULES} | {
    "number": {"why": "자료에 없는 숫자다(check 는 값은 있으나 단위가 다름). 지어낸 수치는 기만 광고가 된다.",
               "fix": "자료의 수치로 바꾸거나 「[수치]」 빈칸으로 둔다."},
    "placeholder": {"why": "채워야 할 빈칸", "fix": "내보내기 전에 실제 값으로 채운다."},
}

#: Kinds that need a source in the material before the copy can go out.
_BLOCK_UNLESS_GROUNDED = {"superlative", "comparison", "testimonial", "guarantee", "offer"}

_PLACEHOLDER = re.compile(r"\[[^\[\]\n]{1,30}\]|［[^［］\n]{1,30}］")
_NUMBER = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(만|천|억|백)?\s*"
    r"(%|퍼센트|배|원|명|개|곳|건|회|분|초|시간|일|주|개월|년|월|점|시|위|kg|g|ml|mL|L|km|m|GB|TB|MB|단계|가지|종)?"
)


#: Numbers written in words: 「한두 시간」「세 배」「수천 명」.
_WORD_NUMBER = re.compile(
    r"(?<![가-힣])(?:한두|두세|서너|너덧|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|수십|수백|수천|수만|몇십|몇백|몇천)"
    r"\s*(?:시간|분(?![야석기])|배(?![송달경])|명|곳|개사|개\s*기업|기업|주(?![문소요제])|달|개월|년)"
)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _value(digits: str, scale: str | None) -> float:
    value = float(digits.replace(",", ""))
    return value * {"만": 1e4, "천": 1e3, "억": 1e8, "백": 1e2}.get(scale or "", 1)


def numbers(text: str) -> list[dict]:
    """Every number in `text` with its scaled value and unit."""
    found = []
    for match in _NUMBER.finditer(text or ""):
        digits, scale, unit = match.group(1), match.group(2), match.group(3) or ""
        found.append({"text": match.group(0).strip(), "value": _value(digits, scale), "unit": unit})
    return found


#: Counting words: often a count of things the material lists (「연동 채널 4개」), so a person checks.
_COUNT_UNITS = {"개", "가지", "단계", "종", "곳"}


def _numbers_at(text: str) -> list[tuple[dict, int]]:
    found = []
    for match in _NUMBER.finditer(text or ""):
        digits, scale, unit = match.group(1), match.group(2), match.group(3) or ""
        found.append(({"text": match.group(0).strip(), "value": _value(digits, scale), "unit": unit}, match.start()))
    return found


def _is_list_marker(text: str, start: int) -> bool:
    """`1.` `2)` `3단계` at a line start, or `1/8` page counters, are numbering, not claims."""
    line_start = text.rfind("\n", 0, start) + 1
    before = text[line_start:start]
    after = text[start:start + 6]
    if before.strip(" \t-*#>|") == "" and re.match(r"\d{1,2}\s*(?:[.)．]|단계|장|번|/)", after):
        return True
    return bool(re.match(r"\d{1,2}\s*/\s*\d{1,2}(?!\d)", after))


#: 「20분 통화」「30분 데모」: the length of a meeting the copy proposes, not a claim.
_PROPOSAL = re.compile(r"\s*(?:정도|가량|남짓)?\s*(?:의\s*)?(?:짧은\s*)?(?:통화|미팅|대화|화상|회의|데모|상담|시간을?\s*내)")


def _clock_hours(material: str) -> set[tuple[float, str]]:
    """「10:00~20:00」 in the material also grounds 「10시」, 「오후 8시」."""
    hours = set()
    for match in re.finditer(r"(?<!\d)([01]?\d|2[0-3])\s*:\s*[0-5]\d", material or ""):
        hour = int(match.group(1))
        hours.add((float(hour), "시"))
        if hour > 12:
            hours.add((float(hour - 12), "시"))
    return hours


def _tokens_in(phrase: str, squashed: str) -> bool:
    """Every word and number of `phrase` occurs in the material, in any order (「14일 무료」 ~ 「무료 체험 14일」)."""
    words = re.findall(r"[가-힣]{2,}|\d+", phrase)
    return bool(words) and all(word in squashed for word in words)


def _in_placeholder(text: str, start: int, end: int) -> bool:
    return any(m.start() <= start and end <= m.end() for m in _PLACEHOLDER.finditer(text))


def scan(text: str, material: str = "", category: str = "general") -> list[dict]:
    if category not in CATEGORIES:
        raise ValueError(f"category 는 {', '.join(CATEGORIES)} 가운데 하나")
    squashed = _squash(material)
    findings: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for kind, pattern, _why, _fix in RULES:
        for match in re.finditer(pattern, text):
            phrase = match.group(0).strip()
            if not phrase or (kind, phrase) in seen or _in_placeholder(text, match.start(), match.end()):
                continue
            seen.add((kind, phrase))
            grounded = bool(squashed) and (_squash(phrase) in squashed or (
                kind in ("offer", "urgency") and _tokens_in(phrase, squashed)))
            if kind == "style":
                severity = "info"
            elif kind == "medical":
                strict = category in ("food", "health_food", "cosmetics")
                severity = "block" if strict and not grounded else "check"
            elif kind in _BLOCK_UNLESS_GROUNDED:
                severity = "check" if grounded else "block"
            else:
                severity = "info" if grounded else "check"
            findings.append({"kind": kind, "match": phrase, "grounded": grounded, "severity": severity})
    if material:
        have = {(round(n["value"], 4), n["unit"]) for n in numbers(material)} | _clock_hours(material)
        values = {round(n["value"], 4) for n in numbers(material)} | {v for v, _ in _clock_hours(material)}
        for number, start in _numbers_at(text):
            end = start + len(number["text"])
            if _in_placeholder(text, start, end) or _is_list_marker(text, start) or _PROPOSAL.match(text, end):
                continue
            key = (round(number["value"], 4), number["unit"])
            if key in have or (not number["unit"] and key[0] in values):
                continue
            if ("number", number["text"]) in seen:
                continue
            seen.add(("number", number["text"]))
            loose = key[0] in values or number["unit"] in _COUNT_UNITS or not number["unit"]
            findings.append({"kind": "number", "match": number["text"], "grounded": False,
                             "severity": "check" if loose else "block"})
    if material:
        for match in _WORD_NUMBER.finditer(text):
            phrase = match.group(0)
            if ("number", phrase) in seen or _squash(phrase) in squashed:
                continue
            seen.add(("number", phrase))
            findings.append({"kind": "number", "match": phrase, "grounded": False, "severity": "block"})
    for match in _PLACEHOLDER.finditer(text):
        findings.append({"kind": "placeholder", "match": match.group(0), "grounded": False,
                         "severity": "info"})
    return findings


def _material(options: dict) -> str:
    text = str(options.get("material") or "")
    name = options.get("material_file")
    if name:
        path = Path(str(name))
        if not path.is_file():
            raise ValueError(f"자료 파일을 찾지 못했습니다: {name}")
        text += "\n" + path.read_text(encoding="utf-8", errors="replace")
    return text


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    if not isinstance(options, dict):
        options = {}
    items = options.get("items")
    if not items and options.get("text"):
        items = [{"label": "문구", "text": options["text"]}]
    if not isinstance(items, list) or not items:
        print(json.dumps({"ok": False, "error": "items 또는 text 가 필요합니다"}, ensure_ascii=False))
        return 1
    try:
        material = _material(options)
        category = str(options.get("category") or "general")
        rows = []
        for n, item in enumerate(items[:200], start=1):
            item = item if isinstance(item, dict) else {"text": str(item)}
            label = str(item.get("label") or f"문구 {n}")
            rows.append({"label": label, "findings": scan(str(item.get("text") or ""), material, category)})
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    counts = {"block": 0, "check": 0, "info": 0}
    for row in rows:
        marks = [f"{f['severity']}:{f['kind']}「{f['match']}」" for f in row["findings"] if f["kind"] != "placeholder"]
        for f in row["findings"]:
            counts[f["severity"]] += 1
        print(f"{row['label']}: " + (", ".join(marks) if marks else "문제 표현 없음"))
    kinds = sorted({f["kind"] for r in rows for f in r["findings"]})
    summary = {"ok": True, "material_given": bool(material.strip()), "items": rows, **counts,
               "guide": {kind: GUIDE[kind] for kind in kinds},
               "placeholders": sorted({f["match"] for r in rows for f in r["findings"] if f["kind"] == "placeholder"})}
    if not material.strip():
        summary["note"] = "자료를 주지 않아 숫자 대조를 하지 않았습니다. material 을 넘기면 자료에 없는 수치를 찾습니다."
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
