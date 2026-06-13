# 성능 측정 (실측 throughput)

- KloudChat 운영 환경에서 **직접 측정한 값**.
- 측정 안 한 셀 = `N/A` — 해당 GPU·모델 조합 미운영, 또는 측정 명령 미실행.
- **원칙: nominal spec 추정치 금지, 실측값만 기입** — `N/A` 는 공란 유지, 새 측정 결과만 추가.

## 측정 조건

- **chat 모델**: `POST /v1/chat/completions`, 짧은 prompt (~20 tok) + `max_tokens=200`, `temperature=0`, single sequence (batch=1), warm 상태 (cudagraph capture 완료된 두 번째 호출 이후). 측정값은 `total_time` 기반의 *output 토큰 / 초*.
- **embedding (bge-m3)**: `POST /v1/embeddings`, 3개 짧은 문장 array, warm. 측정값은 *요청당 latency (ms)*.
- **image (FLUX-schnell GGUF)**: ComfyUI native API 의 `/prompt` + `/history` 폴링, 512×512, 4 steps, warm. 측정값은 *이미지당 latency (s)*.

- **공통**: vLLM backend, cudagraph capture 활성 (`--enforce-eager` 미적용).

## 전체 매트릭스 — 모델 × GPU × 양자화 × 컨텍스트

단일 GPU(`tensor-parallel=1`), `kv-cache=fp8`, `max-num-seqs=128`, warm(서버 기동 시 cudagraph capture 완료 + 측정 전 워밍업) 기준. 측정 하네스: `runcfg.sh`(컨텍스트 자동 탐색 + 용량 로그 추출) + `ctxbench.py`(컨텍스트 길이별 프롬프트로 단일·동시성 측정).

- **GPU**: **PRO5000** = amd64, 48GB (sm_120) · **PRO6000** = amd64, 96GB (sm_120) · **GB10** = arm64, 128GB unified (sm_121).
- **모델**: `gemma-4-26b`(주력 chat, 최대 128K) · `qwen3.5:122b`(A10b MoE 122B/active 10B, Deep Research, 최대 256K) · `qwen3-coder-next`(80B MoE, 최대 256K). 아키텍처 상한은 qwen 계열 256K(`max_position_embeddings=262144`), gemma 128K.

> **현 클러스터는 2×GB10** (122b 는 GB10·NVFP4 단일, 운영 max-len 128K). GB10·NVFP4 행은
> `.254` 실측이다(decode 거의 평탄 14→12 tok/s, KV/token 12.6 KB). **PRO5000/PRO6000·FP8 행은
> 현 하드웨어 부재로 미검증(참고용)**, aggregate 표는 동시성 방법론 차이 + 256K 미측정 주의.
> 운영 정답값은 `scheduler/catalog.py`.

### (1) 용량 — weights / KV 풀 / 최대 컨텍스트 / 256K 시 최대 동시성

vLLM 기동 로그(`gpu_worker`/`kv_cache_utils`)에서 추출. KV/token 은 양자화 무관(둘 다 fp8 KV) — 차이는 weights 크기에서 옴. 셋 다 256K 풀 컨텍스트 적재 가능.

> GB10·NVFP4 행 = `.254` 기동로그 실측(max-len 256K 구성): KV 풀 1,838,979 tok, maxconc@256K
> **7.02×**(로그 직접). 운영 128K 구성은 풀 1,745,130 → maxconc@128K 13.31×(풀은 구성마다 다름).
> **†** = 현 클러스터에 없는 구성(상단 caveat). FP8 122b(≈122 GiB)는 단일 GB10 미적재라 행 제거.

