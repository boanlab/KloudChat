#!/usr/bin/env bash
# Set the SD.Next active checkpoint to sd_xl_base_1.0 via the runtime API.
#
# SD.Next persists this setting in its volume, but the first deployment still
# needs a one-shot API call. The script is a no-op on hosts where the sdnext
# container does not run (anything other than Linux + amd64 + NVIDIA GPU).
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()  { echo -e "${GREEN}✓${NC} $*"; }
warn(){ echo -e "${YELLOW}!${NC} $*"; }

if ! docker ps --format '{{.Names}}' | grep -q '^sdnext$'; then
  warn "sdnext container is not running — host is not Linux + amd64 + NVIDIA GPU, or services haven't started."
  exit 0
fi

echo "  Setting SD.Next active checkpoint..."
for i in 1 2 3 4 5; do
  if docker exec sdnext curl -s --max-time 3 -X POST \
      http://localhost:7860/sdapi/v1/options \
      -H "Content-Type: application/json" \
      -d '{"sd_model_checkpoint":"sd_xl_base_1.0"}' >/dev/null 2>&1; then
    ok "SD.Next active model: sd_xl_base_1.0"
    exit 0
  fi
  sleep 5
done

warn "No response from SD.Next API — wait for the container to come up and retry."
exit 1
