"""Deterministic Korean fixes (Hanja, spacing, Cyrillic); substitutions reported."""

from __future__ import annotations

import re

import hanja

from app.services.lint import _GLOSSED, _HANJA

#: prose fences, not protected
_PROSE_FENCES = ("kpi", "steps", "chart", "table", "mermaid")

#: code tags/fences/spans, never rewritten
_CODE = re.compile(
    r"<(code|pre)\b[^>]*>.*?</\1\s*>"
    rf"|```(?!\s*(?:{'|'.join(_PROSE_FENCES)})\b).*?```"
    r"|`[^`\n]+`",
    re.S | re.I,
)


def _protected(text: str) -> list[tuple[int, int]]:
    """Protected spans: glosses and code."""
    return [(m.start(), m.end()) for m in (*_GLOSSED.finditer(text), *_CODE.finditer(text))]


#: Chinese words a model slips into Korean whose Hanja reading is no Korean word
#: (「本次会议」 read 「본차회의」, 「补提」 read 「보제」): the Korean word instead
_CHINESE_WORDS = {
    "本次": "이번", "本周": "이번 주", "下周": "다음 주", "下次": "다음", "上周": "지난주",
    "补提": "보완 제출", "补交": "보완 제출", "提交": "제출", "负责人": "담당자", "负责": "담당",
    "截止": "마감", "讨论": "논의",
}
_CHINESE_WORD = re.compile("|".join(sorted(map(re.escape, _CHINESE_WORDS), key=len, reverse=True)))


def _read_run(run: str, before: str) -> str:
    """A Hanja run in Hangul; a Chinese word as its Korean word, set off by a space from the
    Korean it was glued to (Chinese writes none)."""
    if not _CHINESE_WORD.search(run):
        return hanja.translate(run, "substitution")
    parts, at = [], 0
    for m in _CHINESE_WORD.finditer(run):
        if m.start() > at:
            parts.append(hanja.translate(run[at:m.start()], "substitution"))
        parts.append(_CHINESE_WORDS[m.group(0)])
        at = m.end()
    if at < len(run):
        parts.append(hanja.translate(run[at:], "substitution"))
    read = " ".join(parts)
    return f" {read}" if re.match(r"[가-힣]", before[-1:]) else read


def read_back(text: str) -> tuple[str, list[str]]:
    """(text, replaced) with Hanja runs read in Hangul."""
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
        read = _read_run(run, text[:found.start()])
        if read == run:  # no reading known; `lint` still reports it
            continue
        out.append(text[at : found.start()])
        out.append(read)
        replaced.append(run)
        at = found.end()
    out.append(text[at:])
    return "".join(out), replaced


#: digit+space+counter; 1-syllable counter joins only before non-Hangul/particle (3 일반인 stays)
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
#: Latin/digit + space + particle
_PARTICLE = re.compile(
    r"([A-Za-z0-9)]) (은|는|이|가|을|를|의|와|과|로|으로|에|에서|도|만|보다|처럼|까지|부터)"
    r"(?![가-힣])"
)


def tidy_spacing(text: str) -> str:
    """Close spaces before counters and particles."""
    if not text:
        return text
    text = _COUNTER_LONG.sub(r"\1\2", text)
    text = _COUNTER_SHORT.sub(r"\1\2", text)
    return _PARTICLE.sub(r"\1\2", text)


__all__ = ["read_back", "tidy_spacing"]


#: Cyrillic inside a Korean word: sampling slip
_STRAY = re.compile(r"[\u0400-\u04FF]")
_MIXED_WORD = re.compile(r"[가-힣\u0400-\u04FF]*[가-힣][가-힣\u0400-\u04FF]*")


#: common slip loanwords, when the document lacks a clean spelling
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
    """Word as jamo sounds (Cyrillic mapped to nearest jamo, silent ㅇ dropped)."""
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


#: Japanese kana run
_KANA = re.compile(r"[\u3040-\u30ff][\u3040-\u30ff\u30fc]*")
#: frequent kana slips at a Korean sentence end, read as Korean
_KANA_KO = {
    "できません": " 할 수 없습니다", "できます": " 할 수 있습니다", "ありません": " 없습니다",
    "あります": " 있습니다", "です": "입니다", "でした": "이었습니다", "ください": "주세요",
    "など": " 등", "について": "에 대해",
}
_SENTENCE = re.compile(r"[^.!?。\n]*[.!?。]?")


