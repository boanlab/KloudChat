"""Model checks for `verify`: claims checked against the web, assessment items solved."""

from __future__ import annotations

import asyncio
import json
import logging
import re

from app.services import research, settings_store
from app.services.verify import Check, Doc, Finding, model_check

log = logging.getLogger(__name__)

MAX_CLAIMS = 12
MAX_QUESTIONS = 12
_EXCERPT = 2500

_PICK_PROMPT = """아래는 보고서의 문장 목록이다. 틀리면 독자가 잘못 가르치거나 잘못 판단하게 되는,
웹에서 확인할 수 있는 사실 주장이 든 문장을 최대 {n}개 골라라.

고를 것: 과학 개념의 정의·화학식·원자 수, 실험 안전 절차, 출처가 있는 통계 수치와 그 대상,
제품·서비스·제도의 현재 상태.
고르지 말 것: 의견, 계획, 이 문서가 스스로 정한 가정과 그 계산.

문장마다 그 사실을 확인할 한국어 검색어(핵심어 3~6개)를 붙여라.

문장:
{sentences}

JSON 배열 하나만 답하라. 예: [{{"i": 3, "query": "염화나트륨 이온 결합 분자 아님"}}]"""

_JUDGE_PROMPT = """아래 근거 자료에 비추어 문장의 사실 주장을 판정하라.

문장: {claim}

근거 자료:
{evidence}

먼저 근거가 문장과 같은 대상(같은 개념·통계·제품·회사)을 다루는지 판단하라. 다른 대상이면
「확인 불가」다. 같은 대상인데 분명히 어긋나면 「틀림」, 뒷받침하면 「맞음」.
「틀림」이면 어긋남을 보여 주는 근거 본문의 구절을 고치지 말고 그대로 옮겨라(20~120자).
회사·제품의 옛 이름과 새 이름, 반올림 차이, 표현 차이는 어긋남이 아니다.

JSON 하나만 답하라:
{{"same_subject": true, "verdict": "맞음|틀림|확인 불가", "quote": "근거 구절",
  "why": "한 문장", "source": "A"}}"""

_QUESTION_PROMPT = """아래는 중고등학교 평가 문항이다. 정답을 보지 않은 것처럼 먼저 직접 풀고 나서
검토하라.

문제로 삼을 것은 다음 넷뿐이다.
1. 표시된 정답이 틀렸다.
2. 표시된 정답 말고 다른 보기도 맞다(정답이 하나여야 하는 문항).
3. 문항·보기·해설·채점 기준에 과학 사실 오류가 있다(교사가 그대로 가르치면 오개념이 된다).
4. 채점 기준의 점수 구간이 겹쳐 같은 답이 두 점수를 받을 수 있다.

보기·정답·해설이 빠진 것, 표현을 더 다듬을 수 있는 것, 사소한 용어 선택은 문제로 삼지 마라.

문항:
{item}

JSON 하나만 답하라: {{"ok": true}} 또는
{{"ok": false, "kind": 1, "problem": "무엇이 어떻게 틀렸고 어떻게 고쳐야 하는지"}}"""


def _complete():
    from app.services import report

    return report._complete


def _json(text: str, opening: str) -> object:
    closing = "]" if opening == "[" else "}"
    start, end = (text or "").find(opening), (text or "").rfind(closing)
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _prose(doc: Doc) -> list[tuple[int, str]]:
    from app.services import report

    out = []
    for index, sec in enumerate(doc.sections):
        if report._REFERENCE_HEADING.search(str(sec.get("heading") or "")):
            continue
        for line in str(sec.get("content") or "").split("\n"):
            if line.lstrip().startswith(("|", "!", "```", "#")):
                continue
            for m in report._SENTENCE.finditer(line + " "):
                sentence = m.group(0).strip()
                if 15 <= len(sentence) <= 400:
                    out.append((index, sentence))
    return out


async def _evidence(query: str) -> list[dict]:
    """The two best-ranked pages for `query`, fetched; no model call."""
    backends = await settings_store.tools_config()
    if not backends.search:
        return []
    hits = await research._gather(backends.search, query)
    ranked = sorted(
        (h for h in hits if h.get("url") and not research._rejected(h["url"])),
        key=lambda h: research.score(query, h), reverse=True,
    )[:2]
    bodies = await asyncio.gather(
        *(research.scrape(backends.fetch, h["url"]) for h in ranked), return_exceptions=True
    )
    pages = []
    for hit, body in zip(ranked, bodies, strict=True):
        text = research.page_text(body) if isinstance(body, str) else ""
        if text or hit.get("snippet"):
            pages.append({"title": hit.get("title", ""), "url": hit["url"],
                          "text": (text or hit.get("snippet", ""))[:_EXCERPT]})
    return pages


