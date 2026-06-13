#!/usr/bin/env bash
set -euo pipefail

__SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$__SCRIPT_DIR/lib.sh"
# .env 의 HF_TOKEN 자동 로드. shell export 없어도 gated repo 다운로드 가능.
if [[ -z "${HF_TOKEN:-}" ]]; then
  HF_TOKEN="$(env_get HF_TOKEN)"
  [[ -n "$HF_TOKEN" ]] && export HF_TOKEN
fi

# 모델 경로: amd64 는 컨테이너 볼륨(COMFYUI_DATA_ROOT/models), arm64 는 systemd APP 내부.
if   [[ -n "${COMFYUI_MODELS_DIR:-}" ]]; then MODELS_DIR="$COMFYUI_MODELS_DIR"
elif [[ "$(detect_arch)" == amd64 ]];    then MODELS_DIR="${COMFYUI_DATA_ROOT:-/var/lib/comfyui}/models"
else                                          MODELS_DIR="/opt/comfyui/app/ComfyUI/models"; fi
CKPT_DIR="${MODELS_DIR}/checkpoints"
UNET_DIR="${MODELS_DIR}/unet"
CLIP_DIR="${MODELS_DIR}/clip"
VAE_DIR="${MODELS_DIR}/vae"

# 디폴트 셋 — LibreChat generate_image 툴이 노출하는 alias 기준.
# HF_TOKEN 있으면 flux-dev 추가, 없으면 제외.
# (실제 등록은 shim 의 union 디스커버리(/object_info)로 노드별 alias 활성화 — 여기선 다운로드만)
recommended_aliases() {
  has_nvidia_gpu || return 0
  local base="flux-shared flux-schnell-gguf"
  [[ -n "${HF_TOKEN:-}" ]] && echo "$base flux-dev-gguf" || echo "$base"
}

if { [[ -d "$MODELS_DIR" && ! -w "$MODELS_DIR" ]] || \
     [[ ! -d "$MODELS_DIR" && ! -w "$(dirname "$MODELS_DIR")" ]]; } && [[ $EUID -ne 0 ]]; then
  exec sudo --preserve-env=COMFYUI_MODELS_DIR,COMFYUI_DATA_ROOT,HF_TOKEN "$0" "$@"
fi

mkdir -p "$CKPT_DIR" "$UNET_DIR" "$CLIP_DIR" "$VAE_DIR"

download() {
  local url="$1" dest="$2" name; name="$(basename "$dest")"
  if [[ -f "$dest" ]]; then info "skip $name (이미 보유)"; return 0; fi
  info "pull $name"
  local auth=()
  [[ -n "${HF_TOKEN:-}" ]] && auth=(--header="Authorization: Bearer ${HF_TOKEN}")
  if ! wget --show-progress -q "${auth[@]}" -O "${dest}.tmp" "$url"; then
    rm -f "${dest}.tmp"; err "wget failed: $url"; return 1
  fi
  mv "${dest}.tmp" "$dest"
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [models...]

Aliases (default: recommended).
  flux-shared              Flux T5/CLIP/VAE encoders (공유)        ~10 GB
  flux-dev                 FLUX.1-dev FP16 (gated, HF_TOKEN)       ~22 GB
  flux-schnell             FLUX.1-schnell FP16 (MIT)               ~22 GB
  flux-schnell-gguf        FLUX.1-schnell Q8_0 GGUF (city96)       ~12 GB
  flux-dev-gguf            FLUX.1-dev Q8_0 GGUF (city96, gated)    ~12 GB
  ltx-video                LTX-Video 2b v0.9.5 (Video Studio)      ~9 GB
  recommended              GGUF 셋 (HF_TOKEN 있으면 +flux-dev-gguf)
  all                      모두 (ltx-video 포함)

shim 의 union 디스커버리가 alias → 보유 노드 매핑을 잡아 자동 라우팅한다.
노드별로 다른 변종 (FP16 / GGUF) 을 받아도 무관하다.
EOF
}

