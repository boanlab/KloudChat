# 모델 설정

> 이 문서: 어떤 모델이 어디로 라우팅되는지(라우팅 매트릭스) · 모델 카탈로그를 어디서 수정하는지 · 이미지 모델 매핑. 처음 띄우는 거면 [README](../README.md) 만 보면 됩니다.

## 모델 카탈로그 (lib.sh 단일 진실)

`scripts/lib.sh` 가 모델 셋업의 진실 소스. 주요 자료구조:

| 변수 | 종류 |
|---|---|
| `OPENAI_MODELS` | commercial 카탈로그 |
| `ANTHROPIC_MODELS` | commercial 카탈로그 |
| `GOOGLE_MODELS` | commercial 카탈로그 |
| `OLLAMA_CHAT_CATALOG` | Ollama 채팅 |
| `OLLAMA_EMBED_CATALOG` | Ollama 임베딩 |
| `OPENAI_EMBED_CATALOG` | OR 임베딩 (RAG 폴백) |
| `OLLAMA_DEFAULT_PRIORITY` | 기본 에이전트 우선순위 |
| `MODEL_PRICE_IN_PM` / `MODEL_PRICE_OUT_PM` | USD per 1M tokens |

commercial 3개 (`OPENAI_MODELS` / `ANTHROPIC_MODELS` / `GOOGLE_MODELS`) 는 전부 OpenRouter 경유로 라우팅됩니다. native API 직결은 지원 안 함.

`OLLAMA_CHAT_CATALOG` / `OLLAMA_EMBED_CATALOG` 는 union discovery — 어느 노드든 보유하면 그 노드(들)에 deployment 등록. `OLLAMA_DEFAULT_PRIORITY` 는 기본 에이전트가 어떤 모델로 만들어질지 결정 (이 순서대로 lookup, union 어디든 보유한 첫 모델).

`MODEL_PRICE_*` 는 LiteLLM 의 spend/budget 추적에 사용. Commercial (gpt/claude/gemini) 은 OpenRouter catalog 가격 그대로, 로컬 (`ollama/*` + `bge-m3`) 은 OR 동급 모델 대비 **50% 할인** — 자체 GPU 운영이라 API 마진이 없는 비용 구조 반영. `text-embedding-3-small` 은 OR 경유라 할인 대상 아님.

`gen-litellm-config.sh` / `gen-librechat-config.sh` 가 위 정의 + 환경 (OR 키 + ollama discovery) 을 합쳐서 `litellm-config.yaml` 의 `KLOUDCHAT_AUTOGEN` marker, `librechat.yaml` 의 `KLOUDCHAT_MODELS` marker 사이를 자동 생성.

## 라우팅 결정 매트릭스

### Commercial curated (OpenAI / Anthropic / Google)

OpenRouter 단일 경로. native API 직결은 지원하지 않습니다 — 운영 단순화 + 청구 단일화 목적.

| OR 키 | 등록 결과 |
|---|---|
| 있음 | `<provider>/<id>` model_name 으로 OR route 1개 |
| 없음 | commercial 전부 미등록 |

`model_name` 은 canonical 이름 (`openai/gpt-5.5` 등) 으로 LibreChat 메뉴에 모델당 1개 entry. `litellm_params.model` 은 `openrouter/<provider>/<id>`, `api_key` 는 `os.environ/OPENROUTER_API_KEY`.

### Ollama 카탈로그 (union discovery)

각 노드의 `/api/tags` 응답을 합집합으로 합쳐 모델 → 보유 노드 매핑을 만들고, 모델 하나당 보유 노드 수만큼 같은 `model_name` deployment 를 emit. 노드 1개 unreachable 이면 warn 후 그 노드만 skip.

LiteLLM router 의 `routing_strategy: least-busy` 가 같은 `model_name` 후보들 중 진행 중 요청이 적은 노드를 고름 — 큰 모델은 한 노드만 가지고 있어도 OK, 작은 모델은 여러 노드에 자동 분산.

| 조건 | 등록 결과 |
|---|---|
| 보유 노드 ≥1 | `ollama_chat/<id>` × 보유 노드 수 |
| 보유 0 | 미등록 (commercial 모델로 대체 사용) |

채팅 카탈로그 (`OLLAMA_CHAT_CATALOG`): `qwen3.5:9b`, `qwen3.6:35b`, `llama3.1:8b`, `llama3.3:70b`, `nemotron3:33b`, `qwen3-coder-next:q8_0`. 임베딩 카탈로그 (`OLLAMA_EMBED_CATALOG`): `bge-m3`.

