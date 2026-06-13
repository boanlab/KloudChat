# Scheduler

- **멀티노드 vLLM** 운영 시 노드별 모델·config 자동 결정
- **단일노드 vLLM** 환경에선 불필요 (`KLOUDCHAT_SKIP_SCHEDULER=1` 또는 미호출)

## 언제 필요한가

- vLLM 노드 ≥ 2 — 모델 여러 개 (chat-gemma4-26b / chat-122b / coder-next / embed-bge-m3 / image-flux) 를 메모리 한도 안에 배치
- 노드별 GPU class 차등 — 큰 노드 32K, 작은 노드 16K 식으로 적용
- unified-memory 노드 (GB10) — OS / page cache reserve 가 vLLM 동거에 영향 ([usable_vram_gb](#usable_vram_gb-노드별-cap))

미디어/전사 백엔드도 placement 에 반영 (vLLM 과 VRAM 경합 회피):

- **이미지 + 비디오** — `image-flux`(COMFYUI) 워크로드 **하나**가 ComfyUI 서비스(FLUX 이미지 + LTXV 비디오) 대표
  - `image`·`video` feature 가 공유, 30 GiB reserve 로 vLLM-heavy 노드 packing 차단
  - ComfyUI 는 모델 스왑(직렬 큐) → reserve 는 더 큰 단일 워크플로 peak(=이미지) 기준
- **Whisper** — 전 whisper 노드에 설치 + shim LB 서비스라 "단일 노드 배치"가 아님
  - **running 노드마다 co-tenant reserve**(~3 GiB)로 헤드룸 확보 ([memory_model.node_co_resident_reserve](../scheduler/memory_model.py))

vLLM 노드 1개 + 모델 1개면 `.env` 의 `VLLM_*_URL` + `VLLM_*_GPU_UTIL` 정적 설정으로 충분 — scheduler 우회.

## 구성 요소

```
scheduler/catalog.py             ← workload 별 config grid (max_len × gpu_util × kv_dtype)
scheduler/sites/kloudchat.yaml   ← 노드 ssh 타겟 + workload binding (운영자 작성, gitignored)
scheduler/solver/{milp,greedy}.py ← 우선순위 + 메모리 제약 → 노드별 placement
scheduler/applier.py             ← placement 를 노드별 .env 작성 + compose recreate
scheduler/inventory.py           ← nvidia-smi / docker ps 노드 상태 probe
```

## 사이트 설정 파일

`scheduler/sites/kloudchat.yaml` — 운영자 작성, `.gitignore` 대상. 처음 운영 시 example 에서 복사:

```bash
cp scheduler/sites/kloudchat.yaml.example scheduler/sites/kloudchat.yaml
$EDITOR scheduler/sites/kloudchat.yaml
#   nodes:        — node_id → ssh 타겟 (또는 dict + usable_vram_gb)
#   workloads:    — workload id → container_name / port / env_prefix / model_path
```

- 파일 없이 호출 시 `FileNotFoundError` + hint
- `node_id` — `NODES_VLLM` csv 에서 `setup.sh` 가 도출하는 값과 일치 (`scripts/lib.sh::nodes_to_hosts` — IPv4 마지막 옥텟 또는 hostname 첫 라벨)

## 사용

```bash
./scripts/setup.sh scheduler inventory          # 노드 상태 (GPU class, VRAM, 실행 컨테이너)
./scripts/setup.sh scheduler plan               # 목표 placement dry-run
./scripts/setup.sh scheduler apply -y           # 실제 적용 (.env 수정 + compose recreate)
./scripts/setup.sh scheduler plan --out plan.json
./scripts/setup.sh scheduler apply --plan plan.json

# scheduler 단독
python3 -m scheduler plan --priorities chat,rag,agents-chain,artifacts,image,deep-research
python3 -m scheduler sensitivity --add-node "255:gb10:128:user@new-node"   # spec: id:gpu_class:gib[:host], gpu_class 는 inventory 토큰 (gb10 / pro6000 ...)
```

- `setup.sh scheduler` — `NODES_VLLM` 을 `--hosts` 로 자동 패스하는 래퍼
- 직접 호출 시 `--hosts` 또는 `kloudchat.yaml` 의 `nodes:` 사용

## 우선순위 (two-phase placement)

solver 는 단일 풀이 안에서 두 단계를 가중치 분리로 처리한다 (`scheduler/solver/milp.py`):

- **phase 1 (diversity)** — 우선순위 순서로 각 워크로드를 *하나씩* 띄워 모든 capability 를 살린다 (`W_div`).
- **phase 2 (replica fill)** — 남은 capacity 를 고우선 워크로드 replica 로 채운다 (`W_rep`).

- **`W_div ≫ W_rep`** — 가장 낮은 우선순위 워크로드의 첫 인스턴스조차 어떤 워크로드의 두 번째 replica 보다 먼저 자리잡음
- **feature 순서** — phase 1 의 핵심, 앞 feature 의 requires_workload 가 먼저 자리
- **default** — `catalog.py::DEFAULT_PRIORITIES` (`chat, rag, agents-chain, artifacts, image, deep-research, vision-ocr, coding, mcp-deep-research, ...`)
  - chat 가 rank 1 인 이유: Super Agent 단일패스 챗 두뇌라 빠지면 기본 에이전트 자체가 비활성
- **override** — `KLOUDCHAT_SCHEDULER_PRIORITIES` 환경변수 또는 `--priorities` CLI

### 실사용 기반 우선순위 (`usage-priorities.sh`)

DEFAULT_PRIORITIES 는 손으로 잡은 사용-빈도 추정이다. 실제 트래픽으로 갱신하려면:

```bash
./scripts/usage-priorities.sh                 # 사용량 리포트(모델/스튜디오별 요청수·인/아웃 토큰·avg ms·billed $) + 도출 우선순위 (검토만)
./scripts/usage-priorities.sh --days 30        # 윈도우 30일
./scripts/usage-priorities.sh --apply          # 도출 우선순위로 scheduler apply 즉시 실행
```

- LiteLLM `/spend/logs` 집계 → vLLM 워크로드(chat/deep-research/coding/rag)를 요청수로 랭킹 → `--priorities` 변환
- 기본은 **검토 모드**(미적용) — 관리자가 `plan` 으로 영향 확인 후 `--apply`
- ⚠ apply 는 vLLM 재기동(가중치 재로드) 유발 가능 → 저트래픽 시간대 + diff 클 때만 (diff 없으면 no-op)

리포트 표에 포함되는 **Image Studio / Video Studio** 집계 방식:

- **로컬 FLUX/LTXV** — LiteLLM 완료가 아니라 과금 passthrough(`/localbill`·`/orvideo`, `api_base=billsink`/`openrouter…videos`) 로그로 집계
- **외부 nano-banana/Veo/Sora** — 모델 완료로 집계, `billed $` 컬럼에 명목 단가 합산
- 이미지·비디오는 같은 `image-flux`(ComfyUI) 워크로드 공유 → 별도 placement 결정 없음 → **우선순위 재정렬 제외** (리포트 전용, 기본 순서 유지)

## Feature constraints (placement 강제)

`scheduler/catalog.py::FEATURES` 의 각 feature — `min_effective_len` 으로 최소 ctx 강제. solver 가 조건 충족 config 만 선택.

| feature | `requires_workload` | `min_effective_len` | 의미 |
|---|---|---:|---|
| `chat` | `chat-gemma4-26b` | (없음) | 기본 chat+아티팩트 — 16K도 OK |
| `agents-chain` | `chat-gemma4-26b` | 16K | LibreChat agents endpoint |
| `vision-ocr` | `chat-gemma4-26b` | 8K | gemma-4-26b vision (멀티모달) |
| **`deep-research`** | `chat-122b` | **128K** | LDR ReAct + 누적 search |
| `mcp-deep-research` | `chat-122b` | 128K | 동일 (MCP 경유) |

- deep-research priority ON + 충분한 VRAM 노드 존재 시 → 한 노드에 chat-122b@128K 배치 (KV cache 추가 → VRAM 큰 노드 선호)
- Deep Research 는 plain `local/qwen3.5:122b` 사용

## usable_vram_gb (노드별 cap)

**unified-memory 노드 (GB10)** — `--gpu-memory-utilization` 이 nominal physical VRAM 가정 → OS + page cache + ComfyUI 와 충돌 (swap thrash / NVRM OOM). dict 형으로 적어 planner ceiling 을 physical 보다 낮춤:

```yaml
nodes:
  # 단순 string — physical VRAM (discrete VRAM 노드)
  "gpu-1": user@gpu-node-1

  # dict — usable_vram_gb 명시 (unified-memory 권장)
  "gpu-2":
    host: user@gpu-node-2
    usable_vram_gb: 96     # physical 128 GB 중 96 GB 만 planner 사용
```

- `effective_capacity = usable_vram_gb` — `sum(commitments) ≤ 96GB`
- 차이 (32 GB) 가 OS / page cache / co-resident 로 자동 reserve
- 개별 vLLM `--gpu-memory-utilization` 은 catalog 값 그대로 (rescale 시 KV 자리 사라져 startup OOM)

**discrete VRAM 노드 (PRO6000, RTX4090 등)** — `usable_vram_gb` 생략 → nvidia-smi auto-detect.

## 출력 해석

```
=== plan (milp) ===
score: coverage=26.12  kv_qos=37.27  affinity=2.10  migration=6  imbalance=0.0000
  [253] chat-gemma4-26b cfg=16K@0.45
  [253] embed-bge-m3    cfg=embed@0.15
  [254] chat-gemma4-26b cfg=32K@0.60
```

- `coverage` — 우선순위 가중치 × 등록 workload (높을수록 우선순위 workload 자리 잡음)
- `kv_qos` — 컨텍스트 QoS (effective ctx ÷ 최대 config ctx 비율의 우선순위 가중합; 높을수록 슬랙 VRAM 을 컨텍스트로 더 활용)
- `migration` — 현재 vs 목표 차이 (낮을수록 적은 recreate)
- `imbalance` — 노드 간 부하 편차
- `cfg=16K@0.45` — `catalog.py` 의 Config (max_len × gpu_memory_utilization)

**solver 종류** (`--solver milp|greedy`) — milp 정확하지만 노드 많으면 느림.

## scheduler 끄기

```bash
KLOUDCHAT_SKIP_SCHEDULER=1 ./scripts/setup.sh all
```

- `all` 의 scheduler apply skip → 정적 `VLLM_*_URL` csv 동작 (`VLLM_GEMMA26_GPU_UTIL` 등 default)
- 용도 — 단일노드 또는 노드별 .env 직접 관리 시

## 의존성

- ILP 풀이 — `python3-pulp` + `coinor-cbc` + `python3-yaml`
- `setup.sh scheduler` — 없으면 apt 자동 설치
- venv/pip 직접 관리 — `KLOUDCHAT_SCHEDULER_NO_AUTOINSTALL=1`

## cold-start race 회피

scheduler apply 후 같은 노드의 여러 vLLM 동시 cold-start → unified-memory 노드에서 NVRM OOM 위험 (weight mmap + GPU memory 요청 충돌). 운영 패턴:

```bash
# 1) 모든 vLLM stop  2) page cache 비움  3) 가장 큰 모델 (122b) 단독 startup  4) ready 후 나머지
ssh user@gpu-node 'docker stop vllm-gemma26 vllm-qwen122b vllm-bge-m3'
ssh user@gpu-node 'sync && sudo sh -c "echo 3 > /proc/sys/vm/drop_caches"'
ssh user@gpu-node 'cd /opt/cluster && docker compose -f docker-compose.vllm.yml up -d vllm-qwen122b'
curl -sf http://gpu-node:8002/v1/models | jq '.data[].max_model_len'
ssh user@gpu-node 'cd /opt/cluster && docker compose -f docker-compose.vllm.yml up -d vllm-gemma26 vllm-bge-m3'
```

setup.sh 의 `step_vllm_probe` — 자동 ready 대기 (`KLOUDCHAT_VLLM_WAIT_TIMEOUT/WAIT_INTERVAL` 조정).

## 같이 보면 좋은 문서

- [vLLM 튜닝](vllm-tuning.md) — gpu_memory_utilization / max_model_len 의미
- [GPU 메모리 가이드](gpu-memory.md) — 노드 클래스별 권장 워크로드
- [장애 대응](troubleshooting.md) — cold-start fail / swap thrash 진단
- [환경변수 레퍼런스](env-reference.md) — `KLOUDCHAT_*` 전체
