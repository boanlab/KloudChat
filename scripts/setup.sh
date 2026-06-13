#!/usr/bin/env bash
# Usage: setup.sh <role> [options]
#
# 사전: ./scripts/gen-env.sh 로 .env 생성 후 키/URL 채운다.
# 필수: OPENROUTER_API_KEY 또는 reachable 한 vLLM 노드 (VLLM_*_URL) 1개 이상.
#
# .env 의 NODE_* / NODES_* 가 노드 위치의 SoT. 로컬이면 직접 실행, 원격이면 rsync + ssh 로
# 그 노드에 setup.sh 를 보낸다 (is_local_host 가 localhost / 내 IP / hostname / DNS 매칭).
# 단일노드 = 모든 NODE_* 를 같은 호스트 (또는 비워두면 = 로컬).
#
# Roles (multi-node host installs — .env NODES_<ROLE> csv):
#   vllm        install-vllm.sh      각 NODES_VLLM 노드
#   comfyui     install-comfyui.sh   각 NODES_COMFYUI 노드
#   whisper     install-whisper.sh   각 NODES_WHISPER 노드
#
# Roles (단일 노드 docker stack — .env NODE_<ROLE>):
#   litellm     docker-compose.litellm.yml — LiteLLM + litellm-db + super-agent-shim.
#               teams / service key 발급까지 수행. 끝나면 갱신된 .env 로컬로 pull.
#   librechat   docker-compose.yml — LibreChat + 부속 (mongo/meili/rag/searxng/code-int 등).
#               LiteLLM reachable + .env LITELLM_SERVICE_KEY 검증.
#
# Role (컨트롤 노드 도구 — scheduler 가 자기 site config 의 ssh 타겟에 직접 접속):
#   scheduler <inventory|plan|apply|sensitivity|eval> [opts]
#               python -m scheduler 호출. NODES_VLLM csv 를 --hosts 로 자동 패스.
#               옵션 (--priorities/--solver/--out/--plan/--dry-run/-y) 그대로 전달.
#
# Orchestration:
#   all         vllm→comfyui→whisper (NODES_X 채워진 것만) → scheduler apply
#               (NODES_VLLM 있고 deps 있을 때만) → litellm → pull .env → librechat.
#
# Lifecycle (docker 스택 litellm+librechat 만):
#   stop        두 스택 컨테이너 정지 (docker compose stop — 볼륨/데이터 보존).
#   start       두 스택 컨테이너 재개 (docker compose start). vLLM 등 GPU 백엔드 제외.
#   clean       ⚠️ DESTRUCTIVE — docker compose down + bind-mount runtime data 삭제
#               (litellm/librechat/local 전부, 복구 불가). YES=1 로 프롬프트 스킵.
#
# Env:
#   KLOUDCHAT_REMOTE_DIR             원격 레포 경로 (기본: KloudChat — 로그인 사용자 $HOME 하위)
#   KLOUDCHAT_SCHEDULER_PRIORITIES   scheduler 우선순위 csv (생략 시 catalog.DEFAULT_PRIORITIES)
#   KLOUDCHAT_SKIP_SCHEDULER=1       all 에서 scheduler 단계 강제 스킵 (정적 VLLM_*_URL csv 패턴)
#   YES=1                            clean 확인 프롬프트 스킵
#   KLOUDCHAT_DISPATCHED=1           내부 — ssh 로 도착한 worker 임을 표시 (재dispatch 차단)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"
source "${SCRIPT_DIR}/lib.sh"

# 파일 상단 헤더 주석 블록만 (첫 비주석 라인 직전까지) 출력.
usage() { sed -n '2,/^[^#]/p' "$0" | sed -n 's/^# \{0,1\}//p'; }

# ───────────────────────── shared steps ─────────────────────────

