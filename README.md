# KloudChat

온프레미스 환경에서 운영하는 오픈소스 기반 AI 플랫폼. LibreChat + LiteLLM 위에 Ollama / OpenRouter / native API (OpenAI, Anthropic, Google) 를 묶어 채팅·RAG·이미지 생성·코드 실행을 한 곳에서 제공합니다.

## 빠른 시작

본인 환경에 맞는 시나리오 하나를 골라 따라가면 됩니다:

- **A — 로컬 Ollama 만**: 자체 GPU 가 있을 때. 무료 + 데이터 외부 안 나감. 로컬이 기준선. 단, 모델 다운로드 시간/디스크 100GB+ 필요.
- **B — OpenRouter 만**: GPU 없을 때. OpenRouter API 키 1개로 GPT/Claude/Gemini 다 쓸 수 있음. 5분 안에 띄움. 단, 사용량당 과금 + 데이터는 OpenRouter 경유.
- **C — 로컬 Ollama + OR**: 가장 흔한 셋업. GPU 로 로컬 모델 + OR 키 하나로 frontier 모델 같이. 민감 대화는 로컬로, 고난이도 작업만 OR 경유.
- **D — 풀 하이브리드**: native API 키(OpenAI/Anthropic/Google) + OR + 로컬 Ollama 셋이 공존. native 키 있는 provider 는 native 로, 없으면 OR fallback, 로컬 모델도 동시 노출. 이미 native 계정이 있고 per-provider 청구가 필요할 때.

`setup.sh` 의 사전체크: **reachable Ollama 노드 또는 OPENROUTER_API_KEY 둘 중 하나는 있어야 합니다.** 둘 다 없으면 0단계에서 중단.

### 공통 첫 단계

```bash
git clone https://github.com/boanlab/KloudChat.git && cd KloudChat
./scripts/gen-env.sh        # .env 생성 (시크릿 자동 채움)
```

### A 로컬 Ollama 만 — 자체 GPU

GPU 가 compose 호스트와 같은 머신이면:

```bash
# 1. (선택) FLUX.1-dev 받을 거면 .env 에 HF_TOKEN 먼저 채우기
$EDITOR .env    # HF_TOKEN=hf_...   (없으면 flux-dev 만 빠지고 나머지 이미지 모델은 그대로)

# 2. GPU 호스트에 Ollama + ComfyUI 설치 + 모델 다운로드
./scripts/install-ollama.sh
./scripts/download-ollama-models.sh             # GPU 자동 감지 추천 셋
./scripts/install-comfyui.sh                    # 이미지 생성 쓸 거면
./scripts/download-image-models.sh              # 기본 셋 + (HF_TOKEN 있으면) +flux-dev

# 3. setup + admin (외부 API 키는 빈 칸으로 둠, OLLAMA_URLS/COMFYUI_URLS 는 기본값 그대로)
./scripts/setup.sh --yes
./scripts/manage.sh user create --id admin@example.com --name '관리자' --username admin --password '비번8자이상'
```

GPU 가 별도 노드면:

```bash
# 1. 각 GPU 노드에서 — flux-dev 쓸 거면 HF_TOKEN 환경변수로 전달
./scripts/install-ollama.sh
./scripts/download-ollama-models.sh
./scripts/install-comfyui.sh
HF_TOKEN=hf_... ./scripts/download-image-models.sh      # HF_TOKEN 생략 시 flux-dev 만 빠짐

# 2. compose 호스트의 .env 에서 노드 가리키기
$EDITOR .env
#   OLLAMA_URLS=http://gpu-node-1:11434,http://gpu-node-2:11434
#   COMFYUI_URLS=http://gpu-node-1:8188,http://gpu-node-2:8188

# 3. setup
./scripts/setup.sh --yes
```

> 멀티 노드일 때 LibreChat 메뉴에 등장하는 모델 = **어느 노드에라도 pull 된 모델**(union). 같은 모델을 여러 노드에 받으면 LiteLLM router 가 노드 간 LB.

### B OpenRouter 만 — GPU 없음

```bash
# 1. .env 의 OPENROUTER_API_KEY 채우기 + 나머지는 그대로
$EDITOR .env
#   OPENROUTER_API_KEY=sk-or-v1-...

# 2. setup (Ollama 노드는 unreachable warn 만 뜨고 진행됨)
./scripts/setup.sh --yes

# 3. admin 생성
./scripts/manage.sh user create \
  --id admin@example.com --name '관리자' --username admin --password '비번8자이상'
```

→ LibreChat http://localhost:8080. gpt-5.5, claude-opus-4.7, gemini-3.1-pro-preview 등 OR 라우팅 모델이 메뉴에 나옴.


> 이미지 생성·RAG 임베딩은 로컬 모델이 필요해 이 시나리오에선 비활성. 활성하려면 A 또는 C.

### C 로컬 Ollama + OR — 가장 흔한 셋업

A 의 로컬 GPU 위에 B 의 OR 단일 키를 얹어 frontier 모델까지 함께 운영. native 계정 0개.

```bash
# 1. (선택) FLUX.1-dev 받을 거면 .env 에 HF_TOKEN 먼저 채우기
$EDITOR .env
#   OPENROUTER_API_KEY=sk-or-v1-...
#   OLLAMA_URLS=http://host.docker.internal:11434       (또는 원격 노드 csv)
#   COMFYUI_URLS=http://host.docker.internal:8188       (이미지 생성 쓸 거면)
#   HF_TOKEN=hf_...

# 2. GPU 호스트에서 Ollama + ComfyUI
./scripts/install-ollama.sh
./scripts/download-ollama-models.sh
./scripts/install-comfyui.sh
./scripts/download-image-models.sh

# 3. setup + admin
./scripts/setup.sh --yes
./scripts/manage.sh user create --id admin@example.com --name '관리자' --username admin --password '비번8자이상'
```

