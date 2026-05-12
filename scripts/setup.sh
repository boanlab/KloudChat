#!/usr/bin/env bash
# KloudChat full-deployment script — run once on a fresh host.
#
# Supported environments:
#   - Linux      (x86_64 / aarch64, optional NVIDIA GPU)
#   - macOS      (Intel / Apple Silicon, CPU only — ComfyUI / Whisper skipped)
#   - DGX Spark  (Linux aarch64 + GB10 unified memory, amd64-only containers skipped)
#
# Prerequisites:
#   - Linux : sudo ./scripts/install-ollama.sh   (must be run first)
#   - macOS : ./scripts/install-ollama.sh        (must be run first; no sudo)
#
# Usage:
#   ./scripts/setup.sh                    # interactive (default)
#   ./scripts/setup.sh --yes              # auto-yes for every prompt
#   ./scripts/setup.sh --models all       # download every LLM model
#   ./scripts/setup.sh --skip-models      # skip model downloads
#   ./scripts/setup.sh --no-image-models  # skip image-gen model download (ComfyUI)
#
# Idempotent: previously completed steps are detected and skipped.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# shellcheck source=lib/platform.sh
source "${SCRIPT_DIR}/lib/platform.sh"

# ──────────────────────────────────────────────────────────
# Colour / logging
# ──────────────────────────────────────────────────────────
R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; B='\033[1;34m'; C='\033[0;36m'; N='\033[0m'
hdr() { echo; echo -e "${B}━━━ $* ━━━${N}"; }
ok()  { echo -e "${G}✓${N} $*"; }
warn(){ echo -e "${Y}⚠${N} $*"; }
err() { echo -e "${R}✗${N} $*" >&2; }
ask() {
  (( YES )) && return 0
  read -rp "$(echo -e "${C}?${N} $1 [y/N] ")" ans
  [[ "$ans" =~ ^[Yy]$ ]]
}

# ──────────────────────────────────────────────────────────
# Argument parsing
# ──────────────────────────────────────────────────────────
YES=0
MODELS_ARG=""
SKIP_MODELS=0
SKIP_IMAGE_DOWNLOAD=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y)          YES=1; shift ;;
    --models)          MODELS_ARG="${2:-}"; shift 2 ;;
    --models=*)        MODELS_ARG="${1#*=}"; shift ;;
    --skip-models)     SKIP_MODELS=1; shift ;;
    --no-image-models|--no-amd64) SKIP_IMAGE_DOWNLOAD=1; shift ;;
    -h|--help)
      grep -E '^#( |$)' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) err "Unknown option: $1"; exit 1 ;;
  esac
done

# ──────────────────────────────────────────────────────────
# Step 0: environment check
# ──────────────────────────────────────────────────────────
hdr "0. Environment check"

OS="$(detect_os)"
ARCH="$(detect_arch)"

if [[ "$OS" == unsupported || "$ARCH" == unsupported ]]; then
  err "Unsupported environment: $(uname -s) $(uname -m)"
  err "Supported: Linux (x86_64/aarch64), macOS (Intel/Apple Silicon)"
  exit 1
fi
ok "OS / ARCH: ${OS} / ${ARCH}"

# Docker
if ! command -v docker &>/dev/null; then
  err "Docker is not installed."
  if [[ "$OS" == macos ]]; then
    err "  Install Docker Desktop: https://docs.docker.com/desktop/install/mac-install/"
  else
    err "  curl -fsSL https://get.docker.com | sh"
  fi
  exit 1
fi
ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"

# Compose v2
if ! docker compose version &>/dev/null; then
  err "Docker Compose v2 is not available."
  exit 1
fi
ok "Docker Compose $(docker compose version --short)"

# Docker access (Linux: usually a group issue; macOS: Docker Desktop is per-user)
if ! docker ps &>/dev/null; then
  if [[ "$OS" == linux ]]; then
    warn "Current user is not in the docker group. Run:"
    warn "  sudo usermod -aG docker \$USER  &&  newgrp docker"
  else
    warn "Cannot reach the Docker daemon. Make sure Docker Desktop is running."
  fi
  exit 1
fi

# GPU
GPU_VRAM=0
if has_nvidia_gpu; then
  ok "GPU: $(get_gpu_name)"
  GPU_VRAM=$(get_gpu_vram_mb)
  if has_gb10; then
    ok "GB10 unified memory detected → using system RAM (${GPU_VRAM}MB) as VRAM"
  else
    ok "VRAM: ${GPU_VRAM}MB"
  fi
  if ! docker_has_nvidia_runtime; then
    warn "Docker's NVIDIA runtime is not registered — GPU containers will not run."
    warn "  sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
  else
    ok "Docker NVIDIA runtime registered"
  fi
elif [[ "$OS" == macos ]]; then
  ok "macOS — Ollama will use Metal GPU acceleration (Docker containers stay on CPU)"
else
  warn "No NVIDIA GPU detected → inference will run on CPU (very slow)"
fi

