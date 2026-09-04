#!/usr/bin/env bash
# Reads credentials out of the repository's .env for the integration scripts.
#
# Precedence, highest first:
#
#   1. whatever was already exported when the script was invoked
#   2. ADMIN_EMAIL / ADMIN_PASS in .env — the account the checks use
#   3. KCHAT_ADMIN_EMAIL / KCHAT_ADMIN_PASSWORD in .env — the bootstrap admin
#   4. the calling script's own default
#
# Parsed, not sourced: .env holds non-shell values (KCHAT_CORS_ORIGINS is a
# JSON array). Only the keys listed below are read.
#
# KCHAT_ENV_FILE selects another file; /dev/null skips this entirely.

# Repository root from this file's location, not the caller's $0.
KCHAT_ENV_FILE=${KCHAT_ENV_FILE:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.env"}

#: Credentials only; the rest of .env belongs to the container.
_KCHAT_ENV_KEYS="ADMIN_EMAIL ADMIN_PASS KCHAT_ADMIN_EMAIL KCHAT_ADMIN_PASSWORD LITELLM_BASE_URL LITELLM_MASTER_KEY"

# Last assignment wins, matching how docker compose reads the same file.
# Tolerates a leading `export `, surrounding quotes, and CRLF line endings.
_kchat_env_get() {
  sed -n "s/^[[:space:]]*\(export[[:space:]]\+\)\?$1=//p" "$KCHAT_ENV_FILE" 2>/dev/null \
    | tail -n 1 \
    | tr -d '\r' \
    | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

kchat_load_env() {
  if [ ! -r "$KCHAT_ENV_FILE" ]; then
    return 0
  fi

  local key value
  for key in $_KCHAT_ENV_KEYS; do
    # Already in the environment: that wins, and .env is not consulted for it.
    if [ -n "${!key:-}" ]; then
      continue
    fi
    value=$(_kchat_env_get "$key")
    if [ -n "$value" ]; then
      export "$key=$value"
    fi
  done

  # .env.example ships KCHAT_ADMIN_*; the scripts take ADMIN_*.
  if [ -z "${ADMIN_EMAIL:-}" ] && [ -n "${KCHAT_ADMIN_EMAIL:-}" ]; then
    export ADMIN_EMAIL="$KCHAT_ADMIN_EMAIL"
  fi
  if [ -z "${ADMIN_PASS:-}" ] && [ -n "${KCHAT_ADMIN_PASSWORD:-}" ]; then
    export ADMIN_PASS="$KCHAT_ADMIN_PASSWORD"
  fi

  return 0
}

kchat_load_env
