"""Document-surface web research: plan, search, read. Never raises."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.services import credibility, korean_papers, query_terms, settings_store, thinking
from app.services.tools.builtin import scrape, searxng

log = logging.getLogger(__name__)

#: Planned queries per document.
MAX_QUERIES = 4
#: Results per query before cross-query dedupe.
_PER_QUERY = 5
#: Bodies fetched per query; rest title+snippet.
_BODIES = 2
#: Max chars per page body.
_BODY_CHARS = 6_000
#: Sources kept overall.
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


#: Page chrome: sign-in, menus, share/skip links.
_CHROME = re.compile(
    r"로그인|회원가입|비밀번호|아이디 찾기|바로가기|메뉴|공유하기|구독하기|카카오톡|페이스북|"
    r"Copyright|All rights reserved|쿠키|cookie|Sign in|Log in|Subscribe|Skip to", re.I
)
_LINK_ONLY = re.compile(r"^[\s*>#-]*(?:\[[^\]]*\]\([^)]*\)[\s|·,/]*)+$")


def page_text(markdown: str) -> str:
    """Scraped page minus chrome."""
    kept: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if len(run) < 5:
            kept.extend(run)
        run.clear()

    for line in (markdown or "").split("\n"):
        text = line.strip()
        if not text:
            flush()
            kept.append("")
            continue
        if _LINK_ONLY.match(text) or (len(text) < 40 and _CHROME.search(text)):
            continue
        if len(text) < 15 and not text.startswith("|") and not re.search(r"[.!?。다요]$|\d", text):
            run.append(line)
            continue
        flush()
        kept.append(line)
    flush()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def _publisher(url: str) -> str:
    """Host without `www.`, max 80 chars."""
    host = re.sub(r"^https?://", "", url).split("/")[0]
    return re.sub(r"^www\.", "", host)[:80]


#: Hosts never cited.
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

#: Preferred hosts; boost only.
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

#: Relevance floor.
_FLOOR = 0.34
_SCHOLAR_FLOOR = 0.5

_WORD = re.compile(r"[0-9A-Za-z가-힣]{2,}")


def _terms(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "")}


def relevance(query: str, hit: dict[str, str]) -> float:
    """Fraction of query terms in title+snippet."""
    wanted = _terms(query)
    if not wanted:
        return 1.0
    text = f"{hit.get('title', '')} {hit.get('snippet', '')}".lower()
    return sum(1 for term in wanted if _carries(text, term)) / len(wanted)


def _carries(text: str, term: str) -> bool:
    """Substring match, also without a trailing Korean particle."""
    if term in text:
        return True
    return len(term) >= 3 and term[-1] in "의은는이가을를과와에로도들" and term[:-1] in text


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", url).split("/")[0].lower()


def _rejected(url: str) -> str:
    """Why the host can't be a source, or ''."""
    host = _host(url)
    return "never" if any(bad in host for bad in _NEVER) else ""


def score(query: str, hit: dict[str, str]) -> float:
    """Rank: relevance plus preferred host and credibility tier."""
    base = relevance(query, hit)
    url = hit.get("url", "")
    preferred = 0.4 if any(good in _host(url) for good in _PREFERRED) else 0.0
    return base + preferred + 0.15 * credibility.tier(url)


@dataclass(slots=True)
class Findings:
    """Numbered source shelf plus the same material as prose."""

    sources: list[dict[str, Any]] = field(default_factory=list)
    context: str = ""
    queries: list[str] = field(default_factory=list)
    #: False = no search ran; written from memory.
    searched: bool = False
    #: Hits dropped by selection.
    dropped: int = 0
    usage: dict[str, int] = field(default_factory=lambda: {"inputTokens": 0, "outputTokens": 0})

    @property
    def detail(self) -> str:
        """Step subtitle: joined publishers."""
        return " · ".join(str(s["publisher"]) for s in self.sources)


async def available() -> bool:
    """Search backend configured."""
    backends = await settings_store.tools_config()
    return bool(backends.search)


