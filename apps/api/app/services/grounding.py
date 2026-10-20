"""Pre-writing questions when document material is short; asking turns write nothing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.workspace_context import ContextFile

#: truncation shortfall not worth asking about
_TRIVIAL_SHORTFALL = 2_000


@dataclass(frozen=True, slots=True)
class Question:
    """One pre-writing question; `options` are suggestions, free answers accepted."""

    id: str
    question: str
    options: list[str] = field(default_factory=list)
    #: shown under the question (e.g. how much of a file arrived)
    detail: str = ""

    def wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "options": list(self.options),
            "detail": self.detail,
        }


def file_shortfalls(files: tuple[ContextFile, ...]) -> list[ContextFile]:
    """Attachments not delivered whole, beyond trivial shortfall."""
    out = []
    for file in files:
        if file.state == "included":
            continue
        if file.state == "truncated" and file.total_chars - file.kept_chars < _TRIVIAL_SHORTFALL:
            continue
        out.append(file)
    return out


def questions_for(files: list[ContextFile]) -> list[Question]:
    """At most one question each for unreadable and partial files."""
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


#: request naming an attachment as source (the act, not the bare noun)
_NAMES_AN_ATTACHMENT = re.compile(
    r"첨부(?:한|된|해\s?준|해\s?드린|해\s?놓은)"
    r"|(?:올린|올려\s?준|업로드한|보낸)\s*(?:파일|자료|문서|문건)"
    r"|attached\s+(?:file|document|paper)",
    re.I,
)


def missing_attachment(request: str, files: tuple[ContextFile, ...]) -> Question | None:
    """Question when the request names an attachment and none arrived."""
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


#: outline rule: plan bare topics; ask only if subject absent or named source missing
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

#: replaces `ASK_RULE` after "있는 자료로 진행"
PROCEED_RULE = """- Do not ask back. The user already chose "있는 자료로 진행". Build from the
  material there is. Do not make up figures, dates, names or organizations not in the
  material; leave a bracket blank such as 「[담당자]」. Make the parts the material does not
  cover show as 「자료 없음」 in the outline."""


#: a document's head field the sender fills in (「공문의 시행(발송)일이 언제입니까?」)
_HEAD_FIELD_ASK = re.compile(
    r"(?:시행|발송|발신|접수)\s?\(?[^)\n]{0,6}\)?\s?(?:일|일자|날짜|번호)|문서\s?번호")
#: whether to do what was asked, offering a blank instead (「계산해 넣을지, 자리만
#: 틀어둘까요」 with 「[수치]로 빈칸 두고」)
_BLANK_OPTION = re.compile(r"\[[^\]\n]{1,12}\]|빈칸|자리만|비워\s?두")


def _needless(question: str, options: list[str], request: str) -> bool:
    """Whether a planner question is one the card never asks: a head field, whether to
    leave blank what the request asks for, or one whose offered answer the request already
    says word for word."""
    if _HEAD_FIELD_ASK.search(question):
        return True
    if any(_BLANK_OPTION.search(o) for o in options) and re.search(
            r"(?:할지|할까요|넣을지|채울지|드릴까요|둘까요)", question):
        return True
    said = re.sub(r"[\s()（）·,.]", "", request or "")
    return any(len(core) >= 6 and core in said
               for core in (re.sub(r"[\s()（）·,.]", "", o) for o in options))


def parse_needs(text: str, request: str = "") -> list[Question] | None:
    """Questions from a `{"needs": [...]}` reply, or `None`; questions the card never asks
    (`_needless`) dropped."""
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
        question = _uncited(question)
        options = [o for o in map(_uncited, options) if o and not _INVENTS.search(o)]
        if not question or _needless(question, options, request):
            continue
        out.append(Question(id=f"ask{i}", question=question[:200], options=options[:4]))
    return out or None


#: reference numbers the person never saw
_CITES = re.compile(
    r"\s{0,2}\(?\[\d{1,3}(?:\s{0,2},\s{0,2}\d{1,3}){0,10}\]"
    r"(?:\s{0,2}[~∼,–-]\s{0,2}\[\d{1,3}\]){0,20}\)?")
#: options inventing missing material (card asks, never invents)
_INVENTS = re.compile(
    r"가상|임의|지어\s{0,2}(?:내|서|넣)|꾸며|가정(?:하여|해서|해)\s{0,2}(?:작성|채|넣|진행|써)"
    r"|예시\s{0,2}(?:값|수치|데이터|내용)")


def _uncited(text: str) -> str:
    return re.sub(r"\s{2,}", " ", _CITES.sub("", text)).strip()


def merge_answers(request: str, answers: dict[str, str]) -> str:
    """Answers appended to the request as conditions."""
    # typed reply = answer and note; one line
    said = list(dict.fromkeys(text.strip() for text in answers.values() if text and text.strip()))
    if not said:
        return request
    return request + "\n\n덧붙인 조건:\n" + "\n".join(f"- {line}" for line in said)


def focus_terms(answers: dict[str, str]) -> str:
    """`focus` answer for excerpting a long file; empty if the read part was chosen."""
    text = (answers.get("focus") or "").strip()
    if not text or text.startswith("읽은 앞부분"):
        return ""
    return text


#: the request names its own subject (「제목: …」, 「주제는 …」, 「「…」 보고서」)
_STATED_SUBJECT = re.compile(
    r"(?m)(?:^|(?<=[\s.,;(]))(?:제목|주제|발표\s{0,2}제목|논문\s{0,2}제목)\s{0,2}[:：]\s*\S"
    r"|주제는\s{0,2}\S|「[^」\n]{4,}」"
)
#: request plus material this long carries its subject
_SUBSTANTIAL_CHARS = 300


def subject_missing(text: str, request: str, material: str = "") -> bool:
    """Whether the planner's `subject` shares no word with request or material."""
    obj = re.search(r"\{.*\}", text, re.S)
    if not obj:
        return False
    try:
        data = json.loads(obj.group(0))
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict) or "subject" not in data:
        return False
    # attachment counts as request words
    compact = re.sub(r"\s+", "", request + material)
    if _STATED_SUBJECT.search(request + "\n" + material) or len(compact) >= _SUBSTANTIAL_CHARS:
        # a titled request, or one that brings its material: the planner just left it blank
        # (「제목: … 초록: … 실험 결과 …」 was asked 「무엇에 대한 발표입니까?」)
        return False
    subject = str(data.get("subject") or "").strip()
    if not subject:
        return True
    words = [w for w in re.split(r"[\s,.·/()]+", subject) if len(w) >= 2]
    return bool(words) and not any(w in compact for w in words)
