#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$(dirname "$SCRIPT_DIR")/.env"
source "${SCRIPT_DIR}/lib.sh"

APP_ROOT="${COMFYUI_APP_ROOT:-/opt/comfyui}"
DATA_ROOT="${COMFYUI_DATA_ROOT:-/var/lib/comfyui}"
PORT="${COMFYUI_PORT:-8188}"
REF="${COMFYUI_REF:-master}"
USR="${COMFYUI_USER:-comfyui}"
GRP="${COMFYUI_GROUP:-comfyui}"
VENV="${APP_ROOT}/venv"
APP="${APP_ROOT}/app/ComfyUI"
MODELS="${DATA_ROOT}/models"
OUTPUT="${DATA_ROOT}/output"

TORCH=2.7.1; TORCHVIS=0.22.1; TORCHAUD=2.7.1
TORCH_INDEX=https://download.pytorch.org/whl/cu128

require_supported_platform
[[ $EUID -ne 0 ]] && exec sudo --preserve-env=COMFYUI_APP_ROOT,COMFYUI_DATA_ROOT,COMFYUI_PORT,COMFYUI_REF,COMFYUI_USER,COMFYUI_GROUP "$0" "$@"

echo "=== ComfyUI installer ($(uname -sr) $(detect_arch)) — APP=$APP_ROOT DATA=$DATA_ROOT :$PORT ==="

if has_nvidia_gpu; then
  info "GPU: $(get_gpu_name)"
  has_gb10 && info "GB10 unified memory (DGX Spark)"
else
  warn "NVIDIA GPU 없음 — CPU fallback. https://developer.nvidia.com/cuda-downloads"
fi

if command -v apt-get &>/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip git curl ca-certificates libgl1 libglib2.0-0
else
  warn "apt-get 없음 — python3/python3-venv/git/libgl1/libglib2.0-0 수동 설치 필요"
fi

getent passwd "$USR" &>/dev/null || \
  useradd --system --home-dir "$DATA_ROOT" --shell /usr/sbin/nologin --user-group "$USR"
for g in video render; do
  getent group "$g" &>/dev/null && usermod -a -G "$g" "$USR"
done

mkdir -p "$APP_ROOT" "$APP_ROOT/app" "$DATA_ROOT" "$MODELS" "$OUTPUT"
for s in checkpoints unet clip vae loras controlnet upscale_models; do
  mkdir -p "$MODELS/$s"
done
chown -R "$USR:$GRP" "$APP_ROOT" "$DATA_ROOT"

run_as() { sudo -u "$USR" "$@"; }

if [[ -d "$VENV" ]] && run_as "$VENV/bin/python" -c 'import torch' &>/dev/null; then
  read -rp "venv 존재 (torch=$(run_as "$VENV/bin/python" -c 'import torch; print(torch.__version__)' 2>/dev/null || echo '?')). Reinstall? [y/N] " ans
  if [[ "$ans" =~ ^[Yy]$ ]]; then SKIP_PIP=0; else SKIP_PIP=1; fi
else
  run_as python3 -m venv "$VENV"
  SKIP_PIP=0
fi

if (( SKIP_PIP == 0 )); then
  run_as "$VENV/bin/pip" install --upgrade pip
  run_as "$VENV/bin/pip" install --index-url "$TORCH_INDEX" \
    "torch==$TORCH" "torchvision==$TORCHVIS" "torchaudio==$TORCHAUD"

  if [[ -d "$APP/.git" ]]; then
    run_as git -C "$APP" fetch --depth 1 origin "$REF"
    run_as git -C "$APP" checkout "$REF"
    run_as git -C "$APP" reset --hard "origin/$REF"
  else
    run_as git clone --depth 1 --branch "$REF" https://github.com/comfyanonymous/ComfyUI.git "$APP"
  fi
  run_as "$VENV/bin/pip" install -r "$APP/requirements.txt"

  install_node() {
    local name="$1" url="$2" reqs="$3" dest="$APP/custom_nodes/$1"
    if [[ -d "$dest/.git" ]]; then run_as git -C "$dest" pull --ff-only || warn "ff-only 실패: $name"
    else run_as git clone --depth 1 "$url" "$dest"; fi
    [[ -n "$reqs" && -f "$dest/$reqs" ]] && run_as "$VENV/bin/pip" install -r "$dest/$reqs"
  }
  install_node ComfyUI-Manager https://github.com/ltdrdata/ComfyUI-Manager.git requirements.txt
  install_node ComfyUI-GGUF    https://github.com/city96/ComfyUI-GGUF.git      ""
  run_as "$VENV/bin/pip" install --upgrade gguf
fi

link_into_app() {
  local link="$1" target="$2"
  [[ -L "$link" && "$(readlink "$link")" == "$target" ]] && return 0
  if [[ -d "$link" && ! -L "$link" && -n "$(ls -A "$link")" ]]; then
    warn "${link} 비어 있지 않음 — 직접 옮겨야 함."; return 0
  fi
  rm -rf "$link"; ln -s "$target" "$link"
}
link_into_app "$APP/models" "$MODELS"
link_into_app "$APP/output" "$OUTPUT"

cat > /etc/systemd/system/comfyui.service <<UNIT
[Unit]
Description=ComfyUI (KloudChat image-gen backend)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USR}
Group=${GRP}
WorkingDirectory=${APP}
Environment=PATH=${VENV}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=${VENV}/bin/python ${APP}/main.py --listen 0.0.0.0 --port ${PORT} --highvram
RestrictAddressFamilies=AF_UNIX AF_INET
Restart=on-failure
RestartSec=5
TimeoutStartSec=900

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
if systemctl is-enabled comfyui &>/dev/null; then systemctl restart comfyui
else systemctl enable --now comfyui; fi

if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  warn "ufw active — sudo ufw allow from <compose-host-ip> to any port ${PORT}"
fi

for i in {1..60}; do
  curl -sf "http://localhost:${PORT}/system_stats" &>/dev/null && break
  sleep 2
  (( i == 60 )) && { error "ComfyUI 응답 없음. journalctl -u comfyui -n 50"; exit 1; }
done

if [[ -f "$ENV_FILE" ]]; then
  new="http://host.docker.internal:${PORT}"
  old="$(env_get COMFYUI_URLS)"
  if [[ -z "$old" || "$old" == "$new" ]]; then env_set COMFYUI_URLS "$new"
  else warn "COMFYUI_URLS=$old (custom). 필요시 ${new} 로 수동 변경."; fi
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

=== done ===
  systemd: $(systemctl is-active comfyui 2>/dev/null || echo unknown) — journalctl -u comfyui -f
  models:  ${MODELS}
  다음:    ./scripts/download-image-models.sh
  분산 사용 시 compose 호스트 .env: COMFYUI_URLS=http://${IP:-<this-host-ip>}:${PORT}
EOF
