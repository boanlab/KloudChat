#!/bin/bash
set -euo pipefail

__SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
__ENV_FILE="$(dirname "$__SCRIPT_DIR")/.env"
# .env 의 HF_TOKEN 자동 로드. shell 에 export 안 돼있어도 gated repo 다운로드 가능.
if [[ -z "${HF_TOKEN:-}" && -f "$__ENV_FILE" ]]; then
  HF_TOKEN="$(grep -E '^HF_TOKEN=' "$__ENV_FILE" | head -1 | cut -d= -f2-)"
  [[ -n "$HF_TOKEN" ]] && export HF_TOKEN
fi
source "$__SCRIPT_DIR/lib.sh"

MODELS_DIR="${COMFYUI_MODELS_DIR:-/opt/comfyui/app/ComfyUI/models}"
CKPT_DIR="${MODELS_DIR}/checkpoints"
UNET_DIR="${MODELS_DIR}/unet"
CLIP_DIR="${MODELS_DIR}/clip"
VAE_DIR="${MODELS_DIR}/vae"

# 디폴트 셋 — LibreChat generate_image 툴이 노출하는 alias 기준.
# HF_TOKEN이 있으면 flux-dev 추가, 없으면 제외.
# (실제 등록은 shim 의 union 디스커버리(/object_info)로 노드별 alias 활성화 — 여기선 다운로드만)
recommended_aliases() {
  has_nvidia_gpu || return 0
  local base="qwen-image qwen-image-edit flux-shared flux-schnell"
  [[ -n "${HF_TOKEN:-}" ]] && echo "$base flux-dev" || echo "$base"
}

if { [[ -d "$MODELS_DIR" && ! -w "$MODELS_DIR" ]] || \
     [[ ! -d "$MODELS_DIR" && ! -w "$(dirname "$MODELS_DIR")" ]]; } && [[ $EUID -ne 0 ]]; then
  exec sudo --preserve-env=COMFYUI_MODELS_DIR,HF_TOKEN "$0" "$@"
fi

mkdir -p "$CKPT_DIR" "$UNET_DIR" "$CLIP_DIR" "$VAE_DIR"

