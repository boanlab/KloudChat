#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"
CONFIG_FILE="${PROJECT_DIR}/litellm-config.yaml"
CONFIG_EXAMPLE="${PROJECT_DIR}/litellm-config.yaml.example"
source "${SCRIPT_DIR}/lib.sh"

MARKER_START='# >>> KLOUDCHAT_AUTOGEN_START'
MARKER_END='# <<< KLOUDCHAT_AUTOGEN_END'

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) echo "Usage: $(basename "$0") [--dry-run]"; exit 0 ;;
    *)         err "Unknown: $arg"; exit 2 ;;
  esac
done

# litellm-config.yaml 자체 = gitignored — 사용자 배포 한정. 없으면 example 에서
# 초기화(router_settings / general_settings 등 정적 섹션 + 빈 AUTOGEN 블록).
if [[ ! -f "$CONFIG_FILE" ]]; then
  [[ -f "$CONFIG_EXAMPLE" ]] || { err "$CONFIG_FILE 도 $CONFIG_EXAMPLE 도 없음."; exit 1; }
  cp "$CONFIG_EXAMPLE" "$CONFIG_FILE"
  info "$CONFIG_FILE ← $CONFIG_EXAMPLE"
fi
assert_regen_writable "$CONFIG_FILE" || exit 1
grep -qF "$MARKER_START" "$CONFIG_FILE" && grep -qF "$MARKER_END" "$CONFIG_FILE" \
  || { err "AUTOGEN markers 누락: $CONFIG_FILE"; exit 1; }

# vLLM max_model_len 디스커버리 실패 시 emit 용 ctx 폴백. 정상 시 deployment 마다
# `/v1/models` 의 max_model_len 직접 사용.
CTX_FALLBACK=32768

# canonical name=`<prov>/<id>`, 라우트=`openrouter/<prov>/<id>`. OR 키 없으면 미등록.
emit_commercial_or() {
  # route_prov(5번째, 기본=prov): OR 실제 슬러그 provider 가 표시용과 다를 때 분리.
  # 예: 표시 model_name=meta/llama-4-maverick, 라우트=openrouter/meta-llama/llama-4-maverick
  local prov="$1" id="$2" in_pm="$3" out_pm="$4" route_prov="${5:-$1}"
  has_openrouter || return 0
  echo "  - model_name: ${prov}/${id}"
  echo "    litellm_params:"
  echo "      model: openrouter/${route_prov}/${id}"
  echo "      api_key: os.environ/OPENROUTER_API_KEY"
  echo "    model_info:"
  echo "      input_cost_per_token: $(per_token_cost "$in_pm")"
  echo "      output_cost_per_token: $(per_token_cost "$out_pm")"
}

# 로컬 vLLM 모델의 OR 동일모델 폴백 deployment. router_settings.fallbacks 가
# local/<name> → 이 model_name(OR slug) 으로 라우팅 — 노드 다운/과부하(에러·cooldown)
# 시 OR 자동 우회. LibreChat dropdown 미노출(gen-librechat 가 안 emit). 로컬
# primary 배포된 경우(url 비어있지 않음)만 emit.
emit_or_fallback() {
  local local_url="$1" or_slug="$2" in_pm="$3" out_pm="$4"
  has_openrouter || return 0
  [[ -n "$local_url" ]] || return 0
  echo "  - model_name: ${or_slug}"
  echo "    litellm_params:"
  echo "      model: openrouter/${or_slug}"
  echo "      api_key: os.environ/OPENROUTER_API_KEY"
  echo "    model_info:"
  echo "      input_cost_per_token: $(per_token_cost "$in_pm")"
  echo "      output_cost_per_token: $(per_token_cost "$out_pm")"
}

# LiteLLM router 의 enable_pre_call_checks = token count 시 messages 만 보고
# tools schema + chat-template wrapper 제외. tool-heavy agent 류는 그
# overhead 가 ~1.5K~4K → 작은 ctx(16K) 노드에서 router 가 OK 라고 본
# 호출도 vLLM wrap 후 max_model_len 초과. emit 한도를 absolute headroom 만큼
# 보수적으로 잡아 misroute 회피. KC_PRE_CALL_HEADROOM 으로 운영자 조정 가능.
__declared_max_input_tokens() {
  local ctx="$1" headroom="${KC_PRE_CALL_HEADROOM:-4096}"
  local v=$(( ctx - headroom ))
  # 매우 작은 ctx 의 floor — declared 가 너무 작으면 무의미 → 1024 미만이면
  # ctx 의 절반으로 fallback.
  (( v < 1024 )) && v=$(( ctx > 2048 ? ctx / 2 : ctx ))
  echo "$v"
}

# bge-m3 미보유 환경의 RAG 폴백. 라우트=`openrouter/openai/<id>`
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

