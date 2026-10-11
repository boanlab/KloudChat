"""Minimal Prometheus text parser for vLLM and gpu-exporter."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# bounded line length
_LINE = re.compile(r"^([A-Za-z_:][A-Za-z0-9_:]{0,200})(?:\{(.{0,4000})\})?\s+(\S{1,64})")
_LABEL = re.compile(r'([A-Za-z_][A-Za-z0-9_]{0,100})="((?:[^"\\]|\\.){0,1000})"')
_MAX_LINE = 8192


@dataclass(frozen=True, slots=True)
class Sample:
    name: str
    labels: dict[str, str]
    value: float


def parse(text: str) -> list[Sample]:
    out: list[Sample] = []
    for line in text.splitlines():
        if not line or line[0] == "#" or len(line) > _MAX_LINE:
            continue
        match = _LINE.match(line)
        if not match:
            continue
        try:
            value = float(match.group(3))
        except ValueError:
            continue
        if math.isnan(value):
            continue
        labels = {k: v.replace('\\"', '"') for k, v in _LABEL.findall(match.group(2) or "")}
        out.append(Sample(match.group(1), labels, value))
    return out


def total(samples: list[Sample], name: str) -> float | None:
    """Sum of `name` over label sets, or None."""
    values = [s.value for s in samples if s.name == name]
    return sum(values) if values else None


def buckets(samples: list[Sample], family: str) -> dict[float, float]:
    """Cumulative histogram counts by bound, summed over label sets."""
    out: dict[float, float] = {}
    for s in samples:
        if s.name == f"{family}_bucket" and "le" in s.labels:
            le = math.inf if s.labels["le"] in ("+Inf", "Inf") else float(s.labels["le"])
            out[le] = out.get(le, 0.0) + s.value
    return out


def delta(cur: dict[float, float], prev: dict[float, float] | None) -> dict[float, float]:
    """Counts between two scrapes; counter reset keeps current counts."""
    if not prev:
        return {}
    diff = {le: cur[le] - prev.get(le, 0.0) for le in cur}
    if any(v < 0 for v in diff.values()):
        return dict(cur)
    return diff


def quantile(hist: dict[float, float], q: float) -> float | None:
    """`histogram_quantile` over cumulative counts; None if empty."""
    if not hist:
        return None
    bounds = sorted(hist)
    count = hist[bounds[-1]]
    if count <= 0:
        return None
    rank = q * count
    lower, below = 0.0, 0.0
    for le in bounds:
        cum = hist[le]
        if cum >= rank:
            if math.isinf(le):
                return lower
            if cum == below:
                return le
            return lower + (le - lower) * (rank - below) / (cum - below)
        lower, below = (le if not math.isinf(le) else lower), cum
    return lower
