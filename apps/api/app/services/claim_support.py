"""Whether a cited sentence is backed by the source it cites: its numbers are written there
and most of its content words are. Lexical, so it errs towards keeping a paraphrase; what it
catches is a citation pinned to a source that does not talk about the claim at all, or a
claim whose figures the source does not hold.

Korean words carry particles and endings (「시계로는」), so a word counts as found when a
prefix of it, at most two letters short, is in the source; Latin words are matched whole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CITE = re.compile(r"\[\[?\d{1,3}(?:\s{0,2}[,，\-–]\s{0,2}\d{1,3}){0,9}\]\]?(?:\([^)\s]{1,500}\))?")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_HANGUL = re.compile(r"[가-힣]{2,}")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9+#-]{1,}")
#: words any sentence uses; they back nothing
_COMMON = frozenset("""
있다 있습 있는 있으 없다 없는 없습 합니 한다 하는 하며 하고 하지 해서 했다 됩니 된다 되는 되어 되며
것이 것은 것을 것으 수가 수는 수도 이는 이를 이것 그것 그리 그러 그래 또한 또는 및 등의 등을 등이
때문 위해 위한 통해 대한 대해 따라 따른 경우 가지 모든 각각 서로 다른 같은 이러 이런 그런 매우 가장
더욱 바로 주로 특히 대부 모두 하나 여러 사용 이용 기반 관련 의미 설명 정의 개념 특징 방법 방식 문제
중요 핵심 다음 이번 자료 강의 내용 부분 기본 일반 결과 상태 과정
the and for with that this from are was were have has its into than then they them
""".split())


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


@dataclass(frozen=True, slots=True)
class Support:
    #: share of the claim's content words found in the source (1.0 with none to check)
    coverage: float
    #: numbers in the claim the source does not hold
    missing_numbers: tuple[str, ...]
    words: int

    @property
    def backed(self) -> bool:
        if self.missing_numbers:
            return False
        return self.words < 2 or self.coverage >= THRESHOLD


#: content-word share a backed claim reaches (calibrated on studio outputs judged by hand)
THRESHOLD = 0.35


def _found(word: str, flat: str) -> bool:
    if word[0].isascii():
        return word.lower() in flat
    shortest = max(2, len(word) - 2)
    return any(word[:n] in flat for n in range(len(word), shortest - 1, -1))


def words_of(claim: str) -> list[str]:
    text = _CITE.sub(" ", claim or "")
    out: list[str] = []
    for w in _HANGUL.findall(text):
        if w[:2] not in _COMMON and w not in out:
            out.append(w)
    for w in _LATIN.findall(text):
        if w.lower() not in _COMMON and w not in out:
            out.append(w)
    return out


def support(claim: str, source: str) -> Support:
    """How far `source` backs `claim` (citations in the claim ignored)."""
    text = _CITE.sub(" ", claim or "")
    flat = _squeeze(source)
    plain = re.sub(r"[,\s]", "", source or "")
    missing = tuple(n for n in dict.fromkeys(_NUMBER.findall(text))
                    if len(n.replace(",", "")) >= 2 and n.replace(",", "") not in plain)
    words = words_of(text)
    if not words:
        return Support(1.0, missing, 0)
    found = sum(1 for w in words if _found(w, flat))
    return Support(found / len(words), missing, len(words))


def backed(claim: str, source: str) -> bool:
    return support(claim, source).backed


__all__ = ["THRESHOLD", "Support", "backed", "support", "words_of"]
