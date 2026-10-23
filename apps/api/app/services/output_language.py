"""An agent's output language (`Agent.output_language`): the language every turn and document
of its conversations is written in, unless the turn itself names another language."""

from __future__ import annotations

import re
from typing import Any

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


_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")
#: the checks' Korean mark, as `value_guard.MARK` and the copy check write it
_KO_MARK = re.compile(r"([(\[])\s?확인\s?필요\s?([)\]])")
EN_MARK = "needs checking"


def reads_english(text: str) -> bool:
    """Whether `text` is written in a Latin-script language, not Korean: its own words, with
    links, code and the checks' own marks set aside."""
    body = re.sub(r"```[\s\S]*?(?:```|\Z)|`[^`\n]*`|\]\([^)\s]*\)|https?://\S+", " ", text or "")
    body = _KO_MARK.sub(" ", body)
    latin = len(_LATIN.findall(body))
    return latin >= 40 and len(_HANGUL.findall(body)) * 10 < latin


def typed_in_english(text: str) -> bool:
    """Whether a short ask is typed in English: Latin words and no Hangul (「Make slide 3 a
    table.」 is too short for `reads_english`)."""
    return not _HANGUL.search(text or "") and len(re.findall(r"[A-Za-z]{2,}", text or "")) >= 3


#: a pasted memo line: a list item, a numbered point, a table row
_MEMO_ITEM = re.compile(r"[-*•·□○◦▪]\s|\d{1,2}[.)]\s|\|")
#: words before a colon: a memo's label (「Next meeting:」) or an ask (「회의록 만들어줘:」)
_LABEL = re.compile(r"([가-힣A-Za-z][^:：\n]{0,24})[:：]\s?\S")
#: where a sentence or line ends
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s|\n")


def _language_of(text: str) -> str:
    if reads_english(text) or typed_in_english(text):
        return "en"
    return "ko" if _HANGUL.search(text or "") else ""


def asked(typed: str) -> str:
    """The language the person asked in (``en``, ``ko``, or ``""``), from what they typed: its
    first and last sentence that is no memo item, and when these differ the shorter one, the
    ask put before or after pasted material. 「아래 메모로 회의록을 만들어줘.」 over an English
    memo is Korean; an English memo sent alone is English."""
    text = (typed or "").split("\n\n덧붙인 조건:", 1)[0]
    pieces = [p.strip() for p in _SENTENCE_END.split(text) if p.strip()]
    if not pieces:
        return ""
    # a memo item (「- 화: …」) is material, not the ask; a line opening with 「…:」 reads by
    # the words before its colon
    asks = [m.group(1) if (m := _LABEL.match(p)) else p
            for p in (pieces[0], pieces[-1]) if not _MEMO_ITEM.match(p)] or [pieces[0]]
    first, last = asks[0], asks[-1]
    ends = {_language_of(first), _language_of(last)} - {""}
    if len(ends) == 1:
        return ends.pop()
    if not ends:
        return _language_of(text)
    return _language_of(min(first, last, key=len))


def asked_condition(code: str) -> str:
    """Request condition for a document in the language the person asked in; empty for none."""
    name = LANGUAGES.get(code, "")
    return (f"Write the whole document in {name}, the language the person asked in (질문 언어), "
            "whatever language the material is in.") if name else ""


def localize_marks(text: str) -> str:
    """An answer in English with the checks' 「(확인 필요)」 as 「(needs checking)」; a Korean
    answer as it is."""
    if not text or "확인" not in text or not reads_english(text):
        return text
    return _KO_MARK.sub(lambda m: f"{m.group(1)}{EN_MARK}{m.group(2)}", text)


def english_marks(value: Any) -> Any:
    """`value` (text, or lists and dicts of it) with the checks' marks in English."""
    if isinstance(value, str):
        return _KO_MARK.sub(lambda m: f"{m.group(1)}{EN_MARK}{m.group(2)}", value)
    if isinstance(value, list):
        return [english_marks(item) for item in value]
    if isinstance(value, dict):
        return {key: english_marks(item) for key, item in value.items()}
    return value


__all__ = ["AUTO", "EN_MARK", "HEADING", "LANGUAGES", "asked", "asked_condition",
           "asks_language", "condition",
           "english_marks", "localize_marks", "name_of", "normalize", "reads_english", "rule"]
