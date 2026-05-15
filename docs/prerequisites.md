# 사전 요구사항

[README](../README.md#빠른-시작) 에서 시나리오 (A: 로컬 Ollama / B: OR-only / C: 로컬 + OR / D: 풀 하이브리드) 를 골랐다면 그에 맞는 prerequisite 만 챙기면 됩니다.

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

### A — 로컬 Ollama (GPU 필요)

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

VRAM 점유는 [GPU 메모리 가이드](gpu-memory.md) 참고.

### B — OpenRouter 만 (GPU 불필요)

- `OPENROUTER_API_KEY` (https://openrouter.ai/keys)
- 추가 prerequisite **없음**. compose 호스트만 있으면 됨.

### C — 로컬 Ollama + OR

A 의 GPU 요건 + B 의 OR 키. native API 계정은 없음.

### D — 풀 하이브리드

C 의 요건 + native API 키 (OpenAI/Anthropic/Google 중 가지고 있는 것). 키 있는 provider 만 native 로 등록되고 나머지는 OR fallback.

## 멀티 노드

`OLLAMA_URLS` / `COMFYUI_URLS` 에 csv 로 여러 노드를 적으면 LB 됩니다. 각 노드에 위 install + download 를 동일하게 적용.

**Ollama**: `setup.sh` 는 union 디스커버리로 모델별 보유 노드를 매핑하고, 보유 노드 수만큼 deployment 를 등록합니다. 한 노드만 pull 한 모델은 그 노드로만, 여러 노드에 pull 한 모델은 router 가 `least-busy` 로 LB. 이기종 GPU (예: 큰 모델은 큰 노드에만, 작은 모델은 전 노드에) 그대로 활용 가능.

**ComfyUI**: 노드 간 모델 **intersection** 만 등록 (이미지 모델 대용량 + 워크플로 stateful 특성). 한 노드에만 받은 이미지 모델은 메뉴에 안 나옴 → 노드 간 가중치 동기화 권장.

## DGX Spark (GB10)

- ComfyUI 컨테이너 정상 동작 (arm64 + CUDA 12.8 자체 빌드)
- `nvidia-smi memory.total` 이 `[N/A]` 라서 `lib.sh` 헬퍼가 시스템 RAM 을 VRAM 으로 간주 — `download-ollama-models.sh` 의 추천 셋 결정에 사용
- `CUDA_VISIBLE_DEVICES` 가 빈 값이면 Ollama CPU 폴백 → `0` 으로 명시 ([Ollama 튜닝](ollama-tuning.md))
