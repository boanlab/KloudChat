"""Self-drawn document figures: one planning call, each drawn natively; undrawable ones dropped."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any

from app.services import diagram_render, figure_facts
from app.services.report_export import diagram_key

log = logging.getLogger(__name__)

#: figure kinds with reader-facing names
FIGURES = {"method": "구조도", "flow": "흐름도", "compare": "비교도", "concept": "개념도"}

#: figures per document
MAX_FIGURES = 3

Completion = Callable[[str, list[dict[str, str]], str, int], Awaitable[tuple[str, dict]]]
Wrap = Callable[[str], list[dict[str, str]]]

_STRICT_RULES = """규칙:
- 수치의 비교는 표나 차트지 도식이 아니다. 제안하지 마라.
- 「(표 있음)」이라고 적힌 부분은 이미 표로 비교한 곳이다. 거기에 비교도를 다시
  그리지 마라. 비교도는 구조나 흐름 자체가 다를 때만 그린다.
- 내용이 나열이나 서술뿐이고 구조·흐름·대비·층위가 없으면 제안하지 마라.
  **없는 것이 정상이다.** 억지로 채우지 마라. 이점·효과·특징·요구 사항의 나열은
  개념도가 아니다. 개념도는 상위 개념 아래 하위 개념이 놓이는 층위나 개념 사이의
  관계가 본문에 **적혀 있을 때만** 그린다.
"""

#: report planner rules: every part eligible; tables don't exclude comparisons
_REPORT_RULES = """규칙:
- 보고서는 그림이 있어야 읽힌다. 각 부분에서 **구성(요소와 관계)·흐름(단계·순서)·
  대비(두 쪽 또는 여러 유형)·층위(상위·하위 개념, 분류)** 가운데 하나라도 본문에
  적혀 있으면 그 부분에 도식을 제안하라. 분류(대상 환경 3갈래, 기능 4축 같은 것)는
  개념도, 배포·처리 경로는 구조도나 흐름도, 두 방식의 대비는 비교도다.
- 표가 있는 부분에도 비교도를 그릴 수 있다. 표의 숫자를 옮기지 말고, 두 쪽의
  구조·흐름·구성 요소가 어떻게 다른지를 그려라.
- 수치 자체(점유율, 금액, 비율)를 보여 주는 그림은 차트의 일이다. 제안하지 마라.
- 서론·요약·결론·참고문헌처럼 구조가 없는 부분은 건너뛴다.
"""

_PROMPT = """아래는 {what}의 부분들이다. 각 부분의 제목과 내용을 읽고, **글보다 도식이 더
잘 전달하는 곳**에만 도식을 하나씩 제안하라.

원래 요청:
{request}

부분:
{parts}

도식의 종류 — 넷 중 하나:
- method: 구조도. 구성 요소와 그 사이의 관계·데이터 흐름이 내용일 때.
- flow: 흐름도. 단계·절차·순서, 입력이 결과가 되기까지가 내용일 때.
- compare: 비교도. 기존과 제안, 또는 두 안의 대비가 내용일 때.
- concept: 개념도. 개념들의 층위와 관계가 내용일 때.

{rules}{marked_rule}- 한 부분에 하나, 서로 다른 부분에, 최대 {limit}개.
- description 에는 그릴 내용을 **{language} 구체적으로** 적는다: 구성 요소나 단계의
  이름, 그 사이의 관계와 방향, 비교도라면 양쪽의 이름과 마주 볼 항목. 이름은 그
  부분의 본문에 쓰인 용어 그대로. 본문에 없는 요소를 지어내지 마라.
- caption 은 {caption_rule}

