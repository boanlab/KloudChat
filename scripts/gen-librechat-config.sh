#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="${PROJECT_DIR}/librechat.yaml"
source "${SCRIPT_DIR}/lib.sh"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) echo "Usage: $(basename "$0") [--dry-run]"; exit 0 ;;
    *)         echo "Unknown: $arg" >&2; exit 1 ;;
  esac
done

[[ -f "$CONFIG_FILE" ]] || { echo "error: $CONFIG_FILE 없음." >&2; exit 1; }
grep -qF '# >>> KLOUDCHAT_MODELS_START' "$CONFIG_FILE" \
  && grep -qF '# <<< KLOUDCHAT_MODELS_END' "$CONFIG_FILE" \
  || { echo "error: KLOUDCHAT_MODELS marker 누락: $CONFIG_FILE" >&2; exit 1; }

OLLAMA_PULLED="$(ollama_union_models || true)"
ollama_has() {
  local needle="$1"
  [[ "$needle" != *:* ]] && needle="${needle}:latest"
  grep -qxF "$needle" <<<"$OLLAMA_PULLED"
}

# gen-litellm-config.sh 의 등록 조건과 같은 셋을 LibreChat dropdown 에 노출.
MODELS=()
if has_openrouter; then
  for m in "${OPENAI_MODELS[@]}";    do MODELS+=("openai/${m}");    done
  for m in "${ANTHROPIC_MODELS[@]}"; do MODELS+=("anthropic/${m}"); done
  for m in "${GOOGLE_MODELS[@]}";    do MODELS+=("google/${m}");    done
fi
for m in "${OLLAMA_CHAT_CATALOG[@]}"; do
  if ollama_has "$m"; then MODELS+=("ollama/${m}")
  elif [[ -n "${MODEL_OR_FREE[$m]:-}" ]] && has_openrouter; then MODELS+=("ollama/${m}"); fi
done

SECTION=$(
  echo "          # >>> KLOUDCHAT_MODELS_START"
  for n in "${MODELS[@]}"; do echo "          - \"${n}\""; done
  echo "          # <<< KLOUDCHAT_MODELS_END"
)

if (( DRY_RUN )); then echo "$SECTION"; exit 0; fi

tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
KC_SECTION="$SECTION" python3 - "$CONFIG_FILE" "$tmp" <<'PY'
import os, sys, pathlib
src = pathlib.Path(sys.argv[1]).read_text()
section = os.environ["KC_SECTION"]
i = src.find("# >>> KLOUDCHAT_MODELS_START")
j = src.find("# <<< KLOUDCHAT_MODELS_END")
if i == -1 or j == -1 or j < i:
    sys.exit("error: KLOUDCHAT_MODELS markers missing or reversed")
ls = src.rfind("\n", 0, i) + 1
le = src.find("\n", j); le = len(src) if le == -1 else le
pathlib.Path(sys.argv[2]).write_text(src[:ls] + section + src[le:])
PY
mv "$tmp" "$CONFIG_FILE"; trap - EXIT

echo "==> $CONFIG_FILE — ${#MODELS[@]} models"
