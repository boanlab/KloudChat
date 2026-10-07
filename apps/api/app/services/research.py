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

from app.services import credibility, settings_store, thinking
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


#: A page's frame, not its text: sign-in prompts, menus, share and skip links.
_CHROME = re.compile(
    r"로그인|회원가입|비밀번호|아이디 찾기|바로가기|메뉴|공유하기|구독하기|카카오톡|페이스북|"
    r"Copyright|All rights reserved|쿠키|cookie|Sign in|Log in|Subscribe|Skip to", re.I
)
_LINK_ONLY = re.compile(r"^[\s*>#-]*(?:\[[^\]]*\]\([^)]*\)[\s|·,/]*)+$")


def page_text(markdown: str) -> str:
    """A scraped page without its frame: link-only lines, site chrome and menu runs."""
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
_SCHOLAR_FLOOR = 0.5

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
    """Rank above the floor: relevance, plus preferred hosts and credibility tier."""
    base = relevance(query, hit)
    url = hit.get("url", "")
    preferred = 0.4 if any(good in _host(url) for good in _PREFERRED) else 0.0
    return base + preferred + 0.15 * credibility.tier(url)


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


async def literature_queries(request: str, model: str, api_key: str) -> list[str]:
    """English keyword queries for a literature question; `[]` when the planner gave none."""
    queries, _ = await _plan(request, model, api_key, scholarly=True)
    return [q for q in queries if q.strip() and q.strip() != request[:300].strip()]


async def _plan(
    request: str, model: str, api_key: str, *, scholarly: bool = False
) -> tuple[list[str], dict[str, int]]:
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
                            "content": (
                                _SCHOLAR_PLAN_PROMPT.format(
                                    n=_SCHOLAR_QUERIES, request=request[:2000]
                                )
                                if scholarly
                                else _PLAN_PROMPT.format(n=MAX_QUERIES, request=request[:2000])
                            ),
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
    queries = _parse_queries(text, request)
    if scholarly:
        # The planner's cap is the literature mode's own.
        block = text[text.find("[") : text.rfind("]") + 1] if "[" in text else ""
        try:
            parsed = [str(q).strip()[:200] for q in json.loads(block) if str(q).strip()]
        except (json.JSONDecodeError, ValueError, TypeError):
            parsed = []
        queries = parsed[:_SCHOLAR_QUERIES] or queries
    return queries, spent


#: The literature mode: more English keyword queries, each also through the science lane.
_SCHOLAR_QUERIES = 6
_SCHOLAR_SOURCES = 16
_SCHOLAR_PLAN_PROMPT = """다음 요청으로 학술 문서(논문 초안·문헌 정리)를 쓰려고 한다. 관련 연구를
찾을 영어 학술 검색어를 최대 {n}개 만들어라.

규칙:
- 영어 키워드만. 논문 제목에 나올 법한 용어로(예: "indirect prompt injection defense LLM agents").
- 첫 검색어는 이 분야를 정리한 서베이·SoK를 찾는 것(예: "survey prompt injection attacks
  defenses", "SoK prompt injection"). 서베이가 기준 논문을 알려 준다.
- 나머지는 서로 다른 축을 하나씩: 문제·공격, 방어 계열마다 하나, 벤치마크·평가.
- 요청 문장을 그대로 쓰지 말고, 연도나 학회 이름은 넣지 마라.

요청:
{request}

JSON 배열 하나만 답하라. 예: ["...", "..."]"""

#: Scholarly hosts: one paper per host would leave a single arXiv paper.
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
    """`ranked` without the hits the model judged off-subject; unchanged if judging fails."""
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
    # Hits below the judged window were not shown; they stay out too.
    judged = [[(sc, hit) for sc, hit in keep if hit["url"] in kept_urls] for keep in ranked]
    log.info("research kept %d of %d candidates on subject", len(kept_urls), len(order))
    return judged, spent


def _scholarly_host(url: str) -> bool:
    return any(h in (url or "") for h in _SCHOLAR_HOSTS)


