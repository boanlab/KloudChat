#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mcp>=1.2.0",
#   "httpx>=0.27.0",
#   "psycopg[binary]>=3.2",
# ]
# ///
"""KloudChat smart_search MCP (stdio) — 정밀 문서 검색.

기본 file_search(rag_api dense-only) 위에 reformulation + multi-query + (LLM)
relevance evaluation 을 얹어 회수율/정밀도를 높인다. 부품은 전부 로컬:
  - reformulate / evaluate : local/gemma-4-26b (LiteLLM, 비용 0)
  - embed                  : bge-m3 (LiteLLM)
  - vector store           : vectordb pgvector (langchain_pg_embedding)

파이프라인: reformulation + multi-query + hybrid(dense<=> ⊕ sparse tsvector, RRF
융합) + **rerank(bge-reranker cross-encoder, RERANK_URL 설정 시)** + evaluation.
RERANK_URL 이 비면 rerank 단계만 무변형 통과한다(reranker 미배포 환경 = hybrid 까지).

테넌시 & 역할 분리
----------------
모든 쿼리에 `cmetadata->>'user_id' = LIBRECHAT_USER_ID` 를 강제한다. LibreChat 이
`{{LIBRECHAT_USER_ID}}` 를 호출자별로 치환(per-session stdio spawn, startup:false)하며,
USER_ID 가 비면 전역 검색 대신 **거부**한다(usage.py 와 동일 규율). 그래야 타
사용자의 업로드 문서가 새지 않는다.

**범위 = 사용자 본인 업로드 전용.** 에이전트 지식(`cmetadata.user_id`=agent id)은
native `file_search`(LibreChat 가 agent 의 file_ids 를 서버측 주입해 스코프)가 담당한다.
smart_search 가 에이전트 지식을 못 다루는 건 LibreChat MCP placeholder 가 agent id 를
노출하지 않기 때문(body placeholder 화이트리스트=conversationId/parentMessageId/
messageId). 자세한 근거는 docs/smart-search.md.
"""
from __future__ import annotations

import os
import re
import sys

import httpx
import psycopg
from mcp.server.fastmcp import FastMCP

LITELLM = os.environ.get("LITELLM_URL", "http://litellm:8000").rstrip("/")
KEY = os.environ.get("LITELLM_MASTER_KEY", "")
DB = os.environ.get("RAG_DB_URL", "")  # postgresql://user:pw@vectordb:5432/<db>
USER_ID = os.environ.get("LIBRECHAT_USER_ID", "").strip()
# eval 단계는 gemma 1콜 추가 — latency 민감하면 false 로 끄면 dense+reformulate 만.
DO_EVAL = os.environ.get("SMART_SEARCH_EVAL", "1") not in ("0", "false", "")
# bge-reranker cross-encoder endpoint (Phase 2). 비면 rerank 스킵(=Phase 1 동작 유지).
RERANK = os.environ.get("RERANK_URL", "").rstrip("/")

GEMMA = "local/gemma-4-26b"
EMBED = "bge-m3"

mcp = FastMCP("smart-search")


async def _chat(prompt: str, max_tokens: int = 256) -> str:
    """gemma 순수 생성(도구 없음 — ReAct/언어정책 함정 무관)."""
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            f"{LITELLM}/v1/chat/completions",
            headers={"Authorization": f"Bearer {KEY}"},
            json={"model": GEMMA, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": max_tokens, "temperature": 0.2},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"].get("content") or ""


async def _embed(texts: list[str]) -> list[list[float]]:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            f"{LITELLM}/v1/embeddings",
            headers={"Authorization": f"Bearer {KEY}"},
            json={"model": EMBED, "input": texts},
        )
        r.raise_for_status()
        return [d["embedding"] for d in r.json()["data"]]


