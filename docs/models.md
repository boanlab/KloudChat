# 모델 설정

> 이 문서: 어떤 모델이 어디로 라우팅되는지(라우팅 매트릭스) · 모델 카탈로그를 어디서 수정하는지 · 새 모델을 추가하는 절차 · 이미지 모델 매핑. 처음 띄우는 거면 [README](../README.md) 만 보면 됩니다.

## 모델 카탈로그 (lib.sh 단일 진실)

`scripts/lib.sh` 가 모델 셋업의 진실 소스. 6개 자료구조:

| 변수 | 용도 |
|---|---|
| `OPENAI_NATIVE_MODELS` | OpenAI provider curated 리스트. native key 있으면 native, 없고 OR 있으면 OR fallback |
| `ANTHROPIC_NATIVE_MODELS` | Anthropic provider curated 리스트 |
| `GOOGLE_NATIVE_MODELS` | Google Gemini provider curated 리스트 |
| `OLLAMA_CHAT_CATALOG` | Ollama 채팅 모델 카탈로그 (gpt-oss 포함). 어느 노드든 보유하면 그 노드(들)에 deployment 등록 |
| `OLLAMA_EMBED_CATALOG` | Ollama 임베딩 카탈로그 |
| `MODEL_OR_FREE` | Ollama 카탈로그 모델 → OR free tier 매핑. 어느 노드도 보유 안 할 때 OR 키 있으면 fallback |
| `MODEL_PRICE_IN_PM` / `MODEL_PRICE_OUT_PM` | 모델별 USD per 1M tokens (OpenRouter 환산). LiteLLM 이 spend/budget 추적에 사용 |

`gen-litellm-config.sh` / `gen-librechat-config.sh` 가 위 정의 + 환경(API 키 + ollama discovery)을 합쳐서 `litellm-config.yaml` 의 `KLOUDCHAT_AUTOGEN` marker 사이, `librechat.yaml` 의 `KLOUDCHAT_MODELS` marker 사이를 자동 생성합니다.

## 라우팅 결정 매트릭스

### Commercial curated (OpenAI / Anthropic / Google)

| 케이스 | native key 있음 | native 없음 + OR 있음 | 둘 다 없음 |
|---|---|---|---|
| `<provider>/<id>` model_name 으로 등록 | `litellm_params.model: <provider>/<id>` + `api_key: os.environ/<PROVIDER>_API_KEY` | `model: openrouter/<provider>/<id>` + `api_key: os.environ/OPENROUTER_API_KEY` | 미등록 |

→ LibreChat 메뉴에는 모델당 1개 entry만 노출. 사용자는 라우트를 직접 고를 필요 없음.

### Ollama 카탈로그 (union discovery + 선택적 OR free fallback)

각 노드의 `/api/tags` 응답을 **합집합**으로 합쳐 모델 → 보유 노드 매핑을 만들고, 모델 하나당 보유 노드 수만큼 같은 `model_name` deployment 를 emit. 노드 1개 unreachable 이면 warn 후 그 노드만 skip. 어느 노드도 보유 안 한 모델은 `MODEL_OR_FREE` 매핑이 있고 OR 키 있을 때 OR free 로 fallback.

LiteLLM router 의 `routing_strategy: least-busy` 가 같은 `model_name` 후보들 중 진행 중 요청이 적은 노드를 고름 — 큰 모델은 한 노드만 가지고 있어도 OK, 작은 모델은 여러 노드에 자동 분산.

| 모델 | 보유 노드 ≥1 | 보유 0 + `MODEL_OR_FREE` 매핑 + OR 키 | 보유 0 + 매핑 없거나 OR 없음 |
|---|---|---|---|
| `ollama/gpt-oss:20b` | `ollama_chat/gpt-oss:20b` × 보유 노드 수 | `openrouter/openai/gpt-oss-20b:free` | 미등록 |
| `ollama/gpt-oss:120b` | `ollama_chat/gpt-oss:120b` × 보유 노드 수 | `openrouter/openai/gpt-oss-120b:free` | 미등록 |
| `ollama/qwen3.5:9b` | `ollama_chat/qwen3.5:9b` × 보유 노드 수 | (매핑 주석 처리 — 사용자 검증 후 활성) | 미등록 |
| `ollama/qwen3.5:35b` | 동일 | 동 | 미등록 |
| `ollama/gemma4:26b` | 동일 (gemma4 우선) | 매핑 없음 | 미등록 |
| `ollama/gemma3:27b` | 동일 (gemma4 도 어느 노드에 있으면 skip) | `openrouter/google/gemma-3-27b-it:free` | 미등록 |
| `ollama/qwen3-coder-next:q4_K_M/q8_0` | 동일 | (주석 처리) | 미등록 |
| `bge-m3` (embed) | 동일 | OR free 미지원 | 미등록 |