# hdr 0 — OS/docker/jq/curl/wget/port/disk. 첫 번째 인자로 추가 점검 포트 배열 받음.
step_env_check() {
  hdr "0. Environment"
  require_supported_platform
  ok "OS / ARCH: $(detect_os) / $(detect_arch)"
  command -v docker &>/dev/null || { err "Docker 없음. curl -fsSL https://get.docker.com | sh"; exit 1; }
  ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"
  docker compose version &>/dev/null || { err "Docker Compose v2 필요."; exit 1; }
  ok "Compose $(docker compose version --short)"
  docker ps &>/dev/null || { warn "docker 그룹 추가: sudo usermod -aG docker \$USER && newgrp docker"; exit 1; }

  local missing=()
  local t
  for t in jq curl wget; do command -v "$t" &>/dev/null && ok "$t" || missing+=("$t"); done
  (( ${#missing[@]} > 0 )) && { err "필요 도구 누락: ${missing[*]} (apt/dnf install)"; exit 1; }

  local p
  for p in "$@"; do port_in_use "$p" && warn "포트 :$p 사용 중"; done
  DISK_FREE=$(get_free_disk_gb "$PROJECT_DIR")
  [[ -n "$DISK_FREE" && "$DISK_FREE" -lt 20 ]] && warn "여유 ${DISK_FREE}GB — 컨테이너 이미지 빌드에 부족할 수 있음"
  ok "Free disk: ${DISK_FREE:-?}GB"
}

# hdr 1 — .env 존재 + OR 키 또는 vLLM 노드 둘 중 하나 보장.
step_env_validate() {
  hdr "1. .env"
  [[ -f .env ]] || { err ".env 없음 — ./scripts/gen-env.sh 먼저 실행한다."; exit 1; }
  ok ".env"

  local has_vllm=0
  [[ -n "$(env_get VLLM_GEMMA26_URL)" || -n "$(env_get VLLM_CODERNEXT_URL)" ]] && has_vllm=1
  if has_openrouter; then
    ok "OPENROUTER_API_KEY 설정됨"
  elif (( has_vllm )); then
    ok "vLLM 노드 설정됨 (OR 없음 — vLLM만 사용)"
  else
    err "OPENROUTER_API_KEY 미설정 + vLLM 노드 0 — 둘 중 하나 필수."; exit 1
  fi
  echo "    keys: openrouter=$(has_openrouter && echo y || echo n) hf=$( [[ -n "$(env_get HF_TOKEN)" ]] && echo y || echo n )"
}

# hdr 1b — .env 의 VLLM_*_URL probe. VLLM_ACTIVE / VLLM_CODER_ACTIVE / VLLM_URL 글로벌로.
# URL 이 채워져 있으면 ready 까지 wait — 컨테이너가 Up 인 한(모델 로딩 중)이면 시간 무관하게
# 대기하고, 컨테이너가 exited/없음(=미기동)이면 즉시 종료한다. 시간 기반 grace 가 아니라
# 실제 컨테이너 상태로 판정한다 (lib.sh __vllm_container_state — cold start 가 느려도
# false-dead 안 나고, 진짜 크래시만 fail-fast). 이렇게 해야 후속 gen-litellm-config 가
# vLLM 의 /v1/models 에서 max_model_len 을 정확히 받아온다. 환경변수:
#   KLOUDCHAT_VLLM_WAIT_TIMEOUT (기본 1200s) — 전체 deadline (안전 상한). 큰 모델을
#     GB10 등에서 cold-load 하면 600s 를 넘기는 경우가 있어 상향.
#   KLOUDCHAT_VLLM_WAIT_INTERVAL (10s)      — probe 주기
step_vllm_probe() {
  hdr "1b. vLLM (옵셔널, .env 의 VLLM_*_URL 채워졌을 때만)"
  VLLM_ACTIVE=0
  VLLM_CODER_ACTIVE=0
  VLLM_URL="$(env_get VLLM_GEMMA26_URL)"
  VLLM_RESEARCH_URL_VAL="$(env_get VLLM_QWEN122B_URL)"
  VLLM_CODER_URL_VAL="$(env_get VLLM_CODERNEXT_URL)"
  VLLM_BGE_URL="$(env_get VLLM_BGE_M3_URL)"

  # bge-m3 보유 vLLM 노드 0 + OR 키 있으면 OR 임베딩으로 swap. .env 명시값 우선.
  local cur_embed
  cur_embed="$(env_get EMBEDDINGS_MODEL)"
  if [[ "$cur_embed" == "bge-m3" || -z "$cur_embed" ]]; then
    if [[ -z "$VLLM_BGE_URL" ]] && has_openrouter; then
      env_set EMBEDDINGS_MODEL text-embedding-3-small
      warn "bge-m3 보유 노드 0 + OR 키 감지 — EMBEDDINGS_MODEL=text-embedding-3-small 로 swap"
    fi
  fi

  local wait_timeout="${KLOUDCHAT_VLLM_WAIT_TIMEOUT:-1200}"
  local wait_interval="${KLOUDCHAT_VLLM_WAIT_INTERVAL:-10}"
  # rc=2 (컨테이너 미기동/종료) → 운영 사고. fail-fast 로 종료.
  # rc=3 (timeout) → 로딩이 지나치게 오래 걸림. 모델 디스크 첫 로딩이거나 GPU 메모리 문제 — 운영자 개입 필요.
  __wait_or_exit() {
    local csv="$1" label="$2"
    # set -e 상속: bare 호출이 non-zero 반환 시 rc 캡처 전에 종료되므로 || 로 가드.
    local rc=0
    vllm_wait_until_ready "$csv" "$label" "$wait_timeout" "$wait_interval" 3 || rc=$?
    case "$rc" in
      0) return 0 ;;
      2) err "vLLM ${label} 컨테이너 미기동 (TCP unreachable). GPU 노드에서 ./scripts/manage-vllm.sh up 확인 후 재실행."; exit 1 ;;
      3) err "vLLM ${label} ${wait_timeout}s 안에 ready 못함. 모델 로딩 지연 — GPU 노드 로그 확인 (docker logs <container>)."; exit 1 ;;
      *) err "vLLM ${label} readiness 알 수 없는 rc=$rc"; exit 1 ;;
    esac
  }

  # VLLM_GEMMA26_URL 이 chat profile sentinel — 채워지면 gemma/122b/bge-m3 묶음을 활성으로 간주한다.
  if [[ -n "$VLLM_URL" ]]; then
    __wait_or_exit "$VLLM_URL" "chat ($VLLM_URL)"
    ok "vLLM chat ready"
    VLLM_ACTIVE=1
    if [[ -n "$VLLM_RESEARCH_URL_VAL" ]]; then
      __wait_or_exit "$VLLM_RESEARCH_URL_VAL" "research ($VLLM_RESEARCH_URL_VAL)"
      ok "vLLM research ready"
    fi
    if [[ -n "$VLLM_BGE_URL" ]]; then
      __wait_or_exit "$VLLM_BGE_URL" "embed ($VLLM_BGE_URL)"
      ok "vLLM embed ready"
    fi
  fi

  # coder profile — vllm-codernext 단독 노드, chat 과 격리한다.
  if [[ -n "$VLLM_CODER_URL_VAL" ]]; then
    __wait_or_exit "$VLLM_CODER_URL_VAL" "coder ($VLLM_CODER_URL_VAL)"
    ok "vLLM coder ready"
    VLLM_CODER_ACTIVE=1
  fi

  if (( ! VLLM_ACTIVE && ! VLLM_CODER_ACTIVE )) && [[ -z "$VLLM_URL" && -z "$VLLM_CODER_URL_VAL" ]]; then
    ok "vLLM 비활성 — OpenRouter 모델만 사용"
  fi
}

