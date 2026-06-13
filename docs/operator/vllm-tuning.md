# vLLM 튜닝 가이드

- **vLLM** — 유일한 로컬 LLM 백엔드. docker compose 운영 (`docker-compose.vllm.yml`), LibreChat / LiteLLM stack 과 라이프사이클 분리.
- **평상시 chat profile 컨테이너 기동** — `setup.sh all` 의 **scheduler apply** 담당. `NODES_VLLM` 채워야 진입, cap fit + config 자동 tuning.
- **수동 운영** (개별 logs/status/restart, coder profile) — `./scripts/manage-vllm.sh` 전용.
- **LiteLLM 입장** — 외부 OpenAI 호환 백엔드, `.env::VLLM_*_URL` 로 지정.

## 설정 위치

- `docker-compose.vllm.yml` — 서비스 정의. command list 의 `${VAR:-default}` fallback 으로 튜닝.
- `Dockerfile.vllm` — base + pytest layer (cu129-nightly amd64 의 dynamo lazy import 회피). `install-vllm.sh` 가 pull 직후 자동 rebuild.
- `.env` — 노드별 override.

```bash
# 평상시는 setup.sh all (scheduler apply). 아래는 수동 운영용.
./scripts/manage-vllm.sh up --coder    # coder profile (vllm-codernext 단독, 노드 격리)
./scripts/manage-vllm.sh status        # 컨테이너 + healthcheck
./scripts/manage-vllm.sh logs          # follow
./scripts/manage-vllm.sh restart [svc]
./scripts/manage-vllm.sh down [--coder]
./scripts/manage-vllm.sh pull
./scripts/manage-vllm.sh up            # scheduler 우회 직접 (단일노드 + cap 여유 시만)
```

`up --recreate` — force-recreate. 재로드 소요 **gemma-4-26b ~3-5분 / qwen3.5:122b ~5-7분**.

## 모델 매트릭스

| alias | HF repo | 포트 | 역할 |
|---|---|---|---|
| `gemma-4-26b` | `nvidia/gemma-4-26b-A4B-it-NVFP4` (RTX4090 은 `cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit`) | 8001 | 메인 chat + artifact |
| `qwen3.5-122b-a10b` | `Qwen/Qwen3.5-122B-A10B-NVFP4` | 8002 | Deep Research (NVFP4, 노드 격리) |
| `qwen3-coder-next` | `Qwen/Qwen3-Coder-Next-FP8` | 8003 | 코더, 노드 격리 (외부 클라이언트 전용) |
| `bge-m3` | `BAAI/bge-m3` | 8004 | embed (pooling runner) |

served-model-name 은 LiteLLM 라우트 alias (`local/gemma-4-26b`, `local/qwen3.5:122b`, `local/qwen3-coder-next`, `bge-m3`).

## gpu_memory_utilization

- **gpu_memory_utilization** — 시작 시점 free VRAM 의 fraction. **weights + activations + KV cache** 포함.
- 아래는 `.env.example` default.

| 변수 | 기본값 | 절대값 (GB10 121 GiB) | 근거 |
|---|---|---|---|
| `VLLM_GEMMA26_GPU_UTIL` | `0.45` | ~54 GiB | NVFP4 ~14 + KV ~9-10 + mm-budget + cudagraph profiling (16K 동시 8 안정) |
| `VLLM_QWEN122B_GPU_UTIL` | `0.90` | ~109 GiB | NVFP4 weights ~62 + KV pool + cudagraph, 단독 노드 (대용량 KV 헤드룸) |
| `VLLM_CODERNEXT_GPU_UTIL` | `0.80` | ~97 GiB | 80B MoE FP8 ~80, 단독 노드 |
| `VLLM_BGE_M3_GPU_UTIL` | `0.05` | ~6 GiB | weights ~1 GiB |

- **ComfyUI 동거 노드** — ~26 GiB 미리 차감 후 합산 **≤ 0.65** 권장.
- **ComfyUI 없는 노드** — 합산 **≤ 0.85**.

## chat / Deep Research ctx 옵션 매트릭스

solver 가 enumerate 하는 **(max_len × gpu_util)** 조합. 출처 — `scheduler/catalog.py::_CHAT_GEMMA4_26B_CONFIGS` (chat), `_CHAT_122B_CONFIGS` (Deep Research).