JSON 배열로만 답하라. 없으면 [].
예: [{{"part": 3, "figure": "flow", "description": "입력 문서가 검색기 → 계획기 → \
스타일 조정기를 차례로 거쳐 초안이 되고, 검토기의 의견이 계획기로 되돌아간다", \
"caption": "생성 흐름"}}]"""


_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}", re.M)


def _has_table(text: str) -> bool:
    """Whether a part has a Markdown table."""
    return bool(_TABLE_RULE.search(text or ""))


@dataclass(frozen=True, slots=True)
class Planned:
    """One planned figure on part index (0-based)."""

    index: int
    figure: str
    description: str
    caption: str
    #: part text; figure must agree with its tables
    context: str = ""
    #: whole document, for the recommended option
    document: str = ""
    #: steps the request numbers (①…): a flow of that procedure shows every one
    steps: tuple[str, ...] = ()
    #: the request the document was written from: facts a slide's few words left out
    request: str = ""
    #: labels and caption language (`ko`, `en`)
    language: str = "ko"
    #: things and how they relate: every line names its relation, never a chain of steps
    relation: bool = False


#: what a part's title says it shows (first match wins)
_RELATION_TITLE = re.compile(
    r"관계|연관|상호\s*작용|\b(?:relationships?|relations|interactions?)\b", re.I)
_STRUCTURE_TITLE = re.compile(
    r"구조|아키텍처|구성\s*(?:요소|도)|시스템\s*구성|전체\s*구성|컴포넌트|토폴로지|"
    r"\b(?:architecture|structure|components?|topology|anatomy)\b",
    re.I,
)
_FLOW_TITLE = re.compile(
    r"흐름|(?:동작|작동|처리)\s*(?:원리|방식|과정|절차)|파이프라인|생명\s*주기|라이프\s*사이클|"
    r"\b(?:flow|workflow|pipeline|lifecycle)\b|\bhow\b.{1,30}\bworks?\b",
    re.I,
)
#: 「컨트롤 플레인/워커 노드 구조」: what the request names as a structure, relation or flow
_ASKED_TOPIC = re.compile(
    r"(?P<what>[^,.;:!?\n「」『』'\"()（）]{2,60}?)\s{0,2}(?:의\s{0,2})?"
    r"(?P<word>구조|아키텍처|관계|흐름|처리\s{0,2}과정|동작\s{0,2}원리)"
)
_TOPIC_KIND = {"구조": "method", "아키텍처": "method", "관계": "relation"}
#: words that only frame a phrase (「…를 설명해줘」)
_TOPIC_STOP = frozenset({"설명", "그림", "그려", "넣어", "주고", "만들", "그리고", "핵심"})


def _topic_stem(word: str) -> str:
    """A word's stem for title matching: Latin whole, Hangul without its ending."""
    word = word.lower()
    return word if re.match(r"[a-z0-9]", word) else word[:max(2, len(word) - 2)]


def asked_topics(request: str) -> list[tuple[str, tuple[str, ...]]]:
    """(kind, stems) for each structure, relation or flow the request names with two or more
    words of its own (「컨트롤 플레인/워커 노드 구조」 → method, 컨트롤·플레·워커·노드)."""
    out = []
    for m in _ASKED_TOPIC.finditer(request or ""):
        tokens = re.findall(r"[가-힣A-Za-z0-9]{2,}", m.group("what"))[-4:]
        stems = tuple(dict.fromkeys(_topic_stem(t) for t in tokens
                                    if _topic_stem(t) not in _TOPIC_STOP))
        if len(stems) >= 2:
            word = re.sub(r"\s", "", m.group("word"))
            out.append((_TOPIC_KIND.get(word, "flow"), stems))
    return out


def wanted_figure(title: str, request: str = "") -> str:
    """`relation`, `method` or `flow` when a part's title (or a topic the request names that
    the title covers) is a relation, a structure or a flow; else ``."""
    if _RELATION_TITLE.search(title or ""):
        return "relation"
    if _STRUCTURE_TITLE.search(title or ""):
        return "method"
    if _FLOW_TITLE.search(title or ""):
        return "flow"
    compact = re.sub(r"\s", "", (title or "").lower())
    for kind, stems in asked_topics(request):
        if sum(stem in compact for stem in stems) >= max(2, len(stems) - 1):
            return kind
    return ""


#: a title naming the figure itself: drawn even when the planner passes it by
_NAMES_FIGURE = re.compile(r"구조도|흐름도|관계도|개념도|아키텍처|\bdiagram\b", re.I)

#: listing tags for marked parts
_MARK_TAG = {"method": "구성 요소와 연결", "relation": "대상 사이의 관계", "flow": "단계·순서"}

_MARKED_RULE = """- Parts tagged 「(구성 요소와 연결)」, 「(대상 사이의 관계)」 or 「(단계·순서)」
  have a title that names a structure, a relationship or a flow: propose a figure for each of
  them unless its text has nothing to draw. A relationship (「관계」) is method: the things as
  boxes and each line named with how the two relate, never the things in a row as steps.
"""