def _parse_queries(text: str, request: str, most: int = MAX_QUERIES) -> list[str]:
    """Planned queries, else the request."""
    block = text[text.find("[") : text.rfind("]") + 1] if "[" in text and "]" in text else ""
    try:
        parsed = json.loads(block)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if not isinstance(parsed, list):
        return [request[:300]]
    return query_terms.query_list(parsed)[:most] or [request[:300]]


def _parse_scholar_plan(text: str) -> tuple[list[object], list[str]]:
    """`(entities, queries)` from the planner's JSON object, or a bare list of queries."""
    start = text.find("{")
    if start >= 0:
        try:
            parsed = json.loads(text[start : text.rfind("}") + 1])
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            entities = parsed.get("entities")
            return (
                list(entities) if isinstance(entities, list) else [],
                query_terms.query_list(parsed.get("queries")),
            )
    block = text[text.find("[") : text.rfind("]") + 1] if "[" in text else ""
    return [], query_terms.query_list(block)


def scholar_queries(text: str, request: str) -> tuple[list[str], list[str]]:
    """Planner reply → `(queries, entities)` kept on the request's subject.

    Every query names one of the request's entities when it has any (Hangul spellings
    mapped to Latin); invented topics are dropped and entity queries fill the gap.
    """
    from app.services.context import lead_instruction

    proposed, planned = _parse_scholar_plan(text)
    # names from the instruction only, not from pasted material
    entities = query_terms.key_entities(lead_instruction(request), proposed)
    kept = query_terms.on_request(planned, request, entities)
    if entities and len(kept) < 3:
        for query in query_terms.entity_queries(request, entities, scholarly=True):
            if all(query_terms.norm(query) != query_terms.norm(k) for k in kept):
                kept.append(query)
    return kept[:_SCHOLAR_QUERIES], entities


async def literature_plan(request: str, model: str, api_key: str) -> tuple[list[str], list[str]]:
    """`(queries, entities)` for a literature question; queries [] if none."""
    queries, _, entities = await _planned(
        request, model, api_key, scholarly=True, by_entity=True
    )
    return [q for q in queries if q.strip() and q.strip() != request[:300].strip()], entities


async def _plan(
    request: str, model: str, api_key: str, *, scholarly: bool = False,
    most: int = MAX_QUERIES,
) -> tuple[list[str], dict[str, int]]:
    """Search terms; request on failure."""
    queries, spent, _ = await _planned(request, model, api_key, scholarly=scholarly, most=most)
    return queries, spent


async def _planned(
    request: str, model: str, api_key: str, *, scholarly: bool = False,
    most: int = MAX_QUERIES, by_entity: bool = False,
) -> tuple[list[str], dict[str, int], list[str]]:
    """Search terms, spend and the request's entities (`by_entity`: chat literature lookup,
    every query names one); documents keep the planner's queries as planned."""
    from app.services.context import lead_instruction

    spent = {"inputTokens": 0, "outputTokens": 0}
    known = query_terms.key_entities(lead_instruction(request)) if by_entity else []
    fallback = query_terms.entity_queries(request, known, scholarly=True) or [request[:300]]
    if not model:
        return fallback, spent, known
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
                            "content": (
                                _SCHOLAR_PLAN_PROMPT.format(
                                    n=_SCHOLAR_QUERIES, request=request[:2000]
                                )
                                if scholarly
                                else _PLAN_PROMPT.format(n=most, request=request[:2000])
                            ),
                        }
                    ],
                    "max_tokens": 500 if scholarly else 300,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.info("research query planning failed: %s", exc)
        return fallback, spent, known

    raw = payload.get("usage") or {}
    spent = {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }
    text = (payload["choices"][0]["message"]["content"] or "").strip()
    if by_entity:
        queries, entities = scholar_queries(text, request)
        log.info("literature queries %s (entities %s)", queries, entities)
        return queries or fallback, spent, entities
    if scholarly:
        # Planner cap = literature mode's.
        return _parse_scholar_plan(text)[1][:_SCHOLAR_QUERIES] or fallback, spent, []
    return _parse_queries(text, request, most), spent, []


