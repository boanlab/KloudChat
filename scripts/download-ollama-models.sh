#!/bin/bash
# Pre-download Ollama models against the host Ollama instance.
set -euo pipefail

# ---- Usage ----
usage() {
  cat <<EOF
Usage: $(basename "$0") [models...]

Model aliases:
  gemma4          gemma4:26b              (~17GB)  — Google Gemma 4, general
  qwen3-35b       qwen3.5:35b             (~23GB)  — Alibaba, flagship general
  qwen3-9b        qwen3.5:9b              (~6GB)   — Alibaba, lightweight  [default]
  qwen3-coder-q4  qwen3-coder-next:q4_K_M (~51GB)  — coding-tuned
  qwen3-coder-q8  qwen3-coder-next:q8_0   (~84GB)  — coding-tuned, high quality
  embed           bge-m3                  (~1.2GB) — RAG embeddings (multilingual)  [default]
  all             everything above

Examples:
  $(basename "$0")                 # defaults: qwen3-9b + embed
  $(basename "$0") all
  $(basename "$0") gemma4 embed
EOF
  exit 0
}

# ---- Check if a model is already pulled ----
model_exists() {
  local model="$1"
  [[ "$model" != *:* ]] && model="${model}:latest"
  ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qxF "$model"
}

# ---- Verify the host Ollama is reachable ----
check_ollama() {
  if ! command -v ollama &>/dev/null; then
    echo "error: 'ollama' command not found. Install Ollama from https://ollama.com." >&2
    exit 1
  fi
  if ! ollama list &>/dev/null; then
    echo "error: Ollama server is not responding." >&2
    echo "  sudo systemctl start ollama" >&2
    exit 1
  fi
}

# ---- Pull a single model ----
pull_model() {
  local model="$1"
  if model_exists "$model"; then
    echo "[skip] ${model} (already present)"
    return 0
  fi
  echo "[pull] ${model}"
  ollama pull "$model"
}

# ---- Default args ----
[[ $# -eq 0 ]] && set -- qwen3-9b embed

check_ollama

for arg in "$@"; do
  case "$arg" in
    gemma4)         pull_model "gemma4:26b" ;;
    qwen3-35b)      pull_model "qwen3.5:35b" ;;
    qwen3-9b)       pull_model "qwen3.5:9b" ;;
    qwen3-coder-q4) pull_model "qwen3-coder-next:q4_K_M" ;;
    qwen3-coder-q8) pull_model "qwen3-coder-next:q8_0" ;;
    embed)          pull_model "bge-m3" ;;
    all)            "$0" gemma4 qwen3-35b qwen3-9b qwen3-coder-q4 qwen3-coder-q8 embed ;;
    -h|--help)      usage ;;
    *)              echo "Unknown model alias: $arg" >&2; usage ;;
  esac
done

echo
echo "=== Downloads complete ==="
