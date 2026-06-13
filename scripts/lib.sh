#!/usr/bin/env bash

[[ -n "${__KC_LIB_SH:-}" ]] && return 0
__KC_LIB_SH=1

__R='\033[0;31m'; __G='\033[0;32m'; __Y='\033[1;33m'
__B='\033[1;34m'; __N='\033[0m'

hdr()  { echo; echo -e "${__B}━━━ $* ━━━${__N}"; }
# 진단 메시지는 모두 stderr 로 — `$(...)` 캡처(예: gen-*-config 의 SECTION)에 섞여
# 들어가 YAML 을 오염시키는 걸 방지(컬러 ESC 가 unacceptable char 로 깨짐).
ok()   { echo -e "${__G}✓${__N} $*" >&2; }
info() { echo -e "${__G}[INFO]${__N} $*" >&2; }
warn() { echo -e "${__Y}[WARN]${__N} $*" >&2; }
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
    err "Unsupported: $(uname -s) $(uname -m) (Linux amd64/arm64 만 지원)"
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

# tmp+mv 로 파일을 재생성하기 전 현재 유저가 실제로 쓸 수 있는지 검증한다.
# sudo 로 gen/setup 을 한 번 돌렸거나 rw 마운트된 컨테이너(root)가 파일을 rewrite 하면
# 호스트 파일/디렉터리 소유권이 root(혹은 컨테이너 uid)로 넘어가, 이후 grep/read 가
# Permission denied 로 죽으며 엉뚱한 에러("marker 누락" 등)로 오진된다. 먼저 명시 차단.
#   $1 = 대상 파일, $2 = need_read(기본 1; 기존 내용을 읽어야 하면 1, 통째 재생성이면 0)
# mv 는 부모 디렉터리 쓰기 권한이 필요 → 디렉터리 -w 는 항상 확인.
assert_regen_writable() {
  local f="$1" need_read="${2:-1}" d owner me; d="$(dirname "$f")"; me="$(id -un)"
  if [[ "$need_read" == 1 && -e "$f" && ! -r "$f" ]]; then
    owner="$(stat -c '%U:%G' "$f" 2>/dev/null || echo '?')"
    err "$f 읽기 불가 (소유: $owner, 현재 유저: $me)."
    err "  sudo 로 스크립트를 돌리면 root 소유가 됨 → 복구: sudo chown $(id -u):$(id -g) \"$f\"   (이후 sudo 없이 실행)"
    return 1
  fi
  if [[ ! -w "$d" ]]; then
    owner="$(stat -c '%U:%G' "$d" 2>/dev/null || echo '?')"
    err "$d 디렉터리 쓰기 불가 (소유: $owner, 현재 유저: $me) → $f 갱신 불가."
    err "  복구: sudo chown $(id -u):$(id -g) \"$d\""
    return 1
  fi
  return 0
}

# NODES_* csv (ssh 타겟) 로부터 *_URLS csv 를 자동 derive 한다. Whisper 는 scheduler 가
# 다루지 않아 별도 운영자 입력 대신 NODES_WHISPER 에서 URL 을 유추한다.
# vLLM (VLLM_*_URL) 과 ComfyUI (COMFYUI_URLS) 는 scheduler apply 의 applier 가
# placement 결정 따라 갱신하므로 여기선 건드리지 않는다 — 호출 위치는 scheduler
# apply *이전* setup 단계, 그래야 scheduler 가 우선권 (NODES_* 가 단순 default).
derive_urls_from_nodes() {
  local ssh_target host
  local -A defaults=(
    [NODES_WHISPER]="WHISPER_URLS:9000"
  )
  local nodes_var url_var port spec urls=()
  for nodes_var in "${!defaults[@]}"; do
    local csv; csv="$(env_get "$nodes_var")"
    [[ -z "$csv" ]] && continue
    spec="${defaults[$nodes_var]}"
    url_var="${spec%%:*}"; port="${spec##*:}"
    urls=()
    while IFS= read -r ssh_target; do
      [[ -z "$ssh_target" ]] && continue
      host="${ssh_target##*@}"
      urls+=("http://${host}:${port}")
    done < <(csv_split "$csv")
    if (( ${#urls[@]} > 0 )); then
      local new_val; new_val="$(IFS=,; echo "${urls[*]}")"
      local cur_val; cur_val="$(env_get "$url_var")"
      if [[ "$new_val" != "$cur_val" ]]; then
        env_set "$url_var" "$new_val"
        echo "  [derive] ${url_var}=${new_val}"
      fi
    fi
  done
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

detect_gpu_class() {
  has_nvidia_gpu || { echo none; return; }
  has_gb10 && { echo gb10; return; }
  case "$(get_gpu_name)" in
    *"RTX PRO 6000 Blackwell"*|*"RTX 6000 Pro Blackwell"*|*"RTX 6000 PRO Blackwell"*) echo pro6000 ;;
    *"RTX PRO 5000 Blackwell"*|*"RTX 5000 Pro Blackwell"*|*"RTX 5000 PRO Blackwell"*) echo pro5000 ;;
    *"RTX 5090"*) echo rtx5090 ;;
    *"RTX 4090"*) echo rtx4090 ;;
    *)            echo nvidia-other ;;
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