| 모델 (quant) | GPU | gpu_util | weights | KV 풀 (tokens) | maxconc @256K |
|---|---|---:|---:|---:|---:|
| qwen3.5:122b (NVFP4) | **GB10** | 0.85 | **73.2 GiB** | **1,838,979** | **7.02×** |
| qwen3.5:122b (NVFP4) | PRO5000 † | 0.92 | 21.5 GiB | 1,855,329 | 7.08× |
| qwen3.5:122b (NVFP4) | PRO6000 † | 0.92 | 21.5 GiB | 6,366,644 | 24.3× |
| qwen3.5:122b (FP8) | PRO5000 † | 0.90 | 34.2 GiB | 510,063 | 1.95× |
| qwen3.5:122b (FP8) | PRO6000 † | 0.90 | 34.2 GiB | 4,832,390 | 18.4× |
| coder 80B (FP8) | PRO6000 | 0.95 | 74.9 GiB | 1,069,716 | 4.08× |
| coder 80B (FP8) | GB10 | 0.85 | 74.9 GiB | 2,063,326 | 7.87× |
| coder 80B (FP8) | PRO5000 (TP=2) | 0.90 | 75 GiB (37.5/card) | 517,945 | 1.98× |
| gemma-4-26b (NVFP4) | PRO5000/PRO6000/GB10 | 0.45 | 14 GiB | 0.33M / 1.04M / 1.41M | (최대 128K) |

- **NVFP4 의 진짜 이득은 속도가 아니라 용량**: 같은 모델에서 weights 가 작아져(예 21.5 vs 34.2 GiB) KV 풀이 더 크고(같은 GPU) 256K 동시성이 더 높다(예: PRO6000 24.3× vs 18.4×). decode 속도는 오히려 NVFP4 가 더 느림(아래 (2)).
- PRO5000 FP8 qwen3.5:122b 는 KV 풀 510K(maxconc 1.95×)로 빠듯하지만 256K 단일 시퀀스는 적재됨. coder-80B 는 PRO5000 에선 디스크(37G) 부족으로 미측정(N/A).
- coder-80B(weights 75 GiB, FP8 실측 74.9)는 PRO6000(96G)에 단독 적재 시 다른 GPU 점유가 없어야 함 — 운영 prod(gemma/bge) 동시 적재 상태에선 OOM(첫 시도 실패).

### (1b) 컨텍스트별 최대 동시 시퀀스 (= KV 풀 ÷ 컨텍스트)

각 컨텍스트 길이를 **풀로 채운 요청을 몇 개까지 동시 적재**할 수 있나(KV 풀 / 컨텍스트, floor). "컨텍스트별 용량". 1=단일 시퀀스만 적재 가능.

| 모델 (quant) | GPU | 16K | 32K | 64K | 128K | 256K |
|---|---|---:|---:|---:|---:|---:|
| gemma-4-26b (NVFP4) | PRO5000 | 20 | 10 | 5 | 2 | — |
| gemma-4-26b (NVFP4) | PRO6000 | 63 | 31 | 15 | 7 | — |
| gemma-4-26b (NVFP4) | GB10 | 85 | 42 | 21 | 10 | — |
| qwen3.5:122b (FP8) | PRO5000 | 31 | 15 | 7 | 3 | 1 |
| qwen3.5:122b (FP8) | PRO6000 | 294 | 147 | 73 | 36 | 18 |
| qwen3.5:122b (FP8) | GB10 | 276 | 138 | 69 | 34 | 17 |
| qwen3.5:122b (NVFP4) | PRO5000 | 113 | 56 | 28 | 14 | 7 |
| qwen3.5:122b (NVFP4) | PRO6000 | 388 | 194 | 97 | 48 | 24 |
| qwen3.5:122b (NVFP4) | **GB10** | **112** | **56** | **28** | **14** | **7** |
| coder 80B (FP8) | PRO6000 | 65 | 32 | 16 | 8 | 4 |
| coder 80B (FP8) | GB10 | 125 | 62 | 31 | 15 | 7 |
| coder 80B (FP8) | PRO5000 (TP=2) | 31 | 15 | 7 | 3 | 1 |

