"""Web research step for the document surfaces: plan queries, search, read pages.

Never raises. `Findings.searched` is False when no search backend ran, and the
document prompts say so.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.services import settings_store
from app.services.tools.builtin import scrape, searxng

log = logging.getLogger(__name__)

#: Planned queries per document.
MAX_QUERIES = 4
#: Results kept per query, before cross-query de-duplication.
_PER_QUERY = 5
#: Bodies fetched per query; the rest stay as title and snippet.
_BODIES = 2
#: Characters of any one page body handed to the writer.
_BODY_CHARS = 6_000
#: Sources kept across every query.
MAX_SOURCES = 10

_PLAN_PROMPT = """다음 요청으로 문서를 쓰려고 한다. 사실 확인이 필요한 지점을 찾아
웹 검색어를 최대 {n}개 만들어라.

규칙:
- 요청 문장을 그대로 검색어로 쓰지 마라. 검색엔진에 넣을 핵심 키워드로 쪼개라.
- 서로 다른 사실 축을 하나씩 맡게 하라. 같은 것을 바꿔 쓴 검색어는 낭비다.
- 제품명·모델명·버전·수치처럼 시간이 지나면 틀리는 항목을 우선하라.
- 의견이나 구성에 대한 검색어는 만들지 마라. 확인 가능한 사실만.
- 최신성이 중요한 주제라면 검색어에 연도를 넣어라.

요청:
{request}

