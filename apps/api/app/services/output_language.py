"""An agent's output language (`Agent.output_language`): the language every turn and document
of its conversations is written in, unless the turn itself names another language."""

from __future__ import annotations

import re

AUTO = "auto"

#: codes the agent form offers, with the name the writer is told
LANGUAGES: dict[str, str] = {
    "ko": "Korean (한국어)",
    "en": "English",
    "ja": "Japanese (日本語)",
    "zh": "Chinese (中文)",
}

#: names typed instead of a code
_ALIASES: dict[str, str] = {
    "korean": "ko", "한국어": "ko", "한글": "ko", "kor": "ko",
    "english": "en", "영어": "en", "영문": "en", "eng": "en",
    "japanese": "ja", "일본어": "ja", "日本語": "ja", "jp": "ja",
    "chinese": "zh", "중국어": "zh", "中文": "zh", "cn": "zh",
    "자동": AUTO, "": AUTO,
}

#: a language named in free text: letters, spaces and a few separators, one line
_FREE = re.compile(r"[^\w\s()·,./-]", re.UNICODE)

#: the turn asks for a language itself: 「영어로」「일본어로 써 줘」「in English」
_ASKS = re.compile(
    r"(?:한국어|한글|우리말|영어|영문|일본어|일어|중국어|중문|독일어|독어|프랑스어|불어|스페인어"
    r"|베트남어|러시아어|태국어|인도네시아어|아랍어|포르투갈어|이탈리아어|몽골어)\s{0,2}(?:로|으로)(?!\s{0,2}된)"
    r"|\b(?:in|into)\s+(?:English|Korean|Japanese|Chinese|German|French|Spanish|Vietnamese"
    r"|Russian|Thai|Indonesian|Arabic|Portuguese|Italian)\b",
    re.I,
)


def normalize(value: str | None) -> str:
    """Stored form: a code, `auto`, or a cleaned one-line language name (≤40 chars)."""
    text = " ".join(str(value or "").split())
    key = text.lower()
    if key in LANGUAGES or key == AUTO:
        return key
    if key in _ALIASES:
        return _ALIASES[key]
    return _FREE.sub("", text).strip()[:40] or AUTO


def name_of(value: str | None) -> str:
    """Language name the writer is told; empty for `auto`."""
    code = normalize(value)
    if code == AUTO:
        return ""
    return LANGUAGES.get(code, code)


def asks_language(text: str) -> bool:
    """The turn names the language to write in, which wins over the agent's setting."""
    return bool(_ASKS.search(text or ""))


#: heading of the rule block; `context.build_messages` reads it to skip its own language rule
HEADING = "# 출력 언어"


def rule(value: str | None) -> str:
    """Trusted rule for the agent's language; empty for `auto`."""
    name = name_of(value)
    if not name:
        return ""
    korean = normalize(value) == "ko"
    return (
        f"{HEADING}\n"
        f"This agent writes in {name}. Write the whole answer or document in {name} — title, "
        "headings, table headers, lists, speaker notes, captions — whatever language the "
        "question or the reference material is in. This overrides every other instruction "
        "that picks Korean or the request's language. Keep quotations, proper nouns and "
        "names of laws or products as in the original. Only when the person's own message "
        "asks for another language (「영어로」, 'in Korean'), or asked earlier in this "
        "conversation for every answer in another language and has not withdrawn it, follow "
        "that instead."
        + ("" if korean else " The Korean style rules (합니다체, 한글 표기) do not apply to "
           "text in another language.")
    )


def condition(value: str | None) -> str:
    """Request condition for a document writer; empty for `auto`."""
    name = name_of(value)
    return f"Write the whole document in {name} (에이전트 출력 언어)." if name else ""


__all__ = ["AUTO", "HEADING", "LANGUAGES", "asks_language", "condition", "name_of", "normalize",
           "rule"]
