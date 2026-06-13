# 장애 대응

- **진입점** 문서. 자세한 reference 는 인접 docs.

## 첫 실행 후 점검 — "이게 떴으면 OK"

```bash
# 1) 컨테이너 healthy
docker compose ps                                       # LibreChat stack
docker compose -f docker-compose.litellm.yml ps         # LiteLLM stack

# 2) LiteLLM 도달
curl -sf http://localhost:8000/health/liveliness        # 200 OK

# 3) 모델 카탈로그
KEY=$(grep ^LITELLM_MASTER_KEY .env | cut -d= -f2)
curl -sf -H "Authorization: Bearer $KEY" http://localhost:8000/v1/models | jq '.data | length'

# 4) UI: http://localhost:8080 → 로그인 → dropdown 에 Super Agent 등장
```

- 전 컨테이너 `running (healthy)` → **정상**.
- `health: starting` **5분 이상** 지속 → 진단 필요.

## 컨테이너 restart loop

```bash
docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.RestartCount}}"
docker inspect <name> --format '{{.RestartCount}} / {{.State.Status}} / OOMKilled: {{.State.OOMKilled}} / ExitCode: {{.State.ExitCode}}'
docker logs --tail 100 <name>
```

- `OOMKilled: true` → `free -h` 로 확인.
- **cgroup OOM**: compose `mem_limit` / 호스트 RAM 부족.
- **NVRM OOM** (vLLM): 아래 [vLLM cold-start 실패](#vllm-cold-start-실패) 참고.

## vLLM cold-start 실패

증상: `vllm-gemma26` / `vllm-qwen122b` 등이 starting 무한 재시작, `Engine core initialization failed`.

```bash
docker logs vllm-gemma26 2>&1 | grep -B 5 "Engine core initialization\|CUDA\|out of memory" | head -30
sudo dmesg -T | grep -iE "nvrm|oom" | tail -10
```

| 증상 | 원인 | 조치 |
|---|---|---|
| `_initialize_kv_caches` fail | `--gpu-memory-utilization` 부족 (weight + KV 자리 없음) | `.env::VLLM_GEMMA26_GPU_UTIL` (또는 `VLLM_QWEN122B_GPU_UTIL`) 또는 [scheduler](scheduler.md) catalog 상향 |
| `max_num_seqs (...) exceeds available Mamba cache blocks` | qwen3.5:122b MoE hybrid Mamba 가 cudagraph capture 시 `max_num_seqs ≤ Mamba blocks` 요구 | `VLLM_QWEN122B_MAX_NUM_SEQS` 를 cap 이하 (보통 128). [vllm-tuning.md](vllm-tuning.md#환경변수-레퍼런스) |
| `ModuleNotFoundError: 'pytest'` (amd64 cu129-nightly) | base image dynamo lazy import 회귀, pytest layer 누락 | `install-vllm.sh --reinstall` 또는 `docker compose -f docker-compose.vllm.yml build` |
| `NVRM: Out of memory` (dmesg) | unified-memory (GB10) page cache + 동거 vLLM 점유 | `sync && sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'`. 반복 시 동거 vLLM `gpu_util` 합산을 [usable_vram_gb](scheduler.md#usable_vram_gb-노드별-cap) cap 안에 |
| weight 로딩 중 OS killer | RAM < weight | 다른 컨테이너 stop |

단독 startup 절차는 [scheduler.md::cold-start race](scheduler.md#cold-start-race-회피).

## swap thrash (mid-stream 멈춤)

vLLM KV cache 가 swap 으로 밀리면 토큰당 latency 수 초. UI "생각 중…" 정체.

```bash
free -h                                                  # Swap used 5GB+ 면 의심
cat /proc/sys/vm/swappiness                              # default 60
```

| 조치 | 비고 |
|---|---|
| `./scripts/tune-host.sh` | `vm.swappiness=10` 등 영구 |
| `sudo sysctl vm.swappiness=10` | 즉시 임시 |
| `sudo swapoff -a && sudo swapon -a` | swap 비움 — RAM 부족 시 OOM 위험 |
| scheduler `usable_vram_gb` cap | 근본 회피 |

## vLLM 노드 unreachable

`setup.sh litellm` 의 vLLM probe → TCP unreachable. UI 모델 안 보이면 LiteLLM 디스커버리 0건.

```bash
# 모델 포트 (chat 8001 / qwen122b 8002 / coder 8003 / bge-m3 8004) 응답 확인
curl -sf http://<vllm-host>:8001/v1/models | jq '.data[].id'
ss -tlnp | grep -E '8001|8004|8002|8003'
docker ps --filter name=vllm- --format '{{.Names}}\t{{.Status}}'
```

- 컨테이너 미기동 → GPU 노드에서 `./scripts/manage-vllm.sh up` (코더 노드는 `--coder`) 후 재실행.
- cold-start 무한 재시작 → [vLLM cold-start 실패](#vllm-cold-start-실패) 참고.

## LiteLLM 도달 안 됨

```bash
docker logs --tail 50 litellm
curl -sf http://localhost:8000/health/liveliness         # 200
curl -sf http://localhost:8000/health/readiness          # backend 검증
```

| 원인 | 조치 |
|---|---|
| `LITELLM_MASTER_KEY` 빈 값 | `gen-env.sh` 재실행 또는 수동 채움 |
| DB 마이그레이션 fail | `docker logs litellm-db` — postgres healthy |
| 분리노드 `LITELLM_URL` 미설정 | [env-reference.md](env-reference.md) |

## 모델 메뉴에 안 보임

```bash
KEY=$(grep ^LITELLM_MASTER_KEY .env | cut -d= -f2)
curl -sf -H "Authorization: Bearer $KEY" http://localhost:8000/v1/model/info | jq '.data | map(.model_name)'
docker exec litellm cat /app/config.yaml | grep -E "model_name|api_base" | head -40
```

- `v1/model/info` 에 있는데 UI 안 보임 → **agent 미생성** → `./scripts/manage.sh agent sync`.

## 로그인 실패 / 사용자 생성

```bash
./scripts/manage.sh user list
./scripts/manage.sh user create --id ... --name ... --username ... --password ...
```

- `ALLOW_REGISTRATION=false` (default) → **CLI 로만** 생성.
- **password 8자 미만** 거부.
- `manage.sh user create` → verified 자동 → SMTP 없이 로그인.
- **self-registration** 이 verified 안 됐다면 MongoDB 직접 수정:

```bash
docker exec mongodb mongosh \
  "mongodb://${MONGO_ROOT_USER}:${MONGO_ROOT_PASSWORD}@localhost:27017/LibreChat?authSource=admin" \
  --eval 'db.users.updateOne({email:"<id>"},{$set:{emailVerified:true}})'
```

## Deep Research / LDR fail

증상: "컨텍스트 윈도우 제한" / `[KloudChat] 입력이 모델의 컨텍스트 한도를 초과했습니다`.

```bash
# 1) Deep Research 모델 (local/qwen3.5:122b) 의 서빙 ctx 확인
KEY=$(grep ^LITELLM_MASTER_KEY .env | cut -d= -f2)
curl -sf -H "Authorization: Bearer $KEY" http://localhost:8000/v1/model/info | jq '.data[] | select(.model_name=="local/qwen3.5:122b") | {model_name, api_base: .litellm_params.api_base, actual_ctx: .model_info.actual_ctx_tokens}'

# 2) 안 보이면 scheduler 진단
./scripts/setup.sh scheduler inventory   # 노드별 vLLM + max_model_len
./scripts/setup.sh scheduler plan        # solver 가 122b 를 어떤 ctx config 로 배치했는지

# 3) MCP 자체
docker logs deep-research --tail 50
```

| 원인 | 조치 |
|---|---|
| 122b 미배치 | scheduler 가 122b 를 어느 노드에도 두지 못함. `usable_vram_gb` cap 상향 또는 다른 워크로드 축소 ([scheduler.md](scheduler.md#usable_vram_gb-노드별-cap)) 후 scheduler apply → `gen-litellm-config.sh` |
| 배치됐는데 fail | LDR 누적 input 이 서빙 ctx 초과. `LDR_SEARCH_ITERATIONS` 축소 / 결과 size 제한. callback 이 안전망 ([truncate_to_ctx.py](../../litellm-callbacks/truncate_to_ctx.py)) |
| 더 큰 ctx 필요 | 122b 를 더 높은 `max-model-len` (≥128K) config 로 서빙하는 노드를 둔다 — 별도 alias 없이 plain `local/qwen3.5:122b` 가 그 노드의 ctx 로 라우팅된다 |

## Super Agent dropdown 사라짐

증상: LibreChat 모델 메뉴에 `Super Agent` / `Image Studio` / `Deep Research` 가 없음.

원인:
- **scheduler** 가 cap 제약으로 chat-gemma4-26b 를 어느 노드에도 못 둠 → `lib.sh::super_agent_eligible` 가 false (single-stage 라 chat `local/gemma-4-26b` backend 가용만 요구).
- 같은 조건으로 **functional agent 3종 미생성**.

```bash
./scripts/setup.sh scheduler plan         # chat-gemma4-26b 가 plan 에 있는지
grep -E "^VLLM_GEMMA26_URL" .env          # 비어있으면 chat-gemma4-26b 미배치
```

| 원인 | 조치 |
|---|---|
| chat-gemma4-26b 미배치 (cap 부족) | 다른 워크로드 max_replicas 축소 또는 `usable_vram_gb` cap 상향 ([scheduler.md](scheduler.md#usable_vram_gb-노드별-cap)). 그 후 scheduler apply → `setup.sh librechat` |
| chat-gemma4-26b 있는데 sync 안 됨 | `./scripts/manage.sh agent sync` 재호출. mongoDB wipe 했다면 `setup.sh librechat` 전체 |

## 에이전트 생성/기본/카테고리

| 증상 | 원인 / 조치 |
|---|---|
| 일반 사용자가 agent 생성 불가 | **의도된 동작** — 생성은 ADMIN 전용 (`librechat.yaml interface.agents.CREATE:false` + sync 가 ADMIN 만 복구). 일반 사용자는 공유 카탈로그 사용만 가능. 정책은 [tools.md](tools.md#ai-agent-store--운영-정책) |
| 새 대화 기본 안 잡힘 | `librechat.yaml` 의 `modelSpecs`(`prioritize:true` + 단일 super-agent spec → Super Agent 기본; 보이는 핀 없음)가 적용돼 있다. 안 보이면 `librechat` 재기동 + 브라우저 하드 리프레시(번들/캐시). modelSpecs 끄려면 그 블록 제거 후 재기동 |
| 카테고리 변경 안 보임 | `agent sync` 가 카테고리 시드 → 브라우저 하드 리프레시. LibreChat 업그레이드 후엔 `ensureDefaultCategories` 가 기본 카테고리를 되살릴 수 있어 `agent sync` 재실행 |

## RAG (file_search) 비활성

증상: agent 의 `file_search` 도구가 호출돼도 결과 0, 또는 RAG 빌더가 "임베딩 backend 없음".

원인: scheduler 가 embed-bge-m3 를 어느 노드에도 두지 못함 + OR fallback 도 미설정.

```bash
grep -E "^VLLM_BGE_M3_URL|^OPENROUTER_API_KEY|^EMBEDDINGS_MODEL" .env
./scripts/setup.sh scheduler plan        # bge-m3 가 plan 에 있는지
```

| 상태 | 결과 |
|---|---|
| `VLLM_BGE_M3_URL` 비어있음 + `OPENROUTER_API_KEY` 있음 | `text-embedding-3-small` 자동 swap (~$0.02/1M, RAG 동작) |
| 둘 다 없음 | RAG 비활성. `setup.sh litellm` 의 vLLM probe 가 `EMBEDDINGS_MODEL` 을 swap 시도 |
| bge 빠진 채로 cap 늘리기 | scheduler cap 상향 또는 chat-gemma4-26b/이미지 max_replicas 검토 후 apply |

## 진단 helper

| 도구 | 용도 |
|---|---|
| `manage.sh user list` / `team list` / `key list` | LiteLLM 사용자/팀/가상키 |
| `manage.sh user usage [--user <email>]` | 사용자별 이번 달 spend vs 월 예산(used % · 리셋일) |
| `manage.sh user topup --user <email> --amount <N>` | 월 한도를 $N 일시 상향(spend=실사용량은 유지 → 통계 정확). 원장(`data/ledger/topups.json`)에 원래 한도 기록 → 월 리셋 후 자동 원복 |
| `manage-vllm.sh status` | vLLM health + 모델 |
| `manage-vllm.sh logs <svc>` | vLLM 로그 |
| `setup.sh scheduler inventory` | 노드별 GPU class / VRAM / 컨테이너 |
| `setup.sh scheduler plan` | 현재 → 목표 placement diff (dry-run) |
| `tune-host.sh --check` | sysctl 권장 vs 현재 |

## 운영자 knob

| 변수 | 기본 | 용도 |
|---|---|---|
| `KLOUDCHAT_VLLM_WAIT_TIMEOUT` | 1200s | vLLM ready 대기 deadline |
| `KLOUDCHAT_VLLM_WAIT_INTERVAL` | 10s | vLLM probe 주기 |
| `KLOUDCHAT_SKIP_SCHEDULER` | (off) | `setup.sh all` 에서 scheduler 스킵 |
| `KLOUDCHAT_SCHEDULER_PRIORITIES` | `catalog.DEFAULT_PRIORITIES` (chat, rag, agents-chain, artifacts, image, deep-research, …) | scheduler 우선순위 |
| `KLOUDCHAT_REMOTE_DIR` | `~/KloudChat` | rsync 대상 |

전체는 [환경변수 레퍼런스](env-reference.md).

## 처음으로 돌리기 (nuclear)

```bash
./scripts/setup.sh clean        # 모든 컨테이너 + bind-mount 삭제 (대화/사용자/RAG 임베딩 전부)
./scripts/gen-env.sh            # .env 재생성
./scripts/setup.sh all
```

- **복구 불가**. 실행 전 아래 디렉터리 먼저 복사:
  - `./data/mongodb`, `./data/meilisearch`, `./data/rag/postgres`, `./data/litellm/postgres`
  - **`./data/ledger`** — 발급 가상키 평문(`keys.json`)·topup 원장(`topups.json`)·팀 캐시(`teams.json`) 포함.
- **왜 `ledger` 가 load-bearing 인가**: LiteLLM 은 키 해시만 저장 → `keys.json` 분실 시 발급 키 평문 영구 복구 불가.

## 같이 보면 좋은 문서

- [환경변수 레퍼런스](env-reference.md) — `.env` 전체
- [scheduler](scheduler.md) — 멀티노드 vLLM 배치 + cold-start race
- [GPU 메모리 가이드](gpu-memory.md) — 노드 클래스별 워크로드
- [vLLM 튜닝](vllm-tuning.md) — gpu_memory_utilization / max_model_len
