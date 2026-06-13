#!/usr/bin/env bash
# Usage: download-vllm-models.sh [alias|all|recommended] [...]
#
# 인자 없이 실행 시 GPU class 자동 감지 → 권장 셋 다운로드.
#
# Aliases (lib.sh::VLLM_MODELS):
#   gemma-4-26b        nvidia/gemma-4-26b-A4B-it-NVFP4       ~16 GB (챗 두뇌, NVFP4 MoE A4B)
#   gemma-4-26b-awq    cyankiwi/gemma-4-26B-A4B-it-AWQ-4bit  ~13 GB (RTX4090 — FP4 미지원)
#   qwen3.5-122b-a10b  nvidia/Qwen3.5-122B-A10B-NVFP4        ~78 GB (Deep Research, NVFP4 MoE A10B)
#   bge-m3             BAAI/bge-m3                           ~2 GB (embed)
#
# 명시 호출만 (recommended / all 셋 제외 — 노드 격리 정책):
#   qwen3-coder-next   Qwen/Qwen3-Coder-Next-FP8            ~80 GB (코딩 클라이언트 전용)
#
# Special:
#   recommended       GPU class 자동 감지 권장 셋 (인자 없을 때와 동일)
#   all               coder 제외 전부
#
# Env:
#   HF_TOKEN          .env 자동 로드 (gated 모델용)
#   VLLM_MODELS_ROOT  저장 경로 (default /var/lib/vllm/models)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

# GPU 클래스별 권장 셋. gemma-4-26b 는 어느 단일 GPU든 기본 챗 두뇌. RTX4090 은 FP4
# 미지원이라 AWQ-int4 변종. Deep Research(122b)는 VRAM 큰 노드(PRO6000)만 명시 호출.
recommended_vllm_set() {
  case "$(detect_gpu_class)" in
    gb10|pro6000|pro5000|rtx5090) echo "gemma-4-26b bge-m3" ;;
    rtx4090)                                                  echo "gemma-4-26b-awq bge-m3" ;;
    *) echo "" ;;
  esac
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  grep -E '^# ' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

require_supported_platform
command -v uv &>/dev/null || { err "uv 없음. curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }

HF_TOKEN="$(env_get HF_TOKEN)"
[[ -n "$HF_TOKEN" ]] || warn "HF_TOKEN 미설정 — gated 모델 다운 시 실패."

mkdir -p "$VLLM_MODELS_ROOT"

# 인자 없으면 GPU class 자동 분기.
if [[ $# -eq 0 ]]; then
  r="$(recommended_vllm_set)"
  if [[ -z "$r" ]]; then
    err "GPU class ($(detect_gpu_class)) 권장 셋 없음 — alias 명시 또는 'all'"
    exit 1
  fi
  info "auto-detect $(detect_gpu_class): $r"
  set -- $r
fi

TARGETS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    recommended)
      r=$(recommended_vllm_set)
      [[ -n "$r" ]] || { err "GPU class ($(detect_gpu_class)) 권장 셋 없음"; exit 1; }
      info "recommended for $(detect_gpu_class): $r"
      for a in $r; do TARGETS+=("$a"); done
      ;;
    all)
      # coder 는 노드 격리 정책 — all 셋 제외. 받으려면 명시 호출.
      for a in "${!VLLM_MODELS[@]}"; do
        [[ "$a" == "qwen3-coder-next" ]] && continue
        TARGETS+=("$a")
      done ;;
    *)
      [[ -n "${VLLM_MODELS[$1]:-}" ]] && TARGETS+=("$1") || { err "Unknown alias: $1"; exit 1; }
      ;;
  esac
  shift
done

pull_one() {
  local alias="$1" repo="${VLLM_MODELS[$1]}" dest="$VLLM_MODELS_ROOT/$1"
  hdr "$alias → $repo"
  echo "  dest: $dest"
  if [[ -f "$dest/config.json" ]] && compgen -G "$dest/*.safetensors" >/dev/null; then
    ok "이미 받음 ($(du -sh "$dest" | cut -f1)) — 재다운 원하면 디렉토리 삭제 후 재실행"
    return 0
  fi
  # hf_xet 끔: legacy(non-xet) repo 에서 deadlock — TLS 다수 연결 + io 0 hang. 표준 chunked HTTP 가 안정.
  HF_TOKEN="$HF_TOKEN" \
  HF_HUB_DISABLE_XET=1 \
  HF_HUB_DOWNLOAD_TIMEOUT=180 \
  uv tool run --from huggingface_hub hf download \
    "$repo" --local-dir "$dest" --max-workers 4
  ok "received $(du -sh "$dest" | cut -f1)"
}

for a in "${TARGETS[@]}"; do pull_one "$a"; done

hdr "완료"
du -sh "$VLLM_MODELS_ROOT"/* 2>/dev/null | sort -k2 || echo "  (no models yet)"
