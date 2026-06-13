#!/usr/bin/env bash
# Usage:
#   manage-vllm.sh up [--recreate] [--coder] [--deep]    weight 검증 + compose up
#   manage-vllm.sh down [-v] [--coder] [--deep]          정지 + 컨테이너 제거
#   manage-vllm.sh restart [svc]                재시작
#   manage-vllm.sh logs [svc]                   follow logs
#   manage-vllm.sh status                       컨테이너 + healthcheck 상태
#   manage-vllm.sh pull                         image 업데이트
#
# Profile:
#   default    vllm-gemma26 (챗 두뇌) + vllm-bge-m3 (chat 노드)
#   --deep     vllm-qwen122b 단독 (Deep Research 노드 — 78G+KV, VRAM 큰 노드)
#   --coder    vllm-codernext 단독 (코딩 클라이언트 전용 노드, chat 과 격리)
#
# compose project name = kloudchat-vllm — 메인 stack 과 라이프사이클을 분리한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.vllm.yml"
source "${SCRIPT_DIR}/lib.sh"

[[ -f "$COMPOSE_FILE" ]] || { err "$COMPOSE_FILE 없음"; exit 1; }

usage() {
  sed -n '2,/^[^#]/p' "$0" | sed -n 's/^# \{0,1\}//p'
  exit "${1:-1}"
}

cmd_up() {
  local recreate=0 coder=0 deep=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --recreate) recreate=1; shift ;;
      --coder)    coder=1; shift ;;
      --deep)     deep=1; shift ;;
      -h|--help)  usage 0 ;;
      *)          err "unknown flag: $1"; usage ;;
    esac
  done

  local root="${VLLM_MODELS_ROOT:-/var/lib/vllm/models}"

  # --deep: Qwen3.5-122B-A10B (port 8002) Deep Research 노드만 띄운다 (additive —
  # 돌고 있는 chat gemma 는 안 건드림). VRAM 큰 노드 (78G+KV).
  if (( deep )); then
    local cd; cd="$(env_get VLLM_QWEN122B_DIR)"; cd="${cd:-qwen3.5-122b-a10b}"
    if [[ ! -f "$root/$cd/config.json" ]]; then
      err "vLLM weight 없음: $root/$cd/"
      err "  → ./scripts/download-vllm-models.sh qwen3.5-122b-a10b"
      exit 2
    fi
    ok "weight: $cd ($(du -sh "$root/$cd" 2>/dev/null | cut -f1))"
    local rec=(); (( recreate )) && rec=(--force-recreate)
    docker compose -f "$COMPOSE_FILE" --profile deep up -d "${rec[@]}" vllm-qwen122b
    echo
    echo "  → curl http://localhost:8002/v1/models"
    echo "  → ./scripts/gen-litellm-config.sh && docker compose up -d --force-recreate litellm"
    return
  fi
  local gpu_class; gpu_class="$(detect_gpu_class)"
  local profile_args=() weight_dirs=()
  if (( coder )); then
    local cd; cd="$(env_get VLLM_CODERNEXT_DIR)"
    weight_dirs=("${cd:-qwen3-coder-next}")
    profile_args=(--profile coder)
  else
    local cd
    cd="$(env_get VLLM_GEMMA26_DIR)"
    # RTX4090 은 FP4 미지원이라 AWQ-int4 변종으로 swap 한다. .env 명시값이 있으면 존중한다.
    if [[ "$gpu_class" == "rtx4090" && ( "$cd" == "gemma-4-26b" || -z "$cd" ) ]]; then
      env_set VLLM_GEMMA26_DIR gemma-4-26b-awq
      warn "GPU=RTX4090 — VLLM_GEMMA26_DIR=gemma-4-26b-awq 로 swap (FP4 미지원)"
      cd=gemma-4-26b-awq
    fi
    weight_dirs=("${cd:-gemma-4-26b}" "bge-m3")
  fi

  local d
  for d in "${weight_dirs[@]}"; do
    if [[ ! -f "$root/$d/config.json" ]]; then
      err "vLLM weight 없음: $root/$d/"
      err "  → ./scripts/install-vllm.sh && ./scripts/download-vllm-models.sh $d"
      exit 2
    fi
    ok "weight: $d ($(du -sh "$root/$d" 2>/dev/null | cut -f1))"
  done

  local recreate_args=()
  if (( recreate )); then
    info "force-recreate — 모델 재로드 (~2-4분, gemma-4-26b 기준)"
    recreate_args=(--force-recreate)
  fi
  docker compose -f "$COMPOSE_FILE" "${profile_args[@]}" up -d "${recreate_args[@]}"

  echo
  echo "  → ./scripts/manage-vllm.sh status"
  if (( coder )); then
    echo "  → curl http://localhost:8003/v1/models"
  fi
  echo "  → ./scripts/gen-litellm-config.sh && docker compose up -d --force-recreate litellm"
}

cmd_down() {
  local profile_args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --coder)    profile_args+=(--profile coder); shift ;;
      --deep)     profile_args+=(--profile deep); shift ;;
      *)          break ;;
    esac
  done
  docker compose -f "$COMPOSE_FILE" "${profile_args[@]}" down "$@"
}

# 모든 profile 을 명시해 어느 profile 의 서비스든 대상에 포함시킨다 (미활성은 자동 무시).
_ALL_PROFILES=(--profile deep --profile coder)
cmd_restart() { docker compose -f "$COMPOSE_FILE" "${_ALL_PROFILES[@]}" restart "$@"; }
cmd_logs()    { docker compose -f "$COMPOSE_FILE" "${_ALL_PROFILES[@]}" logs -f "$@"; }
cmd_pull()    { docker compose -f "$COMPOSE_FILE" "${_ALL_PROFILES[@]}" pull "$@"; }

cmd_status() {
  docker compose -f "$COMPOSE_FILE" "${_ALL_PROFILES[@]}" ps
  echo
  for c in vllm-gemma26 vllm-qwen122b vllm-bge-m3 vllm-codernext; do
    s="$(docker inspect "$c" --format '{{.State.Health.Status}}' 2>/dev/null || echo missing)"
    printf "  %-15s %s\n" "$c:" "$s"
  done
}

[[ $# -eq 0 ]] && usage
sub="$1"; shift
case "$sub" in
  up)         cmd_up "$@" ;;
  down)       cmd_down "$@" ;;
  restart)    cmd_restart "$@" ;;
  logs)       cmd_logs "$@" ;;
  status|ps)  cmd_status ;;
  pull)       cmd_pull "$@" ;;
  -h|--help)  usage 0 ;;
  *)          err "unknown subcommand: $sub"; usage ;;
esac
