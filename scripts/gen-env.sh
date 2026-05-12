#!/bin/bash
# Generate .env from .env.example, replacing every change-me-* placeholder
# with a fresh random secret.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
ENV_EXAMPLE="${PROJECT_DIR}/.env.example"

# ---- Usage ----
usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Generate .env from .env.example. Every change-me-* value is replaced with a
random secret.

Options:
  --force     overwrite an existing .env
  -h, --help  show this help
EOF
  exit 0
}

# ---- Random-secret generator ----
gen_secret() {
  local bytes="${1:-16}"
  if command -v openssl &>/dev/null; then
    openssl rand -hex "$bytes"
  else
    od -vN "$bytes" -An -tx1 /dev/urandom | tr -d ' \n'
  fi
}

# ---- Argument parsing ----
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force)   FORCE=1 ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $arg" >&2; usage ;;
  esac
done

# ---- Sanity checks ----
if [[ ! -f "$ENV_EXAMPLE" ]]; then
  echo "error: .env.example not found at ${ENV_EXAMPLE}" >&2
  exit 1
fi

if [[ -f "$ENV_FILE" && $FORCE -eq 0 ]]; then
  echo "[skip] .env already exists. Pass --force to overwrite."
  exit 0
fi

# ---- Walk .env.example and emit .env ----
echo "==> Generating secrets..."

declare -A GENERATED

while IFS= read -r line; do
  # Replace KEY=change-me-... lines.
  if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=change-me- ]]; then
    key="${BASH_REMATCH[1]}"
    case "$key" in
      CREDS_KEY)    secret="$(gen_secret 32)" ;;  # AES-256 key: exactly 32 bytes
      CREDS_IV)     secret="$(gen_secret 16)" ;;  # AES IV: exactly 16 bytes
      *MASTER_KEY*) secret="$(gen_secret 32)" ;;  # LiteLLM / MeiliSearch master keys
      JWT_*)        secret="$(gen_secret 32)" ;;  # JWT signing keys: 32 bytes
      *)            secret="$(gen_secret 16)" ;;
    esac
    GENERATED["$key"]="$secret"
    echo "${key}=${secret}"
  else
    echo "$line"
  fi
done < "$ENV_EXAMPLE" > "$ENV_FILE"

# ---- Summary ----
echo
echo "=== .env generated ==="
for key in "${!GENERATED[@]}"; do
  printf "  %-24s %s\n" "${key}" "${GENERATED[$key]}"
done | sort
echo
echo "Path: ${ENV_FILE}"
echo
echo "Next steps:"
echo "  1. ./scripts/download-ollama-models.sh   # Pull LLM models"
echo "  2. ./scripts/download-image-models.sh    # Pull image-gen models (Linux + NVIDIA, any arch)"
echo "  3. ./scripts/deploy.sh up -d             # Start services"
echo "  4. ./scripts/init.sh                     # Initialise teams + service key"
