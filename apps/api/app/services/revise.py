"""Routes a post-document instruction to its target parts; no rewriting here."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from app.services import settings_store

log = logging.getLogger(__name__)

#: Target cap before a whole-document pass.
MAX_TARGETS = 3

_PROMPT = """The user typed the sentence below while looking at a finished document.
Decide what they are asking to change.

문서 제목: {title}

문서의 구성:
{outline}

사용자 입력:
{message}

Rules:
- If it asks to change specific parts, put those parts' numbers in targets. At most {limit}.
- If it does not name the part but the content makes it guessable, pick that part.
- If the request spans the whole document (e.g. "전체적으로 더 간결하게", "말투를 바꿔줘"),
  set scope to "whole".
- If it asks to **add one or two new parts (sections/chapters) themselves** ("위험 장을 추가해 줘",
  "결론 앞에 한계 절을 넣어 줘"), set scope to "insert": the other parts stay as they are and
  only the new parts are written. A request to put a table, paragraph or figures **inside** an
  existing part ("비용 절에 3년 TCO 표를 넣어 줘") is not insert but "parts", revising that part.
  In after put the number of the part right before where the new part goes (0 for the very
  start), in names the new parts' titles, and in note the new part's content and form (every
  instruction such as tables, columns and rows, none left out). If no place was given, put it
  before the last part (closing or conclusion).
- If it asks to **change the structure itself**, set scope to "outline". This covers raising or
  lowering the number of parts ("20장으로", "3장 더", "절반으로"), adding, deleting or merging
  several parts, and reordering. Then write the structure after the change in note, with the
  count as the final number computed from the current structure (6 parts now and "3장 더" is
  "9장으로 늘린다").
- If it wants **a new document on a different subject**, set scope to "new". This applies only
  when the current document is to be thrown away and written again, not revised.
- When unsure, picking the one nearest part is better than "whole". Rewriting the whole
  document replaces wholesale the text the user was looking at.

note: one or two sentences on how to change that part. Do not copy the user's words; state the
change to make. Write note in Korean.

