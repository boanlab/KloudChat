"""Deterministic Korean text fixes for model output: Hanja read back to Hangul, stray spaces closed.

Parenthesised glosses (`분산(分散)`) and code are left alone; the gloss rule is
`lint`'s own so the two never disagree. A substitution can be a wrong word
(`試點` → 시점), so every one is reported back to the caller.
"""

from __future__ import annotations

import re

import hanja

from app.services.lint import _GLOSSED, _HANJA

#: Fences that hold prose, not code; they are not protected from substitution.
_PROSE_FENCES = ("kpi", "steps", "chart", "table", "mermaid")

#: `<code>`/`<pre>`, Markdown code fences and spans: never rewritten.
_CODE = re.compile(
    r"<(code|pre)\b[^>]*>.*?</\1\s*>"
    rf"|```(?!\s*(?:{'|'.join(_PROSE_FENCES)})\b).*?```"
    r"|`[^`\n]+`",
    re.S | re.I,
)


def _protected(text: str) -> list[tuple[int, int]]:
    """Spans the substitution must not enter: glosses and code."""
    return [(m.start(), m.end()) for m in (*_GLOSSED.finditer(text), *_CODE.finditer(text))]


def read_back(text: str) -> tuple[str, list[str]]:
    """`(text, replaced)`: Hanja runs read in Hangul, and the runs substituted, in order."""
    if not text or not _HANJA.search(text):
        return text, []

    keep = _protected(text)
    replaced: list[str] = []
    out: list[str] = []
    at = 0
    for found in _HANJA.finditer(text):
        if any(start <= found.start() < end for start, end in keep):
            continue
        run = found.group(0)
        read = hanja.translate(run, "substitution")
        if read == run:  # no reading known; `lint` still reports it
            continue
        out.append(text[at : found.start()])
        out.append(read)
        replaced.append(run)
        at = found.end()
    out.append(text[at:])
    return "".join(out), replaced


#: Digit, stray space, counter (`주 3 일`, `5,000 만 원`, `1 단계`). A
#: multi-syllable counter is unambiguous and joins whatever follows; a single
#: syllable joins only before a non-Hangul character or a counter particle
#: (3 일까지 → 3일까지, but 3 일반인 stays).
_COUNTER_LONG = re.compile(
    r"(?<![0-9])([0-9][0-9,.]{0,20}) "
    r"(만 원|억 원|천 원|단계|시간|개월|가지|학점|과목|개소|개교|학기|주차|학년도|학년|분반|"
    r"퍼센트|%)"
)
_COUNTER_SHORT = re.compile(
    r"(?<![0-9])([0-9][0-9,.]{0,20}) "
    r"(일|명|원|건|개|주|년|월|회|차|기|분|초|점|배|층|호|번|매|부|장|쪽|판|시|석|인|대|위|곳|줄|자|권|편|만|억|천)"
    r"(?=[^가-힣]|$|까지|부터|간|째|씩|마다|이|은|을|의|에|로|가|도|만|과|와|씩|째)"
)
#: Latin letter or digit, stray space, particle (`대안 A 는`, `ResNet 은`).
_PARTICLE = re.compile(
    r"([A-Za-z0-9)]) (은|는|이|가|을|를|의|와|과|로|으로|에|에서|도|만|보다|처럼|까지|부터)"
    r"(?![가-힣])"
)


def tidy_spacing(text: str) -> str:
    """Closes the space before a counter after a digit and before a particle after a Latin word."""
    if not text:
        return text
    text = _COUNTER_LONG.sub(r"\1\2", text)
    text = _COUNTER_SHORT.sub(r"\1\2", text)
    return _PARTICLE.sub(r"\1\2", text)


__all__ = ["read_back", "tidy_spacing"]
