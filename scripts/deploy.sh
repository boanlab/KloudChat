#!/usr/bin/env bash
# Pick the compose-file combination that matches this host and exec docker compose.
#
#   docker-compose.yml         — base (LibreChat, LiteLLM, RAG, code-interpreter, TTS, …)
#   docker-compose.gpu.yml     — ComfyUI + shim  (Linux + NVIDIA, any arch)
#   docker-compose.amd64.yml   — Whisper STT     (Linux + amd64 + NVIDIA only)
#
# Branching:
#   Linux + NVIDIA + nvidia runtime   → base + gpu.yml             (DGX Spark / arm64)
#   …                          + amd64 → base + gpu.yml + amd64.yml (full GPU stack)
#   anything else (macOS / GPU-less)   → base only
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# shellcheck source=lib/platform.sh
source "${SCRIPT_DIR}/lib/platform.sh"

OS="$(detect_os)"
ARCH="$(detect_arch)"

if [[ "$OS" == unsupported || "$ARCH" == unsupported ]]; then
  echo "Unsupported environment: $(uname -s) $(uname -m)" >&2
  exit 1
fi

COMPOSE_FILES=(-f docker-compose.yml)
REASON=""

if can_use_amd64_gpu_services; then
  COMPOSE_FILES+=(-f docker-compose.gpu.yml -f docker-compose.amd64.yml)
  REASON="${OS}/${ARCH} + NVIDIA GPU — including ComfyUI (image gen) + Whisper (STT)"
elif can_use_gpu_services; then
  COMPOSE_FILES+=(-f docker-compose.gpu.yml)
  REASON="${OS}/${ARCH} + NVIDIA GPU — including ComfyUI (image gen). Whisper skipped (CUDA image is amd64 only)."
else
  if [[ "$OS" == macos ]]; then
    REASON="macOS — image gen / STT skipped (Docker Desktop does not expose the Apple GPU). TTS runs on CPU."
  elif ! has_nvidia_gpu; then
    REASON="${OS}/${ARCH} — no NVIDIA GPU detected. ComfyUI / Whisper skipped. TTS runs on CPU."
  else
    REASON="${OS}/${ARCH} — Docker nvidia runtime is not registered. ComfyUI / Whisper skipped."
  fi
fi

echo "==> ${REASON}"

cd "$PROJECT_DIR"
exec docker compose "${COMPOSE_FILES[@]}" "$@"
