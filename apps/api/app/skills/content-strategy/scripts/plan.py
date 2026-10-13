"""콘텐츠 주제를 네 기준(고객 영향 40·제품 적합 30·검색 잠재력 20·제작 부담 10)으로 점수 매겨 순위를 내고, 검색형·공유형·실험 비율과 날짜를 맞춘 발행 일정을 짭니다. 채널을 주면 채널마다 그 빈도로 짭니다."""

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
        "channel": str(topic.get("channel") or "").strip(),
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


def _dates(start: dt.date, weeks: int, per_week: int, weekdays: list | None) -> list[dt.date]:
    days = [WEEKDAYS[d] for d in (weekdays or ["화", "목", "월", "수", "금"])[:per_week]]
    if len(days) < per_week:
        raise ValueError("weekdays 가 per_week 보다 적습니다")
    monday = start - dt.timedelta(days=start.weekday())
    return sorted(
        d for w in range(weeks) for wd in days
        if (d := monday + dt.timedelta(weeks=w, days=wd)) >= start
    )


def _channels(spec: dict) -> list[dict]:
    """Each channel's own frequency: 「인스타그램, 링크드인 주 2회씩」 is two a week on each
    (r7 A13-T2: one per_week for both gave 8 posts in all, not 16). A channel given as a
    name takes the calendar's per_week and weekdays."""
    out = []
    for item in spec.get("channels") or []:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            continue
        out.append({"name": str(item["name"]).strip(),
                    "per_week": int(item.get("per_week") or spec.get("per_week") or 1),
                    "weekdays": item.get("weekdays") or spec.get("weekdays")})
    return out


def calendar(ranked: list[dict], mix: dict[str, float], spec: dict) -> list[dict]:
    start = dt.date.fromisoformat(str(spec["start"]))
    weeks = int(spec.get("weeks") or 4)
    if spec.get("end"):
        # a month or a campaign: slots up to its last day, whatever the week count
        end = dt.date.fromisoformat(str(spec["end"]))
        weeks = (end - (start - dt.timedelta(days=start.weekday()))).days // 7 + 1
    else:
        end = None
    channels = _channels(spec) or [{"name": "", "per_week": int(spec.get("per_week") or 1),
                                    "weekdays": spec.get("weekdays")}]
    order = {c["name"]: n for n, c in enumerate(channels)}
    places = sorted(
        ((d, c["name"]) for c in channels
         for d in _dates(start, weeks, c["per_week"], c["weekdays"]) if end is None or d <= end),
        key=lambda p: (p[0], order[p[1]]))
    pools = {k: [t for t in ranked if t["type"] == k] for k in TYPES}

    def pick(kind: str, channel: str) -> dict | None:
        # a topic meant for this channel first, then one meant for none
        pool = pools[kind]
        for want in (channel, ""):
            for i, topic in enumerate(pool):
                if str(topic.get("channel") or "") == want:
                    return pool.pop(i)
        return None

    rows = []
    for (date, channel), kind in zip(places, slots(mix, len(places)), strict=True):
        topic = pick(kind, channel) if channel else (pools[kind].pop(0) if pools[kind] else None)
        row = {"date": date.isoformat(), "weekday": "월화수목금토일"[date.weekday()], "type": kind,
               "title": topic["title"] if topic else f"[{TYPE_LABELS[kind]} 주제 필요]",
               "pillar": topic["pillar"] if topic else ""}
        if channel:
            row = {"channel": channel, **row}
        rows.append(row)
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
    per_channel: dict[str, int] = {}
    for row in schedule:
        if row.get("channel"):
            per_channel[row["channel"]] = per_channel.get(row["channel"], 0) + 1
    print(json.dumps({"ok": True, "ranked": ranked, "weights": weights, "mix": mix, "pillars": pillars,
                      "calendar": schedule, "unfilled_slots": short,
                      **({"posts_per_channel": per_channel} if per_channel else {}),
                      "table_markdown": "\n".join(table)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
