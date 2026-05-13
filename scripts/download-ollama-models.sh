#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<EOF
Usage: $(basename "$0") [aliases...]

인자 없이 실행하면 GPU 클래스를 감지해 권장 셋을 다운로드.

Aliases:
  gemma4          gemma4:26b               ~17GB
  gemma3          gemma3:27b               ~17GB
  qwen3-35b       qwen3.5:35b              ~23GB
  qwen3-9b        qwen3.5:9b               ~6GB
  gpt-oss-20b     gpt-oss:20b              ~14GB
  gpt-oss-120b    gpt-oss:120b             ~65GB
  qwen3-coder-q4  qwen3-coder-next:q4_K_M  ~51GB
  qwen3-coder-q8  qwen3-coder-next:q8_0    ~84GB
  embed           bge-m3                   ~1.2GB
  all             전부
EOF
  exit 0
}

pull() {
  local m="$1" tag="$m"
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
  # gpt-oss-20b/120b는 OpenRouter free 라우팅 → 로컬 pull 안 함 (수동 pull은 alias로 가능)
  case "$class" in
    gb10)           set -- all ;;
    blackwell-pro)  set -- qwen3-9b qwen3-35b gemma3 qwen3-coder-q4 qwen3-coder-q8 embed ;;
    blackwell-5090) set -- qwen3-9b qwen3-35b gemma3 embed ;;
    ada-4090)       set -- qwen3-9b qwen3-35b gemma4 embed ;;
    nvidia-other)
      if   (( vram >= 90000 )); then set -- all
      elif (( vram >= 45000 )); then set -- qwen3-9b qwen3-35b gemma4 embed
      elif (( vram >= 20000 )); then set -- qwen3-9b qwen3-35b embed
      else                           set -- qwen3-9b embed; fi ;;
    *)              set -- qwen3-9b embed ;;
  esac
  echo "==> GPU class: $class — pulling: $*"
fi

for arg in "$@"; do
  case "$arg" in
    gemma4)         pull "gemma4:26b" ;;
    gemma3)         pull "gemma3:27b" ;;
    qwen3-35b)      pull "qwen3.5:35b" ;;
    qwen3-9b)       pull "qwen3.5:9b" ;;
    gpt-oss-20b)    pull "gpt-oss:20b" ;;
    gpt-oss-120b)   pull "gpt-oss:120b" ;;
    qwen3-coder-q4) pull "qwen3-coder-next:q4_K_M" ;;
    qwen3-coder-q8) pull "qwen3-coder-next:q8_0" ;;
    embed)          pull "bge-m3" ;;
    all)            "$0" gemma4 gemma3 qwen3-35b qwen3-9b qwen3-coder-q4 qwen3-coder-q8 embed ;;
    -h|--help)      usage ;;
    *)              echo "Unknown: $arg" >&2; usage ;;
  esac
done