#: Literature-mode query count (each also via science lane).
_SCHOLAR_QUERIES = 6
_SCHOLAR_SOURCES = 16
_SCHOLAR_PLAN_PROMPT = """Plan English scholarly search queries (at most {n}) for the request
below (a literature question, a paper draft or a literature review).

Steps:
1. entities: the specific things the request names — products, services, tools, models,
   datasets, methods, organisations. Write each in its official Latin spelling even when the
   request spells it in Hangul (「허깅페이스」 → "Hugging Face", 「랭체인」 → "LangChain"), and
   copy the request's own spelling into "as_written". Only things the request names; [] if none.
2. queries: English keywords as they would appear in paper titles or text.
   - Stay on the request's own subject. Never switch to another topic.
   - With entities, every query contains at least one entity name exactly as written in
     "entities" (e.g. "<entity> LLM evaluation", "<entity> benchmark experiments").
   - Without entities, the first query finds a survey or SoK of the field; the others take
     one distinct axis each (problem, method families, benchmarks).
   - When the request asks for work at conferences or journals (「top-tier」, 학회, 저널),
     one or two queries add venue names (e.g. "<entity> ACL EMNLP", "<entity> NeurIPS ICLR").
   - Do not copy the request sentence; no years.

Request:
{request}

Answer with one JSON object only:
{{"entities": [{{"name": "...", "as_written": "..."}}], "queries": ["...", "..."]}}"""

#: Scholarly hosts exempt from one-per-host.
_SCHOLAR_HOSTS = (
    "arxiv.org", "aclanthology.org", "openreview.net", "proceedings.", "dl.acm.org",
    "usenix.org", "ieeexplore.ieee.org", "semanticscholar.org", "doi.org", "openalex.org",
    "papers.nips.cc", "neurips.cc", "mlr.press",
)


_JUDGE_PROMPT = """아래는 검색 결과 후보다. 요청의 주제를 직접 다루는 자료의 번호만 골라라.
낱말만 겹치고 다른 나라·시대·분야를 다루는 자료, 광고·쇼핑·잡문은 빼라. 같은 약어를
쓰는 다른 분야(시장 규모의 SAM과 계정 속성 SAM-Account-Name, 밀키트와 주식 트레이딩)도 빼라. 요청에
나라가 적혀 있지 않으면 한국 사용자의 요청이다 — 한국과 무관한 다른 나라 시장·제도
자료는 빼라(국제 비교를 요청한 경우는 예외).

요청:
{request}

후보:
{candidates}

JSON 배열 하나로만 답하라. 예: [1, 4, 7]"""


async def _on_subject(
    request: str,
    ranked: list[list[tuple[float, dict[str, str]]]],
    model: str,
    api_key: str,
) -> tuple[list[list[tuple[float, dict[str, str]]]], dict[str, int]]:
    """`ranked` minus model-judged off-subject hits; unchanged on failure."""
    spent = {"inputTokens": 0, "outputTokens": 0}
    order: list[str] = []
    for keep in ranked:
        for _, hit in keep[:6]:
            if hit["url"] not in order:
                order.append(hit["url"])
    order = order[:30]
    by_url = {hit["url"]: hit for keep in ranked for _, hit in keep}
    listing = "\n".join(
        f"{n}. {by_url[url].get('title', '')[:120]} — {by_url[url].get('snippet', '')[:160]}"
        for n, url in enumerate(order, 1)
    )
    from app.services.context import prompt_request

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
                    "messages": [{"role": "user", "content": _JUDGE_PROMPT.format(
                        request=prompt_request(request, 1500), candidates=listing
                    )}],
                    "max_tokens": 200,
                },
            )
            response.raise_for_status()
            payload = response.json()
        raw = payload.get("usage") or {}
        spent = {
            "inputTokens": int(raw.get("prompt_tokens") or 0),
            "outputTokens": int(raw.get("completion_tokens") or 0),
        }
        text = payload["choices"][0]["message"]["content"] or ""
        match = re.search(r"\[[\d,\s]{0,200}\]", text)
        chosen = {int(n) for n in json.loads(match.group(0))} if match else set()
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.info("research relevance check failed: %s", exc)
        return ranked, spent
    kept_urls = {order[n - 1] for n in chosen if 1 <= n <= len(order)}
    if not kept_urls:
        return ranked, spent
    # Unjudged lower hits stay out.
    judged = [[(sc, hit) for sc, hit in keep if hit["url"] in kept_urls] for keep in ranked]
    log.info("research kept %d of %d candidates on subject", len(kept_urls), len(order))
    return judged, spent


