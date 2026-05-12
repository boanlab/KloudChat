# 아키텍처 상세

## 전체 구조

```
┌─────────────────────────────────────────────────────┐
│  호스트 (Linux / macOS)                              │
│                                                     │
│  Ollama (:11434) ─── GPU 직접 접근 (NVIDIA / Metal)  │
│     ↑  host.docker.internal:11434                   │
│  ┌─────────────────────────────────────────────┐   │
│  │  Docker Network: kloudchat                  │   │
│  │                                             │   │
│  │  LiteLLM (:8000) ←──────────────────────────────── 외부 요청
│  │    │         └→ litellm-db (postgres)       │   │
│  │    │                                        │   │
│  │  LibreChat (:8080) ←────────────────────────────── 브라우저
│  │    ├→ mongodb                               │   │
│  │    ├→ meilisearch                           │   │
│  │    ├→ rag_api                               │   │
│  │    │    └→ vectordb (pgvector)              │   │
│  │    ├→ searxng                               │   │
│  │    ├→ code-interpreter                      │   │
│  │    └→ tts      (openedai-speech, multi-arch)│   │
│  │                                             │   │
│  │  [Linux + NVIDIA GPU — amd64/arm64]         │   │
│  │    ├→ comfyui       (이미지 생성)           │   │
│  │    └→ comfyui-shim  (A1111 어댑터)          │   │
│  │  [Linux + amd64 + NVIDIA GPU 전용]          │   │
│  │    └→ whisper       (STT)                   │   │
│  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

## 컴포넌트별 역할

### LibreChat
사용자 대면 채팅 인터페이스. 에이전트 빌더, 파일 업로드, 웹 검색, 음성 I/O, 이미지 생성 요청을 통합합니다. LiteLLM 을 커스텀 OpenAI 호환 엔드포인트로 등록해 모든 LLM 요청을 라우팅합니다.

### LiteLLM
LLM 게이트웨이. Ollama 로컬 모델과 (선택적으로) 클라우드 API 를 단일 OpenAI 호환 엔드포인트로 추상화합니다. 팀·사용자·가상 키 기반 예산 관리를 담당합니다. Anthropic Messages API 프록시로도 동작해 Claude Code 를 로컬 모델에 연결합니다.

### Ollama
LLM 런타임. Docker 내부가 아닌 **호스트에서 직접 실행**됩니다. 모델은 기본적으로 `~/.ollama/models` 에 저장됩니다. LiteLLM 은 `host.docker.internal:11434` (`.env` 의 `OLLAMA_API_BASE`) 로 접근합니다.

### RAG API
문서 기반 답변 (RAG) 파이프라인. 업로드된 파일을 청크로 분할해 pgvector 에 임베딩을 저장합니다. 임베딩은 `bge-m3` (다국어) 를 Ollama 호스트에 직접 호출 — LiteLLM 을 경유하지 않습니다. `HYBRID_SEARCH=true` 설정으로 pgvector (시맨틱) 와 MeiliSearch (BM25 키워드) 결과를 조합해 검색합니다.

### MeiliSearch
전문 검색 엔진. LibreChat 의 대화 내용 검색과 RAG Hybrid Search 의 키워드 검색 백엔드로 사용됩니다.

### SearXNG
프라이버시 중심 메타 검색 엔진. LibreChat 웹 검색 기능의 백엔드입니다. 외부 포트를 열지 않고 내부 네트워크 (`http://searxng:8080`) 로만 접근합니다.

### Whisper (STT)
faster-whisper (CTranslate2) 기반 OpenAI Audio API 호환 서버. LibreChat 의 STT 기능에 `/v1/audio/transcriptions` 로 직접 연결됩니다.

CTranslate2 사용으로 PyTorch CUDA 커널 의존도가 낮아 신형 GPU (예: Blackwell sm_120) 호환성이 좋습니다.

```
이미지: fedirz/faster-whisper-server:latest-cuda
모델:   Systran/faster-whisper-large-v3 (기본)
포트:   8000 (OpenAI Audio API)
```

### TTS (openedai-speech)
다중 백엔드 TTS 서버. OpenAI Audio API 호환 (`/v1/audio/speech`).

- **piper** (`tts-1`): 영어 음성 빠름 (~1초). voice: alloy / echo / nova / shimmer 등.
- **xtts_v2** (`tts-1-hd`): 다국어 + 한국어 자연스러움. voice: korean / korean-female / english-hd 등.
  cold-start 시 ~36초, warm 호출 ~4초.

