# GPU 메모리 가이드

## 하드웨어 × quant 매트릭스

- **로컬 LLM** 은 전부 **vLLM** 백엔드
- 두 아키텍처(amd64/arm64), 다섯 GPU 티어

| GPU | arch | VRAM | LLM quant | vLLM 이미지 |
|---|---|---|---|---|
| RTX4090 | amd64 | 24 GB | AWQ-int4 (NVFP4 미지원) | `vllm/vllm-openai:cu129-nightly` |
| RTX5090 | amd64 | 32 GB | NVFP4 | `vllm/vllm-openai:cu129-nightly` |
| PRO5000 | amd64 | 48 GB | NVFP4 | `vllm/vllm-openai:cu129-nightly` |
| PRO6000 | amd64 | 96 GB | NVFP4 | `vllm/vllm-openai:cu129-nightly` |
| GB10 | arm64 | 128 GB unified | NVFP4 | `vllm/vllm-openai:nightly-aarch64` |

- **`gemma-4-26b`**: 기본 NVFP4, RTX4090 에서만 AWQ-int4 변종으로 자동 swap (`download-vllm-models.sh` 가 GPU 클래스별 quant 자동 선택)
- 나머지 모델(**`qwen3.5:122b`** NVFP4, **`qwen3-coder-next`** FP8): quant 고정

## 서비스별 VRAM 점유

| 모델 | backend | weights | 운영 reserve (GB10 기준) |
|---|---|---|---|
| gemma-4-26b (NVFP4) | vLLM | ~16 GB (실측 15.9 GPU / 15.3 디스크) | **~48 GB** (`gpu_util 0.45`) — weights + KV fp8 (현 클러스터 32K) + CUDA graph + mm-budget |
| gemma-4-26b (AWQ-int4, RTX4090) | vLLM | ~14 GB | ~18–22 GB |
| qwen3.5:122b-A10b (NVFP4, Deep Research) | vLLM | ~73 GB (실측 73.2 GPU / 77.8 디스크) | **~78 GB+** (`gpu_util` 만큼 선점) — weights + KV fp8 + cudagraph 헤드룸. discrete GPU 권장 (GB10 unified memory 에선 ComfyUI/Whisper 동거 시 swap thrash). scheduler 의 `deep-research` priority 활성 시 자동 배치 |
| qwen3-coder-next (FP8, 80B MoE) | vLLM | 75 GiB (실측 74.9) | **~90 GiB** (`gpu_util 0.80`) |
| bge-m3 (embed) | vLLM | ~1.1 GB | **~6 GB** (`gpu_util 0.05`) — pooling runner overhead 큼 |
| FLUX.1-dev / schnell (Q8 GGUF) | ComfyUI | UNet 12 + Clip 9 + VAE 0.2 = **~21 GB** | GGUF dequant 후 BF16 GEMM — 품질 거의 무손실 |
| Whisper large-v3 (faster-whisper, float16; arm64/CPU → int8) | Whisper | ~1.5 GB | ~1.5 GB |

> vLLM 의 **운영 reserve** 는 `gpu_memory_utilization × total VRAM` 으로 정해지는 상한값이다. weights, KV cache, activation, CUDA graph 가 이 한도 안에서 동적으로 할당된다. 실측 사용량은 동시성과 context 길이에 따라 한도보다 작을 수 있다.

- **vLLM**: 시작 시 `gpu_memory_utilization` 만큼 선점, 컨테이너 라이프타임 동안 비점유 안 함
- **ComfyUI 모델**: 요청 시에만 로드. FLUX 계열은 T5+CLIP 인코더 ~10 GB 별도 점유
- **Whisper 모델**: 첫 호출 때 lazy-load 후 systemd 서비스 재시작 전까지 상주
  - 첫 호출 latency 부담 또는 에어갭 환경: `./scripts/download-whisper-models.sh` 로 prewarm 가능
- **OpenRouter 경유 commercial 모델**(`claude-*`, `gpt-*`, `gemini-*`): 외부 API 호출이라 로컬 GPU 미사용 (native API 직결 미지원, 모두 OpenRouter 경유)
- 모든 로컬 서비스: 동일 물리 GPU VRAM 공유, 격리·예약 없이 선착순 점유

## 시나리오별 필요 VRAM

| 시나리오 | VRAM |
|---|---|
| 텍스트 채팅 (gemma-4-26b) | ~26 GB |
| 텍스트 + 이미지 (gemma + FLUX) | ~47 GB |
| Deep Research (qwen3.5:122b) | ~78 GB |
| 코딩 특화 (coder-next) | ~90 GB |

