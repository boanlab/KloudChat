"""CPU scheduling reference: textbook averages for a request's process table, to read answers
against (대기 = 완료 − 도착 − 실행; ties to the earlier arrival)."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

from app.services import calc_check
from app.services.calc_check import Stated

#: 「P1: 0, 7, 3」 「| P1 | 0 | 7 | 3 |」
_PROCESS = re.compile(
    r"^\s{0,4}\|?\s{0,4}(?P<name>P\d{1,2})\s{0,2}[:|,]?\s{0,4}"
    r"(?P<nums>\d{1,4}(?:\s{0,4}[,|/]?\s{0,4}\d{1,4}){1,2})\s{0,4}\|?\s{0,4}"
    r"(?:\s[^\d\s|,/][^\n]*)?$",
    re.I,
)
_FIELDS = (
    ("arrival", re.compile(r"도착|arrival", re.I)),
    ("burst", re.compile(r"실행|버스트|burst|CPU\s{0,2}시간|서비스\s{0,2}시간", re.I)),
    ("priority", re.compile(r"우선\s{0,2}순위|priority", re.I)),
)
_HIGHER_IS_LARGER = re.compile(r"(?:클수록|큰\s{0,2}(?:값|숫자|수)[이가]?)\s{0,2}"
                               r"(?:우선순위가\s{0,2})?높"
                               r"|larger\s+(?:number|value)s?\s+(?:is|are|means)\s+higher", re.I)
#: setups this reference does not model
_BEYOND = re.compile(
    r"문맥\s{0,2}교환|컨텍스트\s{0,2}스위칭\s{0,2}(?:시간|비용|오버헤드)\s{0,2}(?:은|는|을|를)?\s{0,2}\d"
    r"|context\s{0,2}switch|I\s{0,2}/\s{0,2}O|입출력|다단계|MLF?Q|에이징|aging|멀티\s{0,2}코어"
    r"|multi-?core|HRRN|EDF|복수\s{0,2}(?:CPU|프로세서)", re.I)

_RR = re.compile(r"라운드\s{0,2}로빈|round[\s-]?robin|(?<![A-Za-z])RR(?![A-Za-z])", re.I)
_QUANTUM = re.compile(
    r"(?:타임\s{0,2}퀀텀|타임\s{0,2}슬라이스|시간\s{0,2}할당량|퀀텀|time\s{0,2}quantum|quantum|"
    r"(?<![A-Za-z])(?:TQ|q))\s{0,2}(?:[=:]|은|는|이|가|을|를)?\s{0,2}(\d{1,3})(?![\d.])", re.I)


@dataclass(frozen=True, slots=True)
class Process:
    name: str
    arrival: int
    burst: int
    priority: int | None


def processes(request: str) -> list[Process]:
    """The request's process table, `[]` when there is none or it doesn't read cleanly."""
    lines = (request or "").splitlines()
    found: list[tuple[int, str, list[int]]] = []
    for i, line in enumerate(lines):
        if m := _PROCESS.match(line):
            nums = [int(n) for n in re.findall(r"\d+", m.group("nums"))]
            found.append((i, m.group("name").upper(), nums))
    names = [name for _, name, _ in found]
    # P1…Pn each once, and no process named elsewhere that the table lacks
    if (len(names) < 2 or sorted(int(n[1:]) for n in names) != list(range(1, len(names) + 1))
            or set(re.findall(r"(?<![A-Za-z])P(\d{1,2})(?!\d)", request, re.I))
            - {n[1:] for n in names}):
        return []
    widths = {len(nums) for _, _, nums in found}
    if len(widths) != 1:
        return []
    width = widths.pop()
    order = _order(lines[: found[0][0]], width)
    if order is None:
        return []
    out = []
    for _, name, nums in found:
        values = dict(zip(order, nums, strict=True))
        if values["burst"] <= 0:
            return []
        out.append(Process(name, values["arrival"], values["burst"], values.get("priority")))
    return out


def _order(before: list[str], width: int) -> list[str] | None:
    """Column order from the nearest header line naming the fields, else arrival, burst and
    priority."""
    for line in reversed(before[-4:]):
        at = {key: m.start() for key, rx in _FIELDS if (m := rx.search(line))}
        if len(at) >= 2:
            order = sorted(at, key=at.get)
            return order if len(order) == width else None
    return ["arrival", "burst", "priority"][:width] if width in (2, 3) else None


@dataclass(frozen=True, slots=True)
class Algorithm:
    #: names the answer may use for it; the first is the canonical row
    rows: tuple[str, ...]
    kind: str
    quantum: int = 0


def algorithms(request: str, has_priority: bool) -> list[Algorithm]:
    """Algorithms the request names, in a fixed order."""
    text = request or ""
    out: list[Algorithm] = []
    if re.search(r"(?<![A-Za-z])FCFS(?![A-Za-z])|선입\s{0,2}선처리|first[\s-]come", text, re.I):
        out.append(Algorithm(("FCFS",), "fcfs"))
    if re.search(r"(?<![A-Za-z])SJF(?![A-Za-z])|최단\s{0,2}작업\s{0,2}우선", text, re.I):
        out.append(Algorithm(("SJF",), "sjf"))
    if re.search(r"(?<![A-Za-z])SRTF?(?![A-Za-z])|최소\s{0,2}잔여|최단\s{0,2}잔여", text, re.I):
        out.append(Algorithm(("SRTF",), "srtf"))
    if _RR.search(text):
        for q in dict.fromkeys(int(m.group(1)) for m in _QUANTUM.finditer(text)):
            if q > 0:
                out.append(Algorithm((f"RR(q={q})", f"라운드 로빈(q={q})"), "rr", q))
    if has_priority:
        if re.search(r"우선\s{0,2}순위[^.\n]{0,15}비선점"
                     r"|비선점\s{0,2}(?:형\s{0,2})?우선\s{0,2}순위", text):
            out.append(Algorithm(("우선순위(비선점)",), "priority"))
        if re.search(r"우선\s{0,2}순위[^.\n]{0,15}(?<!비)선점|(?<!비)선점\s{0,2}(?:형\s{0,2})?"
                     r"우선\s{0,2}순위", text):
            out.append(Algorithm(("우선순위(선점)",), "priority-preemptive"))
    return out


def simulate(procs: list[Process], kind: str, quantum: int = 0, *,
             arrivals_first: bool = True, larger_first: bool = False) -> dict[str, int]:
    """Waiting time by process name; `arrivals_first`: in RR, a process arriving as a quantum
    ends queues before the preempted one."""
    n = len(procs)
    left = [p.burst for p in procs]
    done = [0] * n
    if kind == "rr":
        queue: deque[int] = deque()
        admitted: set[int] = set()

        def admit(until: float) -> None:
            for i in sorted(range(n), key=lambda i: (procs[i].arrival, i)):
                if i not in admitted and procs[i].arrival <= until:
                    admitted.add(i)
                    queue.append(i)

        t = 0
        admit(t)
        while any(left):
            if not queue:
                t = min(procs[i].arrival for i in range(n) if i not in admitted)
                admit(t)
                continue
            i = queue.popleft()
            ran = min(quantum, left[i])
            t += ran
            left[i] -= ran
            if left[i]:
                admit(t if arrivals_first else t - 0.5)
                queue.append(i)
            else:
                done[i] = t
            admit(t)
    else:
        sign = -1 if larger_first else 1

        def key(i: int) -> tuple[int, int, int]:
            first = {"fcfs": procs[i].arrival, "sjf": procs[i].burst, "srtf": left[i]}.get(
                kind, sign * (procs[i].priority or 0))
            return (first, procs[i].arrival, i)

        preemptive = kind in ("srtf", "priority-preemptive")
        t = 0
        while any(left):
            ready = [i for i in range(n) if left[i] and procs[i].arrival <= t]
            if not ready:
                t = min(procs[i].arrival for i in range(n) if left[i])
                continue
            i = min(ready, key=key)
            step = left[i]
            if preemptive:
                later = [p.arrival - t for p in procs if p.arrival > t]
                step = min([step, *later])
            t += step
            left[i] -= step
            if not left[i]:
                done[i] = t
    return {p.name: done[i] - p.arrival - p.burst for i, p in enumerate(procs)}


def _stated(alg: Algorithm, waits: dict[str, int], procs: list[Process]) -> list[Stated]:
    n = len(waits)
    average = sum(waits.values()) / n
    turnaround = sum(waits[p.name] + p.burst for p in procs) / n
    out: list[Stated] = []
    for row in alg.rows:
        out.append(Stated(row, "평균 대기시간", _text(average), average))
        out.append(Stated(row, "평균 반환시간", _text(turnaround), turnaround))
        out.extend(Stated(row, f"{name} 대기", str(w), float(w)) for name, w in waits.items())
    return out


def _text(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))


def reference(request: str) -> list[list[list[Stated]]]:
    """Per algorithm, its accepted readings (RR's two queueing orders when they differ), as
    printed-value lists; `[]` when the request is not a plain scheduling exercise."""
    text = request or ""
    if _BEYOND.search(text):
        return []
    procs = processes(text)
    if not procs:
        return []
    has_priority = all(p.priority is not None for p in procs)
    larger = bool(_HIGHER_IS_LARGER.search(text))
    out: list[list[list[Stated]]] = []
    for alg in algorithms(text, has_priority):
        readings = [simulate(procs, alg.kind, alg.quantum, larger_first=larger)]
        if alg.kind == "rr":
            other = simulate(procs, alg.kind, alg.quantum, arrivals_first=False)
            if other != readings[0]:
                readings.append(other)
        out.append([_stated(alg, waits, procs) for waits in readings])
    return out


def check(answer: str, request: str) -> tuple[str, list[str]]:
    """`answer` with averages and waiting times naming a scheduling result set to the textbook
    value: `(answer, corrections)`. An RR result read the other queueing way stands."""
    algs = reference(request)
    if not algs or not answer:
        return answer, []
    picked = [readings[0] for readings in algs]
    for k, readings in enumerate(algs):
        if len(readings) < 2:
            continue
        rows = {s.row for s in readings[0]}

        def misses(reading: list[Stated], k: int = k, rows: set[str] = rows) -> int:
            # read with every other algorithm present, so 「RR」 alone names neither quantum
            stated = [s for j, r in enumerate(picked) if j != k for s in r] + reading
            fixes = calc_check.check_markdown(answer, stated)[1]
            return sum(1 for f in fixes if any(f.endswith(f"({row} {s.label})")
                                               for row in rows for s in reading))

        clean = [r for r in readings if not misses(r)]
        picked[k] = (clean or readings)[0]
    return calc_check.check_markdown(answer, [s for r in picked for s in r])


def latest_request(messages: list[dict]) -> str:
    """The newest user message with a process table."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        text = ("\n".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
                if isinstance(content, list) else str(content or ""))
        if processes(text):
            return text
    return ""
