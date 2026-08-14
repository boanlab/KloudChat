"""The tools kchat runs itself.

None calls a commercial API: search is self-hosted SearXNG, fetch is Crawl4AI
behind a Firecrawl-compatible shim, code runs in a sandboxed container. Every
turn touches these, so no prompt leaves for a third party through them.

Addresses come from the admin screen (`settings_store.tools_config`). A tool
with no address is left out of the list rather than offered and failing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.services import settings_store
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
    # Terse by design: the content is already in the panel, and echoing it into
    # the transcript doubles the tokens.
    return ToolResult(
        content=(
            f"'{title}' 아티팩트를 만들었습니다. 사용자 화면에 이미 열려 있으니 "
            "내용을 다시 적지 말고, 무엇을 만들었는지만 한두 문장으로 설명하세요."
        ),
        detail=title,
    )


CREATE_ARTIFACT = Tool(
    name="create_artifact",
    description=(
        "완성된 결과물을 별도 문서로 만들어 사용자 화면 옆에 띄웁니다. 웹페이지, "
        "실행할 수 있는 스크립트, 설정 파일처럼 사용자가 '만들어 달라'고 요청한 "
        "산출물에 사용하세요. 답변 중 설명을 위한 짧은 예시 코드에는 쓰지 마세요."
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
