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

OLLAMA_PULLED="$(ollama_intersect_models || true)"
ollama_has() {
  local needle="$1"
  [[ "$needle" != *:* ]] && needle="${needle}:latest"
  grep -qxF "$needle" <<<"$OLLAMA_PULLED"
}

MODELS=()
# Commercial: native key 있으면 등록, 없으면 OR 있을 때 등록 (canonical name 동일).
for m in "${OPENAI_NATIVE_MODELS[@]}";    do has_openai_native    || has_openrouter || continue; MODELS+=("openai/${m}");    done
for m in "${ANTHROPIC_NATIVE_MODELS[@]}"; do has_anthropic_native || has_openrouter || continue; MODELS+=("anthropic/${m}"); done
for m in "${GOOGLE_NATIVE_MODELS[@]}";    do has_google_native    || has_openrouter || continue; MODELS+=("google/${m}");    done
# gpt-oss: ollama 또는 OR 둘 중 하나 가능할 때만.
for m in "${GPT_OSS_MODELS[@]}"; do
  if ollama_has "$m" || has_openrouter; then MODELS+=("openai/${m}"); fi
done
# Ollama-only 카탈로그.
GEMMA_SKIP=""
ollama_has gemma4:26b && ollama_has gemma3:27b && GEMMA_SKIP=gemma3:27b
for m in "${OLLAMA_CHAT_CATALOG[@]}"; do
  [[ "$m" == "$GEMMA_SKIP" ]] && continue
  ollama_has "$m" && MODELS+=("ollama/${m}")
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