# vLLM URL csv 에서 model_name 매칭 노드 추출. 디스커버리 0건이면 csv 그대로 fallback
# (vLLM startup 중일 때 — LiteLLM cooldown 이 ready 후 자동 복귀). 두 경로 모두
# 출력 dedup — vllm_union_node_models 자체는 이미 dedup 하나, fallback 경로는
# raw csv 그대로 iterate → 여기서 한 번 더 차단해 AUTOGEN 중복 deployment 방지.
__vllm_resolved_urls() {
  local want="$1" url_csv="$2" discovered
  discovered="$(vllm_union_node_models "$url_csv" \
    | awk -F'\t' -v w="$want" '$2==w {print $1}' \
    | awk '!seen[$0]++')"
  if [[ -n "$discovered" ]]; then echo "$discovered"; return 0; fi
  local IFS=, u
  for u in $url_csv; do
    u="$(__vllm_normalize_url "$u")"
    [[ -n "$u" ]] && echo "$u"
  done | awk '!seen[$0]++'
}

emit_vllm_chat() {
  local m="$1" url_csv="$2" in_pm out_pm urls ctx_fallback
  [[ -n "$url_csv" ]] || return 0
  in_pm="${MODEL_PRICE_IN_PM[$m]:-}"
  out_pm="${MODEL_PRICE_OUT_PM[$m]:-}"
  # 폴백 chain: (1) vLLM /v1/models 의 max_model_len — scheduler 가 노드별 다른 값
  # 박을 수 있으므로 deployment 마다 따로 조회. (2) 실패 시 CTX_FALLBACK
  # (backend 막 떴거나 gen-litellm-config 단독 실행 시 안전망).
  ctx_fallback="$CTX_FALLBACK"
  urls="$(__vllm_resolved_urls "local/${m}" "$url_csv")"
  while IFS= read -r url; do
    [[ -n "$url" ]] || continue
    local ctx
    if ! ctx="$(vllm_discover_max_len "$url")"; then
      warn "${url} max_model_len 디스커버리 실패 — 폴백 ${ctx_fallback}"
      ctx="$ctx_fallback"
    fi
    echo "  - model_name: local/${m}"
    echo "    litellm_params:"
    echo "      model: hosted_vllm/local/${m}"
    echo "      api_base: ${url%/}/v1"
    echo "    model_info:"
    # vLLM = --enable-auto-tool-choice + tool-call-parser 로 native function calling
    # 지원. 이 플래그 노출해야 LibreChat agent 가 ReAct(action/action_input)
    # 텍스트 fallback 대신 구조화된 tool_calls 로 도구 실제 실행.
    echo "      supports_function_calling: true"
    echo "      supports_tool_choice: true"
    # 두 field 분리 — declared = router 라우팅 결정(보수적, tools schema buffer 확보),
    # actual = truncate_to_ctx 의 trim 한도(deployment 실제 capacity 활용).
    # 노드별 다른 ctx 면 router 가 입력 토큰 수 기반 deployment 자동 선택.
    echo "      max_input_tokens: $(__declared_max_input_tokens "$ctx")"
    echo "      actual_ctx_tokens: ${ctx}"
    if [[ -n "$in_pm" && -n "$out_pm" ]]; then
      echo "      input_cost_per_token: $(per_token_cost "$in_pm")"
      echo "      output_cost_per_token: $(per_token_cost "$out_pm")"
    fi
  done <<<"$urls"
}

# GPU 없는 OR 전용 배포: 로컬 두뇌 model_name(local/<m>)을 OR 동일모델로 직결 등록.
# 로컬 vLLM 없어도 에이전트(Super Agent/스튜디오/Deep Research) 두뇌 확보.
emit_local_or_brain() {  # $1=local-model  $2=or-slug  $3=in_pm  $4=out_pm
  echo "  - model_name: local/$1"
  echo "    litellm_params:"
  echo "      model: openrouter/$2"
  echo "      api_key: os.environ/OPENROUTER_API_KEY"
  echo "    model_info:"
  echo "      supports_function_calling: true"
  echo "      supports_tool_choice: true"
  echo "      input_cost_per_token: $(per_token_cost "$3")"
  echo "      output_cost_per_token: $(per_token_cost "$4")"
}

# 두뇌 등록: 로컬 vLLM URL 있으면 로컬(+별도 OR twin 폴백), 없고 OR 키 있으면 OR 직결.
emit_brain() {  # $1=local-model  $2=url_csv  $3=or-slug  $4=or_in_pm  $5=or_out_pm
  if [[ -n "$2" ]]; then emit_vllm_chat "$1" "$2"
  elif has_openrouter; then emit_local_or_brain "$1" "$3" "$4" "$5"; fi
}

emit_vllm_embed() {
  local m="$1" url_csv="$2" in_pm urls
  [[ -n "$url_csv" ]] || return 0
  in_pm="${MODEL_PRICE_IN_PM[$m]:-}"
  urls="$(__vllm_resolved_urls "${m}" "$url_csv")"
  while IFS= read -r url; do
    [[ -n "$url" ]] || continue
    echo "  - model_name: ${m}"
    echo "    model_info:"
    echo "      mode: embedding"
    [[ -n "$in_pm" ]] && echo "      input_cost_per_token: $(per_token_cost "$in_pm")"
    echo "    litellm_params:"
    echo "      model: hosted_vllm/${m}"
    echo "      api_base: ${url%/}/v1"
  done <<<"$urls"
}

