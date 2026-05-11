#!/bin/bash
# Ollama 모델 사전 다운로드 스크립트 (호스트 Ollama 사용)
set -euo pipefail

# ---- 사용법 ----
usage() {
  cat <<EOF
사용법: $(basename "$0") [모델...]

모델 선택:
  gemma3          gemma3:27b              (~17GB)  — Google Gemma 3, 범용
  qwen3-35b       qwen3.5:35b             (~23GB)  — Alibaba, 범용 주력
  qwen3-9b        qwen3.5:9b              (~6GB)   — Alibaba, 경량 [기본]
  qwen3-coder-q4  qwen3-coder-next:q4_K_M (~51GB)  — 코딩 특화
  qwen3-coder-q8  qwen3-coder-next:q8_0   (~84GB)  — 코딩 특화 고품질
  embed           bge-m3                  (~1.2GB) — RAG 임베딩 (다국어, 한국어 우수) [기본]
  all             위 전체

예시:
  $(basename "$0")                 # 기본값: qwen3-9b + embed
  $(basename "$0") all
  $(basename "$0") gemma3 embed
EOF
  exit 0
}

# ---- 모델 존재 여부 확인 (ollama list 기준) ----
model_exists() {
  local model="$1"
  [[ "$model" != *:* ]] && model="${model}:latest"
  ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qxF "$model"
}

# ---- 호스트 Ollama 상태 확인 ----
check_ollama() {
  if ! command -v ollama &>/dev/null; then
    echo "오류: ollama 명령어를 찾을 수 없습니다. https://ollama.com 에서 설치하세요." >&2
    exit 1
  fi
  if ! ollama list &>/dev/null; then
    echo "오류: Ollama 서버가 응답하지 않습니다." >&2
    echo "  sudo systemctl start ollama" >&2
    exit 1
  fi
}

# ---- 다운로드 함수 ----
pull_model() {
  local model="$1"
  if model_exists "$model"; then
    echo "[건너뜀] ${model} (이미 존재)"
    return 0
  fi
  echo "[다운로드] ${model}"
  ollama pull "$model"
}

# ---- 인자 없으면 기본값 ----
[[ $# -eq 0 ]] && set -- qwen3-9b embed

check_ollama

for arg in "$@"; do
  case "$arg" in
    gemma3)         pull_model "gemma3:27b" ;;
    qwen3-35b)      pull_model "qwen3.5:35b" ;;
    qwen3-9b)       pull_model "qwen3.5:9b" ;;
    qwen3-coder-q4) pull_model "qwen3-coder-next:q4_K_M" ;;
    qwen3-coder-q8) pull_model "qwen3-coder-next:q8_0" ;;
    embed)          pull_model "bge-m3" ;;
    all)            "$0" gemma3 qwen3-35b qwen3-9b qwen3-coder-q4 qwen3-coder-q8 embed ;;
    -h|--help)      usage ;;
    *)              echo "알 수 없는 모델: $arg" >&2; usage ;;
  esac
done

echo
echo "=== 다운로드 완료 ==="