# Commercial 카탈로그 — OpenRouter 경유. OR 키 없으면 미등록한다.
# provider 그룹별 배열 → gen-litellm/gen-librechat 가 동일 카탈로그에서 생성.
OPENAI_MODELS=(gpt-5.5 gpt-5 gpt-5-mini gpt-5-nano)
ANTHROPIC_MODELS=(claude-opus-4.8 claude-opus-4.7 claude-opus-4.6 claude-sonnet-4.6 claude-haiku-4.5)
GOOGLE_MODELS=(gemini-3.1-pro-preview gemini-2.5-pro gemini-2.5-flash gemma-4-31b-it)
# 추가 OR provider (deepseek / xAI / perplexity / meta / qwen).
DEEPSEEK_MODELS=(deepseek-v4-pro)
XAI_MODELS=(grok-4.3)
PERPLEXITY_MODELS=(sonar)
META_MODELS=(llama-4-maverick)
QWEN_MODELS=(qwen3.5-397b-a17b)

# bge-m3 미보유 + OR 키 환경에서 RAG 임베딩 폴백.
OPENAI_EMBED_CATALOG=(text-embedding-3-small)

# vLLM 카탈로그 (alias → HF repo). 추가 시 download-vllm-models.sh::recommended_vllm_set 와 동기한다.
# gemma-4-26b 별칭은 노드 하드웨어에 따라 다른 가중치를 가리킨다: NVFP4 지원 카드는 NVFP4,
# RTX4090 은 FP4 미지원이라 AWQ-int4 변종(gemma-4-26b-awq).
declare -A VLLM_MODELS=(
  [gemma-4-26b]="nvidia/gemma-4-26b-A4B-it-NVFP4"
  [gemma-4-26b-awq]="cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit"
  [qwen3.5-122b-a10b]="nvidia/Qwen3.5-122B-A10B-NVFP4"
  [qwen3-coder-next]="Qwen/Qwen3-Coder-Next-FP8"
  [bge-m3]="BAAI/bge-m3"
)
: "${VLLM_MODELS_ROOT:=/var/lib/vllm/models}"

# arch 별 vLLM 이미지. GB10(arm64)은 aarch64 빌드, 디스크리트 카드(amd64)는 표준 amd64 빌드.
# 신규 아키텍처(gemma4 / qwen3.5 MoE) 지원을 위해 nightly 빌드를 사용한다.
vllm_default_image() {
  case "$(detect_arch)" in
    arm64) echo "vllm/vllm-openai:nightly-aarch64" ;;
    amd64) echo "vllm/vllm-openai:cu129-nightly" ;;
    *)     echo "" ;;
  esac
}