| workload | name | max_len | gpu_util | 용처 |
|---|---|---:|---:|---|
| chat-gemma4-26b | `16K@0.35` / `16K@0.45` | 16,384 | 0.35-0.45 | 최소 floor (작은 카드 / ComfyUI·Whisper 동거 빠듯한 노드) |
| chat-gemma4-26b | `32K@0.45` ~ `32K@0.60` | 32,768 | 0.45-0.60 | long-context chat |
| chat-gemma4-26b | `64K@0.60` / `64K@0.70` | 65,536 | 0.60-0.70 | discrete GPU 권장 (gemma 상한 128K) |
| chat-gemma4-26b | `128K@0.50` ~ `128K@0.85` | 131,072 | 0.50-0.85 | heavy agent / vision-OCR — 대용량 노드 |
| chat-122b | `128K@0.85` | 131,072 | 0.85 | Deep Research 운영 기본 (현재 GB10 클러스터) |
| chat-122b | `128K@0.90` / `128K@0.92` | 131,072 | 0.90-0.92 | Deep Research 롱컨텍스트 — **PRO6000 / GB10 같은 대용량 노드** |
| chat-122b | `128K@0.90-tp2` | 131,072 | 0.90 | 단일 카드로 78 GiB 못 담는 노드에서 48G×2 TP=2 |

- **solver 선택 기준** — placement 우선순위 + `usable_vram_gb` cap + feature constraint.
- **슬랙 VRAM 활용** — 남는 VRAM 으로 컨텍스트 최대화. 노드 헤드룸 크면 gemma·122b 모두 더 긴 config 선택.
  - 예: gemma 가 ComfyUI/bge 와 동거하는 GB10 에선 32K.
- **122b 서빙** — plain `local/qwen3.5:122b`.

## gemma-4-26b quant 자동 선택

- **chat 모델 `gemma-4-26b`** — NVFP4 기본.
- **RTX4090 (NVFP4 미지원)** — `manage-vllm.sh up` 시 `detect_gpu_class` 가 `VLLM_GEMMA26_DIR` 을 **AWQ-int4 변종으로 swap**. 이미 명시값 있으면 존중.
- **fit 기준** — NVFP4 ~14 GiB / AWQ-int4 ~14 GiB → **24 GiB 카드부터 적재 가능**.
- **PRO5000 (48 GiB) 단독 적재 불가** — Deep Research (`qwen3.5:122b` NVFP4 ~62 GiB), coder (`qwen3-coder-next` FP8 ~75 GiB) → **PRO6000 / GB10 대용량 노드 격리**.

## startup time (healthcheck start_period)

| 서비스 | start_period | 비고 |
|---|---|---|
| `vllm-gemma26` | 300s | NVFP4 14 GiB + torch.compile + KV profiling |
| `vllm-qwen122b` | 600s | NVFP4 + torch.compile + KV 128K profiling, 5-7분 |
| `vllm-codernext` | 600s | 80B MoE + cudagraph, ~6분 |
| `vllm-bge-m3` | 60s | 1 GiB |

- **healthcheck** — `/v1/models` 200.
- **start_period 중 unhealthy** — 정상.

## 이미지 arch 자동 분기

`scripts/lib.sh::vllm_default_image` 가 `detect_arch` 로 결정 (`docker-compose.vllm.yml` 의 default 는 arm64):

| arch | GPU | 기본 이미지 |
|---|---|---|
| `arm64` | GB10 | `vllm/vllm-openai:nightly-aarch64` |
| `amd64` | RTX4090 / RTX5090 / PRO5000 / PRO6000 | `vllm/vllm-openai:cu129-nightly` |

**nightly 사용 이유** — NGC 안정판이 gemma-4 / qwen3.5-moe arch lag.

## model_name → vLLM URL 매핑

`gen-litellm-config.sh` 가 각 `model_name` 을 해당 URL 변수의 vLLM 노드로 등록. `/v1/models` 디스커버리, **가용 노드만**.

| `model_name` | vLLM URL 변수 |
|---|---|
| `local/gemma-4-26b` | `VLLM_GEMMA26_URL` |
| `local/qwen3.5:122b` | `VLLM_QWEN122B_URL` |
| `local/qwen3-coder-next` | `VLLM_CODERNEXT_URL` |
| `bge-m3` | `VLLM_BGE_M3_URL` |

- **chat (`gemma-4-26b`)** — 전 노드 vLLM 운영.
- **embed (`bge-m3`)** — PRO5000+ 노드.
- **coder / Deep Research** — 대용량 노드 격리.

## 환경변수 레퍼런스

### `.env`