Reply with a JSON object only.
예: {{"scope": "parts", "targets": [3], "note": "분량을 절반으로 줄이고 표는 남긴다"}}
예: {{"scope": "insert", "after": 5, "names": ["위험"],
     "note": "표로. 열은 위험·영향·대응, 행은 …"}}"""


@dataclass(slots=True)
class Plan:
    """Instruction target and request."""

    #: `parts` | `whole` | `insert` | `outline` (re-plan from current) | `new` (from scratch).
    scope: str = "new"
    #: Zero-based, bounded part indices.
    targets: list[int] = field(default_factory=list)
    #: `insert`: after this index (-1 = start).
    after: int = -1
    #: `insert`: new part titles.
    names: list[str] = field(default_factory=list)
    #: Instruction as a task for the rewrite prompt.
    note: str = ""
    usage: dict[str, int] = field(default_factory=lambda: {"inputTokens": 0, "outputTokens": 0})
    #: what the rewrite is told the person typed, minus what code already did (a title)
    typed: str = ""
    #: each target's own asks, when every ask of the instruction names its part
    clauses: dict[int, list[str]] = field(default_factory=dict)
    #: lines of a summary asked at the document's start (written by code, not a rewrite)
    summary: int = 0

    @property
    def revises(self) -> bool:
        return self.scope in ("parts", "whole") and bool(self.targets)

    @property
    def inserts(self) -> bool:
        return self.scope == "insert" and bool(self.names)

    @property
    def restructures(self) -> bool:
        return self.scope == "outline"


def _parse(text: str, count: int, message: str) -> Plan:
    """Model JSON → bounded `Plan`; `new` when unusable."""
    block = text[text.find("{") : text.rfind("}") + 1] if "{" in text and "}" in text else ""
    try:
        parsed = json.loads(block)
    except (json.JSONDecodeError, ValueError):
        return Plan()
    if not isinstance(parsed, dict):
        return Plan()

    scope = str(parsed.get("scope") or "").strip().lower()
    note = str(parsed.get("note") or "").strip()[:600] or message.strip()[:600]
    if scope == "whole":
        return Plan(scope="whole", targets=list(range(count)), note=note)
    if scope == "outline":
        return Plan(scope="outline", note=note)
    if scope == "insert":
        names = [str(n).strip()[:80] for n in (parsed.get("names") or []) if str(n).strip()][:2]
        if not names:
            return Plan(scope="outline", note=note)
        try:
            after = int(parsed.get("after"))  # one-based: 0 = start
        except (TypeError, ValueError):
            after = count - 1  # before the closing part
        after = max(-1, min(after - 1, count - 1))
        return Plan(scope="insert", after=after, names=names, note=note)
    if scope != "parts":
        return Plan(note=note)

    numbers: list[int] = []
    for value in parsed.get("targets") or []:
        try:
            # One-based in prompt, as on screen.
            index = int(value) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= index < count and index not in numbers:
            numbers.append(index)
    if not numbers:
        return Plan(note=note)
    if len(numbers) > MAX_TARGETS:
        return Plan(scope="whole", targets=list(range(count)), note=note)
    return Plan(scope="parts", targets=numbers, note=note)


async def plan(
    *,
    message: str,
    title: str,
    parts: list[str],
    model: str,
    api_key: str,
) -> Plan:
    """Plan one instruction against the outline. Never raises."""
    if not parts or not message.strip():
        return Plan()

    outline = "\n".join(f"{i + 1}. {name}" for i, name in enumerate(parts))
    base, _ = await settings_store.litellm_config()
    try:
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": _PROMPT.format(
                                title=title[:200],
                                outline=outline[:4000],
                                message=message[:1000],
                                limit=MAX_TARGETS,
                            ),
                        }
                    ],
                    # Room for reasoning models.
                    "max_tokens": 600,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.info("revision routing failed: %s", exc)
        return Plan()

    raw = payload.get("usage") or {}
    reply = payload["choices"][0]["message"]
    # Truncated reasoning model leaves answer in reasoning field.
    text = str(reply.get("content") or "").strip() or str(
        reply.get("reasoning_content") or reply.get("reasoning") or ""
    ).strip()
    decided = _parse(text, len(parts), message)
    decided.usage = {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }
    decided = within_named_parts(decided, parts, message)
    decided = named_target(into_existing_part(decided, parts, message), parts, message)
    decided = numbered_target(decided, parts, message)
    decided = count_change(decided, parts, message)
    decided = notes_everywhere(decided, parts, message)
    return place_insert(decided, parts, message)


#: Plain "start over" phrases; checked before routing.
_START_OVER = re.compile(
    r"(새로\s*(써|작성|만들)|처음부터\s*다시|다른\s*주제로|아예\s*다시|버리고\s*다시)"
)


def obviously_new(message: str) -> bool:
    return bool(_START_OVER.search(message))


#: a question, not an ask: 「몇 장이야?」, 「발표자는 누구인가요」, 「무슨 뜻이야」
_QUESTION_WORD = re.compile(
    r"몇|얼마|무엇|뭐|무슨|어디|언제|누가|누구|왜|어떻게|어떤|인가요|나요|니까")
_QUESTION_END = re.compile(r"(?:\?|？|까|니|나|냐|지|야|요)$")
_ASK_VERB = re.compile(
    r"줘|주세요|주십시오|주실|줄\s?수|줄래|바랍니다|부탁|고쳐|고치|바꿔|바꾸|넣어|넣을|빼|지워|지우|"
    r"없애|삭제|추가|줄여|줄이|늘려|늘리|정리해|다듬|붙여|채워|그려|나눠|합쳐|옮겨|맞춰|만들어|"
    r"써\s?(?:줘|주)|해\s?(?:줘|주|줄|봐)|(?:로|으로)\s?(?:해|하|할)")


#: an edit asked in English: 「Make slide 3 a table」, 「Can you shorten slide 2?」
_ASK_VERB_EN = re.compile(
    r"\b(?:change|make|turn|rewrite|reword|shorten|lengthen|add|remove|delete|drop|convert|"
    r"replace|fix|update|move|merge|split|put|insert|simplify|expand|cut|trim|swap|rename|"
    r"translate|tighten|condense)\b", re.I)
#: no change wanted, said outright: 「설명만 해줘」, 「don't change anything」
_NO_CHANGE = re.compile(
    r"설명만|알려만|답만|대답만|말만\s?해|아무것도\s?(?:바꾸|고치|건드리)지|"
    r"\b(?:do\s?n[o']?t|do\s+not)\s+(?:change|edit|touch|modify)\s+anything\b|"
    r"\bwithout\s+changing\b|\bno\s+changes?\b|"
    r"\bjust\s+(?:answer|explain|tell)\b|\bonly\s+(?:answer|explain)\b", re.I)
#: no change wanted, beside a question: 「바꾸지 말고 …뭐야?」, 「don't change it, why …?」
_KEEP_AS_IS = re.compile(
    r"바꾸지\s?(?:마|말)|고치지\s?(?:마|말)|그대로\s?두|"
    r"\b(?:do\s?n[o']?t|do\s+not)\s+(?:change|edit|touch)\b|\bleave\s+it\b", re.I)
#: a question in English: 「What is …?」, 「Why does slide 3 …?」
_QUESTION_EN = re.compile(
    r"(?:^|[.!?]\s{1,4})(?:what|why|where|which|who|whose|when|how|is|are|does|do|did)\b",
    re.I)


def asks_question(message: str) -> bool:
    """Whether the words ask something rather than ask for a change (「지금 슬라이드가 몇
    장이야?」, 「핵심이 뭐야? 설명만 해줘」, 「What is …? Don't change anything.」); a polite
    ask (「바꿔 줄 수 있나요?」, 「Can you make slide 3 a table?」) is not one."""
    text = (message or "").strip()[-400:].rstrip(" .!")
    if not text:
        return False
    if _NO_CHANGE.search(text):
        return True
    shaped = "?" in text or "？" in text or bool(
        _QUESTION_WORD.search(text) and _QUESTION_END.search(text))
    if _KEEP_AS_IS.search(text) and shaped:
        return True
    if _ASK_VERB.search(text) or _ASK_VERB_EN.search(text):
        return False
    return text.endswith(("?", "？")) or bool(
        _QUESTION_WORD.search(text) and _QUESTION_END.search(text)) or bool(
        "?" in text and _QUESTION_EN.search(text))


def asks_edit(message: str) -> bool:
    """Whether the words carry a concrete edit verb (Korean or English)."""
    return bool(_ASK_VERB.search(message or "") or _ASK_VERB_EN.search(message or ""))


#: Export format words; 「한글」 counts only beside a file word or hand-over verb.
_FORMAT_WORDS = (
    ("pdf", r"PDF|피디에프"),
    (
        "hwpx",
        r"HWPX?|한컴|한글\s{0,2}(?=파일|문서|형식|포맷|버전)|"
        r"한글(?=\s{0,2}(?:로|으로|도)\s{0,2}(?:줘|주세요|주시|주라|받|내려|같이|함께|둘|[,.!]|$)|"
        r"\s{0,2}(?:이랑|랑|하고|와|과)\s{0,8}(?:PDF|워드|DOCX?|파일))",
    ),
    ("docx", r"DOCX?|워드|MS\s{0,2}워드|Word"),
    ("pptx", r"PPTX?|파워\s{0,2}포인트|피피티"),
    ("md", r"마크다운|Markdown"),
)
#: Format-word tail up to the hand-over end.
_FORMAT_TAIL = (
    r"(?:\s{0,2}(?:파일|문서|형식|포맷|버전|본))?\s{0,2}"
    r"(?:(?:이랑|랑|하고|와|과|및|[,/·])|(?:로|으로|도|만)?"
    r"(?:\s{0,2}(?:같이|함께|둘\s{0,2}다|모두|전부))?"
    r"(?:\s{0,2}(?:바꿔|변환해|저장해|뽑아|빼|내보내|출력해|만들어)\S{0,3})?"
    r"(?=\s{0,2}(?:줘|주세요|주시|주라|주면|줄래|달라|받|내려|보내|부탁|좀|둘|같이|함께|"
    r"[.!?,~]|$)))"
)
_FORMAT_ASK = [
    (name, re.compile(rf"(?<![A-Za-z])(?:{words})(?![A-Za-z]){_FORMAT_TAIL}", re.I))
    for name, words in _FORMAT_WORDS
]
#: File asked without a format.
_FILE_ASK = re.compile(
    r"다운로드|다운\s{0,2}받|내려\s{0,2}받|내보내\s{0,2}(?:줘|주세요|주시|줄래|기)|파일로\s{0,2}(?:줘|주세요|주시|받|저장|뽑)|"
    r"파일\s{0,2}(?:좀\s{0,2})?(?:줘|주세요|주시)|파일로\s{0,2}(?:만들어|변환)"
)
#: Same-turn edit request.
_EDIT_ASK = re.compile(
    r"고쳐|고치|수정|바꿔|바꾸|변경|추가|넣어|넣고|빼고|빼\s{0,2}줘|삭제|지워|줄여|줄이|늘려|늘리|"
    r"다시\s{0,2}(?:써|쓰|작성)|보완|보강|반영|손봐|다듬|채워|합쳐|나눠|옮겨|맞춰|정정|"
    r"써\s{0,2}(?:줘|주세요)|작성해|요약해|강조|틀렸|잘못"
)
#: Third-party edit, reported not requested.
_THIRD_PARTY_EDIT = re.compile(
    r"[가-힣]{1,10}(?:님이|께서|이|가)\s{1,3}(?:직접\s{1,3})?(?:고쳐|수정해|손봐|바꿔|다듬어)서"
)


def export_request(message: str) -> tuple[list[str], bool] | None:
    """Requested file formats (in word order) and edit flag; None = no file, [] = unnamed format."""
    text = " ".join((message or "").split())[:600]
    if not text:
        return None
    found: list[tuple[int, str]] = []
    rest = text
    for name, pattern in _FORMAT_ASK:
        for match in pattern.finditer(text):
            found.append((match.start(), name))
        rest = pattern.sub(" ", rest)
    generic = bool(_FILE_ASK.search(rest))
    if not found and not generic:
        return None
    rest = _FILE_ASK.sub(" ", rest)
    rest = _THIRD_PARTY_EDIT.sub(" ", rest)
    formats = list(dict.fromkeys(name for _, name in sorted(found)))
    edits = bool(_EDIT_ASK.search(rest))
    if edits and not formats:
        # 「다운로드 버튼 설명」 is about the document, not a file request.
        return None
    return formats, edits


def export_only(message: str) -> list[str] | None:
    """Formats of a file-only turn; else None."""
    asked = export_request(message)
    if asked is None or asked[1]:
        return None
    return asked[0]


def without_export(message: str) -> str:
    """Instruction minus its file request."""
    text = " ".join((message or "").split())
    for _, pattern in _FORMAT_ASK:
        text = pattern.sub(" ", text)
    return " ".join(_FILE_ASK.sub(" ", text).split()) or message


#: Rewrite that talks to the person instead of being the part.
_META_TALK = re.compile(
    r"(?:여기|이곳|이\s{0,2}화면|채팅|대화)(?:에서|서)?\s{0,4}(?:는\s{0,4})?(?:직접\s{0,4})?"
    r"(?:처리|보내|전달|제공|생성|변환|첨부|만들)\S{0,6}\s{0,4}(?:수\s{0,2}없|어렵|못\s)|"
    r"(?:보내|전달해|첨부해|변환해)\s{0,2}드리기(?:는|가)|"
    r"(?:파일|PDF|문서)\S{0,3}\s{0,3}(?:직접\s{0,3})?(?:보내|첨부|전달)\S{0,4}\s{0,2}(?:수\s{0,2}없|어렵)|"
    r"붙여\s{0,2}넣으신\s{0,2}(?:뒤|후)|"
    r"아래(?:에|와\s{0,2}같이)\s{0,4}[^\n]{0,30}(?:완성해|작성해|정리해|고쳐)\s{0,2}"
    r"(?:두었으니|드렸으니|두었습니다만)",
    re.I,
)
_META_OPENING = re.compile(r"^\s{0,4}(?:(?:네|알겠습니다|물론입니다)[,.!\s]|요청하신\s{0,2}대로)")


def meta_talk(body: str) -> bool:
    """Rewritten part contains assistant meta-talk."""
    text = str(body or "")
    return bool(_META_OPENING.search(text) or _META_TALK.search(text[:4000]))


#: P0 rules a markdown part sets off by itself (bold labels read across lines, a short part)
_NOT_FAULTS = frozenset({"markup", "empty"})


def new_faults(old: str, new: str, *, heading: str, instruction: str) -> list[str]:
    """P0 lint findings a rewritten part brought in: not on the part before and not set off
    by the words the person typed (「99.9% 문구를 넣어 줘」)."""
    from app.services import lint

    def p0(text: str) -> list:
        return [f for f in lint.check(lint.from_sections([{"heading": heading, "content": text}]))
                if f.severity == "P0" and f.rule not in _NOT_FAULTS]

    had = {f.rule for f in p0(old)} | {f.rule for f in p0(instruction)}
    return [f.message for f in p0(new) if f.rule not in had]


#: Quoted term swap.
_TERM_SWAP = re.compile(
    r"[「\"“']([^」\"”']{1,80})[」\"”']\s{0,8}(?:를|을|은|는)?\s{0,8}(?:전부|모두|다)?\s{0,8}"
    r"[「\"“']([^」\"”']{1,80})[」\"”']\s{0,8}(?:로|으로)\s{0,8}"
    r"(?:전부|모두|다|문서\s{0,8}전체에서)?\s{0,8}(?:통일|바꿔|바꾸|변경|고쳐|교체|치환)"
)
_SWAP_SCOPE = re.compile(r"전체|전부|모두|다\s|통일|모든")


#: Two parts swap places.
_ORDER_SWAP = re.compile(
    r"(?P<a>[가-힣A-Za-z0-9·/&()]{1,20}(?:\s{1,8}[가-힣A-Za-z0-9·/&()]{1,20}){0,3}?)\s{0,8}(?:장|절|부분|슬라이드|섹션)?"
    r"(?:이랑|하고|과|와|랑)\s{1,8}"
    r"(?P<b>[가-힣A-Za-z0-9·/&()]{1,20}(?:\s{1,8}[가-힣A-Za-z0-9·/&()]{1,20}){0,3}?)\s{0,8}(?:장|절|부분|슬라이드|섹션)?"
    r"(?:의|은|는)?\s{0,8}(?:순서|자리|위치)\s{0,8}(?:를|을)?\s{0,8}(?:서로\s{0,8})?(?:바꿔|바꾸|맞바꿔|교체|뒤집)"
)


def order_swap(instruction: str, parts: list[str]) -> tuple[int, int] | None:
    """Indices of two parts to swap, or None; no model call."""
    text = " ".join((instruction or "").split())
    match = _ORDER_SWAP.search(text)
    if not match:
        return None
    found: list[int] = []
    for name in (match.group("a"), match.group("b")):
        index = _match_part(name.strip(), parts)
        if index is None or index in found:
            return None
        found.append(index)
    return found[0], found[1]


def _match_part(name: str, parts: list[str]) -> int | None:
    """Part by exact title, containing phrase, then shared words."""
    squeeze = lambda t: re.sub(r"\s+", "", t).lower()  # noqa: E731
    wanted = squeeze(name)
    if not any(squeeze(part) == wanted or wanted in squeeze(part) for part in parts):
        # Trailing particle the pattern missed.
        if len(name) > 2 and name[-1] in "이은는을를의":
            return _match_part(name[:-1], parts)
    for index, part in enumerate(parts):
        if squeeze(part) == wanted:
            return index
    hits = [i for i, part in enumerate(parts) if wanted and wanted in squeeze(part)]
    if len(hits) == 1:
        return hits[0]
    words = set(name.split())
    hits = [i for i, part in enumerate(parts) if words and words <= set(part.split())]
    return hits[0] if len(hits) == 1 else None


def term_swap(instruction: str) -> tuple[str, str] | None:
    """`(old, new)` for a document-wide quoted term swap; plain string replace."""
    text = " ".join((instruction or "").split())
    match = _TERM_SWAP.search(text)
    if not match or not _SWAP_SCOPE.search(text):
        return None
    old, new = match.group(1).strip(), match.group(2).strip()
    if not old or old == new:
        return None
    return old, new


def replace_term(value, old: str, new: str) -> tuple[object, int]:
    """`value` with `old` → `new` in all string fields, plus count."""
    if isinstance(value, str):
        count = value.count(old)
        return (value.replace(old, new) if count else value), count
    if isinstance(value, list):
        total = 0
        out = []
        for item in value:
            item, n = replace_term(item, old, new)
            out.append(item)
            total += n
        return out, total
    if isinstance(value, dict):
        total = 0
        out = {}
        for key, item in value.items():
            if key in ("id", "accent", "picture", "diagram", "factCheck"):
                out[key] = item
                continue
            item, n = replace_term(item, old, new)
            out[key] = item
            total += n
        return out, total
    return value, 0


def _part_name(part: dict, is_deck: bool) -> str:
    name = str(part.get("title" if is_deck else "heading") or "")
    return re.sub(r"[\s·:：\-–—.,]+", " ", name).strip().lower()


def _has_content(part: dict, is_deck: bool) -> bool:
    if is_deck:
        fields = ("bullets", "body", "rows", "metrics", "chart", "steps",
                  "timeline", "bands", "tiles", "cards", "columns", "quote")
        return any(part.get(k) for k in fields)
    return bool((part.get("content") or "").strip())


#: Title words too common to identify a part.
_GENERIC_TITLE_WORDS = frozenset({
    "다음", "현황", "사항", "결과", "정리", "소개", "개요", "요약", "및", "그리고", "전체", "주요",
})


def carry_parts(
    new: list[dict], old: list[dict], *, is_deck: bool, mentioned: str
) -> tuple[list[dict], int]:
    """Re-planned parts with untouched parts' text carried; returns `(parts, carried count)`."""
    said = (mentioned or "").lower()
    by_name: dict[str, dict] = {}
    for part in old:
        name = _part_name(part, is_deck)
        if name and _has_content(part, is_deck) and name not in by_name:
            by_name[name] = part
    out: list[dict] = []
    carried = 0
    for part in new:
        name = _part_name(part, is_deck)
        previous = by_name.get(name)
        # Named parts rewritten; generic words ignored.
        tokens = [t for t in name.split() if len(t) >= 2 and t.lower() not in _GENERIC_TITLE_WORDS]
        named = name.lower() in said or (
            bool(tokens) and all(token.lower() in said for token in tokens)
        )
        if previous is None or named:
            out.append(part)
            continue
        merged = {**previous}
        for key in ("id", "accent"):
            if key in part:
                merged[key] = part[key]
        merged.pop("factCheck", None)
        out.append(merged)
        carried += 1
    return out, carried


_TITLE_ASK = re.compile(
    r"제목|타이틀|절\s*이름|장\s*이름|헤드라인|\btitle\b|\bheading\b|\brename\b", re.I
)
_QUOTED = re.compile(r"[「\"“']([^」\"”']{2,80})[」\"”']")


def requested_title(instruction: str) -> str | None:
    """Quoted new name for a rename request, else None."""
    text = instruction or ""
    if not _TITLE_ASK.search(text):
        return None
    quoted = list(_QUOTED.finditer(text))
    if not quoted:
        return None
    if len(quoted) == 1:
        return quoted[0].group(1).strip()
    # New name is the one before 「…으로/로/라고」.
    for match in quoted:
        tail = text[match.end() : match.end() + 6]
        if re.match(r"\s{0,8}(?:으로|로|라고)", tail):
            return match.group(1).strip()
    return quoted[-1].group(1).strip()


#: rename aimed at a part: 「개요 절 제목을」, 「장 이름을」, 「섹션 제목」
_PART_TITLE = re.compile(
    r"(?:절|장|섹션|슬라이드|부분|챕터)\s{0,4}(?:의\s{0,4})?(?:제목|이름|타이틀|헤드라인)|\bheading\b",
    re.I,
)
#: 「제목을 「…」로 바꿔 줘」 as typed, so the rest goes on without it
_TITLE_CLAUSE = re.compile(
    r"[,，]?\s{0,4}(?:그리고\s{1,4})?(?:문서\s{0,4}|보고서\s{0,4}|전체\s{0,4})?(?:제목|타이틀)"
    r"(?:을|를|은|는|도)?\s{0,4}[「\"“'][^」\"”']{2,80}[」\"”']\s{0,4}(?:으로|로|라고)?\s{0,4}"
    r"(?:바꿔|바꾸어|변경해|고쳐|수정해|해)?\s{0,4}(?:줘|주세요|주십시오|주시고|주고)?[.!]?"
)


def title_rename(instruction: str, parts: list[str]) -> tuple[str, int | None] | None:
    """`(new title, part index or None)`: a rename naming a part (「개요 절 제목을」) is that
    part's; 「제목을 「…」로」 with no part named is the document's (index None)."""
    new = requested_title(instruction)
    if not new:
        return None
    text = " ".join((instruction or "").split())
    for index, name in enumerate(parts):
        short = name.strip()
        if short and re.search(
            rf"{re.escape(short)}\s{{0,4}}(?:절|장|섹션|슬라이드|부분)?\s{{0,4}}(?:의\s{{0,4}})?"
            r"(?:제목|이름|타이틀)", text):
            return new, index
    if _PART_TITLE.search(text) or not _TITLE_CLAUSE.search(text):
        return new, -1
    return new, None


def without_title_clause(instruction: str) -> str:
    """The instruction minus its document-title clause; empty when that was all."""
    rest = _TITLE_CLAUSE.sub(" ", instruction or "")
    rest = " ".join(rest.split()).strip(" ,，.")
    return rest if re.search(r"[가-힣A-Za-z]{2}", rest) else ""


_BEFORE = re.compile(r"\s{0,8}(?:절|장|부분)?\s{0,8}(?:바로\s{0,8})?(?:앞에|앞으로|전에)")
_AFTER = re.compile(r"\s{0,8}(?:절|장|부분)?\s{0,8}(?:바로\s{0,8})?(?:뒤에|뒤로|다음에|아래에)")


#: New named part; existing name means revision, not insert.
_ADD_PART = re.compile(
    r"([가-힣A-Za-z0-9·\s]{1,16}?)\s{0,8}(?:장|절|슬라이드|섹션|페이지)(?:을|를)?\s{0,8}"
    r"(?:(한|하나|두|둘|\d)\s{0,8}(?:개|장)?\s{0,8})?(?:더\s{0,8})?(?:추가|넣어|넣고|만들어\s{0,8}넣)"
)


def explicit_insert(message: str, parts: list[str]) -> Plan | None:
    """Insert plan for exactly one new named part; else None."""
    text = " ".join((message or "").split())
    if re.search(r"\d{1,3}\s{0,8}장으로|줄여|늘려|합쳐|합치|삭제|빼", text):
        return None
    match = _ADD_PART.search(text)
    if not match or (match.group(2) or "한") not in ("한", "하나", "1"):
        return None
    name = re.sub(r"^(?:새로\s*|새\s*|그리고\s*)", "", match.group(1)).strip(" ·")
    name = re.split(r"[.,。]\s*", name)[-1].strip()
    # Placement words precede the name.
    name = re.split(r"\s(?:앞|뒤|다음|사이)(?:에|으로|로)?\s+", " " + name)[-1].strip()
    if not (1 <= len(name) <= 14) or any(name == p.strip() for p in parts):
        return None
    plan = Plan(scope="insert", after=len(parts) - 1, names=[name], note=text[:200])
    return place_insert(plan, parts, text)


def place_insert(plan: Plan, parts: list[str], message: str) -> Plan:
    """Insert placed by named neighbour (「결론 뒤에」), else as planned."""
    if not plan.inserts:
        return plan
    text = " ".join((message or "").split())
    best: tuple[int, int] | None = None  # (text position, after-index)
    for index, name in enumerate(parts):
        short = name.strip()
        if not short:
            continue
        for match in re.finditer(re.escape(short), text):
            tail = text[match.end() : match.end() + 14]
            if _BEFORE.match(tail):
                candidate = (match.start(), index - 1)
            elif _AFTER.match(tail):
                candidate = (match.start(), index)
            else:
                continue
            if best is None or candidate[0] > best[0]:
                best = candidate
    if best is None:
        return plan
    return Plan(scope="insert", after=best[1], names=plan.names, note=plan.note, usage=plan.usage)


_PART_WORD = r"\s*(?:장|절|부분|슬라이드|섹션)?\s*(?:을|를|은|는|의|에서|만)"
_CLOSING_NAME = re.compile(r"마무리|결론|감사|요약\s*및\s*결정|closing|thank", re.I)
#: 「<이름> 장을」 part reference.
_NAMED_PHRASE = re.compile(
    r"([가-힣A-Za-z0-9·/&()]{1,20}(?:\s{1,8}[가-힣A-Za-z0-9·/&()]{1,20}){0,3})\s{1,8}(?:장|절|부분|슬라이드|섹션)"
    r"\s{0,8}(?:을|를|은|는|의|에서|만)"
)


_STRUCTURAL_ASK = re.compile(
    r"추가|삭제|빼\s{0,8}줘|빼고|없애|합쳐|합치|병합|나눠|분리|순서|앞으로\s{0,8}옮|뒤로\s{0,8}옮|옮겨|"
    r"\d{1,15}\s{0,8}(?:장|절|쪽|페이지|슬라이드)\s{0,8}(?:으로|로)"
)


def named_target(plan: Plan, parts: list[str], message: str) -> Plan:
    """Plan retargeted to the single existing part named; otherwise unchanged."""
    if plan.scope not in ("parts", "outline"):
        return plan
    if plan.scope == "parts" and not plan.targets:
        return plan
    text = " ".join((message or "").split())
    if _STRUCTURAL_ASK.search(text):
        # Structural changes left to the planner.
        return plan
    # Name counts only with a part word after it.
    mentioned = [
        i for i, name in enumerate(parts)
        if len(name.strip()) >= 2 and re.search(rf"{re.escape(name.strip())}{_PART_WORD}", text)
    ]
    if not mentioned:
        # Phrase before 장/절 as name core.
        phrase = _NAMED_PHRASE.search(text)
        if phrase:
            core = phrase.group(1).strip()
            holders = [i for i, name in enumerate(parts) if core and core in name]
            words = [w for w in core.split() if len(w) >= 2]
            if not holders and words:
                holders = [i for i, name in enumerate(parts) if all(w in name for w in words)]
            shares_a_word = any(any(w in name for w in words) for name in parts)
            if len(holders) == 1:
                mentioned = holders
                text = text.replace(core, parts[holders[0]].strip(), 1)
            elif not holders and not shares_a_word and len(core) >= 2 and plan.scope == "parts":
                # Missing named part added before closing part.
                last_is_closing = bool(parts) and bool(_CLOSING_NAME.search(parts[-1]))
                closing = len(parts) - 1 if last_is_closing else len(parts)
                return Plan(
                    scope="insert", after=closing - 1, names=[core], note=plan.note,
                    usage=plan.usage,
                )
    # Shorter name inside a longer mention belongs to the longer.
    mentioned = [i for i in mentioned if not any(
        j != i and parts[i].strip() in parts[j].strip() and len(parts[j]) > len(parts[i])
        for j in mentioned
    )]
    if len(mentioned) != 1:
        return plan
    index = mentioned[0]
    if plan.scope == "outline" or plan.targets != [index]:
        return Plan(scope="parts", targets=[index], note=plan.note, usage=plan.usage)
    return plan


#: 「예산 표에」, 「과정별 표의」: the table named by what it holds
_NAMED_TABLE = re.compile(
    r"([가-힣A-Za-z0-9·]{2,12})\s{0,2}표\s{0,2}(?:에|에서|를|을|의|는|도|만)(?![가-힣])"
)
#: 「개선점을 … 표로 바꿔」: a list made into a table
_INTO_TABLE = re.compile(
    r"([가-힣A-Za-z0-9·]{2,12})(?:을|를)\s{1,4}(?:[^\s]{1,20}\s{1,4}){0,4}?(?:\d\s{0,2}열\s{0,2})?표로"
)
_TABLE_LINES = re.compile(r"(?m)^[ \t]{0,12}\|.+\|[ \t]*$")


def _stems(word: str) -> list[str]:
    stem = re.sub(r"(?:별|점|사항|내용|현황|들)$", "", word)
    return [w for w in dict.fromkeys([word, stem]) if len(w) >= 2]


def _table_context(text: str) -> str:
    """Each table's header row with the two lines above it (its caption, its lead-in)."""
    lines = (text or "").split("\n")
    out = []
    for i, line in enumerate(lines):
        if _TABLE_LINES.match(line) and not (i and _TABLE_LINES.match(lines[i - 1])):
            out.extend(lines[max(0, i - 2): i + 1])
    return "\n".join(out)


def table_target(plan: Plan, parts: list[str], texts: list[str], message: str) -> Plan:
    """A table edit goes to the one part holding that table (「예산 표에 … 열 추가」 → the part
    whose table is captioned or headed 예산); a list made a table goes to the one part whose
    heading names it (「개선점을 … 표로」 → 「… 개선 사항」). Otherwise the plan stands."""
    if plan.scope not in ("parts", "insert") or not parts:
        return plan
    text = " ".join((message or "").split())
    holders: list[int] = []
    if named := _NAMED_TABLE.search(text):
        words = _stems(named.group(1))
        holders = [i for i, body in enumerate(texts)
                   if any(w in _table_context(body) for w in words)]
    elif made := _INTO_TABLE.search(text):
        words = _stems(made.group(1))
        holders = [i for i, name in enumerate(parts) if any(w in name for w in words)]
        if not holders:
            holders = [i for i, body in enumerate(texts)
                       if any(w in line for w in words
                              for line in body.split("\n") if line.lstrip()[:1] in "-*#")]
    if len(holders) != 1 or (plan.scope == "parts" and plan.targets == holders):
        return plan
    return Plan(scope="parts", targets=holders, note=plan.note, usage=plan.usage,
                typed=plan.typed)


#: 「집행률(%) 열 추가」: a column asked for by name
_ASKED_COLUMN = re.compile(
    r"([가-힣A-Za-z]{2,10})\s{0,2}(?:\([^()\n]{0,8}\))?\s{0,2}열\s{0,2}(?:을|를|도|만)?\s{0,2}"
    r"(?:추가|넣|더|붙)"
)
#: 「'문제-원인-조치' 3열 표」: the columns of a table asked for
_ASKED_HEADER = re.compile(
    r"[「'\"‘“]([^」'\"’”\n]{3,60})[」'\"’”]\s{0,4}(?:\d\s{0,2}열\s{0,2})?(?:의\s{0,2})?표"
)
_ASKED_TOTAL = re.compile(r"합계\s{0,2}행")
_TOTAL_ROW = frozenset({"합계", "계", "총계", "소계", "전체", "총합"})


def _headers_and_labels(texts: list[str]) -> tuple[list[list[str]], set[str]]:
    headers: list[list[str]] = []
    labels: set[str] = set()
    for text in texts:
        lines = [ln.strip() for ln in (text or "").split("\n")]
        for i, line in enumerate(lines):
            if not line.startswith("|"):
                continue
            cells = [re.sub(r"[\s*]", "", c) for c in line.strip("|").split("|")]
            if i + 1 < len(lines) and re.match(r"^\|?\s{0,4}:?-{2,}", lines[i + 1]):
                headers.append(cells)
            elif cells and not re.match(r"^:?-{2,}", cells[0]):
                labels.add(cells[0])
    return headers, labels


def missing_shapes(instruction: str, texts: list[str]) -> list[str]:
    """What the instruction asked of a table that no table in `texts` has: a named column,
    a table's columns, a total row. Empty when all were made (or none was asked)."""
    text = " ".join((instruction or "").split())
    headers, labels = _headers_and_labels(texts)
    flat = ["|".join(h) for h in headers]
    missed: list[str] = []
    for m in _ASKED_COLUMN.finditer(text):
        name = re.sub(r"\s", "", m.group(1))
        if not any(name in head for head in flat):
            missed.append(f"「{m.group(1)}」 열")
    for m in _ASKED_HEADER.finditer(text):
        wanted = [re.sub(r"\s", "", w) for w in re.split(r"[-–—·/,|]", m.group(1)) if w.strip()]
        if len(wanted) >= 2 and not any(all(any(w in c for c in h) for w in wanted)
                                        for h in headers):
            missed.append(f"「{m.group(1)}」 표")
    if _ASKED_TOTAL.search(text) and not labels & _TOTAL_ROW:
        missed.append("합계 행")
    return missed


def into_existing_part(plan: Plan, parts: list[str], message: str) -> Plan:
    """`insert` aimed into an existing part is that part's edit."""
    if not plan.inserts:
        return plan
    text = " ".join((message or "").split())
    for index, name in enumerate(parts):
        short = name.strip()
        if short and re.search(
            rf"{re.escape(short)}\s*(?:절|장|부분)?\s*(?:에|안에|속에|의)\s", text
        ):
            return Plan(scope="parts", targets=[index], note=plan.note, usage=plan.usage)
    return plan


#: Skeleton words.
_SKELETON_WORDS = re.compile(
    r"\d\s{0,2}(?:장|절|번)|장을|장은|절을|슬라이드|섹션|목차|구성|순서|합쳐|나눠"
)


def within_named_parts(plan: Plan, parts: list[str], message: str) -> Plan:
    """`outline` plan editing only inside named parts → pass over current document."""
    if not plan.restructures or not parts:
        return plan
    text = " ".join((message or "").split())
    if _SKELETON_WORDS.search(text):
        return plan
    for name in parts:
        short = name.strip()
        if short and re.search(rf"{re.escape(short)}\s{{0,2}}(?:에|안에|속에)\s", text):
            return Plan(scope="whole", targets=list(range(len(parts))), note=plan.note,
                        usage=plan.usage)
    return plan


_FENCE = re.compile(r"```mermaid\n.*?```(?:\n+\*그림[^*\n]*\*)?", re.S)
_REMOVE_FIGURE = re.compile(
    r"(?:그림|도식|도해|다이어그램|흐름도|구조도|비교도|개념도)\s{0,8}(?:은|을|는|를)?\s{0,8}"
    r"(?:빼|지워|삭제|없이|제거)"
)
_ANY_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.S)


