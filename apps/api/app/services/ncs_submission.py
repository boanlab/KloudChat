"""Conservative direct-answer syntax, never a choice inferred from a problem's numbers."""

from __future__ import annotations

import re

_BARE = re.compile(r"(10|[1-9])[ \t]{0,20}(?:번)?[.!]?")
_KOREAN = re.compile(
    r"(?:^|[.!?\n])[ \t]{0,20}(?:제|내)[ \t]{0,20}(?:답|선택)(?:은|는)"
    r"[ \t]{0,20}(10|[1-9])[ \t]{0,20}번"
    r"(?:[ \t]{1,20}[0-9]{1,10}(?:\.[0-9]{1,10})?%)?"
    r"[ \t]{0,20}(?:입니다|이다|이에요|이야)?(?=[.!?\n]|$)"
)
_ENGLISH = re.compile(
    r"(?:^|[.!?\n])[ \t]{0,20}my[ \t]{1,20}answer[ \t]{1,20}is"
    r"[ \t]{1,20}(10|[1-9])(?=[.!?\n]|$)",
    re.IGNORECASE,
)
_UNCERTAIN = (
    "아니",
    "아닙",
    "아닌",
    "취소",
    "정정",
    "모르",
    "가정",
    "예시",
    "not ",
    "cancel",
    "example",
)


def submitted_choice_from_request(request: str) -> int | None:
    """Recognize an unambiguous supported declaration; otherwise do not auto-grade."""
    if not isinstance(request, str) or len(request) > 8192:
        return None
    text = request.strip()
    if match := _BARE.fullmatch(text):
        return int(match.group(1))
    if any(marker in text.lower() for marker in _UNCERTAIN):
        return None
    # Quoted examples are not the learner's own submission.
    if any(quote in text for quote in ('"', "'", "`", "“", "”", "‘", "’")):
        return None
    answers = {
        int(match.group(1)) for pattern in (_KOREAN, _ENGLISH) for match in pattern.finditer(text)
    }
    return next(iter(answers)) if len(answers) == 1 else None