# LiteLLM spend 추적용 단가 (USD / 1M tokens). 로컬 모델은 무료(0) — GPU 자체호스팅이라
# 토큰 과금 0. OR 폴백(노드 다운/과부하) 시에만 emit_or_fallback 의 OR twin 단가로 과금된다
# (LiteLLM 은 실제 서빙한 deployment 의 단가로 과금하므로 로컬 0 ↔ 폴백 OR 가 자동 분리됨).
declare -A MODEL_PRICE_IN_PM=(
  [gpt-5.5]=5      [gpt-5]=2.5    [gpt-5-mini]=0.25    [gpt-5-nano]=0.05
  [claude-opus-4.8]=5    [claude-opus-4.7]=5    [claude-opus-4.6]=5    [claude-sonnet-4.6]=3    [claude-haiku-4.5]=1
  [gemini-3.1-pro-preview]=5    [gemini-2.5-pro]=2.5    [gemini-2.5-flash]=0.30
  [gemma-4-26b]=0    [qwen3.5:122b]=0
  [qwen3-coder-next]=0
  [bge-m3]=0
  [text-embedding-3-small]=0.02
  # 추가 OR provider — OR pricing_skus 기준.
  [gemma-4-31b-it]=0.12    [qwen3.5-397b-a17b]=0.39    [deepseek-v4-pro]=0.43
  [grok-4.3]=1.25    [sonar]=1.00    [llama-4-maverick]=0.15
)
declare -A MODEL_PRICE_OUT_PM=(
  [gpt-5.5]=30    [gpt-5]=15    [gpt-5-mini]=2    [gpt-5-nano]=0.40
  [claude-opus-4.8]=25    [claude-opus-4.7]=25    [claude-opus-4.6]=25    [claude-sonnet-4.6]=15    [claude-haiku-4.5]=5
  [gemini-3.1-pro-preview]=15    [gemini-2.5-pro]=10    [gemini-2.5-flash]=2.5
  [gemma-4-26b]=0    [qwen3.5:122b]=0
  [qwen3-coder-next]=0
  # 추가 OR provider — OR pricing_skus 기준.
  [gemma-4-31b-it]=0.36    [qwen3.5-397b-a17b]=2.34    [deepseek-v4-pro]=0.87
  [grok-4.3]=2.50    [sonar]=1.00    [llama-4-maverick]=0.60
)

per_token_cost() { awk -v v="$1" 'BEGIN { printf "%.10f", v/1000000 }'; }

has_openrouter() { [[ -n "$(env_get OPENROUTER_API_KEY)" ]]; }

# 출력: "<URL>\t<served-model-name>" per line. 호출측이 URL csv 를 인자로 넘긴다
# (모델별 .env 변수가 별도 — VLLM_GEMMA26_URL / VLLM_QWEN122B_URL / VLLM_CODERNEXT_URL / VLLM_BGE_M3_URL).
__vllm_normalize_url() {
  local u="$1"
  u="$(echo "$u" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s|/$||')"
  echo "$u"
}
__vllm_node_models() {
  local raw="$1" url probe
  url="$(__vllm_normalize_url "$raw")"
  probe="${url//host.docker.internal/localhost}"
  curl -sf --max-time 5 "${probe}/v1/models" 2>/dev/null | jq -r '.data[]?.id' 2>/dev/null || true
}
# 같은 URL 이 csv 에 중복으로 들어가도 (사용자 typo 또는 외부 도구의 dedup 없는 append)
# 한 번만 probe·emit 한다 — downstream AUTOGEN 블록에서 동일 deployment 가 중복 등록되어
# LiteLLM 재시도가 같은 죽은 host 로 증폭되는 사고를 방지한다.
vllm_union_node_models() {
  local urls_csv="$1"
  [[ -n "$urls_csv" ]] || return 0
  local IFS=, u nu tmp
  declare -A __seen_url=()
  for u in $urls_csv; do
    nu="$(__vllm_normalize_url "$u")"
    [[ -n "$nu" ]] || continue
    [[ -n "${__seen_url[$nu]:-}" ]] && continue
    __seen_url[$nu]=1
    tmp="$(__vllm_node_models "$u")"
    if [[ -z "$tmp" ]]; then
      warn "vllm unreachable: $nu" >&2
      continue
    fi
    while IFS= read -r m; do
      [[ -n "$m" ]] && printf '%s\t%s\n' "$nu" "$m"
    done <<<"$tmp"
  done
}

