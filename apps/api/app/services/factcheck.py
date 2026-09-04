"""Checks one passage's factual claims against SearXNG results.

Verdicts: `supported` and `unsupported` require a cited source; `uncertain`
is the default and where every failure lands.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

import httpx

from app.core.config import settings
from app.services import settings_store

log = logging.getLogger(__name__)

#: Claims per passage; each costs one search plus one judgement.
MAX_CLAIMS = 4
#: Search results the judge reads.
_RESULTS = 5
_TIMEOUT = 30.0

_EXTRACT_PROMPT = """다음 슬라이드에서 **사실 확인이 가능한 주장**만 뽑아라.

뽑을 것: 수치, 연도, 순위, 점유율, 고유명사가 들어간 단정. 검색으로 맞는지
확인할 수 있는 문장.

뽑지 말 것:
- 의견·평가·전망 ("우리 방식이 더 낫다", "앞으로 중요해질 것이다")
- 이 발표가 하려는 주장 자체
- 확인할 대상이 없는 일반론

최대 {limit}개. 없으면 빈 배열.
각 항목은 슬라이드에 적힌 그대로가 아니라, **혼자 읽어도 뜻이 통하는 한 문장**으로.

JSON 배열로만 답하라.
예: ["2024년 국내 전기차 등록대수는 60만 대를 넘었다"]

슬라이드 제목: {title}
슬라이드 내용:
{body}"""

_JUDGE_PROMPT = """아래 주장이 검색 결과로 뒷받침되는지 판정하라.

주장: {claim}

검색 결과:
{evidence}

규칙:
- 검색 결과가 주장을 **직접** 뒷받침할 때만 "supported". 비슷한 얘기가 있는
  정도로는 안 된다.
- 검색 결과가 주장과 어긋나거나, 찾았는데 뒷받침하는 것이 없으면 "unsupported".
- 결과가 부족하거나 판단이 갈리면 "uncertain". **애매하면 uncertain 이다.**
- note 는 한 문장. 무엇을 근거로 그렇게 판정했는지, 또는 무엇이 부족한지.
- source 는 근거가 된 결과의 번호([1] 같은). **supported 나 unsupported 로
  판정하려면 반드시 그 근거가 된 번호를 적어라.** 지목할 것이 없으면 판정은
  uncertain 이고 source 는 0 이다.
- 주장이 널리 알려진 상식인데 검색 결과가 곁가지 논쟁만 보여 준다면, 그것은
  반증이 아니다. uncertain 으로 두어라.

JSON 객체로만 답하라.
예: {{"verdict": "uncertain", "note": "2023년 수치만 확인됐고 2024년 자료는 없다", "source": 2}}"""


def _json_block(text: str, opener: str, closer: str) -> Any:
    match = re.search(rf"\{opener}.*\{closer}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _spend() -> dict[str, int]:
    return {"inputTokens": 0, "outputTokens": 0}


def _add(total: dict[str, int], spent: dict[str, int]) -> None:
    total["inputTokens"] += spent["inputTokens"]
    total["outputTokens"] += spent["outputTokens"]


async def _complete(
    model: str, prompt: str, api_key: str, max_tokens: int
) -> tuple[str, dict[str, int]]:
    base, _ = await settings_store.litellm_config()
    async with httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        payload = response.json()
        raw = payload.get("usage") or {}
        return (payload["choices"][0]["message"]["content"] or "").strip(), {
            "inputTokens": int(raw.get("prompt_tokens") or 0),
            "outputTokens": int(raw.get("completion_tokens") or 0),
        }


async def _search(query: str) -> list[dict[str, str]]:
    """SearXNG, same instance the `web_search` tool uses. `[]` on any failure."""
    backends = await settings_store.tools_config()
    if not backends.search:
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(
                f"{backends.search.rstrip('/')}/search",
                params={"q": query, "format": "json", "language": "ko"},
            )
            response.raise_for_status()
            results = (response.json() or {}).get("results") or []
    except (httpx.HTTPError, ValueError) as exc:
        log.info("factcheck search failed: %s", exc)
        return []
    return [
        {
            "title": str(r.get("title") or "")[:200],
            "url": str(r.get("url") or ""),
            "snippet": str(r.get("content") or "")[:500],
        }
        for r in results[:_RESULTS]
        if r.get("url")
    ]


async def available() -> bool:
    return bool((await settings_store.tools_config()).search)


def slide_text(slide: dict) -> str:
    parts = [*(slide.get("bullets") or [])]
    if slide.get("body"):
        parts.append(str(slide["body"]))
    return "\n".join(parts)


async def check_slide(*, slide: dict, model: str, api_key: str) -> tuple[dict, dict[str, int]]:
    """One slide's claims. Thin wrapper over `check_text`."""
    return await check_text(
        title=str(slide.get("title") or ""),
        body=slide_text(slide),
        model=model,
        api_key=api_key,
    )


