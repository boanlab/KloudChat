# 아키텍처 상세

> KloudChat 은 *LibreChat (UI) + LiteLLM (게이트웨이) + 백엔드 다중 소스* 패턴입니다. LibreChat 은 채팅 UI / 에이전트 / 파일·검색 통합만 담당하고, 모든 LLM 호출은 LiteLLM 한 곳을 거치게 만들어 native API · Ollama · OpenRouter 를 동일한 OpenAI 호환 endpoint 로 추상화합니다. 모델별 가격이 LiteLLM 에 박혀 있어 팀·사용자·가상키 기반의 실비 단위 예산 관리가 동작합니다. 이미지 생성은 ComfyUI 가 워크플로 JSON 기반이라 LibreChat 의 A1111 호환 툴과 직접 못 붙어 — 얇은 shim 으로 어댑팅 + 노드 라우팅까지 같이 처리합니다.

## 전체 구조

```
┌─────────────────────────────────────────────────────┐
│  compose 호스트 (Linux)                              │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │  Docker Network: kloudchat                  │   │
│  │                                             │   │
│  │  LibreChat (:8080) ←────────────────────────────── 브라우저
│  │    └→ mongodb / meilisearch / pgvector …    │   │
│  │                                             │   │
│  │  LiteLLM (:8000) ←──────────────────────────────── 외부 API
│  │    │  (모델별 deployment 를 노드별로 등록,    │   │
│  │    │   같은 model_name 끼리 router 가 LB)    │   │
│  │    └────────────────────────────────────────────→ OLLAMA_URLS (csv)
│  │                                             │   │   ├─ http://host.docker.internal:11434
│  │                                             │   │   ├─ http://gpu-node-1:11434
│  │                                             │   │   └─ http://gpu-node-2:11434
│  │  rag_api ────────────────────────────────────────┘   (모델은 노드별로 자유롭게 pull —
│  │    (bge-m3 임베딩도 LiteLLM 경유)             │   │    union 디스커버리로 보유 노드만 등록)
│  │                                             │   │
│  │  searxng / code-interpreter                 │   │
│  │                                             │   │
│  │  comfyui-shim (A1111 어댑터 + 라우터)        │ ──┼─→ COMFYUI_URLS (csv)
│  │                                             │   │   ├─ http://host.docker.internal:8188 (same-host native)
│  │                                             │   │   └─ http://gpu-node-1:8188,http://gpu-node-2:8188 (원격 노드)
│  │                                             │       (요청마다 /queue 깊이로 노드 선택,
│  │                                             │        prompt_id → 노드 고정)
│  └─────────────────────────────────────────────┘
└─────────────────────────────────────────────────────┘
```

## 컴포넌트별 역할

### LibreChat
사용자 대면 채팅 인터페이스. 에이전트 빌더, 파일 업로드, 웹 검색, 이미지 생성 요청을 통합합니다. LiteLLM 을 커스텀 OpenAI 호환 엔드포인트로 등록해 모든 LLM 요청을 라우팅합니다.

### LiteLLM
LLM 게이트웨이. Ollama 로컬 모델, OpenRouter free tier (gpt-oss 등), 클라우드 API (OpenAI/Anthropic/Gemini) 를 단일 OpenAI 호환 엔드포인트로 추상화합니다. 모델별 OpenRouter 환산 가격이 `model_info.input/output_cost_per_token` 으로 박혀 있어 팀·사용자·가상 키 기반 예산 관리가 실비 단위로 동작합니다. Anthropic Messages API 프록시로도 동작해 Claude Code 를 로컬 모델에 연결.

### Ollama
LLM 런타임. Docker 내부가 아닌 **호스트에서 직접 실행**됩니다. 모델은 기본적으로 `~/.ollama/models` 에 저장됩니다.

LiteLLM 이 `OLLAMA_URLS` csv 의 각 노드를 직접 호출합니다. `gen-litellm-config.sh` 는 `/api/tags` 를 노드별로 조회해 모델 → 보유 노드 매핑을 만들고, 모델 하나당 보유 노드 수만큼 같은 `model_name` deployment 를 emit 합니다. LiteLLM router 가 `routing_strategy: least-busy` 로 같은 `model_name` 후보들 사이에서 진행 중 요청이 적은 노드를 고르므로, **이기종 GPU 환경**에서도 큰 모델은 보유 노드(들)에만, 작은 모델은 여러 노드에 자연스럽게 분산됩니다. `.env` 수정 후 `./scripts/gen-litellm-config.sh && docker compose restart litellm`.

