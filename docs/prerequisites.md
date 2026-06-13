# 사전 요구사항

[README](../README.md#빠른-시작) 의 단일 셋업(**로컬 GPU 기본 + OpenRouter 폴백**) 기준.

- **로컬 GPU 사용** — "공통 — compose 호스트" + 아래 "로컬 GPU 추가 요건"
- **OpenRouter 만 사용** — "공통 — compose 호스트" 요건만

## 공통 — compose 호스트

스택은 **LibreChat 측**(`docker-compose.yml`)과 **LiteLLM 측**(`docker-compose.litellm.yml`) 두 docker compose 로 분리. 같은 노드 동거 또는 노드별 분리 운영 가능:

| 토폴로지 | 구성 |
|---|---|
| 단일 노드 | compose 호스트 한 대에 모두 (로컬 GPU 쓰면 그 노드가 GPU 노드 겸용) |
| 분리 | LiteLLM 노드 + LibreChat(frontend) 노드 + (로컬 GPU 쓰면) GPU 노드 — 최대 3 노드 |

| 요건 | 단일 노드 | LibreChat 노드 (분리) | LiteLLM 노드 (분리) |
|---|---|---|---|
| OS | Linux amd64 또는 arm64 | Linux | Linux |
| Docker | Compose v2 | Compose v2 | Compose v2 |
| 유틸 | `jq curl wget` | `jq curl wget` | `jq curl wget` |
| 스택 디스크 (compose volumes + 이미지) | 50 GB | 40 GB | 10 GB |
| RAM | 16 GB | 8 GB+ (부하 시 5–7 GB) | 4 GB+ (워커 4 시 ~3 GB) |
| 포트 | 8000, 8080 | 8080 | 8000 |

- **로컬 GPU 사용 시 디스크** — 위 스택 디스크에 더해 **모델 디스크 100 GB+** 별도 필요 (아래 "로컬 GPU 추가 요건"의 "모델 디스크" 행)
- **단일 노드 + 로컬 GPU** — 합산 **150 GB+** 권장 최소
- **OS** — macOS / Windows 미지원
- **Docker 미설치 시** — `curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER`
- **RAM** — 워커 수(`LITELLM_NUM_WORKERS`, 기본 4 — 워커당 ~600 MB)와 동시 트래픽에 비례
- **자동 검증** — `setup.sh <role>` 의 0단계가 위 항목 검증

## 로컬 GPU 추가 요건 (vLLM / ComfyUI)

| 요건 | 최소 |
|---|---|
| NVIDIA GPU | RTX4090 24GB (gemma-4-26b AWQ-int4) |
| 모델 디스크 | 100 GB |

VRAM 요구 (모델별):

| 모델 | 요구 |
|---|---|
| 채팅 (gemma-4-26b) | RTX4090 24GB AWQ-int4 ~ PRO5000 48GB NVFP4 |
| Deep Research (qwen3.5:122b) | 80 GiB+ |
| coder (qwen3-coder-next FP8) | 90 GiB+ |

- **모델 디스크** — vLLM 가중치 + ComfyUI 가중치 합산

**Deep Research 활용 시 대용량 노드 권장**

- scheduler 의 `deep-research` feature 가 `qwen3.5:122b` 배치 (NVFP4 weights ~62 GiB + KV cache + cudagraph 헤드룸, 총 ~78 GiB+)
- **권장** — PRO6000 / GB10 같은 대용량 노드
- **GB10 등 unified memory 노드** — ComfyUI/Whisper 동거 시 swap thrash 위험 → [scheduler 의 usable_vram_gb](scheduler.md#usable_vram_gb-노드별-cap) 로 보수적 cap 또는 Deep Research 비활성
- **단일노드 / Deep Research 미사용 환경** — 무관

- **NVIDIA Container Toolkit 필요** — chat / Deep Research / coder / embed (bge-m3) 는 모두 vLLM 컨테이너로 서빙
- **ComfyUI / Whisper 백엔드** — 같은 install 스크립트가 아키텍처로 분기
  - **amd64 (RTX/PRO) 노드** — 컨테이너 (`docker-compose.media.yml`, `install-{comfyui,whisper}.sh` 가 `boanlab/kloudchat-{comfyui,whisper}` 이미지 pull·기동, `--reinstall` 시 로컬 빌드)
  - **GB10 (arm64) 노드** — systemd 네이티브 (이유: aarch64 ctranslate2 휠이 CPU-only 라 컨테이너 이점 적고, GB10 unified-memory 가 native 적재에 유리)
- **공통 배선** — 어느 쪽이든 백엔드는 published 포트로 노출, shim 컨테이너(comfyui-shim / whisper-shim)가 `COMFYUI_URLS` / `WHISPER_URLS` 로 HTTP 호출

**Whisper — 오디오 전사(STT) backend**

- **용도** — **Note Taker** 의 오디오 업로드 전사(「텍스트로 업로드」), **자막 없는 YouTube** 영상 전사
- **GPU 전용** — 미설치 시(OR 키만 있는 GPU-less 배포 포함) **Note Taker 에이전트 미생성 + youtube 도구 미부착**
- **OR 폴백 없음** — 다른 backend 와 달리 STT 는 OR 상용 폴백 없음

**`HF_TOKEN` (선택) — FLUX.1-dev (gated) 다운로드용**

- **발급** — https://huggingface.co/settings/tokens 에서 read 토큰 발급 → https://huggingface.co/black-forest-labs/FLUX.1-dev 에서 access 요청 수락
- **없으면** — `download-image-models.sh` 가 flux-dev 만 제외, flux-schnell 등 비-gated 모델은 그대로 다운로드

GPU 호스트에서 prerequisite:

```bash
./scripts/install-vllm.sh               # vLLM docker 이미지 + NVIDIA Container Toolkit
./scripts/download-vllm-models.sh       # GPU 자동 감지 추천 셋

./scripts/install-comfyui.sh            # 이미지 생성 쓸 거면
./scripts/download-image-models.sh      # 기본 셋 + HF_TOKEN 있으면 +flux-dev

./scripts/install-whisper.sh            # 오디오 전사(STT) 쓸 거면 — Note Taker 업로드 + YouTube 자막 없는 영상
./scripts/download-whisper-models.sh    # (선택) 모델 prewarm — 첫 호출 lazy-load 회피
```

> amd64 노드의 `install-{comfyui,whisper}.sh` 는 Docker Hub 의 `boanlab/kloudchat-{comfyui,whisper}` 이미지를 **pull** 한다 (퍼블리시: `./scripts/build-push-images.sh comfyui whisper`, amd64 단일아키). `--reinstall` 로 노드 로컬 빌드도 가능(ComfyUI 최초 빌드 수~수십 분). GB10(arm64)는 systemd venv 설치.

- **vLLM 서빙 대상** — chat (gemma-4-26b) / Deep Research (qwen3.5:122b) / coder (qwen3-coder-next) / embed (bge-m3)
- **vLLM 이미지 (아키텍처별)**
  - **amd64 노드 (RTX4090 / RTX5090 / PRO5000 / PRO6000)** — `vllm/vllm-openai:cu129-nightly`
  - **GB10 (arm64)** — `vllm/vllm-openai:nightly-aarch64`
- **RTX4090** — FP4 미지원이라 gemma-4-26b 를 AWQ-int4 빌드로 적재

VRAM 점유는 [GPU 메모리 가이드](gpu-memory.md) 참고.

## OpenRouter (GPU 불필요 · 폴백)

- `OPENROUTER_API_KEY` (https://openrouter.ai/keys) — 추가 prerequisite **없음**, compose 호스트만 있으면 된다.
- 로컬 GPU 가 없으면 이것만으로 동작(상용 모델), 있으면 **로컬 노드 다운 시 동일 모델 자동 폴백** + 상용 프런티어 보강.
- Commercial 모델 (OpenAI/Anthropic/Google/DeepSeek 등) 은 전부 OpenRouter 경유 — native API 직결은 지원 안 함.

## 멀티 노드

- **ComfyUI / Whisper 노드** — `COMFYUI_URLS` / `WHISPER_URLS` 에 csv 로 여러 노드 기입 시 LB
- **vLLM 노드** — `.env` 의 `NODES_VLLM=user@host,...` 지정 시 scheduler 가 노드별 배치 자동화
- **각 노드 적용** — 위 install + download 를 동일 적용
  - ssh 로 한 노드씩 들어가 실행, 또는
  - **`setup.sh`** 가 `NODES_VLLM` 을 보고 한 줄에 처리 (`./scripts/setup.sh vllm` 이 각 노드에 rsync + ssh 로 install-vllm.sh 실행)

**ComfyUI / Whisper 노드 추가 절차**

1. 그 노드에서 `install-{comfyui,whisper}.sh` 실행
2. compose 호스트 `.env` 의 `COMFYUI_URLS` / `WHISPER_URLS` 에 `http://<노드IP>:<포트>` csv 추가
3. `docker compose restart {comfyui,whisper}-shim`

> 같은 호스트면 install 스크립트가 `.env` 기록 + shim 재기동까지 자동.

**사전 조건 — 각 원격 노드에서 한 번씩 셋업**:

```bash
# 1) 컨트롤 노드 → 원격 노드 비밀번호 없는 ssh
ssh-copy-id <your-user>@<gpu-node>

# 2) 원격 노드에서 install/manage 스크립트가 sudo 를 비대화식으로 호출하므로 NOPASSWD 허용
ssh <your-user>@<gpu-node> "echo '<your-user> ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/kloudchat-<your-user>"
```

> 설치 스크립트는 `apt`, `systemctl`, drop-in 파일 작성 등에 sudo 가 필요 — 위 2단계 없으면 중간에 비밀번호 입력 프롬프트로 멈춤.

**단일 노드 (compose 호스트가 곧 GPU 노드)**

- ssh 단계 생략 가능, 단 `install-vllm.sh` / `install-comfyui.sh` / `install-whisper.sh` / `manage-vllm.sh` / `tune-host.sh` / `setup.sh clean` 이 직접 `sudo` 호출
- **통과 방법** — 대화식 비밀번호 입력, 또는 같은 NOPASSWD 라인을 로컬 `/etc/sudoers.d/kloudchat-<your-user>` 에 기입

**노드 역할 정책**

- **RTX4090 (24GB) / RTX5090 (32GB)** — flux fullset + LLM 동시 적재 시 OOM 가능성 커 `install-comfyui.sh` 가 hard fail (`--force` 로 우회)
- **이미지 생성 라우팅** — **PRO5000 / PRO6000 / GB10** 노드
- **PRO5000 (48GB)** — vLLM gemma-4-26b + bge + Whisper 동시 적재 시 빠듯 → ComfyUI 전용 운영 또는 LLM 분산 담당
- **`qwen3-coder-next` (FP8 ~75 GiB) 적재 노드** — 다른 chat / embed 모델 미배치
- 자세한 매트릭스 — [GPU 메모리 가이드](gpu-memory.md#노드-클래스별-권장-워크로드) 참고

**vLLM 라우팅**

- scheduler 가 노드별 GPU class / VRAM 인벤토리 → 모델 배치, `setup.sh litellm` 의 gen-litellm-config 가 배치된 노드 수만큼 deployment 등록
- **한 노드만 적재한 모델** — 그 노드로만
- **여러 노드 적재 모델** — router 가 `least-busy` 로 LB
- **이기종 GPU** — 큰 모델은 큰 노드에만, 작은 모델은 전 노드에 식으로 그대로 활용 가능

**ComfyUI 라우팅**

- shim 이 union 디스커버리(`/object_info` TTL 캐시) → 매 요청 alias 로 보유 노드 후보 좁힌 뒤 `/queue` 깊이 LB
- **이기종 GPU OK** — 노드별 다른 가중치 셋 OK, 같은 모델 여러 노드 적재 시 자동 분산
- **stateful** — 워크플로 run state 가 노드 stateful 이라 `prompt_id → 노드` 매핑 in-memory 유지

**Whisper 라우팅**

- shim 이 `/health` 캐시(10s TTL)로 reachable 노드만 추림, in-flight 카운터로 LB
- **동일 모델 가정** — 모든 노드가 동일 `WHISPER_MODEL` 서빙 가정, 노드별 다른 모델 미지원
- **stickiness 없음** — 매 호출 self-contained

## DGX Spark (GB10)

- **ComfyUI / Whisper** — GB10(arm64)에선 호스트 native (venv + systemd), 컨테이너 아님 (amd64 노드는 `docker-compose.media.yml` 컨테이너)
  - `install-comfyui.sh` 가 GB10 감지해 `torch 2.9.1+cu128` (NVFP4 dtype 노출)로 분기
  - Whisper 는 aarch64 ctranslate2 휠이 CPU-only 라 `int8` 로 폴백 (install 스크립트가 compute_type probe 로 자동 처리)
- **vLLM** — `*-aarch64` 이미지 사용 (GB10 = arm64)
- **VRAM 감지** — `nvidia-smi memory.total` 이 `[N/A]` 라서 `lib.sh` 헬퍼가 시스템 RAM 을 VRAM 으로 간주 (`download-vllm-models.sh` 의 추천 셋 결정에 사용)