def _scholarly_host(url: str) -> bool:
    return any(h in (url or "") for h in _SCHOLAR_HOSTS)


async def _gather(base_url: str, query: str, *, scholarly: bool = False) -> list[dict[str, str]]:
    """One query's hits; [] on failure."""
    try:
        if scholarly:
            return await searxng(base_url, query, 8, kind="papers")
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
    scholarly: bool = False,
    deep: bool = False,
) -> Findings:
    """Plan, search concurrently, read top pages; `deep` doubles queries and sources.
    Never raises."""
    backends = await settings_store.tools_config()
    if not backends.search:
        return Findings()

    if deep:
        max_sources *= 2
    queries, spent = await _plan(request, model, api_key, scholarly=scholarly,
                                 most=MAX_QUERIES * (2 if deep else 1))
    if scholarly:
        max_sources = max(max_sources, _SCHOLAR_SOURCES)
    # Korean-scholarship requests add a Korean papers query.
    korean_query = (korean_papers.query_for(request, queries)
                    if korean_papers.wanted(request) else "")
    hit_lists, korean_hits = await asyncio.gather(
        asyncio.gather(*(_gather(backends.search, q, scholarly=scholarly) for q in queries)),
        korean_papers.search(korean_query) if korean_query else asyncio.sleep(0, []),
    )
    hit_lists = list(hit_lists)
    if korean_hits:
        # first lane: judged within the candidate cap and picked first each round
        queries, hit_lists = [korean_query, *queries], [korean_hits, *hit_lists]

    # Per-query filter and rank before fetch; round-robin, one per publisher/URL.
    sources: list[dict[str, Any]] = []
    seen_hosts: set[str] = set()
    seen_urls: set[str] = set()
    seen_works: list[tuple[str, ...]] = []
    ranked: list[list[tuple[float, dict[str, str]]]] = []
    dropped: dict[str, int] = {"차단": 0, "저신뢰": 0, "무관": 0}
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
            if credibility.tier(url) == 0:
                # Blogs/content sites uncitable.
                dropped["저신뢰"] += 1
                continue
            # Papers need higher relevance.
            if relevance(query, hit) < (_SCHOLAR_FLOOR if scholarly else _FLOOR):
                dropped["무관"] += 1
                continue
            keep.append((score(query, hit), hit))
        keep.sort(key=lambda pair: pair[0], reverse=True)
        ranked.append(keep)

    if any(dropped.values()):
        log.info(
            "research dropped %d hits (차단 %d · 저신뢰 %d · 무관 %d)",
            sum(dropped.values()),
            dropped["차단"],
            dropped["저신뢰"],
            dropped["무관"],
        )

    # Word overlap ≠ on-subject; one model title review.
    if model and sum(len(keep) for keep in ranked) > 3:
        ranked, judge_spent = await _on_subject(request, ranked, model, api_key)
        spent = {k: spent[k] + judge_spent[k] for k in spent}

    picks: list[tuple[int, dict[str, str]]] = []
    for rank in range(_PER_QUERY):
        for query_index, keep in enumerate(ranked):
            if rank >= len(keep):
                continue
            hit = keep[rank][1]
            url = hit["url"]
            if url in seen_urls:
                continue
            # Same paper across hosts cited once.
            work = _work_key(hit["title"])
            if work and any(_same_work(work, other) for other in seen_works):
                continue
            host = _publisher(url)
            if host in seen_hosts and not (scholarly and _scholarly_host(url)) \
                    and hit.get("lane") != korean_papers.LANE:
                continue
            seen_urls.add(url)
            seen_hosts.add(host)
            if work:
                seen_works.append(work)
            picks.append((query_index, hit))
            if len(picks) >= max_sources:
                break
        if len(picks) >= max_sources:
            break

    # Truncated titles completed from another hit of the same work.
    every = [hit for hits in hit_lists for hit in hits]
    picks = [(q, _fuller(hit, every)) for q, hit in picks]

    # Bodies for top `_BODIES` per query, fetched together.
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
        hit = _titled_from_page(hit, body)
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
                **bibliography(hit),
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
            lines.append(f"본문 발췌:\n{page_text(body)[:_BODY_CHARS]}")
        blocks.append("\n".join(lines))

    if korean_query and not any(hit.get("lane") == korean_papers.LANE for _, hit in picks):
        # None found: stated, so the writer doesn't invent 국내 동향.
        blocks.append(korean_papers.none_found(korean_query))
    return Findings(
        sources=sources,
        context="\n\n".join(blocks),
        queries=queries,
        searched=True,
        dropped=sum(dropped.values()),
        usage=spent,
    )


