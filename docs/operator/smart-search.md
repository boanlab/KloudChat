# smart_search — 정밀 문서 검색 MCP

> 업로드 문서 RAG 강화 — **reformulation + multi-query + (Phase 1) hybrid + (Phase 2) rerank + relevance 평가**.
>
> - **목표**: 기본 `file_search`(rag_api dense-only)가 놓치는 동의어·우회표현 회수율 향상
> - **부품**: 전부 로컬 (gemma reformulate/eval · bge-m3 embed · pgvector)

## 한눈에

```
query
 └─ Reformulate (gemma)   → 원질문 + 서브쿼리 3개
 └─ Embed (bge-m3)        → 각 서브쿼리 1024-d 벡터
 └─ Hybrid retrieve       → dense(코사인 <=>) ⊕ sparse(tsvector ts_rank), RRF 융합
 └─ Rerank (bge-reranker) → cross-encoder 정밀 재정렬 (RERANK_URL 설정 시; 없으면 스킵)
 └─ Evaluate (gemma)      → relevance 일괄 판정으로 노이즈 컷
return [{document, file_id, source, score}]
```

부품: `local/gemma-4-26b`(reformulate/eval, 비용 0) · `bge-m3`(embed) · `vectordb` pgvector `langchain_pg_embedding` · (Phase 2) `bge-reranker-v2-m3`.

## 구성 요소

| 파일 | 역할 |
|---|---|
| [`mcp/smart_search.py`](../../mcp/smart_search.py) | stdio MCP 서버 (`uv run --script`, FastMCP) |
| `librechat.yaml::mcpServers.smart_search` | 등록 (`startup:false`, `{{LIBRECHAT_USER_ID}}` 주입) |
| `docker-compose.yml` LibreChat env `RAG_DB_URL` | pgvector 접속 URL |
| `scripts/manage.sh` `MCP_DEFAULT`/`MCP_RESEARCH` | 에이전트 부착 |

## 테넌시 & 역할 분리 (정책)

- 모든 쿼리에 `cmetadata->>'user_id' = LIBRECHAT_USER_ID` 강제
- `{{LIBRECHAT_USER_ID}}` 는 LibreChat 이 호출자별 치환 (per-session stdio, `startup:false`)
- USER_ID 비면 **거부** (전역검색 금지) — `usage.py` 와 동일 규율

**역할 분리 — `smart_search` 는 "사용자 본인 업로드" 전용.** LibreChat 은 RAG 문서를 `langchain_pg_embedding` 한 테이블에 두 종류로 저장:

| 종류 | `cmetadata.user_id` | 담당 도구 |
|---|---|---|
| **사용자 대화 업로드** | human 사용자 Mongo id | **smart_search** (고도화 retrieve) |
| **에이전트 지식**(agent knowledge) | 에이전트 id (`agent_…`) | **native `file_search`** (LibreChat 내장) |

- native `file_search`: 쿼리 시 LibreChat 이 **에이전트의 `tool_resources.file_search.file_ids` 를 서버측에서 직접 주입**해 rag_api 호출 → 에이전트 지식을 올바르게 스코프
- `smart_search`: 그 file_id 를 받을 수 없어(아래) 사용자 본인 업로드만 담당
- **두 도구가 역할 분담**

> **왜 `smart_search` 가 에이전트 지식을 직접 못 다루나 (조사 결과)**:
> - 에이전트 지식 검색엔 "지금 어느 에이전트인지"(agent id) 필요 — LibreChat MCP 동적 placeholder 가 미노출
> - body placeholder 는 `ALLOWED_BODY_FIELDS = ['conversationId','parentMessageId','messageId']` 하드코딩 → `{{LIBRECHAT_BODY_AGENT_ID}}` 같은 건 없음
> - 우회(conversationId → Mongo 로 agent_id 역추적, 대화 문서에 `agent_id` 존재)는 가능하나 미채택 — Mongo 결합 + 새 대화 첫 메시지의 conversationId 공백 때문
> - 에이전트 지식 검색은 native `file_search` 담당

