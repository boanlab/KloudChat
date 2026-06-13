# 모델 설정

> 라우팅 매트릭스 · 카탈로그 위치 · 이미지 모델 매핑. 처음 띄우는 거면 [README](../../README.md).

## 카탈로그 (lib.sh 단일 진실)

`scripts/lib.sh` 가 모델 셋업의 진실 소스:

| 변수 | 역할 |
|---|---|
| `OPENAI_MODELS` / `ANTHROPIC_MODELS` / `GOOGLE_MODELS` / `DEEPSEEK_MODELS` / `XAI_MODELS` / `PERPLEXITY_MODELS` / `META_MODELS` / `QWEN_MODELS` | commercial provider 그룹 (전부 OpenRouter 경유, native 직결 X) |
| `VLLM_MODELS` | vLLM 로컬 모델 (chat / Deep Research / coder / embed) |
| `OPENAI_EMBED_CATALOG` | OR 임베딩 (RAG 폴백) |
| `MODEL_PRICE_IN_PM` / `MODEL_PRICE_OUT_PM` | USD/1M tokens (LiteLLM spend 추적) |

**`MODEL_PRICE_*` 단가 정책**:

- **commercial** — OR catalog 가격
- **로컬** (`local/*` + `bge-m3`) — **무료(0)**. 자체 GPU 호스팅이라 토큰 과금 없음
- **OR 폴백(노드 다운/과부하) 발동 시** — 실제 서빙한 OR twin deployment 단가로 과금. **LiteLLM** 이 폴백된 deployment 기준으로 cost 계산 → 로컬(0)과 OR 폴백(유료) 자동 분리
  - OR fallback twin 단가는 `gen-litellm-config.sh::emit_or_fallback` 인자(예: gemma 0.06/0.33)에 박힘
- **`text-embedding-3-small`**(bge 대체용 OR 임베딩) — OR 경유라 유료

**config 자동 생성**: `gen-litellm-config.sh` / `gen-librechat-config.sh` 가 위 정의 + 환경을 합쳐 marker 사이 자동 생성.

| 생성 스크립트 | 대상 파일 | marker |
|---|---|---|
| `gen-litellm-config.sh` | `litellm-config.yaml` | `KLOUDCHAT_AUTOGEN` |
| `gen-librechat-config.sh` | `librechat.yaml` | `KLOUDCHAT_MODELS` |

## 모델 셋

vLLM 이 유일한 로컬 LLM 백엔드. 모델은 두 아키텍처에서 동작:

- **amd64** — 디스크리트 카드 (RTX4090 / RTX5090 / PRO5000 / PRO6000), 표준 amd64 vLLM 이미지 (`vllm/vllm-openai:cu129-nightly`)
- **arm64** — GB10 (128GB unified memory), arm64 vLLM 이미지 (`vllm/vllm-openai:nightly-aarch64`)

| 모델 (alias) | 컨테이너 | 포트 | quant | 역할 |
|---|---|---|---|---|
| `local/gemma-4-26b` | `vllm-gemma26` | 8001 | NVFP4 (RTX4090 은 AWQ-int4) | 메인 chat + 아티팩트 |
| `local/qwen3.5:122b` | `vllm-qwen122b` | 8002 | NVFP4 | Deep Research / Slide Studio / 정밀 (MoE A10B) |
| `local/qwen3-coder-next` | `vllm-codernext` | 8003 | FP8 | 코딩 어시스트 (litellm 등록만, 상시 상주 아님 — 온디맨드) |
| `bge-m3` | `vllm-bge-m3` | 8004 | — | embed (pooling runner) |

**`gemma-4-26b` quant 분기**:

- 기본 — **NVFP4** (GB10 / RTX5090 / PRO5000 / PRO6000)
- **RTX4090**(NVFP4 미지원) — **AWQ-int4** 빌드 자동 선택. 별칭(`--served-model-name`)은 동일
- tool-parser `gemma4` (reasoning-parser 없음 — non-thinking, `--max-num-batched-tokens ≥ 16384`)

**parser 매핑**:

| 모델 | tool-parser | reasoning-parser |
|---|---|---|
| `gemma-4-26b` | `gemma4` | 없음 (non-thinking) |
| `qwen3.5:122b` | `qwen3_xml` | `qwen3` |
| `qwen3-coder-next` | `qwen3_coder` | — |

## 라우팅 매트릭스

### Commercial (OpenRouter 단일 경로)

| OR 키 | 결과 |
|---|---|
| 있음 | `<provider>/<id>` model_name 으로 OR route 1개 |
| 없음 | 미등록 |

`model_name` 은 canonical (`openai/gpt-5.5`), `litellm_params.model` 은 `openrouter/<provider>/<id>`.

### vLLM (로컬)

- **등록** — `.env` 의 `VLLM_*_URL` 채우면 같은 model_name 으로 등록
- **디스커버리** — `gen-litellm-config.sh` 가 각 URL `/v1/models` polling → 보유 노드만 등록
- **multi-node** — 같은 model_name 의 deployment 를 노드 수만큼 emit. **LiteLLM router** `least-busy` 가 진행 요청 적은 노드 선택

**운영(profile 기동)**:

- **chat profile** — `setup.sh all` 의 scheduler apply 담당 (cap fit + config 자동)
- **coder profile** — `./scripts/manage-vllm.sh up --coder` 로 별도 노드 격리

