# KloudChat

온프레미스 환경에서 운영하는 **오픈소스 기반 AI 플랫폼**.

- **스택**: **LibreChat** + **LiteLLM** 위에 **vLLM** 과 **OpenRouter** 를 묶어 채팅·RAG·이미지 생성·코드 실행을 한 곳에서 제공
- **Commercial 모델** (OpenAI/Anthropic/Google): **OpenRouter** 경유
- **Super Agent** (단일패스 — `local/gemma-4-26b` 챗+아티팩트): 로컬 `gemma-4-26b` vLLM **또는** OpenRouter 키 중 하나만 있어도 주력 에이전트로 My Agents 상단에 노출
- **GPU 없을 때**: 로컬 두뇌(`local/gemma-4-26b`)를 OpenRouter 동일 모델로 직결
- **빌드 분기**: NVFP4 노드는 NVFP4, RTX4090 은 AWQ-int4 빌드로 동작

## 빠른 시작

**로컬 GPU 기본 + OpenRouter 폴백** — 단일 셋업.

- 로컬 vLLM/ComfyUI 가 있으면 그것으로 구동 (무료·온프레미스, 데이터 외부 유출 없음)
- **노드 다운 시 OpenRouter 동일 모델로 자동 폴백**
- 로컬 GPU 가 아예 없으면 OpenRouter 상용 모델만으로 5분 내 동작

| 구성 | 역할 | 필요한 것 |
|---|---|---|
| **로컬 GPU** (기본) | chat(gemma-4-26b)·Deep Research(122b)·이미지(FLUX)·RAG(bge-m3) 를 vLLM/ComfyUI 로 — 무료·온프레미스 | NVIDIA GPU + 150 GB+ 디스크 ([prerequisites](docs/operator/prerequisites.md)) |
| **OpenRouter** (폴백·보강) | 상용 프런티어(OpenAI/Anthropic/Google/DeepSeek 등) + **로컬 노드 다운 시 동일 모델 자동 폴백** | OpenRouter API 키 |

- 최소 하나는 필수 — 보통 **둘 다** 채워 로컬 우선 + OR 폴백/상용 보강으로 사용
- OR 키: GPU 있으면 선택, 없으면 필수

### 1. 공통

```bash
git clone https://github.com/boanlab/KloudChat.git && cd KloudChat
./scripts/gen-env.sh        # .env 생성 (시크릿 자동, 외부 키는 비어있음)
$EDITOR .env                # 키 채우기 (아래 표 참고)
```

### 2. .env 채우기

| 변수 | 값 | 비고 |
|---|---|---|
| `OPENROUTER_API_KEY` | `sk-or-v1-...` | 상용 모델 + 로컬 폴백용. 로컬 GPU 있으면 선택, 없으면 필수 |
| `HF_TOKEN` | (선택) | FLUX.1-dev 등 gated repo |
| `NODES_VLLM` / `NODES_COMFYUI` / `NODES_WHISPER` | ssh 타겟 csv (`user@host,...`) | 로컬 GPU 쓸 때만 |

- **사용자는 노드(ssh 타겟)만 입력.** `VLLM_*_URL` / `COMFYUI_URLS` 는 직접 기재하지 않음
  - `setup.sh all` 안의 scheduler apply 가 placement 결정에 따라 `.env` 에 **자동 기록** (`WHISPER_URLS` 는 `NODES_WHISPER` 에서 유도)
  - gen-config 가 이를 읽어 LiteLLM/shim 구성
- **단일노드**: `NODES_VLLM=user@localhost` 처럼 자기 자신을 박아두면 scheduler 가 cap fit 검증 + 워크로드 셋 자동 결정
- **수동 관리**: scheduler 를 끄고 URL 을 직접 관리하려면 `KLOUDCHAT_SKIP_SCHEDULER=1` + `VLLM_*_URL` 직접 설정 — [scheduler.md](docs/operator/scheduler.md)

