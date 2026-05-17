#!/usr/bin/env bash

[[ -n "${__KC_LIB_SH:-}" ]] && return 0
__KC_LIB_SH=1

__R='\033[0;31m'; __G='\033[0;32m'; __Y='\033[1;33m'
__B='\033[1;34m'; __N='\033[0m'

hdr()  { echo; echo -e "${__B}━━━ $* ━━━${__N}"; }
ok()   { echo -e "${__G}✓${__N} $*"; }
info() { echo -e "${__G}[INFO]${__N} $*"; }
warn() { echo -e "${__Y}[WARN]${__N} $*"; }
err()  { echo -e "${__R}✗${__N} $*" >&2; }

detect_os() {
  case "$(uname -s)" in Linux) echo linux ;; *) echo unsupported ;; esac
}

detect_arch() {
  case "$(uname -m)" in
    x86_64|amd64)  echo amd64 ;;
    aarch64|arm64) echo arm64 ;;
    *)             echo unsupported ;;
  esac
}

require_supported_platform() {
  if [[ "$(detect_os)" == unsupported || "$(detect_arch)" == unsupported ]]; then
    err "Unsupported: $(uname -s) $(uname -m) (Linux x86_64/aarch64 만 지원)"
    exit 1
  fi
}

__PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
__DEFAULT_ENV_FILE="${__PROJECT_DIR}/.env"

env_get() {
  local file="${ENV_FILE:-$__DEFAULT_ENV_FILE}"
  [[ -f "$file" ]] || return 0
  grep -E "^$1=" "$file" | head -1 | cut -d= -f2- || true
}

env_set() {
  local key="$1" val="$2" file="${ENV_FILE:-$__DEFAULT_ENV_FILE}"
  [[ -f "$file" ]] || { echo "[env_set] error: ${file} not found" >&2; return 1; }
  if grep -qE "^${key}=" "$file"; then sed -i "s|^${key}=.*|${key}=${val}|" "$file"
  else echo "${key}=${val}" >> "$file"; fi
}

has_nvidia_gpu() { command -v nvidia-smi &>/dev/null && nvidia-smi -L &>/dev/null; }

has_gb10() {
  has_nvidia_gpu || return 1
  nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 | grep -q 'GB10'
}

get_gpu_name() {
  has_nvidia_gpu || { echo ""; return; }
  nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1
}

get_mem_mb() {
  awk '/^MemTotal:/ {print int($2/1024); exit}' /proc/meminfo 2>/dev/null || echo 0
}

get_gpu_vram_mb() {
  has_nvidia_gpu || { echo 0; return; }
  if has_gb10; then get_mem_mb; return; fi
  local v
  v="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
        | grep -oE '^[0-9]+' | sort -nr | head -1 || true)"
  echo "${v:-0}"
}

detect_gpu_class() {
  has_nvidia_gpu || { echo none; return; }
  has_gb10 && { echo gb10; return; }
  case "$(get_gpu_name)" in
    *"RTX PRO 6000 Blackwell"*|*"RTX 6000 Pro Blackwell"*|*"RTX 6000 PRO Blackwell"*) echo blackwell-pro ;;
    *"RTX 5090"*) echo blackwell-5090 ;;
    *"RTX 4090"*) echo ada-4090 ;;
    *)            echo nvidia-other ;;
  esac
}

# Image-model quant tier per (GPU class, alias). Echoes one of:
#   nvfp4 — Blackwell FP4 tensor cores, txt2img only (edit-2509 lacks NVFP4)
#   fp8   — FP8 tensor cores (Ada / Hopper / Blackwell)
#   gguf  — software dequant fallback (older / unknown cards)
# Pass the canonical alias as $1; defaults to txt2img variant when empty.
recommended_image_quant() {
  local alias="${1:-qwen-image}"
  case "$(detect_gpu_class)" in
    gb10|blackwell-pro|blackwell-5090)
      case "$alias" in
        *edit*) echo fp8 ;;
        *)      echo nvfp4 ;;
      esac ;;
    ada-4090) echo fp8 ;;
    *)        echo gguf ;;
  esac
}