# super-agent-shim 의 single-pass 챗 두뇌(gemma-4-26b) 합성 — 가격 미설정(내부
# 호출이 LiteLLM 에 별도 logged → double-count 방지).
emit_super_agent() {
  super_agent_eligible || return 0
  echo "  - model_name: local/auto-route"
  echo "    litellm_params:"
  echo "      model: openai/super-agent"
  echo "      api_base: http://super-agent-shim:8080/v1"
  echo "      api_key: os.environ/LITELLM_MASTER_KEY"
  # shim 의 CHAT_MODEL=gemma-4-26b 가 큰 ctx 노드로 LB → 그 ctx 그대로 노출.
  # 누락 시 LibreChat 가 모델명으로 작은 기본값(~38K) 추정 → 긴 입력
  # (대용량 파일 첨부 등) 을 보내기도 전에 프루닝으로 거부.
  local sa_ctx="${KC_SUPER_AGENT_CTX:-131072}"
  echo "    model_info:"
  echo "      max_input_tokens: $(__declared_max_input_tokens "$sa_ctx")"
  echo "      actual_ctx_tokens: ${sa_ctx}"
}

# 등록 순서 = local → openai → anthropic → google (provider 그룹 단위)
SECTION=$(
  echo "  ${MARKER_START}"
  # --- local (vLLM + super-agent shim) ---
  emit_super_agent                                          # local/auto-route (Super Agent 챗 두뇌)
  # 챗/리서치 두뇌: 로컬 vLLM 있으면 로컬, 없고 OR 키 있으면 OR 동일모델 직결.
  emit_brain "gemma-4-26b"  "$(env_get VLLM_GEMMA26_URL)"  "google/gemma-4-26b-a4b-it" 0.06 0.33
  emit_brain "qwen3.5:122b" "$(env_get VLLM_QWEN122B_URL)" "qwen/qwen3.5-122b-a10b"    0.26 2.08
  # 코딩 보조 — litellm 등록만(LibreChat UI/agent 비노출). URL 비면 자동 skip.
  emit_vllm_chat  "qwen3-coder-next" "$(env_get VLLM_CODERNEXT_URL)"
  emit_vllm_embed "bge-m3"           "$(env_get VLLM_BGE_M3_URL)"
  # --- openai (commercial + embed fallback) ---
  for m in "${OPENAI_MODELS[@]}";        do emit_commercial_or openai "$m" "${MODEL_PRICE_IN_PM[$m]}" "${MODEL_PRICE_OUT_PM[$m]}"; done
  for m in "${OPENAI_EMBED_CATALOG[@]}"; do emit_openai_embed "$m"; done
  # --- anthropic ---
  for m in "${ANTHROPIC_MODELS[@]}"; do emit_commercial_or anthropic "$m" "${MODEL_PRICE_IN_PM[$m]}" "${MODEL_PRICE_OUT_PM[$m]}"; done
  # --- google ---
  for m in "${GOOGLE_MODELS[@]}";    do emit_commercial_or google    "$m" "${MODEL_PRICE_IN_PM[$m]}" "${MODEL_PRICE_OUT_PM[$m]}"; done
  # --- deepseek / x-ai / perplexity / meta-llama / qwen (2026 OR paid) ---
  for m in "${DEEPSEEK_MODELS[@]}";   do emit_commercial_or deepseek   "$m" "${MODEL_PRICE_IN_PM[$m]}" "${MODEL_PRICE_OUT_PM[$m]}"; done
  for m in "${XAI_MODELS[@]}";        do emit_commercial_or x-ai       "$m" "${MODEL_PRICE_IN_PM[$m]}" "${MODEL_PRICE_OUT_PM[$m]}"; done
  for m in "${PERPLEXITY_MODELS[@]}"; do emit_commercial_or perplexity "$m" "${MODEL_PRICE_IN_PM[$m]}" "${MODEL_PRICE_OUT_PM[$m]}"; done
  for m in "${META_MODELS[@]}";       do emit_commercial_or meta       "$m" "${MODEL_PRICE_IN_PM[$m]}" "${MODEL_PRICE_OUT_PM[$m]}" meta-llama; done
  for m in "${QWEN_MODELS[@]}";       do emit_commercial_or qwen       "$m" "${MODEL_PRICE_IN_PM[$m]}" "${MODEL_PRICE_OUT_PM[$m]}"; done
  # --- 그 외: 로컬 vLLM → OR 동일모델 폴백 twin (router_settings.fallbacks 참조, UI 비노출) ---
  emit_or_fallback "$(env_get VLLM_GEMMA26_URL)"   "google/gemma-4-26b-a4b-it" 0.06 0.33
  emit_or_fallback "$(env_get VLLM_QWEN122B_URL)"  "qwen/qwen3.5-122b-a10b"    0.26 2.08
  emit_or_fallback "$(env_get VLLM_CODERNEXT_URL)" "qwen/qwen3-coder-next"     0.11 0.80
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
ok "$CONFIG_FILE — $n models"
info "keys: openrouter=$(has_openrouter && echo y || echo n)"