# URL 1개의 상태 — readiness wait 의 단위 판정.
#   0 = ready   (/v1/models 200 + 모델 1개 이상)
#   1 = loading (TCP 응답하지만 HTTP 미준비 — 모델 로딩 / 503 / 빈 모델 리스트)
#   2 = dead    (TCP 거부 / DNS 실패 — 컨테이너 미기동)
# --connect-timeout 으로 TCP 와 HTTP 시간을 분리해 curl exit/HTTP code 로 구분한다.
__vllm_one_state() {
  local raw="$1" probe code body
  probe="$(__vllm_normalize_url "$raw")"
  probe="${probe//host.docker.internal/localhost}"
  code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 8 \
            "${probe}/v1/models" 2>/dev/null || true)"
  case "$code" in
    200)
      body="$(curl -sf --max-time 5 "${probe}/v1/models" 2>/dev/null \
              | jq -r '.data[]?.id' 2>/dev/null)"
      [[ -n "$body" ]] && return 0
      return 1
      ;;
    "" | 000) return 2 ;;   # curl 실패 (TCP / DNS) — dead
    *)        return 1 ;;   # 503/504/5xx 등 — loading
  esac
}

# 단일 URL 의 vLLM self-reported max_model_len 조회. retry 3회 × 2초.
# stdout: integer. 실패 시 비어있고 rc!=0 — 호출자가 폴백 결정.
vllm_discover_max_len() {
  local raw="$1" probe len i
  probe="$(__vllm_normalize_url "$raw")"
  probe="${probe//host.docker.internal/localhost}"
  for i in 1 2 3; do
    len="$(curl -sf --max-time 5 "${probe}/v1/models" 2>/dev/null \
            | jq -r '.data[0].max_model_len // empty' 2>/dev/null)"
    if [[ -n "$len" && "$len" =~ ^[0-9]+$ ]]; then
      echo "$len"; return 0
    fi
    sleep 2
  done
  return 1
}

# CSV 의 모든 URL 이 ready 될 때까지 wait. 매 interval 마다 상태 재평가.
#   - 한 URL 의 컨테이너가 exited/없음으로 dead_thresh 회 연속 → rc=2 (미기동 fast-fail)
#   - 어떤 URL 이 timeout 안에 ready 못함 → rc=3
#   - 모두 ready → rc=0
# vLLM 의 startup 순서가 "모델 로딩 → 그 후에야 uvicorn listen" 이라 cold-start 시 처음
# 수 분간 TCP 거부가 정상. 따라서 TCP refused 면 시간(grace)이 아니라 __vllm_container_state
# 로 실제 컨테이너 상태를 본다 — Up 이면(로딩 중) 시간 무관 대기, exited/없음이면 dead.
# 큰 모델(GB10 122b cold-start ~6-7분)도 false-dead 없이 통과, 진짜 크래시만 fast-fail.
# 진행 로그는 stderr 로 (caller 가 캡처해도 동작 동일).
# 사용: vllm_wait_until_ready "$URL_CSV" "label" [timeout=600] [interval=10] [dead_thresh=3]
# url 의 host → ssh 타겟. NODES_VLLM csv 에서 user@host 매칭, 없으면 host 그대로.
__vllm_ssh_target() {
  local host="$1" csv entry
  csv="$(env_get NODES_VLLM 2>/dev/null)"
  local IFS=,
  for entry in $csv; do
    entry="${entry// /}"
    [[ -n "$entry" && "${entry#*@}" == "$host" ]] && { echo "$entry"; return 0; }
  done
  echo "$host"
}

# 컨테이너 생존 신호 (시간 무관). url 의 port 를 publish 하는 컨테이너가 Up 인가.
#   0 = alive (Up, "health: starting" 포함 = 로딩 중)
#   1 = dead  (해당 포트 컨테이너 없음 / exited / restart 루프)
#   2 = unknown (ssh/docker 조회 실패 → 판단 보류, deadline 까지 대기)
# host 는 URL 에서 추출하므로 is_local_host 대신 여기서 직접 localhost/loopback/내 IP 를
# 비교해 ssh 우회 여부를 정한다.
__vllm_container_state() {
  local raw="$1" url hostport host port target out h islocal=0 ip
  url="$(__vllm_normalize_url "$raw")"
  hostport="${url#http://}"; hostport="${hostport%%/*}"
  host="${hostport%%:*}"; port="${hostport##*:}"
  host="${host//host.docker.internal/localhost}"
  h="${host#*@}"
  [[ -z "$h" || "$h" == localhost || "$h" == 127.0.0.1 || "$h" == ::1 ]] && islocal=1
  for ip in $(hostname -I 2>/dev/null); do [[ "$h" == "$ip" ]] && islocal=1; done
  if (( islocal )); then
    out="$(sudo -n docker ps --filter "publish=${port}" --format '{{.Status}}' 2>/dev/null)" || return 2
  else
    target="$(__vllm_ssh_target "$host")"
    out="$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o LogLevel=ERROR "$target" \
            "sudo -n docker ps --filter publish=${port} --format '{{.Status}}'" 2>/dev/null)" || return 2
  fi
  [[ -z "$out" ]] && return 1
  [[ "$out" == Up* ]] && return 0
  return 1
}