def _ascii_art(block: str) -> bool:
    """Box-character drawing, not code."""
    lines = [ln for ln in block.splitlines() if ln.strip()]
    if len(lines) < 3:
        return False
    box = re.compile(r"[+|]-{2,}|-{2,}[+>]|\|\s{2,}\S.*\||[┌┐└┘├┤─│→←↑↓]")
    boxy = sum(1 for ln in lines if box.search(ln))
    return boxy * 2 >= len(lines)


def keep_figure(old: str, new: str, instruction: str) -> str:
    """Rewrite with old mermaid fence restored unless replaced or removal asked."""
    if "```mermaid" in (new or "") or "```mermaid" not in (old or ""):
        return new
    if _REMOVE_FIGURE.search(instruction or ""):
        return new
    fence = _FENCE.search(old)
    if not fence:
        return new
    # ASCII-box stand-ins dropped.
    body = _ANY_FENCE.sub(lambda m: "" if _ascii_art(m.group(1)) else m.group(0), new or "")
    return f"{body.rstrip()}\n\n{fence.group(0).strip()}"


_COUNT_MENTION = re.compile(
    r"(?:장수|분량|길이)?\s{0,8}[:：]?\s{0,8}(?:총|전체)?\s{0,8}\d{1,3}\s{0,8}(?:장|쪽|페이지|슬라이드|절)\s{0,8}"
    r"(?:안팎|내외|정도|으로|로|을|를|이|가|은|는)?"
    # 「an 8-slide talk」, 「in 10 slides」
    r"|\b\d{1,3}(?:-|\s{1,3})(?:slides?|pages?|sections?)\b",
    re.I,
)


