"""The tools KloudChat runs itself.

None calls a commercial API: search is self-hosted SearXNG, fetch is Crawl4AI
behind a Firecrawl-compatible shim, code runs in a sandboxed container. Every
turn touches these, so no prompt leaves for a third party through them.

Addresses come from the admin screen (`settings_store.tools_config`). A tool
with no address is left out of the list rather than offered and failing.
"""

from __future__ import annotations

import asyncio
import logging
import re
from html import unescape
from typing import Any

import httpx

from app.core.config import settings
from app.services import index_client, knowledge, settings_store
from app.services.tools.base import Tool, ToolContext, ToolResult

log = logging.getLogger(__name__)

# Long enough for a slow page, short enough that one dead host does not consume
# the turn's tool budget.
_FETCH_TIMEOUT = httpx.Timeout(45.0, connect=10.0)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n…(이하 {len(text) - limit:,}자 생략)"


# ── web search ─────────────────────────────────────────────────────────


async def _searxng(base_url: str, query: str, count: int) -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
        response = await client.get(
            f"{base_url.rstrip('/')}/search",
            params={"q": query, "format": "json", "safesearch": 1, "language": "ko"},
        )
        response.raise_for_status()
        data = response.json()
    hits = []
    for row in (data.get("results") or [])[:count]:
        hits.append(
            {
                "title": row.get("title") or "",
                "url": row.get("url") or "",
                "snippet": row.get("content") or "",
            }
        )
    return hits


async def _scrape(base_url: str, url: str) -> str:
    """Page body as Markdown, through the shim. Empty string on failure.

    When the call goes through the gateway, it substitutes its own internal key
    for the Authorization header. The configured key is still sent for
    deployments that point straight at a bare shim.
    """
    if not base_url:
        return ""
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/v1/scrape",
                headers={"Authorization": f"Bearer {settings.scraper_api_key}"},
                json={"url": url, "formats": ["markdown"]},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.info("scrape failed for %s: %s", url, exc)
        return ""
    data = payload.get("data") or payload
    return (data.get("markdown") or data.get("content") or "").strip()


#: The scraper, for callers outside the tool loop. Ingesting a URL into an
#: agent's shelf is the same fetch the `fetch_url` tool makes, and a second
#: implementation would be a second set of timeouts and headers to keep in step.
scrape = _scrape


async def web_search(args: dict[str, Any]) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult(content="오류: query 가 비었습니다.", failed=True)

    backends = await settings_store.tools_config()
    try:
        hits = await _searxng(backends.search, query, settings.web_search_results)
    except (httpx.HTTPError, ValueError) as exc:
        return ToolResult(content=f"오류: 검색에 실패했습니다 ({exc}).", failed=True)
    if not hits:
        return ToolResult(content=f"'{query}' 에 대한 검색 결과가 없습니다.", detail="0개 결과")

    # Top few read in full — snippets alone are too thin to answer from. The
    # rest stay as titles the model can request by URL.
    bodies = await asyncio.gather(
        *(_scrape(backends.fetch, h["url"]) for h in hits[: settings.web_search_scrape])
    )

    lines = [f"'{query}' 검색 결과:\n"]
    for i, hit in enumerate(hits):
        lines.append(f"[{i + 1}] {hit['title']}\n{hit['url']}\n{hit['snippet']}")
        body = bodies[i] if i < len(bodies) else ""
        if body:
            lines.append(f"본문 발췌:\n{_truncate(body, 4000)}")
        lines.append("")

    scraped = sum(1 for b in bodies if b)
    return ToolResult(
        content="\n".join(lines),
        detail=f"{len(hits)}개 결과 · {scraped}개 본문 읽음",
    )


async def fetch_url(args: dict[str, Any]) -> ToolResult:
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return ToolResult(content="오류: http(s) URL 이 필요합니다.", failed=True)
    backends = await settings_store.tools_config()
    body = await _scrape(backends.fetch, url)
    if not body:
        return ToolResult(content=f"오류: {url} 을 읽지 못했습니다.", failed=True)
    return ToolResult(content=_truncate(body, 20_000), detail=f"{len(body):,}자")


