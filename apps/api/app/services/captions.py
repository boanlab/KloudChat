"""Words that caption and point at a document's tables and figures, per document language:
「**표 1. 실행 항목**」 「*그림 2. 처리 흐름*」 and 「표 1과 같습니다」 in a Korean document,
「**Table 1. Action Items**」 「*Figure 2. Flow*」 and 「See Table 1」 in an English one.

Code that writes a caption or a reference takes its words from here; code that reads one
takes `TABLE`/`FIGURE`/`EITHER`, which match both languages, so an English document's
captions are read the same as a Korean one's."""

from __future__ import annotations

from collections.abc import Iterable

from app.services import output_language

KO, EN = "ko", "en"
#: either language's caption word, for readers
TABLE = r"(?:표|[Tt]able)"
FIGURE = r"(?:그림|[Ff]igure)"
EITHER = r"(?:표|그림|[Tt]able|[Ff]igure)"

#: canonical kind (「표」, 「그림」) → the word written in each language
_WORD = {KO: {"표": "표", "그림": "그림"}, EN: {"표": "Table", "그림": "Figure"}}
#: a figure's title when none was given: a picture, a drawn diagram
_UNTITLED = {KO: {"그림": "그림", "도식": "도식"}, EN: {"그림": "Figure", "도식": "Diagram"}}


def language(text: str) -> str:
    """`en` for a body written in English (`output_language.reads_english`), else `ko`."""
    return EN if output_language.reads_english(text or "") else KO


def of_sections(sections: Iterable[dict]) -> str:
    """The document's caption language, read from all its sections' text."""
    return language("\n".join(str(s.get("content") or "") for s in sections))


def kind(word: str) -> str:
    """Canonical kind of a caption word: 「표」 for 「표」 or 「Table」, 「그림」 for 「그림」 or
    「Figure」."""
    return "표" if word.lower() in ("표", "table") else "그림"


def word(of: str, lang: str) -> str:
    """The word for kind `of` (「표」/「그림」, or either language's word) in `lang`."""
    return _WORD[EN if lang == EN else KO][kind(of)]


def name(of: str, number: int | str, lang: str) -> str:
    """「표 2」 / 「Table 2」."""
    return f"{word(of, lang)} {number}"


def untitled(what: str, lang: str) -> str:
    """A figure's title when none was given (`what`: 「그림」 or 「도식」)."""
    return _UNTITLED[EN if lang == EN else KO][what]


def table_caption(number: int, title: str, lang: str) -> str:
    """「**표 1. 제목**」 / 「**Table 1. Title**」; the number alone without a title."""
    label = name("표", number, lang)
    return f"**{label}. {title}**" if title else f"**{label}**"


def figure_caption(number: int, title: str, lang: str) -> str:
    """「*그림 1. 제목*」 / 「*Figure 1. Title*」 under a drawn diagram."""
    return f"*{name('그림', number, lang)}. {title}*"


def picture_alt(number: int, title: str, lang: str) -> str:
    """A picture's alt text that is its caption: 「그림 1. 제목」 / 「Figure 1. Title」."""
    return f"{name('그림', number, lang)}. {title}"


def in_english(label: str) -> bool:
    """Whether a reference (「Table 2」) is written in English."""
    return label[:1].isascii()


def pointer(label: str) -> str:
    """The bracket citing `label` at a sentence's end: 「(표 1)」 right after a Korean sentence,
    「 (Table 1)」 after an English one."""
    return f" ({label})" if in_english(label) else f"({label})"


def lead(label: str, title: str) -> str:
    """An English line leading into a block the text never names: 「See Table 1 for Action
    Items.」, 「See Table 1.」 without a title."""
    return f"See {label} for {title}." if title else f"See {label}."


__all__ = ["EITHER", "EN", "FIGURE", "KO", "TABLE", "figure_caption", "in_english",
           "kind", "language", "lead", "name", "of_sections", "picture_alt", "pointer",
           "table_caption", "untitled", "word"]
