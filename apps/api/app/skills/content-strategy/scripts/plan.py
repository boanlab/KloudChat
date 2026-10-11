"""콘텐츠 주제를 네 기준(고객 영향 40·제품 적합 30·검색 잠재력 20·제작 부담 10)으로 점수 매겨 순위를 내고, 검색형·공유형·실험 비율과 날짜를 맞춘 발행 일정을 짭니다."""

from __future__ import annotations

import datetime as dt
import json
import math
import sys

CRITERIA = ("impact", "fit", "search", "resources")
LABELS = {"impact": "고객 영향", "fit": "제품 적합", "search": "검색 잠재력", "resources": "제작 쉬움"}
TYPES = ("searchable", "shareable", "experimental")
TYPE_LABELS = {"searchable": "검색형", "shareable": "공유형", "experimental": "실험"}
WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}


def _hundred(values: dict, keys: tuple[str, ...], name: str) -> dict[str, float]:
    out = {k: float(values.get(k, 0) or 0) for k in keys}
    if any(v < 0 for v in out.values()) or not math.isclose(sum(out.values()), 100, abs_tol=0.01):
        raise ValueError(f"{name} 는 0 이상이고 합이 100이어야 합니다")
    return out


def score(topic: dict, weights: dict[str, float]) -> dict:
    scores = topic.get("scores") or {}
    basis = topic.get("basis") or {}
    used, total, missing, assumed = 0.0, 0.0, [], []
    for key in CRITERIA:
        value = scores.get(key)
        if value in (None, ""):
            missing.append(key)
            continue
        value = float(value)
        if not 1 <= value <= 10:
            raise ValueError(f"「{topic.get('title')}」 {key} 점수는 1~10")
        used += weights[key]
        total += value * weights[key]
        if not str(basis.get(key) or "").strip():
            assumed.append(key)
    return {
        "title": str(topic.get("title") or "").strip(),
        "pillar": str(topic.get("pillar") or "").strip(),
        "type": topic.get("type") if topic.get("type") in TYPES else "searchable",
        "score": round(total / used, 2) if used else None,
        "missing": missing,
        "assumed": assumed,
    }


def slots(mix: dict[str, float], n: int) -> list[str]:
    """`n` slot types in the mix's proportions, spread out (largest remainder, interleaved)."""
    exact = {k: mix[k] * n / 100 for k in TYPES}
    counts = {k: math.floor(v) for k, v in exact.items()}
    for key in sorted(TYPES, key=lambda k: exact[k] - counts[k], reverse=True)[: n - sum(counts.values())]:
        counts[key] += 1
    order, placed = [], dict.fromkeys(TYPES, 0)
    for i in range(n):
        # The type furthest behind its share so far goes next.
        key = max(TYPES, key=lambda k: (counts[k] * (i + 1) / n - placed[k]) if placed[k] < counts[k] else -1e9)
        order.append(key)
        placed[key] += 1
    return order


def calendar(ranked: list[dict], mix: dict[str, float], spec: dict) -> list[dict]:
    start = dt.date.fromisoformat(str(spec["start"]))
    weeks = int(spec.get("weeks") or 4)
    per_week = int(spec.get("per_week") or 1)
    days = [WEEKDAYS[d] for d in (spec.get("weekdays") or ["화", "목", "월", "수", "금"])[:per_week]]
    if len(days) < per_week:
        raise ValueError("weekdays 가 per_week 보다 적습니다")
    monday = start - dt.timedelta(days=start.weekday())
    dates = sorted(
        d for w in range(weeks) for wd in days
        if (d := monday + dt.timedelta(weeks=w, days=wd)) >= start
    )
    pools = {k: [t for t in ranked if t["type"] == k] for k in TYPES}
    rows = []
    for date, kind in zip(dates, slots(mix, len(dates)), strict=True):
        topic = pools[kind].pop(0) if pools[kind] else None
        rows.append({"date": date.isoformat(), "weekday": "월화수목금토일"[date.weekday()], "type": kind,
                     "title": topic["title"] if topic else f"[{TYPE_LABELS[kind]} 주제 필요]",
                     "pillar": topic["pillar"] if topic else ""})
    return rows


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    if not isinstance(options, dict):
        options = {}
    topics = options.get("topics")
    if not isinstance(topics, list) or not topics:
        print(json.dumps({"ok": False, "error": "topics 목록이 필요합니다"}, ensure_ascii=False))
        return 1
    try:
        weights = _hundred(options.get("weights") or {"impact": 40, "fit": 30, "search": 20, "resources": 10},
                           CRITERIA, "weights")
        mix = _hundred(options.get("mix") or {"searchable": 60, "shareable": 30, "experimental": 10}, TYPES, "mix")
        rows = [score(t, weights) for t in topics[:100] if isinstance(t, dict)]
        ranked = sorted(rows, key=lambda r: (r["score"] is None, -(r["score"] or 0), r["title"]))
        schedule = calendar(ranked, mix, options["calendar"]) if options.get("calendar") else []
    except (KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    table = ["| 순위 | 주제 | 축 | 유형 | 점수 | 근거 없는 기준 |", "|---|---|---|---|---|---|"]
    for n, row in enumerate(ranked, start=1):
        unsure = ", ".join(LABELS[k] for k in row["assumed"]) + (
            (" / 빈 기준: " + ", ".join(LABELS[k] for k in row["missing"])) if row["missing"] else "")
        table.append(f"| {n} | {row['title']} | {row['pillar']} | {TYPE_LABELS[row['type']]} | "
                     f"{'' if row['score'] is None else row['score']} | {unsure} |")
    print("\n".join(table))
    pillars: dict[str, int] = {}
    for row in ranked:
        pillars[row["pillar"] or "(축 없음)"] = pillars.get(row["pillar"] or "(축 없음)", 0) + 1
    short = {k: sum(1 for r in schedule if r["type"] == k and r["title"].startswith("[")) for k in TYPES}
    print(json.dumps({"ok": True, "ranked": ranked, "weights": weights, "mix": mix, "pillars": pillars,
                      "calendar": schedule, "unfilled_slots": short, "table_markdown": "\n".join(table)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
