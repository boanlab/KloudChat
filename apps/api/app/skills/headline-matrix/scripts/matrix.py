"""헤드라인을 각도(호기심·구체성·증거·긴급성·역발상·작동 원리·정체성)별 칸으로 나누고, 쓴 헤드라인의 글자 수·중복·같은 꼴 반복·칸 채움을 잽니다.

인자(JSON 하나):
  칸 만들기   {"count": 25, "angles": ["호기심", ...](선택), "facts": ["자료의 사실", ...](선택),
               "limit": 15, "unit": "chars"}
  점검        위 인자 + "headlines": [{"angle": "호기심", "text": "..."}, ...]
세는 법(unit): chars(공백 포함, 이모지 한 덩이 1자) · chars_dw(한글·한자·전각 2자, Google Ads 방식)
             · chars_no_spaces · bytes_utf8.
칸 만들기: 각도마다 몇 개를 쓸지(quota)와 정의·꼴 힌트를 주고, facts 가 없으면 증거·구체성·긴급성 칸은
  needs_fact=true 로 표시한다(자료에 없는 사실로 채우지 말 것).
점검: 한도 초과, 띄어쓰기·문장부호만 다른 중복, 글자 2-gram 유사도 similarity(기본 0.6) 이상인 거의 중복,
  같은 첫머리·같은 끝맺음이 전체의 40%를 넘는 반복, 각도별 개수 부족을 찾고 각도별 표(markdown)를 낸다.
마지막 줄에 요약 JSON을 냅니다. 파일은 만들지 않습니다.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter

ANGLES: dict[str, tuple[str, str, bool]] = {
    # name: (what it does, a shape to start from, needs a fact from the material)
    "호기심": ("답을 바로 주지 않고 궁금하게 한다. 랜딩 페이지가 답을 줘야 한다(낚시 금지).",
            "「~하는 팀이 먼저 바꾼 것」「~가 늦어지는 진짜 이유」", False),
    "구체성": ("자료의 숫자·대상·기간을 그대로 넣는다.", "「[대상]의 [업무]를 [기간]에」", True),
    "증거": ("자료에 있는 성과·고객 수·수상·인증만 쓴다.", "「[고객 수]곳이 쓰는」「[인증명] 획득」", True),
    "긴급성": ("자료에 있는 실제 마감·한정 조건만 쓴다. 없으면 칸을 비운다.", "「[마감일]까지 [혜택]」", True),
    "역발상": ("통념을 뒤집되 자료로 뒷받침되는 말만 한다.", "「~부터 바꾸지 마세요」「~보다 먼저 볼 것」", False),
    "작동 원리": ("어떻게 되는지 방법·구조를 보여 준다.", "「[입력]만 넣으면 [결과]」", False),
    "정체성": ("누구를 위한 것인지 부른다.", "「[직무·상황]을 위한 [제품 범주]」", False),
}
UNITS = ("chars", "chars_dw", "chars_no_spaces", "bytes_utf8")
_JOINERS = {0x200D, 0xFE0E, 0xFE0F}


def _wide(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F")


def count(text: str, unit: str = "chars") -> int:
    text = text.replace("\r\n", "\n")
    if unit == "bytes_utf8":
        return len(text.encode("utf-8"))
    total, joined = 0, False
    for ch in text:
        code = ord(ch)
        if code in _JOINERS or unicodedata.combining(ch) or 0x1F3FB <= code <= 0x1F3FF:
            joined = code == 0x200D
            continue
        if joined:
            joined = False
            continue
        if unit == "chars_no_spaces" and ch.isspace():
            continue
        total += 2 if unit == "chars_dw" and _wide(ch) else 1
    return total


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text.lower())


def _bigrams(text: str) -> set[str]:
    norm = _norm(text)
    return {norm[i:i + 2] for i in range(len(norm) - 1)} or {norm}


def similarity(a: str, b: str) -> float:
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / len(x | y) if x | y else 0.0


def quotas(count_: int, angles: list[str]) -> dict[str, int]:
    base, extra = divmod(count_, len(angles))
    return {angle: base + (1 if n < extra else 0) for n, angle in enumerate(angles)}


def scaffold(options: dict) -> dict:
    angles = [str(a) for a in options.get("angles") or ANGLES]
    total = int(options.get("count") or 25)
    if not 1 <= total <= 100:
        raise ValueError("count 는 1~100")
    facts = [str(f) for f in options.get("facts") or [] if str(f).strip()]
    plan = []
    for angle, quota in quotas(total, angles).items():
        what, shape, needs_fact = ANGLES.get(angle, ("사용자가 정한 각도", "", False))
        plan.append({"angle": angle, "quota": quota, "what": what, "shape": shape,
                     "needs_fact": needs_fact and not facts})
    return {"count": total, "angles": plan, "facts": facts,
            "limit": options.get("limit"), "unit": options.get("unit") or "chars"}


def check(options: dict) -> dict:
    plan = scaffold(options)
    unit = str(options.get("unit") or "chars")
    if unit not in UNITS:
        raise ValueError(f"unit 은 {', '.join(UNITS)} 가운데 하나")
    limit = options.get("limit")
    limit = int(limit) if limit not in (None, "") else None
    threshold = float(options.get("similarity") or 0.6)
    rows = []
    for n, item in enumerate(options.get("headlines") or [], start=1):
        item = item if isinstance(item, dict) else {"text": str(item)}
        text = " ".join(str(item.get("text") or "").split())
        length = count(text, unit)
        rows.append({"n": n, "angle": str(item.get("angle") or "미지정"), "text": text, "length": length,
                     "over": max(0, length - limit) if limit else 0})
    duplicates, near = [], []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if _norm(a["text"]) == _norm(b["text"]):
                duplicates.append([a["n"], b["n"]])
            elif (score := similarity(a["text"], b["text"])) >= threshold:
                near.append({"pair": [a["n"], b["n"]], "similarity": round(score, 2)})
    heads = Counter(_norm(r["text"])[:3] for r in rows if len(_norm(r["text"])) >= 3)
    tails = Counter(_norm(r["text"])[-3:] for r in rows if len(_norm(r["text"])) >= 3)
    share = 0.4
    repeats = [{"where": "첫머리", "pattern": p, "count": c} for p, c in heads.items() if rows and c / len(rows) > share and c > 2]
    repeats += [{"where": "끝맺음", "pattern": p, "count": c} for p, c in tails.items() if rows and c / len(rows) > share and c > 2]
    have = Counter(r["angle"] for r in rows)
    short = [{"angle": a["angle"], "quota": a["quota"], "have": have.get(a["angle"], 0)}
             for a in plan["angles"] if have.get(a["angle"], 0) < a["quota"]]
    table = ["| 각도 | # | 헤드라인 | 글자 수 |", "|---|---|---|---|"]
    order = [a["angle"] for a in plan["angles"]]
    for row in sorted(rows, key=lambda r: (order.index(r["angle"]) if r["angle"] in order else 99, r["n"])):
        mark = f" (+{row['over']} 초과)" if row["over"] else ""
        table.append(f"| {row['angle']} | {row['n']} | {row['text'].replace('|', '／')} | {row['length']}{mark} |")
    return {"headlines": rows, "count": len(rows), "target": plan["count"], "limit": limit, "unit": unit,
            "over": [r["n"] for r in rows if r["over"]], "duplicates": duplicates, "near_duplicates": near,
            "repeated_shapes": repeats, "short_angles": short, "table_markdown": "\n".join(table)}


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    if not isinstance(options, dict):
        options = {}
    try:
        if options.get("headlines"):
            result = check(options)
            print(result["table_markdown"])
            clean = not (result["over"] or result["duplicates"] or result["near_duplicates"] or result["repeated_shapes"])
            result["clean"] = clean
        else:
            result = scaffold(options)
            for row in result["angles"]:
                print(f"{row['angle']} ×{row['quota']}" + (" (자료의 사실 필요)" if row["needs_fact"] else ""))
    except (TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