async def _reformulate(query: str) -> list[str]:
    """질문 → 원질문 + 서브쿼리 3개 (multi-query)."""
    out = await _chat(
        "다음 질문을 벡터·키워드 검색에 적합한 서로 다른 한국어 검색 쿼리 3개로 분해해줘. "
        "한 줄에 하나씩, 번호·설명·따옴표 없이 쿼리 문장만 출력:\n\n" + query
    )
    subs = [ln.strip(" -•\t\"'") for ln in out.splitlines() if ln.strip()]
    # 원질문을 항상 포함, 중복 제거, 최대 4개
    seen, ordered = set(), []
    for q in [query, *subs]:
        if q and q not in seen:
            seen.add(q)
            ordered.append(q)
    return ordered[:4]


# Hybrid: dense(코사인 <=>) ⊕ sparse(tsvector ts_rank) 를 RRF(60) 로 융합.
# sparse 는 plainto_tsquery 의 AND(&)를 OR(|)로 치환해 다어절 서브쿼리의 recall 을
# 살린다(한 청크에 모든 토큰이 없어도 매칭 → ts_rank 로 정렬). dense 가 의미,
# sparse 가 정확 키워드를 담당. FTS 인덱스(ix_lpe_fts)는 _ensure_fts_index() 가 보장.
_HYBRID_SQL = """
WITH dense AS (
  SELECT uuid, document, cmetadata,
         row_number() OVER (ORDER BY embedding <=> %(vec)s::vector) AS rnk
  FROM langchain_pg_embedding
  WHERE cmetadata->>'user_id' = %(uid)s
  ORDER BY embedding <=> %(vec)s::vector
  LIMIT 50),
sparse AS (
  SELECT uuid, document, cmetadata,
         row_number() OVER (ORDER BY ts_rank(
           to_tsvector('simple', document),
           replace(plainto_tsquery('simple', %(q)s)::text, '&', '|')::tsquery) DESC) AS rnk
  FROM langchain_pg_embedding
  WHERE cmetadata->>'user_id' = %(uid)s
    AND to_tsvector('simple', document)
        @@ replace(plainto_tsquery('simple', %(q)s)::text, '&', '|')::tsquery
  ORDER BY rnk
  LIMIT 50)
SELECT document, cmetadata, sum(1.0/(60+rnk)) AS rrf
FROM (SELECT * FROM dense UNION ALL SELECT * FROM sparse) u
GROUP BY uuid, document, cmetadata
ORDER BY rrf DESC
LIMIT %(k)s
"""


def _ensure_fts_index() -> None:
    """sparse 검색용 FTS GIN 인덱스 보장 (idempotent, best-effort).

    없어도 seq-scan 으로 동작은 하나 느려서, 프로세스 기동 시 1회 만든다. stdio
    MCP 라 stdout 출력 금지 — 실패는 stderr 로만 흘리고 검색은 계속한다."""
    if not DB:
        return
    try:
        with psycopg.connect(DB) as conn:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_lpe_fts ON langchain_pg_embedding "
                "USING gin (to_tsvector('simple', document))")
            conn.commit()
    except Exception as e:  # noqa: BLE001 — best-effort, 검색 기능은 인덱스 없이도 동작
        print(f"[smart_search] FTS index ensure skipped: {e}", file=sys.stderr)


def _vec_literal(v: list[float]) -> str:
    """pgvector ::vector 캐스트용 문자열 (pgvector-python 의존성 회피)."""
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


async def _retrieve(subqueries: list[str], k: int = 20) -> list[dict]:
    """각 서브쿼리를 hybrid 검색 → RRF 로 multi-query 병합."""
    vecs = await _embed(subqueries)
    merged: dict[str, dict] = {}
    with psycopg.connect(DB) as conn:
        for q, v in zip(subqueries, vecs):
            rows = conn.execute(
                _HYBRID_SQL, {"vec": _vec_literal(v), "q": q, "uid": USER_ID, "k": k}
            ).fetchall()
            for document, meta, rrf in rows:
                key = (meta or {}).get("digest") or document[:64]
                row = merged.setdefault(
                    key, {"document": document, "meta": meta or {}, "score": 0.0})
                row["score"] += float(rrf)  # within-subquery RRF 를 multi-query 합산
    return sorted(merged.values(), key=lambda r: -r["score"])[:k]


