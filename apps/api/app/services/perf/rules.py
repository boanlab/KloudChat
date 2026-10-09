"""Verdicts from the recent samples: each names what is wrong, the series and window
it rests on, and what to consider doing. Thresholds come from settings (`perf_*`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.services.perf.store import Key

Points = list[tuple[datetime, float]]

#: Series the rules read, by name.
NAMES = (
    "waiting",
    "kv_cache_percent",
    "preemptions",
    "ttft_p95",
    "up",
    "cpu_cores",
    "mem_limit_percent",
    "mem_percent",
    "disk_used_percent",
    "calls",
    "errors",
    "streams",
    "gpu_temp",
    "node_mem_percent",
)

LEVELS = {"info": 0, "ok": 0, "warn": 1, "critical": 2}


@dataclass(frozen=True)
class Thresholds:
    waiting_max: float = 2
    kv_cache_max: float = 90
    ttft_p95_max_sec: float = 10
    api_cpu_max: float = 80
    mem_max: float = 90
    disk_warn: float = 85
    disk_critical: float = 95
    error_rate_max: float = 5
    gpu_temp_max: float = 85
    sustain_min: int = 10
    api_cpu_sustain_min: int = 15


@dataclass
class Verdict:
    level: str
    code: str
    title: str
    detail: str
    action: str
    evidence: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "code": self.code,
            "title": self.title,
            "detail": self.detail,
            "action": self.action,
            "evidence": self.evidence,
        }


def _within(points: Points, now: datetime, minutes: int) -> Points:
    since = now - timedelta(minutes=minutes)
    return [(t, v) for t, v in points if t >= since]


def sustained(points: Points, now: datetime, minutes: int, over: float) -> bool:
    """At least half the window sampled, and 80% of those samples at or over `over`."""
    window = _within(points, now, minutes)
    if len(window) < max(3, minutes // 2):
        return False
    return sum(1 for _, v in window if v >= over) >= 0.8 * len(window)


def _evidence(
    key: Key, points: Points, now: datetime, minutes: int, threshold: float | None
) -> dict:
    window = _within(points, now, minutes)
    values = [v for _, v in window]
    return {
        "source": key[0],
        "name": key[1],
        "subject": key[2],
        "windowMin": minutes,
        "threshold": threshold,
        "latest": values[-1] if values else None,
        "peak": max(values) if values else None,
        "points": [[int(t.timestamp() * 1000), round(v, 4)] for t, v in window],
    }


def _fmt(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}".rstrip("0").rstrip(".") if digits else f"{value:,.0f}"


def _sum_by_time(series: dict[Key, Points], name: str) -> Points:
    totals: dict[datetime, float] = {}
    for (_, n, _), points in series.items():
        if n == name:
            for t, v in points:
                totals[t] = totals.get(t, 0.0) + v
    return sorted(totals.items())


def evaluate(
    series: dict[Key, Points], now: datetime, th: Thresholds, last_sample: datetime | None
) -> list[Verdict]:
    out: list[Verdict] = []
    win = th.sustain_min

    if last_sample is None or now - last_sample > timedelta(minutes=5):
        out.append(
            Verdict(
                "warn",
                "collector_stalled",
                "수집이 멈췄습니다",
                "최근 5분 동안 새 표본이 없습니다."
                if last_sample
                else "아직 수집한 표본이 없습니다.",
                "api 컨테이너 로그에서 perf 수집기 오류를 확인하세요. 처음 켠 직후라면 몇 분 뒤 다시 보세요.",
            )
        )

    for key, points in series.items():
        source, name, subject = key
        if source == "vllm" and name == "waiting":
            preempt = series.get(("vllm", "preemptions", subject), [])
            if sustained(points, now, win, th.waiting_max + 1e-9):
                out.append(
                    Verdict(
                        "critical",
                        "gpu_saturated",
                        f"GPU 포화 — {subject}",
                        f"대기 요청이 {win}분 동안 {_fmt(th.waiting_max, 0)}건을 넘었습니다 "
                        f"(최근 {_fmt(points[-1][1], 0)}건).",
                        "동시 요청이 이 모델의 처리 한도를 넘습니다. 같은 모델의 GPU 복제본(노드)을 늘리거나, "
                        "일부 작업(개요·판정 등)을 다른 모델로 나누는 것을 검토하세요.",
                        [
                            _evidence(key, points, now, win, th.waiting_max),
                            _evidence(("vllm", "preemptions", subject), preempt, now, win, None),
                        ],
                    )
                )
            elif sustained(points, now, win, 1):
                out.append(
                    Verdict(
                        "warn",
                        "gpu_queue",
                        f"대기열 형성 — {subject}",
                        f"{win}분 내내 대기 요청이 있습니다 (최근 {_fmt(points[-1][1], 0)}건).",
                        "아직 한도 안이지만 동시 요청이 처리량에 가깝습니다. 사용자 수가 더 늘기 전에 증설 계획을 세우세요.",
                        [_evidence(key, points, now, win, 1)],
                    )
                )
        elif (
            source == "vllm"
            and name == "kv_cache_percent"
            and sustained(points, now, win, th.kv_cache_max)
        ):
            preempt = series.get(("vllm", "preemptions", subject), [])
            evicted = sum(v for _, v in _within(preempt, now, win))
            out.append(
                Verdict(
                    "critical" if evicted > 0 else "warn",
                    "kv_cache_full",
                    f"KV 캐시 포화 — {subject}",
                    f"KV 캐시 사용률이 {win}분 동안 {_fmt(th.kv_cache_max, 0)}% 이상입니다"
                    + (f"; 선점(preemption) {_fmt(evicted, 0)}회." if evicted else "."),
                    "동시 세션 수나 문맥 길이가 KV 캐시를 넘고 있습니다. 복제본을 늘리거나 max-num-seqs·"
                    "max-model-len을 줄이거나 gpu-memory-utilization을 올리는 것을 검토하세요.",
                    [
                        _evidence(key, points, now, win, th.kv_cache_max),
                        _evidence(("vllm", "preemptions", subject), preempt, now, win, None),
                    ],
                )
            )
        elif (
            source == "vllm"
            and name == "ttft_p95"
            and sustained(points, now, win, th.ttft_p95_max_sec)
        ):
            sessions = _sum_by_time(series, "streams")
            recent_s = [v for _, v in _within(sessions, now, win)]
            before_s = [
                v
                for t, v in sessions
                if now - timedelta(minutes=3 * win) <= t < now - timedelta(minutes=win)
            ]
            recent_t = [v for _, v in _within(points, now, win)]
            before_t = [
                v
                for t, v in points
                if now - timedelta(minutes=3 * win) <= t < now - timedelta(minutes=win)
            ]
            rising = (
                bool(recent_s and before_s and recent_t and before_t)
                and sum(recent_s) / len(recent_s) > sum(before_s) / len(before_s)
                and sum(recent_t) / len(recent_t) >= 1.5 * sum(before_t) / len(before_t)
            )
            out.append(
                Verdict(
                    "warn",
                    "ttft_high",
                    f"첫 토큰 지연 — {subject}",
                    f"첫 토큰까지 p95가 {win}분 동안 {_fmt(th.ttft_p95_max_sec, 0)}초를 넘었습니다"
                    + (" — 동시 세션이 늘면서 함께 늘었습니다." if rising else "."),
                    "대기 요청(waiting)이 함께 늘었다면 GPU 증설을, 그렇지 않다면 긴 입력(prefill)이나 "
                    "첨부 문서 크기를 먼저 의심하세요.",
                    [
                        _evidence(key, points, now, win, th.ttft_p95_max_sec),
                        _evidence(("app", "streams", "합계"), sessions, now, 3 * win, None),
                    ],
                )
            )
        elif source == "vllm" and name == "up":
            window = _within(points, now, win)
            if window and window[-1][1] == 0 and any(v > 0 for _, v in window):
                out.append(
                    Verdict(
                        "critical",
                        "model_down",
                        f"모델 서버 응답 없음 — {subject}",
                        "최근까지 응답하던 vLLM 서버가 지표에 답하지 않습니다.",
                        "GPU 노드의 vllm 컨테이너 상태와 로그를 확인하세요.",
                        [_evidence(key, points, now, win, None)],
                    )
                )
        elif (
            source == "container"
            and name == "cpu_cores"
            and subject.startswith("api")
            and "다른 인스턴스" not in subject
        ):
            minutes = th.api_cpu_sustain_min
            if sustained(points, now, minutes, th.api_cpu_max / 100):
                out.append(
                    Verdict(
                        "critical" if sustained(points, now, minutes, 0.95) else "warn",
                        "api_cpu",
                        f"api 복제본 추가 검토 — {subject}",
                        f"이 복제본이 {minutes}분 동안 CPU 한 코어의 {_fmt(th.api_cpu_max, 0)}% 이상을 썼습니다 "
                        "(api 프로세스는 코어 하나로 요청을 처리합니다).",
                        "KCHAT_API_REPLICAS를 하나 늘리고 docker compose up -d로 복제본을 추가하세요. "
                        "DB 연결 수(복제본 × (DB_POOL_SIZE + DB_MAX_OVERFLOW))가 Postgres max_connections 안인지도 확인하세요.",
                        [_evidence(key, points, now, minutes, th.api_cpu_max / 100)],
                    )
                )
        elif source == "host" and name == "mem_percent" and sustained(points, now, win, th.mem_max):
            out.append(
                Verdict(
                    "critical",
                    "host_memory",
                    "호스트 메모리 부족",
                    f"호스트 메모리 사용률이 {win}분 동안 {_fmt(th.mem_max, 0)}% 이상입니다.",
                    "메모리를 많이 쓰는 컨테이너(아래 표)를 확인하고, 줄일 수 없다면 메모리 증설이나 서비스 분리를 검토하세요.",
                    [_evidence(key, points, now, win, th.mem_max)],
                )
            )
        elif (
            source == "container"
            and name == "mem_limit_percent"
            and sustained(points, now, win, th.mem_max)
        ):
            out.append(
                Verdict(
                    "warn",
                    "container_memory",
                    f"메모리 한도 근접 — {subject}",
                    f"한도의 {_fmt(th.mem_max, 0)}% 이상을 {win}분 동안 썼습니다. 넘으면 컨테이너가 강제 종료됩니다.",
                    "mem_limit을 올리거나 동시 작업 수를 줄이세요.",
                    [_evidence(key, points, now, win, th.mem_max)],
                )
            )
        elif source == "host" and name == "disk_used_percent" and points:
            used = points[-1][1]
            if used >= th.disk_warn:
                out.append(
                    Verdict(
                        "critical" if used >= th.disk_critical else "warn",
                        "disk_full",
                        f"디스크 공간 부족 — {subject}",
                        f"사용률 {_fmt(used)}% (주의 {_fmt(th.disk_warn, 0)}%, 위험 {_fmt(th.disk_critical, 0)}%).",
                        "사용량 화면의 저장 공간 정리, 오래된 도커 이미지·빌드 캐시 정리(docker system prune), 디스크 증설을 검토하세요.",
                        [_evidence(key, points, now, 60, th.disk_warn)],
                    )
                )
        elif source == "model" and name == "calls" and subject != "*":
            calls = sum(v for _, v in _within(points, now, 15))
            errors = sum(
                v for _, v in _within(series.get(("model", "errors", subject), []), now, 15)
            )
            if calls >= 20 and 100 * errors / calls >= th.error_rate_max:
                rate = 100 * errors / calls
                out.append(
                    Verdict(
                        "critical" if rate >= 3 * th.error_rate_max else "warn",
                        "model_errors",
                        f"모델 오류율 — {subject}",
                        f"최근 15분 {_fmt(calls, 0)}건 중 {_fmt(errors, 0)}건 실패 ({_fmt(rate)}%).",
                        "LiteLLM 로그에서 오류 종류(한도 초과·시간 초과·공급자 장애)를 확인하고, 필요하면 대체 모델을 지정하세요.",
                        [
                            _evidence(
                                ("model", "errors", subject),
                                series.get(("model", "errors", subject), []),
                                now,
                                15,
                                None,
                            ),
                            _evidence(key, points, now, 15, None),
                        ],
                    )
                )
        elif (
            source == "gpu" and name == "gpu_temp" and sustained(points, now, win, th.gpu_temp_max)
        ):
            out.append(
                Verdict(
                    "warn",
                    "gpu_hot",
                    f"GPU 온도 높음 — {subject}",
                    f"{win}분 동안 {_fmt(th.gpu_temp_max, 0)}°C 이상입니다. 클럭이 낮아져 처리량이 줄 수 있습니다.",
                    "노드 냉각과 설치 환경을 확인하세요.",
                    [_evidence(key, points, now, win, th.gpu_temp_max)],
                )
            )
        elif (
            source == "gpu"
            and name == "node_mem_percent"
            and sustained(points, now, win, th.mem_max)
        ):
            out.append(
                Verdict(
                    "warn",
                    "gpu_node_memory",
                    f"GPU 노드 메모리 부족 — {subject}",
                    f"{win}분 동안 {_fmt(th.mem_max, 0)}% 이상입니다 (GB10은 GPU와 메모리를 함께 씁니다).",
                    "같은 노드에 올린 모델 수나 gpu-memory-utilization을 줄이세요.",
                    [_evidence(key, points, now, win, th.mem_max)],
                )
            )
    out.sort(key=lambda v: -LEVELS.get(v.level, 0))
    return out


def overall(verdicts: list[Verdict]) -> str:
    worst = max((LEVELS.get(v.level, 0) for v in verdicts), default=0)
    return {0: "ok", 1: "warn", 2: "critical"}[worst]