async def plan(
    *,
    parts: list[tuple[str, str]],
    eligible: Iterable[int],
    request: str,
    model: str,
    api_key: str,
    complete: Completion,
    slide: bool,
    wrap: Wrap | None = None,
    limit: int = MAX_FIGURES,
    at_least: int = 0,
    marked: dict[int, str] | None = None,
    language: str = "ko",
) -> tuple[list[Planned], dict[str, int]]:
    """Figures for (title, text) parts, eligible indices only; never raises. `marked`: parts
    whose titles name a structure, relation or flow (`wanted_figure`), each given a figure."""
    allowed = sorted(set(eligible))
    if not allowed:
        return [], {"inputTokens": 0, "outputTokens": 0}
    marked = {i: kind for i, kind in (marked or {}).items() if i in allowed}
    limit = min(len(allowed), max(limit, min(len(marked), MAX_MARKED)))

    def tag(index: int) -> str:
        return f" ({_MARK_TAG[marked[index]]})" if index in marked else ""

    listed = "\n\n".join(
        f"[{index + 1}] {parts[index][0]}{tag(index)}"
        f"{' (표 있음)' if _has_table(parts[index][1]) else ''}\n"
        f"{parts[index][1][:1200] or '(내용 없음)'}"
        for index in allowed
    )
    english = language == "en"
    prompt = _PROMPT.format(
        what="발표 슬라이드" if slide else "보고서",
        request=request[:1500],
        parts=listed[:9000],
        limit=limit,
        rules=_STRICT_RULES if slide else _REPORT_RULES,
        marked_rule=_MARKED_RULE if marked else "",
        language="영어로(in English)" if english else "한국어로",
        caption_rule=(
            ("a name of at most four English words, without the word 「Figure」."
             if english else "12자 안쪽의 이름이다. 「그림」이라는 말은 넣지 않는다.")
            if slide
            else ("one English sentence saying what the figure shows." if english
                  else "무엇을 보여 주는 그림인지 말하는 한 문장이다.")
        ),
    )
    messages = wrap(prompt) if wrap else [{"role": "user", "content": prompt}]
    usage = {"inputTokens": 0, "outputTokens": 0}
    try:
        text, usage = await complete(model, messages, api_key, 900)
    except Exception as exc:  # noqa: BLE001 — a document without figures is still a document
        log.info("figure planning failed: %s", exc)
        text = "[]"
    planned = parse(text, limit=limit, eligible=allowed)
    asked_for = not planned and asks_for_diagrams(request)
    short = len(planned) < min(at_least, len(allowed))
    missed = [i for i in marked if all(row.index != i for row in planned)]
    if asked_for or short or missed:
        # explicitly requested, below at_least or a marked part passed by: one retry with reason

        if missed:
            named = ", ".join(f"[{i + 1}] {parts[i][0]}" for i in missed)
            reason = (f"제목이 구조·관계·흐름을 말하는 부분({named})에 도식이 없다. "
                      "그 부분들과, 구성·흐름·대비·분류가 뚜렷하게 적힌 부분을")
        elif asked_for:
            asked = "·".join(dict.fromkeys(_ASKS_DIAGRAM.findall(request or ""))) or "도식"
            reason = (f"원래 요청이 {asked}를 명시적으로 요구한다. "
                      "요구한 종류마다 그것이 적힌 부분을")
        else:
            reason = (f"이 문서에는 도식이 적어도 {at_least}개 필요하다. 구성·흐름·대비·분류가 "
                      "가장 뚜렷하게 적힌 부분을")
        nudged = prompt + (
            f"\n\n{reason} 찾아 하나씩 제안하라(구조가 적힌 부분엔 method, 단계·순서가 적힌 "
            "부분엔 flow, 두 안의 대비가 적힌 부분엔 compare, 분류엔 concept). "
            "정말 없을 때만 [] 로 답하라."
        )
        try:
            text, more = await complete(
                model, wrap(nudged) if wrap else [{"role": "user", "content": nudged}], api_key, 900
            )
            usage = {k: usage.get(k, 0) + more.get(k, 0) for k in ("inputTokens", "outputTokens")}
            again = parse(text, limit=limit, eligible=allowed)
            if missed:
                # the first answer kept; the retry adds what it passed by
                planned += [row for row in again if all(row.index != p.index for p in planned)]
            else:
                planned = again
        except Exception as exc:  # noqa: BLE001
            log.info("figure planning retry failed: %s", exc)
    planned = settle_marked(planned, marked, parts, limit=limit)
    document = "\n\n".join(text for _, text in parts)[:40000]
    return [replace(row, context=parts[row.index][1], document=document, language=language)
            for row in planned], usage


