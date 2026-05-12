#!/usr/bin/env bash
# Ollama host installer — run this directly on the host that will serve Ollama.
#
# Linux : official install.sh → systemd override to bind OLLAMA_HOST=0.0.0.0 → enable service
#
# The systemd override forces 0.0.0.0 binding so Docker containers can reach
# the host through host.docker.internal:11434.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"

# shellcheck source=lib/platform.sh
source "${SCRIPT_DIR}/lib/platform.sh"

# ──────────────────────────────────────────────────────────
# Colour / logging
# ──────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

OS="$(detect_os)"
ARCH="$(detect_arch)"

if [[ "$OS" == unsupported || "$ARCH" == unsupported ]]; then
  error "Unsupported environment: $(uname -s) $(uname -m)"
  error "Supported: Linux (x86_64/aarch64)"
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  error "Linux requires root privileges."
  echo "  sudo $0"
  exit 1
fi

echo "=== Ollama installer ==="
echo "  OS    : $(uname -sr)"
echo "  ARCH  : $ARCH"
echo

# ──────────────────────────────────────────────────────────
# GPU detection (advisory only)
# ──────────────────────────────────────────────────────────
check_gpu() {
  if has_nvidia_gpu; then
    info "NVIDIA GPU detected: $(get_gpu_name)"
    if has_gb10; then
      info "GB10 unified memory (DGX Spark) — VRAM is treated as system RAM"
    fi
  else
    warn "No NVIDIA GPU detected. Ollama will fall back to CPU (very slow)."
    warn "If you need GPU support, install the CUDA driver first:"
    warn "  https://developer.nvidia.com/cuda-downloads"
  fi
  echo
}

# ──────────────────────────────────────────────────────────
# Install the Ollama binary
# ──────────────────────────────────────────────────────────
install_ollama() {
  if command -v ollama &>/dev/null; then
    local ver
    ver="$(ollama --version 2>/dev/null | awk '{print $NF}' || echo '?')"
    info "Ollama is already installed (version: ${ver})"
    read -rp "Reinstall / upgrade? [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] || { info "Skipping install."; return 0; }
  fi

  info "Running the official Ollama install script..."
  curl -fsSL https://ollama.com/install.sh | sh
  info "Ollama installed"
}

# ──────────────────────────────────────────────────────────
# systemd override (binds 0.0.0.0)
#
# Any user customisations already in override.conf (e.g. OLLAMA_MODELS) are
# preserved; only missing keys are appended.
# ──────────────────────────────────────────────────────────
configure_linux() {
  # The official installer creates the ollama user but not always its home
  # directory; without it the service fails to persist its keys.
  local home_dir="/usr/share/ollama"
  if [[ ! -d "$home_dir" ]]; then
    mkdir -p "$home_dir"
    info "Created Ollama home directory: ${home_dir}"
  fi
  chown -R ollama:ollama "$home_dir" 2>/dev/null || true

  local OVERRIDE_DIR="/etc/systemd/system/ollama.service.d"
  local OVERRIDE_FILE="${OVERRIDE_DIR}/override.conf"
  mkdir -p "$OVERRIDE_DIR"

  if [[ -f "$OVERRIDE_FILE" ]]; then
    info "Existing systemd override found: ${OVERRIDE_FILE}"
    cat "$OVERRIDE_FILE"; echo
  fi

  declare -A DESIRED=(
    [OLLAMA_HOST]="0.0.0.0:11434"
    [OLLAMA_NUM_PARALLEL]="4"
    [OLLAMA_KEEP_ALIVE]="5m"
  )

  local changed=0
  for key in "${!DESIRED[@]}"; do
    if grep -qE "^Environment=\"${key}=" "$OVERRIDE_FILE" 2>/dev/null; then
      info "Keep: ${key}=$(grep -oP "(?<=${key}=)[^\"]+" "$OVERRIDE_FILE" || echo '(existing)')"
    else
      grep -q '^\[Service\]' "$OVERRIDE_FILE" 2>/dev/null || echo '[Service]' >> "$OVERRIDE_FILE"
      echo "Environment=\"${key}=${DESIRED[$key]}\"" >> "$OVERRIDE_FILE"
      info "Add: ${key}=${DESIRED[$key]}"
      changed=1
    fi
  done

  (( changed == 0 )) && info "override.conf unchanged (all keys already present)." \
                     || info "systemd override updated."

  info "Reloading systemd daemon..."
  systemctl daemon-reload

  if systemctl is-enabled ollama &>/dev/null; then
    info "Restarting Ollama service..."
    systemctl restart ollama
  else
    info "Enabling and starting Ollama service..."
    systemctl enable --now ollama
  fi
}

# ──────────────────────────────────────────────────────────
# Wait for Ollama to respond
# ──────────────────────────────────────────────────────────
wait_for_ollama() {
  local retries=15
  info "Waiting for Ollama to respond..."
  for ((i=1; i<=retries; i++)); do
    if curl -sf http://localhost:11434/api/tags &>/dev/null; then
      info "Ollama service is up"
      return 0
    fi
    sleep 2
  done
  error "Ollama did not respond within the timeout."
  echo "  journalctl -u ollama -n 30 --no-pager"
  exit 1
}

# ──────────────────────────────────────────────────────────
# Firewall hint
# ──────────────────────────────────────────────────────────
firewall_hint() {
  if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    warn "ufw is active."
    warn "  Do not expose port 11434 externally. Docker → host traffic uses"
    warn "  host.docker.internal:11434, which does not require a ufw rule."
  fi
}

# ──────────────────────────────────────────────────────────
# Update OLLAMA_API_BASE in .env
# ──────────────────────────────────────────────────────────
update_env() {
  local key="$1" val="$2"
  if [[ ! -f "$ENV_FILE" ]]; then
    warn ".env not found. Run ./scripts/gen-env.sh first."
    warn "  Manual workaround: echo '${key}=${val}' >> .env"
    return 0
  fi
  if grep -qE "^${key}=" "$ENV_FILE"; then
    local old; old="$(grep -E "^${key}=" "$ENV_FILE" | cut -d= -f2-)"
    if [[ "$old" == "$val" ]]; then
      info ".env unchanged: ${key}=${val}"; return 0
    fi
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    info ".env updated: ${key}  ${old} → ${val}"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
    info ".env appended: ${key}=${val}"
  fi
}

# ──────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────
print_summary() {
  echo
  echo "=== Install complete ==="
  echo "  Ollama version : $(ollama --version 2>/dev/null || echo 'unknown')"
  echo "  Bind address   : 0.0.0.0:11434"
  echo "  Docker access  : host.docker.internal:11434"
  echo "  Service state  : $(systemctl is-active ollama 2>/dev/null || echo unknown)"
  echo "  Logs           : journalctl -u ollama -f"
  echo
  echo "Next steps:"
  echo "  1. Download models : ./scripts/download-ollama-models.sh"
  echo "  2. Start services  : ./scripts/deploy.sh up -d"
  echo "  3. Initialise      : ./scripts/init.sh"
}

# ──────────────────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────────────────
check_gpu
install_ollama
configure_linux
firewall_hint
wait_for_ollama
update_env "OLLAMA_API_BASE" "http://host.docker.internal:11434"
print_summary