def without_counts(request: str) -> str:
    """Request minus part counts; planner reads the note's count."""
    return " ".join(_COUNT_MENTION.sub(" ", " ".join((request or "").split())).split())


_TARGET_COUNT = re.compile(
    r"(?:총|전체(?:를)?|모두)?\s{0,8}(\d{1,3})\s{0,8}(?:장|절|쪽|페이지|슬라이드)\s{0,8}(?:으로|로)\s{0,8}"
    r"(?:줄여|늘려|맞춰|만들|다시|재구성|정리|압축|요약)"
    # 「Shrink to 3 slides」, 「cut the deck down to 6 slides」, 「make it 8 slides」
    r"|\b(?:shrink|shorten|cut|reduce|trim|condense|expand|extend|grow|make|bring)\b"
    r"(?:\s{1,3}[a-z]{1,8}){0,3}?\s{1,3}(?:(?:down|up)\s{1,3})?(?:to|into)?\s{0,3}"
    r"(\d{1,3})\s{1,3}(?:slides|sections|pages)\b",
    re.I,
)


_NOTES_WORD = r"(?:발표자?\s*노트|스피커\s*노트|노트|speaker\s*notes?|notes?)"
_NOTES_VERB = r"(?:넣어|추가|써|달아|붙여|채워|보강|작성|add|write|fill|put)"
_NOTES_ASK = re.compile(
    rf"{_NOTES_WORD}\s*(?:를|을|만|도)?\s*(?:[^\n]{{0,30}})?{_NOTES_VERB}|"
    rf"{_NOTES_VERB}[^\n]{{0,20}}{_NOTES_WORD}",
    re.I,
)
_BODY_TOO = re.compile(r"본문도|내용도|슬라이드도|글머리표도|함께\s*고쳐|같이\s*고쳐")


#: a slide named by number: 「3장」, 「8번째 장」, 「슬라이드 5」
_SLIDE_REF = re.compile(
    r"(?<!\d)(\d{1,2})\s?(?:번째\s?)?(?:장|번\s?슬라이드|슬라이드|페이지|쪽)(?!점|단|면|소|비|기)"
    r"|슬라이드\s?(\d{1,2})(?!\d)")
#: where one ask ends and the next begins: a sentence end, 「그리고」, a comma before a slide
_ASK_BREAK = re.compile(
    r"(?<=[.!?。])\s+|\n+|\s*(?:그리고|또한|또|그다음|다음으로)\s+"
    r"|,\s*(?=\d{1,2}\s?(?:번째\s?)?(?:장|슬라이드))")


#: a slide or section named by number in English: 「slide 4」, 「slides 2-3」, 「the 4th slide」
_EN_PART_REF = re.compile(
    r"\b(?:slides?|sections?|pages?)\s{1,3}(\d{1,2})"
    r"(?:\s{0,3}(?:-|–|to|and|&)\s{0,3}(\d{1,2}))?(?!\d)"
    r"|\bthe\s{1,3}(\d{1,2}|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
    r"(?:st|nd|rd|th)?\s{1,3}(?:slide|section|page)\b", re.I)
_ORDINAL = {w: i + 1 for i, w in enumerate(
    "first second third fourth fifth sixth seventh eighth ninth tenth".split())}


def _english_refs(text: str) -> set[int]:
    out: set[int] = set()
    for first, last, nth in _EN_PART_REF.findall(text or ""):
        if nth:
            out.add(_ORDINAL.get(nth.lower()) or int(nth))
        elif last and int(first) < int(last) <= int(first) + 9:
            out.update(range(int(first), int(last) + 1))
        else:
            out.update({int(first), *([int(last)] if last else [])})
    return out


def named_slides(text: str) -> set[int]:
    """Slide numbers (one-based) the words name, in Korean or English."""
    return {int(a or b) for a, b in _SLIDE_REF.findall(text or "")} | _english_refs(text)


#: a change of the document's structure or a rewrite of it all: parts added, cut, merged, split,
#: moved or counted again, the outline or the whole document
_RESTRUCTURE = re.compile(
    r"추가|삭제|빼\s{0,8}줘|빼고|없애|합쳐|합치|병합|나눠|분리|순서|옮겨|옮기|구성|목차|개요|"
    r"\d{1,3}\s{0,4}(?:장|절|쪽|페이지|슬라이드)\s{0,4}(?:으로|로|더|짜리|만)|장\s?수|"
    r"늘려|늘리|처음부터|새로\s{0,4}(?:써|작성|만들)|다시\s{0,4}(?:써|짜|만들)|전체|전부|모든|"
    r"\b(?:add|insert|remove|delete|drop|merge|split|reorder|restructure|outline|more|fewer|"
    r"whole|entire|all\s{1,4}slides|start\s{1,4}over|from\s{1,4}scratch)\b"
    r"|\b(?:to|into)\s{1,3}\d{1,3}\s{1,3}(?:slides|sections|pages)\b", re.I)
#: words that join or split, which within one named part act on its items
_JOINS = re.compile(r"합쳐|합치|병합|나눠|분리|묶어|묶|\b(?:merge|split|combine|join)\b", re.I)
#: a part's own items: 「항목」, 「bullets」
_ITEM_WORD = re.compile(r"항목|요점|글머리|불릿|\b(?:items?|bullets?|points?)\b", re.I)


def joins_items(message: str) -> bool:
    """Whether the words join or split a part's items (「항목을 하나로 합쳐줘」, 「merge the
    bullets」)."""
    return bool(_JOINS.search(message or "") and _ITEM_WORD.search(message or ""))


def asks_restructure(message: str) -> bool:
    """Whether the words ask for a new structure or the whole document anew, not one part's
    change: only these may re-plan the outline. Items of one named part joined or split
    (「2장 항목을 하나로 합쳐줘」, 「Merge the items on slide 4 into one」) are that part's edit."""
    text = " ".join((message or "").split())
    found = [m.group(0) for m in _RESTRUCTURE.finditer(text)]
    if found and all(_JOINS.fullmatch(f) for f in found) and _ITEM_WORD.search(text) and len(
            named_slides(text) | {int(n) for n in _SECTION_REF.findall(text)}) == 1:
        return False
    return bool(found)


def numbered_parts(message: str, parts: list[str]) -> list[int] | None:
    """Zero-based parts the words name by number (「4장」, 「2절」, 「slide 4」) when that is
    where the ask goes: all within the document, at most `MAX_TARGETS`, no structure change."""
    if asks_restructure(message):
        return None
    numbers = named_slides(message) | {int(n) for n in _SECTION_REF.findall(message or "")}
    targets = sorted(n - 1 for n in numbers if 1 <= n <= len(parts))
    if not targets or len(targets) != len(numbers) or len(targets) > MAX_TARGETS:
        return None
    return targets


def numbered_target(plan: Plan, parts: list[str], message: str) -> Plan:
    """The parts the words name by number (「4장」, 「2절」, 「slide 4」) as the targets, whatever
    the planner picked; a structure change is left to it."""
    if plan.scope not in ("parts", "whole", "outline"):
        return plan
    targets = numbered_parts(message, parts)
    if targets is None:
        return plan
    if plan.scope == "parts" and plan.targets == targets:
        return plan
    return Plan(scope="parts", targets=targets, note=plan.note, usage=plan.usage)


def slide_clause(instruction: str, index: int) -> str:
    """The part of a many-slide instruction meant for slide `index` (zero-based): 「3장을 표로
    바꿔줘. 그리고 8장 그림은 빼줘.」 → 「8장 그림은 빼줘.」 for the eighth. An instruction naming
    no slide number is meant whole for every target."""
    clauses = [c.strip() for c in _ASK_BREAK.split(instruction or "") if c and c.strip()]
    named = [(c, named_slides(c)) for c in clauses]
    if not any(numbers for _, numbers in named):
        return instruction
    mine = [c for c, numbers in named if index + 1 in numbers]
    if mine:
        return " ".join(mine)
    return " ".join(c for c, numbers in named if not numbers) or instruction


#: a picture on a slide asked away: 「8장 그림은 빼줘」, 「사진 지워 줘」
_REMOVE_PICTURE = re.compile(
    r"(?:그림|사진|이미지|일러스트|삽화|도식|도해|다이어그램)\S{0,3}\s{0,8}"
    r"(?:빼|지워|지우|삭제|없애|제거|없이)\S{0,6}"
    r"(?:\s{0,4}(?:줘|주세요|주십시오|줄래|해\s?줘|해\s?주세요|바랍니다))?")
_FILLER = frozenset({"그리고", "슬라이드", "부탁해", "부탁합니다", "주세요", "해줘", "그냥"})


def removes_picture(text: str) -> bool:
    """Whether the words ask a slide's picture away."""
    return bool(_REMOVE_PICTURE.search(text or ""))


def only_removes_picture(text: str) -> bool:
    """Whether removing the picture is all the words ask (nothing left to rewrite)."""
    if not removes_picture(text):
        return False
    rest = _REMOVE_PICTURE.sub(" ", _SLIDE_REF.sub(" ", text or ""))
    return not [w for w in re.findall(r"[가-힣A-Za-z]{2,}", rest) if w not in _FILLER]