### OR 임베딩 (RAG 폴백)

OR 키 있으면 `OPENAI_EMBED_CATALOG` (기본 `text-embedding-3-small`) 를 LiteLLM 에 등록 — 라우트 `openrouter/openai/<id>`. Ollama 노드에 `bge-m3` 가 0대이면 `setup.sh` 가 `.env` 의 `EMBEDDINGS_MODEL` 을 자동으로 `text-embedding-3-small` 로 swap (이미 다른 값이면 존중). 즉 OR 단독 환경에서도 RAG 동작.

| 조건 | 결과 |
|---|---|
| Ollama bge-m3 ≥1 | bge-m3 사용 (로컬, 무료) |
| Ollama bge-m3 0 + OR 키 | text-embedding-3-small 자동 swap (OR, ~$0.02/1M) |
| Ollama bge-m3 0 + OR 키 없음 | RAG 비활성 |

## commercial curated 디폴트

`lib.sh` 의 배열 — 신규 모델 추가/제거 시 여기와 `MODEL_PRICE_*` 만 갱신:

```bash
OPENAI_MODELS=(gpt-5.5 gpt-5 gpt-5-mini gpt-5-nano)
ANTHROPIC_MODELS=(claude-opus-4.7 claude-opus-4.6 claude-sonnet-4.6 claude-haiku-4.5)
GOOGLE_MODELS=(gemini-3.1-pro-preview gemini-2.5-pro gemini-2.5-flash)
OLLAMA_CHAT_CATALOG=(qwen3.5:9b qwen3.6:35b llama3.1:8b llama3.3:70b nemotron3:33b qwen3-coder-next:q8_0)
OLLAMA_EMBED_CATALOG=(bge-m3)
OLLAMA_DEFAULT_PRIORITY=(qwen3.6:35b qwen3.5:9b)
```

`qwen3-coder-next:q8_0` 는 LibreChat dropdown 에서 자동 노출. Claude Code / OpenAI Codex CLI 에서 LiteLLM 게이트웨이를 통해 로컬 코딩 에이전트로도 사용 가능 — [코딩 에이전트 연동](coding-agents.md) 참고.

## 셋업 흐름

```bash
# 1. .env 채우기 — OPENROUTER_API_KEY + OLLAMA_URLS + COMFYUI_URLS + HF_TOKEN
./scripts/gen-env.sh && $EDITOR .env

# 2. Ollama 노드(들)에서 모델 pull (OR-only 면 생략 가능)
./scripts/download-ollama-models.sh                   # GPU class 자동 감지
./scripts/download-ollama-models.sh qwen3-9b qwen3-35b llama3-8b embed
./scripts/download-ollama-models.sh all               # 옵션: --help

# 3. compose 호스트에서 config 생성 + 기동
./scripts/setup.sh
```

## 이미지 생성

