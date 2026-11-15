"""A paper's title, authors, year and arXiv id read from its extracted first page.

A project PDF reaches the report as its file name (「2309.06180.pdf」); a reference entry needs
the work itself. The stored text begins 「[페이지 1]」 (`files._from_pdf`) with the title lines,
then the author line(s), affiliations and e-mails, then 「Abstract」; arXiv stamps its margin
「arXiv:2309.06180v1 [cs.LG] 12 Sep 2023」 somewhere on that page. Pure text reading: nothing
here guesses — a field it cannot read stays empty.
"""

from __future__ import annotations

import re

_PAGE = re.compile(r"^\s*\[(?:페이지|page)\s*(\d+)\]\s*$", re.I | re.M)
_ARXIV_STAMP = re.compile(
    r"arXiv:\s?(?P<id>\d{4}\.\d{4,5})(?:v\d{1,2})?\s{0,3}(?:\[[^\]\n]{1,20}\])?"
    r"(?:\s{0,3}\d{1,2}\s[A-Z][a-z]{2}\s(?P<year>(?:19|20)\d{2}))?")
_ARXIV_NAME = re.compile(r"^(?P<id>\d{4}\.\d{4,5})(?:v\d{1,2})?(?:\.pdf)?$", re.I)
#: lines above or among the title that are no part of it
_NOISE = re.compile(
    r"^(?:Published|Accepted|Preprint|Under\s(?:review|submission)|To\sappear|Proceedings|In\s"
    r"Proceedings|Workshop|Technical\sReport|Copyright|©|arXiv|Content\sWarning|Draft|"
    r"Journal\sof|Vol\.|Volume)\b|^\d{1,4}$|^https?://", re.I)
_ABSTRACT = re.compile(r"^(?:Abstract|ABSTRACT|A\sB\sS\sT\sR\sA\sC\sT)\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|\{[^}]{2,200}\}@")
_AFFILIATION = re.compile(
    r"Universit|Institut|College|School|Department|Dept\.|Laborator|\bLab\b|Research\b|Inc\.|"
    r"Corp|Company|GmbH|Ltd|Center|Centre|Academy|Google|Microsoft|Meta\b|DeepMind|NVIDIA|"
    r"Amazon|Alibaba|Tencent|Huawei|IBM|\bUC\s|\bUSA\b|China\b|Korea\b|Japan\b|Germany\b",
    re.I)
#: an author's superscript marks: 「Kwon1,∗」, 「Zhang1」, 「Greshake∗」, 「Leviathan * 1」
_MARKS = re.compile(r"(?<=[A-Za-zÀ-ÿ.])\s?(?:[\d∗*†‡§¶♠♣♦♥⋆]+(?:\s?,\s?[\d∗*†‡§¶♠♣♦♥⋆]+)*)"
                    r"(?:\s(?:[\d∗*†‡§¶]+\s?)+)?")
_NAME_WORD = r"(?:[A-ZÀ-Þ][a-zà-ÿ'’-]+|[A-Z]\.|[A-Z][a-zà-ÿ]*-[A-Z][a-zà-ÿ]+)"
_NAME = re.compile(rf"^{_NAME_WORD}(?:\s{_NAME_WORD}){{1,2}}$")
#: a title line the next one continues: it ends on a function word, a colon or a lowercase
#: word (「…for Goodput-optimized」 / 「Large Language Model Serving」)
_TITLE_GOES_ON = re.compile(
    r"(?:\b(?:for|of|with|and|the|to|in|on|via|from|a|an|using|by|at|towards?|through|into)"
    r"|[:–—-]|\b[a-z][a-z-]*)$")
_VENUE_LINES = (
    # 「SOSP ’23, October 23–26, 2023, Koblenz, Germany」, 「HotInfra'23, June 18, 2023」
    re.compile(r"^(?P<venue>[A-Z][A-Za-z]{2,15}\s?['’]\d{2}),\s[^\n]{0,60}?(?P<year>(?:19|20)\d{2})",
               re.M),
    # 「37th Conference on Neural Information Processing Systems (NeurIPS 2023).」
    re.compile(r"Conference[^\n()]{0,120}\((?P<venue>[A-Z][A-Za-z]{2,12})\s(?P<year>(?:19|20)\d{2})\)"),
)
#: 「Proceedings of the 40 th International Conference on Machine Learning, …, 2023」
_PROCEEDINGS = re.compile(r"Proceedings\sof\sthe\s(?P<body>[^,\n]{6,120}?)(?:,|$)")


def _first_page(text: str) -> str:
    body = text or ""
    marks = list(_PAGE.finditer(body))
    if marks:
        start = marks[0].end()
        end = marks[1].start() if len(marks) > 1 else len(body)
        body = body[start:end]
    return body[:8000]


