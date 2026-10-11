"""Korean scholarly papers (KCI·DBpia·RISS 등), found through the ordinary search backend.

There is no KCI API here. A Korean researcher looks for domestic papers by searching the
subject with 「학회 논문」, 「학술대회 발표」, 「학회지」, or inside the paper databases
themselves; `search` does the same: several query variants through the search backend at
once, the hits merged and deduplicated, those on the Korean scholarly databases first.

The top hits' landing pages are then read (raw HTML through the page reader) for the
bibliographic meta tags the databases publish — `citation_title`, `citation_author`,
`citation_publication_date`, `citation_journal_title`, `citation_volume`/`issue`,
`citation_doi`, and `DC.*` — so a reference reads with its authors, year, venue and DOI.
A page that cannot be read or carries no tags leaves the hit as its title, the venue the
search title names (else the database), and its link.

Each hit carries the fields `research.bibliography` reads: title, url, snippet, published,
authors, journal, doi (and volume, issue), with `lane` set to `LANE`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from app.services import credibility, settings_store

log = logging.getLogger(__name__)

LANE = "korean_papers"

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
등재지 등재 학회 학회지 학술대회 학술지 찾아 찾아줘
""".split())
_PARTICLE = re.compile(
    r"(?:으로서|으로|에서|에게|까지|부터|처럼|이나|이며|과의|와의|이라|라는|"
    r"의|을|를|은|는|이|가|에|로|과|와|도|만)$")
#: A verb, not a subject: 「정리해」, 「작성하는」, 「비교한」.
_VERB = re.compile(r"(?:해|해서|하여|하고|하기|하는|하며|했|합니다|주세요|해줘|된|되는|되어|한)$")

#: How a Korean researcher phrases a search for domestic papers on a subject.
_PHRASES = ("{} 학회 논문", "{} 학술대회 발표", "{} 학회지")
#: The paper databases searched inside, each with its own query.
SITES = ("kci.go.kr", "dbpia.co.kr", "riss.kr", "scienceon.kisti.re.kr", "earticle.net")

#: Korean paper databases, most useful first when one work turns up on several of them
#: (the first ones publish the fullest Korean meta tags).
_DATABASES = (
    "dbpia.co.kr", "journal.kci.go.kr", "www.kci.go.kr", "kci.go.kr", "earticle.net",
    "kiss.kstudy.com", "scienceon.kisti.re.kr", "koreascience.kr", "koreascience.or.kr",
    "riss.kr",
)
#: Pages of those databases that are one paper, not a search, list or front page.
_ARTICLE_PAGE = re.compile(
    r"dbpia\.co\.kr/journal/articleDetail|kci\.go\.kr/(?:.*articleView|.*ciSereArtiView|"
    r"kciportal/landing/article|handle/kci/)|"
    # RISS: journal articles (1a0202e37d52c72d) and theses (be54d9b8bc7cdb09), not books.
    r"riss\.kr/search/detail/DetailView\.do\?p_mat_type=(?:1a0202e37d52c72d|be54d9b8bc7cdb09)|"
    r"earticle\.net/Article/A|kiss\.kstudy\.com/(?:Detail/Ar|thesis/thesis-view)|"
    r"scienceon\.kisti\.re\.kr/srch/selectPORSrchArticle|koreascience\.(?:or\.)?kr/article/",
    re.I)
#: Words a Korean paper's title ends or turns on: 「…에 관한 연구」, 「…의 효과 분석」.
_PAPER_TITLE = re.compile(r"연구|분석|고찰|방안|효과|개발|설계|평가|탐색|비교|모형|기법|사례|검증")
#: A database's own page title, or a journal's notice, not a paper.
_NOT_A_PAPER = re.compile(
    r"상세보기|^(?:RISS|DBpia|KCI|ScienceON|KISS|earticle)\b.*검색|투고\s?규정|편집\s?위원|"
    r"^목차|발간사|회칙|^Research (?:&amp;|&) Publication Ethics", re.I)
#: Pages read for their meta tags.
_ENRICH = 6


def wanted(request: str) -> bool:
    """Whether the request asks about Korean research (and Korean papers should be searched)."""
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


def query_for(request: str, planned: list[str] | None = None) -> str:
    """The subject words (up to four) to search Korean papers by: a planned Korean query's, else the
    request's."""
    for candidate in [*(q for q in planned or [] if re.search(r"[가-힣]", q)), request]:
        if found := terms(candidate, 4):
            return " ".join(found)
    return ""