async def check_text(
    *, title: str, body: str, model: str, api_key: str, limit: int = MAX_CLAIMS
) -> tuple[dict, dict[str, int]]:
    """`(factCheck, usage)` for one passage; always `done`, possibly with no claims."""
    usage = _spend()
    if not body.strip():
        return {"status": "done", "claims": []}, usage

    try:
        raw, spent = await _complete(
            model,
            _EXTRACT_PROMPT.format(limit=limit, title=title, body=body[:2000]),
            api_key,
            400,
        )
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.warning("claim extraction failed: %s", exc)
        return {"status": "done", "claims": []}, usage
    _add(usage, spent)

    items = _json_block(raw, "[", "]")
    claims = [str(c).strip() for c in items if str(c).strip()] if isinstance(items, list) else []
    if not claims:
        return {"status": "done", "claims": []}, usage

    judged = await asyncio.gather(
        *(_judge(c, model, api_key) for c in claims[:limit]), return_exceptions=True
    )
    out = []
    for claim, outcome in zip(claims[:limit], judged, strict=False):
        if isinstance(outcome, BaseException):
            log.info("factcheck judge crashed: %s", outcome)
            result = {
                "verdict": "uncertain",
                "note": "확인하지 못했습니다.",
                "sourceUrl": "",
            }
        else:
            result, spent = outcome
            _add(usage, spent)
        out.append({"id": f"c_{uuid.uuid4().hex[:8]}", "text": claim, **result})
    return {"status": "done", "claims": out}, usage


async def _judge(claim: str, model: str, api_key: str) -> tuple[dict, dict[str, int]]:
    """One claim → `(verdict, usage)`. Every failure path returns `uncertain`."""
    hits = await _search(claim)
    if not hits:
        return {
            "verdict": "uncertain",
            "note": "검색 결과를 얻지 못했습니다. 직접 확인이 필요합니다.",
            "sourceUrl": "",
        }, _spend()

    evidence = "\n\n".join(
        f"[{i + 1}] {h['title']}\n{h['url']}\n{h['snippet']}" for i, h in enumerate(hits)
    )
    try:
        raw, spent = await _complete(
            model, _JUDGE_PROMPT.format(claim=claim, evidence=evidence), api_key, 300
        )
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.info("factcheck judge failed: %s", exc)
        return (
            {"verdict": "uncertain", "note": "판정하지 못했습니다.", "sourceUrl": ""},
            _spend(),
        )

    data = _json_block(raw, "{", "}")
    if not isinstance(data, dict):
        return (
            {"verdict": "uncertain", "note": "판정을 읽지 못했습니다.", "sourceUrl": ""},
            spent,
        )

    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in ("supported", "unsupported", "uncertain"):
        verdict = "uncertain"

    index = data.get("source")
    url = ""
    if isinstance(index, (int, float)) and 1 <= int(index) <= len(hits):
        url = hits[int(index) - 1]["url"]

    # A confident verdict without a citable source collapses to uncertain.
    if verdict in ("supported", "unsupported") and not url:
        verdict = "uncertain"
        note_suffix = " (근거로 지목된 자료가 없어 판정을 보류합니다)"
    else:
        note_suffix = ""

    return {
        "verdict": verdict,
        "note": (str(data.get("note") or "").strip() + note_suffix)[:300],
        "sourceUrl": url,
    }, spent


__all__ = ["MAX_CLAIMS", "available", "check_slide", "slide_text"]
