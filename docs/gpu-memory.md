# GPU 메모리 가이드

## 서비스별 VRAM 점유

| 서비스 | 모델 | VRAM | 상시 점유 |
|---|---|---|---|
| Ollama | qwen3.5:9b (Q4) | ~5GB | 대화 후 2분 유지 |
| Ollama | qwen3.6:35b (Q4) | ~22GB | 대화 후 2분 유지 |
| Ollama | llama3.1:8b (Q4) | ~5GB | 대화 후 2분 유지 |
| Ollama | llama3.3:70b (Q4) | ~40GB | 대화 후 2분 유지 |
| Ollama | nemotron3:33b (Q4) | ~20GB | 대화 후 2분 유지 |
| Ollama | qwen3-coder-next (Q8_0) | ~84GB | 대화 후 2분 유지 |
| Ollama | bge-m3 (embed) | ~1.2GB | RAG 요청 시 |
| OpenRouter / native API | claude-* / gpt-5.5 / gemini-* | **0** | 외부 — native key 또는 OR 로 라우팅되는 모델은 로컬 GPU 사용 안 함 |
| ComfyUI | Qwen-Image (Q8_0 GGUF) | ~22GB | 요청 시만 |
| ComfyUI | Qwen-Image-Edit (Q8_0 GGUF) | ~22GB | 요청 시만 |
| ComfyUI | FLUX.1-dev / schnell (FP16) | ~24GB | 요청 시만, T5+CLIP 인코더 ~10GB 별도 로드 |

모든 서비스는 동일한 물리 GPU VRAM 을 공유합니다. 격리나 예약 없이 선착순으로 점유합니다.

## 시나리오별 필요 VRAM

| 시나리오 | 구성 | 필요 VRAM | 맞는 GPU |
|---|---|---|---|
| 텍스트 채팅 (소형) | qwen3.5:9b 또는 llama3.1:8b | ~6GB | RTX 3080 10GB |
| 텍스트 채팅 (중형) | qwen3.6:35b 또는 nemotron3:33b | ~22GB | RTX 3090 / 4090 24GB |
| 텍스트 채팅 (대형) | llama3.3:70b | ~40GB | A100 80GB |
| 텍스트 + 이미지 (Flux/Qwen-Image) | qwen3.6:35b + ComfyUI(Qwen-Image) | ~46GB | A100 80GB |
| 코딩 특화 | qwen3-coder-next:q8_0 | ~85GB | H100 80GB×2 / GB10 |

## ComfyUI 성능 메모

ComfyUI 컨테이너는 `--highvram` 으로 기동되어 모델을 GPU 메모리에 상주시킵니다 (DGX Spark 의 GB10 unified memory 124 GB 를 정상 활용). 측정 예시 (DGX Spark, 1024×1024):

| 모델 | step | 1 step | 1장 |
|---|---|---|---|
| Qwen-Image Q8_0 GGUF | 25 | ~20 s | ~10 분 |
| Qwen-Image-Edit Q8_0 GGUF | 25 | ~20 s | ~10 분 |
| FLUX.1-schnell FP16 | 4 | (측정 필요) | (측정 필요) |
| FLUX.1-dev FP16 | 20 | (측정 필요) | (측정 필요) |

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