## 단계별 로드맵

- **Phase 0** — reformulation + dense 검색(RRF 병합) + relevance 평가. `SMART_SEARCH_EVAL=0` 로 eval 끄면 latency 절감.
- **Phase 1 (현재)** — + tsvector sparse 로 **hybrid**(dense ⊕ sparse, RRF 융합). sparse 는 `plainto_tsquery` 의 AND 를 OR 치환해 다어절 recall 확보. FTS 인덱스는 서버 기동 시 `_ensure_fts_index()` 가 보장(idempotent). 수동 생성:
  ```sql
  CREATE INDEX ix_lpe_fts ON langchain_pg_embedding
    USING gin (to_tsvector('simple', document));
  ```
- **Phase 2 (코드 완료, 배포 게이트)** — `bge-reranker-v2-m3` cross-encoder 재정렬. MCP 통합은 끝났고 `RERANK_URL` 만 설정되면 즉시 동작(빈값=스킵, hybrid 까지). reranker 서버는 GPU 노드 배포라 아래 절차로 켠다.

### Phase 2 reranker 배포

**reranker** 모델 서버를 GPU 노드에 배포 후 `RERANK_URL` 연결. bge-m3 임베딩 서버와 동일 패턴 (vLLM pooling runner).

1. **가중치 다운로드** (GPU 노드): `BAAI/bge-reranker-v2-m3` (~1.1 GiB fp16) → `${VLLM_MODELS_ROOT}/bge-reranker-v2-m3`.
2. **서빙** — `docker-compose.vllm.yml` 에 `vllm-bge-m3` 를 복제한 서비스 추가, `--convert embed` → `--convert score`(cross-encoder; vLLM 버전에 따라 `--task score`), 포트 `8005`:
   ```yaml
   vllm-bge-reranker:
     image: ${VLLM_IMAGE:-vllm/vllm-openai:nightly-aarch64}
     command: [--model, /model, --served-model-name, bge-reranker-v2-m3,
               --runner, pooling, --convert, score, --host, 0.0.0.0, --port, "8000",
               --gpu-memory-utilization, "${VLLM_BGE_RERANKER_GPU_UTIL:-0.05}", --trust-remote-code]
     volumes: ["${VLLM_MODELS_ROOT:-/var/lib/vllm/models}/bge-reranker-v2-m3:/model:ro"]
     ports: ["8005:8000"]
   ```
   (또는 경량 TEI `text-embeddings-inference` 의 `/rerank`. 응답 스키마는 `_rerank` 가 Jina/TEI 둘 다 허용.) **vLLM 플래그는 버전별로 다르니 배포 시 `/rerank` 응답을 확인**할 것.
3. **scheduler 통합(선택)** — `scheduler/catalog.py` 에 `rerank-bge-m3` 워크로드(`embed-bge-m3` 미러, `weight_bytes≈1.2 GiB`, `min_replicas=1`) 추가 후 `setup.sh scheduler apply`. 254 가 빠듯하면(VRAM) `plan` 으로 자리부터 확인.
4. **연결** — `.env` 에 `RERANK_URL=http://<reranker-host>:8005` → `docker compose up -d --force-recreate librechat`. 끝나면 smart_search 가 hybrid 후보를 cross-encoder 로 재정렬한다.

## 활성화

```bash
# 1) RAG_DB_URL 주입 위해 LibreChat 재생성 (librechat.yaml 단일파일 마운트)
docker compose up -d --force-recreate librechat
# 2) 에이전트에 MCP 부착
./scripts/manage.sh agent sync
# 3) 확인 — 문서 첨부 후 Super/Deep Research 에서 smart_search 호출되는지
```

**비활성화** — `manage.sh` 의 MCP 배열에서 `sys__all__sys_mcp_smart_search` 제거 후 `agent sync`.

## 같이 보면 좋은 문서

- [도구/MCP](tools.md) · [라우팅 정책](routing-policy.md) · [모델 설정](models.md)