#: figures a deck may take when that many of its titles name a structure, relation or flow
MAX_MARKED = 5


def settle_marked(
    planned: list[Planned], marked: dict[int, str], parts: list[tuple[str, str]], *, limit: int
) -> list[Planned]:
    """Planned figures held to what marked titles say: a structure or relation is never a flow,
    a relation names its lines; a title naming its figure (「핵심 오브젝트 관계도」) that the
    planner passed by is drawn from the part's own words. Marked parts first within `limit`."""
    out = []
    for row in planned:
        kind = marked.get(row.index, "")
        if kind == "relation":
            row = replace(row, figure="method", relation=True)
        elif kind == "method" and row.figure == "flow":
            row = replace(row, figure="method")
        out.append(row)
    for index, kind in marked.items():
        title, text = parts[index]
        if any(row.index == index for row in out) or not _NAMES_FIGURE.search(title):
            continue
        if len(text.strip()) < 8:
            continue
        out.append(Planned(index=index, figure="flow" if kind == "flow" else "method",
                           description=f"{title}: " + " / ".join(text.split("\n"))[:1100],
                           caption=title[:40], relation=kind == "relation"))
    out.sort(key=lambda row: (row.index not in marked, row.index))
    return sorted(out[:limit], key=lambda row: row.index)


_ASKS_DIAGRAM = re.compile(
    r"구조도|흐름도|비교도|개념도|다이어그램|도식|도해|아키텍처\s*(?:그림|도)|"
    # structure/flow asked as a picture = diagram
    r"(?:구조|흐름|관계|과정|절차|단계)[^\n.]{0,12}?그림으로|그림으로\s*(?:그려|보여|표현|나타)|시각화|도식화|"
    r"\b(?:diagram|flowchart|architecture\s+figure)\b",
    re.I,
)


def asks_for_diagrams(request: str) -> bool:
    """Whether the request names a diagram."""
    return bool(_ASKS_DIAGRAM.search(request or ""))


def parse(text: str, *, limit: int, eligible: Iterable[int]) -> list[Planned]:
    """Planner answer as Planned rows: known kinds, eligible parts, one each."""
    block = text[text.find("[") : text.rfind("]") + 1] if "[" in text and "]" in text else ""
    try:
        parsed = json.loads(block)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    allowed = set(eligible)
    out: list[Planned] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("part", 0)) - 1
        except (TypeError, ValueError):
            continue
        figure = str(item.get("figure") or "").strip().lower()
        description = " ".join(str(item.get("description") or "").split())
        if index not in allowed or figure not in FIGURES or len(description) < 8:
            continue
        if any(row.index == index for row in out):
            continue
        out.append(
            Planned(
                index=index,
                figure=figure,
                description=description[:1200],
                caption=" ".join(str(item.get("caption") or "").split())[:120],
            )
        )
        if len(out) >= limit:
            break
    return out


_WORD = re.compile(r"[가-힣A-Za-z0-9]{2,}")


def _flow_words(text: str) -> set[str]:
    """Step words of a flow: labels of a mermaid flowchart, else the text's words."""
    from app.services import diagram_shapes

    graph = diagram_shapes.parse(text) if text.lstrip().startswith(("flowchart", "graph")) \
        else None
    if graph is not None:
        text = " ".join(node.label for node in graph.nodes.values())
    return {w[:4] for w in _WORD.findall(text or "")}


def drop_repeated_flows(planned: list[Planned], drawn: Iterable[str]) -> list[Planned]:
    """Planned flows minus ones redrawing a procedure already drawn (or planned before):
    two figures of one procedure end up disagreeing."""
    seen = [words for source in drawn if (words := _flow_words(source))]
    out = []
    for row in planned:
        if row.figure == "flow":
            words = _flow_words(row.description)
            if any(len(words & other) >= max(3, 0.5 * min(len(words), len(other)))
                   for other in seen):
                log.info("report figures: %r repeats a drawn flow", row.caption[:60])
                continue
            seen.append(words)
        out.append(row)
    return out