def variants(subject: str) -> list[tuple[str, str]]:
    """(query, site) pairs searched for `subject`: the researcher's phrasings on the open
    web, then the subject inside each paper database."""
    subject = subject.strip()
    if not subject:
        return []
    return [*((p.format(subject), "") for p in _PHRASES), *((subject, s) for s in SITES)]


# ── hits ──────────────────────────────────────────────────────────────


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _database(url: str) -> int:
    """The rank of the Korean paper database `url` is on; -1 when it is on none."""
    host = _host(url)
    for rank, name in enumerate(_DATABASES):
        if host == name or host.endswith("." + name):
            return rank
    return -1


def _hangul(text: str) -> bool:
    return bool(re.search(r"[가-힣]", text or ""))


#: What the databases put around a title in a search result or page title.
_TITLE_TAIL = re.compile(
    r"\s*(?:\|\s*(?:DBpia|Korea Science|KCI|RISS|earticle|KISS|ScienceON)[^|]*|"
    r"-\s*(?:논문|학위논문|국내학술지논문)\s*)$", re.I)


_VENUE_TAIL = re.compile(
    r"\s-\s([^-|]*?(?:학회|학술|논문|저널|Journal|연구)[^-|]*?)"
    r"(?:\s*:\s*논문|\s*-\s*[^-|]*)?\s*(?:\|.*)?$")


def _venue_split(title: str, url: str) -> tuple[str, str]:
    """(title, venue) of a database's result title that names the venue after it:
    「제목 - 한국통신학회지(정보와통신) : 논문」, 「제목 - 한국정보처리학회 학술대회논문집 - …」.
    Only on the paper databases, where the tail is theirs and not the paper's subtitle."""
    if _database(url) < 0 or not (m := _VENUE_TAIL.search(title)):
        return title, ""
    venue = re.sub(r"\s*:?\s*논문(?:지)?(?:\.{2,}|…)$", "", m.group(1).strip())
    venue = re.sub(r"\s*:\s*논문$", "", venue).strip(" .…")
    return title[: m.start()], venue if len(venue) >= 4 else ""


def clean_title(title: str, url: str = "") -> str:
    """A database's search-result title as the paper's: no 「[논문]」, no 「- 논문 | DBpia」,
    no 「- 학술지명 : 논문」, no 「= English title」 (RISS), no trailing 「....」."""
    t = re.sub(r"\s+", " ", title or "").strip()
    t = re.sub(r"^\[(?:논문|학위논문|보고서|학술대회)\]\s*", "", t)
    for _ in range(2):
        t = _TITLE_TAIL.sub("", t)
    t = _venue_split(t, url)[0]
    if " = " in t and _hangul(t.split(" = ")[0]):
        t = t.split(" = ")[0]
    return t.rstrip(" .…").strip()