JSON 배열로만 답하라. 예: ["검색어1", "검색어2"]"""


def _publisher(url: str) -> str:
    """Host without `www.`, truncated to 80 characters."""
    host = re.sub(r"^https?://", "", url).split("/")[0]
    return re.sub(r"^www\.", "", host)[:80]


#: Host substrings never cited in a work, study or research document.
_NEVER = (
    # Video and music.
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "vimeo.com",
    "soundcloud.com",
    "music.bugs.co.kr",
    "genie.co.kr",
    "melon.com",
    # Lyrics.
    "lyrics.co.kr",
    "klyrics",
    "azlyrics.com",
    "genius.com",
    # Shopping and classifieds.
    "coupang.com",
    "11st.co.kr",
    "gmarket.co.kr",
    "auction.co.kr",
    "aliexpress.com",
    "amazon.",
    "ebay.",
    # Social and short-form.
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "threads.net",
    "pinterest.",
    "reddit.com",
    # Content farms and scrapers.
    "wikiwand.com",
    "dbpedia.org",
    "coursehero.com",
    "scribd.com",
    "slideshare.net",
    "studocu.com",
)

#: Host substrings ranked first; a boost, never a requirement.
_PREFERRED = (
    ".go.kr",
    ".or.kr",
    ".ac.kr",
    ".re.kr",  # 정부·공공·학교·연구기관
    ".gov",
    ".edu",
    ".int",
    "arxiv.org",
    "ieee.org",
    "acm.org",
    "nist.gov",
    "iso.org",
    "ietf.org",
    "docs.",
    "developer.",
    "learn.microsoft.com",
    "cloud.google.com",
    "kostat.go.kr",
    "law.go.kr",
    "kisa.or.kr",
)

#: Relevance below which a hit is dropped as unrelated.
_FLOOR = 0.34

_WORD = re.compile(r"[0-9A-Za-z가-힣]{2,}")


def _terms(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "")}


def relevance(query: str, hit: dict[str, str]) -> float:
    """Fraction of the query's terms found in the hit's title and snippet, 0 to 1."""
    wanted = _terms(query)
    if not wanted:
        return 1.0
    text = f"{hit.get('title', '')} {hit.get('snippet', '')}".lower()
    return sum(1 for term in wanted if _carries(text, term)) / len(wanted)


def _carries(text: str, term: str) -> bool:
    """Substring match; a trailing Korean particle on the term is also tried without."""
    if term in text:
        return True
    return len(term) >= 3 and term[-1] in "의은는이가을를과와에로도들" and term[:-1] in text


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", url).split("/")[0].lower()


def _rejected(url: str) -> str:
    """Why this host cannot be a source, or `''` when it can."""
    host = _host(url)
    return "never" if any(bad in host for bad in _NEVER) else ""


def score(query: str, hit: dict[str, str]) -> float:
    """Rank within what survives the floor. Preferred hosts sort first."""
    base = relevance(query, hit)
    return base + (0.4 if any(good in _host(hit.get("url", "")) for good in _PREFERRED) else 0.0)


@dataclass(slots=True)
class Findings:
    """What research produced: the numbered source shelf and the same material as prose."""

    sources: list[dict[str, Any]] = field(default_factory=list)
    context: str = ""
    queries: list[str] = field(default_factory=list)
    #: False means no search backend ran and the document is written from memory.
    searched: bool = False
    #: Hits thrown away by selection.
    dropped: int = 0
    usage: dict[str, int] = field(default_factory=lambda: {"inputTokens": 0, "outputTokens": 0})

    @property
    def detail(self) -> str:
        """Step subtitle: the publishers, joined."""
        return " · ".join(str(s["publisher"]) for s in self.sources)


async def available() -> bool:
    """Whether a search backend is configured at all."""
    backends = await settings_store.tools_config()
    return bool(backends.search)


def _parse_queries(text: str, request: str) -> list[str]:
    """Planned queries, or the request itself when the planner gave nothing."""
    block = text[text.find("[") : text.rfind("]") + 1] if "[" in text and "]" in text else ""
    try:
        parsed = json.loads(block)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if not isinstance(parsed, list):
        return [request[:300]]
    queries = [str(q).strip()[:200] for q in parsed if str(q).strip()]
    return queries[:MAX_QUERIES] or [request[:300]]


async def _plan(request: str, model: str, api_key: str) -> tuple[list[str], dict[str, int]]:
    """Search terms for this request; the request itself on any failure."""
    spent = {"inputTokens": 0, "outputTokens": 0}
    if not model:
        return [request[:300]], spent
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
                            "content": _PLAN_PROMPT.format(n=MAX_QUERIES, request=request[:2000]),
                        }
                    ],
                    "max_tokens": 300,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.info("research query planning failed: %s", exc)
        return [request[:300]], spent

    raw = payload.get("usage") or {}
    spent = {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }
    text = (payload["choices"][0]["message"]["content"] or "").strip()
    return _parse_queries(text, request), spent


async def _gather(base_url: str, query: str) -> list[dict[str, str]]:
    """One query's hits; `[]` on any failure."""
    try:
        return await searxng(base_url, query, _PER_QUERY)
    except (httpx.HTTPError, ValueError) as exc:
        log.info("research search failed for %r: %s", query[:60], exc)
        return []


