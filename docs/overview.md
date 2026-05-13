# 아키텍처 상세

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
│  │    └→ http://ollama-lb:11434                 │   │
│  │                                             │   │
│  │  ollama-lb (nginx, least_conn)              │ ──┼─→ OLLAMA_URLS (csv)
│  │                                             │   │   ├─ http://host.docker.internal:11434
│  │                                             │   │   ├─ http://gpu-node-1:11434
│  │                                             │   │   └─ http://gpu-node-2:11434
│  │  rag_api ────────────────────────────────────────┘   (각 노드에 동일 모델 pull 필요)
│  │    (bge-m3 임베딩도 LiteLLM 경유로 LB)        │   │
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

LiteLLM 은 `http://ollama-lb:11434` 단일 endpoint 만 호출하고, **nginx (`ollama-lb`) 가 `OLLAMA_URLS` csv 의 백엔드들 사이를 `least_conn` 으로 분산**합니다. `.env` 수정 → `./scripts/gen-nginx-config.sh` → `docker compose restart ollama-lb`.

### RAG API
문서 기반 답변 (RAG) 파이프라인. 업로드된 파일을 청크로 분할해 pgvector 에 임베딩을 저장합니다. 임베딩은 `bge-m3` (다국어) 를 Ollama 호스트에 직접 호출 — LiteLLM 을 경유하지 않습니다. `HYBRID_SEARCH=true` 설정으로 pgvector (시맨틱) 와 MeiliSearch (BM25 키워드) 결과를 조합해 검색합니다.

### MeiliSearch
전문 검색 엔진. LibreChat 의 대화 내용 검색과 RAG Hybrid Search 의 키워드 검색 백엔드로 사용됩니다.

### SearXNG
프라이버시 중심 메타 검색 엔진. LibreChat 웹 검색 기능의 백엔드입니다. 외부 포트를 열지 않고 내부 네트워크 (`http://searxng:8080`) 로만 접근합니다.

### ComfyUI (이미지 생성)
노드 기반 디퓨전 런타임. KloudChat 은 SDXL · Qwen-Image (텍스트→이미지) · Qwen-Image-Edit (이미지+프롬프트→편집) 세 파이프라인을 워크플로 템플릿으로 미리 정의해 둡니다.

ComfyUI 는 항상 **호스트 native** (venv + systemd) 로 실행 — `scripts/install-comfyui.sh` 가 `/opt/comfyui/{venv,app}` + `/var/lib/comfyui/{models,output}` 을 만들고 `:8188` 에 바인딩. compose 호스트와 같은 머신에 설치하든 (`COMFYUI_URLS=http://host.docker.internal:8188`), 원격 GPU 노드(들)에 설치하든 (`COMFYUI_URLS=http://gpu-node-1:8188,...`) 형태는 동일. LibreChat 은 `comfyui-shim` 을 통해서만 접근.

```
설치:     scripts/install-comfyui.sh (PyTorch cu128, ComfyUI master)
포트:     8188 (ComfyUI 본 API, 내부/LAN 전용)
가중치:   /var/lib/comfyui/models/{checkpoints,unet,clip,vae}
```

### comfyui-shim (A1111 어댑터 + 라우터)
LibreChat 의 내장 stable-diffusion 툴은 A1111 형식 (`/sdapi/v1/txt2img`, `/sdapi/v1/img2img`) 만 알아듣지만 ComfyUI 는 워크플로 JSON 기반이라 둘이 호환되지 않습니다. 이 shim 이 A1111 요청을 받아 워크플로 템플릿에 프롬프트·시드·CFG 를 끼워넣은 뒤 ComfyUI `/prompt` 큐에 넣고 결과 이미지를 base64 로 반환합니다. 모델은 요청의 `override_settings.sd_model_checkpoint` 값으로 `sdxl` / `qwen-image` / `qwen-image-edit` 중 선택.

`COMFYUI_URLS` 에 여러 백엔드를 넣으면 shim 이 라우터로 동작합니다: 매 요청마다 후보 노드들의 `/queue` 깊이를 병렬 probe → 가장 한가한 노드로 `/prompt` 전송 → `prompt_id → 노드` 매핑을 in-memory 로 들고 이후 `/history` polling 과 `/view` fetch 를 같은 노드로 고정. ComfyUI 의 run state 는 노드 stateful 하므로 단순 round-robin LB 는 polling 단계에서 깨지기 때문입니다.

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
          code-interpreter:8000, ollama-lb:11434,
          comfyui-shim:7860
호스트:   ollama:11434, comfyui:8188 (둘 다 ollama-lb / shim upstream)
```

## 요청 흐름 — 채팅

```
브라우저
  → LibreChat (인증·세션 관리)
  → LiteLLM (/v1/chat/completions, LITELLM_SERVICE_KEY, model_name 기준 라우팅)
  ├─ ollama/*       → ollama-lb (nginx, least_conn) → Ollama (OLLAMA_URLS 백엔드) → GPU
  └─ openrouter/*   → OpenRouter free tier (gpt-oss 등, 로컬 GPU 0)
```

## 요청 흐름 — RAG

```
파일 업로드
  → LibreChat
  → RAG API (청크 분할 + 임베딩)
  → LiteLLM → ollama-lb → Ollama (bge-m3)
  → pgvector (벡터 저장)

질문
  → LibreChat
  → RAG API (쿼리 임베딩 + Hybrid 검색)
    ├─ pgvector (시맨틱 검색)
    └─ MeiliSearch (키워드 검색)
  → 결과 병합 → LiteLLM → ollama-lb → Ollama (답변 생성)
```

## 요청 흐름 — 이미지 생성 / 편집

```
브라우저 / 에이전트
  → LibreChat (stable-diffusion 툴)
  → comfyui-shim:7860 (/sdapi/v1/txt2img | /sdapi/v1/img2img)
       │  ① override_settings.sd_model_checkpoint 로 워크플로 선택
       │     sdxl / qwen-image / qwen-image-edit
       │  ② 워크플로 JSON 에 프롬프트·시드·CFG·해상도 주입
       │  ③ (img2img 만) /upload/image 로 입력 이미지 업로드
  → ComfyUI native (host.docker.internal:8188 또는 원격 노드, /prompt 큐)
  → GPU (NVIDIA)
  → /history/<prompt_id> 폴링 + /view 로 결과 회수
  ← base64 PNG 반환 → LibreChat 채팅에 인라인 표시
```

## 스케일 고려사항

단일 서버 온프레미스 배포를 기준으로 설계됐습니다. 동시 사용자가 많아지면 다음 병목이 발생합니다:

1. **Ollama**: 단일 프로세스, 요청 큐잉 — LiteLLM `num_workers` 설정으로 완화
2. **GPU VRAM**: LLM · ComfyUI 공유 — [GPU 메모리 가이드](gpu-memory.md) 참고
3. **MongoDB**: 기본 단일 노드 — 필요 시 replica set 전환