vllm_wait_until_ready() {
  local urls_csv="$1" label="$2"
  local timeout="${3:-600}" interval="${4:-10}" dead_thresh="${5:-3}"
  [[ -n "$urls_csv" ]] || return 0
  local deadline; deadline=$(( $(date +%s) + timeout ))
  local IFS=, u nu state cstate
  declare -A __seen=()
  declare -A __dead=()
  for u in $urls_csv; do
    nu="$(__vllm_normalize_url "$u")"
    [[ -n "$nu" ]] || continue
    [[ -n "${__seen[$nu]:-}" ]] && continue
    __seen[$nu]=1
    while :; do
      state=0; __vllm_one_state "$u" || state=$?
      if (( state == 0 )); then
        echo "  [ready] ${label}: ${nu}" >&2
        break
      fi
      if (( state == 2 )); then
        # TCP refused. 시간이 아니라 "컨테이너가 살아있나"로 판정한다 — cold start 가
        # 아무리 느려도(vLLM 은 모델 로드+cudagraph 후에야 포트를 엶) 컨테이너가 Up 이면
        # 로딩으로 보고 계속 대기, exited/없음이면 dead_thresh 회 연속 확인 후 fail-fast.
        cstate=0; __vllm_container_state "$u" || cstate=$?
        if (( cstate == 0 )); then
          __dead[$nu]=0
          echo "  [load]  ${label}: ${nu} 컨테이너 Up — 모델 로딩 중 (포트 미개통)" >&2
        elif (( cstate == 1 )); then
          __dead[$nu]=$(( ${__dead[$nu]:-0} + 1 ))
          if (( __dead[$nu] >= dead_thresh )); then
            echo "  [fail]  ${label}: ${nu} 컨테이너 미기동/종료 ${dead_thresh}회 연속 — vLLM down (GPU 노드 docker logs 확인)" >&2
            return 2
          fi
          echo "  [wait]  ${label}: ${nu} 컨테이너 없음/종료 (${__dead[$nu]}/${dead_thresh})" >&2
        else
          # cstate 2 = 컨테이너 상태 조회 실패(ssh/docker) — 판단 보류, deadline 까지 대기.
          echo "  [load]  ${label}: ${nu} 컨테이너 상태 조회 불가 — 대기 지속" >&2
        fi
      else
        __dead[$nu]=0
        echo "  [load]  ${label}: ${nu} 모델 로딩 중…" >&2
      fi
      if (( $(date +%s) >= deadline )); then
        echo "  [fail]  ${label}: ${nu} ${timeout}s 안에 ready 못함" >&2
        return 3
      fi
      sleep "$interval"
    done
  done
  return 0
}