download() {
  local url="$1" dest="$2" name; name="$(basename "$dest")"
  if [[ -f "$dest" ]]; then echo "[skip] $name"; return 0; fi
  echo "[pull] $name"
  local auth=()
  [[ -n "${HF_TOKEN:-}" ]] && auth=(--header="Authorization: Bearer ${HF_TOKEN}")
  if ! wget --show-progress -q "${auth[@]}" -O "${dest}.tmp" "$url"; then
    rm -f "${dest}.tmp"; echo "error: wget failed: $url" >&2; return 1
  fi
  mv "${dest}.tmp" "$dest"
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [models...]

Aliases (default: recommended). Qwen 계열은 GPU 클래스에 따라 quant 자동 선택:
  qwen-image[:QUANT]       Qwen-Image UNet + encoder + VAE
                            QUANT: nvfp4 (Blackwell) / fp8 (Ada+) / gguf (older)
  qwen-image-edit[:QUANT]  Qwen-Image-Edit-2509 UNet + 공유 encoder/VAE
                            QUANT: fp8 / gguf (NVFP4 변형 없음)
  flux-shared              Flux T5/CLIP/VAE encoders (공유)     ~10GB
  flux-dev                 FLUX.1-dev FP16 (gated, HF_TOKEN)    ~22GB
  flux-schnell             FLUX.1-schnell FP16 (MIT)            ~22GB
  recommended              HF_TOKEN 유무로 분기 (있으면 +flux-dev)
  all                      모두 (Qwen 계열은 자동 quant)

QUANT 생략 시 \`recommended_image_quant\` 가 GPU 클래스로 결정:
  gb10 / blackwell-pro / blackwell-5090 → NVFP4 (edit 은 FP8)
  ada-4090                              → FP8
  기타                                  → GGUF

shim 의 union 디스커버리가 alias→보유 노드 매핑을 잡아 자동 라우팅 — 노드별
다른 quant 를 받아도 됩니다. 명시적 \`qwen-image:fp8\` 형태로 강제 가능.
EOF
  exit 0
}

# alias + quant → "<url>\t<dest>" (UNet 파일만; encoder/VAE 는 quant 무관 공유)
qi_unet() {
  local alias="$1" quant="$2"
  case "$alias:$quant" in
    qwen-image:nvfp4)
      printf '%s\t%s' \
        'https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_nvfp4.safetensors' \
        "$UNET_DIR/qwen-image-nvfp4.safetensors" ;;
    qwen-image:fp8)
      printf '%s\t%s' \
        'https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_fp8_e4m3fn.safetensors' \
        "$UNET_DIR/qwen-image-fp8.safetensors" ;;
    qwen-image:gguf)
      printf '%s\t%s' \
        'https://huggingface.co/city96/Qwen-Image-gguf/resolve/main/qwen-image-Q8_0.gguf' \
        "$UNET_DIR/qwen-image-Q8_0.gguf" ;;
    qwen-image-edit:fp8)
      printf '%s\t%s' \
        'https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors' \
        "$UNET_DIR/qwen-image-edit-fp8.safetensors" ;;
    qwen-image-edit:gguf)
      printf '%s\t%s' \
        'https://huggingface.co/QuantStack/Qwen-Image-Edit-2509-GGUF/resolve/main/Qwen-Image-Edit-2509-Q8_0.gguf' \
        "$UNET_DIR/qwen-image-edit-Q8_0.gguf" ;;
    qwen-image-edit:nvfp4)
      echo "error: qwen-image-edit 는 NVFP4 변형 없음. fp8 또는 gguf 사용." >&2; return 1 ;;
    *) echo "error: unsupported alias:quant — $alias:$quant" >&2; return 1 ;;
  esac
}

# encoder/VAE 는 quant 무관 공유 (모든 Qwen 변형이 같은 텍스트 인코더 + VAE 사용)
QI_TEXT_URL=https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors
QI_VAE_URL=https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors
QI_TEXT="$CLIP_DIR/qwen-image-text-encoder.safetensors"
QI_VAE="$VAE_DIR/qwen-image-vae.safetensors"

# Flux (FP16). dev 는 gated; schnell 은 MIT. T5/CLIP/VAE 는 두 변형이 공유.
FLUX_DEV_URL=https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors
FLUX_SCHNELL_URL=https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/flux1-schnell.safetensors
FLUX_T5_URL=https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors
FLUX_CLIPL_URL=https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors
FLUX_VAE_URL=https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors

FLUX_DEV="$UNET_DIR/flux1-dev.safetensors"
FLUX_SCHNELL="$UNET_DIR/flux1-schnell.safetensors"
FLUX_T5="$CLIP_DIR/t5xxl_fp16.safetensors"
FLUX_CLIPL="$CLIP_DIR/clip_l.safetensors"
FLUX_VAE="$VAE_DIR/flux-ae.safetensors"

# alias[:quant] 파싱 — 'qwen-image:fp8' → alias=qwen-image, quant=fp8.
# quant 생략 시 recommended_image_quant 가 GPU 로 결정.
do_qwen_alias() {
  local raw="$1" alias quant pair url dest
  alias="${raw%%:*}"
  if [[ "$raw" == *:* ]]; then
    quant="${raw#*:}"
  else
    quant="$(recommended_image_quant "$alias")"
    echo "GPU class $(detect_gpu_class) → $alias quant=$quant"
  fi
  pair="$(qi_unet "$alias" "$quant")"
  url="${pair%$'\t'*}"; dest="${pair#*$'\t'}"
  download "$url"         "$dest"
  download "$QI_TEXT_URL" "$QI_TEXT"
  download "$QI_VAE_URL"  "$QI_VAE"
}

[[ $# -eq 0 ]] && set -- recommended

for arg in "$@"; do
  case "$arg" in
    qwen-image|qwen-image:*)            do_qwen_alias "$arg" ;;
    qwen-image-edit|qwen-image-edit:*)  do_qwen_alias "$arg" ;;
    flux-shared)     download "$FLUX_T5_URL"      "$FLUX_T5"
                     download "$FLUX_CLIPL_URL"   "$FLUX_CLIPL"
                     download "$FLUX_VAE_URL"     "$FLUX_VAE" ;;
    flux-dev)        "$0" flux-shared
                     download "$FLUX_DEV_URL"     "$FLUX_DEV" ;;
    flux-schnell)    "$0" flux-shared
                     download "$FLUX_SCHNELL_URL" "$FLUX_SCHNELL" ;;
    all)             "$0" qwen-image qwen-image-edit flux-shared flux-dev flux-schnell ;;
    recommended)
      rec="$(recommended_aliases)"
      if [[ -z "$rec" ]]; then
        echo "GPU 미감지 — recommended 셋 없음. 명시적 alias 필요. (--help)" >&2
        exit 1
      fi
      echo "GPU class: $(detect_gpu_class) → recommended: $rec"
      "$0" $rec ;;
    -h|--help)       usage ;;
    *)               echo "Unknown alias: $arg" >&2; usage ;;
  esac
done

# sudo 로 실행됐으면 모델 디렉토리 소유권을 comfyui 데몬에 맞춤 — install-comfyui.sh 가
# /opt/comfyui 트리를 comfyui:comfyui 로 만들지만 sudo 가 wget 한 파일은 root 소유.
if [[ $EUID -eq 0 ]] && getent passwd comfyui &>/dev/null; then
  chown -R comfyui:comfyui "$MODELS_DIR" 2>/dev/null || true
fi

echo "=== done ==="
echo "  unet (safetensors): $(ls "$UNET_DIR"/*.safetensors 2>/dev/null | wc -l)  unet (gguf): $(ls "$UNET_DIR"/*.gguf 2>/dev/null | wc -l)  clip: $(ls "$CLIP_DIR"/*.safetensors 2>/dev/null | wc -l)  vae: $(ls "$VAE_DIR"/*.safetensors 2>/dev/null | wc -l)"