#: Product comparison request.
_PRODUCTS = re.compile(r"솔루션|제품|도구|툴|서비스|플랫폼|벤더|프레임워크")
_COMPARES = re.compile(r"비교|동향|분석|조사")
_PRODUCTS_PER_REQUEST = 4

_PRODUCTS_PROMPT = """아래 웹 자료에 실제로 이름이 나오는 제품·서비스를 최대 {n}개 골라라.
요청: {request}

규칙:
- 「제품명 (제공사)」 꼴로. 예: "Akto Argus (Akto)", "시큐어브리지 (안랩클라우드메이트)".
- 자료에 그 이름이 그대로 나와야 한다. 범주·분야·기술 이름(「AI 기반 위협 탐지」)은 넣지 마라.
- 요청 주제와 직접 관련된 것만. 없으면 [].

자료:
{context}

JSON 배열 하나만 답하라."""


async def _ask(prompt: str, model: str, api_key: str, max_tokens: int) -> tuple[str, dict]:
    spent = {"inputTokens": 0, "outputTokens": 0}
    base, _ = await settings_store.litellm_config()
    async with httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(90.0, connect=10.0),
    ) as client:
        body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens, **thinking.switch(model)}
        response = await client.post("/v1/chat/completions", json=body)
        if thinking.refused(model, response):
            body.update(thinking.switch(model))
            response = await client.post("/v1/chat/completions", json=body)
        response.raise_for_status()
        payload = response.json()
    raw = payload.get("usage") or {}
    spent = {"inputTokens": int(raw.get("prompt_tokens") or 0),
             "outputTokens": int(raw.get("completion_tokens") or 0)}
    return (payload["choices"][0]["message"]["content"] or "").strip(), spent


#: Comparison axis.
_AXIS = r"[가-힣A-Za-z0-9]{1,10}(?: [가-힣A-Za-z0-9]{1,10})?"
_AXES = re.compile(
    rf"((?:{_AXIS},\s?){{2,}}{_AXIS}?)\s?"
    r"(?:등을|등의|을|를)\s?(?:종합적으로\s?|중심으로\s?)?(?:비교|분석|조사|정리)"
)
#: Clause ending, not an axis.
_CONNECTIVE = re.compile(r"(?:는데|하고|하며|지만|으며|이며|는|고|며|서)$")


def comparison_axes(request: str) -> list[str]:
    """Product comparison axes; [] if none."""
    if not (_PRODUCTS.search(request) and _COMPARES.search(request)):
        return []
    m = _AXES.search(request)
    if not m or re.search(r"구성[은:]", request[max(0, m.start() - 8):m.start()]):
        return []
    axes = []
    for item in (a.strip() for a in m.group(1).split(",")):
        item = item.rsplit("의 ", 1)[-1]  # 「3곳의 가격」 → 「가격」
        if item and not _CONNECTIVE.search(item):
            axes.append(item)
    return axes[:5] if len(axes) >= 2 else []