ComfyUI + A1111 shim. ComfyUI 는 항상 native (systemd) 로 실행 — `./scripts/install-comfyui.sh` 가 각 GPU 노드에 설치 (`/opt/comfyui/{venv,app}` + `/var/lib/comfyui/output`). 가중치는 `/opt/comfyui/app/ComfyUI/models/`. 아키텍처는 [overview.md](overview.md#comfyui-이미지-생성), VRAM 점유 / 성능은 [gpu-memory.md](gpu-memory.md).

### 가중치 다운로드

```bash
./scripts/download-image-models.sh                # 기본 셋 + (HF_TOKEN 있으면) flux-dev
./scripts/download-image-models.sh recommended    # 동일 (명시)
./scripts/download-image-models.sh all            # 전부
./scripts/download-image-models.sh qwen-image flux-schnell # 명시 alias
```

기본 셋: `qwen-image + qwen-image-edit + flux-shared + flux-schnell`. `HF_TOKEN` 이 `.env` 에 있으면 `+ flux-dev`.

| alias | 크기 |
|---|---|
| `qwen-image` | ~21 GB |
| `qwen-image-edit` | ~21 GB |
| `flux-shared` | ~10 GB |
| `flux-dev` | ~22 GB |
| `flux-schnell` | ~22 GB |

`qwen-image` 는 Qwen-Image UNet + 인코더 + VAE — 텍스트→이미지에 강하고 한글 텍스트 처리가 좋음. UNet quant 는 GPU class 자동 매핑 (`recommended_image_quant`): Blackwell(gb10/pro/5090) → NVFP4, Ada 4090 → FP8, 그 외 → Q8 GGUF. `qwen-image-edit` 는 Qwen-Image-Edit-2509 — 이미지 편집 용도, NVFP4 변형이 없어 FP8 또는 Q8 GGUF, 인코더/VAE 는 `qwen-image` 와 공유.

`flux-shared` 는 Flux 공유 인코더 (T5-XXL FP16, CLIP-L, AE VAE) — `flux-dev` / `flux-schnell` 둘 다 사용. `flux-dev` 는 FLUX.1-dev FP16 (gated, **HF_TOKEN 필수**) — 최고 품질이지만 ~20 step 으로 느림. `flux-schnell` 은 FLUX.1-schnell FP16 (MIT) — 4 step 으로 빠른 iteration.

GPU VRAM 이 부족한 노드에서 무거운 모델은 받지 마세요 — 명시적 alias 지정. ComfyUI/Ollama 모두 union 디스커버리라 한 노드만 받은 모델도 그대로 활성화되고 shim/router 가 보유 노드로 자동 라우팅.

### 모델 선택 메커니즘

LibreChat 의 `generate_image` 툴 스키마에 `model` enum 필드 (`flux-schnell`, `flux-dev`, `qwen-image`, `qwen-image-edit`) 가 있어 LLM 이 alias 를 직접 지정 (`rag-patches/patch_librechat_sd_model.js` 가 빌드 시 적용). 호출 시 `model="<alias>"` 인자가 페이로드의 `override_settings.sd_model_checkpoint` 로 변환되어 shim 이 워크플로 분기. 미지정 시 shim 의 `DEFAULT_MODEL` (=`qwen-image`).

사용자별 에이전트는 `./scripts/manage.sh` 가 자동 생성:
- `user create --name ... --username ... --password ...` — 신규 유저 풀 프로비저닝 시
- `agent sync` — 기존 유저 전체에 카탈로그 모델로 upsert. 우리 관리 prefix (`ollama|openai|anthropic|google`) 인 agent 이름을 현 spec 으로 자동 rename (in-place, preset agent_id 보존). 사용자 수동 생성 agent 는 건드리지 않음.

모델 1개당 에이전트 1개. 이름 prefix 는 능력 라벨일 뿐 — 실제 builtin 부착은 provider 가 결정합니다. anthropic 만 `generate_image` 가 빠지고 (자사 image API 없음), 나머지 provider 는 4종 builtin (execute_code / file_search / web_search / generate_image) 전부 부착. 자세한 매트릭스 + MCP 매핑은 [도구 문서](tools.md#모델별-도구-매트릭스).

prefix 별 해당 모델:
- `Text` — claude-haiku-4.5
- `Text + Code` — claude-opus-4.7/4.6, claude-sonnet-4.6
- `Text + Image` — gpt-5-mini, gpt-5-nano, gemini-3.1-pro-preview/2.5-pro/flash
- `Text + Image + Code` — gpt-5.5, gpt-5, 전 ollama 모델

`generate_image` 의 `model` arg 는 에이전트 provider 별로 다른 백엔드로 갑니다: ollama 로컬 → ComfyUI alias (`flux-schnell`/`flux-dev`/`qwen-image`/`qwen-image-edit`), openai → `gpt-image-2` (OR 경유), google → `nano-banana` (OR 경유), anthropic → 없음.

기본 preset 은 `OLLAMA_DEFAULT_PRIORITY` 의 첫 매치 (현재 `qwen3.6:35b → qwen3.5:9b`). `llama3.3:70b` 는 카탈로그엔 있지만 priority 후보에선 제외 — selector 에서 명시 선택. `agent sync` 는 기존 default agent 가 카탈로그에서 빠지면 자동으로 새 default 로 reassign (멱등).

### MCP 도구 + Built-in

Built-in (`execute_code`, `file_search`, `web_search`, `generate_image`) 과 MCP 서버 카탈로그 + 모델별 부착 매트릭스 + 추가 절차는 [도구 문서](tools.md) 에 정리. 모델 카탈로그 변경 시 작은 모델 셋 (`SMALL_MODELS`) 도 같이 갱신해야 합니다.

## RAG 임베딩

RAG API → LiteLLM (`RAG_OPENAI_BASEURL=http://litellm:8000/v1`) → router → `bge-m3` 보유 Ollama 노드. 보유 노드가 여러 대면 router 가 `least-busy` 로 LB. 적어도 1개 노드에 `bge-m3` 가 pull 돼 있어야 RAG 동작.
