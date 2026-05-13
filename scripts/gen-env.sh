#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
ENV_EXAMPLE="${PROJECT_DIR}/.env.example"

usage() { echo "Usage: $(basename "$0") [--force]"; exit 0; }

gen_secret() {
  if command -v openssl &>/dev/null; then openssl rand -hex "${1:-16}"
  else od -vN "${1:-16}" -An -tx1 /dev/urandom | tr -d ' \n'; fi
}

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force)   FORCE=1 ;;
    -h|--help) usage ;;
    *)         echo "Unknown: $arg" >&2; usage ;;
  esac
done

[[ -f "$ENV_EXAMPLE" ]] || { echo "error: $ENV_EXAMPLE not found" >&2; exit 1; }
if [[ -f "$ENV_FILE" && $FORCE -eq 0 ]]; then
  echo "[skip] .env exists. --force 로 덮어쓰기."; exit 0
fi

declare -A GENERATED
while IFS= read -r line; do
  if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=change-me- ]]; then
    key="${BASH_REMATCH[1]}"
    case "$key" in
      CREDS_KEY|*MASTER_KEY*|JWT_*) secret="$(gen_secret 32)" ;;
      CREDS_IV)                     secret="$(gen_secret 16)" ;;
      *)                            secret="$(gen_secret 16)" ;;
    esac
    GENERATED["$key"]="$secret"
    echo "${key}=${secret}"
  else
    echo "$line"
  fi
done < "$ENV_EXAMPLE" > "$ENV_FILE"

echo
echo "=== .env generated: ${ENV_FILE} ==="
for key in "${!GENERATED[@]}"; do printf "  %-24s %s\n" "$key" "${GENERATED[$key]}"; done | sort
