#!/usr/bin/env bash
# 컨테이너 데이터 디렉토리를 삭제합니다 (ollama 제외).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

TARGETS=(
  librechat
  litellm
  meilisearch
  mongodb
  rag
  code-interpreter
  scripts/.data
)

echo "==> 삭제 대상 디렉토리 (ollama 제외):"
for dir in "${TARGETS[@]}"; do
  echo "    $PROJECT_ROOT/$dir"
done

read -r -p "계속하시겠습니까? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  echo "취소되었습니다."
  exit 0
fi

for dir in "${TARGETS[@]}"; do
  target="$PROJECT_ROOT/$dir"
  if [ -e "$target" ]; then
    sudo rm -rf "$target"
    echo "삭제됨: $target"
  else
    echo "건너뜀 (없음): $target"
  fi
done

echo "==> 완료."