# ── code execution ─────────────────────────────────────────────────────


async def execute_code(args: dict[str, Any]) -> ToolResult:
    code = str(args.get("code") or "")
    if not code.strip():
        return ToolResult(content="오류: code 가 비었습니다.", failed=True)
    backends = await settings_store.tools_config()
    if not backends.exec:
        return ToolResult(content="오류: 코드 실행이 설정되지 않았습니다.", failed=True)

    # The sandbox names it "py", not "python".
    lang = {"python": "py", "py": "py"}.get(str(args.get("language") or "python"), "py")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
            response = await client.post(
                f"{backends.exec.rstrip('/')}/exec",
                headers={"x-api-key": settings.code_interpreter_api_key},
                json={"code": code, "lang": lang},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return ToolResult(content=f"오류: 코드 실행에 실패했습니다 ({exc}).", failed=True)

    stdout = (payload.get("stdout") or "").strip()
    stderr = (payload.get("stderr") or "").strip()
    parts = []
    if stdout:
        parts.append(f"stdout:\n{_truncate(stdout, 8000)}")
    if stderr:
        parts.append(f"stderr:\n{_truncate(stderr, 4000)}")
    if not parts:
        # Said explicitly, so the model does not invent output for a script
        # that printed nothing.
        parts.append("실행되었지만 출력이 없습니다. 결과를 보려면 print() 를 쓰세요.")
    return ToolResult(content="\n\n".join(parts), failed=bool(stderr and not stdout))


# ── registry ───────────────────────────────────────────────────────────

WEB_SEARCH = Tool(
    name="web_search",
    description=(
        "웹을 검색하고 상위 결과의 본문을 읽어 옵니다. 최신 정보, 뉴스, 통계, "
        "모델이 모르는 사실을 확인할 때 사용하세요."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "검색어. 자연어 질문보다 핵심 키워드가 낫습니다.",
            }
        },
        "required": ["query"],
    },
    run=web_search,
    label="웹 검색 중",
)

FETCH_URL = Tool(
    name="fetch_url",
    description=(
        "특정 URL 의 본문을 마크다운으로 읽어 옵니다. 검색 결과의 출처를 직접 "
        "확인할 때 사용하세요."
    ),
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "읽을 페이지의 전체 URL"}},
        "required": ["url"],
    },
    run=fetch_url,
    label="문서 읽는 중",
)

EXECUTE_CODE = Tool(
    name="execute_code",
    description=(
        "샌드박스에서 Python 코드를 실행합니다. 계산, 수식 전개(sympy), 데이터 처리에 "
        "쓰세요. 결과는 반드시 print() 로 출력해야 보입니다. 네트워크는 막혀 있습니다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "실행할 코드. 출력은 print() 로."},
            "language": {"type": "string", "enum": ["python"], "default": "python"},
        },
        "required": ["code"],
    },
    run=execute_code,
    label="코드 실행 중",
)

#: What chat can produce as a standalone document. Report and deck are excluded:
#: they have their own pipelines, and an artifact minted here could not be
#: regenerated or exported by those screens.
_ARTIFACT_KINDS = {"html", "code"}

#: Tags that only mark up running text. Markup drawn from nothing but these is
#: prose in an HTML costume: no layout, no styling, nothing a browser gives it
#: that a chat bubble does not already give it. `table`, `main`, `div` and the
#: rest stay out on purpose — they suggest a page, and letting a page through is
#: the cheaper mistake.
_PROSE_TAGS = frozenset(
    {
        "p", "br", "hr", "span", "a", "b", "i", "u", "em", "strong", "small",
        "ul", "ol", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
    }
)

#: Languages that name prose rather than something a program reads back. Only
#: honoured when the model states one: `language` defaults to "text" further
#: down, so treating an absent value as prose would refuse the short shell
#: script whose author simply left the field off.
_PROSE_LANGUAGES = frozenset({"text", "txt", "plain", "md", "markdown"})