async def _gather(base_url: str, query: str, *, scholarly: bool = False) -> list[dict[str, str]]:
    """One query's hits; `[]` on any failure."""
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
) -> Findings:
    """Plan queries, search them concurrently, read the top pages. Never raises.

    `scholarly`: the literature mode — English keyword queries through the science lane
    too, more sources, and several papers from one scholarly host."""
    backends = await settings_store.tools_config()
    if not backends.search:
        return Findings()

    queries, spent = await _plan(request, model, api_key, scholarly=scholarly)
    if scholarly:
        max_sources = max(max_sources, _SCHOLAR_SOURCES)
    hit_lists = await asyncio.gather(
        *(_gather(backends.search, q, scholarly=scholarly) for q in queries)
    )

    # Filtered and ranked per query before anything is fetched; picked
    # round-robin across queries, one publisher and one URL at most.
    sources: list[dict[str, Any]] = []
    seen_hosts: set[str] = set()
    seen_urls: set[str] = set()
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
                # A blog or content site is not something a document can cite.
                dropped["저신뢰"] += 1
                continue
            # Papers need more of the query's terms to count as relevant.
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

    # Shared words do not make a hit on-subject; the model reviews the titles once.
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
            host = _publisher(url)
            if host in seen_hosts and not (scholarly and _scholarly_host(url)):
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
            lines.append(f"본문 발췌:\n{page_text(body)[:_BODY_CHARS]}")
        blocks.append("\n".join(lines))

    return Findings(
        sources=sources,
        context="\n\n".join(blocks),
        queries=queries,
        searched=True,
        dropped=sum(dropped.values()),
        usage=spent,
    )


#: A request comparing products: 「AI 보안 솔루션 … 배포 방법, 주요 기능 … 분석」.
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


#: The axes a comparison is asked along: 「대상 환경, 배포 방법, 주요 기능 등을 … 분석」.
_AXIS = r"[가-힣A-Za-z0-9]{1,10}(?: [가-힣A-Za-z0-9]{1,10})?"
_AXES = re.compile(
    rf"((?:{_AXIS},\s?){{2,}}{_AXIS}?)\s?"
    r"(?:등을|등의|을|를)\s?(?:종합적으로\s?|중심으로\s?)?(?:비교|분석|조사|정리)"
)
#: A clause ending, not a thing compared: 「…등장하고 있는데,」.
_CONNECTIVE = re.compile(r"(?:는데|하고|하며|지만|으며|이며|는|고|며|서)$")


def comparison_axes(request: str) -> list[str]:
    """The axes a product comparison is asked along; `[]` when no products are compared."""
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
    """Products found by the first search, each searched again for its own pages.

    Returns `findings` with the new pages appended; never raises."""
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
    # A name the pages do not carry is the model's, not the sources'.
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
    sources = list(findings.sources)
    blocks = [findings.context] if findings.context else []
    seen = {s["url"] for s in sources}
    for more in found:
        if isinstance(more, BaseException) or not more.sources:
            continue
        for key in findings.usage:
            findings.usage[key] += more.usage.get(key, 0)
        parts = re.split(r"(?m)^(?=\[\d+\] )", more.context)
        by_ordinal = {}
        for part in parts:
            m = re.match(r"\[(\d+)\] ", part)
            if m:
                by_ordinal[int(m.group(1))] = part
        for src in more.sources:
            if src["url"] in seen:
                continue
            seen.add(src["url"])
            ordinal = len(sources) + 1
            part = by_ordinal.get(src["ordinal"], "")
            sources.append({**src, "ordinal": ordinal})
            if part:
                blocks.append(re.sub(r"^\[\d+\] ", f"[{ordinal}] ", part.strip(), count=1))
        findings.queries = [*findings.queries, *more.queries]
    log.info("product pages read for %s: %d sources now", names, len(sources))
    findings.sources = sources
    findings.context = "\n\n".join(blocks)
    return findings


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


def _title_terms(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[0-9A-Za-z가-힣]{2,}", text or "")}


async def verify_titles(titles: list[str]) -> dict[str, bool]:
    """Whether each title turns up in a search (70% of its words in a hit).

    Titles that could not be searched are left out rather than called unverified."""
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
