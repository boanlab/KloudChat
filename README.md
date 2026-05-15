# KloudChat

온프레미스 환경에서 운영하는 오픈소스 기반 AI 플랫폼.

## 기능

| 기능 | 컴포넌트 |
|---|---|
| 멀티 LLM 채팅 | LibreChat + LiteLLM + Ollama |
| RAG (문서 기반 답변) | pgvector + MeiliSearch (Hybrid Search) |
| 웹 검색 | SearXNG |
| 코드 실행 샌드박스 | LibreCodeInterpreter |
| HWP/PDF/DOCX 파일 업로드 | LibreChat RAG API |
| 이미지 생성 | ComfyUI + A1111 shim — SDXL, Qwen-Image(-Edit), FLUX.1 (dev/schnell) |
| 팀·사용자·예산 관리 | LiteLLM + CLI 스크립트 |

## 지원 환경

| 환경 | 채팅·RAG·검색·코드 | 이미지 (ComfyUI) |
|---|:---:|:---:|
| Linux x86_64 + NVIDIA GPU | ✅ | ✅ |
| Linux aarch64 — **DGX Spark (GB10)** | ✅ | ✅ |

`./scripts/setup.sh` 가 아키텍처·GPU 를 자동 감지해서 사용 가능한 서비스만 띄웁니다.

### 분산 배포 — compose 호스트와 GPU 노드 분리

`.env` 의 csv 한 줄로 원격 GPU 노드들을 가리킬 수 있습니다.

- **Ollama**: `OLLAMA_URLS=http://gpu-node-1:11434,http://gpu-node-2:11434` — nginx (`ollama-lb`) 가 `least_conn` 으로 분산. RAG 임베딩도 동일 경로.
- **ComfyUI**: `COMFYUI_URLS=http://gpu-node-1:8188,http://gpu-node-2:8188` — shim 이 매 요청 `/queue` 깊이로 가장 한가한 노드 선택, `prompt_id → 노드` 매핑 유지.

각 GPU 노드에서 `./scripts/install-ollama.sh` 또는 `./scripts/install-comfyui.sh` 실행 후 compose 호스트 `.env` 갱신 → `setup.sh --yes`.

## 빠른 시작

`setup.sh` 는 stack 구성 + 백엔드 discovery 만 하고 모델 다운로드는 하지 않습니다. **필수 전제**: `.env` 의 `OPENROUTER_API_KEY` 또는 `OLLAMA_URLS` reachable 노드(모델 보유) 중 하나는 있어야 합니다. 둘 다 없으면 0단계에서 중단.

### 단일 호스트 (compose + GPU 같은 머신)

```bash
git clone https://github.com/boanlab/KloudChat.git && cd KloudChat

# 1. .env 생성 후 키 채우기 (OPENAI/ANTHROPIC/GEMINI/OPENROUTER/HF_TOKEN, OLLAMA_URLS, COMFYUI_URLS 등)
./scripts/gen-env.sh
$EDITOR .env

# 2. 모델 호스트 (Ollama + ComfyUI) 설치 + 가중치 (OR-only 로 갈 거면 생략 가능)
./scripts/install-ollama.sh
./scripts/download-ollama-models.sh        # GPU 자동 감지 → 추천 셋
./scripts/install-comfyui.sh
./scripts/download-image-models.sh         # 기본 셋 + HF_TOKEN 있으면 flux-dev 추가

# 3. compose stack 구성 + 백엔드 discovery + 기동 + init
./scripts/setup.sh --yes

# 4. admin 사용자 생성 (LibreChat + LiteLLM + 키 + agent 자동)
./scripts/manage.sh user create \
  --id admin@example.com --name '관리자' --username admin --password '비번8자이상'
```

### 분산 (compose 호스트 ≠ GPU 노드)

각 GPU 노드에서 위 2번 (`install-*` + `download-*`) 실행 → compose 호스트의 `.env` 에 `OLLAMA_URLS` / `COMFYUI_URLS` 를 csv 로 설정 → `setup.sh` 가 모든 백엔드의 모델 intersection 으로 discovery.

LibreChat: http://localhost:8080  
LiteLLM: http://localhost:8000

## 아키텍처

```
[사용자]
  └─ 채팅 / 에이전트 / 이미지 → LibreChat (:8080)

[LLM 게이트웨이]
  └─ LiteLLM (:8000) ─→ ollama-lb (nginx, least_conn) ─→ Ollama (호스트, OLLAMA_URLS)

[데이터 레이어]
  ├─ pgvector      — 시맨틱 검색
  ├─ MeiliSearch   — BM25 키워드 검색 (Hybrid RAG)
  └─ MongoDB       — 대화 이력

[검색 / 코드 실행]
  ├─ SearXNG          — 웹 검색
  └─ code-interpreter — 코드 실행 샌드박스

[Linux + NVIDIA GPU]
  ├─ ComfyUI       — 이미지 생성 (SDXL, Qwen-Image, Qwen-Image-Edit, FLUX.1 dev/schnell)
  └─ comfyui-shim  — A1111 호환 어댑터 (LibreChat 내장 image-generation 툴 연동)
```

## 문서

- [사전 요구사항](getting-started/prerequisites.md)
- [환경변수 레퍼런스](docs/env-reference.md)
- [모델 설정](docs/models.md)
- [GPU 메모리 가이드](docs/gpu-memory.md)
- [Ollama 튜닝 가이드](docs/ollama-tuning.md)
- [아키텍처 상세](docs/overview.md)

CLI 사용법은 `./scripts/manage.sh` (인자 없이 실행 시 도움말 출력).

## 라이선스

MIT — 자세한 내용은 [LICENSE](LICENSE) 참고.