### 3. GPU 호스트 설치 (로컬 GPU 쓸 때)

> **설치 경로는 둘 중 하나만 선택** (둘 다 하면 중복 실행):
> - **(a)** 이 노드에서 아래 스크립트를 직접 실행
> - **(b)** `.env` 의 `NODES_*` 에 ssh 타겟을 채우고 §4 `setup.sh all` 이 해당 노드들에 install 스크립트를 ssh 로 대행
>
> **모델 다운로드(`download-*-models.sh`)는 어느 경우든 항상 직접** — `setup.sh all` 은 install 만 대행, download 는 미수행.

```bash
# vLLM 은 모든 GPU 노드 (GB10 / RTX4090 / RTX5090 / PRO5000 / PRO6000) 에서 동작한다.
# 컨테이너 띄움은 다음 단계의 setup.sh all 안의 scheduler apply 가 담당.
./scripts/install-vllm.sh && ./scripts/download-vllm-models.sh
# ComfyUI 는 PRO5000 / PRO6000 / GB10 에서 동작 (RTX4090 / RTX5090 은 hard fail)
./scripts/install-comfyui.sh && ./scripts/download-image-models.sh
# 오디오 전사(STT) backend — Note Taker 오디오 업로드 + 자막 없는 YouTube 전사 (선택)
./scripts/install-whisper.sh && ./scripts/download-whisper-models.sh
```

### 4. 메인 스택

```bash
./scripts/setup.sh all
```

- `setup.sh all` 이 **admin 계정도 `.env` 의 `ADMIN_EMAIL`/`ADMIN_PW`/`ADMIN_ID` 로 자동 생성** (`ADMIN_PW` 는 `gen-env.sh` 가 랜덤 발급)
- 즉 §1 의 `gen-env.sh` 만 돌렸으면 별도 계정 생성 단계 불필요

**로그인 / 확인**:

- LibreChat http://localhost:8080 에 **`.env` 의 `ADMIN_EMAIL` + `ADMIN_PW`** 로 로그인
- 모델 dropdown 에 **Super Agent** (로컬 gemma-4-26b vLLM 띄웠거나 OR 키 있으면 — GPU 없는 OR 전용 배포에서도 OR 두뇌로 노출) 또는 commercial 모델이 보이면 정상
- 안 보이면 [장애 대응](docs/operator/troubleshooting.md)
- 사용자 추가: `./scripts/manage.sh user create --id <email> --name <name> --username <user> --password <8+>`

## 기능