step_comfyui_probe() {
  hdr "2. ComfyUI (선택)"
  local comfy_urls; comfy_urls="$(env_get COMFYUI_URLS)"
  if [[ -n "$comfy_urls" ]]; then
    local img_pulled img_n img_nodes
    img_pulled="$(comfyui_union_models || true)"
    img_n=$(echo "$img_pulled" | grep -c . || true)
    img_nodes=$(comfyui_union_node_models | awk -F'\t' 'NF==2 {print $1}' | sort -u | grep -c . || true)
    if (( img_n > 0 )); then
      ok "ComfyUI 이미지 모델 union: ${img_n}개 / ${img_nodes} 노드 ($(echo "$img_pulled" | paste -sd, -))"
    else
      warn "ComfyUI 노드에 이미지 모델 0개 — 이미지 생성 비활성. 노드에서 ./scripts/download-image-models.sh"
    fi
  else
    warn "COMFYUI_URLS 미설정 — 이미지 생성 비활성"
  fi
}

wait_for_lc() {
  local max="${1:-300}" step="${2:-5}" elapsed status
  for elapsed in $(seq 0 "$step" "$max"); do
    status=$(docker inspect LibreChat --format '{{.State.Health.Status}}' 2>/dev/null || echo missing)
    printf "\r    [%3ds] LibreChat: %-10s" "$elapsed" "$status"
    [[ "$status" == "healthy" ]] && { echo; return 0; }
    sleep "$step"
  done
  echo; warn "Timeout — status: $status"
}

# csv 의 노드 각각 /v1/models probe. 1+ unreachable 이면 return 1.
probe_vllm() {
  local urls_csv="$1" label="$2" any_bad=0
  local IFS=, u url probe code
  for u in $urls_csv; do
    url="$(echo "$u" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -n "$url" ]] || continue
    probe="${url//host.docker.internal/localhost}"
    probe="${probe%/}"
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "${probe}/v1/models" 2>/dev/null || echo fail)
    case "$code" in
      200)         printf "    %-22s %s  ready\n"        "$label" "$url" ;;
      000|fail|"") printf "    %-22s %s  loading/down\n" "$label" "$url"; any_bad=1 ;;
      *)           printf "    %-22s %s  http %s\n"      "$label" "$url" "$code"; any_bad=1 ;;
    esac
  done
  return "$any_bad"
}

print_vllm_status() {
  [[ "$VLLM_ACTIVE" == "1" || "$VLLM_CODER_ACTIVE" == "1" ]] || return 0
  echo
  echo "  vLLM stack:"
  local loading=0
  if [[ "$VLLM_ACTIVE" == "1" ]]; then
    probe_vllm "$VLLM_URL" "gemma-4-26b" || loading=1
    [[ -n "$VLLM_RESEARCH_URL_VAL" ]] && { probe_vllm "$VLLM_RESEARCH_URL_VAL" "qwen3.5:122b (research)" || loading=1; }
    [[ -n "$VLLM_BGE_URL" ]] && { probe_vllm "$VLLM_BGE_URL" "bge-m3" || loading=1; }
  fi
  if [[ "$VLLM_CODER_ACTIVE" == "1" ]]; then
    probe_vllm "$VLLM_CODER_URL_VAL" "qwen3-coder-next" || loading=1
  fi
  echo "    logs/status: GPU 노드에서 ./scripts/manage-vllm.sh {logs,status}"
  if (( loading )); then
    echo "    loading 은 weight load + torch.compile 로 보통 2-4분(gemma) 소요된다."
    echo "    LiteLLM 이 cooldown 시도/실패를 자동 재시도한다."
  fi
}