async def _rerank(query: str, cands: list[dict], n: int = 8) -> list[dict]:
    """bge-reranker-v2-m3 cross-encoder 로 후보를 재정렬한다.

    RERANK 미설정이면 **무변형 통과**(Phase 1 동작 유지). hybrid 가 RRF 로 모은
    후보를, 교차 인코더가 (질문, 청크) 쌍을 직접 채점해 정밀 재정렬한다.
    엔드포인트는 vLLM `--runner pooling`(/rerank, Jina 호환) 또는 동등 서버.
    응답 {results:[{index,...}]} (Jina/vLLM) · [{index,...}] (TEI) 둘 다 허용."""
    if not RERANK or len(cands) <= 1:
        return cands
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            f"{RERANK}/rerank",
            json={"model": "bge-reranker-v2-m3", "query": query,
                  "documents": [d["document"] for d in cands], "top_n": n},
        )
        r.raise_for_status()
        body = r.json()
    items = body.get("results") if isinstance(body, dict) else body
    order = [it["index"] for it in items]  # relevance 내림차순 인덱스
    return [cands[i] for i in order if 0 <= i < len(cands)][:n]


async def _evaluate(query: str, cands: list[dict]) -> list[dict]:
    """gemma 1콜로 후보들의 relevance 를 일괄 판정 → 노이즈 컷."""
    if not cands:
        return cands
    listing = "\n\n".join(f"[{i}] {d['document'][:500]}" for i, d in enumerate(cands))
    out = await _chat(
        f"질문: {query}\n\n후보 발췌들:\n{listing}\n\n"
        "위 발췌 중 질문에 답하는 데 직접 쓸모 있는 것의 번호만 쉼표로 나열 "
        "(예: 0,2,5). 쓸모 있는 게 없으면 none:",
        max_tokens=40,
    )
    if "none" in out.lower():
        return []
    idxs = {int(x) for x in re.findall(r"\d+", out)}
    keep = [d for i, d in enumerate(cands) if i in idxs]
    return keep or cands  # 파싱 실패 시 원본 보존(과도 필터 방지)


@mcp.tool()
async def smart_search(query: str, top_k: int = 8) -> list[dict]:
    """업로드한 문서에서 질문에 답할 근거를 정밀 검색한다.

    reformulation(다중 쿼리) → dense 검색(RRF 병합) → relevance 평가 순으로,
    단순 키워드 매칭이 놓치는 동의어·우회 표현까지 회수한다. 인용 가능한 발췌
    청크 리스트를 반환한다(각 항목에 file_id/source 포함).

    Args:
        query: 사용자 질문(자연어).
        top_k: 반환할 청크 수(기본 8).
    """
    if not USER_ID:
        raise RuntimeError(
            "no user scope (LIBRECHAT_USER_ID empty) — refusing global search")
    if not DB:
        raise RuntimeError("RAG_DB_URL not configured")

    subqueries = await _reformulate(query)
    cands = await _retrieve(subqueries, k=max(20, top_k * 2))
    cands = await _rerank(query, cands, n=max(top_k * 2, 12))  # RERANK 없으면 무변형
    if DO_EVAL:
        cands = await _evaluate(query, cands)
    return [
        {"document": d["document"],
         "file_id": d["meta"].get("file_id"),
         "source": d["meta"].get("source"),
         "score": round(d["score"], 4)}
        for d in cands[:top_k]
    ]


if __name__ == "__main__":
    _ensure_fts_index()  # sparse FTS 인덱스 보장 (1회, best-effort)
    mcp.run()
