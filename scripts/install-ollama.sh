#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$(dirname "$SCRIPT_DIR")/.env"
source "${SCRIPT_DIR}/lib.sh"

REINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --reinstall) REINSTALL=1 ;;
    -h|--help)   echo "Usage: $(basename "$0") [--reinstall]"; exit 0 ;;
    *)           echo "Unknown: $arg" >&2; exit 2 ;;
  esac
done

require_supported_platform
[[ $EUID -ne 0 ]] && exec sudo "$0" "$@"

echo "=== Ollama installer ($(uname -sr) $(detect_arch)) ==="

if has_nvidia_gpu; then
  info "GPU: $(get_gpu_name)"
  has_gb10 && info "GB10 unified memory (DGX Spark)"
else
  warn "NVIDIA GPU 없음 — CPU fallback (느림). https://developer.nvidia.com/cuda-downloads"
fi

if command -v ollama &>/dev/null; then
  info "Ollama 설치됨 ($(ollama --version 2>/dev/null | awk '{print $NF}' || echo '?'))"
  if (( REINSTALL )); then
    info "--reinstall — 재설치 진행"
    curl -fsSL https://ollama.com/install.sh | sh
  elif [[ -t 0 ]]; then
    read -rp "Reinstall? [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] && curl -fsSL https://ollama.com/install.sh | sh
  else
    info "non-interactive — 기존 설치 유지 (재설치 강제: --reinstall)"
  fi
else
  curl -fsSL https://ollama.com/install.sh | sh
fi

mkdir -p /usr/share/ollama
chown -R ollama:ollama /usr/share/ollama 2>/dev/null || true

OVERRIDE=/etc/systemd/system/ollama.service.d/override.conf
mkdir -p "$(dirname "$OVERRIDE")"
# CONTEXT_LENGTH 명시 안 하면 Ollama 가 VRAM 보고 auto-compute — GB10 의 122 GiB
# unified memory 면 모델당 200K~256K 토큰 KV 캐시 할당, NUM_PARALLEL=4 와 곱해져
# 9B 모델이 18+ GiB 점유하는 사태 발생. 8192 면 일반 채팅 충분, 긴 문서가 필요한
# 호스트는 'sudo systemctl edit ollama' 로 키우면 됨 (docs/ollama-tuning.md 참조).
# FLASH_ATTENTION=1 은 Ampere+ (compute 8.0+) 에서만 활성 — 미지원 카드에선 자동 무시.
cat > "$OVERRIDE" <<'UNIT'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_NUM_PARALLEL=4"
Environment="OLLAMA_KEEP_ALIVE=5m"
Environment="OLLAMA_CONTEXT_LENGTH=8192"
Environment="OLLAMA_FLASH_ATTENTION=1"
RestrictAddressFamilies=AF_UNIX AF_INET
UNIT

systemctl daemon-reload
if systemctl is-enabled ollama &>/dev/null; then systemctl restart ollama
else systemctl enable --now ollama; fi

for i in {1..15}; do
  curl -sf http://localhost:11434/api/tags &>/dev/null && break
  sleep 2
  (( i == 15 )) && { err "Ollama 응답 없음. journalctl -u ollama -n 30"; exit 1; }
done

if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  warn "ufw active — port 11434 외부 노출 금지 (Docker는 host.docker.internal 사용)"
fi

HOST_URL="http://host.docker.internal:11434"
if [[ ! -f "$ENV_FILE" ]]; then
  warn ".env 없음. ./scripts/gen-env.sh 먼저 실행."
else
  current="$(env_get OLLAMA_URLS)"
  if [[ "$current" == *,* ]]; then
    info "OLLAMA_URLS multi-node 유지: ${current}"
    [[ ",${current}," != *",${HOST_URL},"* ]] && warn "이 호스트(${HOST_URL})가 OLLAMA_URLS 에 없음."
  else
    env_set OLLAMA_URLS "$HOST_URL"
  fi
fi

cat <<EOF

=== done ===
  $(ollama --version 2>/dev/null || echo unknown) — $(systemctl is-active ollama 2>/dev/null || echo unknown)
  다음: ./scripts/download-ollama-models.sh
EOF
