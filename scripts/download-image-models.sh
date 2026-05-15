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

# 디폴트 셋. HF_TOKEN이 .env/env에 있으면 flux-dev 추가, 없으면 제외.
# (실제 등록은 shim 의 union 디스커버리(/object_info)로 노드별 alias 활성화 — 여기선 다운로드만)
recommended_aliases() {
  has_nvidia_gpu || return 0
  local base="sdxl sdxl-vae qwen-image qwen-image-edit flux-shared flux-schnell"
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

Aliases (default: recommended):
  sdxl             SDXL 1.0 base                        ~6.9GB
  sdxl-vae         SDXL VAE FP16 fix                    ~160MB
  qwen-image       Qwen-Image Q8_0 GGUF + encoder + VAE ~21GB
  qwen-image-edit  Qwen-Image-Edit-2509 Q8_0 GGUF       ~21GB
  flux-shared      Flux T5/CLIP/VAE encoders (공유)     ~10GB
  flux-dev         FLUX.1-dev FP16 (gated, HF_TOKEN)    ~22GB
  flux-schnell     FLUX.1-schnell FP16 (MIT)            ~22GB
  recommended      HF_TOKEN 유무로 분기 (있으면 +flux-dev)
  all              모두 (~104GB)

기본 셋 (HF_TOKEN 무관): sdxl + sdxl-vae + qwen-image + qwen-image-edit + flux-shared + flux-schnell
HF_TOKEN 있을 때만 flux-dev 추가 (gated repo).

GPU VRAM이 부족한 노드에서 무거운 모델은 받지 마세요 — 명시적 alias 지정.
shim 의 union 디스커버리가 alias→보유 노드 매핑을 잡아 자동 라우팅하므로
노드별로 다른 셋을 받아도 됩니다.
EOF
  exit 0
}

[[ $# -eq 0 ]] && set -- recommended

SDXL_URL=https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors
SDXL_VAE_URL=https://huggingface.co/madebyollin/sdxl-vae-fp16-fix/resolve/main/sdxl_vae.safetensors
QI_UNET_URL=https://huggingface.co/city96/Qwen-Image-gguf/resolve/main/qwen-image-Q8_0.gguf
QI_EDIT_UNET_URL=https://huggingface.co/QuantStack/Qwen-Image-Edit-2509-GGUF/resolve/main/Qwen-Image-Edit-2509-Q8_0.gguf
QI_TEXT_URL=https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors
QI_VAE_URL=https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors
# Flux (FP16). dev 는 gated; schnell 은 MIT. T5/CLIP/VAE 는 두 변형이 공유.
FLUX_DEV_URL=https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors
FLUX_SCHNELL_URL=https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/flux1-schnell.safetensors
FLUX_T5_URL=https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors
FLUX_CLIPL_URL=https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors
FLUX_VAE_URL=https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors

QI_UNET="$UNET_DIR/qwen-image-Q8_0.gguf"
QI_EDIT_UNET="$UNET_DIR/qwen-image-edit-Q8_0.gguf"
QI_TEXT="$CLIP_DIR/qwen-image-text-encoder.safetensors"
QI_VAE="$VAE_DIR/qwen-image-vae.safetensors"
FLUX_DEV="$UNET_DIR/flux1-dev.safetensors"
FLUX_SCHNELL="$UNET_DIR/flux1-schnell.safetensors"
FLUX_T5="$CLIP_DIR/t5xxl_fp16.safetensors"
FLUX_CLIPL="$CLIP_DIR/clip_l.safetensors"
FLUX_VAE="$VAE_DIR/flux-ae.safetensors"

for arg in "$@"; do
  case "$arg" in
    sdxl)            download "$SDXL_URL"         "$CKPT_DIR/sd_xl_base_1.0.safetensors" ;;
    sdxl-vae)        download "$SDXL_VAE_URL"     "$VAE_DIR/sdxl_vae_fp16_fix.safetensors" ;;
    qwen-image)      download "$QI_UNET_URL"      "$QI_UNET"
                     download "$QI_TEXT_URL"      "$QI_TEXT"
                     download "$QI_VAE_URL"       "$QI_VAE" ;;
    qwen-image-edit) download "$QI_EDIT_UNET_URL" "$QI_EDIT_UNET"
                     download "$QI_TEXT_URL"      "$QI_TEXT"
                     download "$QI_VAE_URL"       "$QI_VAE" ;;
    flux-shared)     download "$FLUX_T5_URL"      "$FLUX_T5"
                     download "$FLUX_CLIPL_URL"   "$FLUX_CLIPL"
                     download "$FLUX_VAE_URL"     "$FLUX_VAE" ;;
    flux-dev)        "$0" flux-shared
                     download "$FLUX_DEV_URL"     "$FLUX_DEV" ;;
    flux-schnell)    "$0" flux-shared
                     download "$FLUX_SCHNELL_URL" "$FLUX_SCHNELL" ;;
    all)             "$0" sdxl sdxl-vae qwen-image qwen-image-edit flux-shared flux-dev flux-schnell ;;
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

if [[ $EUID -eq 0 && "$MODELS_DIR" == /var/lib/comfyui/* ]] && getent passwd comfyui &>/dev/null; then
  chown -R comfyui:comfyui "$MODELS_DIR"
fi

echo "=== done ==="
echo "  ckpt: $(ls "$CKPT_DIR"/*.safetensors 2>/dev/null | wc -l)  unet: $(ls "$UNET_DIR"/*.gguf 2>/dev/null | wc -l)  clip: $(ls "$CLIP_DIR"/*.safetensors 2>/dev/null | wc -l)  vae: $(ls "$VAE_DIR"/*.safetensors 2>/dev/null | wc -l)"
if [[ "$MODELS_DIR" == /var/lib/comfyui/* ]]; then
  echo "  → sudo systemctl restart comfyui"
fi
