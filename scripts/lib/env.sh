#!/usr/bin/env bash
# Reads credentials out of the repository's .env for the integration scripts.
#
# Precedence, highest first:
#
#   1. whatever was already exported when the script was invoked
#   2. ADMIN_EMAIL / ADMIN_PASS in .env
#   3. KCHAT_ADMIN_EMAIL / KCHAT_ADMIN_PASSWORD in .env
#   4. the calling script's own default
#
# Layer 2 exists because the bootstrap administrator and the account these
# checks sign in as are not always the same one. An instance whose first admin
# arrived by signing up has no KCHAT_ADMIN_* at all, and an instance that was
# bootstrapped may since have had that password changed. Putting ADMIN_EMAIL
# and ADMIN_PASS in .env says "this is the account the checks use", separately
# from "this is the account the container creates on an empty database".
#
# The file is parsed rather than sourced. Sourcing executes whatever is in it,
# and .env legitimately holds values that are not shell — KCHAT_CORS_ORIGINS is
# a JSON array. Only the keys listed below are read; nothing else in .env
# reaches the script's environment.
#
# Set KCHAT_ENV_FILE to read a different file, or point it at /dev/null to skip
# this entirely.

# Repository root, derived from this file rather than from the caller's $0, so
# it is correct however the script was invoked.
KCHAT_ENV_FILE=${KCHAT_ENV_FILE:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.env"}

#: Credentials only. Everything else in .env belongs to the container, not to
#: these scripts, and importing it silently would make a failure here look like
#: a failure in the app.
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

  # .env.example ships KCHAT_ADMIN_*; the scripts take ADMIN_*. Mapped here so
  # a stock .env works without anyone having to know both names.
  if [ -z "${ADMIN_EMAIL:-}" ] && [ -n "${KCHAT_ADMIN_EMAIL:-}" ]; then
    export ADMIN_EMAIL="$KCHAT_ADMIN_EMAIL"
  fi
  if [ -z "${ADMIN_PASS:-}" ] && [ -n "${KCHAT_ADMIN_PASSWORD:-}" ]; then
    export ADMIN_PASS="$KCHAT_ADMIN_PASSWORD"
  fi

  return 0
}

kchat_load_env