multi-arch (amd64 + arm64) + CPU 가능 — base 서비스 (GPU 불필요).
voice 매핑은 `tts-config/voice_to_speaker.yaml` 에서 정의.

```
이미지: ghcr.io/matatonic/openedai-speech:latest
포트:   8000 (OpenAI Audio API)
```

### ComfyUI (이미지 생성)
노드 기반 디퓨전 런타임. KloudChat 은 SDXL · Qwen-Image (텍스트→이미지) · Qwen-Image-Edit (이미지+프롬프트→편집) 세 파이프라인을 워크플로 템플릿으로 미리 정의해 둡니다. ComfyUI 컨테이너는 Linux + NVIDIA GPU 에서 amd64/arm64 모두 동작 (DGX Spark 포함). 컨테이너는 외부 포트를 노출하지 않으며 LibreChat 은 항상 `comfyui-shim` 을 경유합니다.

```
이미지: 자체 빌드 (Dockerfile.comfyui — nvidia/cuda 12.6 베이스, multi-arch)
포트:   8188 (ComfyUI 본 API, 내부 전용)
가중치: comfyui/models/{checkpoints,unet,clip,vae}
```

### comfyui-shim (A1111 어댑터)
LibreChat 의 내장 stable-diffusion 툴은 A1111 형식 (`/sdapi/v1/txt2img`, `/sdapi/v1/img2img`) 만 알아듣지만 ComfyUI 는 워크플로 JSON 기반이라 둘이 호환되지 않습니다. 이 shim 이 A1111 요청을 받아 워크플로 템플릿에 프롬프트·시드·CFG 를 끼워넣은 뒤 ComfyUI `/prompt` 큐에 넣고 결과 이미지를 base64 로 반환합니다. 모델은 요청의 `override_settings.sd_model_checkpoint` 값으로 `sdxl` / `qwen-image` / `qwen-image-edit` 중 선택.

```
이미지: 자체 빌드 (Dockerfile.comfyui-shim — python:3.11-slim, FastAPI)
포트:   7860 (A1111 호환, 내부 전용)
환경변수: COMFYUI_URL=http://comfyui:8188, DEFAULT_MODEL=sdxl
```

## 네트워크

모든 컨테이너는 `kloudchat` 브리지 네트워크에 속합니다. 외부에 노출되는 포트는 LibreChat (8080), LiteLLM (8000) 두 개입니다. GPU 가속 서비스 (ComfyUI · comfyui-shim · Whisper) 와 multi-arch TTS 모두 포트를 외부에 노출하지 않습니다.

```
외부 노출: 8080 (LibreChat), 8000 (LiteLLM)
내부 전용: mongodb:27017, meilisearch:7700, vectordb:5432,
          litellm-db:5432, rag_api:8000, searxng:8080,
          code-interpreter:8000, tts:8000, whisper:8000,
          comfyui:8188, comfyui-shim:7860
호스트:   ollama:11434 (host.docker.internal 경유)
```

## 요청 흐름 — 채팅

```
브라우저
  → LibreChat (인증·세션 관리)
  → LiteLLM (/v1/chat/completions, LITELLM_SERVICE_KEY)
  → Ollama (http://host.docker.internal:11434)
  → GPU
```

## 요청 흐름 — RAG

```
파일 업로드
  → LibreChat
  → RAG API (청크 분할 + 임베딩)
  → rag_api → Ollama (bge-m3, LiteLLM 우회)
  → pgvector (벡터 저장)

질문
  → LibreChat
  → RAG API (쿼리 임베딩 + Hybrid 검색)
    ├─ pgvector (시맨틱 검색)
    └─ MeiliSearch (키워드 검색)
  → 결과 병합 → LiteLLM → Ollama (답변 생성)
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
  → comfyui:8188 (/prompt 큐)
  → GPU (NVIDIA, amd64 또는 arm64)
  → /history/<prompt_id> 폴링 + /view 로 결과 회수
  ← base64 PNG 반환 → LibreChat 채팅에 인라인 표시
```

## 스케일 고려사항

단일 서버 온프레미스 배포를 기준으로 설계됐습니다. 동시 사용자가 많아지면 다음 병목이 발생합니다:

1. **Ollama**: 단일 프로세스, 요청 큐잉 — LiteLLM `num_workers` 설정으로 완화
2. **GPU VRAM**: LLM · Whisper · ComfyUI 공유 — [GPU 메모리 가이드](gpu-memory.md) 참고
3. **MongoDB**: 기본 단일 노드 — 필요 시 replica set 전환