async def run(
    request: str,
    *,
    model: str = "",
    api_key: str = "",
    max_sources: int = MAX_SOURCES,
) -> Findings:
    """Plan queries, search them concurrently, read the top pages. Never raises."""
    backends = await settings_store.tools_config()
    if not backends.search:
        return Findings()

    queries, spent = await _plan(request, model, api_key)
    hit_lists = await asyncio.gather(*(_gather(backends.search, q) for q in queries))

    # Filtered and ranked per query before anything is fetched; picked
    # round-robin across queries, one publisher and one URL at most.
    sources: list[dict[str, Any]] = []
    seen_hosts: set[str] = set()
    seen_urls: set[str] = set()
    ranked: list[list[tuple[float, dict[str, str]]]] = []
    dropped: dict[str, int] = {"차단": 0, "무관": 0}
    for query_index, hits in enumerate(hit_lists):
        keep: list[tuple[float, dict[str, str]]] = []
        query = queries[query_index] if query_index < len(queries) else request
        for hit in hits:
            url, title = hit.get("url") or "", (hit.get("title") or "").strip()
            if not url or not title:
                continue
            if _rejected(url):
                dropped["차단"] += 1
                continue
            if relevance(query, hit) < _FLOOR:
                dropped["무관"] += 1
                continue
            keep.append((score(query, hit), hit))
        keep.sort(key=lambda pair: pair[0], reverse=True)
        ranked.append(keep)

    if any(dropped.values()):
        log.info(
            "research dropped %d hits (차단 %d · 무관 %d)",
            sum(dropped.values()),
            dropped["차단"],
            dropped["무관"],
        )

    picks: list[tuple[int, dict[str, str]]] = []
    for rank in range(_PER_QUERY):
        for query_index, keep in enumerate(ranked):
            if rank >= len(keep):
                continue
            hit = keep[rank][1]
            url = hit["url"]
            if url in seen_urls:
                continue
            host = _publisher(url)
            if host in seen_hosts:
                continue
            seen_urls.add(url)
            seen_hosts.add(host)
            picks.append((query_index, hit))
            if len(picks) >= max_sources:
                break
        if len(picks) >= max_sources:
            break

    # Bodies for the first `_BODIES` picks of each query, fetched together.
    wanted = [
        index
        for index, (query_index, _) in enumerate(picks)
        if sum(1 for j, _ in picks[:index] if j == query_index) < _BODIES
    ]
    bodies = dict(
        zip(
            wanted,
            await asyncio.gather(*(scrape(backends.fetch, picks[i][1]["url"]) for i in wanted)),
            strict=True,
        )
    )

    blocks: list[str] = []
    for index, (query_index, hit) in enumerate(picks):
        ordinal = len(sources) + 1
        body = (bodies.get(index) or "").strip()
        sources.append(
            {
                "id": f"src{index}_{uuid.uuid4().hex[:6]}",
                "ordinal": ordinal,
                "title": hit["title"][:200],
                "publisher": _publisher(hit["url"]),
                "url": hit["url"],
                "origin": "web",
                "originLabel": "웹 검색",
                "quote": (hit.get("snippet") or "")[:300],
            }
        )
        lines = [
            f"[{ordinal}] {hit['title']}",
            f"출처: {hit['url']}",
            f"검색어: {queries[query_index] if query_index < len(queries) else ''}",
        ]
        if hit.get("snippet"):
            lines.append(hit["snippet"][:300])
        if body:
            lines.append(f"본문 발췌:\n{body[:_BODY_CHARS]}")
        blocks.append("\n".join(lines))

    return Findings(
        sources=sources,
        context="\n\n".join(blocks),
        queries=queries,
        searched=True,
        dropped=sum(dropped.values()),
        usage=spent,
    )


#: Header of the user-role data block; names the provenance as a web search.
CONTEXT_HEADER = (
    "# 웹 검색 결과\n"
    "아래는 이 문서를 쓰기 위해 방금 웹에서 찾은 자료입니다. "
    "사실·수치·제품명·버전은 기억이 아니라 이 자료에서 가져오세요. "
    "여기에 없는 사실을 단정하지 말고, 자료와 기억이 어긋나면 자료를 따르세요.\n"
)


def context_block(findings: Findings) -> str:
    """The findings as one untrusted-context entry, or `''` when empty."""
    if not findings.context:
        return ""
    return CONTEXT_HEADER + "\n" + findings.context


#: Told to the writer when a search ran and found nothing worth citing.
EMPTY_RULE = (
    "웹을 검색했지만 인용할 만한 자료를 찾지 못했습니다. "
    "확인되지 않은 수치나 제품명을 단정하지 말고, 확인하지 못했다는 사실을 "
    "본문에 밝히세요."
)

#: Told to the writer when no search backend could run.
UNRESEARCHED_RULE = (
    "웹 검색을 쓸 수 없어 이 문서는 검색 없이 작성됩니다. "
    "최신 정보나 제품명·버전·수치를 단정하지 말고, 확인하지 못한 항목은 "
    "확인이 필요하다고 밝히세요."
)
