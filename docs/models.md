# 모델 설정

## Ollama 모델

`litellm-config.yaml` 은 Ollama 모델 5개를 명시적으로 등록합니다. 와일드카드 (`ollama/*`) 는 LiteLLM 가 사용자 키 응답 시 model_list 와 매핑하지 못 해 `ollama/llama2` 한 개로만 fallback 노출되므로 사용하지 않습니다.

```yaml
# litellm-config.yaml (발췌)
- model_name: ollama/qwen3.5:9b
  litellm_params:
    model: ollama_chat/qwen3.5:9b
    api_base: os.environ/OLLAMA_API_BASE
# ... qwen3.5:35b, gemma4:26b, qwen3-coder-next:q4_K_M, qwen3-coder-next:q8_0 동일 패턴
```

LiteLLM 에서 모델명은 `ollama/<Ollama태그>` 형태가 됩니다 (예: `ollama/qwen3.5:9b`).
임베딩 모델 (`bge-m3`) 은 rag_api 가 Ollama 호스트로 직접 호출하므로 LiteLLM 에 등록하지 않습니다.

### 기본 제공 모델

| Ollama 태그 | LiteLLM 모델명 | 용도 |
|---|---|---|
| `qwen3.5:9b` | `ollama/qwen3.5:9b` | 경량·타이틀 생성 |
| `qwen3.5:35b` | `ollama/qwen3.5:35b` | 범용 주력 |
| `gemma4:26b` | `ollama/gemma4:26b` | 창의·UI — DGX Spark / RTX 4090 / 기타 NVIDIA |
| `gemma3:27b` | `ollama/gemma3:27b` | 창의·UI — RTX 5090 / RTX PRO 6000 Blackwell 폴백 (gemma4 Ollama 이슈 우회) |
| `qwen3-coder-next:q4_K_M` | `ollama/qwen3-coder-next:q4_K_M` | 코딩 (경량) |
| `qwen3-coder-next:q8_0` | `ollama/qwen3-coder-next:q8_0` | 코딩 (고품질) |
| `bge-m3` | — (rag_api 직접 호출) | RAG 임베딩 (다국어, 한국어 우수) |

`setup.sh` 가 `nvidia-smi --query-gpu=name` 결과로 GPU 클래스를 분류하고 적절한 모델 셋을 추천합니다 (`scripts/lib/platform.sh` 의 `detect_gpu_class`). 데스크톱 Blackwell (RTX 5090 / RTX PRO 6000 Blackwell) 에서는 Ollama 가 gemma4 를 안정적으로 로드하지 못해 (ollama#15238, #15264, #14374) `gemma3:27b` 를 일반 채팅 default 로 사용합니다.

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

## 이미지 생성 모델 (ComfyUI + A1111 shim)

KloudChat 은 이미지 생성을 ComfyUI 컨테이너로 일원화합니다. LibreChat 의 내장 stable-diffusion 툴은 A1111 형식만 알아듣기 때문에 앞에 얇은 어댑터 (`comfyui-shim`) 를 두고, shim 이 A1111 요청을 받아 모델별 워크플로 템플릿 (`comfyui-shim/workflows/*.json`) 으로 변환해 ComfyUI 큐에 넣고 결과 이미지를 base64 로 돌려줍니다.

지원 환경: **Linux + NVIDIA GPU (amd64 / arm64 모두, DGX Spark 포함)**. GPU 가 없는 호스트에서는 ComfyUI 컨테이너가 자동 제외됩니다.

### 가중치 다운로드

가중치는 `./comfyui/models/{checkpoints,unet,clip,vae}/` 하위로 떨어집니다 (ComfyUI 가 자동 감지).

```bash
# 기본 (전체 세트, ~50GB)
./scripts/download-image-models.sh

# alias 별
./scripts/download-image-models.sh sdxl sdxl-vae       # SDXL 만
./scripts/download-image-models.sh qwen-image          # Qwen-Image (텍스트→이미지)
./scripts/download-image-models.sh qwen-image-edit     # Qwen-Image-Edit (편집)
```

| alias | 모델 | 크기 | 용도 |
|---|---|---|---|
| `sdxl` | SDXL 1.0 base | ~6.9 GB | 범용, LoRA 풍부 |
| `sdxl-vae` | SDXL VAE FP16 fix | ~160 MB | sdxl 색감 개선 |
| `qwen-image` | Qwen-Image Q8 GGUF + text encoder + VAE | ~21 GB | 최신 텍스트→이미지, 한국어 프롬프트 강함 |
| `qwen-image-edit` | Qwen-Image-Edit-2509 Q8 GGUF | ~21 GB | 이미지 + 프롬프트 편집 (text-encoder / VAE 공유) |

### 모델 선택

LibreChat 에이전트 / 채팅에서 이미지 생성을 호출할 때 `override_settings.sd_model_checkpoint` 값으로 alias 를 지정하면 shim 이 해당 워크플로로 라우팅합니다 (기본값 `qwen-image`).

### 외부 포트 / 디버깅

`comfyui` · `comfyui-shim` 둘 다 외부 포트를 노출하지 않습니다 (LibreChat 가 `http://comfyui-shim:7860` 으로 내부 접근). ComfyUI 웹 UI 를 직접 보려면 `docker-compose.gpu.yml` 의 `comfyui` 서비스에 `ports: ["8188:8188"]` 을 임시로 추가하세요. 워크플로 템플릿 수정은 `comfyui-shim/workflows/*.json` 을 편집 후 shim 재시작 (`./scripts/deploy.sh restart comfyui-shim`).

## RAG 임베딩 모델

RAG API 는 `OLLAMA_BASE_URL` 을 통해 Ollama 에 직접 접근해 임베딩을 생성합니다 (`langchain_ollama.OllamaEmbeddings` 사용, LiteLLM 을 경유하지 않습니다).

기본 임베딩 모델은 `bge-m3` (다국어 + 한국어 우수) 이며, `.env` 의 `EMBEDDINGS_MODEL` 로 지정됩니다. Ollama 에 해당 모델이 pull 되어 있어야 합니다.

```bash
ollama pull bge-m3
```
