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
        if (not any(a.kind.startswith("priority") for a in out)
                and _PRIORITY_RUN.search(text)):
            # 「우선순위 스케줄링」 with neither named: both readings, each its own row
            out += [Algorithm(("우선순위(비선점)",), "priority"),
                    Algorithm(("우선순위(선점)",), "priority-preemptive")]
    return out


#: priority scheduling named as an algorithm (not just a column of the table)
_PRIORITY_RUN = re.compile(r"우선\s{0,2}순위\s{0,2}(?:기반\s{0,2})?(?:스케줄링|알고리즘|방식)"
                           r"|priority\s{0,2}(?:scheduling|algorithm)", re.I)


def simulate(procs: list[Process], kind: str, quantum: int = 0, *,
             arrivals_first: bool = True, larger_first: bool = False) -> dict[str, int]:
    """Waiting time by process name; `arrivals_first`: in RR, a process arriving as a quantum
    ends queues before the preempted one."""
    return _run(procs, kind, quantum, arrivals_first=arrivals_first,
                larger_first=larger_first)[0]


def _run(procs: list[Process], kind: str, quantum: int = 0, *, arrivals_first: bool = True,
         larger_first: bool = False) -> tuple[dict[str, int], list[tuple[str, int, int]]]:
    """Waiting times and the Gantt runs `(name, start, end)`, idle gaps left out."""
    n = len(procs)
    runs: list[tuple[str, int, int]] = []

    def ran(i: int, start: int, end: int) -> None:
        if runs and runs[-1][0] == procs[i].name and runs[-1][2] == start:
            runs[-1] = (procs[i].name, runs[-1][1], end)
        else:
            runs.append((procs[i].name, start, end))

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
            slice_ = min(quantum, left[i])
            ran(i, t, t + slice_)
            t += slice_
            left[i] -= slice_
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
            ran(i, t, t + step)
            t += step
            left[i] -= step
            if not left[i]:
                done[i] = t
    return {p.name: done[i] - p.arrival - p.burst for i, p in enumerate(procs)}, runs


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


#: what the block tells the writer (`calc.computed_values` speaks the same way)
_BLOCK_HEAD = (
    "# 코드로 계산한 값\n"
    "아래 값은 요청의 프로세스 표로 각 스케줄링 알고리즘을 코드로 실행해 구했다(대기 = 완료 − 도착 "
    "− 실행). 간트 차트 구간·대기시간·평균은 이 값을 그대로 옮기고, 같은 항목을 손으로 다시 "
    "셈하거나 다른 값으로 적지 마라. 본문과 비교 표는 같은 값을 쓴다."
)


def computed_block(request: str) -> str:
    """The request's scheduling exercise run by code, as a writer's computed-values block
    (「FCFS | 평균 대기시간 = 4.75」 lines), or '' when it is not a plain exercise."""
    text = request or ""
    if _BEYOND.search(text):
        return ""
    procs = processes(text)
    if not procs:
        return ""
    algs = algorithms(text, all(p.priority is not None for p in procs))
    if not algs:
        return ""
    larger = bool(_HIGHER_IS_LARGER.search(text))
    lines = ["동점 규칙: 조건이 같으면 먼저 도착한 프로세스가 먼저; 라운드 로빈에서 퀀텀이 끝나는 "
             "시각에 도착한 프로세스는 선점된 프로세스보다 먼저 큐에 들어간다"]
    if any(a.kind.startswith("priority") for a in algs):
        lines.append("우선순위: 숫자가 " + ("클수록" if larger else "작을수록") + " 높다")
    n = len(procs)
    for alg in algs:
        waits, runs = _run(procs, alg.kind, alg.quantum, larger_first=larger)
        done = {p.name: waits[p.name] + p.arrival + p.burst for p in procs}
        average = sum(waits.values()) / n
        turnaround = sum(done[p.name] - p.arrival for p in procs) / n
        row = alg.rows[0]
        lines.append(f"{row} | 간트 차트 = " + ", ".join(f"{name} {a}-{b}"
                                                      for name, a, b in runs))
        lines.append(f"{row} 대기 풀이: " + ", ".join(
            f"{p.name} {done[p.name]}−{p.arrival}−{p.burst}={waits[p.name]}"
            for p in procs) + f" → 평균 {sum(waits.values())}/{n}={_text(average)}")
        # every name the slides may use for the row (「RR(q=2)」 「라운드 로빈(q=2)」)
        for row in alg.rows:
            lines.append(f"{row} | 평균 대기시간 = {_text(average)} | "
                         f"평균 반환시간 = {_text(turnaround)}")
            lines.append(f"{row} | " + " | ".join(f"{p.name} 대기 = {waits[p.name]}"
                                                   for p in procs))
    return _BLOCK_HEAD + "\n\n" + "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Schedule:
    """One algorithm run on the request's processes."""

    alg: Algorithm
    procs: tuple[Process, ...]
    #: Gantt runs `(name, start, end)`, idle gaps left out
    runs: tuple[tuple[str, int, int], ...]
    waits: dict[str, int]

    @property
    def average(self) -> float:
        return sum(self.waits.values()) / len(self.procs)

    @property
    def average_text(self) -> str:
        return _text(self.average)

    @property
    def reading(self) -> str:
        """「비선점」/「선점」 for a priority reading, else the algorithm's name."""
        return _READING.get(self.alg.kind, self.alg.rows[0])


