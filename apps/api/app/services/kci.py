"""Korean scholarly literature from KCI (한국학술지인용색인, kci.go.kr).

With `kci_api_key` (env `KCI_API_KEY`, compose `KCHAT_KCI_API_KEY`) the official Open API
(`open.kci.go.kr`, `apiCode=articleSearch`) is asked by title words: each hit carries the
work's title, authors, year, journal, DOI and KCI page, the same fields `research.bibliography`
reads for the reference list. Without a key the search backend is asked within
`site:kci.go.kr` instead: titles and links only, no authors.

The API container calls `open.kci.go.kr` directly (it has internet egress, as the weather tool
does). The key travels in the query string, so it is never logged: the httpx request line is
redacted and a failure is logged by its type and status only.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from app.core.config import settings
from app.services import settings_store

log = logging.getLogger(__name__)

API_URL = "https://open.kci.go.kr/po/openapi/openApiSearch.kci"
SITE = "kci.go.kr"
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

#: A request about Korean scholarship: 「국내 동향」, 「국내외 연구」, 「한국 학계」,
#: 「KCI」, 「등재지」.
_WANTED = re.compile(
    r"국내|KCI|한국\s{0,2}(?:의\s{0,2})?(?:연구|학계|학술|논문)|"
    r"등재\s{0,2}(?:지|학술지|논문)|국문\s{0,2}논문|한국어\s{0,2}논문|한국학술지", re.I)

#: Words of a request that say what to write, not what it is about.
_STOP = frozenset("""
국내 국외 국내외 해외 한국 동향 연구 연구들 논문 논문들 학술 학계 자료
문헌 선행 최근 현황 조사 정리 분석 검토 보고서 계획서 제안서 리포트 발표
슬라이드 작성 작성해 써 써줘 써주세요 주세요 해주세요 해줘 만들어 만들어줘
대한 관한 위한 관련 대해 대해서 중심 중심으로 바탕 바탕으로 기반 포함
포함한 포함해 및 등 그리고 또는 내용 주제 개요 있는 없는 하는 되는 통한
따른 무엇 어떤 어떻게 정도 부분 기존 향후 앞으로 필요 필요성 방향 KCI
등재지 등재
""".split())
_PARTICLE = re.compile(
    r"(?:으로서|으로|에서|에게|까지|부터|처럼|이나|이며|과의|와의|이라|라는|"
    r"의|을|를|은|는|이|가|에|로|과|와|도|만)$")


#: A verb, not a subject: 「정리해」, 「작성하는」, 「비교한」.
_VERB = re.compile(r"(?:해|해서|하여|하고|하기|하는|하며|했|합니다|주세요|해줘|된|되는|되어|한)$")


def wanted(request: str) -> bool:
    """Whether the request asks about Korean research (and KCI should be searched)."""
    return bool(_WANTED.search(request or ""))


def terms(text: str, limit: int = 3) -> list[str]:
    """The subject words of `text`, in order: Hangul words without their particles and the
    request words, and Latin names (「LLM」)."""
    out: list[str] = []
    for raw in re.findall(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9+-]{1,20}", text or ""):
        word = raw
        if re.match(r"[가-힣]", raw) and len(stem := _PARTICLE.sub("", raw)) >= 2:
            word = stem  # 「교육과」 → 「교육」; 「평가」 stays
        if len(word) < 2 or word in _STOP or raw in _STOP or word in out or (
                len(raw) >= 3 and _VERB.search(raw)):
            continue
        out.append(word)
        if len(out) >= limit:
            break
    return out


class _Redact(logging.Filter):
    """The httpx request line without the KCI key."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "open.kci.go.kr" in message and "key=" in message:
            record.msg, record.args = re.sub(r"key=[^&\s\"']+", "key=***", message), ()
        return True


logging.getLogger("httpx").addFilter(_Redact())


def _text(node: ET.Element | None) -> str:
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip() if node is not None else ""