async def deepen_for_products(
    request: str, findings: Findings, *, model: str, api_key: str
) -> Findings:
    """Re-search each found product for its own pages; appends to `findings`. Never raises."""
    axes = comparison_axes(request)
    if not findings.sources or not model or not axes:
        return findings
    try:
        text, spent = await _ask(
            _PRODUCTS_PROMPT.format(n=_PRODUCTS_PER_REQUEST, request=request[:600],
                                    context=findings.context[:15000]),
            model, api_key, 300,
        )
        block = text[text.find("[") : text.rfind("]") + 1] if "[" in text else "[]"
        names = [str(n).strip()[:80] for n in json.loads(block) if str(n).strip()]
    except Exception as exc:  # noqa: BLE001 — the first search stands
        log.info("product discovery failed: %s", exc)
        return findings
    lowered = findings.context.lower()
    # Names absent from pages are the model's; dropped.
    names = [n for n in names if re.split(r"[\s(]", n.lower())[0] in lowered]
    names = names[:_PRODUCTS_PER_REQUEST]
    findings.usage = {k: findings.usage.get(k, 0) + spent.get(k, 0) for k in spent}
    if not names:
        return findings
    found = await asyncio.gather(
        *(run(f"{name} {' '.join(axes)}", model=model, api_key=api_key, max_sources=2)
          for name in names),
        return_exceptions=True,
    )
    for more in found:
        if isinstance(more, BaseException) or not more.sources:
            continue
        findings = merge(findings, more)
    log.info("product pages read for %s: %d sources now", names, len(findings.sources))
    return findings


def merge(base: Findings, more: Findings, *, most: int | None = None) -> Findings:
    """`base` plus `more`'s sources it lacks (same URL counts once), renumbered after base's;
    base keeps its numbering, its pages and its order. `most` caps the sources added."""
    sources = list(base.sources)
    blocks = [base.context] if base.context else []
    seen = {s.get("url") for s in sources}
    by_ordinal = {}
    for part in re.split(r"(?m)^(?=\[\d+\] )", more.context or ""):
        if m := re.match(r"\[(\d+)\] ", part):
            by_ordinal[int(m.group(1))] = part
    added = 0
    for src in more.sources:
        if src.get("url") in seen or (most is not None and added >= most):
            continue
        seen.add(src.get("url"))
        ordinal = len(sources) + 1
        part = by_ordinal.get(src.get("ordinal"), "")
        sources.append({**src, "ordinal": ordinal})
        added += 1
        if part:
            blocks.append(re.sub(r"^\[\d+\] ", f"[{ordinal}] ", part.strip(), count=1))
    usage = {k: base.usage.get(k, 0) + more.usage.get(k, 0)
             for k in {*base.usage, *more.usage}}
    return Findings(
        sources=sources,
        context="\n\n".join(blocks),
        queries=[*base.queries, *(q for q in more.queries if q not in base.queries)],
        searched=base.searched or more.searched,
        dropped=base.dropped + more.dropped,
        usage=usage,
    )


def carried(findings: Findings, web_count: int) -> dict[str, Any]:
    """The outline pass's web findings as stored on the proposal (`from_carried` reads it);
    only the first `web_count` sources — project files are appended after them."""
    return {
        "sources": [dict(s) for s in findings.sources[:web_count]],
        "context": findings.context,
        "queries": list(findings.queries),
        "dropped": findings.dropped,
    }


def from_carried(stored: Any) -> Findings | None:
    """Findings the outline pass found (`carried`), or None when the plan carries none."""
    if not isinstance(stored, dict) or not stored.get("sources"):
        return None
    sources = [dict(s) for s in stored["sources"] if isinstance(s, dict) and s.get("url")]
    if not sources:
        return None
    return Findings(
        sources=sources,
        context=str(stored.get("context") or ""),
        queries=[str(q) for q in stored.get("queries") or []],
        searched=True,
        dropped=int(stored.get("dropped") or 0),
    )


#: Heading words that name a part of any document, not what to look up.
_GENERIC_HEADING = frozenset(
    "개요 현황 결론 요약 시사점 방안 전략 분석 배경 목적 서론 제언 권고 정리 검토 대응 과제 향후 "
    "계획 주요 기대 효과 필요성 동향 결과 참고문헌 부록 방향 개선 추진 문제점 결론및 및 위한 대한 "
    "관련 사항 내용 기준 비교 종합 핵심 경로 절차 단계 체계 구조 이해 조사 범위 출처 확인 필요 "
    "목차 서문 맺음말 마무리 들어가며 나가며".split()
)
_HEADING_TAIL = re.compile(r"(?:의|과|와|별|을|를|에|및)$")


