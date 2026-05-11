# 모델 설정

## Ollama 모델

`litellm-config.yaml` 은 Ollama 모델 5개를 명시적으로 등록합니다. 와일드카드 (`ollama/*`) 는 LiteLLM 가 사용자 키 응답 시 model_list 와 매핑하지 못 해 `ollama/llama2` 한 개로만 fallback 노출되므로 사용하지 않습니다.

```yaml
# litellm-config.yaml (발췌)
- model_name: ollama/qwen3.5:9b
  litellm_params:
    model: ollama_chat/qwen3.5:9b
    api_base: os.environ/OLLAMA_API_BASE
# ... qwen3.5:35b, gemma3:27b, qwen3-coder-next:q4_K_M, qwen3-coder-next:q8_0 동일 패턴
```

LiteLLM 에서 모델명은 `ollama/<Ollama태그>` 형태가 됩니다 (예: `ollama/qwen3.5:9b`).
임베딩 모델 (`bge-m3`) 은 rag_api 가 Ollama 호스트로 직접 호출하므로 LiteLLM 에 등록하지 않습니다.

### 기본 제공 모델

| Ollama 태그 | LiteLLM 모델명 | 용도 |
|---|---|---|
| `qwen3.5:9b` | `ollama/qwen3.5:9b` | 경량·타이틀 생성 |
| `qwen3.5:35b` | `ollama/qwen3.5:35b` | 범용 주력 |
| `gemma3:27b` | `ollama/gemma3:27b` | 창의·UI |
| `qwen3-coder-next:q4_K_M` | `ollama/qwen3-coder-next:q4_K_M` | 코딩 (경량) |
| `qwen3-coder-next:q8_0` | `ollama/qwen3-coder-next:q8_0` | 코딩 (고품질) |
| `bge-m3` | — (rag_api 직접 호출) | RAG 임베딩 (다국어, 한국어 우수) |

### 모델 다운로드

```bash
# 기본값: qwen3.5:9b + bge-m3 (embed)
./scripts/download-ollama-models.sh

# 권장 구성
./scripts/download-ollama-models.sh qwen3-9b qwen3-35b embed

# 전체
./scripts/download-ollama-models.sh all
```

### 모델 추가 방법

1. Ollama 에 pull
2. `litellm-config.yaml` 의 `model_list` 에 항목 추가 후 LiteLLM 재시작

```bash
ollama pull llama3.1:8b
docker compose restart litellm
```

비용 추적도 같은 항목에 단가를 명시해 함께 처리합니다.

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

SD.Next 컨테이너는 외부 포트를 열지 않습니다 (LibreChat 가 내부 네트워크 `http://sdnext:7860` 로만 접근). 활성 체크포인트는 `./scripts/set-sdnext-model.sh` 로 설정합니다 (setup.sh 가 자동 호출). 웹 UI 에 접근해야 한다면 `docker-compose.amd64.yml` 의 `sdnext` 서비스에 `ports: ["7860:7860"]` 을 추가하세요.

## RAG 임베딩 모델

RAG API 는 `OLLAMA_BASE_URL` 을 통해 Ollama 에 직접 접근해 임베딩을 생성합니다 (`langchain_ollama.OllamaEmbeddings` 사용, LiteLLM 을 경유하지 않습니다).

기본 임베딩 모델은 `bge-m3` (다국어 + 한국어 우수) 이며, `.env` 의 `EMBEDDINGS_MODEL` 로 지정됩니다. Ollama 에 해당 모델이 pull 되어 있어야 합니다.

```bash
ollama pull bge-m3
```