def _work_key(title: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", clean_title(title).lower())


def _same_work(a: str, b: str) -> bool:
    if not a or not b:
        return False
    short, long_ = sorted((a, b), key=len)
    # A title the engine cut off is the start of the whole one.
    return short == long_ or (len(short) >= 12 and long_.startswith(short))


def _covers(text: str, word: str) -> bool:
    text = text.lower()
    word = word.lower()
    return word in text or (len(word) >= 4 and word[:-1] in text)


def _usable(hit: dict[str, Any]) -> bool:
    """A hit that may be a Korean paper: one paper's page on a paper database, or a
    scholarly page (credibility tier 3) with a Korean title."""
    url, title = str(hit.get("url") or ""), str(hit.get("title") or "")
    if not url or not title or title.startswith(("http://", "https://")) or url in title \
            or _NOT_A_PAPER.search(title):
        return False
    if _database(url) >= 0:
        return bool(_ARTICLE_PAGE.search(url))
    # Elsewhere (a university repository, a society's site) only what reads as a paper.
    return (credibility.tier(url) >= 3 and _hangul(title) and " | " not in title
            and not url.lower().endswith(".pdf") and bool(_PAPER_TITLE.search(title)))


def merge(batches: list[list[dict[str, Any]]], subject: str) -> list[dict[str, Any]]:
    """The hits of every variant as one list: papers only, each work once (the copy on the
    better database kept), ranked by subject words matched, paper database, and how many
    variants found it."""
    words = subject.split()
    works: list[dict[str, Any]] = []
    for batch in batches:
        seen_here: set[int] = set()
        for raw in batch:
            if not isinstance(raw, dict) or not _usable(raw):
                continue
            title = clean_title(str(raw["title"]), str(raw["url"]))
            if len(title) < 4:
                continue
            hit = {**raw, "title": title, "lane": LANE}
            venue = _venue_split(_TITLE_TAIL.sub("", str(raw["title"]).strip()), raw["url"])[1]
            if not hit.get("journal") and venue:
                hit["journal"] = venue
            key = _work_key(title)
            match = next((i for i, w in enumerate(works)
                          if w["hit"]["url"] == hit["url"] or _same_work(w["key"], key)), None)
            if match is None:
                works.append({"hit": hit, "key": key, "found": 1})
                seen_here.add(len(works) - 1)
                continue
            work = works[match]
            if match not in seen_here:
                work["found"] += 1
                seen_here.add(match)
            old = work["hit"]
            rank_new, rank_old = _database(hit["url"]), _database(old["url"])
            better = rank_new >= 0 and (rank_old < 0 or rank_new < rank_old)
            keep, other = (hit, old) if better else (old, hit)
            # The whole title over one the engine cut off; fields one copy lacks from the other.
            if len(other["title"]) > len(keep["title"]) and _same_work(
                    _work_key(other["title"]), _work_key(keep["title"])):
                keep = {**keep, "title": other["title"]}
            work["hit"] = {**{k: v for k, v in other.items() if v}, **{
                k: v for k, v in keep.items() if v}, "url": keep["url"], "title": keep["title"]}
            work["key"] = _work_key(work["hit"]["title"])

    def rank(work: dict[str, Any]) -> tuple[int, int, int]:
        hit = work["hit"]
        text = f"{hit['title']} {hit.get('snippet') or ''}"
        covered = sum(1 for w in words if _covers(text, w))
        return (-covered, 0 if _database(hit["url"]) >= 0 else 1, -work["found"])

    ranked = [w for w in works if not words or any(
        _covers(f"{w['hit']['title']} {w['hit'].get('snippet') or ''}", word) for word in words)]
    ranked.sort(key=rank)
    return [w["hit"] for w in ranked]


# ── landing pages ─────────────────────────────────────────────────────


class _Meta(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag != "meta":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        name = (a.get("name") or a.get("property") or "").strip().lower()
        content = re.sub(r"\s+", " ", a.get("content") or "").strip()
        if name and content:
            self.meta.setdefault(name, []).append(content)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def _korean_side(value: str) -> str:
    """「Korean Journal of Sociology (한국사회학)」 → 「한국사회학」;
    「지능정보연구 = Journal of …」 → 「지능정보연구」; other values unchanged."""
    value = value.strip()
    if m := re.fullmatch(r"([^가-힣]+?)\s*\(([^()]*[가-힣][^()]*)\)", value):
        return m.group(2).strip()
    if " = " in value and _hangul(left := value.split(" = ")[0]):
        return left.strip()
    return value


def _year(value: str) -> str:
    m = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", value or "")
    return m.group(1) if m else ""


def _doi(value: str) -> str:
    m = re.search(r"10\.\d{4,9}/[^\s\"'<>]{1,160}", value or "")
    return m.group(0).rstrip(".,;") if m else ""


def parse_meta(html: str) -> dict[str, Any]:
    """The bibliographic fields a landing page's meta tags give (Highwire `citation_*`,
    Dublin Core `DC.*`/`dcterms.*`, PRISM, and DBpia's 「저자; 저자 | 학술지 | 2024.3」
    `og:description`). Only what the page says; empty fields left out."""
    parser = _Meta()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - a broken page is a page without tags
        return {}
    meta = parser.meta

    def first(*names: str) -> str:
        for name in names:
            for value in meta.get(name, []):
                if value.strip():
                    return value.strip()
        return ""

    out: dict[str, Any] = {}
    if title := first("citation_title", "dc.title", "dcterms.title", "prism.title"):
        out["title"] = clean_title(_korean_side(title))
    names: list[str] = []
    for value in meta.get("citation_author") or meta.get("dc.creator") or meta.get(
            "dcterms.creator") or []:
        # koreascience puts them in one tag: 「A;B;C;」.
        parts = [n.strip() for n in value.split(";") if n.strip()]
        if len(parts) == 1 and re.fullmatch(r"[가-힣]{2,4}(?:\s+[가-힣]{2,4})+", parts[0]):
            parts = parts[0].split()  # RISS: 「박규태 이현승 임성주」 in one tag
        names.extend(parts)
    authors = []
    for name in names:
        name = re.sub(r"\s*\((?![^)]*[가-힣])[^)]*\)\s*$", "", _korean_side(name)).strip()
        if name and name not in authors:
            authors.append(name)
    year = _year(first("citation_publication_date", "citation_date", "citation_year",
                       "citation_online_date", "dc.date.issued", "dcterms.issued", "dc.date",
                       "dcterms.date", "prism.publicationdate"))
    journal = first("citation_journal_title", "citation_conference_title",
                    "citation_inbook_title", "prism.publicationname", "dc.source",
                    "dc.relation.ispartof", "dcterms.ispartof")
    doi = _doi(first("citation_doi", "prism.doi", "dc.identifier.doi")) or next(
        (d for v in meta.get("dc.identifier", []) + meta.get("dcterms.identifier", [])
         if (d := _doi(v))), "")
    if not (authors and journal and year) and (og := first("og:description")):
        # DBpia: 「이용준; 강장묵; 김지효 | 한국산학기술학회 논문지 | 2024.3」.
        parts = [p.strip() for p in og.split(" | ")]
        if len(parts) == 3 and _year(parts[2]) and len(parts[0]) < 200:
            authors = authors or [n.strip() for n in parts[0].split(";") if n.strip()]
            journal = journal or parts[1]
            year = year or _year(parts[2])
    if authors:
        out["authors"] = authors
    if year:
        out["published"] = year
    if journal:
        out["journal"] = _korean_side(journal)
    elif publisher := first("citation_publisher", "dc.publisher"):
        out["journal"] = _korean_side(publisher)
    for key, tag in (("volume", "citation_volume"), ("issue", "citation_issue")):
        if (value := first(tag, f"prism.{key}")) and len(value) <= 20:
            out[key] = value
    if doi:
        out["doi"] = doi
    if abstract := first("citation_abstract", "dc.description", "dcterms.abstract"):
        out["snippet"] = abstract[:600]
    return out


def enrich(hit: dict[str, Any], html: str) -> dict[str, Any]:
    """`hit` with what its landing page's meta tags say. The search title stays when the
    page gives only an English one for a Korean title, or when the page's title is not
    this work's."""
    found = parse_meta(html)
    if not found:
        return hit
    out = {**hit, **{k: v for k, v in found.items() if k not in ("title", "snippet")}}
    title = found.get("title") or ""
    if title and (_hangul(title) or not _hangul(hit["title"])):
        out["title"] = title
    if found.get("snippet") and len(found["snippet"]) > len(str(hit.get("snippet") or "")):
        out["snippet"] = found["snippet"]
    return out


# ── search ────────────────────────────────────────────────────────────


async def search(query: str, count: int = 6) -> list[dict[str, Any]]:
    """Korean papers on `query`'s subject: every variant searched at once, merged, the top
    ones' landing pages read for their meta tags. `[]` on any failure; never raises."""
    from app.services.tools.builtin import scrape_html, searxng

    subject = query_for(query) or (query or "").strip()
    asked = variants(subject)
    if not asked:
        return []
    backends = await settings_store.tools_config()
    if not backends.search:
        return []
    batches = await asyncio.gather(
        *(searxng(backends.search, q, 8, hints={"site": site} if site else None)
          for q, site in asked),
        return_exceptions=True,
    )
    failed = [b for b in batches if isinstance(b, BaseException)]
    if failed:
        log.info("Korean paper search: %d of %d variants failed (%s)", len(failed),
                 len(batches), type(failed[0]).__name__)
    hits = merge([b for b in batches if isinstance(b, list)], subject)[:count]
    if not hits or not backends.fetch:
        return hits
    pages = await asyncio.gather(
        *(scrape_html(backends.fetch, h["url"]) for h in hits[:_ENRICH]),
        return_exceptions=True,
    )
    for i, page in enumerate(pages):
        if isinstance(page, str) and page:
            hits[i] = enrich(hits[i], page)
    return hits


__all__ = ["LANE", "SITES", "clean_title", "enrich", "merge", "parse_meta", "query_for",
           "search", "terms", "variants", "wanted"]