#: a chart on a slide asked away: 「4장 차트 빼줘」, 「그래프는 지워 줘」
_REMOVE_CHART = re.compile(
    r"(?:차트|그래프)\S{0,3}\s{0,8}(?:빼|지워|지우|삭제|없애|제거|없이)\S{0,6}"
    r"(?:\s{0,4}(?:줘|주세요|주십시오|줄래|해\s?줘|해\s?주세요|바랍니다))?")


def removes_chart(text: str) -> bool:
    """Whether the words ask a slide's chart away."""
    return bool(_REMOVE_CHART.search(text or ""))


def only_removes_chart(text: str) -> bool:
    """Whether removing the chart is all the words ask."""
    if not removes_chart(text):
        return False
    rest = _REMOVE_CHART.sub(" ", _SLIDE_REF.sub(" ", text or ""))
    return not [w for w in re.findall(r"[가-힣A-Za-z]{2,}", rest) if w not in _FILLER]


def notes_only(message: str) -> bool:
    """Speaker-notes-only request."""
    text = " ".join((message or "").split())
    return bool(_NOTES_ASK.search(text)) and not _BODY_TOO.search(text)


_EVERY_PART = re.compile(
    r"모든\s*(?:장|절|슬라이드|부분)|장마다|절마다|전체\s*(?:장|슬라이드)|각\s*장|"
    r"every\s+slide|all\s+slides",
    re.I,
)


def notes_everywhere(plan: Plan, parts: list[str], message: str) -> Plan:
    """Notes for every slide as whole-deck pass, past target cap."""
    if plan.scope in ("outline", "new") or not parts:
        return plan
    text = " ".join((message or "").split())
    if notes_only(text) and _EVERY_PART.search(text):
        return Plan(
            scope="whole", targets=list(range(len(parts))), note=plan.note, usage=plan.usage
        )
    return plan


def count_change(plan: Plan, parts: list[str], message: str) -> Plan:
    """Outline plan for a stated new part count, the count said in the note in the words the
    deck planner reads (a planner's own outline note may state it otherwise or not at all)."""
    if not parts:
        return plan
    match = _TARGET_COUNT.search(" ".join((message or "").split()))
    if not match:
        return plan
    wanted = int(match.group(1) or match.group(2))
    if wanted <= 0 or wanted == len(parts):
        return plan
    # One count only, word-led so 「- 6장으로」 isn't a range.
    note = f"장수는 {wanted}장으로 맞춘다. {without_counts(plan.note)}".strip()
    return Plan(scope="outline", note=note[:600], usage=plan.usage)


#: Part text chars shown to restructure planner/writer.
_PART_TEXT_CHARS = 600


def outline_block(parts: list[str], texts: list[str] | None = None) -> str:
    """Current outline for the restructure planner."""
    lines = []
    for i, name in enumerate(parts):
        lines.append(f"{i + 1}. {name}")
        text = " ".join((texts[i] if texts and i < len(texts) else "").split())
        if text:
            lines.append(f"   내용: {text[:_PART_TEXT_CHARS]}")
    listed = "\n".join(lines)
    return (
        "# Current outline\n"
        f"{listed}\n"
        "Rebuild the outline as requested, starting from this one. If a number was asked for, "
        "match it exactly; merge or drop only the parts the request says to merge or drop. Keep "
        "every other part under the same title, and do not drop parts the request did not mention "
        "or change the subject. Carry what the current document holds (figures, requested items, "
        "schedules) over into some part. A merged part takes the tables, figures and dates "
        "from its 「내용」 line above as they are, in one part — keep what is in the current "
        "document even when the original request does not have it."
    )


def _title_tokens(title: str) -> set[str]:
    return {t for t in re.findall(r"[가-힣A-Za-z]{2,}", title or "")}


def _dedupe_tables(slides: list[dict]) -> list[dict]:
    from app.services.deck import dedupe_rows

    for slide in slides:
        if isinstance(slide.get("rows"), list):
            slide["rows"] = dedupe_rows(slide["rows"])
    return slides


def absorb_rows(new: list[dict], old: list[dict], *, mentioned: str) -> list[dict]:
    """Merged-away table rows moved into the absorbing slide (≤ six rows with header)."""

    said = (mentioned or "").lower()
    # Writer's own repeats yield slots first.
    new = _dedupe_tables(new)
    new_titles = {str(s.get("title") or "") for s in new}
    for gone in old:
        title = str(gone.get("title") or "")
        rows = gone.get("rows")
        if title in new_titles or not isinstance(rows, list) or len(rows) < 2:
            continue
        tokens = {t for t in _title_tokens(title) if t.lower() in said}
        if not tokens:
            continue
        tables = [s for s in new if isinstance(s.get("rows"), list) and s.get("rows")]
        host = next((s for s in tables if _title_tokens(str(s.get("title") or "")) & tokens), None)
        if host is None:
            # Slide named with the gone one in a merge phrase is its destination.
            for words in _merge_phrases(said):
                if tokens & words:
                    partners = words - tokens
                    host = next(
                        (s for s in tables if _title_tokens(str(s.get("title") or "")) & partners),
                        None,
                    )
                    break
        if host is None:
            continue
        width = len(host["rows"][0])
        have = " ".join(str(c) for r in host["rows"] for c in r)
        for row in rows[1:]:
            if len(host["rows"]) >= 6 or not row:
                break
            key = str(row[0]).strip()
            key_tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", key))
            # Covered if all key words, or one distinctive word, in host.
            distinctive = {
                t for t in key_tokens if (len(t) >= 3 and not t.isascii()) or re.search(r"\d", t)
            }
            covered = bool(key_tokens) and (
                all(t in have for t in key_tokens) or any(t in have for t in distinctive)
            )
            if key and key not in have and not covered:
                cells = [str(c) for c in row][:width]
                host["rows"].append(cells + [""] * (width - len(cells)))
    return _dedupe_tables(new)


def carry_by_content(new: list[dict], old: list[dict], *, mentioned: str) -> list[dict]:
    """New timelines sharing a date with a longer old one take back old pairs."""

    def keys(slide: dict) -> set[str]:
        out = set()
        for pair in slide.get("timeline") or []:
            if isinstance(pair, list) and pair:
                digits = re.findall(r"\d+", str(pair[0]))
                if digits:
                    out.add("/".join(str(int(x)) for x in digits[:2]))
        return out

    old_timelines = [s for s in old if s.get("layout") == "timeline" and keys(s)]
    for slide in new:
        if slide.get("layout") != "timeline":
            continue
        mine = keys(slide)
        match = next(
            (o for o in old_timelines if mine & keys(o) and len(keys(o)) > len(mine)), None
        )
        if match is not None:
            slide["timeline"] = [list(p) for p in match["timeline"]]
    return new


def _merge_phrases(said: str) -> list[set[str]]:
    """Particle-stripped word sets per 「A와 B는 한 장으로」 phrase."""
    out: list[set[str]] = []
    for segment in re.split(r"[.。,，\n;]", said or ""):
        m = re.search(r"^(.{0,60}?)\s{0,8}(?:한\s{0,8}장|하나)\s{0,8}(?:으로|에)", segment)
        if m:
            words = {
                re.sub(r"(?:은|는|이|가|와|과|도|을|를)$", "", t) for t in _title_tokens(m.group(1))
            }
            words = {w for w in words if w}
            if words:
                out.append(words)
    return out


def _destination(gone: dict, tables: list[dict], said: str) -> dict | None:
    """Destination table slide by title word or merge phrase."""
    title = str(gone.get("title") or "")
    tokens = {t for t in _title_tokens(title) if t.lower() in said}
    host = next((s for s in tables if _title_tokens(str(s.get("title") or "")) & tokens), None)
    if host is None and tokens:
        for words in _merge_phrases(said):
            if tokens & words:
                partners = words - tokens
                host = next(
                    (s for s in tables if _title_tokens(str(s.get("title") or "")) & partners),
                    None,
                )
                break
    return host


def unmix_rows(new: list[dict], old: list[dict], *, mentioned: str) -> list[dict]:
    """Gone table's rows removed from non-destination tables (≥ two body rows kept)."""
    said = (mentioned or "").lower()
    new_titles = {str(s.get("title") or "") for s in new}
    tables = [s for s in new if isinstance(s.get("rows"), list) and len(s.get("rows") or []) > 1]
    for gone in old:
        if str(gone.get("title") or "") in new_titles or not isinstance(gone.get("rows"), list):
            continue
        keys = {str(r[0]).strip().lower() for r in gone["rows"][1:] if r and str(r[0]).strip()}
        if not keys:
            continue
        home = _destination(gone, tables, said)
        for table in tables:
            if table is home:
                continue
            body = table["rows"][1:]
            strayed = [r for r in body if r and str(r[0]).strip().lower() in keys]
            if strayed and len(body) - len(strayed) >= 2:
                table["rows"] = [table["rows"][0], *[r for r in body if r not in strayed]]
    return new


def _timeline_keys(slide: dict) -> set[str]:
    out = set()
    for pair in slide.get("timeline") or []:
        if isinstance(pair, list) and pair:
            digits = re.findall(r"\d+", str(pair[0]))
            if digits:
                out.add("/".join(str(int(x)) for x in digits[:2]))
    return out


_TIMELINE_WORDS = ("단계", "일정", "로드맵", "타임라인", "계획", "마일스톤", "스케줄")


def keep_timeline_layout(
    new: list[dict], old: list[dict], *, mentioned: str, request: str = ""
) -> list[dict]:
    """Unnamed old timeline restored onto a matching new slide when none remains."""
    said = (mentioned or "").lower()
    if any(s.get("layout") == "timeline" for s in new):
        return new
    for gone in old:
        if gone.get("layout") != "timeline" or not _timeline_keys(gone):
            continue
        title = str(gone.get("title") or "")
        if title.lower() in said:
            continue
        tokens = _title_tokens(title) - _GENERIC_TITLE_WORDS
        host = next(
            (
                s for s in new
                if s.get("layout") not in ("title", "agenda", "section", "closing")
                and (
                    _title_tokens(str(s.get("title") or "")) & tokens
                    or any(w in str(s.get("title") or "") for w in _TIMELINE_WORDS)
                )
            ),
            None,
        )
        if host is None:
            # No host: unrequested agenda/statement slide yields its slot.
            asked = f"{said} {(request or '').lower()}"
            filler = next(
                (
                    s for s in new
                    if s.get("layout") == "agenda"
                    and not re.search(r"목차|차례|agenda", asked)
                ),
                None,
            ) or next(
                (
                    s for s in new
                    if s.get("layout") in ("statement", "quote")
                    and not re.search(r"한\s*줄\s*요약|요약\s*장|핵심\s*메시지", asked)
                ),
                None,
            )
            if filler is None:
                continue
            new.remove(filler)
            at = len(new) - 1 if new and new[-1].get("layout") == "closing" else len(new)
            new.insert(at, {
                **{k: v for k, v in gone.items() if k not in ("id",)},
                "id": filler.get("id") or gone.get("id"),
            })
            break
        for key in (
            "bullets", "steps", "rows", "cards", "bands", "tiles", "metrics", "chart", "body"
        ):
            host.pop(key, None)
        host["layout"] = "timeline"
        host["timeline"] = [list(p) for p in gone["timeline"]]
        break
    return new


def refresh_agenda(slides: list[dict]) -> list[dict]:
    """Carried agenda slide relists the new deck."""
    body_titles = [
        str(s.get("title") or "")
        for s in slides
        if s.get("layout") not in ("title", "agenda", "section", "closing")
    ]
    for slide in slides:
        if slide.get("layout") == "agenda" and body_titles:
            slide["bullets"] = body_titles[:8]
    return slides


def document_block(parts: list[str], texts: list[str]) -> str:
    """Current document text as restructure source."""

    chunks = []
    for i, name in enumerate(parts):
        text = " ".join((texts[i] if i < len(texts) else "").split())
        if text:
            chunks.append(f"## {name}\n{text[:_PART_TEXT_CHARS]}")
    return (
        "# Current document content\n"
        "A part that is merged or renamed carries over the tables, figures, dates and items below "
        "as they are. What is here is a fact of this document even if the original request does "
        "not have it.\n\n" + "\n\n".join(chunks)
    )


#: where one ask of a report edit ends: a sentence end, 「그리고」, 「…빼고,」
_CLAUSE_BREAK = re.compile(
    r"(?<=[.!?。])\s+|\n+|(?:그리고|또한|그다음|다음으로)\s+|(?<=[가-힣]고),\s*|(?<=[가-힣]며),\s*")
