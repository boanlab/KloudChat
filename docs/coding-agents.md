# 로컬 코딩 에이전트 연동 (Claude Code / Codex)

> 이 문서: Anthropic Claude Code 와 OpenAI Codex CLI 를 로컬 `qwen3-coder-next` 모델로 구동하는 법. LiteLLM 의 Anthropic-호환 (`/v1/messages`) + OpenAI-호환 (`/v1/chat/completions`) 듀얼 엔드포인트를 활용합니다. KloudChat 이 띄워져 있고 `qwen3-coder-next:*` 를 Ollama 노드에 pull 한 상태 전제.

## 왜?

- Claude Code / Codex 의 UX (에이전트 / 멀티턴 / 파일 편집) 그대로 + 코드는 외부로 안 나감
- frontier 모델 청구서 없이 무제한 사용 (단, VRAM 비용은 부담)
- `qwen3-coder-next:q8_0` (~84 GB VRAM, 고품질) 단일 quant — VRAM 부족 환경에선 본 가이드 비권장
- 같은 LiteLLM 게이트웨이를 LibreChat 도 공유 — 키·예산·로깅 통일

## 사전 준비

1. KloudChat 띄워져 있고 LiteLLM healthy (`http://localhost:8000/health/liveliness` → 200)
2. Ollama 노드에 `qwen3-coder-next:q8_0` pull 됨 — `ollama pull qwen3-coder-next:q8_0`
3. (옵션) `qwen3.5:9b` / `llama3.1:8b` 같은 가벼운 모델 — Claude Code 의 Haiku 슬롯에 매핑
4. `.env` 의 `LITELLM_MASTER_KEY` 확인 — Claude Code / Codex 에 인증으로 사용

VRAM 점유는 [GPU 메모리 가이드](gpu-memory.md). q8_0 은 GB10 (DGX Spark) / H100 80GB×2 수준이 필요합니다.

## Claude Code

Claude Code 는 환경변수 5개만 세팅하면 LiteLLM 의 Anthropic-호환 endpoint 를 통해 로컬 모델로 라우팅됩니다:

```bash
export ANTHROPIC_BASE_URL="http://localhost:8000"                                       # LiteLLM Anthropic proxy
export ANTHROPIC_AUTH_TOKEN="$(grep -E '^LITELLM_MASTER_KEY=' .env | cut -d= -f2-)"
export ANTHROPIC_MODEL="ollama/qwen3-coder-next:q8_0"                                   # 메인 모델
export ANTHROPIC_DEFAULT_SONNET_MODEL="ollama/qwen3-coder-next:q8_0"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="ollama/qwen3.5:9b"
export ANTHROPIC_DEFAULT_OPUS_MODEL="ollama/qwen3-coder-next:q8_0"

claude
```

위 5줄을 `~/.bashrc` / `~/.zshrc` 에 박아두거나, 프로젝트별 `direnv` (`.envrc`) 로 관리 권장.

라우팅: Claude Code 가 `POST /v1/messages` 를 `localhost:8000` 으로 → LiteLLM 의 `anthropic_proxy` (litellm-config.yaml 마지막 블록) 가 Anthropic Messages API 를 OpenAI chat API 로 변환 → `model_name=ollama/qwen3-coder-next:q8_0` 매칭 → router 가 보유 Ollama 노드 중 한 곳으로 직접 호출.

### 모델 슬롯 매핑 커스터마이즈

Claude Code 는 작업 난이도에 따라 4가지 슬롯을 사용합니다. 스크립트 편집해서 어떤 로컬 모델을 어디에 매핑할지 조정:

| Claude Code 슬롯 | 용도 | 기본 매핑 | 대안 |
|---|---|---|---|
| `ANTHROPIC_MODEL` | 명시 호출 시 | `ollama/qwen3-coder-next:q8_0` | 동일 |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | 가장 어려운 작업 | `ollama/qwen3-coder-next:q8_0` | OR 키 있으면 `anthropic/claude-opus-4.7` |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | 균형형 | `ollama/qwen3-coder-next:q8_0` | `ollama/qwen3.6:35b` |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | 가벼운 작업 | `ollama/qwen3.5:9b` | `ollama/llama3.1:8b` |

## OpenAI Codex CLI

Codex CLI 는 `OPENAI_BASE_URL` 로 endpoint 를 바꿀 수 있어 LiteLLM 의 OpenAI 호환 `/v1` 을 그대로 가리키면 됩니다.

```bash
export OPENAI_BASE_URL="http://localhost:8000/v1"
export OPENAI_API_KEY="$(grep -E '^LITELLM_MASTER_KEY=' .env | cut -d= -f2-)"
codex --model "ollama/qwen3-coder-next:q8_0"
```

Codex 의 `~/.codex/config.toml` 에 프로파일로 박아두면 매번 flag 안 줘도 됩니다:

```toml
[model_providers.kloudchat]
name = "KloudChat LiteLLM"
base_url = "http://localhost:8000/v1"
env_key = "LITELLM_MASTER_KEY"

[profiles.qwen-q8]
model = "ollama/qwen3-coder-next:q8_0"
model_provider = "kloudchat"
```

```bash
LITELLM_MASTER_KEY="$(grep -E '^LITELLM_MASTER_KEY=' .env | cut -d= -f2-)" \
  codex --profile qwen-q8
```

## 모델 선택

| 모델 | VRAM | 토큰/s (DGX Spark) | 용도 |
|---|---|---|---|
| `qwen3-coder-next:q8_0` | ~84 GB | ~25 t/s | 어려운 리팩터, deep reasoning |
| `qwen3.6:35b` | ~22 GB | ~70 t/s | 가벼운 코딩 / 일반 작업 (코딩 특화 X) |

VRAM 80GB+ (GB10, H100 80GB×2 등) 필요. 그 미만은 `qwen3.6:35b` 같은 일반 모델로 대체. Ollama 가 모델 스왑할 때 컨텍스트 전환 latency 발생 — 한 작업에는 한 모델만 쓰는 게 유리.

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `401 unauthorized` | `ANTHROPIC_AUTH_TOKEN` / `OPENAI_API_KEY` 가 빈 값. `.env` 의 `LITELLM_MASTER_KEY` 확인 |
| `404 model not found` | LiteLLM 에 모델 미등록. `./scripts/gen-litellm-config.sh && docker compose up -d litellm` |
| `Ollama 노드 unreachable` | discovery 시 그 노드만 skip, 다른 노드에 모델 있으면 거기로 라우팅. 어느 노드에도 없으면 노드에서 `ollama pull qwen3-coder-next:q8_0` |
| 응답이 매우 느림 | 처음 호출 시 모델 로딩 (`q8_0` 은 80GB+ 로드에 20-40s). 2분 idle 후 unload — `OLLAMA_KEEP_ALIVE` 로 유지 ([Ollama 튜닝](ollama-tuning.md)) |
| 툴 콜이 깨짐 | `qwen3-coder-next` 는 hyphen 포함 함수명에 약함 — 이미지 같은 멀티모달 툴은 별도 모델 권장 ([에이전트 분리 패턴](models.md)) |
| OOM | VRAM 부족. q8 → q4 다운그레이드, 또는 `comfyui-shim stop` |

## 같이 보면 좋은 문서

- [모델 설정](models.md) — 카탈로그 + 라우팅 매트릭스
- [GPU 메모리 가이드](gpu-memory.md) — VRAM 점유
- [Ollama 튜닝](ollama-tuning.md) — `OLLAMA_KEEP_ALIVE`, `MAX_LOADED_MODELS`
