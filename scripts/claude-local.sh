#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export ANTHROPIC_BASE_URL="http://localhost:8000"
export ANTHROPIC_AUTH_TOKEN="$(grep -E '^LITELLM_MASTER_KEY=' "${SCRIPT_DIR}/../.env" 2>/dev/null | cut -d= -f2-)"
export ANTHROPIC_MODEL="ollama/qwen3-coder-next:q8_0"
export ANTHROPIC_DEFAULT_SONNET_MODEL="ollama/qwen3-coder-next:q4_K_M"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="ollama/gemma4:26b"
export ANTHROPIC_DEFAULT_OPUS_MODEL="ollama/qwen3-coder-next:q8_0"

claude "$@"
