# 환경변수 레퍼런스

> `.env` 변수가 무엇이고 누가 채우는지 (사용자 vs `gen-env.sh` vs `setup.sh`). 처음 띄우는 거면 [README](../README.md) 만 보면 된다.

- **`.env` 생성**: `./scripts/gen-env.sh` 가 `.env.example` 복사 + `change-me-*` → 랜덤 시크릿 치환.
- **직접 채울 변수**: **외부 키**(OpenRouter / `HF_TOKEN`) + **노드 토폴로지**(`NODES_*`, `*_URL`).

## 멘탈 모델 — 노드 구성

- **KloudChat = 멀티노드 기본 설계** — 역할을 여러 노드에 분산 운영.
- **단일노드** = 한 머신이 모든 역할 겸하는 특수 케이스.

| 노드 역할 | 무엇이 도는가 | 선언 변수 |
|---|---|---|
| **컨트롤 / compose** | `.env` 한 벌 + `docker-compose.yml` (LibreChat · RAG · 각종 shim) + `setup.sh` 오케스트레이션 | `NODE_LIBRECHAT` |
| **LiteLLM** | `docker-compose.litellm.yml` (게이트웨이 + DB + super-agent-shim) | `NODE_LITELLM` |
| **GPU (1+대)** | vLLM(컨테이너) + ComfyUI / Whisper(host native) — chat·Deep Research·coder·embed·이미지·전사 backend | `NODES_VLLM` / `NODES_COMFYUI` / `NODES_WHISPER` + `*_URL` |

핵심 흐름:

1. `.env` 는 **컨트롤 노드 한 곳**에만 둔다 (SoT).
2. `setup.sh` 가 `.env` 의 `NODE*_` ssh 타겟을 보고 각 노드에 install 스크립트를 **rsync + ssh 로 대행** (로컬이면 직접).
3. **scheduler** 가 GPU 노드들을 인벤토리해 vLLM 컨테이너를 KV-aware 로 배치하고, 배치 결과를 `*_URL` 로 박는다.
4. LiteLLM router 는 모델별 가용 노드 수만큼 deployment 를 등록해 `least-busy` LB.

→ **단일노드 설정**:
- `NODES_*` 를 비우거나 전부 `user@localhost`.
- `setup.sh` 가 ssh 우회(`is_local_host`) + backend URL `host.docker.internal` 기본값.

`.env` 5섹션 구성:

- **§1 토폴로지·필수 입력** — 직접 채움
- **§2** 자동 시크릿
- **§3** setup 발급
- **§4** 내부 endpoint
- **§5** backend URL

---

## 1. 토폴로지 + 필수 입력 (직접 채움)

**최소 조건**: `OPENROUTER_API_KEY` 또는 reachable vLLM 노드 ≥1 — 둘 다 없으면 `setup.sh` 거부 (로컬 GPU 없으면 OR 상용 모델, 노드 다운 시 OR 폴백).

### 1-1. 노드 ssh 타겟 (`NODES_*` / `NODE_*`)

멀티노드의 핵심 knob — `setup.sh` 가 이걸 **SoT** 로 각 역할 dispatch.

| 변수 | 타입 | 사용처 |
|---|---|---|
| `NODES_VLLM` | csv `user@host` | `setup.sh vllm` — install-vllm.sh + scheduler 가 배치 |
| `NODES_COMFYUI` | csv `user@host` | `setup.sh comfyui` — install-comfyui.sh |
| `NODES_WHISPER` | csv `user@host` | `setup.sh whisper` — install-whisper.sh |
| `NODE_LITELLM` | 단일 | `setup.sh litellm` — litellm stack + `.env`(SERVICE_KEY) 회수 |
| `NODE_LIBRECHAT` | 단일 | `setup.sh librechat` — `.env` 재배포. `manage.sh` 의 docker exec / mongosh 가 이 노드로 ssh |

- **원격 전제**: `ssh-copy-id` + `NOPASSWD` sudo, `~/.ssh/config` alias 가능. 비우면 로컬 fallback (`is_local_host` 가 hostname/IPv4/DNS 비교 → 자기 자신이면 ssh 우회).
- **원격 레포 경로**: 기본 `~/KloudChat`, `KLOUDCHAT_REMOTE_DIR=<path>` override. rsync 시 runtime 데이터(DB/로그/캐시) 제외.