# ───────────────────────── litellm role ─────────────────────────

role_litellm() {
  step_env_check 8000
  step_env_validate
  step_vllm_probe
  ./scripts/gen-litellm-config.sh

  hdr "3. Pull images (litellm stack)"
  # 자체 이미지는 boanlab/kloudchat-* 를 pull (빌드/퍼블리시는 build-push-images.sh).
  docker compose -f docker-compose.litellm.yml pull

  hdr "4. Start litellm stack"
  docker compose -f docker-compose.litellm.yml up -d

  hdr "5. teams + service key"
  wait_for_litellm 120
  ./scripts/manage.sh team create --alias admin   --budget 9999 --tpm 100000 --rpm 500
  ./scripts/manage.sh team create --alias default --budget 9999 --tpm 100000 --rpm 500
  ./scripts/manage.sh key issue --service librechat --budget 9999
  # 기존 팀의 model allowlist 를 현재 카탈로그로 sync 한다 (멱등).
  ./scripts/manage.sh team sync

  hdr "Verify (litellm)"
  docker compose -f docker-compose.litellm.yml ps --format 'table {{.Name}}\t{{.State}}\t{{.Status}}' 2>/dev/null
  local ll
  ll=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${LITELLM_URL}/health/liveliness" || echo fail)
  echo "  LiteLLM   ${LITELLM_URL}     $ll"
  echo
  echo "  ${LITELLM_URL}/ui  admin UI (admin / \$LITELLM_MASTER_KEY)"
  echo
  echo "  다음 단계: ./scripts/setup.sh librechat"
  echo "    (컨트롤 노드에서 호출하면 NODE_LIBRECHAT 로 .env + repo 자동 rsync. 직접 librechat"
  echo "     노드에서 돌릴 거면 이 노드의 .env 를 그쪽으로 복사 후 LITELLM_URL 갱신.)"
  print_vllm_status
}

# ───────────────────────── librechat role ─────────────────────────

role_librechat() {
  step_env_check 8080
  step_env_validate
  step_vllm_probe

  # LITELLM_URL 도달성 + service key 사전 검증 (librechat 빌드 후 fail 회피).
  hdr "1c. LiteLLM 사전 검증"
  if ! curl -sf --max-time 5 "${LITELLM_URL}/health/liveliness" >/dev/null; then
    err "${LITELLM_URL}/health/liveliness 도달 불가 — LiteLLM 노드에서 setup.sh litellm 먼저"
    exit 1
  fi
  ok "LiteLLM reachable: ${LITELLM_URL}"
  if [[ -z "$(env_get LITELLM_SERVICE_KEY)" ]]; then
    err ".env 의 LITELLM_SERVICE_KEY 비어있음 — LiteLLM 노드 setup 후 그 .env 에서 복사"
    exit 1
  fi
  ok "LITELLM_SERVICE_KEY 설정됨"

  ./scripts/gen-litellm-config.sh
  ./scripts/gen-librechat-config.sh
  ./scripts/gen-searxng-config.sh
  warn "LiteLLM 이 별도 노드라면 위에서 생성된 litellm-config.yaml 을 그 노드로 sync 후 'setup.sh litellm' 재실행 필요"

  step_comfyui_probe

  hdr "3. Pull images (main stack)"
  # 자체 이미지는 boanlab/kloudchat-* 를 pull (빌드/퍼블리시는 build-push-images.sh).
  docker compose pull

  hdr "4. Start main stack"
  docker compose up -d
  # 사이드카 자동 비활성: URL 이 비었는데 shim 만 떠있으면 health 무의미 fail → 명시 stop.
  # 단 comfyui-shim 은 COMFYUI_URLS 비어도 OR 키 있으면 외부 이미지/비디오 라우팅용으로
  # 유지(빈 백엔드로 부팅 가능). whisper-shim 은 whisper 가 GPU 전용(OR STT 폴백 없음)이라
  # WHISPER_URLS 비면 그냥 stop. URL/키 채워지면 다음 setup 의 up -d 가 자동 start.
  if [[ -z "$(env_get COMFYUI_URLS)" ]] && ! has_openrouter; then
    warn "COMFYUI_URLS 비어있음 + OR 키 없음 — comfyui-shim stop (이미지/비디오 백엔드 없음)"
    docker compose stop comfyui-shim >/dev/null 2>&1 || true
  fi
  if [[ -z "$(env_get WHISPER_URLS)" ]]; then
    warn "WHISPER_URLS 비어있음 — whisper-shim stop (Note Taker/youtube 전사 비활성)"
    docker compose stop whisper-shim >/dev/null 2>&1 || true
  fi
  wait_for_lc 300 5

  hdr "6. restart librechat + rag (service key 적용)"
  docker compose restart librechat rag_api
  wait_for_lc 60 8

  # .env ADMIN_* 로 LibreChat ADMIN 계정 자동 생성 (멱등). agent sync 가 ADMIN owner 를
  # 요구하므로 반드시 그 전에.
  step_admin_user

  # ADMIN 글로벌 공유 에이전트 카탈로그를 현재 spec 으로 upsert + 전 사용자 read-only
  # 공유 (멱등). 사용자 생성과 무관한 1회성 부트스트랩이라 여기서만 호출한다.
  ./scripts/manage.sh agent sync

  step_verify_main
  print_vllm_status
}

