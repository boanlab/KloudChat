# 아키텍처 상세

> **KloudChat** = *LibreChat (UI) + LiteLLM (게이트웨이) + vLLM · OpenRouter*.

- 모든 **LLM 호출**을 **LiteLLM** 한 곳으로 집약 — **vLLM · OpenRouter** 를 OpenAI 호환 endpoint 로 추상화 (commercial 은 OR 경유)
- 모델별 가격을 **LiteLLM** 에 박아 팀·사용자·가상키 단위 실비 예산 관리
- **ComfyUI** 는 워크플로 JSON 기반 → A1111 호환 툴과 직접 미연결, 얇은 **shim** 으로 어댑팅 + 라우팅
- 같은 *shim* 패턴이 두 곳 더 — **Super Agent** (`local/gemma-4-26b` 단일패스 챗+아티팩트), **Crawl4AI** (Firecrawl Cloud 대신 self-host Playwright)

## 배포 모드 (GPU 유무)

처음 읽는다면 여기부터 — 나머지 상세는 이 두 모드의 변형.

- **GPU 노드 있음** — 전 기능 활성
  - 로컬 vLLM(`gemma-4-26b` / `qwen3.5:122b`) + ComfyUI(FLUX) + Whisper 가동
  - OpenRouter 는 폴백·상용 보강
- **GPU 없음 (OR 전용)**
  - 로컬 두뇌(`local/gemma-4-26b` · `local/qwen3.5:122b`)가 OR 동일모델로 직결 등록(`gen-litellm-config.sh::emit_brain`) → Super Agent · Slide Studio · Deep Research · Paper Banana · 이미지/비디오 에이전트가 OR 두뇌로 동작
  - **Whisper 의존 기능(Note Taker / 자막 없는 YouTube 전사)만 비활성** — whisper 는 GPU 전용 + OR STT 폴백 없음 → 도구 자체 미부착
  - RAG 임베딩은 `bge-m3` → OR `text-embedding-3-small` 자동 swap

게이팅 상세(capability 플래그) → [모델 설정 — GPU 없는 OR 전용 배포](models.md).

## 전체 구조

- 두 **docker compose stack** 분리 (별도 project / bridge)
- 같은 노드면 host publish 포트, 다른 노드면 `.env` 의 `LITELLM_URL` 이 LiteLLM 노드

- `docker-compose.yml`         (project `kloudchat`)        — LibreChat 측 (외부 :8080)
- `docker-compose.litellm.yml` (project `kloudchat-litellm`) — LiteLLM 측 (외부 :8000)

```
┌──────────────────────────────────┐    ┌──────────────────────────────┐
│ LibreChat 노드 (kloudchat)        │    │ LiteLLM 노드 (kloudchat-      │
│                                  │    │ litellm)                     │
│  LibreChat (:8080) ←─── 브라우저  │    │                              │
│    ├→ mongodb / meilisearch      │    │  LiteLLM (:8000) ←── 외부 API │
│    ├→ rag_api → pgvector         │    │    │  (모델별 deployment 를   │
│    ├→ code-interpreter           │    │    │   노드별로 등록,         │
│    ├→ comfyui-shim ──────────────│────│    │   같은 model_name 끼리   │
│    ├→ whisper-shim ──────────────│────│    │   router 가 LB)         │
│    ├→ crawl4ai-shim              │    │    │                         │
│    ├→ searxng / valkey           │    │    └→ VLLM_*_URL              │
│    ├→ deep-research              │    │                              │
│    └→ LiteLLM (LITELLM_URL) ─────│────→ :8000                         │
│         (rag_api / deep-research │    │                              │
│          / MCP 자식도 동일 URL)   │    │  super-agent-shim ←──────────│
│                                  │    │    (local/auto-route 가상   │
│                                  │    │     모델 — LiteLLM 만 호출)  │
│                                  │    │                              │
│                                  │    │  litellm-db (postgres)       │
└──────────────────────────────────┘    └──────────────────────────────┘
        │                                          │
        ├→ COMFYUI_URLS (호스트/원격 GPU 노드들)
        ├→ WHISPER_URLS
        └→ vLLM 노드들 (LiteLLM 노드 측에서 직접 호출)
```

