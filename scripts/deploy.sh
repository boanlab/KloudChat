#!/usr/bin/env bash
# Pick the compose-file combination that matches this host and exec docker compose.
#
#   docker-compose.yml         — base (LibreChat, LiteLLM, RAG, code-interpreter, TTS, …)
#   docker-compose.amd64.yml   — Whisper STT + SD.Next (Linux + amd64 + NVIDIA GPU only)
#
# Branching:
#   Linux + amd64 + NVIDIA GPU + nvidia runtime   → base + amd64.yml
#   anything else (Linux arm64 / macOS / GPU-less amd64) → base only
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

if can_use_gpu_services; then
  COMPOSE_FILES+=(-f docker-compose.amd64.yml)
  REASON="${OS}/${ARCH} + NVIDIA GPU + Docker nvidia runtime — including Whisper (STT) + SD.Next (image gen)"
else
  if [[ "$OS" == macos ]]; then
    REASON="macOS — Whisper / SD.Next skipped (no CUDA containers). TTS runs on CPU."
  elif [[ "$ARCH" == arm64 ]]; then
    REASON="${OS}/arm64 — Whisper / SD.Next skipped (CUDA images are amd64 only). TTS runs."
  elif ! has_nvidia_gpu; then
    REASON="${OS}/${ARCH} — no NVIDIA GPU detected. Whisper / SD.Next skipped. TTS runs on CPU."
  else
    REASON="${OS}/${ARCH} — Docker nvidia runtime is not registered. Whisper / SD.Next skipped."
  fi
fi

echo "==> ${REASON}"

cd "$PROJECT_DIR"
exec docker compose "${COMPOSE_FILES[@]}" "$@"
