#!/usr/bin/env bash

[[ -n "${__KC_LIB_SH:-}" ]] && return 0
__KC_LIB_SH=1

__R='\033[0;31m'; __G='\033[0;32m'; __Y='\033[1;33m'
__B='\033[1;34m'; __C='\033[0;36m'; __N='\033[0m'

hdr()  { echo; echo -e "${__B}━━━ $* ━━━${__N}"; }
ok()   { echo -e "${__G}✓${__N} $*"; }
info() { echo -e "${__G}[INFO]${__N} $*"; }
warn() { echo -e "${__Y}[WARN]${__N} $*"; }
err()  { echo -e "${__R}✗${__N} $*" >&2; }

ask() {
  (( ${YES:-0} )) && return 0
  local ans; read -rp "$(echo -e "${__C}?${__N} $1 [y/N] ")" ans
  [[ "$ans" =~ ^[Yy]$ ]]
}

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

comfyui_urls() {
  local v; v="$(env_get COMFYUI_URLS)"; echo "${v:-http://host.docker.internal:8188}"
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

gpu_supports_gemma4() {
  case "$(detect_gpu_class)" in
    blackwell-pro|blackwell-5090|none) return 1 ;;
    *)                                 return 0 ;;
  esac
}

# gpt-oss:120b는 ~65-80GB. GB10 unified(128GB) / Blackwell PRO(96GB) 에서만 동작.
gpu_supports_120b() {
  case "$(detect_gpu_class)" in
    gb10|blackwell-pro) return 0 ;;
    nvidia-other) (( $(get_gpu_vram_mb) >= 80000 )) ;;
    *) return 1 ;;
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

CHAT_MODELS=(
  qwen3.5:9b
  qwen3.5:35b
  gemma4:26b
  gemma3:27b
  gpt-oss:20b
  gpt-oss:120b
  qwen3-coder-next:q4_K_M
  qwen3-coder-next:q8_0
  claude-opus-4.7
  claude-opus-4.6
  claude-sonnet-4.6
  gpt-5.5
)
LIBRECHAT_MODELS=(
  qwen3.5:9b
  qwen3.5:35b
  gemma4:26b
  gemma3:27b
  gpt-oss:20b
  gpt-oss:120b
  claude-opus-4.7
  claude-opus-4.6
  claude-sonnet-4.6
  gpt-5.5
)
EMBED_MODELS=(bge-m3)

# OpenRouter 기준 가격 (USD per 1M tokens). 신규 모델 추가 시 여기도 갱신.
declare -A MODEL_PRICE_IN_PM=(
  [qwen3.5:9b]=0.04
  [qwen3.5:35b]=0.08
  [gemma4:26b]=0.08
  [gemma3:27b]=0.08
  [gpt-oss:20b]=0
  [gpt-oss:120b]=0
  [qwen3-coder-next:q4_K_M]=0.15
  [qwen3-coder-next:q8_0]=0.22
  [claude-opus-4.7]=5
  [claude-opus-4.6]=5
  [claude-sonnet-4.6]=3
  [gpt-5.5]=5
  [bge-m3]=0.02
)
declare -A MODEL_PRICE_OUT_PM=(
  [qwen3.5:9b]=0.10
  [qwen3.5:35b]=0.28
  [gemma4:26b]=0.16
  [gemma3:27b]=0.16
  [gpt-oss:20b]=0
  [gpt-oss:120b]=0
  [qwen3-coder-next:q4_K_M]=1.20
  [qwen3-coder-next:q8_0]=1.80
  [claude-opus-4.7]=25
  [claude-opus-4.6]=25
  [claude-sonnet-4.6]=15
  [gpt-5.5]=30
)

# OpenRouter로 라우팅할 모델. key=로컬 표기, value=OR model id (free tier면 :free 접미사 포함).
declare -A MODEL_OPENROUTER_FREE=(
  [gpt-oss:20b]=openai/gpt-oss-20b:free
  [gpt-oss:120b]=openai/gpt-oss-120b:free
  [claude-opus-4.7]=anthropic/claude-opus-4.7
  [claude-opus-4.6]=anthropic/claude-opus-4.6
  [claude-sonnet-4.6]=anthropic/claude-sonnet-4.6
  [gpt-5.5]=openai/gpt-5.5
)

per_token_cost() { awk -v v="$1" 'BEGIN { printf "%.10f", v/1000000 }'; }

# 모델 식별자 prefix. OR 라우팅 모델은 OR id의 provider segment(anthropic/openai/google/...),
# 그 외는 'ollama'.
model_prefix() {
  local or="${MODEL_OPENROUTER_FREE[$1]:-}"
  [[ -n "$or" ]] && { echo "${or%%/*}"; return; }
  echo ollama
}

litellm_chat_models_csv() {
  local out="" m
  for m in "${CHAT_MODELS[@]}"; do out+="${out:+,}$(model_prefix "$m")/${m}"; done
  echo "$out"
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