# .env 의 ADMIN_ID/ADMIN_PW/ADMIN_EMAIL 로 LibreChat ADMIN 계정을 생성한다 (멱등).
# ADMIN_PW 는 gen-env.sh 가 change-me- placeholder 를 랜덤값으로 교체한 결과.
step_admin_user() {
  hdr "6b. Admin 계정 (.env ADMIN_*)"
  local aid apw aemail
  aid=$(env_get ADMIN_ID); apw=$(env_get ADMIN_PW); aemail=$(env_get ADMIN_EMAIL)
  if [[ -z "$aemail" || -z "$apw" ]]; then
    warn "ADMIN_EMAIL / ADMIN_PW 미설정 — admin 자동 생성 건너뜀 (gen-env.sh 후 재실행)"
    return 0
  fi
  if [[ "$apw" == change-me-* ]]; then
    warn "ADMIN_PW 가 placeholder 그대로 — gen-env.sh 로 랜덤 생성 후 재실행. 건너뜀"
    return 0
  fi
  ./scripts/manage.sh user create --admin \
    --id "$aemail" --username "${aid:-admin}" --name "${aid:-Admin}" \
    --password "$apw" --budget 9999
}

step_verify_main() {
  hdr "7. Verify"
  docker compose ps --format 'table {{.Name}}\t{{.State}}\t{{.Status}}' 2>/dev/null \
    || docker ps --format 'table {{.Names}}\t{{.Status}}'

  local lc ll
  # lib.sh 가 .env 의 LIBRECHAT_URL / NODE_LIBRECHAT 보고 분리 노드 케이스에서도
  # 외부 접속 URL 로 계산. localhost:8080 fallback 은 단일 노드용.
  lc=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${LIBRECHAT_URL}" || echo fail)
  ll=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${LITELLM_URL}/health/liveliness" || echo fail)
  echo "  LibreChat ${LIBRECHAT_URL}     $lc"
  echo "  LiteLLM   ${LITELLM_URL}      $ll"
  echo
  echo "  → 브라우저에서 ${LIBRECHAT_URL} 접속 후 admin 계정으로 로그인."

  hdr "done"
  cat <<EOF

  Admin 계정 (자동 생성): $(env_get ADMIN_EMAIL)  /  비밀번호 = .env ADMIN_PW
  추가 사용자: ./scripts/manage.sh user create --id <email> --name <name> --username <user> --password <8+>

  http://localhost:8080         LibreChat
  ${LITELLM_URL}/ui  LiteLLM admin (admin / \$LITELLM_MASTER_KEY)
EOF
}

# ───────────────────────── dispatch helpers ─────────────────────────

# host install role 1개를 NODES_<ROLE> csv 의 각 노드에 분배. csv 비었거나 노드가 로컬이면
# install-*.sh 직접 실행. ssh 케이스는 rsync + ssh "KLOUDCHAT_DISPATCHED=1 setup.sh <role>".
dispatch_csv() {
  local role="$1"; shift
  local var="NODES_${role^^}"
  local csv; csv="$(env_get "$var")"
  if [[ -z "$csv" ]]; then
    # NODES_X 비어있음 = 이 호스트가 타겟. install-*.sh 로컬 실행.
    hdr "[${role}] localhost (${var} 비어있음 — 로컬 실행)"
    "${SCRIPT_DIR}/install-${role}.sh" "$@"
    return $?
  fi
  local host n=0 fail=0
  while IFS= read -r host; do
    n=$((n+1))
    hdr "[${role}] ${host}"
    if is_local_host "$host"; then
      "${SCRIPT_DIR}/install-${role}.sh" "$@" \
        || { warn "install-${role}.sh 실패 (local): $host"; fail=$((fail+1)); }
    else
      if ! rsync_push "$host"; then warn "rsync 실패: $host"; fail=$((fail+1)); continue; fi
      local qargs="" a
      for a in "$@"; do qargs+=" $(printf '%q' "$a")"; done
      if ! ssh_run "$host" "KLOUDCHAT_DISPATCHED=1 ./scripts/setup.sh ${role}${qargs}"; then
        warn "setup.sh ${role} 실패: $host"; fail=$((fail+1))
      fi
    fi
  done < <(csv_split "$csv")
  echo
  ok "${role}: ${n} 노드 중 $((n-fail)) 성공 / ${fail} 실패"
  (( fail > 0 )) && return 1 || return 0
}

