# 아키텍처 상세

## 전체 구조

```
┌─────────────────────────────────────────────────────┐
│  호스트 (Ubuntu)                                     │
│                                                     │
│  Ollama (:11434) ─── GPU 직접 접근                   │
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
│  │    └→ code-interpreter                      │   │
│  │                                             │   │
│  │  [amd64 전용]                               │   │
│  │    ├→ whisper  (STT)                        │   │
│  │    ├→ kokoro   (TTS)                        │   │
│  │    └→ sdnext   (이미지 생성)                 │   │
│  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

## 컴포넌트별 역할

### LibreChat
사용자 대면 채팅 인터페이스. 에이전트 빌더, 파일 업로드, 웹 검색, 음성 I/O,
이미지 생성 요청을 통합합니다. LiteLLM을 커스텀 OpenAI 호환 엔드포인트로 등록해
모든 LLM 요청을 라우팅합니다.

### LiteLLM
LLM 게이트웨이. Ollama 로컬 모델과 (선택적으로) 클라우드 API를 단일 OpenAI 호환
엔드포인트로 추상화합니다. 팀·사용자·가상 키 기반 예산 관리를 담당합니다.
Anthropic Messages API 프록시로도 동작해 Claude Code를 로컬 모델에 연결합니다.

### Ollama
LLM 런타임. Docker 내부가 아닌 **호스트에서 직접 실행**됩니다.
모델은 기본적으로 `~/.ollama/models`에 저장됩니다.
LiteLLM은 `host.docker.internal:11434` (`.env`의 `OLLAMA_API_BASE`)로 접근합니다.

### RAG API
문서 기반 답변(RAG) 파이프라인. 업로드된 파일을 청크로 분할해 pgvector에
임베딩을 저장합니다. `HYBRID_SEARCH=true` 설정으로 pgvector(시맨틱)와
MeiliSearch(BM25 키워드) 결과를 조합해 검색합니다.

### MeiliSearch
전문 검색 엔진. LibreChat의 대화 내용 검색과 RAG Hybrid Search의 키워드 검색
백엔드로 사용됩니다.

### SearXNG
프라이버시 중심 메타 검색 엔진. LibreChat 웹 검색 기능의 백엔드입니다.
외부 포트를 열지 않고 내부 네트워크(`http://searxng:8080`)로만 접근합니다.

### Whisper (STT)
faster-whisper (CTranslate2) 기반 OpenAI Audio API 호환 서버.
LibreChat 의 STT 기능에 `/v1/audio/transcriptions` 로 직접 연결됩니다.

CTranslate2 사용으로 PyTorch CUDA 커널 의존도가 낮아 신형 GPU(예: Blackwell sm_120) 호환성이 좋습니다.

```
이미지: fedirz/faster-whisper-server:latest-cuda
모델:   Systran/faster-whisper-large-v3 (기본)
포트:   8000 (OpenAI Audio API)
```

### Kokoro (TTS)
경량 고품질 TTS 모델(82M 파라미터). OpenAI TTS API 호환 서버로 실행됩니다.

GPU 변형 이미지가 신형 GPU(sm_120 등)에서 PyTorch 커널 미지원으로 시작 실패하는 이슈가 있어,
CPU 변형을 사용합니다. 영어 음성 4종(af_sky, af_bella, am_adam, am_michael) 만 제공.
한국어 음성 출력이 필요하면 MeloTTS / XTTS-v2 같은 한국어 지원 OSS TTS 로 별도 교체 필요.

```
이미지: ghcr.io/remsky/kokoro-fastapi-cpu:latest
포트:   8880
```

### SD.Next
A1111(Stable Diffusion WebUI)의 활발히 유지보수되는 포크.
`/sdapi/v1/txt2img` 엔드포인트로 LibreChat 과 직접 연동됩니다.

```
이미지: vladmandic/sdnext-cuda:latest
포트:   7860 (A1111 호환)
```

## 네트워크

모든 컨테이너는 `kloudchat` 브리지 네트워크에 속합니다.
외부에 노출되는 포트는 LibreChat(8080), LiteLLM(8000) 두 개입니다.
amd64 전용 서비스(Whisper·Kokoro·SD.Next)는 포트를 외부에 노출하지 않습니다.

```
외부 노출: 8080 (LibreChat), 8000 (LiteLLM)
내부 전용: mongodb:27017, meilisearch:7700, vectordb:5432,
          litellm-db:5432, rag_api:8000, searxng:8080,
          code-interpreter:8000, whisper:9000, kokoro:8880, sdnext:7860
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
  → LiteLLM → Ollama (nomic-embed-text)
  → pgvector (벡터 저장)

질문
  → LibreChat
  → RAG API (쿼리 임베딩 + Hybrid 검색)
    ├─ pgvector (시맨틱 검색)
    └─ MeiliSearch (키워드 검색)
  → 결과 병합 → LiteLLM → Ollama (답변 생성)
```

## 스케일 고려사항

단일 서버 온프레미스 배포를 기준으로 설계됐습니다.
동시 사용자가 많아지면 다음 병목이 발생합니다:

1. **Ollama**: 단일 프로세스, 요청 큐잉 — LiteLLM `num_workers` 설정으로 완화
2. **GPU VRAM**: LLM·Whisper·SD.Next 공유 — [GPU 메모리 가이드](gpu-memory.md) 참고
3. **MongoDB**: 기본 단일 노드 — 필요 시 replica set 전환