# Flux (FP16). dev = gated, schnell = MIT. T5/CLIP/VAE 는 모든 변형 공유.
# city96/FLUX.1-{dev,schnell}-gguf 의 Q8_0 변형은 weight 만 quantized — encoder/VAE 동일.
FLUX_DEV_URL=https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors
FLUX_SCHNELL_URL=https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/flux1-schnell.safetensors
FLUX_SCHNELL_GGUF_URL=https://huggingface.co/city96/FLUX.1-schnell-gguf/resolve/main/flux1-schnell-Q8_0.gguf
FLUX_DEV_GGUF_URL=https://huggingface.co/city96/FLUX.1-dev-gguf/resolve/main/flux1-dev-Q8_0.gguf
FLUX_T5_URL=https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp16.safetensors
FLUX_CLIPL_URL=https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors
FLUX_VAE_URL=https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors

# LTX-Video (Video Studio). 2b all-in-one 체크포인트 — T5 인코더+VAE 번들이라 단일 파일.
# checkpoints/ 로 받음(CheckpointLoaderSimple). mp4 합성엔 ComfyUI-VideoHelperSuite
# 커스텀노드(VHS_VideoCombine) 필요 — install-comfyui.sh 참고.
LTXV_URL=https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltx-video-2b-v0.9.5.safetensors
LTXV="$CKPT_DIR/ltx-video-2b-v0.9.5.safetensors"

FLUX_DEV="$UNET_DIR/flux1-dev.safetensors"
FLUX_SCHNELL="$UNET_DIR/flux1-schnell.safetensors"
FLUX_SCHNELL_GGUF="$UNET_DIR/flux1-schnell-Q8_0.gguf"
FLUX_DEV_GGUF="$UNET_DIR/flux1-dev-Q8_0.gguf"
FLUX_T5="$CLIP_DIR/t5xxl_fp16.safetensors"
FLUX_CLIPL="$CLIP_DIR/clip_l.safetensors"
FLUX_VAE="$VAE_DIR/flux-ae.safetensors"

[[ $# -eq 0 ]] && set -- recommended

for arg in "$@"; do
  case "$arg" in
    flux-shared)        download "$FLUX_T5_URL"           "$FLUX_T5"
                        download "$FLUX_CLIPL_URL"        "$FLUX_CLIPL"
                        download "$FLUX_VAE_URL"          "$FLUX_VAE" ;;
    flux-dev)           "$0" flux-shared
                        download "$FLUX_DEV_URL"          "$FLUX_DEV" ;;
    flux-schnell)       "$0" flux-shared
                        download "$FLUX_SCHNELL_URL"      "$FLUX_SCHNELL" ;;
    flux-schnell-gguf)  "$0" flux-shared
                        download "$FLUX_SCHNELL_GGUF_URL" "$FLUX_SCHNELL_GGUF" ;;
    flux-dev-gguf)      [[ -n "${HF_TOKEN:-}" ]] || { err "flux-dev-gguf 도 HF_TOKEN 필요 (FLUX.1-dev gated)"; exit 1; }
                        "$0" flux-shared
                        download "$FLUX_DEV_GGUF_URL"     "$FLUX_DEV_GGUF" ;;
    ltx-video)          download "$LTXV_URL"              "$LTXV" ;;
    all)                "$0" flux-shared flux-dev flux-schnell flux-schnell-gguf flux-dev-gguf ltx-video ;;
    recommended)
      rec="$(recommended_aliases)"
      if [[ -z "$rec" ]]; then
        err "GPU 미감지 — recommended 셋 없음. 명시적 alias 필요. (--help)"
        exit 1
      fi
      info "GPU class: $(detect_gpu_class) → recommended: $rec"
      "$0" $rec ;;
    -h|--help)       usage; exit 0 ;;
    *)               err "Unknown alias: $arg"; usage; exit 2 ;;
  esac
done

# sudo 로 실행됐으면 모델 디렉토리 소유권을 comfyui 데몬에 맞춤 — install-comfyui.sh 가
# /opt/comfyui 트리를 comfyui:comfyui 로 만들지만 sudo 가 wget 한 파일은 root 소유라서.
if [[ $EUID -eq 0 ]] && getent passwd comfyui &>/dev/null; then
  chown -R comfyui:comfyui "$MODELS_DIR" 2>/dev/null || true
fi

ok "done"
echo "  unet (safetensors): $(ls "$UNET_DIR"/*.safetensors 2>/dev/null | wc -l)  clip: $(ls "$CLIP_DIR"/*.safetensors 2>/dev/null | wc -l)  vae: $(ls "$VAE_DIR"/*.safetensors 2>/dev/null | wc -l)"