# 단일노드 stack role (litellm/librechat) 을 NODE_<ROLE> 에 분배. 로컬이면 role_*() 직접.
dispatch_single() {
  local role="$1"; shift
  local var="NODE_${role^^}"
  local host; host="$(env_get "$var")"
  if [[ -z "$host" ]]; then
    hdr "[${role}] localhost (${var} 비어있음 — 로컬 실행)"
    "role_${role}" "$@"
    return $?
  fi
  hdr "[${role}] ${host}"
  if is_local_host "$host"; then
    "role_${role}" "$@"
  else
    rsync_push "$host"
    local qargs="" a
    for a in "$@"; do qargs+=" $(printf '%q' "$a")"; done
    ssh_run "$host" "KLOUDCHAT_DISPATCHED=1 ./scripts/setup.sh ${role}${qargs}"
  fi
}

# setup.sh litellm 후 원격 노드의 .env 가 LITELLM_SERVICE_KEY 를 박는다. 이걸 로컬로 회수해
# 다음 단계 (librechat rsync) 가 같은 키를 들고 가게 한다. 로컬 케이스는 .env 가 이미 같은 곳.
pull_litellm_env() {
  local host; host="$(env_get NODE_LITELLM)"
  [[ -z "$host" ]] && return 0
  is_local_host "$host" && return 0
  rsync_pull_file "$host" ".env"
  ok "litellm 노드의 .env (LITELLM_SERVICE_KEY 포함) 로컬로 회수"
}

# pulp(python) + cbc(MILP solver 바이너리) + PyYAML. apt 로 자동 설치 시도하며,
# apt 가 없거나 KLOUDCHAT_SCHEDULER_NO_AUTOINSTALL=1 이면 수동 설치 안내로 fallback.
# venv 안에서 돌리거나 pip 으로 관리하고 싶을 때 NO_AUTOINSTALL=1 을 쓰면 됨.
ensure_scheduler_deps() {
  local need_py=0 need_cbc=0
  python3 -c 'import pulp, yaml' &>/dev/null || need_py=1
  command -v cbc &>/dev/null || need_cbc=1
  (( need_py || need_cbc )) || return 0

  if [[ "${KLOUDCHAT_SCHEDULER_NO_AUTOINSTALL:-0}" == "1" ]]; then
    err "scheduler 의존성 미설치 (KLOUDCHAT_SCHEDULER_NO_AUTOINSTALL=1) — sudo apt install python3-pulp coinor-cbc python3-yaml"
    return 1
  fi
  if ! command -v apt-get &>/dev/null; then
    err "scheduler 의존성 미설치 + apt-get 없음 — python3-pulp coinor-cbc python3-yaml 수동 설치 필요"
    return 1
  fi
  hdr "scheduler 의존성 자동 설치 (apt): python3-pulp coinor-cbc python3-yaml"
  if ! sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       python3-pulp coinor-cbc python3-yaml; then
    err "apt 설치 실패 — sudo apt update && sudo apt install python3-pulp coinor-cbc python3-yaml 수동 시도 필요"
    return 1
  fi
  # apt 가 성공했어도 venv 등에서 import 못 잡는 경우가 있어 한 번 더 검증한다.
  if ! python3 -c 'import pulp, yaml' &>/dev/null; then
    err "설치 후에도 pulp/yaml import 실패 — venv/PYTHONPATH 가 시스템 site-packages 를 못 보는지 확인"
    return 1
  fi
  ok "scheduler 의존성 설치 완료"
}

# scheduler 는 컨트롤 노드에서 python -m scheduler 직접 호출 (자기 site config 의 ssh 타겟에
# 직접 접속해 vLLM 컨테이너 배치 + 노드별 .env 오버라이드 수행). NODES_VLLM csv 를 --hosts 로
# 자동 패스. site config (scheduler/sites/*.yaml) 는 workload binding 만 책임.
run_scheduler() {
  local sub="${1:-}"; shift || true
  if [[ -z "$sub" ]]; then
    err "scheduler subcommand 누락. 사용: setup.sh scheduler {inventory|plan|apply|sensitivity|eval} [opts]"
    return 1
  fi
  if ! command -v python3 &>/dev/null; then
    err "python3 미설치 — scheduler 실행 불가"
    return 1
  fi
  ensure_scheduler_deps || return 1
  local hosts_arg=() nodes_csv
  nodes_csv="$(env_get NODES_VLLM)"
  if [[ -n "$nodes_csv" ]]; then
    local hosts; hosts="$(nodes_to_hosts "$nodes_csv")"
    [[ -n "$hosts" ]] && hosts_arg=(--hosts "$hosts")
  fi
  hdr "[scheduler ${sub}] python3 -m scheduler ${sub} ${hosts_arg[*]} $*"
  python3 -m scheduler "$sub" "${hosts_arg[@]}" "$@"
}

# ───────────────────────── all role ─────────────────────────

