#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<EOF
Usage: $(basename "$0") [aliases...]

인자 없이 실행하면 GPU 클래스를 감지해 권장 셋을 다운로드.

Aliases:
  qwen3-9b        qwen3.5:9b               ~6GB
  qwen3-35b       qwen3.6:35b              ~23GB
  llama3-8b       llama3.1:8b              ~5GB
  llama3-70b      llama3.3:70b             ~40GB (Q4)
  nemotron3       nemotron3:33b            ~20GB (Q4)
  qwen3-coder-q8  qwen3-coder-next:q8_0    ~84GB
  embed           bge-m3                   ~1.2GB
  all             전부
EOF
  exit 0
}

pull() {
  local m="$1"
  local tag="$m"
  [[ "$tag" != *:* ]] && tag="${tag}:latest"
  if ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qxF "$tag"; then
    echo "[skip] $m"
  else
    echo "[pull] $m"; ollama pull "$m"
  fi
}

command -v ollama &>/dev/null || { echo "error: 'ollama' not found." >&2; exit 1; }
ollama list &>/dev/null || { echo "error: ollama server unreachable." >&2; exit 1; }

if [[ $# -eq 0 ]]; then
  class="$(detect_gpu_class)"
  vram="$(get_gpu_vram_mb)"
  case "$class" in
    gb10)           set -- all ;;
    blackwell-pro)  set -- qwen3-9b qwen3-35b llama3-8b llama3-70b nemotron3 qwen3-coder-q8 embed ;;
    blackwell-5090) set -- qwen3-9b qwen3-35b llama3-8b nemotron3 embed ;;
    ada-4090)       set -- qwen3-9b llama3-8b embed ;;
    nvidia-other)
      if   (( vram >= 100000 )); then set -- all
      elif (( vram >=  80000 )); then set -- qwen3-9b qwen3-35b llama3-8b llama3-70b nemotron3 qwen3-coder-q8 embed
      elif (( vram >=  40000 )); then set -- qwen3-9b qwen3-35b llama3-8b nemotron3 embed
      elif (( vram >=  20000 )); then set -- qwen3-9b qwen3-35b llama3-8b embed
      else                            set -- qwen3-9b llama3-8b embed; fi ;;
    *)              set -- qwen3-9b llama3-8b embed ;;
  esac
  echo "==> GPU class: $class — pulling: $*"
fi

for arg in "$@"; do
  case "$arg" in
    qwen3-9b)       pull "qwen3.5:9b" ;;
    qwen3-35b)      pull "qwen3.6:35b" ;;
    llama3-8b)      pull "llama3.1:8b" ;;
    llama3-70b)     pull "llama3.3:70b" ;;
    nemotron3)      pull "nemotron3:33b" ;;
    qwen3-coder-q8) pull "qwen3-coder-next:q8_0" ;;
    embed)          pull "bge-m3" ;;
    all)            "$0" qwen3-9b qwen3-35b llama3-8b llama3-70b nemotron3 qwen3-coder-q8 embed ;;
    -h|--help)      usage ;;
    *)              echo "Unknown: $arg" >&2; usage ;;
  esac
done
