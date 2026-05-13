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

EXCLUDE=()
if gpu_supports_gemma4; then EXCLUDE+=(gemma3:27b); else EXCLUDE+=(gemma4:26b); fi
gpu_supports_120b || EXCLUDE+=(gpt-oss:120b)

excluded() { local m="$1" x; for x in "${EXCLUDE[@]}"; do [[ "$x" == "$m" ]] && return 0; done; return 1; }

SECTION=$(
  echo "          # >>> KLOUDCHAT_MODELS_START"
  for m in "${LIBRECHAT_MODELS[@]}"; do
    excluded "$m" && continue
    echo "          - \"$(model_prefix "$m")/${m}\""
  done
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

class="$(detect_gpu_class)"
echo "==> $CONFIG_FILE — GPU class: $class, excluded: ${EXCLUDE[*]}"