get_free_disk_gb() {
  df -BG "${1:-.}" 2>/dev/null | awk 'NR==2 {gsub("G",""); print $4; exit}'
}

port_in_use() {
  local p="$1"
  if   command -v ss   &>/dev/null; then ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE ":${p}\$"
  elif command -v lsof &>/dev/null; then lsof -nP -iTCP:"$p" -sTCP:LISTEN &>/dev/null
  else return 1; fi
}

# Model catalog
# Commercial 카탈로그 — OpenRouter 경유. OR 키 없으면 미등록.
OPENAI_MODELS=(gpt-5.5 gpt-5 gpt-5-mini gpt-5-nano)
ANTHROPIC_MODELS=(claude-opus-4.7 claude-opus-4.6 claude-sonnet-4.6 claude-haiku-4.5)
GOOGLE_MODELS=(gemini-3.1-pro-preview gemini-2.5-pro gemini-2.5-flash)

# Ollama 카탈로그 — union discovery 로 어느 노드든 보유 시 그 노드(들)에 deployment 등록.
OLLAMA_CHAT_CATALOG=(
  qwen3.5:9b qwen3.6:35b
  llama3.1:8b llama3.3:70b
  nemotron3:33b
  qwen3-coder-next:q8_0
)
OLLAMA_EMBED_CATALOG=(bge-m3)

# OpenAI/OR 임베딩 카탈로그 — OR 키 있으면 등록. Ollama bge-m3 가 없을 때 RAG
# 가용성 보장용. setup.sh 가 bge-m3 미보유 시 .env 의 EMBEDDINGS_MODEL 을 자동 swap.
OPENAI_EMBED_CATALOG=(text-embedding-3-small)

# 기본 에이전트 우선순위 — 위 카탈로그 중 union 에 존재하는 첫 매치가 default preset.
# llama3.3:70b 는 모델 리스트엔 있지만 (사용자가 selector 에서 선택) default 후보에선 제외.
OLLAMA_DEFAULT_PRIORITY=(
  qwen3.6:35b
  qwen3.5:9b
)

# 모델 단가 (USD per 1M tokens). LiteLLM spend 추적용.
declare -A MODEL_PRICE_IN_PM=(
  [gpt-5.5]=5      [gpt-5]=2.5    [gpt-5-mini]=0.25    [gpt-5-nano]=0.05
  [claude-opus-4.7]=5    [claude-opus-4.6]=5    [claude-sonnet-4.6]=3    [claude-haiku-4.5]=1
  [gemini-3.1-pro-preview]=5    [gemini-2.5-pro]=2.5    [gemini-2.5-flash]=0.30
  [qwen3.5:9b]=0.04    [qwen3.6:35b]=0.08
  [llama3.1:8b]=0.04    [llama3.3:70b]=0.20
  [nemotron3:33b]=0.10
  [qwen3-coder-next:q8_0]=0.22
  [bge-m3]=0.02
  [text-embedding-3-small]=0.02
)
declare -A MODEL_PRICE_OUT_PM=(
  [gpt-5.5]=30    [gpt-5]=15    [gpt-5-mini]=2    [gpt-5-nano]=0.40
  [claude-opus-4.7]=25    [claude-opus-4.6]=25    [claude-sonnet-4.6]=15    [claude-haiku-4.5]=5
  [gemini-3.1-pro-preview]=15    [gemini-2.5-pro]=10    [gemini-2.5-flash]=2.5
  [qwen3.5:9b]=0.10    [qwen3.6:35b]=0.28
  [llama3.1:8b]=0.10    [llama3.3:70b]=0.60
  [nemotron3:33b]=0.30
  [qwen3-coder-next:q8_0]=1.80
)

per_token_cost() { awk -v v="$1" 'BEGIN { printf "%.10f", v/1000000 }'; }

has_openrouter() { [[ -n "$(env_get OPENROUTER_API_KEY)" ]]; }

