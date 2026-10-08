"""자기소개서 답의 글자 수·바이트를 지원 사이트의 세는 법대로 세고 한도·하한과 견줍니다.

인자(JSON 하나): {"items": [
  {"label": "문항 1", "text": "...", "limit": 700, "unit": "chars_with_spaces"},
  {"label": "문항 2", "text": "...", "limit": 600, "unit": "chars_no_spaces", "min_ratio": 0.9},
  {"label": "문항 3", "text": "...", "limit": 1500, "unit": "bytes_utf8", "newline": 1},
  {"label": "문항 4", "text": "...", "limit": 1000, "unit": "bytes_ko2", "newline": 2}
]}
세는 법:
  chars_with_spaces  공백 포함 글자 수 = len(text). 줄바꿈은 1자("newline": 2 면 2자).
  chars_no_spaces    공백·줄바꿈·탭을 뺀 글자 수.
  bytes_utf8         UTF-8 실제 바이트(한글 3, ASCII 1, ‘·’ 2, ‘→’ 3). 줄바꿈은 "newline" 바이트(기본 2).
  bytes_ko2          한글·한자·전각 문자 2바이트, 그 밖 1바이트. 줄바꿈은 "newline" 바이트(기본 2).
줄바꿈은 \\r\\n 을 하나로 셉니다. "skip_first_line": true 면 첫 줄(문항 제목)을 세지 않습니다.
하한은 limit × min_ratio(기본 0.8)를 올림한 값입니다. "min" 을 주면 그 값을 씁니다.
결과는 문항마다 한 줄을 출력하고 마지막 줄에 요약 JSON을 냅니다. 파일은 만들지 않습니다.
"""

from __future__ import annotations

import json
import math
import re
import sys

UNITS = ("chars_with_spaces", "chars_no_spaces", "bytes_utf8", "bytes_ko2")
LABELS = {"chars_with_spaces": ("공백 포함", "자"), "chars_no_spaces": ("공백 제외", "자"),
          "bytes_utf8": ("UTF-8", "바이트"), "bytes_ko2": ("한글 2바이트", "바이트")}


def _wide(ch: str) -> bool:
    code = ord(ch)
    return (
        0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F
        or 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF or 0xF900 <= code <= 0xFAFF
        or 0xFF00 <= code <= 0xFFEF or 0x3000 <= code <= 0x303F
    )


def normalise(text: str, skip_first_line: bool = False) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if skip_first_line and "\n" in text:
        text = text.split("\n", 1)[1]
    return text


def measure(text: str, unit: str = "chars_with_spaces", newline: int | None = None) -> int:
    """The count a submission system makes of `text` (see the module text)."""
    if unit not in UNITS:
        raise ValueError(f"모르는 단위: {unit}")
    text = normalise(text)
    breaks = text.count("\n")
    if unit == "chars_with_spaces":
        return len(text) + breaks * ((newline or 1) - 1)
    if unit == "chars_no_spaces":
        return len(re.sub(r"\s", "", text))
    per_break = 2 if newline is None else int(newline)
    body = text.replace("\n", "")
    if unit == "bytes_utf8":
        size = len(body.encode("utf-8"))
    else:
        size = sum(2 if _wide(ch) else 1 for ch in body)
    return size + breaks * per_break


def fit(item: dict) -> dict:
    unit = str(item.get("unit") or "chars_with_spaces")
    newline = item.get("newline")
    text = normalise(item.get("text", ""), bool(item.get("skip_first_line")))
    value = measure(text, unit, int(newline) if newline is not None else None)
    row = {"label": str(item.get("label") or ""), "unit": unit, "value": value,
           "counted_as": LABELS[unit][0], "suffix": LABELS[unit][1]}
    if newline is not None:
        row["newline"] = int(newline)
    elif unit.startswith("bytes"):
        row["newline"] = 2
    if item.get("limit") is not None:
        limit = int(item["limit"])
        if item.get("min") is not None:
            low = int(item["min"])
        else:
            ratio = float(item.get("min_ratio", 0.8))
            low = math.ceil(limit * ratio - 1e-9)
        row.update({
            "limit": limit, "min": low, "percent": round(value / limit * 100, 1) if limit else None,
            "over": max(0, value - limit), "under": max(0, low - value),
            "status": "over" if value > limit else "under" if value < low else "ok",
        })
    return row


def shown(row: dict) -> str:
    """「공백 포함 692자 / 700자」."""
    text = f"{row['counted_as']} {row['value']:,}{row['suffix']}"
    if "limit" in row:
        text += f" / {row['limit']:,}{row['suffix']}"
    return text


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    items = options.get("items") if isinstance(options, dict) else None
    if not isinstance(items, list) or not items:
        print(json.dumps({"ok": False, "error": "items 목록이 필요합니다"}, ensure_ascii=False))
        return 1
    try:
        rows = [fit(item) for item in items[:50]]
    except (ValueError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    for row in rows:
        status = {"ok": "맞음", "over": f"{row.get('over', 0):,} 초과", "under": f"{row.get('under', 0):,} 모자람"}
        print(f"{row['label']}: {shown(row)}" + (f" — {status[row['status']]}" if "status" in row else ""))
    summary = {"ok": True, "items": rows,
               "over": sum(r.get("status") == "over" for r in rows),
               "under": sum(r.get("status") == "under" for r in rows)}
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