role_all() {
  # 0) NODES_* → *_URLS 자동 derive (Whisper — scheduler 가 안 다루는 것).
  # vLLM / ComfyUI 의 URL 은 step 2 의 scheduler apply 가 placement 따라 갱신.
  derive_urls_from_nodes

  # 1) host installs — NODES_X 채워진 role 만. 실패해도 다음 단계 진행 (운영자 결정).
  local r
  for r in vllm comfyui whisper; do
    if [[ -n "$(env_get "NODES_${r^^}")" ]]; then
      dispatch_csv "$r" || true
    else
      warn "NODES_${r^^} 비어있음 — ${r} 건너뜀"
    fi
  done

  # 2) scheduler — NODES_VLLM 채워졌고 KLOUDCHAT_SKIP_SCHEDULER 아니면. deps 없으면 run_scheduler 안에서 fail.
  # --priorities 생략 시 scheduler CLI 가 catalog.DEFAULT_PRIORITIES 사용 (single source of truth).
  # 운영자가 KLOUDCHAT_SCHEDULER_PRIORITIES 로 override 시에만 명시 전달.
  if [[ "${KLOUDCHAT_SKIP_SCHEDULER:-0}" != "1" && -n "$(env_get NODES_VLLM)" ]]; then
    local prio_args=()
    [[ -n "${KLOUDCHAT_SCHEDULER_PRIORITIES:-}" ]] && prio_args=(--priorities "$KLOUDCHAT_SCHEDULER_PRIORITIES")
    run_scheduler apply -y "${prio_args[@]}" \
      || warn "scheduler 단계 실패 — 정적 VLLM_*_URL 로 fallback 시도"
  fi

  # 3) litellm — 실패하면 librechat 사전 검증이 fail 하므로 중단.
  dispatch_single litellm
  pull_litellm_env

  # 4) librechat — 갱신된 .env 가 rsync_push 되어 SERVICE_KEY / LITELLM_URL 동기화됨.
  dispatch_single librechat
}

# ── start / stop ──────────────────────────────────────────
# docker 스택(litellm + librechat) 만 정지/재개한다. 컨테이너 state 만 바꾸고
# (compose stop/start) 볼륨·네트워크·bind-mount 데이터는 모두 보존 — 데이터 삭제는
# clean role 담당. vLLM/ComfyUI 등 GPU 백엔드는 대상 아님 (수동: manage-vllm.sh).
# 노드는 .env 의 NODE_LITELLM / NODE_LIBRECHAT 기준 (분리 노드면 각자 ssh).
#
# 의존 방향: librechat → litellm. stop 은 역순(librechat 먼저), start 는 정순.
role_stack_stop() {
  hdr "stop — docker 스택 정지 (데이터 보존)"
  echo "[librechat] $(env_get NODE_LIBRECHAT 2>/dev/null || echo localhost)"
  docker_on_node NODE_LIBRECHAT compose -p kloudchat stop 2>&1 | sed 's/^/  /' || true
  echo "[litellm] $(env_get NODE_LITELLM 2>/dev/null || echo localhost)"
  docker_on_node NODE_LITELLM compose -f docker-compose.litellm.yml stop 2>&1 | sed 's/^/  /' || true
  ok "stop 완료 — 재개: ./scripts/setup.sh start"
}

role_stack_start() {
  hdr "start — docker 스택 재개"
  echo "[litellm] $(env_get NODE_LITELLM 2>/dev/null || echo localhost)"
  docker_on_node NODE_LITELLM compose -f docker-compose.litellm.yml start 2>&1 | sed 's/^/  /' || true
  echo "[librechat] $(env_get NODE_LIBRECHAT 2>/dev/null || echo localhost)"
  docker_on_node NODE_LIBRECHAT compose -p kloudchat start 2>&1 | sed 's/^/  /' || true
  ok "start 완료 — http://localhost:8080"
}

# ── clean (DESTRUCTIVE) ───────────────────────────────────
# 재설치/초기화용. docker compose down 후 bind-mount runtime data 디렉토리 삭제.
# ⚠️ 복구 불가 — 대화/사용자(Mongo), 검색 인덱스(Meili), RAG 임베딩(pgvector),
#    LiteLLM 사용량/팀/키(litellm-db), Code Interpreter 샌드박스 파일이 통째로 삭제된다.
#    백업이 필요하면 실행 전 따로 받을 것 (이 스크립트는 백업 안 함).
# 노드는 .env NODE_LITELLM / NODE_LIBRECHAT 기준 (분리 노드면 각자 rsync+ssh dispatch).
# YES=1 로 확인 프롬프트 스킵.
CLEAN_LITELLM_TARGETS=(data/litellm)
CLEAN_LIBRECHAT_TARGETS=(data/librechat data/mongodb data/meilisearch data/rag data/code-interpreter)
CLEAN_LOCAL_TARGETS=(data/ledger)

# docker compose down (컨테이너+네트워크 제거; named volume 은 보존). compose 파일 없으면 skip.
clean_stack_down() {
  local compose_file="$1"
  if [[ -f "$compose_file" ]]; then
    echo "  → docker compose -f $compose_file down"
    docker compose -f "$compose_file" down 2>&1 | sed 's/^/    /' || true
  else
    echo "  → $compose_file 없음 — compose down skip"
  fi
}