- 단일 시퀀스 VRAM(컨텍스트 L) ≈ weights + L × (KV/token). KV/token 실측: **qwen3.5:122b ≈ 12.6 KB**(GB10·NVFP4, `.254` 256K 구성 22.14 GiB ÷ 1,838,979 tok; 구성/런마다 ±10% 변동), **coder ≈ 12.2 KB**, **gemma-4-26b ≈ 28 KB**(dense GQA — qwen MoE 는 하이브리드 Mamba 라 full-attention 레이어가 적어 KV/token 이 작다). fp8 KV 기준.
- 예: qwen3.5:122b 256K 1요청 KV ≈ 3.2 GiB(weights 73.2 + 3.2 = 76.4 GiB → 단일 GB10 적재). 16K 1요청 KV ≈ 0.20 GiB.

### (1c) 배치 VRAM — 모델 적재에 필요한 총 용량

vLLM 기동 로그 분해(`Model loading took` + `cudagraph` + non-torch). **오버헤드 ≈ 4 GiB**(cudagraph ~1.6 + non-torch/activation ~2.4). "최소 적재"는 KV≈0(컨텍스트 거의 없음) 기준 — 실제 서빙은 여기에 KV(아래 식)를 더해야 함.

| 모델 (quant) | weights | +오버헤드 | 최소 적재(KV≈0) | +256K 1seq KV | 적재 가능 GPU |
|---|---:|---:|---:|---:|---|
| gemma-4-26b (NVFP4) | 14 GiB | ~4 | **~18 GiB** | (최대 128K, +1.3) | 전부 (RTX4090 은 AWQ-int4) |
| qwen3.5:122b (NVFP4) | **73.2 GiB** | ~4 | **~77 GiB** | +3.2 | GB10 (실측) |
| qwen3.5:122b (FP8) † | ~122 GiB | ~4 | **~126 GiB** | +3.6 | 없음 (단일 GPU 미적재) |
| coder 80B (FP8) | 74.9 GiB | ~5 | **~80 GiB** | +3.1 | PRO6000(단독)·GB10 |

- GPU VRAM: **RTX4090 24GB / RTX5090 32GB / PRO5000 48GB / PRO6000 96GB / GB10 128GB(unified)**.
- **서빙 VRAM ≈ 최소적재 + (동시 N × 컨텍스트 L × KV/token)**. KV/token(§1b 실측): qwen3.5:122b 12.6KB, coder 12.2KB, gemma-4-26b 28KB. 예) qwen3.5:122b-NVFP4 로 32K×32동시 = 77 + 32×32768×12.6KB ≈ 77 + 13 = **~90 GiB**(→ GB10).
- `†` FP8 122b 는 weights ≈122 GiB 라 현 클러스터의 단일 GB10(128G)에도 안 들어간다 — 운영은 NVFP4 단일.
- coder-80B(~80G)는 **PRO5000 단일 카드 불가**(48G), PRO6000 은 다른 모델 동시 적재 시 OOM(95G 빠듯) — GB10(120G)이 여유. 운영 배치 시 단독 점유 필요.
- 단 **PRO5000 은 TP=2(2×48G=96G)로 coder-80B 적재 가능**(실측, weights 37.5/card + KV 풀 518K). 4-bit 양자화로 단일 카드(48G)에 넣는 건 NVFP4 추정 ~47G 라 KV 자리가 없어 비실용 — 2 카드 TP=2 가 정석.

### (2) 단일 요청 decode tok/s (batch=1, warm) — 컨텍스트별

순수 decode 속도(스트리밍 첫 토큰 이후). 컨텍스트가 길수록 KV attention 비용으로 완만히 감소.

