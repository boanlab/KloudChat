# 모델 설정

## 모델 정의 (lib.sh 단일 진실)

`scripts/lib.sh` 의 다섯 자료구조가 모델 셋업의 진실 소스:
- `CHAT_MODELS` — LiteLLM 에 등록 (전체)
- `LIBRECHAT_MODELS` — LibreChat dropdown 노출 (코더 모델 제외)
- `EMBED_MODELS` — 임베딩 모델
- `MODEL_PRICE_IN_PM` / `MODEL_PRICE_OUT_PM` — 모델별 USD per 1M tokens (OpenRouter 환산). LiteLLM 이 spend/budget 추적에 사용
- `MODEL_OPENROUTER_FREE` — OpenRouter free tier 로 라우팅할 모델 (Ollama pull 불필요)

`gen-litellm-config.sh` / `gen-librechat-config.sh` 가 위 정의로 `litellm-config.yaml` 의 `KLOUDCHAT_AUTOGEN` marker 사이, `librechat.yaml` 의 `KLOUDCHAT_MODELS` marker 사이를 자동 생성합니다. nginx (`ollama-lb`) 가 `OLLAMA_URLS` 의 Ollama 백엔드들 사이를 `least_conn` 으로 분산.

| 식별자 | 백엔드 | 용도 |
|---|---|---|
| `ollama/qwen3.5:9b` | Ollama | 경량·타이틀 생성 |
| `ollama/qwen3.5:35b` | Ollama | 범용 주력 (기본 agent) |
| `ollama/gemma4:26b` | Ollama | 창의·UI (DGX Spark / RTX 4090 / 기타 NVIDIA) |
| `ollama/gemma3:27b` | Ollama | 창의·UI (Blackwell desktop 폴백 — gemma4 Ollama 이슈 우회) |
| `openrouter/gpt-oss:20b` | OpenRouter free | OSS 경량, 로컬 GPU 메모리 0 |
| `openrouter/gpt-oss:120b` | OpenRouter free | OSS 대형 (~65 GB 급), 로컬 GPU 메모리 0 |
| `ollama/qwen3-coder-next:q4_K_M` | Ollama | 코딩 (경량). LibreChat dropdown 제외 — Claude Code (`./scripts/claude-local.sh`) 전용 |
| `ollama/qwen3-coder-next:q8_0` | Ollama | 코딩 (고품질). 동일 |
| `bge-m3` | Ollama (embedding) | RAG 임베딩 (다국어) |

setup.sh 가 GPU 클래스를 감지해 적합한 셋을 추천. 데스크톱 Blackwell (RTX 5090 / RTX PRO 6000) 은 Ollama 가 gemma4 를 안정 로드하지 못함 (ollama#15238, #15264, #14374) → `gemma3:27b` 로 폴백.

```bash
./scripts/download-ollama-models.sh                   # 기본: qwen3-9b + embed
./scripts/download-ollama-models.sh qwen3-9b qwen3-35b embed
./scripts/download-ollama-models.sh all               # 옵션: --help
```

### 모델 추가 (Ollama 로컬)

1. `ollama pull <태그>` — 모든 `OLLAMA_URLS` 백엔드에서
2. `scripts/lib.sh` 의 `CHAT_MODELS` 또는 `EMBED_MODELS` 에 추가
3. (선택, 비용 추적) `MODEL_PRICE_IN_PM` / `MODEL_PRICE_OUT_PM` 에 USD/1M 토큰 단가 추가
4. (선택, dropdown 노출) `LIBRECHAT_MODELS` 에도 추가 → `./scripts/gen-librechat-config.sh && docker compose restart librechat`
5. `./scripts/gen-litellm-config.sh && docker compose restart litellm`

가격을 안 적으면 spend = $0 으로 기록됩니다 (LiteLLM 의 default).

### 모델 추가 (OpenRouter free 라우팅)

로컬 GPU 메모리가 부족하거나 큰 모델을 시험만 해보고 싶을 때 사용. 로컬 pull 없이 OpenRouter free tier 로 우회:

1. `.env` 에 `OPENROUTER_API_KEY` 설정 (이미 있으면 skip)
2. `scripts/lib.sh` 의 `MODEL_OPENROUTER_FREE` 에 `[<태그>]=<openrouter-model-id>:free` 추가
3. `CHAT_MODELS` / `LIBRECHAT_MODELS` 에도 추가, `MODEL_PRICE_*` 는 `0` 으로 (free)
4. `./scripts/gen-litellm-config.sh`
5. `docker compose up -d litellm` (⚠️ `restart` 는 `.env` 재해석 안 함 — 신규 env 반영하려면 반드시 `up -d`)

`model_name` prefix 가 자동으로 `openrouter/` 로 바뀝니다. LibreChat / agent 에선 동일 이름으로 보이지만 백엔드는 OR free 로 라우팅.

주의: free tier 는 주당 토큰 한도 + provider 자동 라우팅으로 가변 latency. 핵심 워크로드엔 로컬 Ollama 권장.

## 클라우드 모델 (선택)

API 키는 `docker-compose.yml` 의 LiteLLM 환경에 자동 주입됨. 노출하려면:

1. `.env` — `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `OPENROUTER_API_KEY` 채우기
2. `litellm-config.yaml` 의 해당 model_list entry 주석 해제
3. `docker compose restart litellm`

## 이미지 생성

ComfyUI + A1111 shim. ComfyUI 는 항상 native (systemd) 로 실행 — `./scripts/install-comfyui.sh` 가 각 GPU 노드에 설치 (`/opt/comfyui/{venv,app}` + `/var/lib/comfyui/output`). 가중치는 `/opt/comfyui/app/ComfyUI/models/` (ComfyUI default 경로). 아키텍처는 [overview.md](overview.md#comfyui-이미지-생성), VRAM 점유 / 성능은 [gpu-memory.md](gpu-memory.md). 가중치 다운로드:

```bash
./scripts/download-image-models.sh   # /opt/comfyui/app/ComfyUI/models, ~50GB
```

| alias | 모델 | 크기 | 용도 |
|---|---|---|---|
| `sdxl` | SDXL 1.0 base | ~6.9 GB | 범용, 빠름 |
| `sdxl-vae` | SDXL VAE FP16 fix | ~160 MB | sdxl 색감 개선 |
| `qwen-image` | Qwen-Image Q8 GGUF + 인코더 + VAE | ~21 GB | 텍스트→이미지 (한글 강함) |
| `qwen-image-edit` | Qwen-Image-Edit-2509 Q8 GGUF | ~21 GB | 이미지 편집 (인코더/VAE 공유) |

LibreChat 호출 시 `override_settings.sd_model_checkpoint` 값으로 alias 선택 (기본 `qwen-image`). 워크플로 템플릿: `comfyui-shim/workflows/*.json`.

## RAG 임베딩

RAG API → LiteLLM (`RAG_OPENAI_BASEURL=http://litellm:8000/v1`) → ollama-lb → Ollama. 멀티 노드 LB 자동 적용. 기본 모델 `bge-m3`, 모든 백엔드에 pull 필요.
