# 사전 요구사항

[README](../README.md#어떤-시나리오로-띄울까) 에서 시나리오 (A: OR-only / B: Ollama-only / C: 하이브리드) 를 골랐다면 그에 맞는 prerequisite 만 챙기면 됩니다.

## 공통 — compose 호스트

| 요건 | 비고 |
|---|---|
| Linux x86_64 또는 aarch64 | macOS / Windows 미지원 |
| Docker + Docker Compose v2 | `curl -fsSL https://get.docker.com \| sh && sudo usermod -aG docker $USER` |
| 유틸 | `jq curl wget` |
| 디스크 | 50 GB 이상 (이미지 빌드 + 데이터) |
| RAM | 16 GB 이상 권장 |
| 포트 | 8000, 8080 미사용 |

`setup.sh` 0단계가 위 항목들을 자동 검증합니다.

## 시나리오별 추가 요건

### A — OpenRouter 만 (GPU 불필요)

- `OPENROUTER_API_KEY` (https://openrouter.ai/keys)
- 추가 prerequisite **없음**. compose 호스트만 있으면 됨.

### B — 로컬 Ollama (GPU 필요)

| 요건 | 비고 |
|---|---|
| NVIDIA GPU | 최소 10 GB VRAM (qwen3.5:9b 까지). 35b/gemma 까지 쓰려면 24 GB+ |
| NVIDIA Container Toolkit | [공식 가이드](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) — compose 호스트가 GPU 머신과 같을 때만 |
| 모델 디스크 | 100 GB+ (Ollama 모델 + ComfyUI 가중치) |

GPU 호스트에서 prerequisite:

```bash
./scripts/install-ollama.sh             # systemd, 0.0.0.0:11434
./scripts/download-ollama-models.sh     # GPU 자동 감지 추천 셋

./scripts/install-comfyui.sh            # 이미지 생성 쓸 거면
./scripts/download-image-models.sh      # 기본 셋 + HF_TOKEN 있으면 +flux-dev
```

VRAM 점유는 [GPU 메모리 가이드](../docs/gpu-memory.md) 참고.

### C — 하이브리드

A + B 의 요건을 모두 충족. native key (OpenAI/Anthropic/Google) 도 함께 사용.

## 멀티 노드

`OLLAMA_URLS` / `COMFYUI_URLS` 에 csv 로 여러 노드를 적으면 LB 됩니다. 각 노드에 위 install + download 를 동일하게 적용.

**주의**: `setup.sh` 는 노드 간 모델 **intersection** 만 등록합니다. 한 노드에만 pull 된 모델은 LibreChat 메뉴에 안 나옴 → 운영 시 모델 셋을 노드 간 동기화하세요.

## DGX Spark (GB10)

- ComfyUI 컨테이너 정상 동작 (arm64 + CUDA 12.8 자체 빌드)
- `nvidia-smi memory.total` 이 `[N/A]` 라서 `lib.sh` 헬퍼가 시스템 RAM 을 VRAM 으로 간주 — `download-ollama-models.sh` 의 추천 셋 결정에 사용
- `CUDA_VISIBLE_DEVICES` 가 빈 값이면 Ollama CPU 폴백 → `0` 으로 명시 ([Ollama 튜닝](../docs/ollama-tuning.md))