def repair_kana(text: str, vocabulary: str = "", *,
                keep_sentences: bool = False) -> tuple[str, list[str]]:
    """(text, changed): Japanese kana slipped into Korean prose read as Korean, or its
    sentence dropped. Text or vocabulary written in Japanese is left alone. `keep_sentences`
    (a chat answer, where a dropped sentence may be the step the person asked for): an
    unknown kana run goes, its sentence stays."""
    if not _KANA.search(text or "") or _KANA.search(vocabulary or ""):
        return text, []
    if len(re.findall(r"[가-힣]", text)) < len(re.findall(r"[\u3040-\u30ff]", text)):
        return text, []
    keep = _protected(text)
    changed: list[str] = []

    def mend(m: re.Match) -> str:
        sentence = m.group(0)
        if not _KANA.search(sentence) or any(a <= m.start() < b for a, b in keep):
            return sentence
        fixed = _KANA.sub(lambda k: _KANA_KO.get(k.group(0), k.group(0)), sentence)
        fixed = re.sub(r"\s+ (?=할 수|있습니다|없습니다|등\b)", " ", fixed)
        if _KANA.search(fixed):  # unknown kana: the run goes in a chat answer, else the sentence
            fixed = re.sub(r" {2,}", " ", _KANA.sub("", fixed)) if keep_sentences else ""
        changed.append(f"{sentence.strip()}→{fixed.strip()}")
        return fixed

    out = _SENTENCE.sub(mend, text)
    return (re.sub(r"(?<=\S) {2,}(?=\S)", " ", out) if changed else text), changed


#: a whole Cyrillic word between Korean words (「흥미 или 즐거움」): read as Korean
_CYRILLIC_WORD = re.compile(r"(?<![\u0400-\u04FFA-Za-z])[\u0400-\u04FF]+(?![\u0400-\u04FFA-Za-z])")
_CYRILLIC_KO = {"или": "또는", "и": "및", "но": "하지만", "да": "네", "нет": "아니요",
                "для": "위한", "в": "", "на": ""}


def _lone_cyrillic(text: str, vocabulary: str) -> tuple[str, list[str]]:
    """Whole Cyrillic words in a Korean text read as Korean when known, else taken out; a text
    or vocabulary with Russian of its own (more than a few such words) is left alone."""
    words = [m.group(0) for m in _CYRILLIC_WORD.finditer(text or "")
             if not re.search(r"[가-힣]", text[max(0, m.start() - 1):m.start()]
                              + text[m.end():m.end() + 1])]
    if not words or _CYRILLIC_WORD.search(vocabulary or "") or len(words) > 3 \
            or len(re.findall(r"[가-힣]", text)) < 20:
        return text, []
    keep = _protected(text)
    changed: list[str] = []

    def mend(m: re.Match) -> str:
        word = m.group(0)
        if re.search(r"[가-힣]", text[max(0, m.start() - 1):m.start()]
                     + text[m.end():m.end() + 1]):
            return word  # inside a Korean word: the mixed-word repair reads it
        if any(a <= m.start() < b for a, b in keep):
            return word  # code or a gloss
        fixed = _CYRILLIC_KO.get(word.lower(), "")
        changed.append(f"{word}→{fixed}")
        return fixed or _GONE

    return _closed(_CYRILLIC_WORD.sub(mend, text)), changed


def repair_mixed_script(text: str, vocabulary: str = "", *,
                        keep_sentences: bool = False) -> tuple[str, list[str]]:
    """(text, changed): Cyrillic-tainted Korean words fixed or stripped; Greek kept;
    Japanese kana slips read as Korean or dropped (`keep_sentences`: only the kana run goes).
    A tainted word with one Korean syllable left and no clean match goes whole
    (「턱 인тягивание(chin tuck)」 → 「턱(chin tuck)」, not 「턱 인(chin tuck)」)."""
    import difflib

    text, kana = repair_kana(text, vocabulary, keep_sentences=keep_sentences)
    if not _STRAY.search(text or ""):
        return text, kana
    text, lone = _lone_cyrillic(text, vocabulary)
    kana += lone
    if not _STRAY.search(text or ""):
        return text, kana
    clean_words = {
        w for w in re.findall(r"[가-힣]{2,}", f"{text} {vocabulary} {_COMMON_TERMS}")
    }
    changed: list[str] = []

    def mend(m: re.Match) -> str:
        word = m.group(0)
        if not _STRAY.search(word) or not re.search(r"[가-힣]", word):
            return word
        skeleton = _STRAY.sub("", word)
        # compared by sound: right sound, wrong alphabet
        heard = _jamo(word)
        candidates = [w for w in clean_words if abs(len(w) - len(skeleton)) <= 2]
        scored = sorted(
            ((difflib.SequenceMatcher(None, heard, _jamo(w)).ratio(), w) for w in candidates),
            reverse=True,
        )
        fixed = scored[0][1] if scored and scored[0][0] >= 0.6 else skeleton
        if fixed == skeleton and len(skeleton) <= 1 and len(_STRAY.findall(word)) >= 3:
            changed.append(f"{word}→")
            return _GONE
        changed.append(f"{word}→{fixed}")
        return fixed

    return _closed(_MIXED_WORD.sub(mend, text)), kana + changed


#: where a word was taken out whole
_GONE = "\x00"


def _closed(text: str) -> str:
    """No gap where a word went: 「턱 (chin tuck)」 → 「턱(chin tuck)」, two spaces → one."""
    if _GONE not in text:
        return text
    text = re.sub(r" ?\x00(?=\()", "", text)
    text = re.sub(r"(?<=\S) \x00 (?=\S)", " ", text)
    return text.replace(_GONE, "")