_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_PARTICLE = re.compile(r"(?:에서|으로|에게|에|이|가|을|를|은|는|의|로|와|과|시)$")


def given_steps(request: str) -> tuple[str, ...]:
    """Steps the request numbers ① ② ③ … in order (3 or more), else ()."""
    found: list[tuple[str, str]] = []
    depth = 0
    for ch in request or "":
        if ch in "(（":
            depth += 1
        elif ch in ")）":
            depth = max(0, depth - 1)
        if ch in _CIRCLED and depth == 0:
            found.append((ch, ""))
        elif found and ch == "\n":
            found[-1] = (found[-1][0], found[-1][1] + "\0")  # a step ends with its line
        elif found and not found[-1][1].endswith("\0"):
            found[-1] = (found[-1][0], found[-1][1] + ch)
    found = [(n, text.rstrip("\0").split("→")[0].strip(" ,·/-")) for n, text in found]
    numbers = [_CIRCLED.index(n) for n, _ in found]
    if len(found) < 3 or numbers != list(range(len(found))):
        return ()
    return tuple(text for _, text in found)


def _stems(text: str) -> set[str]:
    return {_PARTICLE.sub("", w) for w in _WORD.findall(text or "")} - {""}


def covers_steps(source: str, steps: Iterable[str]) -> bool:
    """Whether a flowchart of these steps draws them all and nothing else; True when it is
    about something else (under 3 steps named)."""
    from app.services import diagram_shapes

    graph = diagram_shapes.parse(source)
    if graph is None:
        return True
    steps = list(steps)
    labels = [_stems(node.label) for node in graph.nodes.values()]
    named = [any(_stems(step) & label for label in labels) for step in steps]
    if sum(named) < 3:
        return True
    spare = [label for label in labels if not any(_stems(step) & label for step in steps)]
    return all(named) and not spare


def steps_flowchart(steps: Iterable[str]) -> str:
    """Flowchart of the given steps in order; 「(미비 시 ②로 돌아가 …)」 becomes a back edge."""
    steps = list(steps)
    lines = ["flowchart LR"]
    back = []
    for k, text in enumerate(steps):
        label = re.sub(r"\s{0,2}[(（][^()（）]*[)）]", "", text).strip() or text
        label = label.replace('"', "'")[:40]
        lines.append(f'  s{k}["{_CIRCLED[k]} {label}"]')
        if m := re.search(rf"([가-힣]{{1,6}})\s{{0,2}}시\s{{0,2}}([{_CIRCLED}])\s{{0,2}}(?:으로|로)"
                          r"\s{0,2}(?:돌아|되돌)", text):
            target = _CIRCLED.index(m.group(2))
            if target < len(steps):
                back.append(f"  s{k} -->|{m.group(1)}| s{target}")
    chain = " --> ".join(f"s{k}" for k in range(len(steps)))
    return "\n".join([*lines, f"  {chain}", *back])


async def make(
    planned: Planned, *, model: str, api_key: str, slide: bool, accent: str = ""
) -> tuple[dict[str, Any], dict[str, int], bytes]:
    """Source, key, spend, PNG for one planned figure; raises if undrawable."""
    # local import: avoids a cycle through report/deck
    from app.services import diagram

    source, caption, usage, png = await diagram.draw_native(
        description=planned.description,
        figure=planned.figure,
        model=model,
        api_key=api_key,
        slide=slide,
        material="\n\n".join(filter(None, [
            planned.context, f"요청:\n{planned.request[:1500]}" if planned.request else ""])),
        accent=accent or diagram.ACCENT,
        language=planned.language,
        relation=planned.relation,
    )
    if planned.figure == "flow" and planned.steps and not covers_steps(source, planned.steps):
        # the given procedure drawn with steps missing or made up: drawn from the steps
        whole = steps_flowchart(planned.steps)
        redrawn = await diagram_render.render_png(whole, accent or diagram.ACCENT)
        if redrawn:
            log.info("figure redrawn from the %d given steps", len(planned.steps))
            source, png = whole, redrawn
    if planned.context:
        # figures contradicting their tables dropped; off-recommendation highlight removed
        checked = figure_facts.check(source, context=planned.context, document=planned.document)
        if checked.problems:
            raise figure_facts.Contradicted("; ".join(checked.problems))
        if checked.source != source:
            redrawn = await diagram_render.render_png(checked.source, accent or diagram.ACCENT)
            if redrawn:
                source, png = checked.source, redrawn
    return {
        "figure": planned.figure,
        "description": planned.description,
        "source": source,
        "caption": (planned.caption or caption)[:120],
        "key": diagram_key(source),
    }, {
        "inputTokens": int(usage.get("inputTokens") or 0),
        "outputTokens": int(usage.get("outputTokens") or 0),
    }, png