### RAG API
문서 기반 답변 (RAG) 파이프라인. 업로드된 파일을 청크로 분할해 pgvector 에 임베딩을 저장합니다. 임베딩은 `bge-m3` (다국어) 를 Ollama 호스트에 직접 호출 — LiteLLM 을 경유하지 않습니다. `HYBRID_SEARCH=true` 설정으로 pgvector (시맨틱) 와 MeiliSearch (BM25 키워드) 결과를 조합해 검색합니다.

### MeiliSearch
전문 검색 엔진. LibreChat 의 대화 내용 검색과 RAG Hybrid Search 의 키워드 검색 백엔드로 사용됩니다.

### SearXNG
프라이버시 중심 메타 검색 엔진. LibreChat 웹 검색 기능의 백엔드입니다. 외부 포트를 열지 않고 내부 네트워크 (`http://searxng:8080`) 로만 접근합니다.

### ComfyUI (이미지 생성)
노드 기반 디퓨전 런타임. KloudChat 은 Qwen-Image · Qwen-Image-Edit · FLUX.1-dev · FLUX.1-schnell 네 파이프라인을 워크플로 템플릿으로 미리 정의해 둡니다.

ComfyUI 는 항상 **호스트 native** (venv + systemd) 로 실행 — `scripts/install-comfyui.sh` 가 `/opt/comfyui/{venv,app}` + `/var/lib/comfyui/output` 을 만들고 `:8188` 에 바인딩. 모델 가중치는 ComfyUI repo 안 default 경로 (`/opt/comfyui/app/ComfyUI/models`) 에 둡니다. compose 호스트와 같은 머신에 설치하든 (`COMFYUI_URLS=http://host.docker.internal:8188`), 원격 GPU 노드(들)에 설치하든 (`COMFYUI_URLS=http://gpu-node-1:8188,...`) 형태는 동일. LibreChat 은 `comfyui-shim` 을 통해서만 접근.

```
설치:     scripts/install-comfyui.sh (PyTorch cu128, ComfyUI master)
포트:     8188 (ComfyUI 본 API, 내부/LAN 전용)
가중치:   /opt/comfyui/app/ComfyUI/models/{checkpoints,unet,clip,vae}
출력:     /var/lib/comfyui/output (심링크로 app 안에 노출)
```

### comfyui-shim (A1111 어댑터 + 라우터)
LibreChat 의 `image-generation` 툴은 A1111 형식 (`/sdapi/v1/txt2img`, `/sdapi/v1/img2img`) 만 알아듣지만 ComfyUI 는 워크플로 JSON 기반이라 둘이 호환되지 않습니다. 이 shim 이 A1111 요청을 받아 워크플로 템플릿에 프롬프트·시드·CFG 를 끼워넣은 뒤 ComfyUI `/prompt` 큐에 넣고 결과 이미지를 base64 로 반환합니다. 모델은 요청의 `override_settings.sd_model_checkpoint` 값으로 `flux-schnell` / `flux-dev` / `qwen-image` / `qwen-image-edit` 중 선택 — `image-generation` 툴 스키마의 `model` enum 필드 (`rag-patches/patch_librechat_sd_model.js`) 가 LLM 에 노출.

`COMFYUI_URLS` 에 여러 백엔드를 넣으면 shim 이 라우터로 동작합니다. 시작 시 (그리고 `MODEL_DISCOVERY_TTL_SEC` 마다) 각 노드의 `/object_info` 를 디스커버해 alias→보유 노드 매핑을 캐시합니다. 매 요청마다 ① alias 로 후보 노드를 좁히고 ② 후보들의 `/queue` 깊이를 병렬 probe → 가장 한가한 노드로 `/prompt` 전송 → ③ `prompt_id → 노드` 매핑을 in-memory 로 들고 이후 `/history` polling 과 `/view` fetch 를 같은 노드로 고정. ComfyUI 의 run state 는 노드 stateful 하므로 단순 round-robin LB 는 polling 단계에서 깨지기 때문입니다. 결과: 노드별로 다른 모델 가중치만 받아도 자동으로 그 노드로 라우팅되고, 같은 모델을 여러 노드에 받으면 큐 깊이 LB.

