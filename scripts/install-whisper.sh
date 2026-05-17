#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"
source "${SCRIPT_DIR}/lib.sh"

APP_ROOT="${WHISPER_APP_ROOT:-/opt/whisper}"
DATA_ROOT="${WHISPER_DATA_ROOT:-/var/lib/whisper}"
PORT="${WHISPER_PORT:-9000}"
USR="${WHISPER_USER:-whisper}"
GRP="${WHISPER_GROUP:-whisper}"
VENV="${APP_ROOT}/venv"
MODEL="${WHISPER_MODEL:-large-v3}"
DEVICE="${WHISPER_DEVICE:-auto}"
COMPUTE_TYPE="${WHISPER_COMPUTE_TYPE:-float16}"

require_supported_platform
[[ $EUID -ne 0 ]] && exec sudo --preserve-env=WHISPER_APP_ROOT,WHISPER_DATA_ROOT,WHISPER_PORT,WHISPER_USER,WHISPER_GROUP,WHISPER_MODEL,WHISPER_DEVICE,WHISPER_COMPUTE_TYPE "$0" "$@"

echo "=== Whisper installer ($(uname -sr) $(detect_arch)) — APP=$APP_ROOT DATA=$DATA_ROOT :$PORT ==="

if has_nvidia_gpu; then
  info "GPU: $(get_gpu_name)"
  has_gb10 && info "GB10 unified memory (DGX Spark)"
else
  warn "NVIDIA GPU 없음 — CPU 모드. 추론 느림 (실시간×~0.3)."
  COMPUTE_TYPE="int8"
fi

if command -v apt-get &>/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip ffmpeg ca-certificates curl
else
  warn "apt-get 없음 — python3/python3-venv/ffmpeg 수동 설치 필요"
fi

getent passwd "$USR" &>/dev/null || \
  useradd --system --home-dir "$DATA_ROOT" --shell /usr/sbin/nologin --user-group "$USR"
for g in video render; do
  getent group "$g" &>/dev/null && usermod -a -G "$g" "$USR"
done

mkdir -p "$APP_ROOT" "$DATA_ROOT"
chown -R "$USR:$GRP" "$APP_ROOT" "$DATA_ROOT"

# Place app.py from project repo into the installation root (versioned alongside).
install -o "$USR" -g "$GRP" -m 0644 "${PROJECT_DIR}/whisper/app.py" "${APP_ROOT}/app.py"

run_as() { sudo -u "$USR" "$@"; }

if [[ -d "$VENV" ]] && run_as "$VENV/bin/python" -c 'import faster_whisper' &>/dev/null; then
  ans=""
  if [[ -t 0 ]]; then
    read -rp "venv 존재 (faster-whisper $(run_as "$VENV/bin/python" -c 'import faster_whisper; print(faster_whisper.__version__)' 2>/dev/null || echo '?')). Reinstall? [y/N] " ans || ans=""
  else
    echo "venv 존재 — non-interactive, 재설치 건너뜀 (재설치 강제하려면 venv 삭제 후 재실행)"
  fi
  if [[ "$ans" =~ ^[Yy]$ ]]; then SKIP_PIP=0; else SKIP_PIP=1; fi
else
  run_as python3 -m venv "$VENV"
  SKIP_PIP=0
fi

if (( SKIP_PIP == 0 )); then
  run_as "$VENV/bin/pip" install --upgrade pip
  run_as "$VENV/bin/pip" install \
    "faster-whisper>=1.0" "fastapi>=0.110" "uvicorn[standard]>=0.27" "python-multipart>=0.0.9"
fi

cat > /etc/systemd/system/whisper.service <<UNIT
[Unit]
Description=Whisper (KloudChat audio transcription backend)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USR}
Group=${GRP}
WorkingDirectory=${APP_ROOT}
Environment=PATH=${VENV}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=HF_HOME=${DATA_ROOT}
Environment=WHISPER_MODEL=${MODEL}
Environment=WHISPER_DEVICE=${DEVICE}
Environment=WHISPER_COMPUTE_TYPE=${COMPUTE_TYPE}
Environment=WHISPER_PORT=${PORT}
ExecStart=${VENV}/bin/python -m uvicorn app:app --host 0.0.0.0 --port ${PORT}
RestrictAddressFamilies=AF_UNIX AF_INET
Restart=on-failure
RestartSec=5
TimeoutStartSec=900

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
if systemctl is-enabled whisper &>/dev/null; then systemctl restart whisper
else systemctl enable --now whisper; fi

if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  warn "ufw active — sudo ufw allow from <compose-host-ip> to any port ${PORT}"
fi

for i in {1..60}; do
  curl -sf "http://localhost:${PORT}/health" &>/dev/null && break
  sleep 2
  (( i == 60 )) && { error "Whisper 응답 없음. journalctl -u whisper -n 50"; exit 1; }
done

if [[ -f "$ENV_FILE" ]]; then
  new="http://host.docker.internal:${PORT}"
  old="$(env_get WHISPER_URL)"
  if [[ -z "$old" || "$old" == "$new" ]]; then env_set WHISPER_URL "$new"
  else warn "WHISPER_URL=$old (custom). 필요시 ${new} 로 수동 변경."; fi
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

=== done ===
  systemd:  $(systemctl is-active whisper 2>/dev/null || echo unknown) — journalctl -u whisper -f
  model:    ${MODEL} (lazy-load 첫 호출 시 ~3 GB 다운로드 → ${DATA_ROOT})
  device:   ${DEVICE} / compute: ${COMPUTE_TYPE}
  분산 사용 시 compose 호스트 .env: WHISPER_URL=http://${IP:-<this-host-ip>}:${PORT}
EOF