def _names(line: str) -> list[str]:
    """The person names on an author line; [] when it is not one."""
    if _EMAIL.search(line) or _ABSTRACT.match(line) or ":" in line:
        return []
    marked = _MARKS.sub("|", line)
    parts = [p.strip(" ,;") for p in re.split(r"\||,|;|\band\b|\s{2,}", marked)]
    parts = [p for p in parts if p]
    names = [p for p in parts if _NAME.match(p) and not _AFFILIATION.search(p)]
    return names if names and len(names) == len(parts) else []


def read(text: str, name: str = "") -> dict[str, str]:
    """{"title", "author", "year", "arxiv", "venue"} of the paper whose extracted text is
    `text`; fields that cannot be read are left out."""
    page = _first_page(text)
    out: dict[str, str] = {}
    stamp = _ARXIV_STAMP.search(page) or _ARXIV_STAMP.search((text or "")[:20000])
    named = _ARXIV_NAME.match((name or "").strip())
    arxiv = stamp.group("id") if stamp else (named.group("id") if named else "")
    if arxiv:
        out["arxiv"] = arxiv
    lines = [" ".join(x.split()) for x in page.split("\n")]
    lines = [x for x in lines if x]
    title: list[str] = []
    authors: list[str] = []
    k = 0
    while k < len(lines) and _NOISE.match(lines[k]):
        k += 1
    while k < len(lines) and len(title) < 4:
        line = lines[k]
        goes_on = bool(title) and bool(_TITLE_GOES_ON.search(title[-1]))
        if _ABSTRACT.match(line) or _EMAIL.search(line) or (_names(line) and not goes_on) or (
                title and not goes_on and _AFFILIATION.search(line)):
            break
        if not _NOISE.match(line) and not _ARXIV_STAMP.search(line):
            title.append(line)
        k += 1
    # author lines until the abstract: names, then affiliations and e-mails between them
    previous = ""
    for line in lines[k:k + 40]:
        if _ABSTRACT.match(line):
            break
        # 「CISPA Helmholtz Center for / Information Security」: an affiliation running on
        if not re.search(r"\b(?:for|of|and|the|in|on|at)$", previous) and (
                found := _names(line)):
            authors += [n for n in found if n not in authors]
        previous = line
    if title and len(" ".join(title)) <= 300:
        out["title"] = re.sub(r"\s+", " ", " ".join(title)).strip(" .")
    if authors:
        shown = authors[:3]
        out["author"] = ", ".join(shown) + (" et al." if len(authors) > 3 else "")
    for pattern in _VENUE_LINES:
        if m := pattern.search(page):
            venue = m.group("venue")
            out["venue"] = venue if re.search(r"\d", venue) else f"{venue} {m.group('year')}"
            out["year"] = m.group("year")
            break
    if "venue" not in out and (m := _PROCEEDINGS.search(re.sub(r"\s+", " ", page))):
        body = re.sub(r"(\d)\s(st|nd|rd|th)\b", r"\1\2", m.group("body")).strip()
        out["venue"] = f"Proceedings of the {body}"
        if y := re.search(r"(?:19|20)\d{2}", page[page.find("Proceedings"):][:300]):
            out["year"] = y.group(0)
    if "year" not in out and arxiv:
        out["year"] = f"20{arxiv[:2]}"
    elif "year" not in out and stamp and stamp.group("year"):
        out["year"] = stamp.group("year")
    return out


def first_line_title(text: str) -> str:
    """The document's own first line as its title, page marks and noise aside."""
    for line in _first_page(text).split("\n"):
        line = " ".join(line.split())
        if 4 <= len(line) <= 160 and not _NOISE.match(line) and not _ARXIV_STAMP.search(line):
            return line.strip(" .")
    return ""


def source_fields(text: str, name: str = "") -> dict[str, str]:
    """A project paper's fields for its reference entry (`report_export.citation_text`): title,
    author, year, publisher (venue, else 「arXiv:<id>」) and the arXiv link; {} when the text
    gives no title of its own."""
    meta = read(text, name)
    title = meta.get("title") or ""
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", (name or "").strip()).lower()
    if not title or title.lower() == stem:
        return {}
    out = {"title": title}
    if meta.get("author"):
        out["author"] = meta["author"]
    if meta.get("year"):
        out["year"] = meta["year"]
    if meta.get("venue"):
        out["publisher"] = meta["venue"]
    elif meta.get("arxiv"):
        out["publisher"] = f"arXiv:{meta['arxiv']}"
    if meta.get("arxiv"):
        out["url"] = f"https://arxiv.org/abs/{meta['arxiv']}"
    return out


__all__ = ["first_line_title", "read", "source_fields"]
