#!/usr/bin/env bash
# Wipe container data directories (Ollama models excluded).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

TARGETS=(
  librechat
  litellm
  meilisearch
  mongodb
  rag
  code-interpreter
  scripts/.data
)

echo "==> Directories to delete (Ollama models are preserved):"
for dir in "${TARGETS[@]}"; do
  echo "    $PROJECT_DIR/$dir"
done

read -r -p "Continue? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  echo "Cancelled."
  exit 0
fi

for dir in "${TARGETS[@]}"; do
  target="$PROJECT_DIR/$dir"
  if [ -e "$target" ]; then
    sudo rm -rf "$target"
    echo "removed: $target"
  else
    echo "skipped (missing): $target"
  fi
done

echo "==> Done."