#: a part named by number: 「2절의」, 「제3장」; not a count (「3장으로」)
_SECTION_REF = re.compile(
    r"(?<![\d.])(?:제\s?)?(\d{1,2})\s?(?:절|장)(?!\s?(?:으로|로|짜리|씩))")
_COUNT_WORD = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5}
#: a summary asked into the document: 「3줄 요약을 넣어줘」, 「요약 3줄 추가」
_SUMMARY_ASK = re.compile(
    r"(?:(?P<n>\d|한|두|세|네|다섯)\s?(?:줄|문장)(?:\s?(?:짜리|로\s?된))?\s?(?:핵심\s?)?요약"
    r"|요약\s?(?:을|를)?\s?(?P<m>\d|한|두|세|네|다섯)\s?(?:줄|문장))"
    r"[^.!?\n]{0,16}?(?:넣|추가|달|붙|써|만들|작성)")
_FRONT = re.compile(r"맨\s?(?:앞|위|처음)|첫머리|서두|문서\s?(?:앞|첫)|앞부분|상단")
#: wording the person points at: 「…4%라는 표현」, 「'허용 한도'라는 문구」
_PHRASE_REF = re.compile(
    r"(?P<p>[^,.!?\n]{2,60}?)\s?(?:이?라는|라고\s?(?:쓴|적은|한))\s?(?:표현|문구|말|문장|부분|내용)")
_QUOTED = re.compile(r"[「『'\"‘“]([^」』'\"’”\n]{2,60})[」』'\"’”]")
_VALUE = re.compile(r"\d[\d,.]*\s?(?:%|만\s?원|억\s?원?|원|명|건|개월|일)")
_PARTICLE = re.compile(r"(?:까지|에서|으로|이라|라는|이가|은|는|이|가|을|를|의|에|로|과|와|도)$")


def _plain_words(text: str) -> set[str]:
    out = set()
    for word in re.findall(r"[가-힣A-Za-z]{2,20}", text or ""):
        word = _PARTICLE.sub("", word)
        if len(word) >= 2:
            out.add(word)
    return out


def summary_lines_asked(clause: str) -> int:
    """Lines of a summary the clause asks put in (「3줄 요약을 넣어줘」 → 3), else 0."""
    m = _SUMMARY_ASK.search(" ".join((clause or "").split()))
    if not m:
        return 0
    word = m.group("n") or m.group("m")
    return int(word) if word.isdigit() else _COUNT_WORD.get(word, 0)


def rewords(clause: str) -> bool:
    """Whether the clause corrects wording it points at (「…라는 표현은 … 고쳐줘」), not a value."""
    return bool(_PHRASE_REF.search(clause or "") or _QUOTED.search(clause or ""))


def _phrase_holders(clause: str, texts: list[str]) -> list[int]:
    """Parts whose text states the wording the clause points at."""
    quoted = [q.strip() for q in _QUOTED.findall(clause)]
    if quoted:
        return [i for i, t in enumerate(texts) if any(q in t for q in quoted)]
    m = _PHRASE_REF.search(clause)
    if not m:
        return []
    phrase = m.group("p")
    values = [re.sub(r"\s", "", v) for v in _VALUE.findall(phrase)]
    words = _plain_words(_VALUE.sub(" ", phrase))
    if not values and len(words) < 2:
        return []
    holders = []
    for i, text in enumerate(texts):
        for line in re.split(r"(?<=[.!?。])\s+|\n", text or ""):
            flat = re.sub(r"\s", "", line)
            if all(v in flat for v in values) and (
                    len(words & _plain_words(line)) >= (1 if values else 2)):
                holders.append(i)
                break
    return holders


def section_asks(instruction: str, parts: list[str], texts: list[str]
                 ) -> tuple[dict[int, list[str]], int] | None:
    """`({part: its asks}, summary lines)` when every ask of a report edit names its part: by
    number (「2절의 표 2는 빼고」), by its name, as a summary at the start (「맨 앞에 3줄 요약」,
    part 0) or by wording only some parts state (「…4%라는 표현은 … 고쳐줘」). None when an ask
    names no part (the planner decides)."""
    clauses = [c.strip(" ,") for c in _CLAUSE_BREAK.split(instruction or "") if c and c.strip(" ,")]
    if not clauses or not parts:
        return None
    out: dict[int, list[str]] = {}
    summary = 0
    for clause in clauses:
        numbered = {int(n) - 1 for n in _SECTION_REF.findall(clause)} | {
            n - 1 for n in _english_refs(clause)}
        if any(not 0 <= n < len(parts) for n in numbered):
            return None
        named = {i for i, name in enumerate(parts)
                 if len(name.strip()) >= 3 and name.strip() in clause}
        lines = summary_lines_asked(clause)
        if lines and (_FRONT.search(clause) or not numbered | named):
            summary = lines
            out.setdefault(0, []).append(clause)
            continue
        holders = set(numbered | named) or set(_phrase_holders(clause, texts))
        if not holders:
            return None
        for i in holders:
            out.setdefault(i, []).append(clause)
    return out, summary


#: numbered tables and figures asked away: 「표 2와 그림 1은 빼고」, 「그림 3 삭제해 줘」
_NUMBERED = r"(?:표|그림)\s?\d{1,3}(?![\d.])"
_NUMBERED_REMOVAL = re.compile(
    rf"{_NUMBERED}(?:\s{{0,4}}(?:와|과|,|및|·|하고|그리고)\s{{0,4}}{_NUMBERED})*"
    r"\s{0,4}(?:은|는|을|를|도)?\s{0,8}(?:모두\s{0,4}|다\s{0,4}|전부\s{0,4})?"
    r"(?:빼|지워|지우|삭제|없애|제거)\S{0,6}"
    r"(?:\s{0,4}(?:줘|주세요|주십시오|해\s?줘|해\s?주세요|바랍니다))?")
_NUMBERED_ITEM = re.compile(r"(표|그림)\s?(\d{1,3})(?![\d.])")
_REMOVAL_FILLER = frozenset({*_FILLER, "모두", "전부", "부탁드립니다", "해주세요", "주십시오",
                             "바랍니다"})


def _only_removal(clause: str) -> bool:
    """Whether taking numbered tables or figures away is all the clause asks (「표 2는 빼고
    그 내용은 글로 풀어 줘」 asks more: that is a rewrite's)."""
    if not _NUMBERED_REMOVAL.search(clause):
        return False
    rest = _SECTION_REF.sub(" ", _NUMBERED_REMOVAL.sub(" ", clause))
    words = [_PARTICLE.sub("", w) for w in re.findall(r"[가-힣A-Za-z]{2,}", rest)]
    return not [w for w in words if len(w) > 1 and w not in _REMOVAL_FILLER]


#: an ask leaning on what goes: 「그 내용은 본문으로」, 「대신 글로 풀어」
_POINTS_BACK = re.compile(r"그\s?(?:내용|표|그림|수치|자료)|해당\s?(?:내용|표|그림)|대신|풀어")


def _clauses(instruction: str) -> list[str]:
    return [c.strip(" ,") for c in _CLAUSE_BREAK.split(instruction or "") if c and c.strip(" ,")]


def numbered_removals(instruction: str) -> list[tuple[str, int]]:
    """`[("표", 2), ("그림", 1)]` for the numbered tables and figures the words only ask away."""
    clauses = _clauses(instruction)
    if any(_POINTS_BACK.search(c) for c in clauses if not _only_removal(c)):
        return []  # 「…빼고, 그 내용은 글로」: the rewrite reads the table before it goes
    out: list[tuple[str, int]] = []
    for clause in filter(_only_removal, clauses):
        for m in _NUMBERED_REMOVAL.finditer(clause):
            for kind, n in _NUMBERED_ITEM.findall(m.group(0)):
                if (kind, int(n)) not in out:
                    out.append((kind, int(n)))
    return out


def settled_by_code(instruction: str, parts: list[str], texts: list[str]) -> bool:
    """Whether a report edit is numbered removals plus asks that each name their part (「표 2와
    그림 1은 빼고, 맨 앞에 3줄 요약을 넣어줘」): routed and done without the planner, whose reading
    may turn it into a new outline."""
    if not numbered_removals(instruction):
        return False
    rest = without_numbered_removals(instruction)
    return not rest or (not _STRUCTURAL_ASK.search(rest)
                        and section_asks(rest, parts, texts) is not None)


def without_numbered_removals(instruction: str) -> str:
    """The instruction minus its asks that only take numbered tables or figures away."""
    return " ".join(c for c in _clauses(instruction) if not _only_removal(c))


_TABLE_RULE = re.compile(r"^\s{0,3}\|?\s{0,3}:?-{2,}")
_PICTURE_LINE = re.compile(r"^\s*!\[(?P<alt>[^\]]{0,200})\]\([^)\s]+\)\s*$")
_CAPTION_OF = {"표": re.compile(r"^\s{0,12}(?:\*\*)?\s?<?\s?표\s?(\d{1,3})\s?[.:：>*]"),
               "그림": re.compile(r"^\s{0,12}[*_]{0,2}\s?그림\s?(\d{1,3})\s?[.:：*]")}
_REFERENCE_PART = re.compile(r"참고\s?문헌|참고\s?자료|references|bibliography", re.I)


def _blocks_in(text: str) -> list[tuple[str, int, int, int | None]]:
    """`(kind, first line, end line, captioned number)` for each table and figure of a part."""
    lines = text.split("\n")
    out: list[tuple[str, int, int, int | None]] = []
    i, fenced = 0, False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```") and not stripped.startswith("```mermaid"):
            fenced = not fenced
            i += 1
            continue
        if fenced:
            i += 1
            continue
        if (picture := _PICTURE_LINE.match(line)) or stripped.startswith("```mermaid"):
            end = i + 1
            if not picture:
                while end < len(lines) and not lines[end].strip().startswith("```"):
                    end += 1
                end += 1
            number = None
            if picture and (m := _CAPTION_OF["그림"].match(picture.group("alt"))):
                number = int(m.group(1))
            j = end
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and (m := _CAPTION_OF["그림"].match(lines[j])):
                number = number or int(m.group(1))
                end = j + 1
            out.append(("그림", i, end, number))
            i = end
            continue
        if stripped.startswith("|") and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1]):
            start, end = i, i
            while end < len(lines) and lines[end].strip().startswith("|"):
                end += 1
            k = start - 1
            while k >= 0 and not lines[k].strip():
                k -= 1
            number = None
            if k >= 0 and (m := _CAPTION_OF["표"].match(lines[k])):
                number, start = int(m.group(1)), k
            out.append(("표", start, end, number))
            i = end
            continue
        i += 1
    return out


def _particle(word: str, closed: str, opened: str) -> str:
    """`closed` after a final consonant (digits read aloud), else `opened`."""
    last = (word or " ")[-1]
    if "가" <= last <= "힣":
        return closed if (ord(last) - 0xAC00) % 28 else opened
    if last.isdigit():
        return closed if last in "013678" else opened
    return opened


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。])\s+")


def _drop_references(text: str, name: str) -> str:
    """Prose citing `name` (a table or figure taken away) without the citation: 「(표 2)」
    goes, a sentence that is about it goes, 「표 2와 같이」 goes from its sentence."""
    kind, n = name.split(" ", 1)
    num = rf"{kind}\s?{n}"
    if not re.search(rf"{num}(?![\d.])", text):
        return text
    out = []
    for line in text.split("\n"):
        if line.lstrip().startswith(("|", "![", "```")) or _CAPTION_OF["표"].match(line) \
                or _CAPTION_OF["그림"].match(line) or not re.search(rf"{num}(?![\d.])", line):
            out.append(line)
            continue
        # in a bracket: 「(표 2)」 gone, 「(표 1, 표 2)」 → 「(표 1)」
        line = re.sub(rf"\s?\(\s?{num}\s?\)", "", line)
        line = re.sub(rf"(\((?:[^()]{{0,40}}?))(?:,\s?|·){num}(?![\d.])", r"\1", line)
        line = re.sub(rf"\({num}(?:,\s?|·)", "(", line)
        lead = re.match(r"\s{0,24}(?:[-*+]\s{1,4})?", line).group(0)
        kept = []
        for sentence in _SENTENCE_SPLIT.split(line[len(lead):]):
            if not re.search(rf"{num}(?![\d.])", sentence):
                kept.append(sentence)
                continue
            if re.search(rf"{num}(?:은|는|이|가|의)(?=\s)", sentence):
                continue  # about the gone table or figure
            sentence = re.sub(
                rf"(?:위|아래|다음)?\s?{num}(?:과|와|에서|에|을|를|의|처럼|로|으로)?\s?"
                r"(?:같이|처럼|보듯이|보듯|나타낸 것처럼|정리한 것처럼|나타냈듯이)?\s?,?\s?", "",
                sentence)
            if sentence.strip():
                kept.append(sentence.strip())
        if kept:
            out.append(lead + " ".join(kept))
        elif not line.strip():
            out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