def _cell_stems(text: str) -> list[str]:
    return [w[:max(2, len(w) - 1)] for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or "")]


def _in(text: str, cells: str) -> bool:
    """Whether most words of `text` are in `cells` (stems, so particles do not count)."""
    stems = _cell_stems(text)
    return bool(stems) and sum(stem in cells for stem in stems) >= 0.75 * len(stems)


def repeats_table(made: dict[str, Any], table: str) -> bool:
    """Whether a drawn comparison or line of steps says no more than `table` (one Markdown
    table): a comparison whose criteria are the table's rows, two in three of them with both
    sides the same; steps that a two-column table lists by name, with at most two other rows."""
    from app.services import figure_kinds  # local: figure_kinds draws, this module plans

    slots = figure_kinds.from_source(str(made.get("source") or ""))
    if slots is None:
        return False
    rows = [r for t in figure_facts._tables(table) for r in t[1:]]
    lines = [" ".join(r) for r in rows]
    if slots.get("kind") == "steps":
        # a table of the steps by name, with a line or two besides (「총 기간」)
        names = [step["name"] for step in slots.get("steps") or []]
        return (bool(names) and len(rows) <= len(names) + 2
                and max((len(r) for r in rows), default=0) <= 2
                and all(any(_in(name, line) for line in lines) for name in names))
    if slots.get("kind") != "compare":
        return False
    compared = slots.get("rows") or []
    # every criterion is a table row, and at least two in three rows say both sides the same
    same = [any(_in(row["aspect"], line) and _in(row["before"], line)
                and _in(row["after"], line) for line in lines) for row in compared]
    return (bool(compared) and all(any(_in(row["aspect"], line) for line in lines)
                                   for row in compared)
            and sum(same) * 3 >= 2 * len(compared))


def too_thin(made: dict[str, Any]) -> bool:
    """Whether a structure drawn without a fixed kind has fewer than four boxes: a sentence says
    as much."""
    from app.services import diagram_shapes, figure_kinds  # local: both draw

    source = str(made.get("source") or "")
    if figure_kinds.from_source(source) is not None:
        return False
    graph = diagram_shapes.parse(source)
    return graph is not None and len(graph.nodes) < 4


def _flat(text: str) -> str:
    return re.sub(r"[\s\-_·/]", "", text or "").lower()


def ungrounded(source: str, local: str, wider: str = "") -> list[str]:
    """Boxes and groups of a drawn structure that the text never names: a label is grounded
    when the text holds it whole or holds one of its Latin words (「API 서버」 for API Server,
    「etcd 저장소」). A Hangul-only label summing up three points (「클러스터 운영」) is not."""
    from app.services import diagram_shapes  # local: diagram_shapes draws

    graph = diagram_shapes.parse(source or "")
    if graph is None:
        return []
    text = f"{local}\n{wider}"
    flat = _flat(text)
    latin = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]+", text)}
    labels = [node.label for node in graph.nodes.values() if node.label != node.id]
    labels += [label for gid, (label, _) in graph.groups.items() if label != gid]
    out = []
    for label in dict.fromkeys(labels):
        line = label.split("\n")[0].strip()
        if not _WORD.search(line) or _flat(line) in flat or any(
                w.lower() in latin for w in re.findall(r"[A-Za-z][A-Za-z0-9]+", line)):
            continue
        out.append(line)
    return out


def fence(made: dict[str, Any]) -> str:
    """Figure as report Markdown: mermaid fence plus caption."""
    caption = str(made.get("caption") or "").strip()
    block = f"```mermaid\n{str(made.get('source') or '').strip()}\n```"
    return f"{block}\n\n*그림: {caption}*" if caption else block


__all__ = ["FIGURES", "MAX_FIGURES", "MAX_MARKED", "Planned", "asked_topics", "fence", "make",
           "parse", "plan", "repeats_table", "settle_marked", "too_thin", "ungrounded",
           "wanted_figure"]
