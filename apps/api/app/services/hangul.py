"""Deterministic Korean text fixes for model output: Hanja to Hangul, spacing, stray Cyrillic.

Glosses and code are left alone, using `lint`'s gloss rule. A substitution can pick a
wrong word, so every one is reported back to the caller.
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


#: Cyrillic inside a Korean word (「프로мп트」): a sampling slip.
_STRAY = re.compile(r"[\u0400-\u04FF]")
_MIXED_WORD = re.compile(r"[가-힣\u0400-\u04FF]*[가-힣][가-힣\u0400-\u04FF]*")


#: Loanwords a slip most often lands in, for when the document never spells one cleanly.
_COMMON_TERMS = (
    "프롬프트 에이전트 컨텍스트 파이프라인 플랫폼 클라우드 인프라 데이터베이스 아키텍처 "
    "프레임워크 알고리즘 네트워크 엔드포인트 게이트웨이 프로토콜 인터페이스 시스템 서비스 "
    "솔루션 모델 벤더 컴플라이언스 거버넌스 리스크 시나리오 프로세스 프로젝트 모니터링 "
    "대시보드 워크플로 오케스트레이션 인젝션 마이크로서비스 컨테이너 쿠버네티스 템플릿"
)


_CYRILLIC_SOUND = str.maketrans({
    "м": "ㅁ", "п": "ㅍ", "т": "ㅌ", "к": "ㅋ", "с": "ㅅ", "н": "ㄴ", "л": "ㄹ", "р": "ㄹ",
    "б": "ㅂ", "д": "ㄷ", "г": "ㄱ", "в": "ㅂ", "з": "ㅈ", "ж": "ㅈ", "ч": "ㅊ", "ш": "ㅅ",
    "ф": "ㅍ", "х": "ㅎ", "ц": "ㅊ", "а": "ㅏ", "о": "ㅗ", "е": "ㅔ", "э": "ㅔ", "и": "ㅣ",
    "ы": "ㅡ", "у": "ㅜ", "ю": "ㅠ", "я": "ㅑ", "й": "ㅣ",
})
_LEADS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_VOWELS = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_TAILS = ["", *"ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"]


def _jamo(word: str) -> str:
    """A word as the sounds it is written with: Hangul split into jamo, Cyrillic read as
    the nearest jamo, the silent initial ㅇ dropped."""
    out = []
    for ch in word.lower().translate(_CYRILLIC_SOUND):
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            lead, vowel, tail = code // 588, (code % 588) // 28, code % 28
            onset = "" if _LEADS[lead] == "ㅇ" else _LEADS[lead]
            out.append(onset + _VOWELS[vowel] + _TAILS[tail])
        else:
            out.append(ch)
    return "".join(out)


def repair_mixed_script(text: str, vocabulary: str = "") -> tuple[str, list[str]]:
    """Korean words with Cyrillic inside, replaced by the closest clean word or stripped.

    Returns (text, changed words). Greek is left alone, being notation."""
    import difflib

    if not _STRAY.search(text or ""):
        return text, []
    clean_words = {
        w for w in re.findall(r"[가-힣]{2,}", f"{text} {vocabulary} {_COMMON_TERMS}")
    }
    changed: list[str] = []

    def mend(m: re.Match) -> str:
        word = m.group(0)
        if not _STRAY.search(word) or not re.search(r"[가-힣]", word):
            return word
        skeleton = _STRAY.sub("", word)
        # Compared by sound: the slip is the right sound in the wrong alphabet.
        heard = _jamo(word)
        candidates = [w for w in clean_words if abs(len(w) - len(skeleton)) <= 2]
        scored = sorted(
            ((difflib.SequenceMatcher(None, heard, _jamo(w)).ratio(), w) for w in candidates),
            reverse=True,
        )
        fixed = scored[0][1] if scored and scored[0][0] >= 0.6 else skeleton
        changed.append(f"{word}→{fixed}")
        return fixed

    return _MIXED_WORD.sub(mend, text), changed