| 기능 | 컴포넌트 |
|---|---|
| 멀티 LLM 채팅 | LibreChat + LiteLLM |
| 로컬 LLM 런타임 | vLLM (docker; GB10 / RTX4090 / RTX5090 / PRO5000 / PRO6000) |
| 단일패스 에이전트 | Super Agent (super-agent-shim — `local/gemma-4-26b` 챗+아티팩트 단일패스) |
| 깊은 검색 / 추론 | Deep Research (`local/qwen3.5:122b`, LDR MCP — [tools.md](docs/operator/tools.md#mcp-servers)) |
| 발표자료 생성 | Slide Studio (`local/qwen3.5:122b`, 자체완결 HTML 슬라이드 아티팩트) |
| 회의록 / 전사 | Note Taker (`local/gemma-4-26b`, 오디오 「텍스트로 업로드」 → 내장 STT 전사 → 문서) |
| 학술 figure | Paper Banana (`local/gemma-4-26b`, paperbanana MCP — OpenRouter) |
| RAG | pgvector + MeiliSearch + bge-m3 |
| 웹 검색 / 스크레이핑 | SearXNG + valkey limiter / Crawl4AI shim |
| 코드 실행 | LibreCodeInterpreter |
| 이미지 생성 | Image Studio (ComfyUI + A1111 shim — FLUX.1 dev/schnell Q8 GGUF / 외부 Nano Banana·GPT Image) |
| 비디오 생성 | Video Studio (로컬 LTX-Video 기본 / 외부 OpenRouter Veo·Sora) |
| MCP 도구 | stdio (fetch_url / time / usage / youtube / smart_search / export_deck / generate_video / paperbanana) + HTTP (deep_research) |
| 운영 관리 | LiteLLM 가상 키 + `scripts/manage.sh` (팀/사용자/예산) |

## 지원 환경

| 환경 | 동작 |
|---|---|
| Linux amd64, GPU 없음 | OpenRouter 전용 (상용 모델) |
| Linux amd64 + NVIDIA GPU (RTX4090 / RTX5090 / PRO5000 / PRO6000) | 로컬 GPU + OR 폴백 |
| Linux arm64 — GB10 | 로컬 GPU + OR 폴백 |

자세한 아키텍처 다이어그램은 [docs/operator/overview.md](docs/operator/overview.md).

## 다음에 읽을 것

문서는 읽는 사람 기준으로 나뉜다 — **사용자**(플랫폼으로 무엇을 어떻게)와 **운영자**(배포·운영·튜닝).

### 사용자 관점 — 플랫폼으로 무엇을, 어떻게 ([docs/user/](docs/user/))

- [에이전트 활용 가이드](docs/user/) — Super Agent · Image Studio · Video Studio · Slide Studio · Note Taker · Deep Research (도구·MCP 활용 + 예시 프롬프트)
- [코딩 에이전트 연동](docs/user/coding-agents.md) — Claude Code / Codex 를 본인 키로 연결

### 운영자 관점 — 배포 · 운영 · 튜닝 ([docs/operator/](docs/operator/))

**첫 셋업 / 막힐 때:**
- [사전 요구사항](docs/operator/prerequisites.md) — 하드웨어/소프트웨어 체크리스트
- [장애 대응](docs/operator/troubleshooting.md) — 컨테이너 죽음, vLLM cold-start fail, 로그 위치
- [환경변수 레퍼런스](docs/operator/env-reference.md) — `.env` 변수 전체

**멀티노드 / 모델 운영:**
- [scheduler](docs/operator/scheduler.md) — 멀티노드 vLLM placement 자동화 (단일노드면 안 봐도 됨)
- [모델 설정](docs/operator/models.md) — 카탈로그 + 라우팅 매트릭스 + 모델 추가법
- [vLLM 튜닝](docs/operator/vllm-tuning.md) — ctx 옵션 + gpu_memory_utilization
- [라우팅 정책](docs/operator/routing-policy.md) — instruction / 도구 / 모델 / shim / scheduler 의 변경 위치 한 곳 인덱스

**참고:**
- [도구](docs/operator/tools.md) — Built-in / MCP / image 백엔드 + agent 별 도구 매트릭스
- [Slide Studio 내보내기](docs/operator/slide-export.md) — 덱 PDF/PPTX export 서비스 + export_deck MCP
- [정밀 문서 검색](docs/operator/smart-search.md) — smart_search MCP (hybrid retrieve + rerank)
- [Video Studio 운영](docs/operator/video-studio.md) — 동작 구조 · 과금 · 로컬 LTXV 설정
- [아키텍처 상세](docs/operator/overview.md) — 컴포넌트별 동작 + 요청 흐름
- [GPU 메모리 가이드](docs/operator/gpu-memory.md) — 로컬 GPU VRAM 점유
- [성능 측정](docs/operator/performance.md) — 실측 throughput 매트릭스
- [브랜딩 커스터마이징](docs/operator/branding.md) — 로고 / 파비콘 / PWA

> CLI 스크립트(`setup.sh` / `manage.sh` / `manage-vllm.sh` / `usage-priorities.sh` / `build-push-images.sh` / `tune-host.sh`)는 **인자 없이 실행하면 각자 도움말을 출력**한다.

## 라이선스

MIT — 자세한 내용은 [LICENSE](LICENSE) 참고.
