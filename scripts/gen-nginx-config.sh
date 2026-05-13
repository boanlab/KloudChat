#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"
CONFIG_FILE="${PROJECT_DIR}/nginx/ollama.conf"

[[ -f "$ENV_FILE" ]] || { echo "error: $ENV_FILE 없음." >&2; exit 1; }

URLS_RAW="$(grep -E '^OLLAMA_URLS=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
URLS_RAW="${URLS_RAW%\"}"; URLS_RAW="${URLS_RAW#\"}"
URLS_RAW="${URLS_RAW%\'}"; URLS_RAW="${URLS_RAW#\'}"
[[ -n "$URLS_RAW" ]] || { echo "error: OLLAMA_URLS 비어 있음." >&2; exit 1; }

IFS=',' read -ra URLS <<< "$URLS_RAW"

mkdir -p "$(dirname "$CONFIG_FILE")"
{
  echo "upstream ollama {"
  echo "  least_conn;"
  for u in "${URLS[@]}"; do
    u="$(echo "$u" | sed 's|^[[:space:]]*http://||;s|/.*$||;s|[[:space:]]*$||')"
    echo "  server $u max_fails=3 fail_timeout=30s;"
  done
  cat <<'CFG'
}

server {
  listen 11434;
  proxy_buffering off;
  proxy_read_timeout 600s;
  proxy_send_timeout 600s;
  proxy_next_upstream error timeout http_502 http_503 http_504;

  location / {
    proxy_pass http://ollama;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
  }
}
CFG
} > "$CONFIG_FILE"

echo "==> $CONFIG_FILE — upstream: ${#URLS[@]} backend"
