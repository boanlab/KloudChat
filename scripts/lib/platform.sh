#!/usr/bin/env bash
# scripts/lib/platform.sh — OS / arch / hardware detection helpers.
# Source this from other scripts. No side effects (does not read .env).
#
# Supported environments:
#   - Linux  (x86_64 / aarch64, optional NVIDIA GPU)
#   - macOS  (Intel x86_64 / Apple Silicon arm64, CPU only)
#   - DGX Spark (Linux aarch64 + GB10 unified memory)

# Prevent double-sourcing
if [[ -n "${__KC_PLATFORM_SH:-}" ]]; then return 0; fi
__KC_PLATFORM_SH=1

# ──────────────────────────────────────────────────────────
# OS / architecture
# ──────────────────────────────────────────────────────────

# detect_os → linux | macos | unsupported
detect_os() {
  case "$(uname -s)" in
    Linux)  echo linux ;;
    Darwin) echo macos ;;
    *)      echo unsupported ;;
  esac
}

# detect_arch → amd64 | arm64 | unsupported
detect_arch() {
  case "$(uname -m)" in
    x86_64|amd64)        echo amd64 ;;
    aarch64|arm64)       echo arm64 ;;
    *)                   echo unsupported ;;
  esac
}

is_linux() { [[ "$(detect_os)" == linux ]]; }
is_macos() { [[ "$(detect_os)" == macos ]]; }

# ──────────────────────────────────────────────────────────
# GPU
# ──────────────────────────────────────────────────────────

# has_nvidia_gpu — true when nvidia-smi is present and reports at least one GPU.
has_nvidia_gpu() {
  command -v nvidia-smi &>/dev/null && nvidia-smi -L &>/dev/null
}

# has_gb10 — DGX Spark's GB10 unified-memory variant.
# nvidia-smi returns memory.total = [N/A] on this part, so callers that need
# VRAM must fall back to system RAM. This helper exposes that branch.
has_gb10() {
  has_nvidia_gpu || return 1
  nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 | grep -q 'GB10'
}

# get_gpu_vram_mb — VRAM (MB) of the primary GPU. Returns 0 when unavailable.
# GB10 returns N/A from nvidia-smi → fall back to system RAM (unified memory).
get_gpu_vram_mb() {
  if ! has_nvidia_gpu; then echo 0; return; fi
  if has_gb10; then
    get_mem_mb
    return
  fi
  # Multi-GPU hosts may have some N/A rows → take the max numeric value.
  local vram
  vram="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
            | grep -oE '^[0-9]+' | sort -nr | head -1 || true)"
  echo "${vram:-0}"
}

# get_gpu_name — Name of the primary GPU, or empty string when unavailable.
get_gpu_name() {
  if ! has_nvidia_gpu; then echo ""; return; fi
  nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1
}

# ──────────────────────────────────────────────────────────
# Memory / disk / ports
# ──────────────────────────────────────────────────────────

# get_mem_mb — total system RAM in MB.
get_mem_mb() {
  if is_macos; then
    # sysctl returns bytes.
    local bytes
    bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
    echo $(( bytes / 1024 / 1024 ))
  else
    awk '/^MemTotal:/ {print int($2/1024); exit}' /proc/meminfo 2>/dev/null || echo 0
  fi
}

# get_free_disk_gb <path> — free space (GB) on the partition that holds <path>.
get_free_disk_gb() {
  local target="${1:-.}"
  if is_macos; then
    # BSD df: -g reports GiB (similar to Linux -BG).
    df -g "$target" 2>/dev/null | awk 'NR==2 {print $4; exit}'
  else
    df -BG "$target" 2>/dev/null | awk 'NR==2 {gsub("G",""); print $4; exit}'
  fi
}

# port_in_use <port> — returns 0 when the TCP port is in LISTEN state.
port_in_use() {
  local port="$1"
  if command -v lsof &>/dev/null; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN &>/dev/null
    return $?
  fi
  if command -v ss &>/dev/null; then
    ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE ":${port}\$"
    return $?
  fi
  if command -v netstat &>/dev/null; then
    netstat -an 2>/dev/null | awk '/LISTEN/ {print $4}' | grep -qE "[.:]${port}\$"
    return $?
  fi
  return 1   # No tool available → assume free.
}

# ──────────────────────────────────────────────────────────
# Docker
# ──────────────────────────────────────────────────────────

# docker_has_nvidia_runtime — true when the Docker daemon advertises the
# nvidia container runtime.
docker_has_nvidia_runtime() {
  command -v docker &>/dev/null || return 1
  docker info 2>/dev/null | grep -qi 'Runtimes:.*nvidia'
}

# can_use_gpu_services — true when this host can run any NVIDIA-only
# container, regardless of arch. Used by ComfyUI + shim which we build
# multi-arch (amd64 + arm64, incl. DGX Spark).
can_use_gpu_services() {
  is_linux && has_nvidia_gpu && docker_has_nvidia_runtime
}

# can_use_amd64_gpu_services — true when this host can run NVIDIA containers
# whose published image is amd64-only (currently: Whisper STT). Stricter
# than can_use_gpu_services.
can_use_amd64_gpu_services() {
  can_use_gpu_services && [[ "$(detect_arch)" == amd64 ]]
}

# ──────────────────────────────────────────────────────────
# Debug summary
# ──────────────────────────────────────────────────────────

platform_summary() {
  echo "  OS    : $(detect_os) ($(uname -sr))"
  echo "  ARCH  : $(detect_arch) ($(uname -m))"
  echo "  RAM   : $(get_mem_mb) MB"
  if has_nvidia_gpu; then
    echo "  GPU   : $(get_gpu_name)"
    echo "  VRAM  : $(get_gpu_vram_mb) MB"
    has_gb10 && echo "  NOTE  : GB10 unified memory (VRAM = system RAM)"
  else
    echo "  GPU   : (none — CPU only)"
  fi
}
