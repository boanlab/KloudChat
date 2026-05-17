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
| Qwen-Image (NVFP4 / FP8 / Q8 GGUF) | ~20 / 20 / 22 GB |
| Qwen-Image-Edit (FP8 / Q8 GGUF) | ~20 / 22 GB |
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

ComfyUI 는 기본 모드 (`--normalvram`) 로 기동되어 생성 중에만 GPU 메모리를 점유하고 끝나면 RAM 으로 offload — LLM 과 VRAM 공존이 수월합니다. 첫 생성 시 모델 로드 latency (수~수십 초) 가 붙지만, 같은 모델을 연달아 호출하면 캐시 활용. DGX Spark 1024×1024 측정 예시:

| 모델 | step | 1장 |
|---|---|---|
| Qwen-Image Q8_0 GGUF (구) | 25 | ~10 분 |
| Qwen-Image-Edit Q8_0 GGUF (구) | 25 | ~10 분 |
| FLUX.1-schnell FP16 | 4 | (측정 필요) |
| FLUX.1-dev FP16 | 20 | (측정 필요) |

Qwen-Image quant 는 GPU 클래스별로 자동 선택됩니다 — `recommended_image_quant` 가 `gb10` / `blackwell-pro` / `blackwell-5090` 은 NVFP4 (edit 은 FP8 — NVFP4 변형 미배포), `ada-4090` 은 FP8, 그 외는 Q8_0 GGUF 로 분기. Blackwell FP4 텐서코어를 native 활용하면 동일 step 에서 GGUF 대비 4-5배 빨라집니다 (Q8 GGUF 는 매 step 마다 uint8 → bf16 dequant + matmul 을 소프트웨어로 처리). `./scripts/download-image-models.sh qwen-image:fp8` 같은 명시적 quant 지정도 가능 — 노드 호환성 디버그용.

shim 의 `COMFYUI_ALIAS_VARIANTS` 가 NVFP4 → FP8 → GGUF 순으로 preference 갖고, `/object_info` discovery 가 노드별로 실제 보유한 파일을 찾아 그에 맞는 workflow 를 자동 선택. 즉 같은 alias `qwen-image` 도 노드에 따라 다른 workflow 가 실행됩니다.

## Ollama 메모리 관리

Ollama 환경변수 설정 (`KEEP_ALIVE`, `MAX_LOADED_MODELS` 등) 은 [Ollama 튜닝 가이드](ollama-tuning.md) 를 참고하세요. 특정 모델을 즉시 언로드하려면 호스트에서 `ollama stop <model>` (예: `ollama stop llama3.3:70b`).
