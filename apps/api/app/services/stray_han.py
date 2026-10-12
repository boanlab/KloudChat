"""Chinese words slipped into a Korean answer (「공휴일顺延 규정」, 「增值시키는지」).

A Han run is a slip when it sits outside a gloss (「협의(協議)」), code, a quote of the
person's own words, and is not something the person wrote. Its sentence goes back to the
writer once to be said in Korean; a sentence still holding one gets the Hangul reading.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable

import hanja

from app.services import units

log = logging.getLogger(__name__)

_HAN = re.compile(r"[㐀-䶿一-鿿]+")
#: a gloss in brackets: 「협의(協議)」, 「[協議]」
_GLOSS = re.compile(r"[(\[（［][^)\]）］\n]{0,40}[㐀-䶿一-鿿][^)\]）］\n]{0,40}[)\]）］]")
_CODE = re.compile(r"```.*?```|`[^`\n]+`", re.S)
_QUOTE = re.compile(r"「[^」\n]{1,200}」|“[^”\n]{1,200}”|\"[^\"\n]{1,200}\"")

PROMPT = """These Korean sentences contain Chinese words by mistake. Rewrite each sentence in
natural Korean with the same meaning, replacing the Chinese words with Korean words. Keep
everything else (numbers, names, citations [n], Markdown) exactly as it is. Answer only with
a JSON array of the rewritten sentences, in the same order.

{items}"""


def korean(text: str) -> bool:
    """Whether `text` is written in Korean (Hangul well ahead of Han)."""
    hangul = len(re.findall(r"[가-힣]", text or ""))
    return hangul >= 20 and hangul > 4 * len(_HAN.findall(text or ""))


def strays(text: str, given: str = "") -> list[tuple[int, int, str]]:
    """`(start, end, run)` of each slipped Han run in a Korean `text`."""
    if not text or not _HAN.search(text) or not korean(text):
        return []
    return _runs(text, given)


def _runs(text: str, given: str) -> list[tuple[int, int, str]]:
    kept = [(m.start(), m.end()) for m in (*_GLOSS.finditer(text), *_CODE.finditer(text))]
    flat = re.sub(r"\s+", "", given or "")
    for m in _QUOTE.finditer(text):
        if re.sub(r"\s+", "", m.group(0)[1:-1]) in flat:
            kept.append((m.start(), m.end()))
    return [(m.start(), m.end(), m.group(0)) for m in _HAN.finditer(text)
            if not any(a <= m.start() < b for a, b in kept) and m.group(0) not in flat]


def _sentence_at(text: str, pos: int) -> tuple[int, int]:
    """Span of the sentence (within its line) holding `pos`."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    end = len(text) if end < 0 else end
    line = text[start:end]
    at = 0
    for part in units.SENTENCE_BREAK.split(line):
        piece = line.find(part, at)
        if piece <= pos - start < piece + len(part):
            return start + piece, start + piece + len(part)
        at = piece + len(part)
    return start, end


def read_in_hangul(sentence: str, runs: list[tuple[int, int, str]]) -> str:
    """`sentence` with the given runs (offsets in it) replaced by their Hangul reading."""
    out = sentence
    for s, e, run in sorted(runs, reverse=True):
        out = out[:s] + hanja.translate(run, "substitution") + out[e:]
    return out


Complete = Callable[..., Awaitable[tuple[str, dict]]]


async def mend(text: str, *, given: str = "", complete: Complete | None = None,
               model: str = "", api_key: str = "",
               usage: dict | None = None) -> tuple[str, list[str]]:
    """`(text, changed)`: sentences with slipped Han rephrased once, else read in Hangul."""
    found = strays(text, given)
    if not found:
        return text, []
    spans: list[tuple[int, int]] = []
    for s, _, _ in found:
        span = _sentence_at(text, s)
        if span not in spans:
            spans.append(span)
    sentences = [text[a:b] for a, b in spans]
    log.info("stray Chinese in a Korean answer: %s", [run for _, _, run in found][:12])
    rewritten: list[str] = []
    if complete is not None and model:
        try:
            reply, spent = await complete(
                model, [{"role": "user", "content": PROMPT.format(
                    items=json.dumps(sentences, ensure_ascii=False))}],
                api_key, min(2000, 200 + 120 * len(sentences)))
            for key in ("inputTokens", "outputTokens"):
                if usage is not None:
                    usage[key] = usage.get(key, 0) + int((spent or {}).get(key, 0))
            match = re.search(r"\[.*\]", reply or "", re.S)
            data = json.loads(match.group(0)) if match else []
            if isinstance(data, list) and len(data) == len(sentences):
                rewritten = [str(x) for x in data]
        except Exception as exc:  # noqa: BLE001 — the reading below still applies
            log.info("stray Chinese rewrite failed: %s", exc)
    changed: list[str] = []
    out = text
    for k in range(len(spans) - 1, -1, -1):
        a, b = spans[k]
        old = sentences[k]
        new = rewritten[k].strip() if k < len(rewritten) else ""
        lead, tail = old[:len(old) - len(old.lstrip())], old[len(old.rstrip()):]
        if not new or _runs(new, given) or not (
                0.5 * len(old.strip()) <= len(new) <= 2 * len(old.strip()) + 20):
            # still Chinese, or not the same sentence: the runs read in Hangul instead
            runs = [(s - a, e - a, run) for s, e, run in found if a <= s < b]
            new = read_in_hangul(old, runs).strip()
        fixed = lead + new + tail
        if fixed != old:
            changed.append(f"{old.strip()[:60]}→{new[:60]}")
            out = out[:a] + fixed + out[b:]
    return out, changed


__all__ = ["korean", "mend", "read_in_hangul", "strays"]