| 모델 (quant) | GPU | 16K | 32K | 64K | 128K | 256K |
|---|---|---:|---:|---:|---:|---:|
| gemma-4-26b (NVFP4) | PRO5000 | 127 | 122 | 116 | 108 | — |
| gemma-4-26b (NVFP4) | PRO6000 | 151 | 144 | 137 | 127 | — |
| gemma-4-26b (NVFP4) | GB10 | 26 | 25 | 24 | 22 | — |
| qwen3.5:122b (FP8) | PRO5000 | 201 | 196 | 185 | 169 | 141 |
| qwen3.5:122b (FP8) | PRO6000 | **221** | 215 | 203 | 186 | 159 |
| qwen3.5:122b (FP8) | GB10 | 51 | 49 | 46 | 40 | 33 |
| qwen3.5:122b (NVFP4) | PRO5000 | 180 | 174 | 165 | 153 | 130 |
| qwen3.5:122b (NVFP4) | PRO6000 | 181 | 177 | 170 | 157 | 137 |
| qwen3.5:122b (NVFP4) | **GB10** | **14** | **14** | **14** | **13** | **12** |
| coder 80B (FP8) | PRO6000 | 173 | 169 | 161 | 149 | 129 |
| coder 80B (FP8) | GB10 | 47 | 45 | 41 | 36 | 29 |
| coder 80B (FP8) | PRO5000 (TP=2) | 217 | 213 | 205 | 190 | 170 |

- **GPU 서열(decode)**: PRO6000 > PRO5000 ≫ GB10. GB10 은 통합메모리 대역폭이 낮아 단일 decode 가 3~4× 느림.
- **GB10 운영(NVFP4): gemma-4-26b(26 tok/s) > qwen3.5:122b(14)** — NVFP4 dequant 오버헤드로 122b decode 가 느리다. (FP8 였다면 122b 의 MoE active 10B 가 dense 26B gemma 보다 빨라 51 vs 26 — (2) 표의 FP8 행 참조.)
- **NVFP4 < FP8 (decode)** — 3 GPU 공통. 4-bit dequant 오버헤드가 대역폭 절감을 상쇄. NVFP4 는 용량/동시성용이지 단일 속도용이 아님.
- coder-80B(active ~3B 추정) decode 는 qwen3.5:122b 와 비슷한 대역폭 특성. **PRO5000 TP=2(2장) 217 tok/s** 는 PRO6000 단일(173) 보다 빠름 — 2장 합산 대역폭(qwen3.5:122b TP=2 와 동일 패턴). 단 카드 2장 사용이라 단일 GPU 비교 아님.
- **PRO5000 qwen3.5:122b-FP8 을 TP=2(2장)로** 돌리면 단일 스트림 **~248 tok/s**(짧은 컨텍스트) — 단일 GPU 201 보다 빠르지만 카드 2장 사용이라 위 단일 GPU 표와 직접 비교는 아님. 2장 집계 대역폭이 단일 PRO6000(221)도 살짝 웃돔.
- **GB10 122b 실측** (`.254`, NVFP4·gpu_util 0.85, 2026-06): decode 16K→256K = **14/14/14/13/12 tok/s(거의 평탄)**, TTFT = **6.4/14.8/34.9/104.5/331.6 s**(16K~64K 는 64K 구성, 128K/256K 는 256K 구성에서 측정 — decode/TTFT 는 컨텍스트별이라 max-len config 무관. 현 운영은 128K). aggregate(중간 동시성, AGGTOK 80): 16K 11.1(c12)/32K 5.1(c8)/64K 2.2(c6)/128K 0.8(c4) tok/s — prefill 지배라 급감; 256K aggregate 는 prefill 과부하로 미측정.

> 주의: qwen3.5:122b MoE 는 `--enforce-eager`(cudagraph 비활성) 를 절대 쓰지 말 것 — single-request decode 가 4~16× 느려진다(PRO6000 기준 13 tok/s 까지 추락). cudagraph capture 가 켜진 기본 상태로 둔다.

### (3) TTFT — prefill 시간 (s), 컨텍스트별

