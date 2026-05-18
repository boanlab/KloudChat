# KloudChat

온프레미스 환경에서 운영하는 오픈소스 기반 AI 플랫폼. LibreChat + LiteLLM 위에 Ollama와 OpenRouter를 묶어 채팅, RAG, 이미지 생성, 코드 실행 등을 한 곳에서 제공합니다. Commercial 모델 (OpenAI/Anthropic/Google)은 OpenRouter를 통해 제공합니다.

## 빠른 시작

본인 환경에 맞는 시나리오 하나를 골라 따라가면 됩니다:

- **A — 로컬 Ollama 만**: 자체 GPU 클러스터가 있을 때. 무료 + 데이터 외부 안 나감. 로컬이 기준선. 단, 모델 다운로드 시간/디스크 100GB+ 필요.
- **B — OpenRouter 만**: 자체 GPU 클러스터가 없을 때. OpenRouter API 키 1개로 GPT/Claude/Gemini 다 쓸 수 있음. 5분 안에 띄움. 단, 사용량당 과금 + 데이터는 OpenRouter 경유.
- **C — 로컬 Ollama + OR**: 가장 흔한 셋업. GPU 로 로컬 모델 + OR 키 하나로 frontier 모델 같이. 민감 대화는 로컬로, 고난이도 작업만 OR 경유.

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
./scripts/download-image-models.sh              # 기본 셋 + (HF_TOKEN 있으면 flux-dev)
./scripts/install-whisper.sh                    # 자막 없는 YouTube 영상 전사 쓸 거면
./scripts/download-whisper-models.sh            # (선택) prewarm — 첫 호출 lazy-load 회피

# 3. setup + admin (OPENROUTER_API_KEY 빈 칸 두면 commercial 모델 미등록, OLLAMA_URLS/COMFYUI_URLS는 기본값 그대로)
./scripts/setup.sh
./scripts/manage.sh user create --id admin@example.com --name '관리자' --username admin --password '비번8자이상'
```

GPU 가 별도 노드면:

```bash
# 1. 각 GPU 노드에서 — flux-dev 쓸 거면 HF_TOKEN 환경변수로 전달
./scripts/install-ollama.sh
./scripts/download-ollama-models.sh
./scripts/install-comfyui.sh
HF_TOKEN=hf_... ./scripts/download-image-models.sh      # HF_TOKEN 생략 시 flux-dev만 빠짐
./scripts/install-whisper.sh                            # YouTube 음성 인식 폴백 쓸 거면
./scripts/download-whisper-models.sh                    # (선택) prewarm — 첫 호출 lazy-load 회피

# 2. compose 호스트의 .env 에서 노드 가리키기
$EDITOR .env
#   OLLAMA_URLS=http://gpu-node-1:11434,http://gpu-node-2:11434
#   COMFYUI_URLS=http://gpu-node-1:8188,http://gpu-node-2:8188
#   WHISPER_URLS=http://gpu-node-1:9000,http://gpu-node-2:9000   # whisper 멀티노드 쓸 거면

# 3. setup
./scripts/setup.sh
```

> 멀티 노드일 때 LibreChat 메뉴에 등장하는 모델 = **어느 노드에라도 pull 된 모델**(union). 같은 모델을 여러 노드에 받으면 LiteLLM router가 노드 간 LB.

### B OpenRouter 만 — GPU 없음

```bash
# 1. .env 의 OPENROUTER_API_KEY 채우기 + 나머지는 그대로
$EDITOR .env
#   OPENROUTER_API_KEY=sk-or-v1-...

# 2. setup (Ollama 노드는 unreachable warn 만 뜨고 진행됨)
./scripts/setup.sh

# 3. admin 생성
./scripts/manage.sh user create \
  --id admin@example.com --name '관리자' --username admin --password '비번8자이상'
```

→ LibreChat http://localhost:8080. gpt-5.5, claude-opus-4.7, gemini-3.1-pro-preview 등 OR 라우팅 모델이 메뉴에 나옴.

> 이미지 생성·RAG 임베딩은 로컬 모델이 필요해 이 시나리오에선 비활성. 활성하려면 A 또는 C.

### C 로컬 Ollama + OR — 가장 흔한 셋업

A의 로컬 GPU 위에 B의 OR 단일 키를 얹어 frontier 모델까지 함께 운영.

```bash
# 1. (선택) FLUX.1-dev 받을 거면 .env 에 HF_TOKEN 먼저 채우기
$EDITOR .env
#   OPENROUTER_API_KEY=sk-or-v1-...
#   OLLAMA_URLS=http://host.docker.internal:11434       (또는 원격 노드 csv)
#   COMFYUI_URLS=http://host.docker.internal:8188       (이미지 생성 쓸 거면)
#   HF_TOKEN=hf_...

# 2. GPU 호스트에서 Ollama + ComfyUI + Whisper
./scripts/install-ollama.sh
./scripts/download-ollama-models.sh
./scripts/install-comfyui.sh
./scripts/download-image-models.sh
./scripts/install-whisper.sh
./scripts/download-whisper-models.sh            # (선택) prewarm — 첫 호출 lazy-load 회피