- **채팅**(gemma-4-26b): RTX4090 24 GB(AWQ-int4) ~ PRO5000 48 GB(NVFP4)
- **텍스트+이미지**(gemma-4-26b + ComfyUI FLUX Q8 GGUF): PRO5000(ComfyUI 전용 노드 분리 운영 시) / PRO6000 96 GB / GB10
- **Deep Research**(qwen3.5:122b)·**코딩 특화**(qwen3-coder-next): PRO6000 또는 GB10 같은 대용량 노드

## ComfyUI 성능 메모

- **`--disable-dynamic-vram` 강제 기동**(모든 ComfyUI 노드): dynamic VRAM staging 비활성 → 결정적 로딩 (GB10/Blackwell 의 메모리 회계 noise·SIGTRAP 회피). weight 는 요청 시 로드 후 상주 → 같은 GPU 의 LLM 과 VRAM 경합하므로 노드 VRAM 예산 주의
- 첫 생성 시 모델 로드 latency(수~수십 초), 같은 모델 연달아 호출 시 캐시 활용
- GB10 1024×1024 측정 예시:

| 모델 | step | 1장 |
|---|---|---|
| FLUX.1-schnell Q8_0 GGUF | 4 | cold-load 포함 ~3 분, 캐시 후 ~18 초 |
| FLUX.1-dev Q8_0 GGUF | 20 | cold-load 포함 ~4 분, 캐시 후 ~2-3 분 |

- **FLUX**: Q8 GGUF 만 운영(`flux-schnell-gguf` / `flux-dev-gguf`)
  - GGUF dequant 가 layer-by-layer → GPU peak 가 FP16 weight 전체보다 작고, weight ~12 GB 로 축소 → GB10 unified memory 에서 ComfyUI admission 마진 확보
  - FP16 변종도 download 스크립트로 수신 가능하나 일반 운영 비권장
- **shim 의 `COMFYUI_ALIAS_VARIANTS`**: FP16 → GGUF 순 preference, `/object_info` discovery 가 노드별 실제 보유 파일 탐색해 워크플로 자동 선택
  - 같은 alias `flux-schnell` 도 노드의 보유 변종에 따라 FP16 또는 GGUF workflow 실행

## 노드 클래스별 권장 워크로드

- **shim 라우팅**(`comfyui-shim` / `whisper-shim`): 컨테이너 환경변수에 등록된 백엔드만 사용
- 노드별 서비스 설치 여부가 곧 정책 — RTX4090/RTX5090 에 ComfyUI 설치 시 flux fullset + LLM 동시 적재로 OOM 위험 큼
- 아래 매트릭스 기준으로 install 스크립트 실행

| 노드 | VRAM | LLM 모델 | embed | Whisper | ComfyUI | Coder |
|---|---|---|---|---|---|---|
| RTX4090 | 24 G | gemma-4-26b AWQ-int4 | ○ | ○ | ✗ | ✗ |
| RTX5090 | 32 G | gemma-4-26b NVFP4 | ○ | ○ | ✗ | ✗ |
| PRO5000 | 48 G | gemma-4-26b NVFP4 | △ | △ | ○ (단, vLLM 분리 권장) | ✗ |
| PRO6000 | 96 G | gemma-4-26b NVFP4 (+ qwen3.5:122b 는 별 노드) | ○ | ○ | ○ | ✗ |
| GB10 | 128 G | gemma-4-26b + qwen3.5:122b (bge 는 ComfyUI 미적재 노드로 분산) | ○ | ○ | ○ (FP8 / GGUF) | ○ (별 노드) |

- **Deep Research**(`qwen3.5:122b-A10b` NVFP4, ~78 GiB+): 단일 노드 VRAM 제약상 gemma chat + bge + ComfyUI 동시 적재 곤란 → ComfyUI 미적재 대용량 노드(PRO6000 / GB10)로 분산 권장(`VLLM_QWEN122B_URL` / `VLLM_BGE_M3_URL` 별 노드)
- **gemma-4-26b**: 전 노드 공통 chat 모델 — `download-vllm-models.sh` 가 GPU 클래스별 quant(RTX4090=AWQ-int4, 그 외=NVFP4) 자동 선택

**ComfyUI 정책**:
- RTX4090/RTX5090: flux fullset(~22 GB) + LLM 동시 적재 OOM 위험 → `install-comfyui.sh` hard fail(`--force` 우회). 이미지 생성은 **PRO5000 / PRO6000 / GB10** 노드로 라우팅
- PRO5000(48G): vLLM gemma NVFP4 + bge + Whisper 풀적재 ~33.5 GB → ComfyUI generation(추가 ~21 GB) 시 48 GB 포화 → **ComfyUI 전용 노드 운영 또는 vLLM/bge/Whisper 타 노드 분산** 권장(운영자 책임)