# URL 양끝 공백 + trailing / trim.
__ollama_normalize_url() {
  local u="$1"
  u="$(echo "$u" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s|/$||')"
  echo "$u"
}

# 단일 노드의 모델 목록. host.docker.internal → localhost 치환 (compose 호스트에서 호출).
__ollama_node_models() {
  local raw="$1" url probe
  url="$(__ollama_normalize_url "$raw")"
  probe="${url//host.docker.internal/localhost}"
  curl -sf --max-time 5 "${probe}/api/tags" 2>/dev/null | jq -r '.models[]?.name' 2>/dev/null || true
}

# 노드별 모델 매핑 — 각 줄 "<URL>\t<model>". unreachable 노드는 stderr 경고 후 skip.
ollama_union_node_models() {
  local urls; urls="$(env_get OLLAMA_URLS)"
  [[ -n "$urls" ]] || return 0
  local IFS=, u nu tmp
  for u in $urls; do
    nu="$(__ollama_normalize_url "$u")"
    [[ -n "$nu" ]] || continue
    tmp="$(__ollama_node_models "$u")"
    if [[ -z "$tmp" ]]; then
      echo "  [warn] ollama unreachable: $nu" >&2
      continue
    fi
    while IFS= read -r m; do
      [[ -n "$m" ]] && printf '%s\t%s\n' "$nu" "$m"
    done <<<"$tmp"
  done
}

# 전 노드 모델 합집합 (중복 제거, 정렬).
ollama_union_models() {
  ollama_union_node_models | awk -F'\t' 'NF==2 {print $2}' | sort -u
}

# OLLAMA_DEFAULT_PRIORITY 순서대로 union 에서 lookup. 매치 0건이면 빈 출력.
ollama_default_chat_model() {
  local union; union="$(ollama_union_models)"
  local m tag
  for m in "${OLLAMA_DEFAULT_PRIORITY[@]}"; do
    tag="$m"; [[ "$tag" != *:* ]] && tag="${tag}:latest"
    if grep -qxF "$tag" <<<"$union"; then echo "$m"; return 0; fi
  done
  return 0
}

