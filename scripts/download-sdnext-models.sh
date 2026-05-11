#!/bin/bash
# Download SD.Next checkpoint(s).
# SD.Next auto-detects .safetensors files in ./sdnext/models/Stable-diffusion/.
set -euo pipefail

MODELS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/sdnext/models"
SD_DIR="${MODELS_DIR}/Stable-diffusion"
VAE_DIR="${MODELS_DIR}/VAE"

mkdir -p "${SD_DIR}" "${VAE_DIR}"

# ---- Downloader ----
# args: <url> <dest> [auth]
#   auth="hf"  → pass HF_TOKEN as Authorization (gated HuggingFace models)
download() {
  local url="$1"
  local dest="$2"
  local auth="${3:-}"
  local name
  name="$(basename "$dest")"

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

  if command -v wget &>/dev/null; then
    wget --show-progress -q "${wget_auth[@]}" -O "${dest}.tmp" "$url" && mv "${dest}.tmp" "$dest"
  elif command -v curl &>/dev/null; then
    curl -L --progress-bar "${curl_auth[@]}" -o "${dest}.tmp" "$url" && mv "${dest}.tmp" "$dest"
  else
    echo "error: wget or curl is required." >&2
    exit 1
  fi

  echo "  done: $(du -sh "$dest" | cut -f1)"
}

# ---- Usage ----
usage() {
  cat <<EOF
Usage: $(basename "$0") [models...]

Model aliases:
  sdxl          SDXL 1.0 base                (~6.9GB) — general purpose  [default]
  sdxl-turbo    SDXL-Turbo                   (~6.9GB) — fast (1-4 steps)
  vae           SDXL VAE FP16 Fix            (~160MB) — improved SDXL colour  [recommended]
  flux-schnell  Flux.1-schnell Q8 GGUF       (~24GB)  — high quality, Apache 2.0
                (requires HF_TOKEN env var for HuggingFace)
  all           sdxl + sdxl-turbo + vae

Examples:
  $(basename "$0") sdxl vae
  $(basename "$0") all
  HF_TOKEN=hf_xxx $(basename "$0") flux-schnell
EOF
  exit 0
}

[[ $# -eq 0 ]] && set -- sdxl vae   # defaults

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
        echo "error: Flux.1-schnell requires a HuggingFace token." >&2
        echo "  HF_TOKEN=hf_xxx ./$(basename "$0") flux-schnell" >&2
        exit 1
      fi
      download \
        "https://huggingface.co/city96/FLUX.1-schnell-gguf/resolve/main/flux1-schnell-Q8_0.gguf" \
        "${SD_DIR}/flux1-schnell-Q8_0.gguf" hf
      download \
        "https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors" \
        "${VAE_DIR}/flux_ae.safetensors" hf
      ;;
    all)
      "$0" sdxl sdxl-turbo vae
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
echo "SD models: $(ls "${SD_DIR}"/*.{safetensors,gguf} 2>/dev/null | wc -l)"
echo "VAE files: $(ls "${VAE_DIR}"/*.safetensors 2>/dev/null | wc -l)"
echo
echo "Start SD.Next:  ./scripts/deploy.sh up -d sdnext"