def uncovered(headings: list[str], context: str) -> list[str]:
    """Approved headings the found pages say nothing about: fewer than half of a heading's
    own words (generic part names like 「현황」·「시사점」 aside) occur in `context`.
    Spacing is ignored (「디지털 서비스몰」 covers 「디지털서비스몰」)."""
    flat = re.sub(r"\s+", "", (context or "").lower())
    gaps: list[str] = []
    for heading in headings:
        words = []
        for word in re.findall(r"[0-9A-Za-z가-힣]{2,}", heading or ""):
            if len(word) > 2:
                word = _HEADING_TAIL.sub("", word)
            if word not in _GENERIC_HEADING and not word.isdigit():
                words.append(word.lower())
        if words and sum(1 for w in words if w in flat) * 2 < len(words):
            gaps.append(heading)
    return gaps


#: Untrusted data block header, provenance web search.
CONTEXT_HEADER = (
    "# 웹 검색 결과\n"
    "아래는 이 문서를 쓰기 위해 방금 웹에서 찾은 자료입니다. "
    "사실·수치·제품명·버전은 기억이 아니라 이 자료에서 가져오세요. "
    "여기에 없는 사실을 단정하지 말고, 자료와 기억이 어긋나면 자료를 따르세요.\n"
)


def context_block(findings: Findings) -> str:
    """Findings as one untrusted-context entry, or ''."""
    if not findings.context:
        return ""
    return CONTEXT_HEADER + "\n" + findings.context


#: Writer rule: search found nothing citable.
EMPTY_RULE = (
    "웹을 검색했지만 인용할 만한 자료를 찾지 못했습니다. "
    "확인되지 않은 수치나 제품명을 단정하지 말고, 확인하지 못했다는 사실을 "
    "본문에 밝히세요."
)

#: Writer rule: no search backend.
UNRESEARCHED_RULE = (
    "웹 검색을 쓸 수 없어 이 문서는 검색 없이 작성됩니다. "
    "최신 정보나 제품명·버전·수치를 단정하지 말고, 확인하지 못한 항목은 "
    "확인이 필요하다고 밝히세요."
)


def _work_key(title: str) -> tuple[str, ...]:
    """Lowercased title words for work matching; () under four words."""
    words = tuple(w.lower() for w in re.findall(r"[0-9A-Za-z가-힣]+", title or ""))
    return words if len(words) >= 4 else ()


