#!/bin/bash
# SD.Next 모델 다운로드 스크립트
# SD.Next는 .safetensors 파일을 ./sdnext/models/Stable-diffusion/ 에서 자동 감지합니다.
set -euo pipefail

MODELS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/sdnext/models"
SD_DIR="${MODELS_DIR}/Stable-diffusion"
VAE_DIR="${MODELS_DIR}/VAE"

mkdir -p "${SD_DIR}" "${VAE_DIR}"

# ---- 다운로드 함수 ----
download() {
  local url="$1"
  local dest="$2"
  local name
  name="$(basename "$dest")"

  if [[ -f "$dest" ]]; then
    echo "[건너뜀] ${name} (이미 존재)"
    return 0
  fi

  echo "[다운로드] ${name}"
  echo "  출처: ${url}"
  echo "  저장: ${dest}"

  if command -v wget &>/dev/null; then
    wget --show-progress -q -O "${dest}.tmp" "$url" && mv "${dest}.tmp" "$dest"
  elif command -v curl &>/dev/null; then
    curl -L --progress-bar -o "${dest}.tmp" "$url" && mv "${dest}.tmp" "$dest"
  else
    echo "오류: wget 또는 curl이 필요합니다." >&2
    exit 1
  fi

  echo "  완료: $(du -sh "$dest" | cut -f1)"
}

# ---- 사용법 ----
usage() {
  cat <<EOF
사용법: $(basename "$0") [모델...]

모델 선택:
  sdxl          SDXL 1.0 Base (~6.9GB) — 범용 [기본]
  sdxl-turbo    SDXL-Turbo (~6.9GB)    — 고속 생성 (1~4 step)
  vae           SDXL VAE FP16 Fix (~160MB) — SDXL 색감 개선 [권장]
  flux-schnell  Flux.1-schnell Q8 GGUF (~24GB) — 고품질, Apache 2.0
                (HuggingFace 토큰 필요: HF_TOKEN 환경변수 설정)
  all           sdxl + sdxl-turbo + vae

예시:
  $(basename "$0") sdxl vae
  $(basename "$0") all
  HF_TOKEN=hf_xxx $(basename "$0") flux-schnell
EOF
  exit 0
}

[[ $# -eq 0 ]] && set -- sdxl vae   # 인자 없으면 기본값

for arg in "$@"; do
  case "$arg" in
    sdxl)
      download \
        "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors" \
        "${SD_DIR}/sd_xl_base_1.0.safetensors"
      ;;
    sdxl-turbo)
      download \
        "https://huggingface.co/stabilityai/sdxl-turbo/resolve/main/sd_xl_turbo_1.0_fp16.safetensors" \
        "${SD_DIR}/sd_xl_turbo_1.0_fp16.safetensors"
      ;;
    vae)
      download \
        "https://huggingface.co/madebyollin/sdxl-vae-fp16-fix/resolve/main/sdxl_vae.safetensors" \
        "${VAE_DIR}/sdxl_vae_fp16_fix.safetensors"
      ;;
    flux-schnell)
      if [[ -z "${HF_TOKEN:-}" ]]; then
        echo "오류: Flux.1-schnell은 HuggingFace 토큰이 필요합니다." >&2
        echo "  HF_TOKEN=hf_xxx ./$(basename "$0") flux-schnell" >&2
        exit 1
      fi
      download \
        "https://huggingface.co/city96/FLUX.1-schnell-gguf/resolve/main/flux1-schnell-Q8_0.gguf" \
        "${SD_DIR}/flux1-schnell-Q8_0.gguf"
      download \
        "https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors" \
        "${VAE_DIR}/flux_ae.safetensors"
      ;;
    all)
      "$0" sdxl sdxl-turbo vae
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "알 수 없는 모델: $arg" >&2
      usage
      ;;
  esac
done

echo
echo "=== 다운로드 완료 ==="
echo "SD 모델: $(ls "${SD_DIR}"/*.{safetensors,gguf} 2>/dev/null | wc -l)개"
echo "VAE:     $(ls "${VAE_DIR}"/*.safetensors 2>/dev/null | wc -l)개"
echo
echo "SD.Next 기동: docker compose up -d sdnext"