긴 프롬프트를 실제로 넣었을 때 첫 토큰까지(prefill). 컨텍스트에 거의 선형, GB10 은 256K 에서 매우 김.

| 모델 (quant) | GPU | 16K | 32K | 64K | 128K | 256K |
|---|---|---:|---:|---:|---:|---:|
| gemma-4-26b (NVFP4) | PRO5000 | 0.9 | 1.9 | 4.0 | 9.5 | — |
| gemma-4-26b (NVFP4) | PRO6000 | 0.8 | 1.4 | 3.0 | 7.2 | — |
| gemma-4-26b (NVFP4) | GB10 | 2.2 | 4.1 | 9.0 | 23.0 | — |
| qwen3.5:122b (FP8) | PRO5000 | 2.2 | 2.2 | 5.9 | 18.4 | 63.1 |
| qwen3.5:122b (FP8) | PRO6000 | 1.4 | 1.6 | 4.4 | 13.8 | 47.5 |
| qwen3.5:122b (FP8) | GB10 | 3.8 | 7.1 | 17.5 | 50.9 | 150.6 |
| qwen3.5:122b (NVFP4) | PRO5000 | 1.3 | 2.7 | 7.5 | 17.8 | 63.1 |
| qwen3.5:122b (NVFP4) | PRO6000 | 0.6 | 1.6 | 4.5 | 13.9 | 47.7 |
| qwen3.5:122b (NVFP4) | **GB10** | **6.4** | **14.8** | **34.9** | **104.5** | **331.6** |
| coder 80B (FP8) | PRO6000 | 1.2 | 2.1 | 5.7 | 17.4 | 58.2 |
| coder 80B (FP8) | GB10 | 4.4 | 9.9 | 23.9 | 66.1 | **194.5** |
| coder 80B (FP8) | PRO5000 (TP=2) | 1.2 | 2.5 | 5.7 | 15.5 | 49.0 |

> NVFP4 TTFT ≈ 같은 GPU 의 FP8 (prefill 은 KV-dtype·연산 동일). GPU 서열은 prefill 도 decode 와 같음(PRO6000 > PRO5000 ≫ GB10, ~3×). coder 는 PRO5000 디스크 부족으로 미측정.

## 동시성 처리량 — aggregate output tok/s (vLLM continuous batching)

- 위 chat 표 = **단일 요청 (batch=1)** decode tok/s = "사용자 1명 체감 속도".
- 실제 시스템 처리량 = 동시 요청을 **continuous batching** 으로 묶으면 훨씬 높음 — 같은 가중치 read 로 여러 시퀀스 동시 decode → 대역폭 상각.
- 측정: `local/qwen3.5:122b`, `max_tokens=200`, 동시 요청 N개 동시 발사 후 `(N×200)/wall_time`. per-stream = 개별 요청 평균.

AGG = aggregate(전체 처리량), 괄호는 per-stream(개별 요청 평균) tok/s.

| 동시 요청 N | PRO5000 (NVFP4, 1-GPU) | GB10 (FP8) | PRO6000 (FP8, 1-GPU) |
|---:|---:|---:|---:|
| 1 | **178** (178) | **53** (53) | **218** (218) |
| 8 | **632** (79) | **221** (28) | **1,037** (130) |
| 16 | **1,635** (102) | **354** (22) | **1,734** (110) |
| 32 | **2,430** (76) | **480** (15) | **2,085** (66) |
| 64 | **3,378** (53) | **702** (11) | **4,014** (64) |

