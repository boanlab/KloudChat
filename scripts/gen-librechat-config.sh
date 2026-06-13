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
    *)         err "Unknown: $arg"; exit 2 ;;
  esac
done

[[ -f "$CONFIG_FILE" ]] || { err "$CONFIG_FILE 없음."; exit 1; }
assert_regen_writable "$CONFIG_FILE" || exit 1
grep -qF '# >>> KLOUDCHAT_MODELS_START' "$CONFIG_FILE" \
  && grep -qF '# <<< KLOUDCHAT_MODELS_END' "$CONFIG_FILE" \
  || { err "KLOUDCHAT_MODELS marker 누락: $CONFIG_FILE"; exit 1; }

MODELS=()
# 로컬 vLLM 모델 먼저 — dropdown / agent builder 상단 노출.
# 챗 두뇌(gemma-4-26b) = vLLM 전용. 중복 회피.
vllm_gemma26_url="$(env_get VLLM_GEMMA26_URL || true)"
if [[ -n "$vllm_gemma26_url" ]] && ! printf '%s\n' "${MODELS[@]}" | grep -qxF "local/gemma-4-26b"; then
  MODELS+=("local/gemma-4-26b")
fi
# Deep Research(122b) 도 vLLM 전용 — 사용자 직접 선택 가능.
vllm_qwen122b_url="$(env_get VLLM_QWEN122B_URL || true)"
if [[ -n "$vllm_qwen122b_url" ]] && ! printf '%s\n' "${MODELS[@]}" | grep -qxF "local/qwen3.5:122b"; then
  MODELS+=("local/qwen3.5:122b")
fi
# 그다음 OpenRouter 상용 모델.
if has_openrouter; then
  for m in "${OPENAI_MODELS[@]}";    do MODELS+=("openai/${m}");    done
  for m in "${ANTHROPIC_MODELS[@]}"; do MODELS+=("anthropic/${m}"); done
  for m in "${GOOGLE_MODELS[@]}";    do MODELS+=("google/${m}");    done
  for m in "${DEEPSEEK_MODELS[@]}";   do MODELS+=("deepseek/${m}");   done
  for m in "${XAI_MODELS[@]}";        do MODELS+=("x-ai/${m}");       done
  for m in "${PERPLEXITY_MODELS[@]}"; do MODELS+=("perplexity/${m}"); done
  for m in "${META_MODELS[@]}";       do MODELS+=("meta/${m}");      done
  for m in "${QWEN_MODELS[@]}";       do MODELS+=("qwen/${m}");       done
fi
# Super Agent 내부 라우트(local/auto-route) = 모델 목록 포함 필수 — LibreChat
# ResumableAgentController 가 에이전트 model 을 엔드포인트 models 목록과 대조 검증 →
# 누락 시 Super Agent illegal_model_request 초기화 실패. UI 노출은 librechat-patch.py
# 의 hide_auto_route_in_builder 가 agent builder 모델 메뉴에서만 숨김(Super Agent 는
# 이 모델로 정상 동작). 맨 끝에 둠 — 패치 실패 시에도 목록 최하단. 조건 = super_agent_eligible.
if super_agent_eligible && ! printf '%s\n' "${MODELS[@]}" | grep -qxF "local/auto-route"; then
  MODELS+=("local/auto-route")
fi

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

# Video Studio 기본 모델: 로컬 ComfyUI 있으면 ltx-video(무료), 없고 OR 키면 veo-lite(외부·저가).
# generate_video MCP 의 VIDEO_MODEL(librechat.yaml literal) → 매 gen 시 ComfyUI 가용성에 맞춤.
vid_default="ltx-video"; [[ -z "$(env_get COMFYUI_URLS)" ]] && has_openrouter && vid_default="veo-lite"
sed -i "s|VIDEO_MODEL: \"[^\"]*\"|VIDEO_MODEL: \"${vid_default}\"|" "$CONFIG_FILE"

ok "$CONFIG_FILE — ${#MODELS[@]} models (Video 기본=${vid_default})"
