# 사전 요구사항

## 하드웨어

| 항목 | 최소 | 권장 |
|---|---|---|
| CPU | 8 코어 | 16 코어 이상 |
| RAM | 32 GB | 64 GB |
| 디스크 | 100 GB | 500 GB (모델 포함) |
| GPU (선택) | NVIDIA 10 GB VRAM | 24 GB VRAM 이상 |

VRAM 점유는 [GPU 메모리 가이드](../docs/gpu-memory.md) 참고.

## compose 호스트

- Linux x86_64 또는 aarch64
- Docker + Docker Compose v2 — `curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER`
- 유틸: `jq curl wget`

NVIDIA GPU 가 같은 머신에 있다면 NVIDIA Container Toolkit 도 필요 — [공식 가이드](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html). `./scripts/setup.sh` 가 위 항목들을 검증.

## 환경 파일

`.env` 가 없으면 `setup.sh` 가 0단계에서 거부합니다. compose 호스트에서:

```bash
./scripts/gen-env.sh        # .env 생성 (시크릿 자동 채움)
$EDITOR .env                # OPENROUTER_API_KEY / 외부 API 키 / OLLAMA_URLS / COMFYUI_URLS / HF_TOKEN
```

`OPENROUTER_API_KEY` 또는 reachable 한 `OLLAMA_URLS` 노드 둘 중 하나는 필수.

## 모델 호스트 (GPU 노드)

compose 호스트와 같은 머신이거나 별도 머신. OpenRouter 만 쓸 거면 생략 가능:

```bash
./scripts/install-ollama.sh             # systemd, 0.0.0.0:11434
./scripts/download-ollama-models.sh     # GPU 자동 감지 추천 셋

./scripts/install-comfyui.sh            # systemd, 0.0.0.0:8188 (이미지 생성 시)
./scripts/download-image-models.sh      # 기본 셋, HF_TOKEN 있으면 +flux-dev
```

여러 GPU 노드를 두면 compose 호스트 `.env` 에 csv 로 나열 (`OLLAMA_URLS`, `COMFYUI_URLS`). `setup.sh` 가 노드 간 모델 **intersection** 으로 discovery 해서 `litellm-config.yaml` / `librechat.yaml` 에 자동 등록.

## DGX Spark (GB10)

- ComfyUI 컨테이너 정상 동작 (arm64 + CUDA 12.8 직접 빌드)
- `nvidia-smi memory.total` 가 `[N/A]` — `setup.sh` 가 시스템 RAM 을 VRAM 으로 간주
- `CUDA_VISIBLE_DEVICES` 빈 값이면 CPU 폴백 → `0` 으로 명시 ([Ollama 튜닝](../docs/ollama-tuning.md))
