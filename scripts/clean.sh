#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TARGETS=(librechat litellm meilisearch mongodb rag code-interpreter scripts/.data)

echo "==> 삭제할 디렉토리:"
for d in "${TARGETS[@]}"; do echo "    $PROJECT_DIR/$d"; done
read -rp "Continue? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Cancelled."; exit 0; }

for d in "${TARGETS[@]}"; do
  t="$PROJECT_DIR/$d"
  if [[ -e "$t" ]]; then sudo rm -rf "$t"; echo "removed: $t"
  else echo "skipped: $t"; fi
done