LibreChat 메뉴에는 OR 라우팅 모델(gpt-5.5, claude-opus-4.7, gemini-3.1-pro-preview 등) + 로컬 Ollama 모델(qwen3.5/3.6, llama3.x/4, nemotron3, qwen3-coder-next 등)이 함께 노출. `MODEL_OR_FREE` 매핑이 활성화된 Ollama 카탈로그 모델은 Ollama 에 pull 됐으면 로컬, 안 됐으면 OR free 로 fallback (기본 비활성, 필요 시 `lib.sh` 에서 활성화).

### D 풀 하이브리드 — native API + OR + 로컬 Ollama

이미 OpenAI/Anthropic/Google 계정이 있어 per-provider 청구가 필요할 때. `.env` 에서:

```dotenv
OPENAI_API_KEY=sk-...               # 있는 만큼만 채움
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
OPENROUTER_API_KEY=sk-or-...        # 없는 provider 의 fallback 으로
OLLAMA_URLS=http://gpu-node-1:11434 # 로컬 Ollama 노드
HF_TOKEN=hf_...                     # flux-dev 받을 거면
```

라우팅 규칙: native 키가 있는 provider 는 native 로 (`openai/gpt-5.5`), 없는 provider 는 OR fallback (`openrouter/anthropic/claude-opus-4.7`), Ollama 노드에 pull 된 모델은 로컬로 (`ollama/qwen3.6:35b`). `MODEL_OR_FREE` 매핑이 활성화된 카탈로그 모델은 Ollama 우선, 없으면 OR free 로 fallback.

자세한 매트릭스는 [docs/models.md](docs/models.md#라우팅-결정-매트릭스) 참고.

## 동작 방식 한 눈에

```
1. ./scripts/gen-env.sh        → .env 생성 (시크릿 자동, 외부 키는 사용자가 채움)
2. (선택) GPU 노드에서 install-* + download-*
3. ./scripts/setup.sh           → .env + 노드 discovery 로 모델 매트릭스 결정 → docker compose up
4. ./scripts/manage.sh user create  → admin 생성, 끝
```

## 기능

| 기능 | 컴포넌트 |
|---|---|
| 멀티 LLM 채팅 | LibreChat + LiteLLM (native API + Ollama + OpenRouter 통합) |
| RAG (문서 기반 답변) | pgvector + MeiliSearch (Hybrid Search), bge-m3 임베딩 |
| 웹 검색 | SearXNG |
| 코드 실행 샌드박스 | LibreCodeInterpreter |
| HWP/PDF/DOCX 업로드 | LibreChat RAG API |
| 이미지 생성 | ComfyUI + A1111 shim — Qwen-Image(-Edit), FLUX.1 (dev/schnell) |
| URL fetch / 수학 | MCP 서버 (mcp-server-fetch · mcp-sympy · mcp-server-math) — uvx 로 stdio spawn |
| 팀·사용자·예산 관리 | LiteLLM + `scripts/manage.sh` |

## 지원 환경

| 환경 | 가능한 시나리오 | 비고 |
|---|---|---|
| Linux x86_64, GPU 없음 | B (OR-only) | 채팅만 가능. 이미지 / RAG 임베딩은 로컬 모델 필요 → 비활성 |
| Linux x86_64 + NVIDIA GPU | A / B / C / D | 전체 시나리오 |
| Linux aarch64 — DGX Spark (GB10) | A / B / C / D | 전체 시나리오. arm64 + CUDA 12.8 자체 빌드된 ComfyUI |

## 아키텍처

```
[사용자] → LibreChat (:8080)
            ↓
         LiteLLM (:8000) ──→ Ollama 노드들 (OLLAMA_URLS, model_name 별 least-busy LB)
            ├─ native API (OpenAI/Anthropic/Google)
            └─ OpenRouter

         RAG API → LiteLLM → bge-m3 임베딩 → pgvector + MeiliSearch (Hybrid)
         comfyui-shim → ComfyUI (COMFYUI_URLS)
         SearXNG / code-interpreter
```

더 자세한 다이어그램과 컴포넌트 설명은 [docs/overview.md](docs/overview.md).

## 다음에 읽을 것

처음 띄울 때는 위 빠른 시작만 따라하면 됩니다. 막힐 때 / 더 깊이 보고 싶을 때:

- [사전 요구사항](docs/prerequisites.md) — 하드웨어/소프트웨어 체크리스트
- [환경변수 레퍼런스](docs/env-reference.md) — `.env` 변수 전체
- [모델 설정](docs/models.md) — 카탈로그 + 라우팅 매트릭스 + 모델 추가법
- [코딩 에이전트 연동](docs/coding-agents.md) — Claude Code / Codex 를 로컬 `qwen3-coder-next` 로 구동
- [브랜딩 커스터마이징](docs/branding.md) — 로고 / 파비콘 / PWA / 엔드포인트 아이콘 교체
- [아키텍처 상세](docs/overview.md) — 컴포넌트별 동작
- [GPU 메모리 가이드](docs/gpu-memory.md) — 시나리오별 VRAM 점유
- [Ollama 튜닝](docs/ollama-tuning.md)

CLI 사용법은 `./scripts/manage.sh` (인자 없이 실행 시 도움말).

## 라이선스

MIT — 자세한 내용은 [LICENSE](LICENSE) 참고.
