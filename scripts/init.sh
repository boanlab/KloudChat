#!/bin/bash
# Run once after the stack first comes up: create default teams + service key.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

echo "=== KloudChat initialise ==="

# 1. Wait for LiteLLM to come up
wait_for_litellm 120

# 2. Default teams (--models uses manage.sh's built-in default)
echo
echo "--- create teams ---"
"${SCRIPT_DIR}/manage.sh" team create \
  --alias admin \
  --budget 9999 \
  --tpm 100000 \
  --rpm 500

"${SCRIPT_DIR}/manage.sh" team create \
  --alias default \
  --budget 9999 \
  --tpm 100000 \
  --rpm 500

# 3. Issue the LibreChat service key (manage.sh writes .env automatically)
echo
echo "--- issue LibreChat service key ---"
"${SCRIPT_DIR}/manage.sh" key issue --service librechat --budget 9999

# 4. Wrap up
echo
echo "=== Initialisation complete ==="
echo "Next steps:"
echo "  - Restart LibreChat to pick up the service key:"
echo "      ./scripts/deploy.sh restart librechat"
echo "  - Create a user with LibreChat account + LiteLLM key (recommended):"
echo "      ./scripts/manage.sh user create --id <email> --name '<name>' --username <id> --password '<8+ chars>'"
echo "  - LiteLLM-only user:"
echo "      ./scripts/manage.sh user create --id <email> --team default"
echo "      ./scripts/manage.sh key issue  --user <email> --team default"