# 단일 ComfyUI 노드 보유 alias (flux-schnell / flux-dev / qwen-image / qwen-image-edit).
# 같은 alias 의 quant 변형 (nvfp4 / fp8 / gguf) 중 하나라도 있으면 alias 1회만 emit —
# 변형 선택은 shim 의 COMFYUI_ALIAS_VARIANTS preference order 가 담당.
__comfyui_node_models() {
  local raw="$1" url info
  url="$(echo "$raw" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  url="${url//host.docker.internal/localhost}"
  info="$(curl -sf --max-time 5 "${url}/object_info" 2>/dev/null)" || return 0
  local unets
  unets="$(echo "$info" | jq -r '
    (.UNETLoader.input.required.unet_name[0] // []) + (.UnetLoaderGGUF.input.required.unet_name[0] // []) | .[]?' 2>/dev/null)"
  grep -qxFf <(printf '%s\n' 'qwen-image-nvfp4.safetensors' 'qwen-image-fp8.safetensors' 'qwen-image-Q8_0.gguf') <<<"$unets" && echo qwen-image
  grep -qxFf <(printf '%s\n' 'qwen-image-edit-fp8.safetensors' 'qwen-image-edit-Q8_0.gguf')                     <<<"$unets" && echo qwen-image-edit
  grep -qxF  'flux1-schnell.safetensors' <<<"$unets" && echo flux-schnell
  grep -qxF  'flux1-dev.safetensors'     <<<"$unets" && echo flux-dev
}

# 노드별 alias 매핑 — 각 줄 "<URL>\t<alias>". unreachable 노드는 stderr 경고 후 skip.
comfyui_union_node_models() {
  local urls; urls="$(env_get COMFYUI_URLS)"
  [[ -n "$urls" ]] || return 0
  local IFS=, u nu tmp
  for u in $urls; do
    nu="$(echo "$u" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s|/$||')"
    [[ -n "$nu" ]] || continue
    tmp="$(__comfyui_node_models "$u")"
    if [[ -z "$tmp" ]]; then
      echo "  [warn] comfyui unreachable: $nu" >&2
      continue
    fi
    while IFS= read -r a; do
      [[ -n "$a" ]] && printf '%s\t%s\n' "$nu" "$a"
    done <<<"$tmp"
  done
}

# 전 노드 alias 합집합 (중복 제거).
comfyui_union_models() {
  comfyui_union_node_models | awk -F'\t' 'NF==2 {print $2}' | sort -u
}

# union 결과에서 모델 보유 확인. tag 누락 시 :latest 보정.
ollama_pulled_has() {
  local pulled="$1" needle="$2"
  [[ "$needle" != *:* ]] && needle="${needle}:latest"
  grep -qxF "$needle" <<<"$pulled"
}

# LiteLLM team allowlist 용 canonical model_name CSV.
# gen-litellm-config.sh 의 emit 로직과 같은 조건을 따라야 함 — 실제 등록될 모델만 반환.
litellm_chat_models_csv() {
  local pulled; pulled="$(ollama_union_models 2>/dev/null || true)"
  local out=() m
  if has_openrouter; then
    for m in "${OPENAI_MODELS[@]}";    do out+=("openai/$m");    done
    for m in "${ANTHROPIC_MODELS[@]}"; do out+=("anthropic/$m"); done
    for m in "${GOOGLE_MODELS[@]}";    do out+=("google/$m");    done
  fi
  for m in "${OLLAMA_CHAT_CATALOG[@]}"; do
    if ollama_pulled_has "$pulled" "$m"; then
      out+=("ollama/$m")
    fi
  done
  for m in "${OLLAMA_EMBED_CATALOG[@]}"; do
    if ollama_pulled_has "$pulled" "$m"; then out+=("$m"); fi
  done
  if has_openrouter; then
    for m in "${OPENAI_EMBED_CATALOG[@]}"; do out+=("$m"); done
  fi
  local IFS=,
  echo "${out[*]:-}"
}

LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-$(env_get LITELLM_MASTER_KEY)}"
LITELLM_URL="${LITELLM_URL:-http://localhost:8000}"
DATA_DIR="${__PROJECT_DIR}/scripts/.data"

__litellm_call() {
  local method="$1" endpoint="$2" payload="${3:-}"
  local args=(-s -w "\n%{http_code}" -X "$method" "${LITELLM_URL}${endpoint}"
              -H "Authorization: Bearer ${LITELLM_MASTER_KEY}")
  [[ -n "$payload" ]] && args+=(-H "Content-Type: application/json" -d "$payload")
  local resp; resp=$(curl "${args[@]}")
  local code; code=$(echo "$resp" | tail -1)
  local body; body=$(echo "$resp" | head -n -1)
  if (( code < 200 || code >= 300 )); then
    echo "ERROR [HTTP $code]: $body" >&2; return 1
  fi
  echo "$body"
}

litellm_post() { __litellm_call POST "$1" "$2"; }
litellm_get()  { __litellm_call GET  "$1"; }

wait_for_litellm() {
  local max="${1:-60}" elapsed=0
  echo -n "Waiting for LiteLLM..."
  until curl -sf "${LITELLM_URL}/health/liveliness" >/dev/null 2>&1; do
    (( elapsed >= max )) && { echo " timeout (${max}s)" >&2; return 1; }
    sleep 2; elapsed=$((elapsed+2)); echo -n "."
  done
  echo " ready"
}

team_id_by_alias() {
  mkdir -p "$DATA_DIR"
  local alias="$1" cache="${DATA_DIR}/teams.json" id
  if [[ -f "$cache" ]]; then
    id=$(jq -r --arg a "$alias" '.[] | select(.team_alias == $a) | .team_id' "$cache" 2>/dev/null || true)
    [[ -n "$id" ]] && { echo "$id"; return 0; }
  fi
  litellm_get "/team/list" | jq -r --arg a "$alias" '.[] | select(.team_alias == $a) | .team_id' 2>/dev/null || true
}