# 단일 ComfyUI 노드의 보유 alias 를 반환한다. 같은 alias 의 quant 변형 (FP16/GGUF) 중
# 하나라도 있으면 alias 1회만 emit 한다. 변형 선택은 shim 의 COMFYUI_ALIAS_VARIANTS 가 결정한다.
__comfyui_node_models() {
  local raw="$1" url info
  url="$(echo "$raw" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  url="${url//host.docker.internal/localhost}"
  info="$(curl -sf --max-time 5 "${url}/object_info" 2>/dev/null)" || return 0
  local unets
  unets="$(echo "$info" | jq -r '
    (.UNETLoader.input.required.unet_name[0] // []) + (.UnetLoaderGGUF.input.required.unet_name[0] // []) | .[]?' 2>/dev/null)"
  grep -qxFf <(printf '%s\n' 'flux1-schnell.safetensors' 'flux1-schnell-Q8_0.gguf') <<<"$unets" && echo flux-schnell
  grep -qxFf <(printf '%s\n' 'flux1-dev.safetensors'     'flux1-dev-Q8_0.gguf')     <<<"$unets" && echo flux-dev
}

# 출력: "<URL>\t<alias>" per line. unreachable 노드는 stderr 에 [warn] 후 skip 한다.
comfyui_union_node_models() {
  local urls; urls="$(env_get COMFYUI_URLS)"
  [[ -n "$urls" ]] || return 0
  local IFS=, u nu tmp
  for u in $urls; do
    nu="$(echo "$u" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s|/$||')"
    [[ -n "$nu" ]] || continue
    tmp="$(__comfyui_node_models "$u")"
    if [[ -z "$tmp" ]]; then
      warn "comfyui unreachable: $nu" >&2
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

# Super Agent 활성 조건 — single-stage 구조라 별도 router 는 없다. chat 두뇌
# (gemma-4-26b) 가 vLLM 로 잡히면 유효 (shim 이 단일 stage + post-processing/artifact
# 디렉티브를 챗 경로에 직접 적용).
# gemma 챗 두뇌 가용 = 로컬 vLLM(VLLM_GEMMA26_URL) 또는 OR 키(gen-litellm 가 local/gemma
# -4-26b 를 OR 동일모델로 직결). Super Agent + 두뇌형 functional 에이전트의 등장 조건.
super_agent_eligible() {
  [[ -n "$(env_get VLLM_GEMMA26_URL 2>/dev/null || true)" ]] || has_openrouter
}

# whisper(STT) 백엔드 가용 — Note Taker / youtube 무자막 전사 전제. GPU 전용(OR 폴백 없음).
whisper_eligible() {
  [[ -n "$(env_get WHISPER_URLS 2>/dev/null || true)" ]]
}

# 이미지/비디오 생성 백엔드 가용 = 로컬 ComfyUI(COMFYUI_URLS) 또는 OR 키(외부 모델).
# Image/Video Studio 등장 조건 (두뇌 super_agent_eligible 과 함께).
image_gen_eligible() {
  [[ -n "$(env_get COMFYUI_URLS 2>/dev/null || true)" ]] || has_openrouter
}

# LiteLLM team allowlist — gen-litellm-config 의 emit 결과와 동일한 셋을 반환해야 한다.
litellm_chat_models_csv() {
  local vllm_gemma_url; vllm_gemma_url="$(env_get VLLM_GEMMA26_URL 2>/dev/null || true)"
  local vllm_research_url; vllm_research_url="$(env_get VLLM_QWEN122B_URL 2>/dev/null || true)"
  local vllm_coder_url; vllm_coder_url="$(env_get VLLM_CODERNEXT_URL 2>/dev/null || true)"
  local vllm_embed_url; vllm_embed_url="$(env_get VLLM_BGE_M3_URL 2>/dev/null || true)"
  local out=() m
  if has_openrouter; then
    for m in "${OPENAI_MODELS[@]}";     do out+=("openai/$m");     done
    for m in "${ANTHROPIC_MODELS[@]}";  do out+=("anthropic/$m");  done
    for m in "${GOOGLE_MODELS[@]}";     do out+=("google/$m");     done
    for m in "${DEEPSEEK_MODELS[@]}";   do out+=("deepseek/$m");   done
    for m in "${XAI_MODELS[@]}";        do out+=("x-ai/$m");       done
    for m in "${PERPLEXITY_MODELS[@]}"; do out+=("perplexity/$m"); done
    for m in "${META_MODELS[@]}";       do out+=("meta/$m");       done
    for m in "${QWEN_MODELS[@]}";       do out+=("qwen/$m");       done
  fi
  # 챗 두뇌 (gemma-4-26b), Deep Research (122b) 는 로컬 vLLM 또는 OR 직결(emit_brain)로
  # emit 되므로 allowlist 도 같은 조건(URL 또는 OR 키)이어야 team gate 통과 — 안 그러면
  # OR 전용 배포에서 에이전트는 떠도 직접호출이 model-not-allowed 로 막힌다. 코딩 보조
  # (coder-next) 는 OR-직결 없이 vLLM 전용이라 URL 게이팅 유지.
  { [[ -n "$vllm_gemma_url"    ]] || has_openrouter; } && out+=("local/gemma-4-26b")
  { [[ -n "$vllm_research_url" ]] || has_openrouter; } && out+=("local/qwen3.5:122b")
  # coder-next 의 served-model-name 은 'qwen3-coder-next' — emit_vllm_chat 의
  # 'local/qwen3-coder-next' 와 일치시킨다.
  [[ -n "$vllm_coder_url"    ]] && out+=("local/qwen3-coder-next")
  [[ -n "$vllm_embed_url"    ]] && out+=("bge-m3")
  if has_openrouter; then
    for m in "${OPENAI_EMBED_CATALOG[@]}"; do out+=("$m"); done
  fi
  super_agent_eligible && out+=("local/auto-route")
  local IFS=,
  echo "${out[*]:-}"
}

LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-$(env_get LITELLM_MASTER_KEY)}"
# LITELLM_URL 우선순위: shell env > .env > localhost:8000. .env 의 값은 docker 내부에서
# 쓰는 호스트명(host.docker.internal 또는 service name 'litellm')일 수 있어, host-side
# 에서 호출할 때는 호스트 publish 포트로 직접 접근하도록 정규화한다.
LITELLM_URL="${LITELLM_URL:-$(env_get LITELLM_URL)}"
LITELLM_URL="${LITELLM_URL:-http://localhost:8000}"
LITELLM_URL="${LITELLM_URL//host.docker.internal/localhost}"
LITELLM_URL="${LITELLM_URL//\/\/litellm:/\/\/localhost:}"
DATA_DIR="${__PROJECT_DIR}/data/ledger"

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

# ───────────────────────── multi-node dispatch ─────────────────────────
# .env 의 NODE_* / NODES_* 가 SoT. setup.sh / manage.sh 가 ssh 우회 결정에 이걸 쓴다.
# 단일노드 = 모든 NODE_* 를 같은 호스트로 (또는 비워두면 = 로컬). is_local_host 가
# localhost / 내 IP / 내 hostname / DNS resolve 까지 비교해 ssh 를 우회한다.

# 원격 노드의 레포 경로. 로그인 사용자의 $HOME 하위. env 로 override.
KLOUDCHAT_REMOTE_DIR="${KLOUDCHAT_REMOTE_DIR:-KloudChat}"

# host 가 "이 노드" 인지 판정. user@ 접두는 strip. localhost/loopback, $HOSTNAME 또는
# short hostname, hostname -I 의 IPv4, target hostname 의 DNS resolve 결과까지 매칭.
is_local_host() {
  local target="${1#*@}"
  [[ -z "$target" ]] && return 1
  case "$target" in localhost|127.0.0.1|::1) return 0 ;; esac
  local self_short="${HOSTNAME%%.*}"
  [[ "$target" == "$HOSTNAME" || "$target" == "$self_short" ]] && return 0
  local local_ips ip
  local_ips="$(hostname -I 2>/dev/null || true)"
  for ip in $local_ips; do
    [[ "$target" == "$ip" ]] && return 0
  done
  # target 이 hostname 인 경우 DNS resolve 후 IP 매칭. getent 가 없거나 실패해도 무해.
  local target_ip
  target_ip="$(getent hosts "$target" 2>/dev/null | awk '{print $1; exit}')"
  if [[ -n "$target_ip" ]]; then
    for ip in $local_ips; do
      [[ "$target_ip" == "$ip" ]] && return 0
    done
  fi
  return 1
}

# is_local_node NODE_LIBRECHAT — env var name 받아서 host 추출 후 is_local_host. 비어 있으면 true
# (= "비어있음 = 로컬" 규약).
is_local_node() {
  local var="$1" host
  host="$(env_get "$var")"
  [[ -z "$host" ]] && return 0
  is_local_host "$host"
}

# csv → newline-delimited (공백/따옴표 trim).
csv_split() {
  local IFS=, s
  for s in $1; do
    s="$(echo "$s" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//;s/^"//;s/"$//')"
    [[ -n "$s" ]] && echo "$s"
  done
}

# csv "user@host,user@host" → "id=user@host,id=user@host" (scheduler --hosts 포맷).
# id 는 IPv4 마지막 옥텟 (예: 1.2.3.4 → 4), IP 가 아니면 hostname 의 첫 '.' 이전 부분 (닷이 없는 짧은 호스트명은 그대로).
nodes_to_hosts() {
  local csv="$1" entry host id out=""
  while IFS= read -r entry; do
    host="${entry#*@}"
    if [[ "$host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.([0-9]+)$ ]]; then
      id="${BASH_REMATCH[1]}"
    else
      id="${host%%.*}"
    fi
    out+="${out:+,}${id}=${entry}"
  done < <(csv_split "$csv")
  echo "$out"
}

# 레포를 원격으로 push. runtime data (DB 볼륨 / 캐시 / 로그) 는 제외.
rsync_push() {
  local host="$1"
  echo "  → rsync to ${host}:${KLOUDCHAT_REMOTE_DIR}/"
  rsync -az --delete \
    --exclude='.git/' \
    --exclude='data/' \
    --exclude='whisper/.cache/' \
    --exclude='searxng/settings.yml' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --rsync-path="mkdir -p '${KLOUDCHAT_REMOTE_DIR}' && rsync" \
    "${__PROJECT_DIR}/" "${host}:${KLOUDCHAT_REMOTE_DIR}/"
}

# 원격의 단일 파일 1개를 로컬로 pull (setup.sh litellm 의 SERVICE_KEY .env 회수용).
rsync_pull_file() {
  local host="$1" path="$2"
  echo "  → rsync ${host}:${KLOUDCHAT_REMOTE_DIR}/${path} → ${path}"
  rsync -az "${host}:${KLOUDCHAT_REMOTE_DIR}/${path}" "${__PROJECT_DIR}/${path}"
}

# 로컬 단일 파일 1개를 원격에 push (service key 회전 후 .env 동기화 등).
rsync_push_file() {
  local host="$1" path="$2"
  echo "  → rsync ${path} → ${host}:${KLOUDCHAT_REMOTE_DIR}/${path}"
  rsync -az "${__PROJECT_DIR}/${path}" "${host}:${KLOUDCHAT_REMOTE_DIR}/${path}"
}

# ssh + cd repo + 명령. -n 으로 호출자 stdin 보호 — while-read 안에서 ssh 호출 시
# 첫 노드 후 EOF 보는 버그 회피.
ssh_run() {
  local host="$1"; shift
  ssh -n -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 "$host" \
    "set -e; cd '${KLOUDCHAT_REMOTE_DIR}' && $*"
}

# docker_on_node NODE_LIBRECHAT exec -i chat-mongodb mongosh ...
# 로컬이면 docker 직접, 아니면 ssh 우회. stdin 은 자동 전달 (원격 케이스 -n 없음).
# 각 인자는 printf %q 로 안전 quote 후 remote shell 에 전달.
docker_on_node() {
  local var="$1"; shift
  if is_local_node "$var"; then
    docker "$@"
    return
  fi
  local host; host="$(env_get "$var")"
  local q=() a
  for a in "$@"; do q+=("$(printf '%q' "$a")"); done
  ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 "$host" \
    "docker ${q[*]}"
}

# LIBRECHAT_URL 우선순위: shell env > .env LIBRECHAT_URL > NODE_LIBRECHAT 호스트 + :8080 > localhost:8080.
# manage.sh 의 register_litellm_key_for_librechat_user 가 이걸 쓴다.
__compute_librechat_url() {
  local url
  url="${LIBRECHAT_URL:-$(env_get LIBRECHAT_URL)}"
  if [[ -z "$url" ]]; then
    local h; h="$(env_get NODE_LIBRECHAT)"
    if [[ -n "$h" ]] && ! is_local_host "$h"; then
      url="http://${h#*@}:8080"
    else
      url="http://localhost:8080"
    fi
  fi
  echo "$url"
}
LIBRECHAT_URL="$(__compute_librechat_url)"