def web_facts(judge: str, api_key: str) -> Check:
    """Up to a dozen checkable claims, each searched and judged against what it finds."""

    @model_check
    async def check(doc: Doc):
        complete = _complete()
        usage = {"inputTokens": 0, "outputTokens": 0}
        prose = _prose(doc)
        if not prose or not await research.available():
            return [], usage
        listed = "\n".join(f"{n}. {s}" for n, (_, s) in enumerate(prose[:160]))
        text, spent = await complete(
            judge,
            [{"role": "user", "content": _PICK_PROMPT.format(n=MAX_CLAIMS, sentences=listed)}],
            api_key, 1200,
        )
        for key in usage:
            usage[key] += spent.get(key, 0)
        picked = _json(text, "[")
        claims = []
        for item in picked if isinstance(picked, list) else []:
            try:
                n, query = int(item["i"]), str(item["query"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= n < len(prose) and query:
                claims.append((prose[n], query))
        gate = asyncio.Semaphore(4)

        async def one(claim: tuple[int, str], query: str):
            async with gate:
                pages = await _evidence(query)
                if not pages:
                    return None
                evidence = "\n\n".join(
                    f"[{'AB'[k]}] {p['title']} ({p['url']})\n{p['text']}"
                    for k, p in enumerate(pages)
                )
                verdict, used = await complete(
                    judge, [{"role": "user", "content": _JUDGE_PROMPT.format(
                        claim=claim[1], evidence=evidence)}], api_key, 300,
                )
                return claim, pages, verdict, used

        results = await asyncio.gather(*(one(c, q) for c, q in claims[:MAX_CLAIMS]),
                                       return_exceptions=True)
        found = []
        for result in results:
            if not result or isinstance(result, BaseException):
                continue
            (index, sentence), pages, verdict, used = result
            for key in usage:
                usage[key] += used.get(key, 0)
            judged = _json(verdict, "{")
            if not isinstance(judged, dict) or str(judged.get("verdict", "")).strip() != "틀림":
                continue
            letter = str(judged.get("source") or "A").strip()[:1]
            page = pages["AB".index(letter)] if letter in "AB"[: len(pages)] else pages[0]
            # Only on a page about the same subject that really holds the quoted words;
            # a judge reading an unrelated page says 「틀림」 too.
            quote = re.sub(r"\s+", "", str(judged.get("quote") or ""))
            if judged.get("same_subject") is not True or len(quote) < 8 or quote not in re.sub(
                r"\s+", "", page["text"]
            ):
                continue
            found.append(Finding(
                index, sentence, "fact",
                f"웹 자료와 어긋난다: {str(judged.get('why') or '').strip()} "
                f"(근거: {page['title']}, {page['url']})",
                evidence=page["text"][:1200], rank=1, auto=False,
            ))
        if found:
            log.info("web check found %d of %d claims wrong", len(found), len(claims))
        return found, usage

    return check


def _items(doc: Doc) -> list[tuple[int, str]]:
    from app.services import report

    out = []
    for index, sec in enumerate(doc.sections):
        content = str(sec.get("content") or "")
        lines = content.split("\n")
        starts = report._question_lines(content)
        for k, start in enumerate(starts):
            end = starts[k + 1] if k + 1 < len(starts) else min(len(lines), start + 14)
            block = "\n".join(lines[start:end]).strip()
            if block:
                out.append((index, block))
        # Each row of a question table (header names 문항 and 정답) is an item; a lesson
        # plan's table is not.
        header = ""
        for line in lines:
            if not line.lstrip().startswith("|"):
                header = ""
                continue
            if not header:
                header = line
                continue
            if re.match(r"^\s*\|?\s*:?-{2,}", line):
                continue
            if re.search(r"문항|문제", header) and re.search(r"정답|답", header):
                out.append((index, line.strip()))
    return out


def solve_questions(judge: str, api_key: str) -> Check:
    """Each assessment item solved by the judge: one right answer, no science error."""

    @model_check
    async def check(doc: Doc):
        complete = _complete()
        usage = {"inputTokens": 0, "outputTokens": 0}
        items = _items(doc)[:MAX_QUESTIONS]
        gate = asyncio.Semaphore(6)

        async def one(item: str):
            async with gate:
                return await complete(
                    judge, [{"role": "user", "content": _QUESTION_PROMPT.format(item=item[:2500])}],
                    api_key, 400,
                )

        replies = await asyncio.gather(*(one(b) for _, b in items), return_exceptions=True)
        found = []
        for (index, block), reply in zip(items, replies, strict=True):
            if isinstance(reply, BaseException):
                continue
            text, used = reply
            for key in usage:
                usage[key] += used.get(key, 0)
            judged = _json(text, "{")
            if isinstance(judged, dict) and judged.get("ok") is False and judged.get("problem"):
                # A wrong key or second right answer is repaired; a science or scoring
                # judgement is only shown.
                kind = str(judged.get("kind") or "")
                found.append(Finding(index, block, "question",
                                     f"평가 문항 검토: {str(judged['problem']).strip()}", rank=1,
                                     auto=kind in ("1", "2")))
        if found:
            log.info("question check found %d of %d items wrong", len(found), len(items))
        return found, usage

    return check