def _same_work(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Same work: equal keys or one a truncated prefix."""
    short, long_ = sorted((a, b), key=len)
    return long_[: len(short)] == short


def _fuller(hit: dict, others: list[dict]) -> dict:
    """`hit` with the longest title for its work."""
    title = str(hit.get("title") or "").strip()
    work = _work_key(title)
    if not work:
        return hit
    longer = [str(o.get("title") or "").strip() for o in others
              if len(str(o.get("title") or "").strip()) > len(title)]
    best = max((t for t in longer if (key := _work_key(t)) and _same_work(work, key)),
               key=len, default="")
    return {**hit, "title": best} if best else hit


#: Dangling last word of a cut-off title.
_DANGLING = re.compile(
    r"(?:\b(?:of|for|and|the|in|on|with|to|a|an|via|from|by|using)|[:,\-–]|\.\.\.|…)$", re.I)


def _titled_from_page(hit: dict, body: str) -> dict:
    """Cut-off title completed from the read page."""
    title = str(hit.get("title") or "").strip()
    if not body or not _DANGLING.search(title):
        return hit
    stem = re.sub(r"\s*(?:\.\.\.|…)$", "", title).strip()
    if len(stem) < 12:
        return hit
    for line in body[:6000].splitlines():
        line = line.strip(" #*\t")
        if (line.lower().startswith(stem.lower()) and len(stem) < len(line) <= 250
                and not _DANGLING.search(line)):
            return {**hit, "title": line}
    return hit


#: Hosts mapped to venue names.
_VENUES = {
    "arxiv.org": "arXiv", "aclanthology.org": "ACL Anthology", "dl.acm.org": "ACM",
    "ieeexplore.ieee.org": "IEEE", "proceedings.neurips.cc": "NeurIPS",
    "papers.nips.cc": "NeurIPS", "openreview.net": "OpenReview", "mdpi.com": "MDPI",
    "link.springer.com": "Springer", "sciencedirect.com": "Elsevier",
    "proceedings.mlr.press": "PMLR", "aaai.org": "AAAI", "ojs.aaai.org": "AAAI",
    "kci.go.kr": "KCI", "dbpia.co.kr": "DBpia", "nature.com": "Nature", "riss.kr": "RISS",
    "earticle.net": "earticle", "kiss.kstudy.com": "KISS", "scienceon.kisti.re.kr": "ScienceON",
    "koreascience.kr": "KoreaScience", "koreascience.or.kr": "KoreaScience",
}
#: Year in a paper URL (arXiv, ACL Anthology, /YYYY/).
_URL_YEAR = (
    re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{2})(?:0[1-9]|1[0-2])\.\d{4,5}"),
    re.compile(r"aclanthology\.org/(\d{4})\.[a-z]"),
    re.compile(r"/((?:19|20)\d{2})/"),
)
_DOI = re.compile(r"(?:doi\.org/|/doi/(?:abs/|full/|pdf/)?)(10\.\d{4,9}/[^\s?#]{1,120})", re.I)


def bibliography(hit: dict) -> dict[str, str]:
    """Authors, year, venue, DOI from search fields and link only; nothing guessed."""
    out: dict[str, str] = {}
    url = str(hit.get("url") or "")
    authors = hit.get("authors")
    if isinstance(authors, (list, tuple)):
        names = [str(a).strip() for a in authors if str(a).strip()]
        if names:
            out["author"] = ", ".join(names[:3]) + (" 외" if len(names) > 3 else "")
    elif isinstance(authors, str) and authors.strip():
        out["author"] = authors.strip()[:200]
    published = str(hit.get("published") or "")
    year = published[:4] if re.fullmatch(r"(?:19|20)\d{2}", published[:4]) else ""
    if not year:
        for pattern in _URL_YEAR:
            if m := pattern.search(url):
                year = m.group(1) if len(m.group(1)) == 4 else f"20{m.group(1)}"
                break
    if year:
        out["year"] = year
    venue = str(hit.get("journal") or "").strip()
    host = _publisher(url)
    venue = venue or next((name for key, name in _VENUES.items()
                           if host == key or host.endswith("." + key)), "")
    if venue:
        # Volume/issue when given.
        volume, issue = (str(hit.get(k) or "").strip() for k in ("volume", "issue"))
        if volume and hit.get("journal"):
            venue = f"{venue}, {volume}" + (f"({issue})" if issue else "")
        out["publisher"] = venue[:120]
    doi = str(hit.get("doi") or "").strip() or (m.group(1) if (m := _DOI.search(url)) else "")
    if doi:
        out["doi"] = doi.rstrip(".")
    return out


def _title_terms(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[0-9A-Za-z가-힣]{2,}", text or "")}


async def verify_titles(titles: list[str]) -> dict[str, bool]:
    """Title found in search (70% word overlap); unsearchable titles omitted."""
    backends = await settings_store.tools_config()
    if not backends.search or not titles:
        return {}

    async def one(title: str) -> tuple[str, bool | None]:
        try:
            hits = await searxng(backends.search, f'"{title}"', 6)
        except (httpx.HTTPError, ValueError):
            return title, None
        want = _title_terms(title)
        for hit in hits:
            have = _title_terms(f"{hit.get('title', '')} {hit.get('snippet', '')}")
            if want and len(want & have) >= 0.7 * len(want):
                return title, True
        return title, False

    results = await asyncio.gather(*(one(t) for t in titles[:15]))
    return {title: ok for title, ok in results if ok is not None}
