# 모델 설정

## Ollama 모델

`litellm-config.yaml`은 `ollama/*` 와일드카드 단일 항목으로 Ollama에 pull된 모든 모델을
자동으로 LiteLLM에 노출합니다. 개별 항목을 추가하지 않아도 됩니다.

```yaml
# litellm-config.yaml (현재 설정)
- model_name: "ollama/*"
  litellm_params:
    model: "ollama_chat/*"
    api_base: os.environ/OLLAMA_API_BASE
```

LiteLLM에서 모델명은 `ollama/<Ollama태그>` 형태가 됩니다 (예: `ollama/qwen3.5:9b`).

### 기본 제공 모델

| Ollama 태그 | LiteLLM 모델명 | 용도 |
|---|---|---|
| `qwen3.5:9b` | `ollama/qwen3.5:9b` | 경량·타이틀 생성 |
| `qwen3.5:35b` | `ollama/qwen3.5:35b` | 범용 주력 |
| `gemma4:26b` | `ollama/gemma4:26b` | 창의·UI |
| `qwen3-coder-next:q4_K_M` | `ollama/qwen3-coder-next:q4_K_M` | 코딩 (경량) |
| `qwen3-coder-next:q8_0` | `ollama/qwen3-coder-next:q8_0` | 코딩 (고품질) |
| `nomic-embed-text` | `ollama/nomic-embed-text` | RAG 임베딩 |

### 모델 다운로드

```bash
# 기본값: qwen3.5:9b + nomic-embed-text
./scripts/download-ollama-models.sh

# 권장 구성
./scripts/download-ollama-models.sh qwen3-9b qwen3-35b embed

# 전체
./scripts/download-ollama-models.sh all
```

### 모델 추가 방법

1. Ollama에 pull
2. LiteLLM 재시작 없이 즉시 사용 가능 (와일드카드가 자동 노출)

```bash
ollama pull llama3.1:8b
# → LiteLLM에서 ollama/llama3.1:8b 로 접근 가능
```

비용 추적이 필요하면 `litellm-config.yaml`에 개별 항목을 추가합니다.

```yaml
- model_name: llama3.1-8b
  litellm_params:
    model: ollama_chat/llama3.1:8b
    api_base: os.environ/OLLAMA_API_BASE
    input_cost_per_token: 0.0000002
    output_cost_per_token: 0.0000006
```

## 클라우드 모델 추가 (선택)

세 파일을 함께 수정해야 합니다.

**1. `litellm-config.yaml`** — `model_list` 내 해당 공급자 주석 해제

**2. `docker-compose.yml`** — litellm 서비스 `environment` 내 API 키 주석 해제

```yaml
environment:
  LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
  OLLAMA_API_BASE: ${OLLAMA_API_BASE}
  DATABASE_URL: ...
  OPENAI_API_KEY: ${OPENAI_API_KEY}      # 주석 해제
  ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY} # 주석 해제
  GEMINI_API_KEY: ${GEMINI_API_KEY}       # 주석 해제
```

**3. `.env`** — API 키 추가

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=AIza...
```

이후 LiteLLM을 재시작합니다: `docker compose restart litellm`

## 이미지 생성 모델 (SD.Next)

SD.Next는 `.safetensors` 파일을 `./sdnext/models/Stable-diffusion/`에서 자동 감지합니다.

```bash
# 기본 (SDXL + VAE)
./scripts/download-sdnext-models.sh

# 옵션별 크기
# sdxl          ~6.9GB  범용
# sdxl-turbo    ~6.9GB  고속 (1~4 step)
# vae           ~160MB  색감 개선 (권장)
# flux-schnell  ~24GB   고품질, Apache 2.0, HF 토큰 필요
```

SD.Next 기동 후 `http://서버IP:7860`에서 모델을 확인하고 선택할 수 있습니다.

## RAG 임베딩 모델

RAG API는 `OLLAMA_BASE_URL`을 통해 Ollama에 직접 접근해 임베딩을 생성합니다.
(`langchain_ollama.OllamaEmbeddings` 사용, LiteLLM을 경유하지 않습니다.)

기본 임베딩 모델은 `nomic-embed-text`이며, Ollama에 해당 모델이 pull되어 있어야 합니다.

```bash
ollama pull nomic-embed-text
```