## 컴포넌트별 역할

**LibreChat** — 채팅 UI

- 에이전트 빌더 / 파일 업로드 / 웹 검색 / 이미지 생성 통합
- LiteLLM 을 custom OpenAI endpoint 로 등록

**LiteLLM** — LLM 게이트웨이

- vLLM 로컬 + OpenRouter commercial 을 단일 endpoint 로 통합
- `model_info.input/output_cost_per_token` 에 OR 환산 가격 박아 실비 예산 관리
- 로컬 chat 모델은 `model_info.supports_function_calling=true` 로 emit → LibreChat 에이전트가 도구를 ReAct 텍스트가 아닌 native function call 로 실행
- Anthropic Messages API 프록시로 동작 → Claude Code 를 로컬 모델에 연결

**vLLM** — 유일한 로컬 LLM 런타임

- GPU 노드에서 `docker-compose.vllm.yml` 로 구동
- continuous batching + PagedAttention 으로 동시 8+ throughput 높고 TTFT 짧음 (GB10 bench: 동시 8 throughput 3-5x, TTFT 30-89x)
- LiteLLM 이 `.env` 의 `VLLM_*_URL` csv 각 노드를 직접 호출
- `gen-litellm-config.sh` 가 각 URL `/v1/models` 디스커버리로 모델 → 노드 매핑 생성 → 같은 `model_name` deployment 를 N개 emit, router 의 `least-busy` 가 분산
- `/v1/models` 의 `max_model_len` 으로 모델별 ctx emit (gemma-4-26b 32K / 122b 는 디스커버리값(현 클러스터 128K) / coder 32K)
- `.env` 수정 후 `gen-litellm-config.sh && docker compose -f docker-compose.litellm.yml restart litellm`; 분리 노드면 생성된 yaml sync
- chat profile 라이프사이클은 `setup.sh all` 의 scheduler apply; 수동 운영은 `manage-vllm.sh`
- coder 는 `--profile coder` 로 scheduler 영역 밖 격리

**super-agent-shim** — `local/auto-route` 백엔드 (default Super Agent)

- 기본 `CHAT_MODEL=local/gemma-4-26b` 단일패스로 챗·도구·아티팩트·이미지 업로드(멀티모달) 모두 처리 (`ARTIFACT_MODEL` 비움, 별도 coder/2-pass 없음)
- artifacts 토글 ON 이면 챗 경로에 concise/detox 아티팩트 지시문 주입
- `ROUTE_HEAVY_MODEL` 설정 시 무거운 요청(`_is_heavy` 휴리스틱)은 그 모델(122b)로 라우팅(아티팩트 제외)
- 자세히는 `super-agent-shim/app.py`

**RAG API** — 문서 답변

- 청크 분할 → pgvector
- 임베딩은 `bge-m3` (vLLM, 없으면 `text-embedding-3-small` 자동 swap)
- `RAG_OPENAI_BASEURL=${LITELLM_URL}/v1` 로 LiteLLM 경유 → spend/라우팅 통일
- `HYBRID_SEARCH=true` 로 pgvector + MeiliSearch (BM25) 조합

**MeiliSearch** — 전문 검색. 대화 검색 + RAG Hybrid 키워드 백엔드.

**SearXNG + valkey** — 프라이버시 메타 검색

- LibreChat webSearch 백엔드, 내부 (`http://searxng:8080`) 만
- `searxng/settings.yml` 에 `science` 탭 (Scholar / arxiv / semantic scholar / OpenAlex / crossref / PubMed) + 일반 web (Google / DuckDuckGo / Naver / Wikipedia)
- autocomplete=duckduckgo (Google 은 CAPTCHA 위험)
- `GRANIAN_WORKERS=4`
- rate limit 은 `limiter.toml` + valkey, `172.18.0.0/16` 는 pass_ip

**crawl4ai-shim** — `webSearch.scraperType=firecrawl` 의 `/v2/scrape` 를 self-host 로 받는 어댑터