| 변수 | 기본값 | 용도 |
|---|---|---|
| `VLLM_IMAGE` | (자동, arch) | 이미지 태그 override |
| `VLLM_MODELS_ROOT` | `/var/lib/vllm/models` | weight 경로 |
| `VLLM_GEMMA26_URL` | (빈 값) | **sentinel** — 채우면 chat profile (gemma + bge) 활성 |
| `VLLM_QWEN122B_URL` | (빈 값) | Deep Research profile 활성 |
| `VLLM_CODERNEXT_URL` | (빈 값) | coder profile 활성 |
| `VLLM_BGE_M3_URL` | (빈 값) | embed |
| `VLLM_GEMMA26_DIR` | `gemma-4-26b` | chat (RTX4090 자동 swap → AWQ-int4 변종) |
| `VLLM_QWEN122B_DIR` | `qwen3.5-122b-a10b` | Deep Research |
| `VLLM_CODERNEXT_DIR` | `qwen3-coder-next` | coder |
| `VLLM_GEMMA26_GPU_UTIL` | `0.45` | chat |
| `VLLM_QWEN122B_GPU_UTIL` | `0.90` | Deep Research |
| `VLLM_CODERNEXT_GPU_UTIL` | `0.80` | coder |
| `VLLM_BGE_M3_GPU_UTIL` | `0.05` | embed |
| `VLLM_GEMMA26_MAX_LEN` | `16384` | chat |
| `VLLM_QWEN122B_MAX_LEN` | `131072` | Deep Research (128K) |
| `VLLM_CODERNEXT_MAX_LEN` | `32768` | coder |
| `VLLM_QWEN122B_MAX_NUM_SEQS` | `128` | Deep Research 동시 seq cap (qwen3.5-MoE hybrid Mamba cudagraph capture 가 max_num_seqs ≤ Mamba cache blocks 요구; scheduler 기본 `_MNS_122B=128`) |

- **위 default 적용 범위** — 단일노드 / scheduler apply 미사용 시.
- **scheduler 환경** — `scheduler/applier.py` 가 노드별 `.env` 에 직접 기입, 노드마다 다른 값 적용 (예: 32 GB = 16K, 128 GB = 32K).
- **`*_MAX_LEN`** — LiteLLM 의 `max_input_tokens` 폴백.
  - 정상 시엔 `gen-litellm-config.sh` 가 `/v1/models` 에서 `max_model_len` 디스커버리해 deployment 별 emit. 노드별 차등이 router **ctx-aware 라우팅**까지 자동 전파.

### compose 인라인 (yaml 직접)

| 위치 | 값 | 용도 |
|---|---|---|
| `vllm-gemma26.command` | `--enable-auto-tool-choice --tool-call-parser gemma4` | gemma-4 tool-call. **`--reasoning-parser` 절대 추가 금지** (gemma 는 non-thinking) |
| `vllm-gemma26.command` | `--max-num-batched-tokens ${VLLM_GEMMA26_MAX_BATCHED_TOKENS:-16384}` | multimodal (vision) mm-budget — ≥16384 |
| `vllm-qwen122b.command` | `--enable-auto-tool-choice --tool-call-parser qwen3_xml` | qwen3.5 tool-call (XML) |
| `vllm-qwen122b.command` | `--reasoning-parser qwen3` | `<think>...</think>` → `reasoning_content` |
| `vllm-qwen122b.command` | `--max-num-batched-tokens ${VLLM_QWEN122B_MAX_BATCHED_TOKENS:-16384}` | prefill batch — ≥16384 |
| `vllm-codernext.command` | `--enable-auto-tool-choice --tool-call-parser qwen3_coder` | Claude Code/Codex 호환 |
| `vllm-codernext.command` | `--default-chat-template-kwargs '{"enable_thinking": false}'` | coder thinking off (latency + tool 정확성) |
| `vllm-{gemma26,qwen122b,codernext}.command` | `--kv-cache-dtype fp8` | KV cache fp8 (~50% 절약) |
| `vllm-qwen122b.command` | `--max-num-seqs ${VLLM_QWEN122B_MAX_NUM_SEQS:-128}` | hybrid Mamba cap |
| `vllm-bge-m3.command` | `--runner pooling --convert embed` | nightly embed API |

- **`environment.HF_TOKEN`** — gated weight 다운로드용. `.env` 자동 전달.
- **tool-call parser** — chat_template 출력 포맷과 1:1. 모델 family 변경 시 같이 교체.
- **reasoning-parser** — thinking-enabled 모델 (`qwen3.5:122b`) 에만.
- **`gemma-4-26b` (non-thinking)** — `--reasoning-parser` 금지. `<think>` 토큰 없음 → 파서 오작동.

## 같이 보기

- [모델 설정](models.md)
- [GPU 메모리 가이드](gpu-memory.md)
- [코딩 에이전트](../user/coding-agents.md)