- PRO5000 측정 노드: NVFP4 single-GPU (`gpu_util=0.92, kv-cache=fp8`, KV 용량 ~1.85M tok → 동시 64 도 여유). 동시 16 에서 이미 ~1.6K tok/s — 흔히 보는 "벤치 1500 tok/s" 는 이 aggregate 수치지 batch=1 latency 가 아니다.
- GB10 측정 노드: 라이브 qwen3.5:122b (FP8, `gpu_util=0.45, max-model-len=16384`). conc=1 의 53 tok/s 는 위 단일 decode 표 GB10 16K(~51)와 같은 수준. gpu_util 0.45 라 KV 헤드룸이 작아 batching 이득이 일찍 포화 — 동시 64 에서 PRO5000 의 ~1/4.8, PRO6000 의 ~1/5.7. (운영 서버라 측정 중 다른 트래픽 섞였을 수 있음.)
- PRO6000 측정 노드: 라이브 qwen3.5:122b (FP8, `gpu_util=0.65, max-model-len=16384`). conc=1 218 은 단일 decode 표 PRO6000 16K(~221)와 같은 수준. 모든 동시성 구간에서 PRO5000 NVFP4·GB10 보다 빠르고 conc=64 에서 ~4K tok/s — 96G FP8 라 KV 헤드룸이 가장 커서 가장 멀리 스케일.
- per-stream 은 N 이 커질수록 감소 — 총 처리량은 늘지만 개별 사용자 체감 속도는 떨어지는 trade-off.

- 위 표 = **짧은 프롬프트(~20 tok)** 기준 aggregate = "처리량 상한".
- 긴 컨텍스트: 프롬프트마다 KV 풀 다량 소비 → 동시성 제한 + prefill 비용이 wall time 지배 → aggregate 급감 (전체 매트릭스의 `maxconc @256K` · TTFT 참조).
- 예: PRO5000 NVFP4 256K 는 **동시 7개 한계**.

### 컨텍스트별 aggregate tok/s (battery, max-num-seqs=128 매트릭스 설정)

각 컨텍스트 길이의 **실제 긴 프롬프트**로 동시 N개(괄호) 발사한 end-to-end aggregate `(N×200)/wall` — **prefill 포함**이라 긴 컨텍스트에선 prefill 이 지배해 급감. N 은 KV 풀 한계로 컨텍스트가 길수록 작아짐(긴 컨텍스트는 4~16 으로 캡).

| 모델 (quant) | GPU | 16K | 32K | 64K | 128K | 256K |
|---|---|---:|---:|---:|---:|---:|
| gemma-4-26b (NVFP4) | PRO5000 | 1312 (c20) | 666 (c10) | 280 (c5) | 96 (c2) | — |
| gemma-4-26b (NVFP4) | PRO6000 | 1941 (c32) | 1254 (c31) | 520 (c15) | 180 (c7) | — |
| gemma-4-26b (NVFP4) | GB10 | 578 (c32) | 412 (c32) | 160 (c16) | 55 (c8) | — |
| qwen3.5:122b (FP8) | PRO5000 | 195 (c31) | 88 (c15) | 32 (c7) | 11 (c3) | 3 (c1) |
| qwen3.5:122b (FP8) | PRO6000 | 292 (c32) | 121 (c32) | 44 (c32) | 14 (c32) | 4 (c18) |
| qwen3.5:122b (FP8) | GB10 | 51 (c32) | 25 (c32) | 11 (c16) | 4 (c8) | 1 (c4) |
| qwen3.5:122b (NVFP4) | PRO5000 | 156 (c32) | 77 (c32) | 27 (c28) | 11 (c14) | 3 (c7) |
| qwen3.5:122b (NVFP4) | PRO6000 | 294 (c32) | 121 (c32) | 45 (c32) | 14 (c32) | 4 (c24) |
| qwen3.5:122b (NVFP4) | GB10 | 60 (c32) | 28 (c32) | 12 (c16) | 4 (c8) | 1 (c4) |
| coder 80B (FP8) | PRO6000 | 215 (c32) | 87 (c32) | 34 (c16) | 11 (c8) | 3 (c4) |
| coder 80B (FP8) | GB10 | 36 (c32) | 18 (c32) | 8 (c16) | 3 (c8) | 1 (c4) |
| coder 80B (FP8) | PRO5000 (TP=2) | 159 (c31) | 79 (c15) | 35 (c7) | 13 (c3) | 4 (c1) |