```
이미지:   자체 빌드 (Dockerfile.comfyui-shim — python:3.11-slim, FastAPI + httpx)
포트:     7860 (A1111 호환, 내부 전용)
환경변수: COMFYUI_URLS=<csv>, DEFAULT_MODEL=qwen-image
```

## 네트워크

모든 컨테이너는 `kloudchat` 브리지 네트워크에 속합니다. 외부에 노출되는 포트는 LibreChat (8080), LiteLLM (8000) 두 개입니다.

```
외부 노출: 8080 (LibreChat), 8000 (LiteLLM)
내부 전용: mongodb:27017, meilisearch:7700, vectordb:5432,
          litellm-db:5432, rag_api:8000, searxng:8080,
          code-interpreter:8000, comfyui-shim:7860
호스트:   ollama:11434 (LiteLLM router 가 직접 호출),
          comfyui:8188 (shim upstream)
```

## 요청 흐름 — 채팅

```
브라우저
  → LibreChat (인증·세션 관리)
  → LiteLLM (/v1/chat/completions, LITELLM_SERVICE_KEY, model_name 기준 라우팅)
  ├─ ollama/*       → router (least-busy, 보유 노드만 후보) → Ollama (OLLAMA_URLS 노드) → GPU
  ├─ openai/*       → native (OPENAI_API_KEY 있으면) 또는 OpenRouter
  ├─ anthropic/*    → native 또는 OpenRouter (claude-opus-4.7/4.6, claude-sonnet-4.6, claude-haiku-4.5)
  └─ google/*       → native 또는 OpenRouter (gemini-3.1-pro-preview, gemini-2.5-pro/flash)
```

## 요청 흐름 — RAG

```
파일 업로드
  → LibreChat
  → RAG API (청크 분할 + 임베딩)
  → LiteLLM (router) → Ollama 노드 (bge-m3 보유 노드만)
  → pgvector (벡터 저장)

질문
  → LibreChat
  → RAG API (쿼리 임베딩 + Hybrid 검색)
    ├─ pgvector (시맨틱 검색)
    └─ MeiliSearch (키워드 검색)
  → 결과 병합 → LiteLLM (router) → Ollama 노드 (답변 생성)
```

## 요청 흐름 — 이미지 생성 / 편집

```
브라우저 / 에이전트 (예: qwen3.6:35b)
  → LibreChat (image-generation 툴, model="<alias>")
  → comfyui-shim:7860 (/sdapi/v1/txt2img | /sdapi/v1/img2img)
       │  ① override_settings.sd_model_checkpoint 로 워크플로 선택
       │     flux-schnell / flux-dev / qwen-image / qwen-image-edit
       │  ② 워크플로 JSON 에 프롬프트·시드·CFG·해상도 주입
       │  ③ (img2img 만) /upload/image 로 입력 이미지 업로드
  → ComfyUI native (host.docker.internal:8188 또는 원격 노드, /prompt 큐)
  → GPU (NVIDIA)
  → /history/<prompt_id> 폴링 + /view 로 결과 회수
  ← base64 PNG 반환 → LibreChat 채팅에 인라인 표시

에이전트는 모델당 1개 (이름 = 모델 태그, 예: `qwen3.6:35b`). 통합 toolset 으로 image-generation 포함 — 사용자 의도 (`빠르게`/`고품질`/`텍스트 포함`) 에 따라 LLM 드라이버가 alias 직접 선택.
```

## 스케일 고려사항

단일 서버 온프레미스 배포를 기준으로 설계됐습니다. 동시 사용자가 많아지면 다음 병목이 발생합니다:

1. **Ollama**: 단일 프로세스, 요청 큐잉 — LiteLLM `num_workers` 설정으로 완화
2. **GPU VRAM**: LLM · ComfyUI 공유 — [GPU 메모리 가이드](gpu-memory.md) 참고
3. **MongoDB**: 기본 단일 노드 — 필요 시 replica set 전환