#: Prose shorter than this is an answer even when the user said "만들어 줘":
#: reading it is the whole use, and a panel puts a click in front of that. Above
#: it a transcript genuinely struggles and export starts to earn its keep. Set
#: generously, because refusing a real document — unexportable, unversioned,
#: copied out by hand — costs more than one needless click.
_PROSE_MAX_CHARS = 1000

#: Below this the panel holds so little that the answer can carry it too, and
#: above it repeating the body would double the turn's output for a reader who
#: is going to scroll the panel anyway.
_ECHO_MAX_CHARS = 600

_TAG_NAME = re.compile(r"<\s*/?\s*([A-Za-z][\w-]*)")
_ANY_TAG = re.compile(r"<[^>]*>")


def _visible_length(kind: str, content: str) -> int:
    """How much a reader actually sees, markup and entities discounted."""
    text = _ANY_TAG.sub(" ", content) if kind == "html" else content
    return len(" ".join(unescape(text).split()))


def _is_prose(kind: str, content: str, language: str) -> bool:
    """Whether this is writing to be read rather than a file to be used.

    The question a length test cannot answer: a four-line docker-compose.yml is
    a document because a program reads it back, and twelve paragraphs of an
    explanation are an answer because reading them is the point. So this asks
    what the payload is made of, and leaves length to the caller.
    """
    if kind == "code":
        return language in _PROSE_LANGUAGES
    if "<!doctype" in content.lower():
        return False
    return {tag.group(1).lower() for tag in _TAG_NAME.finditer(content)} <= _PROSE_TAGS


