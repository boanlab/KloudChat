#!/bin/bash
# Download image-generation model weights for ComfyUI.
#
# ComfyUI auto-discovers weights placed under ./comfyui/models/<subdir>/:
#   checkpoints/       SDXL .safetensors
#   unet/              Qwen-Image GGUF (UnetLoaderGGUF)
#   clip/              Qwen-Image text encoder
#   vae/               Qwen-Image VAE  + SDXL VAE fix
set -euo pipefail

MODELS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/comfyui/models"
CKPT_DIR="${MODELS_DIR}/checkpoints"
UNET_DIR="${MODELS_DIR}/unet"
CLIP_DIR="${MODELS_DIR}/clip"
VAE_DIR="${MODELS_DIR}/vae"

# If comfyui is already running, its root-owned bind-mounted volumes block
# host-side mkdir. Detect that case and reclaim ownership via `docker exec`,
# which doesn't need sudo (the container is already running as root).
if [[ -d "$MODELS_DIR" && ! -w "$MODELS_DIR" ]]; then
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'comfyui'; then
    echo "==> reclaiming ownership of comfyui/models from inside the container..."
    docker exec comfyui chown -R "$(id -u):$(id -g)" /app/ComfyUI/models /app/ComfyUI/output \
      || { echo "error: chown failed inside comfyui container" >&2; exit 1; }
  else
    echo "error: ${MODELS_DIR} is not writable and comfyui container is not running." >&2
    echo "  Start it first (./scripts/deploy.sh up -d comfyui) or chown manually." >&2
    exit 1
  fi
fi

mkdir -p "${CKPT_DIR}" "${UNET_DIR}" "${CLIP_DIR}" "${VAE_DIR}"

# ---- Downloader ----
# args: <url> <dest> [auth]
#   auth="hf"  → pass HF_TOKEN as Authorization header (gated HuggingFace models)
download() {
  local url="$1"
  local dest="$2"
  local auth="${3:-}"
  local name; name="$(basename "$dest")"

  if [[ -f "$dest" ]]; then
    echo "[skip] ${name} (already present)"
    return 0
  fi

  echo "[pull] ${name}"
  echo "  from: ${url}"
  echo "  to:   ${dest}"

  local wget_auth=() curl_auth=()
  if [[ "$auth" == "hf" && -n "${HF_TOKEN:-}" ]]; then
    wget_auth=(--header="Authorization: Bearer ${HF_TOKEN}")
    curl_auth=(-H "Authorization: Bearer ${HF_TOKEN}")
  fi

  # `set -e` does NOT propagate failures from inside `cmd1 && cmd2`, which
  # would let a wget 401 silently leave a 0-byte .tmp file. Use explicit
  # if/else so we fail loudly and remove the partial.
  if command -v wget &>/dev/null; then
    if ! wget --show-progress -q "${wget_auth[@]}" -O "${dest}.tmp" "$url"; then
      rm -f "${dest}.tmp"
      echo "error: wget failed for ${name} (url: ${url})" >&2
      return 1
    fi
  elif command -v curl &>/dev/null; then
    if ! curl -fL --progress-bar "${curl_auth[@]}" -o "${dest}.tmp" "$url"; then
      rm -f "${dest}.tmp"
      echo "error: curl failed for ${name} (url: ${url})" >&2
      return 1
    fi
  else
    echo "error: wget or curl is required." >&2
    exit 1
  fi

  mv "${dest}.tmp" "$dest"
  echo "  done: $(du -sh "$dest" | cut -f1)"
}

# ---- Usage ----
usage() {
  cat <<EOF
Usage: $(basename "$0") [models...]

Model aliases:
  sdxl              SDXL 1.0 base                       (~6.9GB) — general SDXL pipeline
  sdxl-vae          SDXL VAE FP16 fix                   (~160MB) — recommended with sdxl
  qwen-image        Qwen-Image Q8_0 GGUF + encoder + VAE (~21GB) — modern text-to-image
  qwen-image-edit   Qwen-Image-Edit-2509 Q8_0 GGUF      (~21GB)  — image+prompt editing
                    (shares text-encoder + VAE with qwen-image; pull both for full coverage)
  all               sdxl + sdxl-vae + qwen-image + qwen-image-edit  [default]

Environment:
  HF_TOKEN          Required for some gated HuggingFace repos.

Examples:
  $(basename "$0")                            # everything (~50GB total)
  $(basename "$0") sdxl sdxl-vae              # SDXL only
  HF_TOKEN=hf_xxx $(basename "$0") qwen-image # Qwen text-to-image
EOF
  exit 0
}

