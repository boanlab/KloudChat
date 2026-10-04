"""Routes an instruction typed under a finished document to the parts it targets.

Scopes: `parts` (named parts), `whole` (every part), `new` (plan again from
scratch). Nothing here rewrites; the surfaces do that.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from app.services import settings_store

log = logging.getLogger(__name__)

#: Parts one instruction may touch before it becomes a whole-document pass.
MAX_TARGETS = 3

_PROMPT = """사용자가 이미 완성된 문서를 보면서 아래 문장을 입력했다.
무엇을 고쳐 달라는 것인지 판정하라.

문서 제목: {title}

문서의 구성:
{outline}

사용자 입력:
{message}

판정 규칙:
- 특정 부분을 고치라는 것이면 그 부분의 번호를 targets 에 담아라. 최대 {limit}개.
- 어느 부분인지 말하지 않았어도 내용으로 짐작되면 그 부분을 골라라.
- 문서 전체에 걸친 요청(예: "전체적으로 더 간결하게", "말투를 바꿔줘")이면
  scope 를 "whole" 로 하라.
- **새 부분(절·장) 자체를 하나나 둘 추가**하라는 요청("위험 장을 추가해 줘", "결론 앞에
  한계 절을 넣어 줘")이면 scope 를 "insert" 로 하라 — 다른 부분은 그대로 두고 새 부분만
  쓴다. 이미 있는 부분 **안에** 표·문단·수치를 넣으라는 요청("비용 절에 3년 TCO 표를
  넣어 줘")은 insert 가 아니라 그 부분을 고치는 "parts" 다.
  after 에는 새 부분이 들어갈 자리 바로 앞 부분의 번호(맨 앞이면 0), names 에는 새
  부분의 제목, note 에는 새 부분의 내용과 형식(표·열·행 같은 지시를 빠짐없이)을 적어라.
  자리를 말하지 않았으면 마지막 부분(마무리·결론) 앞에 넣는다.
- **구성 자체를 바꾸는 요청**이면 scope 를 "outline" 로 하라. 부분의 수를 늘리거나
  줄이기("20장으로", "3장 더", "절반으로"), 여러 부분을 추가·삭제·병합하기, 순서 바꾸기가
  여기 든다. 이때 note 에는 바뀐 뒤의 구성을 적어라 — 수는 지금 구성을 기준으로
  계산한 최종 수로(지금 6장에 "3장 더" 는 "9장으로 늘린다").
- **다른 주제의 새 문서**를 원하는 것이면 scope 를 "new" 로 하라. 지금 문서를
  고치는 것이 아니라 버리고 다시 쓰는 경우만 해당한다.
- 애매하면 "whole" 이 아니라 가장 가까운 부분 하나를 고르는 쪽이 낫다. 문서
  전체를 다시 쓰는 것은 사용자가 보고 있던 글을 통째로 바꾸는 일이다.

note 는 그 부분을 어떻게 고쳐야 하는지 한두 문장으로. 사용자의 말을 그대로
옮기지 말고, 고칠 내용으로 적어라.

JSON 객체로만 답하라.
예: {{"scope": "parts", "targets": [3], "note": "분량을 절반으로 줄이고 표는 남긴다"}}
예: {{"scope": "insert", "after": 5, "names": ["위험"],
     "note": "표로. 열은 위험·영향·대응, 행은 …"}}"""


@dataclass(slots=True)
class Plan:
    """Where an instruction lands, and what it asks for there."""

    #: `parts` · `whole` · `insert` · `outline` · `new`. `insert` adds new parts after
    #: `after` and leaves the rest alone; `outline` means the document's skeleton
    #: changes and the caller should plan it again from the current one; `new` means
    #: the caller should plan again from nothing.
    scope: str = "new"
    #: Indices into the document's parts, zero-based and already bounded.
    targets: list[int] = field(default_factory=list)
    #: `insert`: the new parts go after this zero-based index (-1 = at the start).
    after: int = -1
    #: `insert`: titles of the new parts, in order.
    names: list[str] = field(default_factory=list)
    #: The instruction as something to do, for the rewrite prompt.
    note: str = ""
    usage: dict[str, int] = field(default_factory=lambda: {"inputTokens": 0, "outputTokens": 0})

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
    """The model's JSON as a `Plan`, bounded to parts that exist; `new` when unusable."""
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
            after = int(parsed.get("after"))  # one-based: 0 = at the start
        except (TypeError, ValueError):
            after = count - 1  # before the closing part
        after = max(-1, min(after - 1, count - 1))
        return Plan(scope="insert", after=after, names=names, note=note)
    if scope != "parts":
        return Plan(note=note)

    numbers: list[int] = []
    for value in parsed.get("targets") or []:
        try:
            # One-based in the prompt, as numbered on screen.
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
    """Read one instruction against one document's outline (`parts`, in order). Never raises."""
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
                    # Room for a reasoning model to think and still answer.
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
    # A reasoning model that ran out of room leaves its answer in the reasoning field.
    text = str(reply.get("content") or "").strip() or str(
        reply.get("reasoning_content") or reply.get("reasoning") or ""
    ).strip()
    decided = _parse(text, len(parts), message)
    decided.usage = {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }
    decided = named_target(into_existing_part(decided, parts, message), parts, message)
    decided = count_change(decided, parts, message)
    decided = notes_everywhere(decided, parts, message)
    return place_insert(decided, parts, message)


#: Phrases that plainly mean "start over"; checked before the routing call.
_START_OVER = re.compile(
    r"(새로\s*(써|작성|만들)|처음부터\s*다시|다른\s*주제로|아예\s*다시|버리고\s*다시)"
)


def obviously_new(message: str) -> bool:
    return bool(_START_OVER.search(message))


#: 「관리형 OpenSearch Service」를 「AWS OpenSearch Service」로 통일해 줘 — a term swap.
_TERM_SWAP = re.compile(
    r"[「\"“']([^」\"”']{1,80})[」\"”']\s*(?:를|을|은|는)?\s*(?:전부|모두|다)?\s*"
    r"[「\"“']([^」\"”']{1,80})[」\"”']\s*(?:로|으로)\s*"
    r"(?:전부|모두|다|문서\s*전체에서)?\s*(?:통일|바꿔|바꾸|변경|고쳐|교체|치환)"
)
_SWAP_SCOPE = re.compile(r"전체|전부|모두|다\s|통일|모든")


#: 「보안 규칙 장과 필수 계정 장의 순서를 바꿔 줘」 — two parts trade places.
_ORDER_SWAP = re.compile(
    r"(?P<a>[가-힣A-Za-z0-9·/&()]+(?:\s+[가-힣A-Za-z0-9·/&()]+){0,3}?)\s*(?:장|절|부분|슬라이드|섹션)?"
    r"(?:이랑|하고|과|와|랑)\s+"
    r"(?P<b>[가-힣A-Za-z0-9·/&()]+(?:\s+[가-힣A-Za-z0-9·/&()]+){0,3}?)\s*(?:장|절|부분|슬라이드|섹션)?"
    r"(?:의|은|는)?\s*(?:순서|자리|위치)\s*(?:를|을)?\s*(?:서로\s*)?(?:바꿔|바꾸|맞바꿔|교체|뒤집)"
)


def order_swap(instruction: str, parts: list[str]) -> tuple[int, int] | None:
    """Indices of the two existing parts the instruction asks to trade places, or `None`.

    A swap is a rearrangement, not a rewrite: when both names match parts (exactly, or
    one name a prefix/word-subset of the other, as `named_target` matches), the two are
    swapped in place and the model is not asked to redraw the deck around them."""
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
    """The part `name` points at: an exact title, then a title containing the phrase,
    then a title sharing every word of the name."""
    squeeze = lambda t: re.sub(r"\s+", "", t).lower()  # noqa: E731
    wanted = squeeze(name)
    if not any(squeeze(part) == wanted or wanted in squeeze(part) for part in parts):
        # 「첫 주 일정이랑」: a particle the pattern could not split off the name.
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
    """`(old, new)` when the instruction is a document-wide replacement of one quoted
    term by another, else None. Done by string replacement: exact, instant, and nothing
    else in the document moves — which is what 「그 외는 바꾸지 마」 means."""
    text = " ".join((instruction or "").split())
    match = _TERM_SWAP.search(text)
    if not match or not _SWAP_SCOPE.search(text):
        return None
    old, new = match.group(1).strip(), match.group(2).strip()
    if not old or old == new:
        return None
    return old, new


def replace_term(value, old: str, new: str) -> tuple[object, int]:
    """`value` with every string field's `old` replaced by `new`, and the count."""
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


#: Words so common in titles and notes alike that their presence names no part.
_GENERIC_TITLE_WORDS = frozenset({
    "다음", "현황", "사항", "결과", "정리", "소개", "개요", "요약", "및", "그리고", "전체", "주요",
})


def carry_parts(
    new: list[dict], old: list[dict], *, is_deck: bool, mentioned: str
) -> tuple[list[dict], int]:
    """The re-planned document with every part the person did not touch carried over
    from the document they were looking at.

    A restructure (「6장으로 줄여」, 「위험 장을 둘 추가해 줘」) is planned again and written
    again; a part whose name survived unchanged and which the instruction never
    mentioned is the same part, and a person who asked to shorten a deck did not ask
    to have its untouched slides retyped. The new part keeps its position, id and
    accent; its words come from the old one.
    """
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
        # A part the instruction names (「지표와 진척은 한 장으로」) is being changed:
        # it is written fresh, particles and all. A generic word the note happens to use
        # (「다음」 in a note about 「다음 단계」-less changes) names nothing.
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
    """The new name when the instruction asks to rename a part and quotes the name
    (「위험 절 제목을 「위험과 대응」으로 바꾸고 …」), else None."""
    text = instruction or ""
    if not _TITLE_ASK.search(text):
        return None
    quoted = list(_QUOTED.finditer(text))
    if not quoted:
        return None
    if len(quoted) == 1:
        return quoted[0].group(1).strip()
    # 「위험」 절 제목을 「위험과 대응」으로: the new name is the one 「…으로/로/라고」 follows.
    for match in quoted:
        tail = text[match.end() : match.end() + 6]
        if re.match(r"\s*(?:으로|로|라고)", tail):
            return match.group(1).strip()
    return quoted[-1].group(1).strip()


_BEFORE = re.compile(r"\s*(?:절|장|부분)?\s*(?:바로\s*)?(?:앞에|앞으로|전에)")
_AFTER = re.compile(r"\s*(?:절|장|부분)?\s*(?:바로\s*)?(?:뒤에|뒤로|다음에|아래에)")


#: 「위험 장을 하나 추가해 줘」 「한계 절을 넣어 줘」 「FAQ 슬라이드 추가」: a new part named
#: outright. The name is what precedes 장/절/슬라이드; a part that already exists is a
#: revision of it, not an insert.
_ADD_PART = re.compile(
    r"([가-힣A-Za-z0-9·\s]{1,16}?)\s*(?:장|절|슬라이드|섹션|페이지)(?:을|를)?\s*"
    r"(?:(한|하나|두|둘|\d)\s*(?:개|장)?\s*)?(?:더\s*)?(?:추가|넣어|넣고|만들어\s*넣)"
)


def explicit_insert(message: str, parts: list[str]) -> Plan | None:
    """An instruction that names one new part to add, as an insert plan placed by its own
    words (「요청 사항 장 앞에」), without asking the planner; `None` otherwise — several
    parts, a part that already exists, or a count change are the planner's to read."""
    text = " ".join((message or "").split())
    if re.search(r"\d+\s*장으로|줄여|늘려|합쳐|합치|삭제|빼", text):
        return None
    match = _ADD_PART.search(text)
    if not match or (match.group(2) or "한") not in ("한", "하나", "1"):
        return None
    name = re.sub(r"^(?:새로\s*|새\s*|그리고\s*)", "", match.group(1)).strip(" ·")
    name = re.split(r"[.,。]\s*", name)[-1].strip()
    # 「결론 뒤에 한계 절을」: the placement words come before the name.
    name = re.split(r"\s(?:앞|뒤|다음|사이)(?:에|으로|로)?\s+", " " + name)[-1].strip()
    if not (1 <= len(name) <= 14) or any(name == p.strip() for p in parts):
        return None
    plan = Plan(scope="insert", after=len(parts) - 1, names=[name], note=text[:200])
    return place_insert(plan, parts, text)


def place_insert(plan: Plan, parts: list[str], message: str) -> Plan:
    """An insert whose words name the neighbour (「요청 사항 장 앞에」, 「결론 뒤에」) goes
    exactly there; the planner's guess is kept only when the words say nothing."""
    if not plan.inserts:
        return plan
    text = " ".join((message or "").split())
    best: tuple[int, int] | None = None  # (position in text, after-index)
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
#: 「<이름> 장을」: the words a person uses to point at a part by its name.
_NAMED_PHRASE = re.compile(
    r"([가-힣A-Za-z0-9·/&()]+(?:\s+[가-힣A-Za-z0-9·/&()]+){0,3})\s+(?:장|절|부분|슬라이드|섹션)"
    r"\s*(?:을|를|은|는|의|에서|만)"
)


_STRUCTURAL_ASK = re.compile(
    r"추가|삭제|빼\s*줘|빼고|없애|합쳐|합치|병합|나눠|분리|순서|앞으로\s*옮|뒤로\s*옮|옮겨|"
    r"\d+\s*(?:장|절|쪽|페이지|슬라이드)\s*(?:으로|로)"
)


def named_target(plan: Plan, parts: list[str], message: str) -> Plan:
    """When the words name exactly one existing part as the thing to change (「다음 단계
    장을 연표로」), that part is the target, whatever the planner guessed — a different
    part, or a restructure of the whole deck. Two names, or none, leave the planner's
    reading alone, as do a whole-document or insert plan and any instruction that asks
    for a structural change (add, remove, merge, reorder, a new count)."""
    if plan.scope not in ("parts", "outline"):
        return plan
    if plan.scope == "parts" and not plan.targets:
        return plan
    text = " ".join((message or "").split())
    if _STRUCTURAL_ASK.search(text):
        # Merging, adding, removing or reordering parts is the planner's call to make.
        return plan
    # A name counts only when the words point at it as a part (「다음 단계 장을」): a date
    # in the instruction that happens to be a slide's title (「9/22 오픈」) is not a mention.
    mentioned = [
        i for i, name in enumerate(parts)
        if len(name.strip()) >= 2 and re.search(rf"{re.escape(name.strip())}{_PART_WORD}", text)
    ]
    if not mentioned:
        # 「다음 단계 장을 …」 names a slide called 「다음 단계와 마무리」: the phrase before
        # 장/절 is the name's core, and the one part carrying it is the one meant.
        phrase = _NAMED_PHRASE.search(text)
        if phrase:
            core = phrase.group(1).strip()
            holders = [i for i, name in enumerate(parts) if core and core in name]
            words = [w for w in core.split() if len(w) >= 2]
            if not holders and words:
                # 「오버헤드 결과 장을」 for a slide called 「오버헤드 측정 결과」: every word
                # of the phrase is in that one name, so that is the part.
                holders = [i for i, name in enumerate(parts) if all(w in name for w in words)]
            shares_a_word = any(any(w in name for w in words) for name in parts)
            if len(holders) == 1:
                mentioned = holders
                text = text.replace(core, parts[holders[0]].strip(), 1)
            elif not holders and not shares_a_word and len(core) >= 2 and plan.scope == "parts":
                # 「다음 단계 장을 연표로」 when no slide is called that: the person believes
                # the deck has one. Guessing another slide and rewriting it is the one
                # thing not to do; the part is made, where a last part belongs.
                last_is_closing = bool(parts) and bool(_CLOSING_NAME.search(parts[-1]))
                closing = len(parts) - 1 if last_is_closing else len(parts)
                return Plan(
                    scope="insert", after=closing - 1, names=[core], note=plan.note,
                    usage=plan.usage,
                )
    # A name inside a longer named part (「다음 단계」 inside 「요청 사항 및 다음 단계」) is the
    # longer part's mention, not its own.
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


def into_existing_part(plan: Plan, parts: list[str], message: str) -> Plan:
    """An `insert` that the words aim *into* an existing part (「비용 절에 표를 넣어 줘」)
    is that part's edit, not a new part beside it."""
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


_FENCE = re.compile(r"```mermaid\n.*?```(?:\n+\*그림[^*\n]*\*)?", re.S)
_REMOVE_FIGURE = re.compile(
    r"(?:그림|도식|도해|다이어그램|흐름도|구조도|비교도|개념도)\s*(?:은|을|는|를)?\s*"
    r"(?:빼|지워|삭제|없이|제거)"
)
_ANY_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.S)


def _ascii_art(block: str) -> bool:
    """A code block that is a drawing in box characters, not code."""
    lines = [ln for ln in block.splitlines() if ln.strip()]
    if len(lines) < 3:
        return False
    box = re.compile(r"[+|]-{2,}|-{2,}[+>]|\|\s{2,}\S.*\||[┌┐└┘├┤─│→←↑↓]")
    boxy = sum(1 for ln in lines if box.search(ln))
    return boxy * 2 >= len(lines)


def keep_figure(old: str, new: str, instruction: str) -> str:
    """The rewritten section with its figure back, when the rewrite dropped the mermaid
    fence and the person did not ask for the figure to go.

    A rewrite is prose work; the figure under the prose belongs to the section the way
    its table does, and a model that was told to add one sentence has no business
    losing it. A new fence in the rewrite (the figure itself was changed) is kept as is."""
    if "```mermaid" in (new or "") or "```mermaid" not in (old or ""):
        return new
    if _REMOVE_FIGURE.search(instruction or ""):
        return new
    fence = _FENCE.search(old)
    if not fence:
        return new
    # A model asked to touch the flow sometimes redraws it as ASCII boxes in a code
    # block; that drawing goes, the real figure comes back.
    body = _ANY_FENCE.sub(lambda m: "" if _ascii_art(m.group(1)) else m.group(0), new or "")
    return f"{body.rstrip()}\n\n{fence.group(0).strip()}"


_COUNT_MENTION = re.compile(
    r"(?:장수|분량|길이)?\s*[:：]?\s*(?:총|전체)?\s*\d{1,3}\s*(?:장|쪽|페이지|슬라이드|절)\s*"
    r"(?:안팎|내외|정도|으로|로|을|를|이|가|은|는)?"
)


def without_counts(request: str) -> str:
    """The request with its part counts taken out, so a restructure's own count
    (「6장으로 줄인다」 in the note) is the only one the planner reads. Two counts in one
    text cancel to none, and an outline with no count to hit went to thirty-nine slides."""
    return " ".join(_COUNT_MENTION.sub(" ", request or "").split())


_TARGET_COUNT = re.compile(
    r"(?:총|전체(?:를)?|모두)?\s*(\d{1,3})\s*(?:장|절|쪽|페이지|슬라이드)\s*(?:으로|로)\s*"
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
    """Whether the instruction is about speaker notes and nothing else (「모든 장에 발표자
    노트를 두 문장씩 넣어 줘」): the slides' words are not to move."""
    text = " ".join((message or "").split())
    return bool(_NOTES_ASK.search(text)) and not _BODY_TOO.search(text)


_EVERY_PART = re.compile(
    r"모든\s*(?:장|절|슬라이드|부분)|장마다|절마다|전체\s*(?:장|슬라이드)|각\s*장|"
    r"every\s+slide|all\s+slides",
    re.I,
)


def notes_everywhere(plan: Plan, parts: list[str], message: str) -> Plan:
    """A notes request for every slide is a whole-deck pass, however many parts the
    planner picked; the planner's cap of three targets is for rewrites of words."""
    if plan.scope in ("outline", "new") or not parts:
        return plan
    text = " ".join((message or "").split())
    if notes_only(text) and _EVERY_PART.search(text):
        return Plan(
            scope="whole", targets=list(range(len(parts))), note=plan.note, usage=plan.usage
        )
    return plan


def count_change(plan: Plan, parts: list[str], message: str) -> Plan:
    """「6장으로 줄여 줘」 against a nine-part document is a restructure, whatever the
    planner read it as — a partial edit, or a request for a new document: the words
    state a count the document on screen does not have."""
    if plan.scope == "outline" or not parts:
        return plan
    match = _TARGET_COUNT.search(" ".join((message or "").split()))
    if not match:
        return plan
    wanted = int(match.group(1))
    if wanted <= 0 or wanted == len(parts):
        return plan
    # The note carries the target count once and no other: 「9장을 6장으로」 would cancel
    # to no count at all for the planner that has to hit it. A word leads the number:
    # merged as a bullet, 「- 6장으로」 reads as a range to the count parser.
    note = f"장수는 {wanted}장으로 맞춘다. {without_counts(plan.note)}".strip()
    return Plan(scope="outline", note=note[:600], usage=plan.usage)


#: How much of a part's own words the restructure planner and writer see.
_PART_TEXT_CHARS = 600


def outline_block(parts: list[str], texts: list[str] | None = None) -> str:
    """The current skeleton, for the planner that draws the next one from it — with each
    part's words when given, so a merged part keeps what the parts it absorbs said (the
    risk table a later turn added is in the deck, not in the original request)."""
    lines = []
    for i, name in enumerate(parts):
        lines.append(f"{i + 1}. {name}")
        text = " ".join((texts[i] if texts and i < len(texts) else "").split())
        if text:
            lines.append(f"   내용: {text[:_PART_TEXT_CHARS]}")
    listed = "\n".join(lines)
    return (
        "# 지금 문서의 구성\n"
        f"{listed}\n"
        "이 구성을 출발점으로 요청대로 구성을 다시 짜라. 요청한 수가 있으면 정확히 그 "
        "수로 맞추고, 합치거나 빼라고 한 부분만 합치거나 빼라. 그 밖의 부분은 같은 제목으로 "
        "그대로 두고, 요청이 말하지 않은 부분을 빼거나 주제를 바꾸지 마라. 지금 문서에 있는 "
        "내용(수치·요청 사항·일정)은 어느 부분으로든 옮겨 남긴다. 합쳐지는 부분은 위 「내용」의 "
        "표·수치·날짜를 그대로 가져와 한 부분에 담는다 — 요청 원문에 없는 것도 지금 문서에 "
        "있으면 남긴다."
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
    """A restructure that merged a table slide into another (「이슈·위험도 한 장으로」) keeps
    the absorbed slide's rows: an old table slide whose title is gone from the deck and
    whose name the instruction says lends its rows, by first cell, to the new table slide
    that shares a word with it — up to six rows, header included."""
    said = (mentioned or "").lower()
    # The writer's own repeats give up their slots before absorbed rows ask for them.
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
            # 「이슈·위험도 한 장으로」: the slide named with the gone one in the same phrase
            # is where it went (the one whose title says 「이슈」).
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
            # 「p95 응답시간 미달」 beside 「p95 2.8초」, 「조직도 API 보안 미완」 beside 「조직도
            # API」: the host already has that row under its own words — every word of the
            # key is there, or a distinctive one (three Hangul syllables, or a name with a
            # digit like p95) is.
            distinctive = {
                t for t in key_tokens if (len(t) >= 3 and not t.isascii()) or re.search(r"\d", t)
            }
            covered = bool(key_tokens) and (
                all(t in have for t in key_tokens) or any(t in have for t in distinctive)
            )
            if key and key not in have and not covered:
                cells = [str(c) for c in row][:width]
                host["rows"].append(cells + [""] * (width - len(cells)))
    # An absorbed row that says what a row already says (in other words) goes too.
    return _dedupe_tables(new)


def carry_by_content(new: list[dict], old: list[dict], *, mentioned: str) -> list[dict]:
    """A timeline the planner renamed (「다음 단계」 → 「오픈까지 남은 일정」) is still the
    person's timeline: when a new timeline shares a date with an old one and the
    instruction did not name the old slide, the old pairs (the dates the person listed)
    replace the rewritten ones under the new title."""
    said = (mentioned or "").lower()

    def keys(slide: dict) -> set[str]:
        out = set()
        for pair in slide.get("timeline") or []:
            if isinstance(pair, list) and pair:
                digits = re.findall(r"\d+", str(pair[0]))
                if digits:
                    out.add("/".join(str(int(x)) for x in digits[:2]))
        return out

    # A restructure carries no instruction to drop dates (that is a revision of the
    # timeline itself), so the planner's note naming the slide does not exclude it.
    del said
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
    """The word sets of each 「A와 B는 한 장으로」 phrase in an instruction, one per
    comma- or sentence-separated segment, particles stripped."""
    out: list[set[str]] = []
    for segment in re.split(r"[.。,，\n;]", said or ""):
        m = re.search(r"^(.*?)\s*(?:한\s*장|하나)\s*(?:으로|에)", segment)
        if m:
            words = {
                re.sub(r"(?:은|는|이|가|와|과|도|을|를)$", "", t) for t in _title_tokens(m.group(1))
            }
            words = {w for w in words if w}
            if words:
                out.append(words)
    return out


def _destination(gone: dict, tables: list[dict], said: str) -> dict | None:
    """The new table slide a gone table's rows belong to: one sharing a title word the
    instruction mentions, else the one named with it in the same 「… 한 장으로」 phrase."""
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
    """A restructure's writer, shown every old table, sometimes pours the progress rows
    into the issues table. A row whose first cell is a row of a gone table that belongs to
    a different slide (or to none) leaves the table it strayed into; a table keeps at
    least two body rows."""
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
    """A timeline the person asked for (「연표로 바꿔 줘」) survives a restructure that redrew
    its slide as steps or bullets: when the old deck had a timeline the instruction did
    not name and the new deck has none, the new slide about the same thing (a shared title
    word, or a title about 단계·일정·로드맵) becomes that timeline again."""
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
            # The planner dropped the timeline slide to make the count. A slide nobody
            # asked for gives up its slot instead: an agenda the request never named,
            # else a one-line statement that is not one of the person's parts.
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
    """An agenda slide carried over from the old deck lists the new deck's slides."""
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
    """The document on screen, as material for a restructure's writer: what a merged or
    renamed part absorbs is here, numbers and dates the original request never had."""
    chunks = []
    for i, name in enumerate(parts):
        text = " ".join((texts[i] if i < len(texts) else "").split())
        if text:
            chunks.append(f"## {name}\n{text[:_PART_TEXT_CHARS]}")
    return (
        "# 지금 문서의 내용\n"
        "합치거나 이름을 바꾸는 부분은 아래 표·수치·날짜·항목을 그대로 옮겨 담는다. "
        "요청 원문에 없어도 여기 있으면 이 문서의 사실이다.\n\n" + "\n\n".join(chunks)
    )


def label(plan: Plan, parts: list[str]) -> str:
    """What the step on screen says this pass is doing."""
    if plan.scope == "insert":
        return "추가하는 중: " + " · ".join(plan.names)
    if plan.scope == "whole":
        return "문서 전체 고치는 중"
    named = " · ".join(parts[i] for i in plan.targets if i < len(parts))
    return f"고치는 중: {named}"[:120]


__all__ = ["MAX_TARGETS", "Plan", "label", "obviously_new", "outline_block", "plan"]