async def create_artifact(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Records an artifact for the turn to store once it finishes.

    Complements post-hoc extraction of fenced code blocks: extraction depends on
    how the answer happened to be formatted, while a tool call is a decision
    made where the request is understood.
    """
    kind = str(args.get("kind") or "").strip().lower()
    title = str(args.get("title") or "").strip()
    content = str(args.get("content") or "")
    language = str(args.get("language") or "").strip().lower()

    if kind not in _ARTIFACT_KINDS:
        return ToolResult(
            content=f"오류: kind 는 {' 또는 '.join(sorted(_ARTIFACT_KINDS))} 여야 합니다.",
            failed=True,
        )
    if not content.strip():
        return ToolResult(content="오류: content 가 비어 있습니다.", failed=True)
    if not title:
        return ToolResult(content="오류: title 이 필요합니다.", failed=True)

    visible = _visible_length(kind, content)
    # The one call the description cannot prevent, since the model has already
    # decided by the time it reads one. Only the model's own guess is overruled:
    # a user who asked for a file said so, and `userRequested` carries that
    # through. Not `failed`, because nothing went wrong — an errored step paints
    # the whole turn 중단됨 in the timeline while the answer that follows is
    # exactly the one the reader wanted.
    if (
        not bool(args.get("userRequested"))
        and visible < _PROSE_MAX_CHARS
        and _is_prose(kind, content, language)
    ):
        return ToolResult(
            content=(
                f"'{title}' 은 문서로 만들지 않았습니다. 실행하거나 다른 프로그램이 "
                f"읽어 갈 파일이 아니라 {visible}자 남짓한 글이라, 옆 패널에 두면 "
                "읽으려고 패널을 여는 수고만 늘어납니다. 본문을 답변에 그대로 "
                "적어 주고, 끝에 한 줄로 파일이나 문서로 따로 만들어 드릴 수도 "
                "있다고 덧붙이세요. 사용자가 그렇게 해 달라고 하면 그때 "
                "userRequested 를 true 로 두고 다시 부르세요."
            ),
            detail="답변에 직접 적기",
        )

    ctx.pending_artifacts.append(
        {
            "kind": kind,
            "title": title[:200],
            "data": {
                "kind": kind,
                "content": content,
                "language": "html" if kind == "html" else (language or "text"),
            },
        }
    )
    # What the answer still owes the reader, which is never nothing. A panel is
    # where a deliverable is kept, not where it is read: a person who asked for
    # three sentences and got one sentence about three sentences was not
    # answered at all.
    if visible < _ECHO_MAX_CHARS:
        carry = (
            "짧으니 답변에도 본문을 그대로 옮겨 적으세요. 패널은 내보내고 버전을 "
            "남기려고 있는 것이고, 읽는 일은 대화 안에서 끝나야 합니다."
        )
    else:
        carry = (
            "길이가 있으니 본문을 다시 옮길 필요는 없지만, 무엇을 만들었고 그 안에 "
            "무엇이 들어 있는지는 답변에 적으세요. '만들었습니다' 한 줄로 끝내지 "
            "마세요."
        )
    return ToolResult(
        content=f"'{title}' 문서를 만들어 사용자 화면에 띄웠습니다. {carry}",
        detail=title,
    )


CREATE_ARTIFACT = Tool(
    name="create_artifact",
    description=(
        "완성된 결과물을 별도 문서로 만들어 사용자 화면 옆에 띄웁니다. 기준은 "
        "길이가 아니라 쓰임새입니다. 대화 밖으로 나가 파일로 저장되거나 실행·"
        "렌더링·불러오기 되는 것만 문서입니다. 웹페이지, 스크립트, 설정 파일, "
        "데이터 파일이 그렇고, 네 줄짜리 docker-compose.yml 도 문서입니다. "
        "읽고 나면 쓰임이 끝나는 글은 사용자가 '만들어 달라'고 했어도 답변에 "
        "그대로 적으세요. 메일 초안, 요약, 번역, 개요, 사과문, 회신 문구가 "
        "그렇습니다. 예외는 절이 여러 개로 나뉜 긴 문서처럼 대화에 그대로 실으면 "
        "읽기 어려운 분량뿐입니다. 애매하면 이렇게 물어 보세요. 사용자가 이 "
        "결과를 다른 프로그램에 넣습니까, 읽고 끝냅니까. 읽고 끝나면 답변입니다. "
        "설명을 위한 짧은 예시 코드에도 쓰지 마세요."
    ),
    parameters={
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["html", "code"],
                "description": (
                    "html 은 브라우저에서 미리보기가 되는 한 페이지, code 는 그 외 소스."
                ),
            },
            "title": {"type": "string", "description": "문서 이름. 파일명처럼 짧게."},
            "content": {
                "type": "string",
                "description": "문서 전체 내용. 마크다운 코드펜스로 감싸지 마세요.",
            },
            "language": {
                "type": "string",
                "description": "kind 가 code 일 때의 언어 (python, bash, yaml 등).",
            },
            "userRequested": {
                "type": "boolean",
                "description": (
                    "사용자가 문서·파일·내보내기를 직접 요구했을 때만 true. 짧은 "
                    "글이라도 그때는 문서로 만듭니다. 스스로 판단해 켜지 마세요."
                ),
            },
        },
        "required": ["kind", "title", "content"],
    },
    run=create_artifact,
    label="아티팩트 만드는 중",
    read_only=False,
    wants_context=True,
)


#: One per series, in order. Assigned here rather than asked of the model,
#: which returns "blue" as readily as "#3b82f6".
_SERIES_COLOURS = ("#5b5bd6", "#e8834a", "#2ea88a", "#c74e8e", "#6b7280")


def _chart_table(series: list[dict], x_label: str) -> dict:
    """The rows the chart was drawn from, derived rather than asked for.

    Computed from the same points the chart renders, so the table and the plot
    cannot disagree.
    """
    keys: list[str] = []
    for one in series:
        for point in one["points"]:
            if point["x"] not in keys:
                keys.append(point["x"])
    rows: list[list] = []
    for key in keys:
        row: list = [key]
        for one in series:
            match = next((p["y"] for p in one["points"] if p["x"] == key), "")
            row.append(match)
        rows.append(row)
    return {"columns": [x_label or "항목", *[s["name"] for s in series]], "rows": rows}


async def create_chart(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Records a chart artifact for the turn to store once it finishes.

    Only the data comes from the model: colours are assigned and the table is
    derived, so the two halves of the artifact cannot contradict each other.
    """
    chart_type = str(args.get("chartType") or "bar").strip().lower()
    if chart_type not in ("bar", "line", "stacked"):
        chart_type = "bar"
    title = str(args.get("title") or "").strip()
    if not title:
        return ToolResult(content="오류: title 이 필요합니다.", failed=True)

    raw = args.get("series")
    if not isinstance(raw, list) or not raw:
        return ToolResult(content="오류: series 가 비어 있습니다.", failed=True)

    series: list[dict] = []
    for index, item in enumerate(raw[: len(_SERIES_COLOURS)]):
        if not isinstance(item, dict):
            continue
        points = []
        for point in item.get("points") or []:
            if not isinstance(point, dict):
                continue
            try:
                value = float(point.get("y"))
            except (TypeError, ValueError):
                # Dropped rather than plotted as zero, which would read as a
                # measured "none" instead of missing data.
                continue
            label = str(point.get("x") or "").strip()
            if label:
                points.append({"x": label[:40], "y": value})
        if points:
            series.append(
                {
                    "name": str(item.get("name") or f"계열 {index + 1}").strip()[:40],
                    "color": _SERIES_COLOURS[index],
                    "points": points[:40],
                }
            )

    if not series:
        return ToolResult(
            content="오류: 그릴 수 있는 값이 없습니다. 각 point 는 x(이름)와 y(숫자)가 필요합니다.",
            failed=True,
        )

    x_label = str(args.get("xLabel") or "").strip()[:40]
    ctx.pending_artifacts.append(
        {
            "kind": "chart",
            "title": title[:200],
            "data": {
                "kind": "chart",
                "chartType": chart_type,
                "caption": str(args.get("caption") or "").strip()[:300],
                "xLabel": x_label,
                "yLabel": str(args.get("yLabel") or "").strip()[:40],
                "series": series,
                "table": _chart_table(series, x_label),
                "sourceFile": str(args.get("sourceFile") or "").strip()[:200],
            },
        }
    )
    return ToolResult(
        content=(
            f"'{title}' 차트를 만들었습니다. 사용자 화면에 이미 열려 있으니 수치를 다시 "
            "나열하지 말고, 이 차트가 무엇을 보여 주는지만 한두 문장으로 설명하세요."
        ),
        detail=title,
    )


CREATE_CHART = Tool(
    name="create_chart",
    description=(
        "수치를 막대/선 그래프로 그려 사용자 화면 옆에 띄웁니다. 비교·추이·분포처럼 "
        "값이 여러 개인 결과를 보여 줄 때 쓰세요. 표로 충분한 두세 개 값이나, "
        "근거 없이 지어낸 수치에는 쓰지 마세요."
    ),
    parameters={
        "type": "object",
        "properties": {
            "chartType": {
                "type": "string",
                "enum": ["bar", "line", "stacked"],
                "description": "bar 는 비교, line 은 시간에 따른 추이.",
            },
            "title": {"type": "string", "description": "차트 이름. 짧게."},
            "caption": {"type": "string", "description": "이 차트가 무엇을 보여 주는지 한 줄."},
            "xLabel": {"type": "string", "description": "가로축이 무엇인지 (연도, 모델 등)."},
            "yLabel": {"type": "string", "description": "세로축의 단위 (건, %, 초 등)."},
            "sourceFile": {
                "type": "string",
                "description": "수치의 출처. 첨부 파일 이름이나 자료 이름.",
            },
            "series": {
                "type": "array",
                "description": "계열 목록. 하나면 단일 그래프, 여럿이면 나란히 그립니다.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "범례에 쓸 이름."},
                        "points": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "x": {"type": "string", "description": "가로축 값의 이름."},
                                    "y": {"type": "number", "description": "숫자 값."},
                                },
                                "required": ["x", "y"],
                            },
                        },
                    },
                    "required": ["name", "points"],
                },
            },
        },
        "required": ["title", "series"],
    },
    run=create_chart,
    label="차트 그리는 중",
    read_only=False,
    wants_context=True,
)