#: a sentence pointing at a table or figure by place: 「아래 표와 같습니다」, 「위 그림처럼」
_POINTER = {kind: re.compile(rf"(?:아래|위|다음|하단|상단)(?:의)?\s?{kind}(?!{not_it})")
            for kind, not_it in (("표", "현|준|시|정|본|기|명|지|면"), ("그림", "자"))}


def _drop_pointers(text: str, kind: str) -> str:
    """`text` without its short sentences pointing at a `kind` by place (none is left)."""
    out = []
    for line in text.split("\n"):
        if line.lstrip().startswith(("|", "![", "```")) or not _POINTER[kind].search(line):
            out.append(line)
            continue
        kept = [x for x in _SENTENCE_SPLIT.split(line)
                if not (_POINTER[kind].search(x) and len(x) <= 80)]
        if kept:
            out.append(" ".join(kept))
    return "\n".join(out)


def drop_numbered(parts: list[dict], asked: list[tuple[str, int]]
                  ) -> tuple[list[dict], list[str], list[str]]:
    """`(parts, taken, absent)`: the asked tables and figures removed by hand, with their
    captions and the prose that cites them; numbered as the reader sees them (the caption, else
    the order in the document). `taken` says where each was (「2절의 표 2」)."""
    captioned: dict[tuple[str, int], tuple[int, int, int]] = {}
    ordinal: dict[tuple[str, int], tuple[int, int, int]] = {}
    counts = {"표": 0, "그림": 0}
    for index, part in enumerate(parts):
        if _REFERENCE_PART.search(str(part.get("heading") or "")):
            continue
        for kind, start, end, number in _blocks_in(str(part.get("content") or "")):
            counts[kind] += 1
            ordinal[(kind, counts[kind])] = (index, start, end)
            if number is not None:
                captioned.setdefault((kind, number), (index, start, end))
    found = {**ordinal, **captioned}
    spans: dict[int, list[tuple[int, int]]] = {}
    taken, absent = [], []
    for kind, n in asked:
        where = found.get((kind, n))
        if where is None:
            absent.append(f"{kind} {n}")
            continue
        index, start, end = where
        if (start, end) in spans.get(index, []):
            continue
        spans.setdefault(index, []).append((start, end))
        taken.append(f"{index + 1}절의 {kind} {n}")
    out = []
    for index, part in enumerate(parts):
        text = str(part.get("content") or "")
        if index in spans:
            lines = text.split("\n")
            for start, end in sorted(spans[index], reverse=True):
                del lines[start:end]
            text = "\n".join(lines)
            for kind in ("표", "그림"):
                if any(t.startswith(f"{index + 1}절의 {kind} ") for t in taken) and not any(
                        b[0] == kind for b in _blocks_in(text)):
                    # 「아래 표와 같습니다」 with no table left under it
                    text = _drop_pointers(text, kind)
        for name in (t.split("의 ", 1)[1] for t in taken):
            text = _drop_references(text, name)
        text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
        if text != str(part.get("content") or "").strip("\n"):
            part = {**part, "content": text}
            part.pop("factCheck", None)
        out.append(part)
    return out, taken, absent


#: an ask for a new table or figure in the same edit: 「표로 정리해」, 「흐름도를 넣어」
_ASKS_BLOCK = {
    kind: re.compile(
        rf"(?:{words})\s?(?:으로|로|을|를|도|하나)?\s?(?:넣|추가|그려|그리|만들|바꿔|정리)")
    for kind, words in (("표", "표"), ("그림", "그림|도식|도해|구조도|흐름도|개념도|다이어그램"))}


def _block_rows(text: str, start: int, end: int) -> set[str]:
    """A table's or figure's lines without its caption, spacing evened."""
    return {" ".join(line.split()) for line in text.split("\n")[start:end]
            if line.strip() and not _CAPTION_OF["표"].match(line)
            and not _CAPTION_OF["그림"].match(line)}


def drop_added(old: str, new: str, removed: list[str], asked: str = "") -> str:
    """`new` without the tables or figures its rewrite added to a part a numbered one was just
    taken out of (`removed`: 「표 2」): the removal holds through the same edit's rewrite, unless
    `asked` asks for a new one."""
    kinds = {name.split(" ", 1)[0] for name in removed}
    kinds = {k for k in kinds if k in _ASKS_BLOCK and not _ASKS_BLOCK[k].search(asked or "")}
    if not kinds:
        return new
    had = [b for b in _blocks_in(old) if b[0] in kinds]
    has = [b for b in _blocks_in(new) if b[0] in kinds]
    old_rows = [_block_rows(old, b[1], b[2]) for b in had]

    def kinship(block: tuple[str, int, int, int | None]) -> int:
        rows = _block_rows(new, block[1], block[2])
        return max((len(rows & seen) for seen in old_rows), default=0)

    gone: list[tuple[str, int, int, int | None]] = []
    for kind in kinds:
        mine = [b for b in has if b[0] == kind]
        extra = len(mine) - sum(1 for b in had if b[0] == kind)
        if extra > 0:
            # the least like any block the part kept
            gone += sorted(mine, key=kinship)[:extra]
    if not gone:
        return new
    lines = new.split("\n")
    for _, start, end, _ in sorted(gone, key=lambda b: b[1], reverse=True):
        del lines[start:end]
    text = "\n".join(lines)
    kept = {(b[0], b[3]) for b in has if b not in gone}
    for kind, _, _, number in gone:
        if number is not None and (kind, number) not in kept:
            text = _drop_references(text, f"{kind} {number}")
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


#: summary the person asked for at the document's start; code builds the block
SUMMARY_PROMPT = """Write the summary the person asked to put at the very start of the
document below: exactly {n} lines, each one sentence, in the document's language and tone.

Rules:
- Only content and numbers stated in the document. Nothing new.
- No title, author, team, date or period line; no heading, no label such as 「요약」 or
  「핵심」, no list marks or numbers.
- Reply with the {n} lines only.

문서 제목: {title}

{document}"""


_SENTENCE_END = re.compile(r"(?:[다요음함됨임]|필요|예정|요청)[.!?。]?$|[.!?。]$")


def _title_line(line: str, heads: set[str]) -> bool:
    """A line restating the title block: its words mostly the title's or the head fields', or
    a short line sharing them that is no sentence (「2026년 10월 3주차(10/12~10/16) 주간 업무
    보고」)."""
    words = [_PARTICLE.sub("", w) for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", line)]
    if not words:
        return True
    shared = sum(1 for w in words if any(w.startswith(h) or h.startswith(w) for h in heads))
    return 5 * shared >= 3 * len(words) or (
        shared > 0 and len(line) <= 40 and not _SENTENCE_END.search(line))


def summary_block(text: str, n: int, title: str, fields: list[str]) -> str | None:
    """The asked summary as the first part's 「핵심」 callout: `n` lines, the title block's
    lines (title, author, period) left out. None when fewer than `n` lines remain."""
    heads = {w for h in [title, *fields] for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", h or "")}
    kept = []
    for line in (text or "").split("\n"):
        line = re.sub(r"^\s{0,4}(?:[-*•·]|\d{1,2}[.)])\s{0,3}", "", line).strip().strip("*_ ")
        if not line or line.startswith("```") or re.fullmatch(r"(?:핵심|요약)\s?[:：]?", line):
            continue
        if not _title_line(line, heads):
            kept.append(line)
    if len(kept) < n:
        # the lines run together as one paragraph: one sentence a line
        kept = [x.strip() for line in kept for x in _SENTENCE_SPLIT.split(line)
                if x.strip() and not _title_line(x.strip(), heads)]
    if len(kept) < n:
        return None
    return "```callout\n핵심\n" + "\n".join(kept[:n]) + "\n```"


def label(plan: Plan, parts: list[str]) -> str:
    """On-screen step label."""
    if plan.scope == "insert":
        return "추가하는 중: " + " · ".join(plan.names)
    if plan.scope == "whole":
        return "문서 전체 고치는 중"
    named = " · ".join(parts[i] for i in plan.targets if i < len(parts))
    return f"고치는 중: {named}"[:120]


__all__ = [
    "MAX_TARGETS",
    "Plan",
    "export_only",
    "export_request",
    "label",
    "meta_talk",
    "obviously_new",
    "outline_block",
    "plan",
    "without_export",
]


#: a sentence count asked of a part: 「두 문장으로」, 「2문장」, 「in two sentences」
_SENTENCES_ASK = re.compile(
    r"(?P<n>\d{1,2}|한|두|세|네|다섯)\s?(?:개의\s?)?문장\s?(?:(?P<most>이내|이하)|으로|로|짜리)"
    r"|\b(?:in|to|into)\s{1,3}(?:just\s{1,3}|only\s{1,3})?"
    r"(?P<m>\d{1,2}|one|two|three|four|five)\s{1,3}sentences?\b", re.I)
_EN_COUNT = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
#: an ask that makes a part shorter
_SHORTENS = re.compile(r"줄여|줄이|짧게|간추|요약|압축|\b(?:shorten|condense|trim|summari[sz]e|"
                       r"cut\s{1,3}down)\b", re.I)
#: names a part states: 「A사」, 「CRM」, 「GPT-4」
_NAME = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z]{1,3}사|[A-Z][A-Z0-9&]{1,15}(?:-\d{1,3})?)"
                   r"(?![A-Za-z0-9])")
#: a sentence's end: 「다.」, 「?」 (not the point in 「5.5%」)
_SENTENCE_END = re.compile(r"[.!?。](?=[\s\"'」』”’)\]]|$)")


def sentences_asked(clause: str) -> tuple[int, bool]:
    """`(sentences, at most)` the clause asks a part to be (「2절을 두 문장으로 줄여줘」 →
    `(2, False)`, 「세 문장 이내로」 → `(3, True)`), else `(0, False)`."""
    if summary_lines_asked(clause):
        return 0, False
    m = _SENTENCES_ASK.search(clause or "")
    if not m:
        return 0, False
    word = (m.group("n") or m.group("m")).lower()
    count = int(word) if word.isdigit() else _COUNT_WORD.get(word) or _EN_COUNT.get(word, 0)
    return count, bool(m.group("most"))


#: a line that is not prose: a caption (「표 2. 진행 사항」), a bold label, a list row
_NOT_PROSE = re.compile(r"(?:표|그림|table|figure)\s?\d{1,3}\s?[.:)]|\*\*[^*\n]{1,200}\*\*$"
                        r"|(?:[-*•·]|\d{1,2}[.)])\s", re.I)


def sentence_count(text: str) -> int:
    """Sentences of a part's prose: each end mark, and a line that stops without one; tables,
    captions, list rows and blocks are not sentences."""
    text = re.sub(r"```[\s\S]*?(?:```|\Z)|<[^<>]{0,2000}>|!\[[^\]]*\]\([^)]*\)", " ", text or "")
    count = 0
    for line in (raw.strip() for raw in text.splitlines()):
        if not line or line.startswith(("|", "#")) or _NOT_PROSE.match(line):
            continue
        line = line.replace("**", "")
        ends = _SENTENCE_END.findall(line)
        last = line.rstrip("\"'」』”’)] ")[-1:]
        count += len(ends) + (0 if last in ".!?。" else 1)
    return count


def _whole_text(text: str) -> str:
    """Everything a part shows as one line of words: prose, list items, table cells, captions
    and boxes; markup and pictures out."""
    text = re.sub(r"<[^<>]{0,2000}>|!\[[^\]]*\]\([^)]*\)", " ", text or "")
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("```") or re.fullmatch(r"[|:\- ]{3,}", line):
            continue
        lines.append(line.replace("|", " ").replace("**", "").lstrip("#").strip())
    return " ".join(lines)


def shortening_asked(asked: str) -> bool:
    """Whether the ask makes a part shorter or names its sentence count."""
    return bool(sentences_asked(asked)[0] or _SHORTENS.search(asked or ""))


