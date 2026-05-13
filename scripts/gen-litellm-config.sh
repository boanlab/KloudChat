#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"
CONFIG_FILE="${PROJECT_DIR}/litellm-config.yaml"
source "${SCRIPT_DIR}/lib.sh"

OLLAMA_BASE="${OLLAMA_LB_URL:-http://ollama-lb:11434}"
MARKER_START='# >>> KLOUDCHAT_AUTOGEN_START'
MARKER_END='# <<< KLOUDCHAT_AUTOGEN_END'

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) echo "Usage: $(basename "$0") [--dry-run]"; exit 0 ;;
    *)         echo "Unknown: $arg" >&2; exit 1 ;;
  esac
done

[[ -f "$CONFIG_FILE" ]] || { echo "error: $CONFIG_FILE 없음." >&2; exit 1; }
grep -qF "$MARKER_START" "$CONFIG_FILE" && grep -qF "$MARKER_END" "$CONFIG_FILE" \
  || { echo "error: AUTOGEN markers 누락: $CONFIG_FILE" >&2; exit 1; }

SECTION=$(
  echo "  ${MARKER_START}"
  for m in "${CHAT_MODELS[@]}"; do
    echo "  - model_name: $(model_prefix "$m")/${m}"
    echo "    litellm_params:"
    or_model="${MODEL_OPENROUTER_FREE[$m]:-}"
    if [[ -n "$or_model" ]]; then
      echo "      model: openrouter/${or_model}"
      echo "      api_key: os.environ/OPENROUTER_API_KEY"
    else
      echo "      model: ollama_chat/${m}"
      echo "      api_base: ${OLLAMA_BASE}"
    fi
    in_pm="${MODEL_PRICE_IN_PM[$m]:-}"
    out_pm="${MODEL_PRICE_OUT_PM[$m]:-}"
    if [[ -n "$in_pm" && -n "$out_pm" ]]; then
      echo "    model_info:"
      echo "      input_cost_per_token: $(per_token_cost "$in_pm")"
      echo "      output_cost_per_token: $(per_token_cost "$out_pm")"
    fi
  done
  for m in "${EMBED_MODELS[@]}"; do
    echo "  - model_name: ${m}"
    echo "    model_info:"
    echo "      mode: embedding"
    in_pm="${MODEL_PRICE_IN_PM[$m]:-}"
    if [[ -n "$in_pm" ]]; then
      echo "      input_cost_per_token: $(per_token_cost "$in_pm")"
    fi
    echo "    litellm_params:"
    echo "      model: ollama/${m}"
    echo "      api_base: ${OLLAMA_BASE}"
  done
  echo "  ${MARKER_END}"
)

if (( DRY_RUN )); then echo "$SECTION"; exit 0; fi

tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
KC_SECTION="$SECTION" python3 - "$CONFIG_FILE" "$tmp" <<'PY'
import os, sys, pathlib
src = pathlib.Path(sys.argv[1]).read_text()
section = os.environ["KC_SECTION"]
i = src.find("# >>> KLOUDCHAT_AUTOGEN_START")
j = src.find("# <<< KLOUDCHAT_AUTOGEN_END")
if i == -1 or j == -1 or j < i:
    sys.exit("error: AUTOGEN markers missing or reversed")
ls = src.rfind("\n", 0, i) + 1
le = src.find("\n", j); le = len(src) if le == -1 else le
pathlib.Path(sys.argv[2]).write_text(src[:ls] + section + src[le:])
PY
mv "$tmp" "$CONFIG_FILE"; trap - EXIT

n_models=$(( ${#CHAT_MODELS[@]} + ${#EMBED_MODELS[@]} ))
echo "==> $CONFIG_FILE — $n_models models, api_base: $OLLAMA_BASE"

if [[ -f "$ENV_FILE" ]]; then
  urls=$(grep -E '^OLLAMA_URLS=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)
  if [[ "$urls" == *,* ]]; then
    echo "Multi-node OLLAMA_URLS — 모든 백엔드에 동일 모델 pull 필요."
  fi
fi