async def available_builtins(web_search_enabled: bool) -> list[Tool]:
    """The built-in tools this deployment can actually run.

    Two filters:

    * **Configured.** A tool with no address is left out rather than offered
      and failing on every call.
    * **The composer toggle.** `web_search` is per turn and user-controlled —
      it changes the character of the answer and costs seconds.
    """
    backends = await settings_store.tools_config()
    tools: list[Tool] = []
    if backends.fetch:
        tools.append(FETCH_URL)
        if web_search_enabled and backends.search:
            # Attached as a pair: search without fetch yields snippets only.
            tools.insert(0, WEB_SEARCH)
    if backends.exec:
        tools.append(EXECUTE_CODE)
    # No backend required — these write rows this instance already owns.
    tools.append(CREATE_ARTIFACT)
    tools.append(CREATE_CHART)
    return tools


def knowledge_tool(
    documents: list[tuple[str, str, str | None]], collection: str = ""
) -> Tool:
    """Search inside the documents attached to the agent running this turn.

    Built per turn around a preloaded shelf rather than reaching for a database:
    tools run inside the streaming loop, which holds no session of its own, and
    a tool that opened one would be writing and reading against a turn that may
    still fail.

    Offered only when the agent has documents. A tool that always answers "이
    에이전트에는 자료가 없습니다" teaches the model to stop calling it, and then
    it is ignored on the agent that does have a shelf.
    """
    # Contents list per document. Filenames are often meaningless, and a model
    # choosing tools by description needs to know what the shelf covers.
    #
    # Headings, not an excerpt: given a sample the model reads it as the
    # material and rules the shelf out without searching.
    def _outline(text: str, limit: int = 12) -> str:
        seen: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("#"):
                continue
            heading = stripped.lstrip("#").strip()
            if heading and heading not in seen:
                seen.append(heading)
            if len(seen) >= limit:
                break
        if not seen:
            # No headings: fall back to the opening line, which at least names
            # the subject in a document that has no structure to show.
            first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            seen = [first[:60]] if first else []
        return f" — {' / '.join(seen)}" if seen else ""

    listed = "; ".join(f"{name}{_outline(text)}" for name, text, _ in documents[:6])
    more = "" if len(documents) <= 6 else f" 외 {len(documents) - 6}건"

    async def run(args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(content="검색어가 비어 있습니다.", failed=True)
        passages, ranked = knowledge.gather(documents, query)
        # The vector half, when the shelf has one and the index answers. Merged
        # rather than preferred: the index finds meaning and the scorer finds
        # exact wording — a 조문 number or a figure comes back from the scorer
        # and nowhere else.
        if collection and ranked:
            hits = await index_client.search(collection=collection, query=query)
            if hits:
                passages = knowledge.merge(hits, passages)
        if not passages:
            # Said plainly, and distinguished from "there is nothing here": the
            # model should be able to tell "I looked and the shelf is silent on
            # this" from "this agent has no material at all".
            return ToolResult(
                content=(
                    f"자료 {len(documents)}건을 찾아봤지만 '{query}' 와 겹치는 대목이 "
                    "없습니다. 자료에 없는 내용을 지어내지 말고, 자료에 없다고 답하세요."
                ),
                detail="해당 없음",
            )
        body = knowledge.render(passages)
        # Said differently on purpose: "read all 3 documents" and "found 4
        # passages" are different claims, and only the second one can have
        # missed something.
        detail = f"{len(passages)}개 대목" if ranked else f"자료 {len(passages)}건 전문"
        return ToolResult(content=body, detail=detail)

    return Tool(
        name="search_knowledge",
        description=(
            "이 에이전트에 첨부된 자료 안에서 검색합니다. 붙어 있는 자료: "
            f"{listed}{more}. "
            "위 목록은 각 자료의 목차일 뿐 내용이 아닙니다. 목차만 보고 "
            "'자료에 없다'고 판단하지 말고, 이 자료가 다룰 만한 주제이면 반드시 "
            "먼저 이 도구를 부르세요. 기억에 의존해 답하지 마세요. 웹 검색이 아니라 "
            "첨부 자료 전용입니다."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "찾을 내용. 문서에 쓰였을 법한 낱말로 적으세요.",
                }
            },
            "required": ["query"],
        },
        run=run,
        label="자료 찾는 중",
    )