**노드 규모별 동시성**:
- gemma-4-26b: RTX4090(AWQ-int4)부터 적재 가능
- PRO5000(48G) 이상 + 동시 사용자 8+: vLLM 의 continuous batching + PagedAttention 이득 명확(throughput 3-5x, TTFT 30-89x — [bench 결과](#vllm-동시성-bench))

**coder 노드 격리 정책**:
- `qwen3-coder-next`(FP8): 단독 노드 점유(vLLM FP8 reserve ~90 GiB), chat(gemma-4-26b) / Deep Research(qwen3.5:122b) / bge / ComfyUI 와 동거 불가
- `gen-litellm-config.sh` 가 노드별 vLLM `/v1/models` 로 보유 모델 감지 → coder 요청은 coder 노드로만, chat/embed 요청은 나머지 노드로 자연 분리
- ComfyUI/Whisper 는 설치 유지 가능(HA fallback — shim 라우팅이 우선 분배하되 fallback 으로 coder 노드도 응답 가능)

VRAM 합산 (대표 시나리오 — vLLM 은 reserve 기준):

| 노드 | 합산 | 점유 | 여유 |
|---|---|---|---|
| RTX4090 24G | vLLM gemma AWQ-int4 ~20 (bge 는 별 노드 권장) | ~20 G | ~4 G — bge/Whisper 는 별 노드 |
| RTX5090 32G | vLLM gemma NVFP4 26 + Whisper 1.5 | ~27.5 G | ~4 G — bge 는 별 노드 |
| PRO5000 48G | vLLM gemma NVFP4 26 + bge-m3 6 + Whisper 1.5 | ~33.5 G | ~14.5 G — Deep Research 는 별 노드 |
| PRO6000 96G | vLLM gemma NVFP4 26 + bge-m3 6 + Whisper 1.5 + ComfyUI gen 시 21 | ~54.5 G | ~41.5 G — qwen3.5:122b 별 노드 권장 |
| GB10 128G (chat) | vLLM gemma NVFP4 26 + bge-m3 6 + ComfyUI gen 시 21 + Whisper 1.5 + system 5 | ~59.5 G | ~68.5 G |
| GB10 128G (Deep Research) | vLLM qwen3.5:122b NVFP4 78 + Whisper 1.5 + system 5 | ~84.5 G | ~43.5 G |
| GB10 128G (coder) | vLLM coder FP8 90 + Whisper 1.5 + system 3 | ~94.5 GiB | ~33.5 GiB |

> ComfyUI 의 weight cache (FLUX Q8 GGUF ~21 GB) 가 generation 종료 후에도 process 에 남아 vLLM reserve 와 누적될 수 있다. `comfyui-shim` 의 admission control 은 `/system_stats` `vram_free` 에 ComfyUI 자기 PyTorch reserve 를 더한 값을 임계값과 비교해 generation 호출을 대기·거부함으로써 OOM 을 막는다. unified memory 노드 (`vram_total == ram_total`) 에선 `system.ram_free` 도 신호로 함께 보고 더 큰 값을 채택한다 — vLLM cudaMallocAsync reserve + OS page cache 가 `vram_free` 를 underreport 하지만 page cache 는 evictable 이라 실사용 가용량을 가린 거짓 신호이기 때문. 멀티 GB10 클러스터에서는 qwen3.5:122b / bge-m3 를 ComfyUI 미적재 노드로 분산하는 편이 안정적이다.

- **`download-vllm-models.sh`**(chat/Deep Research/coder/embed vLLM 모델): GPU 클래스별 권장 셋 + quant 자동 선택
- 노드별 보유 디스커버리로 LiteLLM `least-busy` 가 자연 라우팅

## vLLM 동시성 bench

GB10 의 MoE chat 모델 동시성 스케일링(참고치):

- 동시 8: 168 tok/s + TTFT p95 0.24s
- 동시 16: 273 tok/s — 선형에 가까운 스케일링
- continuous batching + PagedAttention 덕에 동시 사용자 8+ 환경에서 throughput 이 단일 요청 대비 크게 증가, TTFT 짧게 유지
- PRO5000/PRO6000/GB10 같은 대용량 노드에서 효과 큼

vLLM 운영 디테일은 [vLLM 튜닝](vllm-tuning.md).
