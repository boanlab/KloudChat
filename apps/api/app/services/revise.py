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
    decided = count_change(decided, parts, message)
    decided = notes_everywhere(decided, parts, message)
    return place_insert(decided, parts, message)


#: Plain "start over" phrases; checked before routing.
_START_OVER = re.compile(
    r"(새로\s*(써|작성|만들)|처음부터\s*다시|다른\s*주제로|아예\s*다시|버리고\s*다시)"
)


def obviously_new(message: str) -> bool:
    return bool(_START_OVER.search(message))


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
)


def without_counts(request: str) -> str:
    """Request minus part counts; planner reads the note's count."""
    return " ".join(_COUNT_MENTION.sub(" ", request or "").split())


_TARGET_COUNT = re.compile(
    r"(?:총|전체(?:를)?|모두)?\s{0,8}(\d{1,3})\s{0,8}(?:장|절|쪽|페이지|슬라이드)\s{0,8}(?:으로|로)\s{0,8}"
    r"(?:줄여|늘려|맞춰|만들|다시|재구성|정리|압축|요약)"
)


_NOTES_WORD = r"(?:발표자?\s*노트|스피커\s*노트|노트|speaker\s*notes?|notes?)"
_NOTES_VERB = r"(?:넣어|추가|써|달아|붙여|채워|보강|작성|add|write|fill|put)"
_NOTES_ASK = re.compile(
    rf"{_NOTES_WORD}\s*(?:를|을|만|도)?\s*(?:[^\n]{{0,30}})?{_NOTES_VERB}|"
    rf"{_NOTES_VERB}[^\n]{{0,20}}{_NOTES_WORD}",
    re.I,
)
_BODY_TOO = re.compile(r"본문도|내용도|슬라이드도|글머리표도|함께\s*고쳐|같이\s*고쳐")


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
    """Outline plan for a stated new part count."""
    if plan.scope == "outline" or not parts:
        return plan
    match = _TARGET_COUNT.search(" ".join((message or "").split()))
    if not match:
        return plan
    wanted = int(match.group(1))
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
