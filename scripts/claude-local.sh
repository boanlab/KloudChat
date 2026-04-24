#!/bin/bash
# Claude Code를 LiteLLM 경유 로컬 모델로 실행
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export ANTHROPIC_BASE_URL="http://localhost:8000"
export ANTHROPIC_AUTH_TOKEN="$(grep -E '^LITELLM_MASTER_KEY=' "${SCRIPT_DIR}/../.env" 2>/dev/null | cut -d= -f2-)"

export ANTHROPIC_MODEL="qwen3-coder-q8"
export ANTHROPIC_DEFAULT_SONNET_MODEL="qwen3-coder-q8"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="qwen3-coder-q4"
export ANTHROPIC_DEFAULT_OPUS_MODEL="qwen3.5-35b"

claude "$@"