def _doi(raw: str) -> str:
    m = re.search(r"10\.\d{4,9}/\S{1,120}", raw or "")
    return m.group(0).rstrip(".") if m else ""


def parse(xml: str | bytes) -> list[dict[str, Any]]:
    """`articleSearch` records as search hits; `[]` for an error answer.

    Raises `ValueError` for an answer that is not XML or says the key is wrong."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError("KCI answered with something that is not XML") from exc
    if (error := root.find("error")) is not None:
        raise ValueError(f"KCI refused the request: {_text(error)[:120]}")
    hits: list[dict[str, Any]] = []
    for record in root.iter("record"):
        journal, article = record.find("journalInfo"), record.find("articleInfo")
        if article is None:
            continue
        titles = {t.get("lang") or "": _text(t) for t in article.iter("article-title")}
        title = titles.get("original") or next((t for t in titles.values() if t), "")
        url = _text(article.find("url"))
        if not title or not url:
            continue
        # 「김갑수(서울교육대학교)」 → 「김갑수」.
        authors = [re.sub(r"\s*\(.*\)\s*$", "", _text(a)) for a in article.iter("author")]
        year = _text(journal.find("pub-year")) if journal is not None else ""
        abstracts = {a.get("lang") or "": _text(a) for a in article.iter("abstract")}
        hits.append({
            "title": title,
            "url": url,
            "snippet": (abstracts.get("original") or next(iter(abstracts.values()), ""))[:600],
            "published": year if re.fullmatch(r"(?:19|20)\d{2}", year) else "",
            "authors": [a for a in authors if a],
            "journal": _text(journal.find("journal-name")) if journal is not None else "",
            "doi": _doi(_text(article.find("doi"))),
            "lane": "kci",
        })
    return hits


async def _api(query: str, count: int) -> list[dict[str, Any]]:
    params = {"apiCode": "articleSearch", "key": settings.kci_api_key.strip(),
              "title": query, "displayCount": max(1, min(count, 100)), "page": 1}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(API_URL, params=params)
        response.raise_for_status()
        return parse(response.content)


async def _site_search(query: str, count: int) -> list[dict[str, Any]]:
    from app.services.tools.builtin import searxng

    backends = await settings_store.tools_config()
    if not backends.search:
        return []
    hits = await searxng(backends.search, query, count, hints={"site": SITE})
    return [{**hit, "lane": "kci"} for hit in hits]


def configured() -> bool:
    return bool(settings.kci_api_key.strip())


async def search(query: str, count: int = 6) -> list[dict[str, Any]]:
    """KCI hits for `query`: the Open API with a key, a `site:kci.go.kr` search without one.
    A title search with every word of a long query finds little, so a query with no hits is
    tried again on its first two words. `[]` on any failure; never raises."""
    query = (query or "").strip()
    if not query:
        return []
    ask = _api if configured() else _site_search
    tries = [query]
    words = query.split()
    if configured() and len(words) > 2:
        tries.append(" ".join(words[:2]))
    for attempt in tries:
        try:
            if hits := await ask(attempt, count):
                return hits[:count]
        except httpx.HTTPStatusError as exc:
            # The URL holds the key: the status only.
            log.info("KCI search failed: HTTP %s", exc.response.status_code)
            return []
        except (httpx.HTTPError, ValueError) as exc:
            log.info("KCI search failed: %s", type(exc).__name__ if isinstance(
                exc, httpx.HTTPError) else str(exc)[:160])
            return []
    return []


def query_for(request: str, planned: list[str] | None = None) -> str:
    """One title-word query for KCI: a planned Korean query's subject words, else the
    request's."""
    for candidate in [*(q for q in planned or [] if re.search(r"[가-힣]", q)), request]:
        if found := terms(candidate, 3):
            return " ".join(found)
    return ""


__all__ = ["API_URL", "configured", "parse", "query_for", "search", "terms", "wanted"]
