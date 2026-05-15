#!/usr/bin/env bash
# Usage: setup.sh [--yes]
#
# 사전 준비:
#   ./scripts/gen-env.sh         # .env 생성 (없으면 setup 거부)
#   $EDITOR .env                 # OPENAI/ANTHROPIC/GEMINI/OPENROUTER/HF_TOKEN, OLLAMA_URLS, COMFYUI_URLS 등 채우기
#
# 필수: OPENROUTER_API_KEY 또는 OLLAMA_URLS reachable 노드 ≥1 (둘 중 하나)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"
source "${SCRIPT_DIR}/lib.sh"

YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y)  YES=1; shift ;;
    -h|--help) grep -E '^# ' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

hdr "1. .env"
[[ -f .env ]] || { err ".env 없음. 먼저 ./scripts/gen-env.sh 실행 후 키 채워주세요."; exit 1; }
ok ".env"

# Pre-check: OR 또는 Ollama reachable 노드 ≥1 필요.
OLLAMA_PULLED="$(ollama_union_models || true)"
OLLAMA_NMODELS=$(echo "$OLLAMA_PULLED" | grep -c . || true)
if has_openrouter; then
  ok "OPENROUTER_API_KEY 설정됨"
elif (( OLLAMA_NMODELS > 0 )); then
  ok "Ollama union: ${OLLAMA_NMODELS} 모델 (OR 없음 — Ollama만 사용)"
else
  err "OPENROUTER_API_KEY 미설정 + Ollama 노드 없음/모델 0개 — 둘 중 하나 필수."
  err "  → OPENROUTER_API_KEY를 .env에 채우거나 OLLAMA_URLS 노드에서 모델 pull."
  exit 1
fi

# native 키 상태 요약
echo "    keys: openai=$(has_openai_native && echo y || echo n) anthropic=$(has_anthropic_native && echo y || echo n) google=$(has_google_native && echo y || echo n) openrouter=$(has_openrouter && echo y || echo n) hf=$( [[ -n "$(env_get HF_TOKEN)" ]] && echo y || echo n )"

# Config 재생성 (union 결과를 yaml에 반영 — 보유 노드별 deployment).
./scripts/gen-litellm-config.sh
./scripts/gen-librechat-config.sh

hdr "2. ComfyUI (선택)"
COMFY_URLS="$(env_get COMFYUI_URLS)"
if [[ -n "$COMFY_URLS" ]]; then
  IMG_PULLED="$(comfyui_intersect_models || true)"
  IMG_N=$(echo "$IMG_PULLED" | grep -c . || true)
  if (( IMG_N > 0 )); then
    ok "ComfyUI 이미지 모델 intersection: ${IMG_N}개 ($(echo "$IMG_PULLED" | paste -sd, -))"
  else
    warn "ComfyUI 노드에 공통 이미지 모델 0개 — 이미지 생성 비활성. 노드별로 ./scripts/download-image-models.sh"
  fi
else
  warn "COMFYUI_URLS 미설정 — 이미지 생성 비활성"
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