# 3. setup + admin
./scripts/setup.sh
./scripts/manage.sh user create --id admin@example.com --name '관리자' --username admin --password '비번8자이상'
```

LibreChat 메뉴에는 OR 라우팅 모델(gpt-5.5, claude-opus-4.7, gemini-3.1-pro-preview 등) + 로컬 Ollama 모델(qwen3.5/3.6, llama3.1/3.3, nemotron3, qwen3-coder-next 등)이 함께 노출. Ollama 카탈로그 모델은 어느 노드든 pull 돼 있을 때만 메뉴에 등장.

자세한 매트릭스는 [docs/models.md](docs/models.md#라우팅-결정-매트릭스) 참고.

## 동작 방식 한 눈에

```
1. ./scripts/gen-env.sh        → .env 생성 (시크릿 자동, 외부 키는 사용자가 채움)
2. (선택) GPU 노드에서 install-* + download-*
3. ./scripts/setup.sh           → .env + 노드 discovery로 모델 매트릭스 결정 → docker compose up
4. ./scripts/manage.sh user create  → admin 생성, 끝
```

## 기능

| 기능 | 컴포넌트 |
|---|---|
| 멀티 LLM 채팅 | LibreChat + LiteLLM |
| RAG | pgvector + MeiliSearch + bge-m3 |
| 웹 검색 | SearXNG |
| 코드 실행 | LibreCodeInterpreter |
| 파일 업로드 | LibreChat RAG API |
| 이미지 생성 | ComfyUI + A1111 shim |
| MCP 도구 | stdio (fetch_url / time / math / math_basic / usage / youtube) + HTTP (deep_research 사이드카) |
| 운영 관리 | LiteLLM + `scripts/manage.sh` |

이미지 생성은 Qwen-Image / Qwen-Image-Edit / FLUX.1 (dev, schnell) — [tools.md#이미지-백엔드](docs/tools.md#이미지-백엔드). RAG API 는 HWP / PDF / DOCX 등 업로드 처리. MCP 서버 전체 목록은 [tools.md](docs/tools.md). 운영 관리는 팀·사용자·예산 (LiteLLM 가상 키).

## 지원 환경

| 환경 | 시나리오 |
|---|---|
| Linux x86_64, GPU 없음 | B (OR-only) |
| Linux x86_64 + NVIDIA GPU | A / B / C |
| Linux aarch64 — DGX Spark (GB10) | A / B / C |

GPU 없으면 채팅만 가능 — 이미지 / RAG 임베딩은 로컬 모델 필요해서 비활성. ComfyUI · Ollama · Whisper 는 모두 GPU 호스트에 systemd native 로 설치 (컨테이너 아님) — DGX Spark (GB10 arm64) 도 동일, `install-comfyui.sh` 가 GPU class 감지해 Blackwell 노드만 torch 2.9.1 (NVFP4 dtype 노출) 로 설치.

## 아키텍처

```
[사용자] → LibreChat (:8080)
            ↓
         LiteLLM (:8000) ──→ Ollama 노드들 (OLLAMA_URLS, model_name 별 least-busy LB)
                          └─→ OpenRouter (commercial: gpt-*/claude-*/gemini-*)

         RAG API → LiteLLM → bge-m3 임베딩 → pgvector + MeiliSearch (Hybrid)
         comfyui-shim → ComfyUI (COMFYUI_URLS)
         whisper-shim → Whisper (WHISPER_URLS, inflight+VRAM 기준 라우팅)
         youtube MCP → whisper-shim (자막 없는 영상 음성 인식)
         SearXNG / code-interpreter
```

더 자세한 다이어그램과 컴포넌트 설명은 [docs/overview.md](docs/overview.md).

## 다음에 읽을 것

처음 띄울 때는 위 빠른 시작만 따라하면 됩니다. 막힐 때 / 더 깊이 보고 싶을 때:

- [사전 요구사항](docs/prerequisites.md) — 하드웨어/소프트웨어 체크리스트
- [환경변수 레퍼런스](docs/env-reference.md) — `.env` 변수 전체
- [모델 설정](docs/models.md) — 카탈로그 + 라우팅 매트릭스 + 모델 추가법
- [도구](docs/tools.md) — Built-in / MCP / image 백엔드 + 모델별 부착 매트릭스
- [코딩 에이전트 연동](docs/coding-agents.md) — Claude Code / Codex를 로컬 `qwen3-coder-next`로 구동
- [브랜딩 커스터마이징](docs/branding.md) — 로고 / 파비콘 / PWA / 엔드포인트 아이콘 교체
- [아키텍처 상세](docs/overview.md) — 컴포넌트별 동작
- [GPU 메모리 가이드](docs/gpu-memory.md) — 시나리오별 VRAM 점유
- [Ollama 튜닝](docs/ollama-tuning.md)

CLI 사용법은 `./scripts/manage.sh` (인자 없이 실행 시 도움말).

## 라이선스

MIT — 자세한 내용은 [LICENSE](LICENSE) 참고.
