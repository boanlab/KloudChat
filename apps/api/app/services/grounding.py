"""Questions to ask before writing a document whose material is short.

Attachment problems are checked here; a missing subject via `ASK_RULE` in the outline call.
A turn that asks writes nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.workspace_context import ContextFile

#: Truncation shortfalls below this are not worth asking about.
_TRIVIAL_SHORTFALL = 2_000


@dataclass(frozen=True, slots=True)
class Question:
    """One thing to ask before writing; `options` are suggestions, free answers are accepted."""

    id: str
    question: str
    options: list[str] = field(default_factory=list)
    #: Shown under the question: the fact behind it, e.g. how much of a file arrived.
    detail: str = ""

    def wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "options": list(self.options),
            "detail": self.detail,
        }


def file_shortfalls(files: tuple[ContextFile, ...]) -> list[ContextFile]:
    """Attachments that did not reach the model whole, beyond a trivial shortfall."""
    out = []
    for file in files:
        if file.state == "included":
            continue
        if file.state == "truncated" and file.total_chars - file.kept_chars < _TRIVIAL_SHORTFALL:
            continue
        out.append(file)
    return out


def questions_for(files: list[ContextFile]) -> list[Question]:
    """At most one question for unreadable files and one for partial ones."""
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


#: A request naming an attachment as its source: the act (첨부한, 올린 파일), not
#: the bare noun, which may be a subject.
_NAMES_AN_ATTACHMENT = re.compile(
    r"첨부(?:한|된|해\s?준|해\s?드린|해\s?놓은)"
    r"|(?:올린|올려\s?준|업로드한|보낸)\s*(?:파일|자료|문서|문건)"
    r"|attached\s+(?:file|document|paper)",
    re.I,
)


def missing_attachment(request: str, files: tuple[ContextFile, ...]) -> Question | None:
    """A question when the request names an attachment as its source and none arrived."""
    if files or not _NAMES_AN_ATTACHMENT.search(request or ""):
        return None
    return Question(
        id="no_attachment",
        question=(
            "첨부한 파일을 근거로 만들라고 하셨는데, 이번 요청에는 파일이 실려 오지 않았습니다."
        ),
        detail=(
            "파일이 없으면 요청 문장만 남고, 그 문장으로 만든 문서는 첨부와 무관한 내용이 됩니다."
        ),
        options=[
            "파일을 다시 첨부하겠습니다",
            "내용을 직접 붙여넣겠습니다",
            "파일 없이 요청 문장만으로 진행",
        ],
    )


#: Outline-prompt rule: plan a bare topic without asking; ask only when the
#: subject is absent or a named source is not in the material.
ASK_RULE = """- If only a subject is given, do not ask back; build it yourself as an explanation
  for someone meeting the subject for the first time. The same goes for a one-word request.
- **If there is no subject at all, ask back.** When the request names only the document's
  **use and form**, like 「처장님 결재용 한 장 보고 — 결정할 것, 대안 둘, 위험, 권고」, and does
  not say what is being decided, what the alternatives are or what the subject is, that is
  not yours to pick. A document filled with a matter you chose, such as 「전산망 교체」,
  becomes false the moment it goes up the approval line. Then ask what the decision is
  about and what the alternatives are.
- If the request is to build on specific material (an attached paper, document or data)
  and the references do not contain it, or the requested part is not in the material, do
  not make it up; ask back. In either case, answer only in the format below instead of an
  outline.
  {{"needs": [{{"question": "무엇을 알아야 하는지 한 줄",
                "options": ["고를 만한 답", "다른 답"]}}]}}
  At most 3 questions. Ask only what the user can answer in one go. Write the questions
  and options in Korean, like the example."""

#: Replaces `ASK_RULE` on the pass after the user chose "있는 자료로 진행".
PROCEED_RULE = """- Do not ask back. The user already chose "있는 자료로 진행". Build from the
  material there is. Do not make up figures, dates, names or organizations not in the
  material; leave a bracket blank such as 「[담당자]」. Make the parts the material does not
  cover show as 「자료 없음」 in the outline."""


def parse_needs(text: str) -> list[Question] | None:
    """Questions from a `{"needs": [...]}` reply, or `None` for an ordinary outline."""
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
    """Appends the answers to the request as conditions; the original sentence is kept."""
    # A typed reply is both the card's answer and the note on it; one line is enough.
    said = list(dict.fromkeys(text.strip() for text in answers.values() if text and text.strip()))
    if not said:
        return request
    return request + "\n\n덧붙인 조건:\n" + "\n".join(f"- {line}" for line in said)


def focus_terms(answers: dict[str, str]) -> str:
    """The `focus` answer for excerpting a long file; empty if the read part was chosen."""
    text = (answers.get("focus") or "").strip()
    if not text or text.startswith("읽은 앞부분"):
        return ""
    return text


def subject_missing(text: str, request: str, material: str = "") -> bool:
    """Whether the planner's `subject` uses no word found in the request or material."""
    obj = re.search(r"\{.*\}", text, re.S)
    if not obj:
        return False
    try:
        data = json.loads(obj.group(0))
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict) or "subject" not in data:
        return False
    subject = str(data.get("subject") or "").strip()
    if not subject:
        return True
    # The attachment counts as the request's words.
    compact = re.sub(r"\s+", "", request + material)
    words = [w for w in re.split(r"[\s,.·/()]+", subject) if len(w) >= 2]
    return bool(words) and not any(w in compact for w in words)
