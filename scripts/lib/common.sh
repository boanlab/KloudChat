#!/bin/bash
# Shared helpers for scripts that talk to LiteLLM after the stack is up.
set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$(cd "${SCRIPTS_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"

# Read KEY=value from .env (empty string when missing).
env_get() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true
}

# Set KEY=value in .env (append when missing).
env_set() {
  local key="$1" val="$2"
  [[ -f "$ENV_FILE" ]] || { echo "[env_set] error: ${ENV_FILE} not found" >&2; return 1; }
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}

export LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-$(env_get LITELLM_MASTER_KEY)}"
export LITELLM_URL="${LITELLM_URL:-http://localhost:8000}"
export DATA_DIR="${SCRIPTS_DIR}/.data"

mkdir -p "${DATA_DIR}"

litellm_post() {
  local endpoint="$1"
  local payload="$2"
  local full_response http_code body

  full_response=$(curl -s -w "\n%{http_code}" \
    -X POST "${LITELLM_URL}${endpoint}" \
    -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
    -H "Content-Type: application/json" \
    -d "$payload")

  http_code=$(echo "$full_response" | tail -1)
  body=$(echo "$full_response" | head -n -1)

  if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
    echo "ERROR [HTTP $http_code]: $body" >&2
    return 1
  fi
  echo "$body"
}

litellm_get() {
  local endpoint="$1"
  local full_response http_code body

  full_response=$(curl -s -w "\n%{http_code}" \
    -X GET "${LITELLM_URL}${endpoint}" \
    -H "Authorization: Bearer ${LITELLM_MASTER_KEY}")

  http_code=$(echo "$full_response" | tail -1)
  body=$(echo "$full_response" | head -n -1)

  if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
    echo "ERROR [HTTP $http_code]: $body" >&2
    return 1
  fi
  echo "$body"
}

wait_for_litellm() {
  local max_wait="${1:-60}"
  local elapsed=0
  echo -n "Waiting for LiteLLM..."
  until curl -sf "${LITELLM_URL}/health/liveliness" > /dev/null 2>&1; do
    if [[ $elapsed -ge $max_wait ]]; then
      echo " timed out (${max_wait}s)" >&2
      return 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
    echo -n "."
  done
  echo " ready"
}

team_id_by_alias() {
  local alias="$1"
  local cache="${DATA_DIR}/teams.json"
  if [[ -f "$cache" ]]; then
    local id
    id=$(jq -r --arg a "$alias" '.[] | select(.team_alias == $a) | .team_id' "$cache" 2>/dev/null || true)
    if [[ -n "$id" ]]; then
      echo "$id"
      return 0
    fi
  fi
  # Fall back to a live API query when the cache misses.
  local result
  result=$(litellm_get "/team/list")
  echo "$result" | jq -r --arg a "$alias" '.[] | select(.team_alias == $a) | .team_id' 2>/dev/null || true
}
