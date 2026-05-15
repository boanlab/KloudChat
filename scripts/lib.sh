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

# ==== v5 model catalog ====
# 제공자별 native curated 리스트. native key 있으면 native, 없고 OR 있으면 OR로 라우팅. (상호배타)
OPENAI_NATIVE_MODELS=(gpt-5.5 gpt-5 gpt-5-mini gpt-5-nano)
ANTHROPIC_NATIVE_MODELS=(claude-opus-4.7 claude-opus-4.6 claude-sonnet-4.6 claude-haiku-4.5)
GOOGLE_NATIVE_MODELS=(gemini-3.1-pro-preview gemini-2.5-pro gemini-2.5-flash)

# gpt-oss는 native 없음. ollama 디스커버리 우선, 없으면 OR로 fallback.
GPT_OSS_MODELS=(gpt-oss:20b gpt-oss:120b)

# Ollama-only 카탈로그 — intersection discovery로 노드 보유 시에만 등록.
OLLAMA_CHAT_CATALOG=(qwen3.5:9b qwen3.5:35b gemma4:26b gemma3:27b qwen3-coder-next:q4_K_M qwen3-coder-next:q8_0)
OLLAMA_EMBED_CATALOG=(bge-m3)

# 가격 (USD per 1M tokens). 신규 모델 추가 시 같이 갱신.
declare -A MODEL_PRICE_IN_PM=(
  [gpt-5.5]=5      [gpt-5]=2.5    [gpt-5-mini]=0.25    [gpt-5-nano]=0.05
  [claude-opus-4.7]=5    [claude-opus-4.6]=5    [claude-sonnet-4.6]=3    [claude-haiku-4.5]=1
  [gemini-3.1-pro-preview]=5    [gemini-2.5-pro]=2.5    [gemini-2.5-flash]=0.30
  [gpt-oss:20b]=0    [gpt-oss:120b]=0
  [qwen3.5:9b]=0.04    [qwen3.5:35b]=0.08
  [gemma4:26b]=0.08    [gemma3:27b]=0.08
  [qwen3-coder-next:q4_K_M]=0.15    [qwen3-coder-next:q8_0]=0.22
  [bge-m3]=0.02
)
declare -A MODEL_PRICE_OUT_PM=(
  [gpt-5.5]=30    [gpt-5]=15    [gpt-5-mini]=2    [gpt-5-nano]=0.40
  [claude-opus-4.7]=25    [claude-opus-4.6]=25    [claude-sonnet-4.6]=15    [claude-haiku-4.5]=5
  [gemini-3.1-pro-preview]=15    [gemini-2.5-pro]=10    [gemini-2.5-flash]=2.5
  [gpt-oss:20b]=0    [gpt-oss:120b]=0
  [qwen3.5:9b]=0.10    [qwen3.5:35b]=0.28
  [gemma4:26b]=0.16    [gemma3:27b]=0.16
  [qwen3-coder-next:q4_K_M]=1.20    [qwen3-coder-next:q8_0]=1.80
)

per_token_cost() { awk -v v="$1" 'BEGIN { printf "%.10f", v/1000000 }'; }

has_openai_native()    { [[ -n "$(env_get OPENAI_API_KEY)" ]]; }
has_anthropic_native() { [[ -n "$(env_get ANTHROPIC_API_KEY)" ]]; }
has_google_native()    { [[ -n "$(env_get GEMINI_API_KEY)" ]]; }
has_openrouter()       { [[ -n "$(env_get OPENROUTER_API_KEY)" ]]; }

# canonical LibreChat 메뉴용 model_name. native/OR 라우트 무관하게 동일.
canonical_name() {
  local m="$1"
  case " ${OPENAI_NATIVE_MODELS[*]} ${GPT_OSS_MODELS[*]} " in
    *" $m "*) echo "openai/$m"; return ;;
  esac
  case " ${ANTHROPIC_NATIVE_MODELS[*]} " in
    *" $m "*) echo "anthropic/$m"; return ;;
  esac
  case " ${GOOGLE_NATIVE_MODELS[*]} " in
    *" $m "*) echo "google/$m"; return ;;
  esac
  echo "ollama/$m"
}

# Ollama 단일 노드 모델 목록 (실패 시 빈 출력).
__ollama_node_models() {
  local raw="$1" url
  url="$(echo "$raw" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  url="${url//host.docker.internal/localhost}"
  curl -sf --max-time 5 "${url}/api/tags" 2>/dev/null | jq -r '.models[]?.name' 2>/dev/null || true
}

# 모든 reachable ollama 노드의 모델 intersection. 한 줄에 모델 1개. URLS 비었거나 노드 0이면 빈 출력.
ollama_intersect_models() {
  local urls; urls="$(env_get OLLAMA_URLS)"
  [[ -n "$urls" ]] || return 0
  local IFS=, count=0 tmp acc
  acc=""
  for u in $urls; do
    tmp="$(__ollama_node_models "$u")"
    [[ -n "$tmp" ]] || { echo "  [warn] ollama unreachable: $u" >&2; continue; }
    if (( count == 0 )); then acc="$tmp"
    else acc="$(comm -12 <(echo "$acc" | sort -u) <(echo "$tmp" | sort -u))"
    fi
    count=$((count+1))
  done
  (( count > 0 )) || return 0
  echo "$acc"
}

# 단일 ComfyUI 노드가 보유한 모델 별칭 출력 (sdxl, qwen-image, qwen-image-edit, flux-schnell, flux-dev).
__comfyui_node_models() {
  local raw="$1" url info
  url="$(echo "$raw" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  url="${url//host.docker.internal/localhost}"
  info="$(curl -sf --max-time 5 "${url}/object_info" 2>/dev/null)" || return 0
  local ckpts unets
  ckpts="$(echo "$info" | jq -r '
    (.CheckpointLoaderSimple.input.required.ckpt_name[0] // []) | .[]?' 2>/dev/null)"
  unets="$(echo "$info" | jq -r '
    (.UNETLoader.input.required.unet_name[0] // []) + (.UnetLoaderGGUF.input.required.unet_name[0] // []) | .[]?' 2>/dev/null)"
  grep -qxF 'sd_xl_base_1.0.safetensors'        <<<"$ckpts" && echo sdxl
  grep -qxF 'qwen-image-Q8_0.gguf'              <<<"$unets" && echo qwen-image
  grep -qxF 'qwen-image-edit-Q8_0.gguf'         <<<"$unets" && echo qwen-image-edit
  grep -qxF 'flux1-schnell.safetensors'         <<<"$unets" && echo flux-schnell
  grep -qxF 'flux1-dev.safetensors'             <<<"$unets" && echo flux-dev
}

comfyui_intersect_models() {
  local urls; urls="$(env_get COMFYUI_URLS)"
  [[ -n "$urls" ]] || return 0
  local IFS=, count=0 tmp acc=""
  for u in $urls; do
    tmp="$(__comfyui_node_models "$u")"
    [[ -n "$tmp" ]] || { echo "  [warn] comfyui unreachable: $u" >&2; continue; }
    if (( count == 0 )); then acc="$tmp"
    else acc="$(comm -12 <(echo "$acc" | sort -u) <(echo "$tmp" | sort -u))"
    fi
    count=$((count+1))
  done
  (( count > 0 )) || return 0
  echo "$acc"
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
