"""How far a source can be trusted as a citation, by its host. Tier 0 is never cited.

Tiers:

- 3 — official and scholarly: government and public bodies, statistics, law, standards,
  DOI-registered papers, journal and university publishers, preprint servers, academic
  databases (KCI, RISS, DBpia).
- 2 — news of record and reference works: wire services, national newspapers and
  broadcasters, encyclopedias edited by institutions.
- 1 — anything else not known to be weak: company and product documentation, Wikipedia,
  author uploads.
- 0 — weak: personal blogs, Q&A and content sites, user-edited wikis, essay mills.
"""

from __future__ import annotations

import re

_OFFICIAL = (
    ".go.kr", ".gov", ".gov.uk", ".gouv.fr", ".go.jp", ".int", ".mil", ".re.kr",
    "korea.kr", "kostat.go.kr", "law.go.kr", "assembly.go.kr", "bok.or.kr", "kisa.or.kr",
    "kdi.re.kr", "oecd.org", "worldbank.org", "imf.org", "un.org", "who.int", "europa.eu",
    "iso.org", "ietf.org", "rfc-editor.org", "w3.org", "nist.gov",
)
_SCHOLARLY = (
    "doi.org", "arxiv.org", "biorxiv.org", "medrxiv.org", "ssrn.com", "aclanthology.org",
    "openreview.net", "dl.acm.org", "ieeexplore.ieee.org", "usenix.org", "neurips.cc",
    "mlr.press", "semanticscholar.org", "openalex.org", "pubmed.", "ncbi.nlm.nih.gov",
    "springer.com", "link.springer.com", "sciencedirect.com", "elsevier.com", "wiley.com",
    "tandfonline.com", "sagepub.com", "jstor.org", "muse.jhu.edu", "cambridge.org",
    "oup.com", "academic.oup.com", "nature.com", "science.org", "pnas.org", "plos.org",
    "mdpi.com", "frontiersin.org", "dukeupress.edu", "press.uchicago.edu", "kci.go.kr",
    "riss.kr", "dbpia.co.kr", "scienceon.kisti.re.kr", "kiss.kstudy.com", "s-space.snu.ac.kr",
    ".ac.kr", ".edu", ".ac.uk", ".ac.jp",
)
_PRESS = (
    "yna.co.kr", "yonhapnews", "hani.co.kr", "khan.co.kr", "chosun.com", "donga.com",
    "joongang.co.kr", "hankyung.com", "mk.co.kr", "sedaily.com", "kbs.co.kr", "mbc.co.kr",
    "sbs.co.kr", "ytn.co.kr", "jtbc.co.kr", "news1.kr", "newsis.com", "etnews.com",
    "zdnet.co.kr", "bbc.co.uk", "bbc.com", "reuters.com", "apnews.com", "nytimes.com",
    "wsj.com", "ft.com", "economist.com", "bloomberg.com", "theguardian.com",
    "washingtonpost.com", "nhk.or.jp", "encykorea.aks.ac.kr", "britannica.com",
    "terms.naver.com", "stdict.korean.go.kr",
)
_WEAK = (
    "blog.naver.com", "m.blog.naver.com", "tistory.com", "brunch.co.kr", "velog.io",
    "medium.com", "blog.daum.net", "cafe.naver.com", "cafe.daum.net", "kin.naver.com",
    "namu.wiki", "quora.com", "zhihu.com", "wikihow.com", "educba.com", "study.com",
    "chegg.com", "brainly.", "answers.com", "ehow.com", "hubpages.com", "substack.com",
    "wordpress.com", "blogspot.com", "steemit.com", "happycampus.com", "reportworld.co.kr",
    "allreport.co.kr", "egloos.com", "postype.com", "dcinside.com", "fmkorea.com",
    "theqoo.net", "clien.net", "ppomppu.co.kr", "prezi.com", "oocities.org", "geocities.",
    "factually.co", "theacademic.in", "ijcrt.org", "ijsr.net", "ijert.org", "scribd.com",
)


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", (url or "").strip().lower()).split("/")[0]


def _matches(host: str, marks: tuple[str, ...]) -> bool:
    for mark in marks:
        if mark.startswith("."):
            if host.endswith(mark) or f"{mark}." in f"{host}.":
                return True
        elif host == mark or host.endswith("." + mark) or mark in host:
            return True
    return False


def tier(url: str) -> int:
    """0 (weak) to 3 (official or scholarly); unknown hosts are 1."""
    host = _host(url)
    if not host:
        return 1
    if _matches(host, _WEAK):
        return 0
    if _matches(host, _OFFICIAL) or _matches(host, _SCHOLARLY):
        return 3
    if _matches(host, _PRESS):
        return 2
    return 1


LABELS = {3: "공식·학술", 2: "언론·사전", 1: "일반", 0: "저신뢰"}