자세히는 [GPU 메모리 가이드](gpu-memory.md#노드-클래스별-권장-워크로드).

| 모델 | 노드 클래스 | 비고 |
|---|---|---|
| `gemma-4-26b` | 모든 단일 GPU | 메인 chat+아티팩트 (NVFP4, RTX4090 은 AWQ-int4) |
| `qwen3.5:122b` | PRO6000 / GB10 | Deep Research / Slide Studio / 정밀 (NVFP4 MoE A10B, 128K ctx) |
| `qwen3-coder-next` | 노드 격리 | coder 전용 (FP8, 온디맨드) |
| `bge-m3` | PRO5000+ | embed |

**두뇌 역할**:

- **`gemma-4-26b`** — Super Agent 단일패스 챗+아티팩트 두뇌 + title / memory 모델
- **Image/Video Studio 두뇌는 122b** — gemma 의 `generate_image` 툴콜 신뢰도가 낮아 이전

자세히는 [Super Agent 동작](overview.md#컴포넌트별-역할).

### 로컬 → OR 폴백

로컬 vLLM 챗/코딩 모델은 노드 다운·과부하(에러·timeout·cooldown, `num_retries` 소진 후) 시 **OpenRouter 동일 모델**로 자동 폴백. `router_settings.fallbacks` 매핑:

| 로컬 (primary) | OR 폴백 (paid) |
|---|---|
| `local/gemma-4-26b` | `google/gemma-4-26b-a4b-it` ($0.06/$0.33) |
| `local/qwen3.5:122b` | `qwen/qwen3.5-122b-a10b` ($0.26/$2.08) |
| `local/qwen3-coder-next` | `qwen/qwen3-coder-next` ($0.11/$0.80) |

- **emit 조건** — `gen-litellm-config.sh::emit_or_fallback` 가 로컬 primary 배포된 경우만(URL 있음) OR twin deployment emit (model_name = OR slug)
- **dropdown 비노출** — twin 은 `gen-librechat-config.sh` 가 emit 하지 않아 **LibreChat dropdown 엔 비노출**(폴백 전용)
- **비용 주의** — 폴백 발동은 **OR 유료 egress**. 로컬 노드가 자주 죽으면 비용 누수

### 모델별 max_model_len

- **디스커버리** — `gen-litellm-config.sh` 가 각 deployment 의 `/v1/models` 에서 `max_model_len` 디스커버리 → LiteLLM `max_input_tokens` 로 emit (unified memory 의 KV swap 회피)
- **실패 폴백** — 노드 unreachable 로 디스커버리 실패 시 단일 폴백값 `CTX_FALLBACK`(32768)

아래 `ctx` 는 **현 클러스터의 디스커버리값** — 노드/구성 바뀌면 달라짐.

| 모델 | ctx (현 클러스터) | 의도 |
|---|---|---|
| `gemma-4-26b` | 32K | chat+아티팩트 + titleConvo |
| `qwen3.5:122b` | 128K | Deep Research / 정밀 |
| `qwen3-coder-next` | 32K | 긴 코드 |

### OR 임베딩 (RAG 폴백)

| 조건 | 결과 |
|---|---|
| vLLM bge-m3 가용 | bge-m3 (로컬, 무료) |
| bge-m3 미가용 + OR 키 | text-embedding-3-small 자동 swap (~$0.02/1M) |
| bge-m3 미가용 + OR 키 없음 | RAG 비활성 |

**`setup.sh litellm`**(또는 `all`)의 vLLM probe 가 `.env::EMBEDDINGS_MODEL` 자동 swap (이미 값 있으면 존중).

## commercial 디폴트

```bash
OPENAI_MODELS=(gpt-5.5 gpt-5 gpt-5-mini gpt-5-nano)
ANTHROPIC_MODELS=(claude-opus-4.8 claude-opus-4.7 claude-opus-4.6 claude-sonnet-4.6 claude-haiku-4.5)
GOOGLE_MODELS=(gemini-3.1-pro-preview gemini-2.5-pro gemini-2.5-flash gemma-4-31b-it)
# 추가 OR provider (가격 = OR pricing_skus, $/M in→out)
DEEPSEEK_MODELS=(deepseek-v4-pro)        # $0.43/$0.87
XAI_MODELS=(grok-4.3)                    # $1.25/$2.50
PERPLEXITY_MODELS=(sonar)                # $1.00/$1.00
META_MODELS=(llama-4-maverick)           # $0.15/$0.60 (표시 meta/…, OR 라우트는 meta-llama/…)
QWEN_MODELS=(qwen3.5-397b-a17b)          # $0.39/$2.34
VLLM_MODELS=( [gemma-4-26b]=nvidia/gemma-4-26b-A4B-it-NVFP4 [gemma-4-26b-awq]=cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit [qwen3.5-122b-a10b]=Qwen/Qwen3.5-122B-A10B-NVFP4 [qwen3-coder-next]=Qwen/Qwen3-Coder-Next-FP8 [bge-m3]=BAAI/bge-m3 )
```

`qwen3-coder-next` 는 Claude Code / Codex CLI 의 로컬 coder backend — [코딩 에이전트 연동](../user/coding-agents.md).

## 셋업 흐름

```bash
# 1. .env — OPENROUTER_API_KEY + NODES_VLLM + NODES_COMFYUI + HF_TOKEN (URL 은 scheduler 가 자동 기록)
./scripts/gen-env.sh && $EDITOR .env

# 2. vLLM 모델 다운로드 (로컬 GPU 안 쓰면 생략 가능)
./scripts/download-vllm-models.sh           # GPU class 자동 감지
./scripts/download-vllm-models.sh all       # --help 참고

# 3. config 생성 + 기동
./scripts/setup.sh all   # 분리 노드면 setup.sh litellm / setup.sh librechat
```

## 이미지 생성

- **백엔드** — 로컬 ComfyUI + A1111 shim **또는** OpenRouter 외부(Gemini Nano Banana / GPT Image)
- **ComfyUI** — 항상 native systemd (`./scripts/install-comfyui.sh` 가 `/opt/comfyui/{venv,app}` + `/var/lib/comfyui/output`)
- **가중치 경로** — `/opt/comfyui/app/ComfyUI/models/`

아키텍처 + 비교는 [tools.md](tools.md#이미지-백엔드), [overview.md](overview.md#요청-흐름--이미지-생성--편집), [gpu-memory.md](gpu-memory.md).

### 가중치

```bash
./scripts/download-image-models.sh                # 기본 + HF_TOKEN 있으면 flux-dev
./scripts/download-image-models.sh all            # 전부
./scripts/download-image-models.sh flux-shared flux-schnell-gguf
```

| alias | 크기 | 비고 |
|---|---|---|
| `flux-shared` | ~10 GB | T5-XXL + CLIP-L + AE VAE (dev/schnell 공유) |
| `flux-schnell` | ~22 GB | FP16, 4 step |
| `flux-schnell-gguf` | ~12 GB | Q8 |
| `flux-dev` | ~22 GB | FP16, gated (HF_TOKEN 필수), ~20 step |
| `flux-dev-gguf` | ~12 GB | Q8, gated |

- **GGUF Q8** — weight 만 quantize 라 품질 무손실 + VRAM 1/3. GB10 unified-memory 에서 ComfyUI admission 마진 확보
- **노드별 디스커버리** — 한 노드만 받은 모델도 shim/router 가 자동 라우팅

### 모델 선택

- **alias 지정** — `generate_image` 툴 스키마 `model` enum(5종 — flux-schnell / flux-dev / nano-banana / nano-banana-2 / gpt-image-2)에서 LLM 이 직접 지정 (`rag-patches/patch_librechat_sd_model.js`)
- **shim 분기** — `override_settings.sd_model_checkpoint` 로 변환
  - 로컬 — ComfyUI 워크플로
  - 외부 — LiteLLM `modalities=["image","text"]`
- **미지정 시** — shim 의 `DEFAULT_MODEL`. ComfyUI backend 있으면 `flux-schnell`, 없으면 외부 `nano-banana`

**에이전트 공유 모델**: ADMIN 한 벌만 만들어 전 사용자에게 read-only 공유(사용자별 복제 아님). 생성은 ADMIN 전용, 일반 사용자는 사용(USE)만 가능:
- `user create --name ... --username ... --password ...` — LibreChat 계정 + LiteLLM 키 프로비저닝
- `agent sync` — ADMIN 소유로 카탈로그 upsert (`local|openai|anthropic|google`) in-place rename + public viewer ACL 부여 + 같은 이름의 비-ADMIN 복제본 정리. 사용자가 직접 만든 고유 이름 에이전트는 보존.

**일반 agent**: 모델 1개당 에이전트 1개, 이름은 LiteLLM 라우트 (`local/gemma-4-26b`, `openai/gpt-5-mini` 등). 도구는 provider 무관 동일 — builtin 3 (`execute_code` / `file_search` / `web_search`) + MCP 5 (`fetch_url` / `time` / `youtube` / `usage` / `smart_search`). `generate_image` 미부착. `qwen3-coder-next` 는 litellm 등록만 — UI dropdown 비노출 + agent 미생성 (외부 코딩 클라이언트 전용).

**Functional agent** (Super Agent 가용 시 등장 — `gemma-4-26b` chat backend 있어야):
- `Super Agent` — `local/auto-route` (단일패스 — `local/gemma-4-26b` 챗+아티팩트). 동일 도구 + promoted.
- `Image Studio` — `local/qwen3.5:122b` 직결. `generate_image` 만 부착.
- `Deep Research` — `local/qwen3.5:122b` (PLAIN) 직결. `execute_code` + `file_search` + `fetch_url` + `deep_research` + `smart_search` (학술 ReAct + 인용; `web_search` 는 의도적 제외 — deep_research 가 다중소스 검색을 내부 수행).
- `Slide Studio` — `local/qwen3.5:122b` 직결. `export_deck` MCP(PDF/PPTX 내보내기)만 — 본문은 발표 디자이너 프롬프트로 자체완결형 HTML 발표자료를 아티팩트로 직접 저작.
- `Note Taker` — `local/gemma-4-26b` 직결. `file_search` 부착. 오디오를 「텍스트로 업로드」하면 내장 STT(whisper)가 전사 → 회의록/강의노트/보고서 작성. Productivity 카테고리.
- `Video Studio` — `local/qwen3.5:122b` 직결. `generate_video` MCP(텍스트→비디오, 기본=로컬 LTX-Video, 명시 시 OpenRouter Veo/Sora). Productivity 카테고리, promoted(Top Picks). 자세히는 [video-studio.md](../user/video-studio.md).
- `Paper Banana` — `local/gemma-4-26b` 직결. `file_search` + `paperbanana` MCP(학술 figure 생성). Research 카테고리, promoted 아님(Top Picks 제외).

**GPU 없는 OR 전용 배포**: 로컬 두뇌(`local/gemma-4-26b`·`local/qwen3.5:122b`)가 OR 동일모델로 직결 등록(`gen-litellm-config::emit_brain`)돼 `super_agent_eligible` 이 OR 키만으로 참 → **Super Agent·Slide Studio·Deep Research·Paper Banana·모델별 이미지/비디오 agent 가 OR 두뇌로 동작**. capability 별 게이팅:

- **Image/Video Studio** — `image_gen_eligible`(로컬 ComfyUI **또는** OR 키). GPU 없으면 comfyui-shim 이 외부 OR 모델(기본 nano-banana / veo-lite, 유료)로 라우팅. 로컬 FLUX/LTXV 는 GPU 전용.
- **Note Taker / youtube 전사** — `whisper_eligible`(`WHISPER_URLS`). whisper 는 **GPU 전용(OR 폴백 없음)** — 없으면 Note Taker 미생성 + youtube 도구 미부착.
- RAG 임베딩은 bge-m3 대신 OR `text-embedding-3-small` 로 자동 swap.

→ GPU 유무와 무관하게 동작하며, GPU 없을 땐 OR 단가만 발생(이미지 ~$0.04/장·비디오 ~$0.08/초·챗 OR 단가).

매트릭스 + MCP 매핑은 [tools.md](tools.md#agent-별-도구-매트릭스).

**노드 분리**:

- **`qwen3-coder-next`** — 다른 워크로드와 co-fit 안 됨 → 단독 노드 점유 + 온디맨드 기동. chat / Deep Research / bge / ComfyUI 와 동거 불가 ([gpu-memory.md](gpu-memory.md#노드-클래스별-권장-워크로드))
- **`comfyui-shim` / `whisper-shim`** — coder 노드를 후순위로 강등 (서비스 설치는 가능, HA fallback)

**Dropdown 정렬**:

- **순서** — Super Agent → Image Studio → Video Studio → Slide Studio → Note Taker → Deep Research → Paper Banana → 상용 LLM(openai → anthropic → google → deepseek → x-ai → perplexity → meta → qwen) → 모델별 이미지·비디오 에이전트(Nano Banana … Sora 2 Pro)
- **Closed Models 카테고리** — 같은 순서(상용 LLM 먼저, 이미지/비디오는 꼬리)
- **강제 방식** — `manage.sh` 의 `DISPLAY_ORDER` 인덱스(0=상단)만큼 `updatedAt` 을 미래로 띄워 정렬 강제 (LibreChat 의 `{updatedAt:-1, _id:1}`). `createdAt` 은 그대로

**새 대화 기본**:

- **설정** — `librechat.yaml` 의 `modelSpecs` 가 `prioritize:true` + 단일 `super-agent` spec(`showIcon*:false`)으로 **새 대화 기본 = Super Agent** 만 설정
- **핀 없음** — 보이는 핀(사이드바/헤더 아이콘) 미설정. 에이전트는 AI Agent Store/모델 선택기로 선택
- **선택 범위** — `addedEndpoints:[agents]` 로 My Agents + 상용 LLM 선택 가능
- **id 동기화** — `agent_id` 는 실제 id(`${ENV}` 치환 안 됨) 박음. `agent sync` 가 각 spec 의 label 로 현재 id 자동 동기화(wipe/재설정 안전)

자세히는 [tools.md AI Agent Store/운영 정책](tools.md#ai-agent-store--운영-정책).

### MCP + Built-in

- **Built-in 4** — `execute_code`, `file_search`, `web_search`, `generate_image`
- **MCP 9** — `fetch_url`, `time`, `youtube`, `usage`, `smart_search`, `export_deck`, `generate_video`, `paperbanana`, `deep_research`

매트릭스 + 추가 절차는 [tools.md](tools.md).

## RAG 임베딩

- **경로** — RAG API → LiteLLM (`RAG_OPENAI_BASEURL=${LITELLM_URL}/v1`) → router → `bge-m3` 보유 vLLM 노드 (여러 대면 `least-busy` LB)
- **요건** — 최소 1개 노드에 `bge-m3` 가용 필수