- 이 표의 AGG 는 **prefill 포함 end-to-end** (위 "짧은 프롬프트" 표는 prefill 무시한 순수 decode 처리량 상한 — 그래서 훨씬 큼). 긴 컨텍스트 실사용 처리량은 이쪽이 현실적.
- NVFP4 가 같은 GPU FP8 보다 aggregate 가 약간 높은 구간이 있는 건(예: PRO6000 16K 294 vs 292, GB10 60 vs 51) KV 풀이 커서 동시성 여유가 더 크기 때문 — 단일 decode 는 여전히 FP8 이 빠름.

### 단일 PRO5000 — FP8 vs NVFP4 컨텍스트

- `qwen3.5:122b` 하이브리드 Mamba 레이어: **`max-num-seqs` 를 가용 Mamba cache block(48G 기준 ~223) 이하로 둬야** cudagraph capture 성공 (`max-num-seqs=128` 권장).
- 이 조건만 맞추면 **FP8 도 단일 PRO5000(48G)에서 256K 풀 컨텍스트 적재 가능** (KV 풀 510K tok, maxconc@256K 1.95×).
- NVFP4 는 weights 가 작아 KV 풀 3.6× 큼(1.86M tok), 256K 동시성도 7.08× 로 높음.
- 즉 단일 PRO5000 에서 **NVFP4 의 이점 = "컨텍스트 활성화 가능 여부"가 아니라 "같은 컨텍스트에서 동시성 여유"** (운영 자동 swap 의 실효 이득도 속도보다 이 동시성/메모리 마진).

## Embedding (bge-m3) — 요청당 latency (ms, warm)

| 모델 | RTX4090 | RTX5090 | PRO5000 | PRO6000 | GB10 |
|---|---:|---:|---:|---:|---:|
| `bge-m3` (3-문장 batch) | N/A | N/A | **~13** | **~46** | **~20-30** |

- embedding 은 **single forward pass** 라 cudagraph 이득 작음.
- PRO6000 이 GB10 보다 *약간 느린* 이유 = prefill latency 지배 (chat decode loop 와 다른 패턴).

## Image (ComfyUI FLUX GGUF) — 이미지당 latency (s)

ComfyUI native API(`/prompt` + `/history` 폴링), median of 3 warm(글로벌 prewarm 후), `--disable-dynamic-vram`. 측정은 해당 GPU 에 다른 부하 없는 clean 상태(prod vLLM stop).

| 모델 / 설정 | PRO5000 | PRO6000 | GB10 |
|---|---:|---:|---:|
| FLUX-schnell Q8 (512×512, 4 step) | **9.25** | **8.12** | **88.5** |
| FLUX-schnell Q8 (1024×1024, 4 step) | **8.45** | **7.49** | **78.2** |
| FLUX-dev Q8 (1024×1024, 20 step) | **16.13** | **16.12** | **183.7** |

- **레이턴시는 step 수가 주도** — schnell(4 step) ~8s, dev(20 step) ~16s. schnell 은 step 이 적어 고정비(t5xxl 텍스트 인코딩 ~9GB + VAE decode + 그래프 오버헤드)가 지배 → 512²가 1024²보다 빠르지 않음(둘 다 clean 에서 8~9s, 해상도 영향 작음).
- PRO5000 ≈ PRO6000 (이미지는 chat 만큼 큰 격차 없음 — 둘 다 sm_120 Blackwell, FLUX 가 GPU 를 포화시키지 못함). 단 dev(20 step)는 거의 동일.
- **GB10 은 ~10× 느림** (schnell ~80s, dev ~184s vs Blackwell ~8s/~16s) — 통합메모리·낮은 연산 대역폭. 변동도 큼(schnell 512 64~98s). ComfyUI 는 모든 노드에서 `--disable-dynamic-vram` 으로 통일(dynamic VRAM 의 회계 noise/SIGTRAP 회피).
- RTX4090/RTX5090 노드는 FLUX 부적합(`install-comfyui.sh` hard fail) — VRAM 24/32GB 로 FLUX fullset+LLM 동시 적재 시 OOM.
- 참고: PRO6000 을 prod 와 GPU 공유(라이브 트래픽)한 채 측정하면 중앙값은 거의 같고(~0.3–1s) 변동/꼬리 지연만 커짐.

