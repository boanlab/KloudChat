# 사전 요구사항

[README](../README.md#빠른-시작) 에서 시나리오 (A: 로컬 Ollama / B: OR-only / C: 로컬 + OR) 를 골랐다면 그에 맞는 prerequisite 만 챙기면 됩니다.

## 공통 — compose 호스트

| 요건 | 최소 |
|---|---|
| OS | Linux x86_64 또는 aarch64 |
| Docker | Compose v2 |
| 유틸 | `jq curl wget` |
| 디스크 | 50 GB |
| RAM | 16 GB |
| 포트 | 8000, 8080 |

macOS / Windows 는 미지원. Docker 미설치 시 `curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER`. 디스크는 이미지 빌드 + 데이터 합산, RAM 은 권장치, 포트는 미사용 상태여야 함. `setup.sh` 0단계가 위 항목들을 자동 검증합니다.

## 시나리오별 추가 요건

### A — 로컬 Ollama (GPU 필요)

| 요건 | 최소 |
|---|---|
| NVIDIA GPU | 10 GB VRAM |
| 모델 디스크 | 100 GB |

10 GB VRAM 은 qwen3.5:9b 까지 — 35b 까지 쓰려면 24 GB+, 70b 는 40 GB+, q8 코더는 80 GB+ 필요. 모델 디스크는 Ollama 모델 + ComfyUI 가중치 합산.

Ollama / ComfyUI / Whisper 는 호스트에 systemd 네이티브로 설치되어 GPU 에 직접 접근하므로 **NVIDIA Container Toolkit 은 필요 없습니다.** 컨테이너 (LibreChat / LiteLLM / RAG API / comfyui-shim / whisper-shim) 는 네이티브 프로세스를 HTTP 로만 호출.

Whisper 는 자막 없는 YouTube 영상의 음성 인식 폴백 — 설치 안 하면 youtube MCP 가 OR `whisper-1` 로 자동 폴백 (OR 키 있을 때만).

GPU 호스트에서 prerequisite:

```bash
./scripts/install-ollama.sh             # systemd, 0.0.0.0:11434
./scripts/download-ollama-models.sh     # GPU 자동 감지 추천 셋

./scripts/install-comfyui.sh            # 이미지 생성 쓸 거면
./scripts/download-image-models.sh      # 기본 셋 + HF_TOKEN 있으면 +flux-dev

./scripts/install-whisper.sh            # YouTube 자막 없는 영상 전사 쓸 거면
./scripts/download-whisper-models.sh    # (선택) 모델 prewarm — 첫 호출 lazy-load 회피
```

VRAM 점유는 [GPU 메모리 가이드](gpu-memory.md) 참고.

### B — OpenRouter 만 (GPU 불필요)

- `OPENROUTER_API_KEY` (https://openrouter.ai/keys)
- 추가 prerequisite **없음**. compose 호스트만 있으면 됨.

### C — 로컬 Ollama + OR

A 의 GPU 요건 + B 의 OR 키. Commercial 모델 (OpenAI/Anthropic/Google) 은 전부 OpenRouter 경유 — native API 직결은 지원 안 함.

## 멀티 노드

`OLLAMA_URLS` / `COMFYUI_URLS` / `WHISPER_URLS` 에 csv 로 여러 노드를 적으면 LB 됩니다. 각 노드에 위 install + download 를 동일하게 적용.

**Ollama**: `setup.sh` 는 union 디스커버리로 모델별 보유 노드를 매핑하고, 보유 노드 수만큼 deployment 를 등록합니다. 한 노드만 pull 한 모델은 그 노드로만, 여러 노드에 pull 한 모델은 router 가 `least-busy` 로 LB. 이기종 GPU (예: 큰 모델은 큰 노드에만, 작은 모델은 전 노드에) 그대로 활용 가능.

**ComfyUI**: shim 이 union 디스커버리 (`/object_info` TTL 캐시) → 매 요청 alias 로 보유 노드 후보를 좁힌 뒤 `/queue` 깊이 LB. 노드별로 다른 가중치 셋을 받아도 OK (이기종 GPU OK), 같은 모델을 여러 노드에 받으면 자동 분산. 워크플로 run state 는 노드 stateful 이라 `prompt_id → 노드` 매핑 in-memory 유지.

**Whisper**: shim 이 `/health` 캐시 (10s TTL) 로 reachable 노드만 추리고, in-flight 카운터 + 같은 호스트 ollama VRAM 점유로 LB. 모든 노드가 동일 `WHISPER_MODEL` 을 서빙한다고 가정 — 노드별 다른 모델은 지원 안 함. 매 호출이 self-contained 라 stickiness 없음.

## DGX Spark (GB10)

- ComfyUI / Ollama / Whisper 는 모두 호스트 native (venv + systemd) — 컨테이너 아님. `install-comfyui.sh` 가 GB10 감지해 `torch 2.9.1+cu128` (NVFP4 dtype 노출) 로 자동 분기, 비-Blackwell 노드는 `2.7.1` 그대로
- `nvidia-smi memory.total` 이 `[N/A]` 라서 `lib.sh` 헬퍼가 시스템 RAM 을 VRAM 으로 간주 — `download-ollama-models.sh` 의 추천 셋 결정에 사용
- `CUDA_VISIBLE_DEVICES` 가 빈 값이면 Ollama CPU 폴백 → `0` 으로 명시 ([Ollama 튜닝](ollama-tuning.md))