### 1-2. 모델 backend URL (`*_URL`)

각 GPU 노드의 vLLM/ComfyUI/Whisper 주소. **`NODES_*` 만 채우면** `setup.sh all` 이 자동 갱신:

- **Whisper** — `NODES_WHISPER` → `WHISPER_URLS` derive.
- **vLLM/ComfyUI** — scheduler 가 placement 따라 기입.
- 직접 csv 기입도 가능.

```dotenv
# 단일노드 (gen-env.sh 디폴트)
COMFYUI_URLS=http://host.docker.internal:8188
WHISPER_URLS=http://host.docker.internal:9000
VLLM_GEMMA26_URL=          # 비워둠 — scheduler 가 placement 따라 채움 (NODES_VLLM=user@localhost 권장)

# 멀티노드 — csv 로 노드 나열 (자동 LB)
COMFYUI_URLS=http://gpu-node-1:8188,http://gpu-node-2:8188
WHISPER_URLS=http://gpu-node-1:9000,http://gpu-node-2:9000
VLLM_GEMMA26_URL=http://gpu-node-1:8001,http://gpu-node-2:8001
```

vLLM 변수 (포트는 `docker-compose.vllm.yml` 정본):

| 변수 | 포트 | 의미 |
|---|---|---|
| `VLLM_GEMMA26_URL` | 8001 | 메인 chat + 아티팩트 (gemma-4-26b; NVFP4 노드 NVFP4, RTX4090 은 AWQ-int4) |
| `VLLM_QWEN122B_URL` | 8002 | Deep Research / Slide Studio / 정밀 (qwen3.5:122b) |
| `VLLM_CODERNEXT_URL` | 8003 | 코딩 보조 (qwen3-coder-next, 노드 격리 — litellm 등록만) |
| `VLLM_BGE_M3_URL` | 8004 | RAG 임베딩 (bge-m3) |
| `VLLM_*_DIR` | — | 각 weight 디렉토리 (`gemma-4-26b` / `qwen3.5-122b-a10b` / `qwen3-coder-next`) |

- 노드별 launch 옵션(`*_GPU_UTIL`, `*_MAX_LEN`)은 scheduler 가 노드 `.env` 에 직접 박는다.
- `WHISPER_URL`(단수, §4)은 항상 `http://whisper-shim:9000` — shim 이 위 `WHISPER_URLS` 백엔드로 LB.

라우팅·LB 동작:

- **vLLM** — LiteLLM router 가 각 노드 직접 호출. `gen-litellm-config.sh` 가 각 URL `/v1/models` polling 으로 디스커버리 → 가용 노드 수만큼 deployment 등록 → `least-busy`. unreachable 은 warn + fallback.
- **ComfyUI** — shim 이 alias별 노드 매핑을 `/object_info` 로 캐시(TTL `MODEL_DISCOVERY_TTL_SEC`, 기본 300s) → `/queue` 깊이 LB, `prompt_id → 노드` in-memory.
- **Whisper** — `/health` reachable(10s 캐시) + in-flight + GPU VRAM 점유로 LB. 모든 노드 동일 `WHISPER_MODEL` 가정.