# Default: pull the full set so a fresh setup has all three pipelines ready.
[[ $# -eq 0 ]] && set -- all

# ──────────────────────────────────────────────────────────────────────
# Sources. city96 publishes Qwen-Image GGUF quants; the text encoder and
# VAE come from the Comfy-Org distribution alongside the official weights.
# ──────────────────────────────────────────────────────────────────────
SDXL_URL="https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors"
SDXL_VAE_URL="https://huggingface.co/madebyollin/sdxl-vae-fp16-fix/resolve/main/sdxl_vae.safetensors"

# Qwen-Image base UNet is published by city96; the edit variant is on
# QuantStack (city96 hasn't released an Edit-2509 GGUF as of writing).
QWEN_IMAGE_UNET_URL="https://huggingface.co/city96/Qwen-Image-gguf/resolve/main/qwen-image-Q8_0.gguf"
QWEN_IMAGE_EDIT_UNET_URL="https://huggingface.co/QuantStack/Qwen-Image-Edit-2509-GGUF/resolve/main/Qwen-Image-Edit-2509-Q8_0.gguf"
QWEN_IMAGE_TEXT_ENCODER_URL="https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"
QWEN_IMAGE_VAE_URL="https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors"

# Filenames that the workflow templates in comfyui-shim/workflows/ reference.
QWEN_IMAGE_UNET="${UNET_DIR}/qwen-image-Q8_0.gguf"
QWEN_IMAGE_EDIT_UNET="${UNET_DIR}/qwen-image-edit-Q8_0.gguf"
QWEN_IMAGE_TEXT_ENCODER="${CLIP_DIR}/qwen-image-text-encoder.safetensors"
QWEN_IMAGE_VAE="${VAE_DIR}/qwen-image-vae.safetensors"

for arg in "$@"; do
  case "$arg" in
    sdxl)
      download "$SDXL_URL" "${CKPT_DIR}/sd_xl_base_1.0.safetensors"
      ;;
    sdxl-vae)
      download "$SDXL_VAE_URL" "${VAE_DIR}/sdxl_vae_fp16_fix.safetensors"
      ;;
    qwen-image)
      download "$QWEN_IMAGE_UNET_URL"          "$QWEN_IMAGE_UNET"
      download "$QWEN_IMAGE_TEXT_ENCODER_URL"  "$QWEN_IMAGE_TEXT_ENCODER"
      download "$QWEN_IMAGE_VAE_URL"           "$QWEN_IMAGE_VAE"
      ;;
    qwen-image-edit)
      download "$QWEN_IMAGE_EDIT_UNET_URL"     "$QWEN_IMAGE_EDIT_UNET"
      download "$QWEN_IMAGE_TEXT_ENCODER_URL"  "$QWEN_IMAGE_TEXT_ENCODER"
      download "$QWEN_IMAGE_VAE_URL"           "$QWEN_IMAGE_VAE"
      ;;
    all)
      "$0" sdxl sdxl-vae qwen-image qwen-image-edit
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown model alias: $arg" >&2
      usage
      ;;
  esac
done

echo
echo "=== Downloads complete ==="
echo "Checkpoints (SDXL):  $(ls "${CKPT_DIR}"/*.safetensors 2>/dev/null | wc -l)"
echo "UNet (Qwen GGUF):    $(ls "${UNET_DIR}"/*.gguf       2>/dev/null | wc -l)"
echo "CLIP / text-encoder: $(ls "${CLIP_DIR}"/*.safetensors 2>/dev/null | wc -l)"
echo "VAE:                 $(ls "${VAE_DIR}"/*.safetensors 2>/dev/null | wc -l)"
echo
echo "Start ComfyUI:  ./scripts/deploy.sh up -d comfyui comfyui-shim"