# bind-mount 데이터 삭제. sudo 는 컨테이너가 root 로 만든 파일 때문에 필요.
clean_remove_targets() {
  local d t
  for d in "$@"; do
    t="$PROJECT_DIR/$d"
    if [[ -e "$t" ]]; then sudo rm -rf "$t" && ok "removed: $d" || err "failed: $d"
    else info "skipped: $d (없음)"; fi
  done
}

clean_worker_litellm() {
  clean_stack_down "docker-compose.litellm.yml"
  clean_remove_targets "${CLEAN_LITELLM_TARGETS[@]}"
}
clean_worker_librechat() {
  clean_stack_down "docker-compose.yml"
  clean_remove_targets "${CLEAN_LIBRECHAT_TARGETS[@]}"
}
clean_worker_local() {
  # 컨트롤 노드 로컬 캐시 (manage.sh teams.json 등). 컨테이너 무관 → sudo 불필요.
  local d t
  for d in "${CLEAN_LOCAL_TARGETS[@]}"; do
    t="$PROJECT_DIR/$d"
    if [[ -e "$t" ]]; then rm -rf "$t" && ok "removed: $d"
    else info "skipped: $d (없음)"; fi
  done
}

# clean 의 노드별 dispatch — litellm/librechat 워커를 로컬 직접 또는 원격 ssh 로 실행.
# 원격은 setup.sh clean <sub> 를 KLOUDCHAT_DISPATCHED 로 보내 worker 만 직행시킨다.
clean_dispatch() {
  local sub="$1" var="NODE_${1^^}" host
  host="$(env_get "$var")"
  if [[ -z "$host" ]] || is_local_host "$host"; then
    hdr "[clean ${sub}] localhost"
    "clean_worker_${sub}"
  else
    hdr "[clean ${sub}] ${host}"
    rsync_push "$host"
    ssh_run "$host" "KLOUDCHAT_DISPATCHED=1 ./scripts/setup.sh clean ${sub}"
  fi
}

clean_resolve_node() {
  local host; host="$(env_get "$1")"
  [[ -z "$host" ]] && { echo "localhost"; return; }
  is_local_host "$host" && echo "$host (= localhost)" || echo "$host"
}

clean_confirm() {
  [[ "${YES:-0}" == "1" ]] && return 0
  warn "위 runtime data 가 영구 삭제된다. 되돌릴 수 없음 — 백업 확인."
  read -rp "  계속하려면 'yes' 입력: " c
  [[ "$c" == "yes" ]] || { echo "취소됨."; exit 0; }
}

# clean all (litellm → librechat → local). 인자 없이 호출.
role_clean() {
  hdr "삭제 대상"
  echo "  [litellm   @ $(clean_resolve_node NODE_LITELLM)]   ${CLEAN_LITELLM_TARGETS[*]}"
  echo "  [librechat @ $(clean_resolve_node NODE_LIBRECHAT)] ${CLEAN_LIBRECHAT_TARGETS[*]}"
  echo "  [local     @ 이 노드]                       ${CLEAN_LOCAL_TARGETS[*]}"
  clean_confirm
  clean_dispatch litellm
  clean_dispatch librechat
  hdr "[clean local] this node"
  clean_worker_local
}

# ───────────────────────── main ─────────────────────────

[[ $# -eq 0 ]] && { usage; exit 1; }
ROLE="$1"; shift

# ssh 로 도착한 worker — 재dispatch 차단하고 worker 함수 직행. .env 가 mis-config 돼서
# is_local_host 가 가짜 negative 를 내도 무한 ssh 루프를 막는 안전망.
if [[ "${KLOUDCHAT_DISPATCHED:-0}" == "1" ]]; then
  case "$ROLE" in
    litellm)   role_litellm   "$@" ;;
    librechat) role_librechat "$@" ;;
    vllm)      exec "${SCRIPT_DIR}/install-vllm.sh"    "$@" ;;
    comfyui)   exec "${SCRIPT_DIR}/install-comfyui.sh" "$@" ;;
    whisper)   exec "${SCRIPT_DIR}/install-whisper.sh" "$@" ;;
    # clean dispatch: 원격 노드에서 confirm 없이 해당 worker 직행.
    clean)     case "${1:-}" in
                 litellm)   clean_worker_litellm ;;
                 librechat) clean_worker_librechat ;;
                 local)     clean_worker_local ;;
                 *)         err "Unknown dispatched clean target: ${1:-}"; exit 1 ;;
               esac ;;
    *)         err "Unknown dispatched role: $ROLE"; exit 1 ;;
  esac
  exit $?
fi

case "$ROLE" in
  -h|--help)                   usage; exit 0 ;;
  vllm|comfyui|whisper)        dispatch_csv "$ROLE" "$@" ;;
  litellm)                     dispatch_single litellm "$@"; pull_litellm_env ;;
  librechat)                   dispatch_single librechat "$@" ;;
  scheduler)                   run_scheduler "$@" ;;
  all)                         role_all "$@" ;;
  clean)                       role_clean ;;
  stop)                        role_stack_stop ;;
  start)                       role_stack_start ;;
  *)                           err "Unknown role: $ROLE"; usage; exit 1 ;;
esac
