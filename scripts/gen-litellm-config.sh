#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"
CONFIG_FILE="${PROJECT_DIR}/litellm-config.yaml"
source "${SCRIPT_DIR}/lib.sh"

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

# 노드별 모델 매핑을 한 번 캐시. emit 시 노드 1개당 deployment 1개씩.
OLLAMA_NODE_MAP="$(ollama_union_node_models || true)"
OLLAMA_PULLED="$(awk -F'\t' 'NF==2 {print $2}' <<<"$OLLAMA_NODE_MAP" | sort -u)"
ollama_has() {
  local needle="$1"
  [[ "$needle" != *:* ]] && needle="${needle}:latest"
  grep -qxF "$needle" <<<"$OLLAMA_PULLED"
}
nodes_for() {
  local needle="$1"
  [[ "$needle" != *:* ]] && needle="${needle}:latest"
  awk -F'\t' -v m="$needle" '$2==m {print $1}' <<<"$OLLAMA_NODE_MAP"
}

# commercial: OR 키 있을 때만 등록. canonical name 은 `<prov>/<id>`, 라우트는 `openrouter/<prov>/<id>`.
emit_commercial_or() {
  local prov="$1" id="$2" in_pm="$3" out_pm="$4"
  has_openrouter || return 0
  echo "  - model_name: ${prov}/${id}"
  echo "    litellm_params:"
  echo "      model: openrouter/${prov}/${id}"
  echo "      api_key: os.environ/OPENROUTER_API_KEY"
  echo "    model_info:"
  echo "      input_cost_per_token: $(per_token_cost "$in_pm")"
  echo "      output_cost_per_token: $(per_token_cost "$out_pm")"
}

# 보유 노드 1개당 deployment 1개씩 emit. 같은 model_name 의 여러 deployment 는
# LiteLLM router 의 routing_strategy 가 LB. 보유 0 이면 미등록.
emit_ollama_chat() {
  local m="$1" in_pm out_pm urls
  in_pm="${MODEL_PRICE_IN_PM[$m]:-}"
  out_pm="${MODEL_PRICE_OUT_PM[$m]:-}"
  urls="$(nodes_for "$m")"
  [[ -n "$urls" ]] || return 0
  while IFS= read -r url; do
    [[ -n "$url" ]] || continue
    echo "  - model_name: ollama/${m}"
    echo "    litellm_params:"
    echo "      model: ollama_chat/${m}"
    echo "      api_base: ${url}"
    if [[ -n "$in_pm" && -n "$out_pm" ]]; then
      echo "    model_info:"
      echo "      input_cost_per_token: $(per_token_cost "$in_pm")"
      echo "      output_cost_per_token: $(per_token_cost "$out_pm")"
    fi
  done <<<"$urls"
}

emit_ollama_embed() {
  local m="$1" in_pm urls
  urls="$(nodes_for "$m")"
  [[ -n "$urls" ]] || return 0
  in_pm="${MODEL_PRICE_IN_PM[$m]:-}"
  while IFS= read -r url; do
    [[ -n "$url" ]] || continue
    echo "  - model_name: ${m}"
    echo "    model_info:"
    echo "      mode: embedding"
    [[ -n "$in_pm" ]] && echo "      input_cost_per_token: $(per_token_cost "$in_pm")"
    echo "    litellm_params:"
    echo "      model: ollama/${m}"
    echo "      api_base: ${url}"
  done <<<"$urls"
}

# OR 키 있을 때 등록. canonical name 그대로 (`text-embedding-3-small`),
# 라우트는 `openrouter/openai/<id>`. Ollama bge-m3 미보유 환경에서 RAG 폴백.
emit_openai_embed() {
  local m="$1" in_pm
  has_openrouter || return 0
  in_pm="${MODEL_PRICE_IN_PM[$m]:-}"
  echo "  - model_name: ${m}"
  echo "    model_info:"
  echo "      mode: embedding"
  [[ -n "$in_pm" ]] && echo "      input_cost_per_token: $(per_token_cost "$in_pm")"
  echo "    litellm_params:"
  echo "      model: openrouter/openai/${m}"
  echo "      api_key: os.environ/OPENROUTER_API_KEY"
}

SECTION=$(
  echo "  ${MARKER_START}"
  for m in "${OPENAI_MODELS[@]}";    do emit_commercial_or openai    "$m" "${MODEL_PRICE_IN_PM[$m]}" "${MODEL_PRICE_OUT_PM[$m]}"; done
  for m in "${ANTHROPIC_MODELS[@]}"; do emit_commercial_or anthropic "$m" "${MODEL_PRICE_IN_PM[$m]}" "${MODEL_PRICE_OUT_PM[$m]}"; done
  for m in "${GOOGLE_MODELS[@]}";    do emit_commercial_or google    "$m" "${MODEL_PRICE_IN_PM[$m]}" "${MODEL_PRICE_OUT_PM[$m]}"; done
  for m in "${OLLAMA_CHAT_CATALOG[@]}";  do emit_ollama_chat  "$m"; done
  for m in "${OLLAMA_EMBED_CATALOG[@]}"; do emit_ollama_embed "$m"; done
  for m in "${OPENAI_EMBED_CATALOG[@]}"; do emit_openai_embed "$m"; done
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
echo "    keys: openrouter=$(has_openrouter && echo y || echo n)"
echo "    ollama union: $(echo "$OLLAMA_PULLED" | grep -c . || true) models / $(echo "$OLLAMA_NODE_MAP" | awk -F'\t' 'NF==2 {print $1}' | sort -u | grep -c . || true) nodes"
