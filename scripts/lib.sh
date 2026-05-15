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

# Ollama-only 카탈로그 — union discovery로 어느 노드든 보유 시 그 노드(들)에 deployment 등록.
# 어느 노드도 보유 안 할 때 MODEL_OR_FREE 매핑이 있으면 OR free 로 fallback.
OLLAMA_CHAT_CATALOG=(
  gpt-oss:20b gpt-oss:120b
  qwen3.5:9b qwen3.5:35b
  gemma4:26b gemma3:27b
  qwen3-coder-next:q4_K_M qwen3-coder-next:q8_0
)
OLLAMA_EMBED_CATALOG=(bge-m3)

# Ollama 카탈로그 모델 → OR free model id 매핑.
# Ollama 노드에 pulled이면 ollama_chat 우선, 없으면 이 매핑 + OR 키가 있을 때만 OR free로 fallback.
# OR free 카탈로그는 https://openrouter.ai/models?supported_parameters=free 에서 확인 후 활성화.
declare -A MODEL_OR_FREE=(
  [gpt-oss:20b]=openai/gpt-oss-20b:free
  [gpt-oss:120b]=openai/gpt-oss-120b:free
  [gemma3:27b]=google/gemma-3-27b-it:free
  # [qwen3.5:9b]=qwen/qwen3-8b:free                   # 사이즈 근사 — OR ID 검증 후 활성
  # [qwen3.5:35b]=qwen/qwen3-32b:free                 # 동
  # [qwen3-coder-next:q4_K_M]=qwen/qwen3-coder:free   # OR가 :free 제공 여부 미확인
  # [qwen3-coder-next:q8_0]=qwen/qwen3-coder:free
)

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
  case " ${OPENAI_NATIVE_MODELS[*]} " in
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

# Ollama 노드 URL 정규화 (앞뒤 공백/슬래시 trim, host.docker.internal 보존).
__ollama_normalize_url() {
  local u="$1"
  u="$(echo "$u" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s|/$||')"
  echo "$u"
}

# 단일 노드의 모델 조회 시 host.docker.internal → localhost (호스트에서 호출용).
__ollama_node_models() {
  local raw="$1" url probe
  url="$(__ollama_normalize_url "$raw")"
  probe="${url//host.docker.internal/localhost}"
  curl -sf --max-time 5 "${probe}/api/tags" 2>/dev/null | jq -r '.models[]?.name' 2>/dev/null || true
}

# 노드별 모델 매핑. 각 줄: <원본 URL>\t<model>. unreachable 노드는 stderr 경고만.
# OLLAMA_URLS 비었으면 빈 출력.
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

# 모든 reachable 노드의 모델 합집합 (중복 제거, 정렬). 노드별 정보 없이 모델만 필요할 때.
ollama_union_models() {
  ollama_union_node_models | awk -F'\t' 'NF==2 {print $2}' | sort -u
}

# 특정 모델을 보유한 노드 URL 목록 (한 줄에 1개). tag 누락 시 :latest 보정.
ollama_nodes_for_model() {
  local needle="$1"
  [[ "$needle" != *:* ]] && needle="${needle}:latest"
  ollama_union_node_models | awk -F'\t' -v m="$needle" '$2==m {print $1}'
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

# Ollama union 결과에서 모델 보유 확인. tag 누락 시 :latest 보정 (gen-litellm-config.sh ollama_has 와 동일).
ollama_pulled_has() {
  local pulled="$1" needle="$2"
  [[ "$needle" != *:* ]] && needle="${needle}:latest"
  grep -qxF "$needle" <<<"$pulled"
}

# LiteLLM team allowlist 생성용 canonical 모델 CSV.
# gen-litellm-config.sh 의 emit 로직과 일치해야 함 — 실제 등록될 model_name 만 반환.
litellm_chat_models_csv() {
  local pulled; pulled="$(ollama_union_models 2>/dev/null || true)"
  local gemma_skip=""
  if ollama_pulled_has "$pulled" gemma4:26b && ollama_pulled_has "$pulled" gemma3:27b; then
    gemma_skip="gemma3:27b"
  fi
  local out=() m
  for m in "${OPENAI_NATIVE_MODELS[@]}"; do
    if has_openai_native || has_openrouter; then out+=("openai/$m"); fi
  done
  for m in "${ANTHROPIC_NATIVE_MODELS[@]}"; do
    if has_anthropic_native || has_openrouter; then out+=("anthropic/$m"); fi
  done
  for m in "${GOOGLE_NATIVE_MODELS[@]}"; do
    if has_google_native || has_openrouter; then out+=("google/$m"); fi
  done
  for m in "${OLLAMA_CHAT_CATALOG[@]}"; do
    [[ "$m" == "$gemma_skip" ]] && continue
    if ollama_pulled_has "$pulled" "$m"; then
      out+=("ollama/$m")
    elif [[ -n "${MODEL_OR_FREE[$m]:-}" ]] && has_openrouter; then
      out+=("ollama/$m")
    fi
  done
  for m in "${OLLAMA_EMBED_CATALOG[@]}"; do
    if ollama_pulled_has "$pulled" "$m"; then out+=("$m"); fi
  done
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