- 내부는 [Crawl4AI](https://github.com/unclecode/crawl4ai) (Playwright Chromium)
- `firecrawlApiKey` 는 dummy

```
이미지:   자체 빌드 (Dockerfile.crawl4ai-shim — playwright/python:v1.49 + crawl4ai)
포트:     8080 (Firecrawl /v2/scrape /v1/scrape /v0/scrape 호환, 내부 전용)
환경변수: LOG_LEVEL, DEFAULT_TIMEOUT_MS=30000
```

**ComfyUI** — 디퓨전 런타임

- FLUX.1-dev / FLUX.1-schnell (Q8 GGUF) 워크플로 사전 정의
- GPU 노드 배포는 아키텍처별 — amd64 는 컨테이너(`docker-compose.media.yml` / `Dockerfile.comfyui`), GB10(arm64) 는 systemd native
- LibreChat 은 `comfyui-shim` 경유만

```
설치:     scripts/install-comfyui.sh (detect_arch 분기, ComfyUI master, torch cu128)
          ├─ amd64       컨테이너 (docker-compose.media.yml) — torch 2.9.1 단일 빌드 (Ada·Blackwell 공용)
          └─ arm64(GB10) systemd venv — torch 2.9.1 (NVFP4 dtype 노출)
포트:     8188 (ComfyUI API, 내부/LAN 전용)
가중치:   amd64          /var/lib/comfyui/models/{checkpoints,unet,clip,vae}  (COMFYUI_MODELS_DIR 호스트 볼륨)
          arm64(GB10)    /opt/comfyui/app/ComfyUI/models/{checkpoints,unet,clip,vae}
출력:     /var/lib/comfyui/output  (amd64 는 컨테이너 볼륨)
재설치:   ./scripts/install-comfyui.sh --reinstall
```

**comfyui-shim** — A1111 `/sdapi/v1/txt2img` → 워크플로 JSON 변환 → ComfyUI `/prompt` → base64 PNG

- 모델은 `override_settings.sd_model_checkpoint` 로 `flux-schnell` / `flux-dev` (enum 은 `rag-patches/patch_librechat_sd_model.js`)
- `COMFYUI_URLS` 다중이면 큐 깊이 기준 least-busy 라우팅
- backend 없으면 외부 OpenRouter 이미지 모델로 폴백

```
이미지:   자체 빌드 (Dockerfile.comfyui-shim — python:3.11-slim, FastAPI + httpx)
포트:     7860 (A1111 호환, 내부 전용)
환경변수: COMFYUI_URLS=<csv>   # backend 있으면 flux-schnell, 없으면 외부 nano-banana (shim 이 결정)
```

**Whisper** — faster-whisper + FastAPI 의 `/v1/audio/transcriptions`. 오디오 전사(STT) backend

- 두 소비자
  - **Note Taker** 오디오 업로드(「텍스트로 업로드」 시 전사)
  - **자막 없는 YouTube** (`mcp/youtube.py` 가 `yt-dlp` 로 audio → whisper-shim POST)
- GPU 노드 배포는 아키텍처별 — amd64 는 컨테이너(`docker-compose.media.yml`), GB10(arm64) 는 systemd native
- whisper 는 **GPU 전용** — `WHISPER_URLS` 가 비면 `whisper_eligible`(lib.sh) 이 false → Note Taker 미생성 + youtube 도구 미부착(OR STT 폴백 없음)

```
설치:     scripts/install-whisper.sh (detect_arch 분기 — amd64 컨테이너 / arm64 systemd; faster-whisper + FastAPI)
포트:     9000 (OpenAI-compat, 내부/LAN 전용)
모델:     WHISPER_MODEL (기본 large-v3, lazy-load 후 상주)
prewarm:  scripts/download-whisper-models.sh (선택, 첫 호출 latency / 에어갭 대응)
```

`whisper-shim` 라우터:

- `/health` 통과 backend 중 in-flight 최소 노드로 라우팅 (stateless 단일 라운드트립)
- LibreChat 의 `WHISPER_URL` 은 항상 `http://whisper-shim:9000`

**MCP**

- stdio 8개 — `librechat.yaml::mcpServers` 따라 자식 spawn (uvx 가 의존성 자동 설치)
  - 공통: `fetch_url` / `time` / `usage` / `youtube` / `smart_search`
  - 전용: `export_deck`(Slide Studio) / `generate_video`(Video Studio) / `paperbanana`(Paper Banana)
- 수학은 `execute_code` 로 흡수
- HTTP 1개 (`deep_research`) 만 사이드카 — alpine LibreChat 의 playwright wheel 비호환 → debian-slim 분리, `mcp-proxy` 가 stdio → streamable-http (`http://deep-research:8081/mcp`) wrap
- agent.tools 에 `sys__all__sys_mcp_<servername>` 추가 → 그 서버 전체 노출
- 자세히는 [도구 문서](tools.md)

**에이전트 instructions**

- 공유 운영 규칙(도구 honesty / `sources: []` 시 URL 환각 금지 / `execute_code print()` 강제, deep_research·generate_image 가이드)은 `scripts/agent-instructions-appendix.txt` ground truth
- `manage.sh::upsert_shared_agent_catalog::instructionsFor()` 가 base 끝에 append
- 카탈로그는 ADMIN 한 벌이 전 사용자에게 read-only 공유
- `manage.sh agent sync` 멱등 — MongoDB wipe 후 `setup.sh librechat` 만 다시 돌리면 복원

**개인화 메모리** — `librechat.yaml::memory`

- 대화에서 사용자 관련 지속 사실 추출·저장 → 이후 대화에 활용
- 추출 모델은 로컬 `local/gemma-4-26b` (LiteLLM 경유, 데이터 외부 미유출)
- `personalize:true` 라 사용자별 on/off 토글 가능, `validKeys` 로 저장 범주 제한
- `interface.memories:true` 로 UI 노출

**LiteLLM 콜백 (sanitize_python_tag)** — 로컬 모델의 텍스트 함수 호출 leak 처리

- `<|python_tag|>{json}`, ` ```json``` ` fence, `**Call Function:**` prefix, bare JSON, PUA `turn{N}<tool>{M}` 등을 정상 `tool_calls` 로 재구성
- qwen 계열 CJK 한자 leak 을 한글 음독으로 치환
- `litellm-callbacks/sanitize_python_tag.py`, non-stream + stream 양쪽

## 네트워크

- 두 stack 분리 bridge
- cross-stack 은 호스트 publish 포트(`:8000`) 경유 — `.env::LITELLM_URL`
- 외부 노출은 LibreChat 8080 / LiteLLM 8000

```
외부 노출:
  kloudchat:         8080 (LibreChat)
  kloudchat-litellm: 8000 (LiteLLM)

kloudchat 내부:  mongodb:27017, meilisearch:7700, vectordb:5432,
                rag_api:8000, searxng:8080, valkey:6379,
                code-interpreter:8000, comfyui-shim:7860,
                crawl4ai-shim:8080, whisper-shim:9000, deep-research:8081

kloudchat-litellm 내부:  litellm-db:5432, super-agent-shim:8080

Cross-stack (LibreChat 측 → LiteLLM 측):
  LITELLM_URL=http://host.docker.internal:8000  (같은 노드 default)
            =http://<litellm-host>:8000          (다른 노드)
  사용처: librechat / rag_api / deep-research / MCP 자식 (youtube/usage) /
         librechat.yaml 의 custom endpoint baseURL

vLLM 노드:  8001/8002/8003/8004  (gemma/122b/coder/bge — LiteLLM router 가 직접 호출),
호스트:     comfyui:8188  (comfyui-shim upstream),
            whisper:9000  (whisper-shim upstream)
```

## 요청 흐름 — 채팅

```
브라우저
  → LibreChat (인증·세션 관리)
  → LiteLLM (/v1/chat/completions, LITELLM_SERVICE_KEY, model_name 기준 라우팅)
  ├─ local/auto-route → super-agent-shim:8080
  │                        └─ 단일패스 chat: local/gemma-4-26b (stream)
  │                           (artifacts 토글 ON 이면 concise/detox 지시문 주입)
  ├─ local/gemma-4-26b → VLLM_GEMMA26_URL (least-busy)
  │                       (multi-node 시 router 가 input ctx 기반 자동 분기)
  │                       (GPU 없는 배포: URL 미설정 시 → openrouter/google/gemma-4-26b-a4b-it 직결)
  ├─ local/qwen3.5:122b → VLLM_QWEN122B_URL (Deep Research / 정밀, PLAIN)
  │                       (GPU 없는 배포: URL 미설정 시 → openrouter 동일모델 직결)
  ├─ openai/*           → OpenRouter (gpt-5.5/5/5-mini/5-nano)
  ├─ anthropic/*        → OpenRouter (claude-opus-4.8/4.7/4.6, claude-sonnet-4.6, claude-haiku-4.5)
  └─ google/*           → OpenRouter (gemini-3.1-pro-preview, gemini-2.5-pro/flash)
```

## 요청 흐름 — RAG

```
파일 업로드
  → LibreChat → RAG API (청크 + 임베딩)
  → LiteLLM (router) → bge-m3 보유 vLLM 노드 (없으면 OR text-embedding-3-small)
  → pgvector

질문
  → LibreChat → RAG API (쿼리 임베딩 + Hybrid 검색: pgvector + MeiliSearch)
  → LiteLLM (router) → 답변 생성 모델 (사용자 선택)
```

## 요청 흐름 — 이미지 생성 / 편집

```
브라우저 / 에이전트 (예: Image Studio (qwen3.5:122b))
  → LibreChat (generate_image 툴, model="<alias>")
  → comfyui-shim:7860 (/sdapi/v1/txt2img | /sdapi/v1/img2img)
       │  ① override_settings.sd_model_checkpoint 로 워크플로 선택 (flux-schnell / flux-dev)
       │  ② 워크플로 JSON 에 프롬프트·시드·CFG·해상도 주입
       │  ③ (img2img 만) /upload/image 로 입력 이미지 업로드
  → ComfyUI native (host.docker.internal:8188 또는 원격 노드, /prompt 큐)
  → GPU → /history/<prompt_id> 폴링 + /view 회수
  ← base64 PNG → LibreChat 인라인 표시
```

**에이전트 명명**

- `Super Agent` (`local/gemma-4-26b` 단일패스)
- functional 에이전트
  - `Image Studio` (`local/qwen3.5:122b`, `generate_image`) / `Slide Studio` (`local/qwen3.5:122b`, HTML 발표자료 직접 저작)
  - `Note Taker` (내장 STT 전사→문서) / `Deep Research` / `Video Studio` (텍스트→비디오) / `Paper Banana` (학술 figure)
- 모델별 일반·이미지·비디오 에이전트
- dropdown 정렬: Super → Image → Video → Slide → Note Taker → Deep Research → Paper Banana → 상용 LLM(openai → anthropic → google → deepseek → x-ai → perplexity → meta → qwen) → 모델별 이미지·비디오 에이전트 (`manage.sh` 의 `DISPLAY_ORDER` 기반 `updatedAt`)
- `generate_image` 는 `Image Studio` 에만 부착 — Super/일반 에이전트엔 비부착 (`manage.sh::builtinFor`)
- 자세히는 [모델 설정](models.md#모델-선택)

## 스케일 고려사항

GPU 노드 추가로 수평 확장 — `NODES_VLLM` 에 노드 추가 → scheduler 가 재배치 + LiteLLM router 가 모델별 가용 노드로 `least-busy` LB. 병목:

1. **GPU VRAM** — 노드당 LLM · ComfyUI 공유. 모델별 권장 노드 클래스는 [GPU 메모리 가이드](gpu-memory.md).
2. **vLLM 노드 수** — 동시 사용자/모델이 늘면 `NODES_VLLM` 에 노드 추가 + scheduler 재배치. LiteLLM `num_workers` 도 함께 조정.
3. **MongoDB** — 기본 단일 인스턴스. 필요 시 replica set.
