#!/usr/bin/env bash
# Usage: setup.sh [--yes]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"
source "${SCRIPT_DIR}/lib.sh"

YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y)  YES=1; shift ;;
    -h|--help) grep -E '^# Usage' "$0" | sed 's/^# //'; exit 0 ;;
    *)         err "Unknown: $1"; exit 1 ;;
  esac
done

hdr "0. Environment"
require_supported_platform
ok "OS / ARCH: $(detect_os) / $(detect_arch)"

command -v docker &>/dev/null || { err "Docker 없음. curl -fsSL https://get.docker.com | sh"; exit 1; }
ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"
docker compose version &>/dev/null || { err "Docker Compose v2 필요."; exit 1; }
ok "Compose $(docker compose version --short)"
docker ps &>/dev/null || { warn "docker 그룹 추가: sudo usermod -aG docker \$USER && newgrp docker"; exit 1; }

MISSING=()
for t in jq curl wget; do command -v "$t" &>/dev/null && ok "$t" || MISSING+=("$t"); done
(( ${#MISSING[@]} > 0 )) && { err "필요 도구 누락: ${MISSING[*]} (apt/dnf install)"; exit 1; }

for p in 8080 8000; do port_in_use "$p" && warn "포트 :$p 사용 중"; done
DISK_FREE=$(get_free_disk_gb "$PROJECT_DIR")
[[ -n "$DISK_FREE" && "$DISK_FREE" -lt 20 ]] && warn "여유 ${DISK_FREE}GB — 컨테이너 이미지 빌드에 부족할 수 있음"
ok "Free disk: ${DISK_FREE:-?}GB"

hdr "1. .env + configs"
[[ -f .env ]] || ./scripts/gen-env.sh
ok ".env"
./scripts/gen-nginx-config.sh
./scripts/gen-litellm-config.sh
./scripts/gen-librechat-config.sh

hdr "2. 백엔드 모델 검증"

normalize_url() {
  local u="$1"
  u="$(echo "$u" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  echo "${u//host.docker.internal/localhost}"
}

OLLAMA_RAW="$(env_get OLLAMA_URLS)"
[[ -n "$OLLAMA_RAW" ]] || { err "OLLAMA_URLS 비어 있음"; exit 1; }
IFS=',' read -ra OLLAMA_BACKENDS <<< "$OLLAMA_RAW"

REQUIRED_MODELS=()
for m in "${CHAT_MODELS[@]}" "${EMBED_MODELS[@]}"; do
  [[ -n "${MODEL_OPENROUTER_FREE[$m]:-}" ]] && continue  # OR free 라우팅이면 Ollama에 필요 없음
  [[ "$m" != *:* ]] && m="${m}:latest"
  REQUIRED_MODELS+=("$m")
done

ollama_fail=0
for raw in "${OLLAMA_BACKENDS[@]}"; do
  url="$(normalize_url "$raw")"
  if ! tags=$(curl -sf "${url}/api/tags" 2>/dev/null); then
    err "$raw: 접속 실패"
    err "  → 해당 노드에서 ./scripts/install-ollama.sh"
    ollama_fail=1; continue
  fi
  pulled="$(echo "$tags" | jq -r '.models[]?.name' 2>/dev/null)"
  missing=()
  for m in "${REQUIRED_MODELS[@]}"; do
    grep -qxF "$m" <<< "$pulled" || missing+=("$m")
  done
  if (( ${#missing[@]} > 0 )); then
    err "$raw: 모델 누락 — ${missing[*]}"
    err "  → 해당 노드에서 ./scripts/download-ollama-models.sh ${missing[*]}"
    ollama_fail=1
  else
    ok "$raw: ${#REQUIRED_MODELS[@]} 모델 OK"
  fi
done
(( ollama_fail )) && exit 1

COMFY_RAW="$(env_get COMFYUI_URLS)"
if [[ -n "$COMFY_RAW" ]]; then
  IFS=',' read -ra COMFY_BACKENDS <<< "$COMFY_RAW"
  for raw in "${COMFY_BACKENDS[@]}"; do
    url="$(normalize_url "$raw")"
    if curl -sf "${url}/system_stats" >/dev/null 2>&1; then
      ok "$raw: ComfyUI OK"
    else
      warn "$raw: 접속 실패 — 이미지 생성 비활성. 해당 GPU 노드에서 ./scripts/install-comfyui.sh"
    fi
  done
fi

hdr "3. Build images"
build_one() {
  local svc="$1" img="$2" desc="$3"
  if docker image inspect "$img" &>/dev/null; then ok "$svc 빌드됨 ($desc)"
  else echo "  → $svc ($desc)"; DOCKER_BUILDKIT=0 docker compose build "$svc"; fi
}
build_one rag_api          kloudchat-rag_api:latest          "1-3 min"
build_one librechat        kloudchat-librechat:latest        "5-10 min"
build_one code-interpreter kloudchat-code-interpreter:latest "<1 min"
build_one comfyui-shim     kloudchat-comfyui-shim:latest     "<1 min"

hdr "4. Start"
docker compose up -d

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
wait_for_lc 300 5

hdr "5. teams + service key"
wait_for_litellm 120
./scripts/manage.sh team create --alias admin   --budget 9999 --tpm 100000 --rpm 500
./scripts/manage.sh team create --alias default --budget 9999 --tpm 100000 --rpm 500
./scripts/manage.sh key issue --service librechat --budget 9999

hdr "6. restart librechat + rag (service key 적용)"
docker compose restart librechat rag_api
wait_for_lc 60 8

hdr "7. Verify"
docker compose ps --format 'table {{.Name}}\t{{.State}}\t{{.Status}}' 2>/dev/null \
  || docker ps --format 'table {{.Names}}\t{{.Status}}'

LC=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8080 || echo fail)
LL=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8000/health/liveliness || echo fail)
echo "  LibreChat :8080  $LC"
echo "  LiteLLM   :8000  $LL"

cat <<EOF

━━━ done ━━━

  ./scripts/manage.sh user create --id admin@example.com --name 'Admin' --username admin --password '<8+>' --budget 9999

  http://localhost:8080         LibreChat
  http://localhost:8000/ui      LiteLLM admin (admin / \$LITELLM_MASTER_KEY)
EOF