# Required tools
MISSING=()
for tool in jq curl wget; do
  if command -v "$tool" &>/dev/null; then
    ok "$tool"
  else
    MISSING+=("$tool")
  fi
done
if (( ${#MISSING[@]} > 0 )); then
  err "Missing required tools: ${MISSING[*]}"
  if [[ "$OS" == macos ]]; then
    err "  brew install ${MISSING[*]}"
  else
    err "  sudo apt install -y ${MISSING[*]}    (Debian / Ubuntu)"
    err "  sudo dnf install -y ${MISSING[*]}    (Fedora / RHEL)"
  fi
  exit 1
fi

# Port check
for port in 8080 8000 11434; do
  if port_in_use "$port"; then
    warn "Port :${port} is already in use — may collide when KloudChat starts"
  fi
done

# Disk
DISK_FREE_GB=$(get_free_disk_gb "$PROJECT_DIR")
if [[ -n "$DISK_FREE_GB" && "$DISK_FREE_GB" -lt 100 ]]; then
  warn "Only ${DISK_FREE_GB}GB free — models + images may exceed 100GB"
fi
ok "Free disk: ${DISK_FREE_GB:-?}GB"

# ──────────────────────────────────────────────────────────
# Step 1: Ollama (host install)
# ──────────────────────────────────────────────────────────
hdr "1. Ollama (host install)"

ollama_running() {
  curl -sf http://localhost:11434/api/tags &>/dev/null
}

if command -v ollama &>/dev/null && ollama_running; then
  ok "Ollama is already installed and responsive (version: $(ollama --version 2>/dev/null | awk '{print $NF}'))"
else
  if [[ "$OS" == linux ]]; then
    if ask "Run ./scripts/install-ollama.sh with sudo?"; then
      sudo ./scripts/install-ollama.sh
    else
      err "Ollama is not installed or not running — aborting. Install it and rerun."
      exit 1
    fi
  else
    if ask "Run ./scripts/install-ollama.sh? (macOS — no sudo)"; then
      ./scripts/install-ollama.sh
    else
      err "Ollama is not installed or not running — aborting."
      exit 1
    fi
  fi
fi

# ──────────────────────────────────────────────────────────
# Step 2: .env
# ──────────────────────────────────────────────────────────
hdr "2. Generate .env"

if [[ -f .env ]]; then
  ok ".env already exists (force regeneration with ./scripts/gen-env.sh --force)"
else
  ./scripts/gen-env.sh
  ok ".env created"
fi

# ──────────────────────────────────────────────────────────
# Step 3: model downloads
# ──────────────────────────────────────────────────────────
hdr "3. Download Ollama models"

if (( SKIP_MODELS )); then
  warn "--skip-models — skipping model downloads"
else
  if [[ -z "$MODELS_ARG" ]]; then
    # macOS / CPU-only hosts: recommend small models only.
    if [[ "$OS" == macos ]] || ! has_nvidia_gpu; then
      SUGGESTED="qwen3-9b embed"
    elif (( GPU_VRAM >= 90000 )); then
      SUGGESTED="all"
    elif (( GPU_VRAM >= 45000 )); then
      SUGGESTED="qwen3-9b qwen3-35b embed"
    else
      SUGGESTED="qwen3-9b embed"
    fi
    echo "Recommended for this host: ${SUGGESTED}"
    if ask "Download this set?"; then MODELS_ARG="$SUGGESTED"
    else
      read -rp "Custom (e.g. qwen3-9b embed): " MODELS_ARG
    fi
  fi
  # shellcheck disable=SC2086
  ./scripts/download-ollama-models.sh $MODELS_ARG
  ok "Ollama model downloads complete"
fi

# Image-generation models for ComfyUI (Linux + NVIDIA, any arch).
if can_use_gpu_services && ! (( SKIP_IMAGE_DOWNLOAD )); then
  if compgen -G "comfyui/models/checkpoints/*.safetensors" >/dev/null \
     || compgen -G "comfyui/models/unet/*.gguf" >/dev/null; then
    ok "Image-gen models already present in comfyui/models/"
  else
    if ask "Download SDXL + Qwen-Image + Qwen-Image-Edit (~50GB)?"; then
      # The comfyui container creates its bind-mounted volumes as root-owned,
      # so host-side mkdir under comfyui/models/ fails. Reclaim from inside the
      # container when it's running (no sudo needed); otherwise warn loudly.
      if [[ -d comfyui/models && ! -w comfyui/models ]]; then
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'comfyui'; then
          warn "comfyui/models is root-owned — reclaiming via docker exec"
          docker exec comfyui chown -R "$(id -u):$(id -g)" /app/ComfyUI/models /app/ComfyUI/output
        else
          warn "comfyui/models is root-owned but the container isn't running."
          warn "  Start it first (./scripts/deploy.sh up -d comfyui) and re-run setup.sh, or chown manually."
        fi
      fi
      ./scripts/download-image-models.sh
    fi
  fi
elif (( SKIP_IMAGE_DOWNLOAD == 0 )); then
  warn "Skipping image-model download — ComfyUI is not enabled on this host."
fi

# ──────────────────────────────────────────────────────────
# Step 4: build the three custom images
# ──────────────────────────────────────────────────────────
hdr "4. Build custom images (rag_api / librechat / code-interpreter)"

build_one() {
  local svc="$1" img="$2" desc="$3"
  if docker image inspect "$img" &>/dev/null; then
    ok "$svc image already built ($desc)"
  else
    echo "  → building $svc ($desc)..."
    # Use the legacy builder to dodge buildx compatibility issues.
    DOCKER_BUILDKIT=0 ./scripts/deploy.sh build "$svc"
    ok "$svc build complete"
  fi
}

build_one rag_api          kloudchat-rag_api:latest          "1-3 min"
build_one librechat        kloudchat-librechat:latest        "5-10 min (LibreChat clone + npm install + vite build)"
build_one code-interpreter kloudchat-code-interpreter:latest "<1 min (adds NanumGothic font)"

if can_use_gpu_services; then
  build_one comfyui      kloudchat-comfyui:latest      "10-15 min (PyTorch CUDA + ComfyUI + custom nodes)"
  build_one comfyui-shim kloudchat-comfyui-shim:latest "<1 min (FastAPI A1111 adapter)"
fi

# ──────────────────────────────────────────────────────────
# Step 5: start containers
# ──────────────────────────────────────────────────────────
hdr "5. Start services"

./scripts/deploy.sh up -d

echo "  Waiting for containers to become ready (LibreChat healthy, max 5 min)..."
LC_HEALTH="missing"
for ELAPSED in $(seq 0 5 300); do
  LC_HEALTH=$(docker inspect LibreChat --format '{{.State.Health.Status}}' 2>/dev/null || echo "missing")
  printf "\r    [%3ds] LibreChat: %-12s" "$ELAPSED" "$LC_HEALTH"
  [[ "$LC_HEALTH" == "healthy" ]] && break
  sleep 5
done
echo
[[ "$LC_HEALTH" != "healthy" ]] && warn "Timeout — LibreChat status: $LC_HEALTH (check deps: ./scripts/deploy.sh ps)"

# ──────────────────────────────────────────────────────────
# Step 6: init.sh (LiteLLM teams + service key)
# ──────────────────────────────────────────────────────────
hdr "6. Initialise LiteLLM teams + service key"

./scripts/init.sh

# ──────────────────────────────────────────────────────────
# Step 7: restart LibreChat (pick up service key)
# ──────────────────────────────────────────────────────────
hdr "7. Restart LibreChat (apply service key)"

./scripts/deploy.sh restart librechat
echo "  Waiting for LibreChat health..."
for i in 1 2 3 4 5; do
  sleep 8
  status=$(docker inspect LibreChat --format '{{.State.Health.Status}}' 2>/dev/null || echo unknown)
  echo "    [$((i*8))s] $status"
  [[ "$status" == "healthy" ]] && break
done

# ──────────────────────────────────────────────────────────
# Step 8: verification + next-steps
# ──────────────────────────────────────────────────────────
hdr "8. Verify"

echo
./scripts/deploy.sh ps --format 'table {{.Name}}\t{{.State}}\t{{.Status}}' 2>/dev/null \
  || docker ps --format 'table {{.Names}}\t{{.Status}}'

echo
LC_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8080 || echo "fail")
LL_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8000/health/liveliness || echo "fail")
echo "  LibreChat (http://localhost:8080):     ${LC_CODE}"
echo "  LiteLLM   (http://localhost:8000):     ${LL_CODE}"

echo
echo "  Ollama models:"
ollama list 2>/dev/null | tail -n +2 | awk '{printf "    - %s (%s %s)\n", $1, $3, $4}'

cat <<EOF

━━━ Setup complete ━━━

Next steps:

  1. Create the first admin user in a single command (LibreChat account +
     LiteLLM user + virtual key + auto-registered LibreChat keys + default
     agent / preset):

       ./scripts/manage.sh user create \\
         --id admin@example.com --name 'Admin' --username admin \\
         --password '<8+ character password>' --budget 9999

  2. Open the apps:
       - LibreChat       : http://localhost:8080
       - LiteLLM admin UI: http://localhost:8000/ui
         (Username: admin / Password: \$LITELLM_MASTER_KEY)

Known limitations on this host:
EOF

if can_use_amd64_gpu_services; then
  echo "  - All features enabled (Image/ComfyUI + STT/Whisper)"
elif can_use_gpu_services; then
  echo "  - Image gen (ComfyUI)  : enabled"
  echo "  - STT (Whisper)        : disabled — Whisper image is amd64-only (DGX Spark unsupported)"
else
  echo "  - Image gen (ComfyUI)  : disabled — requires Linux + NVIDIA GPU"
  echo "  - STT (Whisper)        : disabled — requires Linux + amd64 + NVIDIA GPU"
fi
echo "  - HWP file RAG         : pyhwp has Python 3.10 issues — use PDF/DOCX instead"
echo
echo "Troubleshooting: ./scripts/deploy.sh logs <service>"