## 측정 방법 (재현 가능)

### Chat tok/s

```bash
PAYLOAD='{"model":"local/gemma-4-26b","messages":[{"role":"user","content":"Write a 200-word essay about coffee."}],"max_tokens":200,"temperature":0,"stream":false}'
for i in 1 2 3 4; do
  curl -s -o /dev/null -w 'total=%{time_total}s\n' \
    -H "Content-Type: application/json" \
    "http://<vllm-host>:8001/v1/chat/completions" -d "$PAYLOAD"
done
# 첫 호출은 warmup (cudagraph capture). 2-4번째 평균이 warm tok/s ≈ 200 / 평균_time.
```

### Embedding latency

```bash
PAYLOAD='{"model":"bge-m3","input":["sentence one","sentence two","sentence three"]}'
for i in 1 2 3 4; do
  curl -s -o /dev/null -w 'total=%{time_total}s\n' \
    -H "Content-Type: application/json" \
    "http://<vllm-host>:8004/v1/embeddings" -d "$PAYLOAD"
done
# warm 상태에서 ms 단위 latency.
```

### Image latency

```bash
# ComfyUI native API 사용 — workflow JSON 은 comfyui-shim/workflows/flux-schnell-txt2img-gguf.json 참고.
# python 으로 /prompt 에 POST + /history polling.
```

## 측정 메모

- 이미지: amd64 노드 `vllm/vllm-openai:cu129-nightly`(sm_120) / GB10 `vllm/vllm-openai:nightly-aarch64`(sm_121).
- 공통 vLLM 설정: `tensor-parallel=1`(단일 GPU), `kv-cache-dtype=fp8`, `max-num-seqs=128`(qwen MoE 하이브리드 Mamba 의 cache-block 한계 회피), cudagraph 활성(`--enforce-eager` 미적용). `gpu_util` 은 노드별 — qwen3.5:122b NVFP4 GB10 0.85(현 운영); PRO5000/PRO6000 행은 구버전 0.90/0.92; coder 0.95(PRO6000)/0.85(GB10); gemma-4-26b 0.45. `max-model-len` 은 적재 가능한 최대(qwen 262144 / gemma 131072)로 자동 탐색.
- 컨텍스트별 측정: 목표 길이의 프롬프트를 실제로 넣어 단일 decode(스트리밍)·TTFT·동시성 측정. 동시성 상한은 KV 풀 기준 + 긴 컨텍스트는 4~16 으로 캡(거대 prefill fan-out 회피).
- NVFP4 는 GB10(sm_121)에서도 정상 동작 확인. 단 decode 속도는 3 GPU 모두 FP8 보다 느림(이점은 용량/동시성).
- coder-80B 는 단일 GPU 에 weights 75 GiB(FP8 실측 74.9) 라 PRO6000(96G)은 다른 GPU 점유 0 일 때만, PRO5000(48G)은 단일 카드 불가(TP=2 로는 가능). gemma/bge 동시 적재 상태에선 OOM.
- 동시성/aggregate 처리량은 위 "동시성 처리량" 표(짧은 프롬프트) 참조. idle 단일 측정값이며 실사용 동시성에선 총 throughput 이 더 큼.
- 측정 하네스: `runcfg.sh`(launch+용량 추출+battery), `ctxbench.py`(컨텍스트별 perf), `imgbench.py`(FLUX), `conc-bench.py`(동시성).
