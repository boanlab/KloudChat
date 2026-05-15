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

# Ollama intersection (pulled). gemma 정책: 둘 다면 gemma4 우선.
OLLAMA_PULLED="$(ollama_intersect_models || true)"
ollama_has() {
  local needle="$1"
  [[ "$needle" != *:* ]] && needle="${needle}:latest"
  grep -qxF "$needle" <<<"$OLLAMA_PULLED"
}

emit_native_or() {
  # prov native_id in_pm out_pm
  local prov="$1" id="$2" in_pm="$3" out_pm="$4"
  local can="${prov}/${id}"
  local key_env native_route or_route="openrouter/${prov}/${id}"
  local route="" model_line="" key_line=""
  case "$prov" in
    openai)    key_env=OPENAI_API_KEY    ; native_route="openai/${id}"    ; has_openai_native    && route=native ;;
    anthropic) key_env=ANTHROPIC_API_KEY ; native_route="anthropic/${id}" ; has_anthropic_native && route=native ;;
    google)    key_env=GEMINI_API_KEY    ; native_route="gemini/${id}"    ; has_google_native    && route=native ;;
  esac
  if [[ "$route" == native ]]; then
    model_line="$native_route"; key_line="$key_env"
  elif has_openrouter; then
    model_line="$or_route";     key_line="OPENROUTER_API_KEY"
  else
    return 0
  fi
  echo "  - model_name: ${can}"
  echo "    litellm_params:"
  echo "      model: ${model_line}"
  echo "      api_key: os.environ/${key_line}"
  echo "    model_info:"
  echo "      input_cost_per_token: $(per_token_cost "$in_pm")"
  echo "      output_cost_per_token: $(per_token_cost "$out_pm")"
}

emit_ollama_chat() {
  local m="$1" in_pm out_pm or_id
  in_pm="${MODEL_PRICE_IN_PM[$m]:-}"
  out_pm="${MODEL_PRICE_OUT_PM[$m]:-}"
  or_id="${MODEL_OR_FREE[$m]:-}"
  if ollama_has "$m"; then
    echo "  - model_name: ollama/${m}"
    echo "    litellm_params:"
    echo "      model: ollama_chat/${m}"
    echo "      api_base: ${OLLAMA_BASE}"
  elif [[ -n "$or_id" ]] && has_openrouter; then
    echo "  - model_name: ollama/${m}"
    echo "    litellm_params:"
    echo "      model: openrouter/${or_id}"
    echo "      api_key: os.environ/OPENROUTER_API_KEY"
  else
    return 0
  fi
  if [[ -n "$in_pm" && -n "$out_pm" ]]; then
    echo "    model_info:"
    echo "      input_cost_per_token: $(per_token_cost "$in_pm")"
    echo "      output_cost_per_token: $(per_token_cost "$out_pm")"
  fi
}

emit_ollama_embed() {
  local m="$1" in_pm
  ollama_has "$m" || return 0
  in_pm="${MODEL_PRICE_IN_PM[$m]:-}"
  echo "  - model_name: ${m}"
  echo "    model_info:"
  echo "      mode: embedding"
  [[ -n "$in_pm" ]] && echo "      input_cost_per_token: $(per_token_cost "$in_pm")"
  echo "    litellm_params:"
  echo "      model: ollama/${m}"
  echo "      api_base: ${OLLAMA_BASE}"
}

# gemma 충돌: intersection이 둘 다 가지면 gemma3 제외.
GEMMA_SKIP=""
if ollama_has gemma4:26b && ollama_has gemma3:27b; then GEMMA_SKIP=gemma3:27b; fi

SECTION=$(
  echo "  ${MARKER_START}"
  # 1. Commercial native curated (provider별)
  for m in "${OPENAI_NATIVE_MODELS[@]}";    do emit_native_or openai    "$m" "${MODEL_PRICE_IN_PM[$m]}" "${MODEL_PRICE_OUT_PM[$m]}"; done
  for m in "${ANTHROPIC_NATIVE_MODELS[@]}"; do emit_native_or anthropic "$m" "${MODEL_PRICE_IN_PM[$m]}" "${MODEL_PRICE_OUT_PM[$m]}"; done
  for m in "${GOOGLE_NATIVE_MODELS[@]}";    do emit_native_or google    "$m" "${MODEL_PRICE_IN_PM[$m]}" "${MODEL_PRICE_OUT_PM[$m]}"; done
  # 2. Ollama chat discovery (gpt-oss / gemma3 등은 MODEL_OR_FREE 매핑 있으면 OR fallback)
  for m in "${OLLAMA_CHAT_CATALOG[@]}"; do
    [[ "$m" == "$GEMMA_SKIP" ]] && continue
    emit_ollama_chat "$m"
  done
  # 3. Ollama embed discovery
  for m in "${OLLAMA_EMBED_CATALOG[@]}"; do emit_ollama_embed "$m"; done
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

n=$(echo "$SECTION" | grep -c '^  - model_name:' || true)
echo "==> $CONFIG_FILE — $n models"
echo "    keys: openai=$(has_openai_native && echo y || echo n) anthropic=$(has_anthropic_native && echo y || echo n) google=$(has_google_native && echo y || echo n) or=$(has_openrouter && echo y || echo n)"
echo "    ollama intersection: $(echo "$OLLAMA_PULLED" | grep -c . || true) models"
