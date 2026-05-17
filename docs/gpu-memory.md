# GPU 메모리 가이드

## 서비스별 VRAM 점유

| 모델 | VRAM |
|---|---|
| qwen3.5:9b (Q4) | ~5 GB |
| llama3.1:8b (Q4) | ~5 GB |
| qwen3.6:35b (Q4) | ~22 GB |
| nemotron3:33b (Q4) | ~20 GB |
| llama3.3:70b (Q4) | ~40 GB |
| qwen3-coder-next (Q8_0) | ~84 GB |
| bge-m3 (embed) | ~1.2 GB |
| Qwen-Image (Q8_0 GGUF) | ~22 GB |
| Qwen-Image-Edit (Q8_0 GGUF) | ~22 GB |
| FLUX.1-dev / schnell (FP16) | ~24 GB |

Ollama 채팅·임베딩 모델은 대화 후 2분 유지 (`OLLAMA_KEEP_ALIVE` 로 변경). ComfyUI 모델은 요청 시만 로드, FLUX 계열은 T5+CLIP 인코더 ~10 GB 가 별도로 잡힘.

OpenRouter 경유 commercial 모델 (`claude-*`, `gpt-*`, `gemini-*`) 은 외부 API 라 로컬 GPU 0. native API 직결은 미지원 (전부 OR 경유).

모든 로컬 서비스는 동일한 물리 GPU VRAM 을 공유합니다 — 격리나 예약 없이 선착순 점유.

## 시나리오별 필요 VRAM

| 시나리오 | VRAM |
|---|---|
| 텍스트 채팅 (소형) | ~6 GB |
| 텍스트 채팅 (중형) | ~22 GB |
| 텍스트 채팅 (대형) | ~40 GB |
| 텍스트 + 이미지 | ~46 GB |
| 코딩 특화 | ~85 GB |

소형 (qwen3.5:9b 또는 llama3.1:8b) 은 RTX 3080 10 GB 정도. 중형 (qwen3.6:35b 또는 nemotron3:33b) 은 RTX 3090 / 4090 24 GB. 대형 (llama3.3:70b) 은 A100 80 GB. 텍스트+이미지 (qwen3.6:35b + ComfyUI Qwen-Image) 도 A100 80 GB. 코딩 특화 (qwen3-coder-next:q8_0) 는 H100 80 GB×2 또는 GB10.

## ComfyUI 성능 메모

ComfyUI 는 `--highvram` 으로 기동되어 모델을 GPU 메모리에 상주시킵니다 (DGX Spark 의 GB10 unified memory 124 GB 를 정상 활용). DGX Spark 1024×1024 측정 예시:

| 모델 | step | 1장 |
|---|---|---|
| Qwen-Image Q8_0 GGUF | 25 | ~10 분 |
| Qwen-Image-Edit Q8_0 GGUF | 25 | ~10 분 |
| FLUX.1-schnell FP16 | 4 | (측정 필요) |
| FLUX.1-dev FP16 | 20 | (측정 필요) |

Qwen-Image GGUF 가 느린 것은 **GGUF Q8_0 의 dequantization 비용** 때문이며 메모리 부족이나 swap 문제가 아닙니다. Blackwell GB10 의 FP4/FP8 텐서코어를 Q8_0 GGUF 가 활용하지 못 해 매 step 마다 uint8 → bf16 dequant + matmul 을 소프트웨어로 처리합니다. 더 빠르게 하려면 BF16 safetensors (~40 GB) 또는 fp8 quant 를 받아 사용하세요 — `comfyui-shim/workflows/qwen-image-*.json` 의 `unet_name` / `class_type` 만 교체하면 됩니다.

## Ollama 메모리 관리

Ollama 환경변수 설정 (`KEEP_ALIVE`, `MAX_LOADED_MODELS` 등) 은 [Ollama 튜닝 가이드](ollama-tuning.md) 를 참고하세요.

## VRAM 부족 시 조치

**이미지 생성 서비스 필요 시만 기동**

```bash
# 평소에는 중지
docker compose stop comfyui-shim

# 이미지 생성 필요할 때만 시작
docker compose start comfyui-shim
```

**Ollama 모델 즉시 언로드**

```bash
# 특정 모델 강제 언로드 (호스트에서 직접 실행)
ollama stop qwen3.6:35b        # 또는 llama3.3:70b, qwen3-coder-next:q8_0 등
```

**노드별 분산 (멀티 노드 한정)**

`COMFYUI_URLS` 에 2개 이상 노드가 있으면 `comfyui-shim` 이 VRAM-aware 라우팅으로 각 노드의 ollama VRAM 사용량을 보고 한가한 쪽 ComfyUI 로 보냄. 한 노드에 70B/q8_0 같은 큰 LLM 이 로드돼 있어도 다른 노드에서 image-gen 이 정상 진행. 임계값은 `OLLAMA_VRAM_LOADED_THRESHOLD_BYTES` (기본 30 GiB).

**외부 image 모델 사용**

외부 LLM 에이전트 (gpt-*, gemini-*) 의 `image-generation` 은 ComfyUI 가 아닌 OpenRouter 경유 image API (`gpt-image-2` / `nano-banana`) 로 라우팅 → 로컬 GPU 비점유. OR 토큰 + LiteLLM spend 추적 비용 발생.
