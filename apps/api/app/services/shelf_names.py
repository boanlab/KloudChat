"""Every user-facing 지식 서가 feature name the server writes (artifact and conversation titles,
export headings), in one place so a rename is one edit. The web keeps its own copy in
`apps/web/src/lib/shelfNames.ts`."""

from __future__ import annotations

#: the feature as a whole (working name)
SHELF = {"ko": "지식 서가", "en": "Knowledge shelf"}
STUDIO = {"ko": "스튜디오", "en": "Studio"}
NOTES = {"ko": "메모함", "en": "Notes"}

#: studio outputs by kind
KINDS: dict[str, dict[str, str]] = {
    "study_guide": {"ko": "학습 가이드", "en": "Study guide"},
    "faq": {"ko": "FAQ", "en": "FAQ"},
    "briefing": {"ko": "브리핑 문서", "en": "Briefing doc"},
    "timeline": {"ko": "타임라인", "en": "Timeline"},
    "glossary": {"ko": "용어집", "en": "Glossary"},
    "mindmap": {"ko": "마인드맵", "en": "Mind map"},
    "quiz": {"ko": "퀴즈", "en": "Quiz"},
    "flashcards": {"ko": "플래시카드", "en": "Flashcards"},
}


def kind_name(kind: str, language: str = "ko") -> str:
    names = KINDS.get(kind) or {}
    return names.get(language) or names.get("ko") or kind


__all__ = ["KINDS", "NOTES", "SHELF", "STUDIO", "kind_name"]