def schedules(request: str) -> list[Schedule]:
    """Each algorithm the request names, run by code; `[]` when it is not a plain exercise
    (the same reading as `computed_block`)."""
    text = request or ""
    if _BEYOND.search(text):
        return []
    procs = processes(text)
    if not procs:
        return []
    larger = bool(_HIGHER_IS_LARGER.search(text))
    out = []
    for alg in algorithms(text, all(p.priority is not None for p in procs)):
        waits, runs = _run(procs, alg.kind, alg.quantum, larger_first=larger)
        out.append(Schedule(alg, tuple(procs), tuple(runs), waits))
    return out


def gantt_rows(s: Schedule) -> list[list[str]]:
    """The schedule as a slide table in time order: each run, what the process has left or,
    when it finishes, its waiting time worked out, then the average (Claude's RR table)."""
    left = {p.name: p.burst for p in s.procs}
    by_name = {p.name: p for p in s.procs}
    rows = [["구간", "실행", "남은 시간 · 대기"]]
    for name, a, b in s.runs:
        left[name] -= b - a
        p = by_name[name]
        done = s.waits[name] + p.arrival + p.burst
        rows.append([f"{a}–{b}", name,
                     f"남은 {left[name]}" if left[name]
                     else f"완료, 대기 {done}−{p.arrival}−{p.burst} = {s.waits[name]}"])
    rows.append(["평균 대기", "",
                 f"({' + '.join(str(s.waits[p.name]) for p in s.procs)}) / {len(s.procs)}"
                 f" = {s.average_text}"])
    return rows


#: the reading's own word when one slide carries both priority readings
_READING = {"priority": "비선점", "priority-preemptive": "선점"}


def readings_rows(several: list[Schedule]) -> list[list[str]]:
    """Several readings of one algorithm on one slide (priority without and with preemption):
    each one's runs in order and its average."""
    rows = [["방식", "간트 차트", "평균 대기"]]
    for s in several:
        rows.append([s.reading, " → ".join(f"{name} {a}–{b}" for name, a, b in s.runs),
                     s.average_text])
    return rows


#: how a slide title names each kind of algorithm
_TITLE_NAMES = {
    "fcfs": re.compile(r"(?<![A-Za-z])FCFS(?![A-Za-z])|선입\s{0,2}선처리|first[\s-]come", re.I),
    "sjf": re.compile(r"(?<!선점형\s)(?<!선점\s)(?<![A-Za-z])SJF(?![A-Za-z])"
                      r"|최단\s{0,2}작업\s{0,2}우선", re.I),
    "srtf": re.compile(r"(?<![A-Za-z])SRTF?(?![A-Za-z])|최소\s{0,2}잔여|최단\s{0,2}잔여"
                       r"|선점\s{0,2}형?\s{0,2}SJF", re.I),
    "rr": _RR,
    "priority": re.compile(r"우선\s{0,2}순위|priority", re.I),
}


def mentioned(title: str, all_: list[Schedule]) -> list[Schedule]:
    """Every schedule a slide title names (「SRTF와 RR 스케줄링」 names two)."""
    named = [kind for kind, rx in _TITLE_NAMES.items() if rx.search(title or "")]
    return [s for kind in named for s in _of_kind(kind, title, all_)]


def titled(title: str, all_: list[Schedule]) -> list[Schedule]:
    """The schedules a slide title is about; none for a title naming two algorithms (a
    comparison) or none."""
    named = [kind for kind, rx in _TITLE_NAMES.items() if rx.search(title or "")]
    return _of_kind(named[0], title, all_) if len(named) == 1 else []


def _of_kind(kind: str, title: str, all_: list[Schedule]) -> list[Schedule]:
    if kind == "priority":
        found = [s for s in all_ if s.alg.kind.startswith("priority")]
        if re.search(r"비\s{0,2}선점|non-?preemptive", title, re.I):
            return [s for s in found if s.alg.kind == "priority"]
        if re.search(r"선점|preemptive", title, re.I):
            return [s for s in found if s.alg.kind == "priority-preemptive"]
        return found
    found = [s for s in all_ if s.alg.kind == kind]
    if kind == "rr" and len(found) > 1:
        # several quanta: the title's own number picks one
        found = [s for s in found if re.search(rf"(?<!\d){s.alg.quantum}(?!\d)", title)]
    return found


def shows_runs(text: str, s: Schedule) -> bool:
    """Every run of `s` written as an interval (「0–7」 「0~7」 「0-7」) in `text`."""
    return all(re.search(rf"(?<![\d.]){a}\s{{0,2}}[-–~—]\s{{0,2}}{b}(?![\d.])", text or "")
               for _, a, b in s.runs)


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