def lost_facts(old: str, new: str) -> list[str]:
    """Figures and names `old` states that `new` no longer does anywhere (its prose, tables,
    lists and captions), read the same way on both sides (deck.figures_in)."""
    from app.services import deck

    plain = _whole_text(new)
    shown, amounts = deck.figures_in(plain), deck.amounts_in(plain)
    names = set(_NAME.findall(plain))
    lost = [f[2] for f in deck.figures_in(_whole_text(old))
            if not deck.figure_kept(f, shown, amounts)]
    old_names = dict.fromkeys(_NAME.findall(_whole_text(old)))
    return lost + [n for n in old_names if n not in names]


def added_facts(old: str, new: str, given: str = "") -> list[str]:
    """Figures and dates `new` states whose number neither the part before (`old`) nor the
    request and material (`given`) state (「10월 13일 기준」 out of nowhere), and dates the part
    before did not state (a shortening says less, it dates nothing anew)."""
    from app.services import deck

    known = set(re.findall(r"\d+(?:\.\d+)?", f"{_whole_text(old)} {given}".replace(",", "")))
    figures = [said for number, _unit, said in deck.figures_in(_whole_text(new))
               if not set(re.findall(r"\d+(?:\.\d+)?", number)) <= known]
    dated = [said for said in added_dates(old, new)
             if not any(said in f or f in said for f in figures)]
    return figures + dated


#: a day as a part writes it: 「10월 16일」, 「10/16」
_DAY = re.compile(r"(?<![\d.])(\d{1,2})\s{0,2}월\s{0,2}(\d{1,2})\s{0,2}일"
                  r"|(?<![\d./])(\d{1,2})/(\d{1,2})(?![\d/])")
#: the weekday written right after a day: 「 목요일」, 「(목)」
_DAY_WEEKDAY = re.compile(r"\s{0,2}(?:\(\s{0,2}([월화수목금토일])(?:요일)?\s{0,2}\)"
                          r"|([월화수목금토일])요일)")


def _days(text: str) -> set[tuple[int, int]]:
    return {(int(a or c), int(b or d)) for a, b, c, d in _DAY.findall(text or "")}


def added_dates(old: str, new: str) -> list[str]:
    """Days `new` writes that `old` does not (「10월 16일」), as written."""
    have = _days(old)
    out = dict.fromkeys(m.group(0) for m in _DAY.finditer(new or "")
                        if (int(m.group(1) or m.group(3)), int(m.group(2) or m.group(4)))
                        not in have)
    return list(out)


def without_added_dates(old: str, new: str) -> str:
    """`new` minus the days it puts beside a weekday that `old` does not date (「10월 16일
    목요일에」 for the part's 「목요일에」): the weekday stays, the day the shortening made up
    goes."""
    have = _days(old)
    out, last = [], 0
    for m in _DAY.finditer(new or ""):
        after = _DAY_WEEKDAY.match(new, m.end())
        if after is None or (int(m.group(1) or m.group(3)), int(m.group(2) or m.group(4))) in have:
            continue
        weekday = after.group(1) or after.group(2)
        if f"{weekday}요일" not in (old or "") and f"({weekday})" not in (old or ""):
            # the weekday is new too: left for the checks to name
            continue
        out += [new[last:m.start()], f"{weekday}요일"]
        last = after.end()
    if not out:
        return new
    return "".join([*out, new[last:]])


#: a sentence that points at the part's table or figure: 「표와 같습니다(표 1).」
_TABLE_POINTER = re.compile(r"(?<![가-힣])(?:표|그림)\s?(\d{1,2})(?!\d)"
                      r"|(?:위|아래|다음)\s?(?:의\s?)?(?:표|그림)|(?<![가-힣])(?:표|그림)(?:와|과)\s?같")
#: the caption of a part's table or figure: 「**표 1. 주요 업무」
_CAPTION_NUMBER = re.compile(r"(?m)^\s{0,3}\*{0,2}(표|그림)\s?(\d{1,2})\s?[.:]")
#: the end of a sentence, citations before it: 「 [1].」
_SENTENCE_TAIL = re.compile(r"(?:\s?\[\d{1,3}(?:[,\-–]\s?\d{1,3})*\])*[.!?。]$")


def _only_points(sentence: str) -> bool:
    """Whether the sentence says nothing but where the table or figure is: no figure, no name."""
    from app.services import deck

    if not _TABLE_POINTER.search(sentence):
        return False
    rest = _TABLE_POINTER.sub(" ", sentence)
    return not deck.figures_in(rest) and not _NAME.search(rest)


def fold_table_pointers(text: str, want: int) -> str:
    """`text` with sentences that only point at the part's table or figure (「요약하면 표와
    같습니다(표 1).」) folded into the sentence before them, as its 「(표 1)」, while the part has
    more sentences than the `want` asked: the table is the part's, not one of its sentences."""
    over = sentence_count(text) - want
    if over <= 0:
        return text
    caption = _CAPTION_NUMBER.search(text or "")
    lines = (text or "").split("\n")
    prev: tuple[int, int] | None = None
    sentences: dict[int, list[str]] = {}
    block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            block = not block
            continue
        if (block or not stripped or stripped.startswith(("|", "#"))
                or _NOT_PROSE.match(stripped)):
            continue
        sentences[i] = re.split(r"(?<=[.!?。])\s+", stripped)
        kept: list[str] = []
        for sent in sentences[i]:
            if over > 0 and prev is not None and _only_points(sent):
                named = _NUMBERED_REF.search(sent) or caption
                ref = f"{named.group(1)} {named.group(2)}" if named else ""
                row, j = prev
                target = kept if row == i else sentences[row]
                last = target[j]
                end = len(last) - 40 if len(last) > 40 else 0
                if ref and ref not in last and (tail := _SENTENCE_TAIL.search(last[end:])):
                    cut = end + tail.start()
                    target[j] = f"{last[:cut]}({ref}){last[cut:]}"
                over -= 1
                continue
            kept.append(sent)
            prev = (i, len(kept) - 1)
        sentences[i] = kept
    for i, kept in sentences.items():
        lines[i] = " ".join(kept)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


#: a part named by its number, as an instruction names it: 「2절을」, 「section 2」
_PART_NAMED = re.compile(r"\d{1,2}\s?(?:절|장)|\bsection\s{1,3}\d{1,2}\b|\bthis\s{1,3}section\b",
                         re.I)
#: what an ask about a part's length is about: 「두 문장」, 「줄여」, 「요청」
_ASK_TOPIC = re.compile(r"문장|줄여|줄이|요청|유지해야|sentences?|shorten|request", re.I)


def without_ask_echo(text: str) -> str:
    """The part minus sentences that restate the ask instead of saying the part's content
    (「2절을 두 문장만 유지해야 합니다.」); tables and blocks as they are."""
    out, block = [], False
    for line in (text or "").split("\n"):
        if line.strip().startswith("```"):
            block = not block
        if block or line.strip().startswith(("|", "```")) or not _PART_NAMED.search(line):
            out.append(line)
            continue
        kept = [s for s in re.split(r"(?<=[.!?。])\s+", line)
                if not (_PART_NAMED.search(s) and _ASK_TOPIC.search(s))]
        out.append(" ".join(kept))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def shortening_faults(old: str, new: str, asked: str, given: str = "") -> list[str]:
    """What a shortened part misses of its ask, for the writer: the sentence count asked, the
    section's figures and names gone, figures it brought in. Empty for an ask that does not
    shorten."""
    if not shortening_asked(asked):
        return []
    want, most = sentences_asked(asked)
    faults = []
    got = sentence_count(new)
    if want and (got > want if most else got != want):
        faults.append(f"The section must be exactly {want} sentence{'s' if want > 1 else ''} "
                      f"of its own content; the last version had {got}")
    if lost := lost_facts(old, new):
        faults.append("Keep the section's facts, said briefly if needed: " + ", ".join(lost))
    if added := added_facts(old, new, given):
        faults.append("Add no figure or date the section does not state: " + ", ".join(added))
    if faults:
        faults.append("Write none of these remarks, nor the request, into the text")
    return faults


def count_met(text: str, want: int, most: bool = False) -> bool:
    """Whether the part has the sentence count asked (at most `want` for 「이내」)."""
    got = sentence_count(text)
    return got <= want if most else got == want


def shortening_misses(old: str, new: str, asked: str, given: str = "") -> int:
    """How far a shortened part is from its ask: a sentence count off, each fact gone or
    brought in."""
    want, most = sentences_asked(asked)
    got = sentence_count(new)
    off = bool(want) and (got > want if most else got != want)
    return int(off) + len(lost_facts(old, new)) + len(added_facts(old, new, given))


#: a line addressed to the person about the rewrite (「다음은 요청하신 대로 … 절입니다.」)
_PREAMBLE = re.compile(
    r"^\s{0,4}(?:(?:다음은|아래는|이하는)[^\n]{0,120}?(?:요청|축약|줄인|줄였|수정|고친|다듬은|버전|"
    r"절입니다|내용입니다)|[^\n]{0,80}?요청하신\s?대로[^\n]{0,80}?(?:입니다|습니다)[.:]?\s{0,2}$"
    r"|(?:here\s+is|below\s+is)\b[^\n]{0,120}?\b(?:shortened|revised|rewritten|version|section)"
    r")", re.I)
_NUMBERED_REF = re.compile(r"(?<![가-힣])(표|그림)\s?(\d{1,2})(?!\d)")


def without_preamble(text: str) -> str:
    """The part minus lines that speak to the person about the rewrite instead of to the
    reader (「다음은 요청하신 대로 … 절입니다.」)."""
    lines = (text or "").split("\n")
    kept = [line for line in lines if not (len(line) <= 200 and _PREAMBLE.match(line))]
    if len(kept) == len(lines):
        return text
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def _terms(text: str) -> set[str]:
    """A part's own terms: Hangul words by their first two syllables, names, figures."""
    from app.services import deck

    plain = _whole_text(text)
    words = {w[:2] for w in re.findall(r"[가-힣]{2,}", plain)} - _COMMON_TERMS
    return words | set(_NAME.findall(plain)) | {s for *_, s in deck.figures_in(plain)}


#: words any section of a report uses
_COMMON_TERMS = frozenset({"있습", "합니", "입니", "니다", "있으", "하고", "하여", "위한", "대한",
                           "통해", "이번", "다음", "현재", "진행", "필요", "관련", "내용", "주요"})


def drift_faults(old: str, new: str, others: list[str]) -> list[str]:
    """Why a rewritten part is no longer itself, for the writer: its terms are another
    part's, or it points at a table or figure that is not there."""
    faults = []
    terms, own = _terms(new), _terms(old)
    if terms and own:
        mine = len(terms & own) / len(terms)
        theirs = max((len(terms & _terms(o)) / len(terms) for o in others if o.strip()),
                     default=0.0)
        if mine < 0.5 and theirs > mine:
            faults.append("It reads as another section's content: keep to this section's own "
                          "subject and facts as it stands now")
    shown = {kind for kind, *_ in _blocks_in(new)}
    if any(kind not in shown and pointer.search(new) for kind, pointer in _POINTER.items()):
        faults.append("It points at a table or figure above or below that this section does "
                      "not have")
    numbered = {(kind, n) for text in (new, *others) for kind, _, _, n in _blocks_in(text)
                if n is not None}
    if missing := sorted({f"{k} {n}" for k, n in _NUMBERED_REF.findall(new)
                          if (k, int(n)) not in numbered}):
        faults.append("It cites " + ", ".join(missing) + ", which the document does not have")
    return faults


#: a box in a part: its first line is its title
_CALLOUT = re.compile(r"```callout\n([^\n`]{1,80})\n")


def removed_blocks(old: str, new: str, part: int) -> list[str]:
    """Numbered figures and boxes `old` showed that `new` no longer does (「2절의 그림 1」,
    「2절의 「단가 승인 지연 시 협상 교착」 상자」), for the reply."""
    kept = {(k, n) for k, _, _, n in _blocks_in(new)}
    out = [f"{part}절의 {k} {n}" for k, _, _, n in _blocks_in(old)
           if k == "그림" and n is not None and (k, n) not in kept]
    had, has = _CALLOUT.findall(old), _CALLOUT.findall(new)
    # a box retitled is still there: only as many named as are gone
    gone = [t for t in had if t not in has][:max(0, len(had) - len(has))]
    return out + [f"{part}절의 「{t}」 상자" for t in gone]