## native curated 디폴트

`lib.sh` 의 배열 — 신규 모델 추가/제거 시 여기와 `MODEL_PRICE_*` 만 갱신:

```bash
OPENAI_NATIVE_MODELS=(gpt-5.5 gpt-5 gpt-5-mini gpt-5-nano)
ANTHROPIC_NATIVE_MODELS=(claude-opus-4.7 claude-opus-4.6 claude-sonnet-4.6 claude-haiku-4.5)
GOOGLE_NATIVE_MODELS=(gemini-3.1-pro-preview gemini-2.5-pro gemini-2.5-flash)
OLLAMA_CHAT_CATALOG=(gpt-oss:20b gpt-oss:120b qwen3.5:9b qwen3.5:35b gemma4:26b gemma3:27b qwen3-coder-next:q4_K_M qwen3-coder-next:q8_0)
OLLAMA_EMBED_CATALOG=(bge-m3)
```

`qwen3-coder-next:*` 는 LibreChat dropdown 에서 자동 노출. Claude Code / OpenAI Codex CLI 에서 LiteLLM 게이트웨이를 통해 로컬 코딩 에이전트로도 사용 가능 — [코딩 에이전트 연동](coding-agents.md) 참고.

데스크톱 Blackwell (RTX 5090 / RTX PRO 6000) 은 Ollama 가 gemma4 를 안정 로드하지 못함 (ollama#15238, #15264, #14374) → 노드에 `gemma3:27b` 만 pull 하면 자동으로 그게 등록.

## 셋업 흐름

```bash
# 1. .env 채우기 — 외부 API 키 + OLLAMA_URLS + COMFYUI_URLS + HF_TOKEN
./scripts/gen-env.sh && $EDITOR .env

# 2. Ollama 노드(들)에서 모델 pull (OR-only 면 생략 가능)
./scripts/download-ollama-models.sh                   # 기본: qwen3-9b + embed
./scripts/download-ollama-models.sh qwen3-9b qwen3-35b embed
./scripts/download-ollama-models.sh all               # 옵션: --help

# 3. compose 호스트에서 config 생성 + 기동
./scripts/setup.sh --yes
```

## 모델 추가

### 케이스 A — 새 native commercial 모델

OpenAI 가 `gpt-6` 를 출시했다고 가정:

1. `scripts/lib.sh` 의 `OPENAI_NATIVE_MODELS` 배열에 `gpt-6` 추가
2. `MODEL_PRICE_IN_PM[gpt-6]=<USD/1M>` / `MODEL_PRICE_OUT_PM[gpt-6]=<USD/1M>` 추가
3. `./scripts/gen-litellm-config.sh && ./scripts/gen-librechat-config.sh`
4. `docker compose up -d litellm librechat` (`restart` 는 `.env` 재해석 안 함)
5. 팀 allowlist 동기화 (`team create` 가 모델 리스트를 freeze 하므로):
   ```bash
   source scripts/lib.sh
   models_json=$(./scripts/gen-litellm-config.sh --dry-run | grep '^  - model_name:' | awk '{print $4}' | jq -R . | jq -s .)
   for tid in $(litellm_get /team/list | jq -r '.[].team_id'); do
     litellm_post /team/update "$(jq -n --arg t "$tid" --argjson m "$models_json" '{team_id:$t, models:$m}')"
   done
   ```

native key 가 .env 에 있으면 native 로, 없으면 OR fallback 으로 자동 라우팅. 둘 다 없으면 미등록.

### 케이스 B — 새 Ollama 로컬 모델

1. 모델을 돌릴 노드(들)에서 `ollama pull <태그>` — 한 노드만 받아도 됨 (router 가 그 노드로 라우팅), 여러 노드에 받으면 자동 LB
2. `scripts/lib.sh` 의 `OLLAMA_CHAT_CATALOG` (또는 embed 면 `OLLAMA_EMBED_CATALOG`) 에 태그 추가
3. (선택) `MODEL_PRICE_*` 에 단가 추가 — 안 적으면 spend = $0 으로 기록
4. `./scripts/gen-litellm-config.sh && ./scripts/gen-librechat-config.sh && docker compose up -d litellm librechat`
5. (필요 시) 팀 allowlist 동기화 — 위와 동일

## 이미지 생성

ComfyUI + A1111 shim. ComfyUI 는 항상 native (systemd) 로 실행 — `./scripts/install-comfyui.sh` 가 각 GPU 노드에 설치 (`/opt/comfyui/{venv,app}` + `/var/lib/comfyui/output`). 가중치는 `/opt/comfyui/app/ComfyUI/models/`. 아키텍처는 [overview.md](overview.md#comfyui-이미지-생성), VRAM 점유 / 성능은 [gpu-memory.md](gpu-memory.md).

### 가중치 다운로드

```bash
./scripts/download-image-models.sh                # 기본 셋 + (HF_TOKEN 있으면) flux-dev
./scripts/download-image-models.sh recommended    # 동일 (명시)
./scripts/download-image-models.sh all            # 전부 (~104GB)
./scripts/download-image-models.sh sdxl qwen-image # 명시 alias
```

기본 셋: `sdxl + sdxl-vae + qwen-image + qwen-image-edit + flux-shared + flux-schnell`. `HF_TOKEN` 이 `.env` 에 있으면 `+ flux-dev`.

| alias | 모델 | 크기 | 용도 |
|---|---|---|---|
| `sdxl` | SDXL 1.0 base | ~6.9 GB | 범용, 빠름 (~5-10s/장) |
| `sdxl-vae` | SDXL VAE FP16 fix | ~160 MB | sdxl 색감 개선 |
| `qwen-image` | Qwen-Image Q8 GGUF + 인코더 + VAE | ~21 GB | 텍스트→이미지 (한글 강함, 느림) |
| `qwen-image-edit` | Qwen-Image-Edit-2509 Q8 GGUF | ~21 GB | 이미지 편집 (인코더/VAE 공유) |
| `flux-shared` | Flux 공유 인코더 (T5-XXL FP16, CLIP-L, AE VAE) | ~10 GB | flux-dev/schnell 둘 다 사용 |
| `flux-dev` | FLUX.1-dev FP16 (gated, **HF_TOKEN 필수**) | ~22 GB | 최고 품질, 느림 (~20 step) |
| `flux-schnell` | FLUX.1-schnell FP16 (MIT) | ~22 GB | 빠른 iteration (4 step) |

GPU VRAM 이 부족한 노드에서 무거운 모델은 받지 마세요 — 명시적 alias 지정. ComfyUI/Ollama 모두 union 디스커버리라 한 노드만 받은 모델도 그대로 활성화되고 shim/router 가 보유 노드로 자동 라우팅합니다.

### 모델 선택 메커니즘

LibreChat 의 `image-generation` 툴 스키마에 `model` enum 필드가 있어 LLM 이 alias 를 직접 지정 (`rag-patches/patch_librechat_sd_model.js` 가 빌드 시 적용). 호출 시 `model="<alias>"` 인자가 페이로드의 `override_settings.sd_model_checkpoint` 로 변환되어 shim 이 워크플로 분기. 미지정 시 shim 의 `DEFAULT_MODEL` (=`qwen-image`).

사용자별 에이전트는 `./scripts/manage.sh` 가 자동 생성:
- `user create --name ... --username ... --password ...` — 신규 유저 풀 프로비저닝 시
- `agent sync` — 기존 유저 전체에 누락분 멱등 재생성 (모델 카탈로그 추가 후)

채팅 에이전트는 `lib.sh:litellm_chat_models_csv()` 가 반환하는 모든 model_name 에 대해 `Text (<model>)` 로 생성. 이미지 에이전트는 alias 별 고정 매핑:

| 에이전트 | 고정 model |
|---|---|
| `Image (sdxl)` | sdxl |
| `Image (qwen-image)` | qwen-image |
| `Image (flux-dev)` | flux-dev |
| `Image (flux-schnell)` | flux-schnell |

LLM 드라이버는 qwen3.5:35b (없으면 다른 채팅 모델). system instructions 로 해당 alias 강제 호출. 대화 시작점이 곧 base 모델 선택.

이미지 backend 추가: 워크플로 템플릿 (`comfyui-shim/workflows/<alias>-txt2img.json`) + `MODEL_ALIASES` 와 `COMFYUI_ALIAS_FILES` (`comfyui-shim/app.py`) + `patch_librechat_sd_model.js` 의 enum + `manage.sh` 의 `img_specs` 항목 + `lib.sh` 의 `__comfyui_node_models` 파일 매핑. shim 의 `COMFYUI_ALIAS_FILES` 와 lib.sh 의 파일 매핑은 동일한 (kind, filename) 셋을 유지해야 노드 디스커버리가 일치합니다.

## RAG 임베딩

RAG API → LiteLLM (`RAG_OPENAI_BASEURL=http://litellm:8000/v1`) → router → `bge-m3` 보유 Ollama 노드. 보유 노드가 여러 대면 router 가 `least-busy` 로 LB. 적어도 1개 노드에 `bge-m3` 가 pull 돼 있어야 RAG 동작.
