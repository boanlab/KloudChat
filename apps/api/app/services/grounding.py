"""Whether a document request has enough under it to be written.

The rule this replaces was one line in three outline prompts:

    요청이 한 단어여도 되묻지 마라. … 자료가 부족하다는 답은 하지 마라.

It exists for a good reason. "전이학습으로 발표자료" is a legitimate request and
should produce a deck, not an interview. But it also forbade the model from
saying the one thing that mattered when somebody attached a 74,000-character
paper and 24,000 characters of it arrived: that it was working from a third of
the source. Told not to mention thin material and asked for a presentation
about a paper it could not see the results of, a model does the only thing left
— it invents one. That is where 'AI 응답 생성의 원리' and '실제 논문 부재의
현실' came from, in place of a deck somebody had already made.

So the rule is now conditional, and the condition is checked in two places.

**Here, from what actually happened to the files.** The server knows each
attachment's fate exactly — the name, how much was kept, how much there was —
and can ask a better question from that than a model guessing at it can. This
is also what stops the model explaining the failure wrongly: it is told the
facts rather than left to infer them from a gap in its context.

**In the outline call, for what only the model can see.** A request whose
subject the material does not cover at all is not something a character count
can detect.

Neither path invents anything and neither writes anything. A turn that asks is
a turn that produced no document, which is precisely why the deck already on
screen survives it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.workspace_context import ContextFile

#: Below this, a file is short enough that "we only read part of it" is not
#: worth stopping over — a page and a half missing off the end of a form.
_TRIVIAL_SHORTFALL = 2_000


@dataclass(frozen=True, slots=True)
class Question:
    """One thing to ask before writing.

    `options` are suggestions, not a closed set: they exist so the common
    answer is one click, and every question stays answerable in prose. A
    question with no options is one where guessing at answers would be worse
    than an empty box.
    """

    id: str
    question: str
    options: list[str] = field(default_factory=list)
    #: Said under the question, when the reason to ask is a fact rather than a
    #: preference — how much of which file arrived, for instance.
    detail: str = ""

    def wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "options": list(self.options),
            "detail": self.detail,
        }


def file_shortfalls(files: tuple[ContextFile, ...]) -> list[ContextFile]:
    """Attachments that did not reach the model whole, worth stopping over."""
    out = []
    for file in files:
        if file.state == "included":
            continue
        if file.state == "truncated" and file.total_chars - file.kept_chars < _TRIVIAL_SHORTFALL:
            continue
        out.append(file)
    return out


def questions_for(files: list[ContextFile]) -> list[Question]:
    """What to ask about attachments that arrived short.

    One question, not one per file: the decision is the same for all of them —
    write from what arrived, or say which part matters — and asking it three
    times over three attachments is a form.

    The options are only what this can actually honour. 'Read the whole thing
    in parts' is not offered, because nothing here does that, and an option
    that quietly does something else is worse than a shorter list.
    """
    if not files:
        return []

    unreadable = [f for f in files if f.state == "unreadable"]
    partial = [f for f in files if f.state in ("truncated", "omitted")]

    out: list[Question] = []
    if unreadable:
        names = ", ".join(f.name for f in unreadable)
        out.append(
            Question(
                id="unreadable",
                question="읽지 못한 파일이 있습니다. 어떻게 할까요?",
                detail=f"{names} — 내용을 꺼내지 못했습니다. 스캔본이라면 OCR 이 필요합니다.",
                options=[
                    "이 파일 없이 나머지로 진행",
                    "내용을 직접 붙여넣겠습니다",
                ],
            )
        )
    if partial:
        detail = " · ".join(
            (
                f"{f.name} — {f.total_chars:,}자 중 {f.kept_chars:,}자만 반영"
                if f.state == "truncated"
                else f"{f.name} — 분량을 넘겨 이번 요청에 들어가지 못함"
            )
            for f in partial
        )
        out.append(
            Question(
                id="focus",
                question="파일을 다 읽지 못했습니다. 어느 부분으로 만들까요?",
                detail=detail,
                options=["읽은 앞부분만으로 진행"],
            )
        )
    return out


#: What the outline call is allowed to do instead of planning.
#:
#: Deliberately narrow. The old rule's instinct was right — a bare topic is a
#: request, not an omission — so this permits a question only where the request
#: names a source the material does not answer for. Everything else still gets
#: planned without being asked about.
ASK_RULE = """- 주제만 주어졌으면 되묻지 말고 그 주제를 처음 접하는 사람에게 설명하는
  것으로 네가 알아서 구성하라. 한 단어짜리 요청도 마찬가지다.
- 다만 요청이 특정 자료(첨부한 논문·문서·데이터)를 근거로 만들라는 것인데
  참고 자료에 그 내용이 없거나, 요청한 대목이 자료에 담겨 있지 않으면
  지어내지 말고 되물어라. 그때는 구성 대신 아래 형식으로만 답하라.
  {{"needs": [{{"question": "무엇을 알아야 하는지 한 줄",
                "options": ["고를 만한 답", "다른 답"]}}]}}
  질문은 최대 3개. 사용자가 한 번에 답할 수 있는 것만 물어라."""

#: What replaces it on the pass after "있는 자료로 진행".
#:
#: Suppressing the question at the other end is not enough: what comes back is
#: then a question where a plan was expected, and the parse fails instead of the
#: loop. The model has to be told, in the one place it is listening.
PROCEED_RULE = """- 되묻지 마라. 참고 자료가 부족해도 그 자리에서 알아서 구성하라.
  사용자는 이미 "있는 자료로 진행"을 골랐다. 자료에 없는 대목은 지어내지 말고
  일반적인 설명으로 채우되, 무엇을 그렇게 채웠는지는 구성에 드러나게 하라."""


def parse_needs(text: str) -> list[Question] | None:
    """The outline call's questions, when it asked instead of planning.

    Returns `None` for an ordinary outline, so the caller can tell "asked" from
    "planned" without the two sharing a shape.
    """
    block = re.search(r"\{.*\}", text, re.S)
    if not block:
        return None
    try:
        data = json.loads(block.group(0))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("needs")
    if not isinstance(raw, list) or not raw:
        return None

    out: list[Question] = []
    for i, item in enumerate(raw[:3]):
        if isinstance(item, str):
            question, options = item, []
        elif isinstance(item, dict):
            question = str(item.get("question") or "").strip()
            options = [str(o).strip() for o in (item.get("options") or []) if str(o).strip()]
        else:
            continue
        if not question:
            continue
        out.append(Question(id=f"ask{i}", question=question[:200], options=options[:4]))
    return out or None


def merge_answers(request: str, answers: dict[str, str]) -> str:
    """Folds what the person answered back into the request.

    Appended rather than substituted: the original sentence is what they asked
    for, and the answers are conditions on it. A rewritten request is one the
    person never checked.
    """
    said = [text.strip() for text in answers.values() if text and text.strip()]
    if not said:
        return request
    return request + "\n\n덧붙인 조건:\n" + "\n".join(f"- {line}" for line in said)


def focus_terms(answers: dict[str, str]) -> str:
    """What the person said to concentrate on, for excerpting a long file.

    Empty when they chose to go with what was already read — the head of the
    document is then the right excerpt and searching for terms would move it
    for no reason.
    """
    text = (answers.get("focus") or "").strip()
    if not text or text.startswith("읽은 앞부분"):
        return ""
    return text