⚠ **노드 역할 제약**: `COMFYUI_URLS` 에는 **PRO5000(48G) / PRO6000(96G) / GB10(128G)** 만. RTX4090(24G)/RTX5090(32G)은 flux fullset + LLM 동시 적재 시 OOM — `install-comfyui.sh` hard fail(`--force` 우회). `qwen3-coder-next`(FP8 ~75G) 노드엔 다른 chat/embed 비공존. → [노드 역할 매트릭스](gpu-memory.md#노드-클래스별-권장-워크로드).

> 토폴로지 수정 후 반영: `./scripts/gen-litellm-config.sh && docker compose -f docker-compose.litellm.yml restart litellm` (또는 `setup.sh litellm`). LiteLLM 이 별도 노드면 생성된 `litellm-config.yaml` sync 후 재기동.

### 1-3. 외부 키

- `OPENROUTER_API_KEY` — commercial 카탈로그(gpt-*/claude-*/gemini-*/deepseek 등) + 외부 이미지(nano-banana 등) + Video Studio(Veo/Sora) + **로컬 노드 다운 시 OR 동일모델 폴백**. 없으면 해당 항목 미등록.
- `HF_TOKEN` — HuggingFace gated repo (`flux-dev` 다운로드 전제).

### 1-4. LiteLLM endpoint

`LITELLM_URL` — librechat / rag 가 바라보는 LiteLLM 주소이자 모든 cross-stack 호출의 단일 knob (`RAG_OPENAI_BASEURL=${LITELLM_URL}/v1` 등 compose env 에서 derive). 기본 `http://localhost:8000`, LiteLLM 이 별도 노드면 `http://<litellm-host>:8000`.

### 1-5. 브랜딩 / 공개 URL / 관리자 / 인증

- **브랜딩** — `APP_TITLE`(좌상단+탭, 기본 `KloudChat`) · `WELCOME_BACK_MESSAGE`(로그인 헤딩) · `HELP_AND_FAQ_URL`(`/`=숨김) · `CUSTOM_FOOTER`(채팅창 하단; 공백1개=숨김, 이 env 만 읽음).
- **공개 URL** — `DOMAIN_CLIENT` / `DOMAIN_SERVER`: 외부 노출 base URL(이메일 링크·OAuth 콜백·이미지 base path). 보통 둘 다 동일(`https://chat.example.com`). 비면 reset 메일이 `undefined/reset/...`.
- **관리자** — `ADMIN_ID`/`ADMIN_PW`/`ADMIN_EMAIL`: `setup.sh` librechat 단계가 LibreChat **ADMIN** 자동 생성(멱등). `ADMIN_PW` 는 `change-me-` 면 `gen-env.sh` 가 랜덤 치환(출력에 표시). `ADMIN_EMAIL`=로그인 email(=가상 키 발급 대상), `ADMIN_ID`=username. 공유 에이전트 카탈로그(`agent sync`)가 ADMIN owner 를 요구.
- **인증/이메일** (LibreChat 가 `/app/.env` 직접 로드) — `ALLOW_REGISTRATION=false` 권장(`manage.sh user create` 로만 발급) · `ALLOW_PASSWORD_RESET=true`("Forgot password?") · `EMAIL_*`(nodemailer SMTP, 기본 gmail; Gmail 은 2FA + [앱 비밀번호](https://support.google.com/accounts/answer/185833)).

---

## 2. 자동 시크릿 (gen-env.sh 생성)

| 변수 | 용도 |
|---|---|
| `JWT_SECRET` / `JWT_REFRESH_SECRET` | JWT 서명 / 리프레시 |
| `CREDS_KEY` / `CREDS_IV` | 자격증명 암호화 키 / IV |
| `MEILI_MASTER_KEY` | MeiliSearch 마스터 |
| `MONGO_ROOT_USER` / `MONGO_ROOT_PASSWORD` | MongoDB 루트 |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | RAG pgvector |
| `LITELLM_DB_USER` / `LITELLM_DB_PASSWORD` | LiteLLM PostgreSQL |
| `LITELLM_MASTER_KEY` | LiteLLM 마스터 (admin UI + 가상 키 발급) |
| `LITELLM_NUM_WORKERS` | uvicorn 워커 (기본 4, 워커당 ~600 MB RSS) |
| `CODE_INTERPRETER_API_KEY` | Code Interpreter 키 (= LibreChat `LIBRECHAT_CODE_API_KEY`) |
| `CODE_INTERPRETER_MINIO_USER` / `_PASSWORD` / `_BUCKET` | MinIO 자격 |

- **기본 사용자/DB 이름**: `kloudchat-librechat`, `kloudchat-ragdb`, `kloudchat-rag`, `kloudchat-litellm`, `code-interpreter`. 비밀번호는 랜덤.
- **LiteLLM stack 분리** (`docker-compose.litellm.yml`): 같은 노드면 `setup.sh all`, 다른 노드면 LiteLLM 노드에서 `setup.sh litellm`.
- **메모리**: ≈ 워커 × 600 MB + master ~200 MB.

## 3. setup.sh 가 발급

| 변수 | 발급 시점 |
|---|---|
| `LITELLM_SERVICE_KEY` | `setup.sh litellm` 5단계 |
| `RAG_OPENAI_API_KEY` | 동일 키 값 |

- **용도**: LibreChat / RAG API → LiteLLM 호출용 가상 키 (`manage.sh key issue --service librechat`).
- **비우면**: 재실행으로 재발급.

> **분리 노드**: LiteLLM 노드에서 발급한 `LITELLM_SERVICE_KEY` 를 LibreChat 노드 `.env` 로 복사해야 `setup.sh librechat` 사전 검증 통과.

## 4. 내부 endpoint (보통 기본값 유지)

| 변수 | 기본값 |
|---|---|
| `RAG_PORT` | `8000` |
| `EMBEDDINGS_PROVIDER` | `openai` |
| `EMBEDDINGS_MODEL` | `bge-m3` (LiteLLM model_name 일치; bge-m3 0대 + OR 키면 `text-embedding-3-small` 자동 swap) |
| `SEARXNG_INSTANCE_URL` | `http://searxng:8080` |
| `LIBRECHAT_CODE_BASEURL` | `http://code-interpreter:8000` |
| `SD_WEBUI_URL` | `http://comfyui-shim:7860` |
| `WHISPER_URL` | `http://whisper-shim:9000` (단수 — shim. 멀티노드 backend 는 `WHISPER_URLS` §1). whisper 는 GPU 전용 — 비면 Note Taker/youtube 비활성(OR STT 폴백 없음) |

### compose / shim 하드코딩 (`.env` 외)

`.env` 에 없고 docker-compose.yml service env 또는 shim Python default 에 박힘 — 변경하려면 해당 위치 수정 후 컨테이너 recreate:

| 변수 | 위치 | 기본값 | 용도 |
|---|---|---|---|
| `VRAM_LOADED_THRESHOLD_BYTES` | `comfyui-shim/app.py`, `whisper-shim/app.py` | `32212254720` (30 GiB) | VRAM-aware 라우팅 임계 — 초과 점유 시 다른 노드 우선 |
| `HEAVY_MODELS` | 두 shim Python default | `qwen3-coder-next` | 이 모델 서빙 노드는 image/whisper 후순위 강등 (csv) |
| `FIRECRAWL_BASE_URL` | `docker-compose.yml::librechat` | `http://crawl4ai-shim:8080` | webSearch firecrawl-compat scrape |
| `FIRECRAWL_API_KEY` | 동상 | `internal-shim-noauth` | client 빈 키 거부해서 dummy |
| `CHAT_MODEL` / `ARTIFACT_MODEL` | `docker-compose.litellm.yml::super-agent-shim` | `local/gemma-4-26b` / (비움) | Super Agent 단일패스 챗+아티팩트. `ARTIFACT_MODEL` 비우면 shim 이 챗(gemma) 경로에 concise/detox 지시문 주입 |
| `CHAT_TIMEOUT_SEC` | 동상 | 900 | stream 대기 |
| `LDR_SEARCH_TOOL` | `docker-compose.yml::deep-research` | `searxng` | LDR 기본 검색. tool 인자로 override |
| `LDR_SEARCH_ITERATIONS` / `LDR_SEARCH_QUESTIONS_PER_ITERATION` | 동상 | `2` / `1` | LDR question 생성 횟수 |
| `LDR_LLM_MODEL` | 동상 | `local/qwen3.5:122b` | LDR 호출 model_name (PLAIN) |
| `DEFAULT_TIMEOUT_MS` | `docker-compose.yml::crawl4ai-shim` | `30000` | scrape per-URL timeout (ms) |
| `CTX_FALLBACK` | `scripts/gen-litellm-config.sh` | `32768` | vLLM `/v1/models` 의 `max_model_len` 디스커버리 실패 시에만 쓰는 ctx 폴백 |

## 5. scheduler 단계 (setup.sh all 안에서 자동)

- **실행 시점/위치**: `setup.sh all` 이 host install 직후·LiteLLM 직전에 **컨트롤 노드**에서 `python3 -m scheduler apply -y`.
- **동작**: GPU 노드 인벤토리 → vLLM 컨테이너 KV-aware 배치 (site config ssh 타겟 직접 접속, rsync 없음).
- **직접 호출**: `setup.sh scheduler {inventory|plan|apply|sensitivity|eval}`.

**실행 조건** (전부 충족):

- `NODES_VLLM`(§1) 비어있지 않음.
- `python3 -m scheduler --help` 실행 가능 (`sudo apt install python3-pulp coinor-cbc python3-yaml`).
- `KLOUDCHAT_SKIP_SCHEDULER=1` 미설정.

| 변수 | 기본 | 의미 |
|---|---|---|
| `KLOUDCHAT_SCHEDULER_PRIORITIES` | `catalog.DEFAULT_PRIORITIES` (`chat, rag, agents-chain, artifacts, image, deep-research, …`) | scheduler 우선순위 csv (생략 시 코드 기본값) |
| `KLOUDCHAT_SKIP_SCHEDULER` | (off) | `1` = `all` 시퀀스 scheduler 강제 스킵 |
| `KLOUDCHAT_VLLM_WAIT_TIMEOUT` | `1200` (s) | vLLM probe deadline. 122b cold-start 가 GB10 등에서 600s 초과 가능해 상향 |
| `KLOUDCHAT_VLLM_WAIT_INTERVAL` | `10` (s) | probe 재시도 주기 |
| `KLOUDCHAT_DISPATCHED` | — | 내부 — ssh worker 표시 (재dispatch 차단) |

자세한 멀티노드 placement 는 [scheduler](scheduler.md).

---

## 참고 — 동작 요약

### 모델 등록

| 조건 | 결과 |
|---|---|
| OR 키 있음 | commercial + OR 임베딩(`text-embedding-3-small`) + 로컬 모델 OR 폴백 |
| OR 키 없음 | commercial / OR 임베딩 미등록 |
| vLLM 모델 가용 노드 ≥1 | `local/<id>` deployment × 노드 수 (route: `hosted_vllm/<id>`) |
| vLLM 가용 0 | 미등록 (로컬 모델 없음 → OR 만) |
| bge-m3 미가용 + OR 키 | `EMBEDDINGS_MODEL=text-embedding-3-small` 자동 swap |
| ComfyUI 노드에 가중치 있음 | alias 활성 |

native API 직결 미지원. `flux-dev` 는 `HF_TOKEN` 게이팅. 자세한 매트릭스는 [모델 설정](models.md).

### Context-trim callback (truncate_to_ctx)

- **동작**: `local/*` 호출 시 input 이 deployment `max_input_tokens` 초과면 oldest history 자동 trim.
- **`max_input_tokens` 산정**: vLLM `/v1/models` 의 `max_model_len` 디스커버리, unreachable 시 `lib.sh` ctx 맵 폴백.
- **옵션**: 전부 선택 — 기본값 그대로 동작. 정책은 `litellm-callbacks/truncate_to_ctx.py` docstring.
- **callback 자동 처리** (별도 env 없음):
  - history trim · `max_completion_tokens` clamp · input-aware output cap.
  - stub-user inject — `role:user` 요구 모델에 system-only 호출이면 `{role:user, content:"Continue."}` 주입.

| 변수 | 기본 | 용도 |
|---|---|---|
| `KC_TRUNCATE_MODE` | `preserve_first` | `preserve_first`=system+첫 user+마지막 user 보호·중간 oldest drop. `drop_oldest`=system+마지막 user 만 |
| `KC_TRUNCATE_SUMMARIZE` | (off) | `1`=drop turn 을 LLM 요약해 system 끝에 `<earlier_conversation_summary>` 합침 (opt-in) |
| `KC_TRUNCATE_SUMMARY_MODEL` | `local/gemma-4-26b` | 요약 호출 모델 |
| `KC_TRUNCATE_SUMMARY_TIMEOUT_SEC` | `30` | timeout. 초과 시 일반 notice 폴백 |
| `KC_TRUNCATE_SUMMARY_MAX_TOKENS` | `400` | summary 생성 max_tokens |
