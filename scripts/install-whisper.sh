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

REINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --reinstall) REINSTALL=1 ;;
    -h|--help)   echo "Usage: $(basename "$0") [--reinstall]"; exit 0 ;;
    *)           echo "Unknown: $arg" >&2; exit 2 ;;
  esac
done

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
  cur_fw="$(run_as "$VENV/bin/python" -c 'import faster_whisper; print(faster_whisper.__version__)' 2>/dev/null || echo '?')"
  if (( REINSTALL )); then
    info "--reinstall — venv 재설치 진행 (faster-whisper=$cur_fw)"
    SKIP_PIP=0
  elif [[ -t 0 ]]; then
    read -rp "venv 존재 (faster-whisper $cur_fw). Reinstall? [y/N] " ans || ans=""
    [[ "$ans" =~ ^[Yy]$ ]] && SKIP_PIP=0 || SKIP_PIP=1
  else
    echo "venv 존재 — non-interactive, 재설치 건너뜀 (재설치 강제: --reinstall)"
    SKIP_PIP=1
  fi
else
  run_as python3 -m venv "$VENV"
  SKIP_PIP=0
fi

if (( SKIP_PIP == 0 )); then
  run_as "$VENV/bin/pip" install --upgrade pip
  run_as "$VENV/bin/pip" install \
    "faster-whisper>=1.0" "fastapi>=0.110" "uvicorn[standard]>=0.27" "python-multipart>=0.0.9"
fi

# HF_TOKEN 은 systemd unit 인라인 금지 — unit 파일은 0644 (world-readable). lazy-load
# 다운로드용 rate-limit/xet CDN 가속 토큰을 0640 secret file 에 분리해서 EnvironmentFile 로 주입.
# .env 에서 토큰 제거 후 재실행하면 stale 토큰 안 남도록 빈 값일 땐 secret 파일 삭제.
HF_TOKEN_VAL="$(env_get HF_TOKEN)"
if [[ -n "$HF_TOKEN_VAL" ]]; then
  mkdir -p /etc/whisper
  printf 'HF_TOKEN=%s\n' "$HF_TOKEN_VAL" > /etc/whisper/env
  chown root:"$GRP" /etc/whisper/env
  chmod 0640 /etc/whisper/env
else
  rm -f /etc/whisper/env
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
EnvironmentFile=-/etc/whisper/env
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
  (( i == 60 )) && { err "Whisper 응답 없음. journalctl -u whisper -n 50"; exit 1; }
done

if [[ -f "$ENV_FILE" ]]; then
  # WHISPER_URL = MCP 가 호출하는 endpoint — 항상 shim 가리킴. 사용자 커스텀이면 보존.
  shim_url="http://whisper-shim:${PORT}"
  cur="$(env_get WHISPER_URL)"
  if [[ -z "$cur" || "$cur" == "http://host.docker.internal:"* || "$cur" == "$shim_url" ]]; then
    env_set WHISPER_URL "$shim_url"
  else
    warn "WHISPER_URL=$cur (custom). shim 경유하려면 ${shim_url} 로 변경."
  fi

  # WHISPER_URLS = shim 이 라우팅할 backend 목록. 이 호스트(compose 호스트와 같은 머신 가정)를
  # idempotent 하게 추가. 다른 GPU 노드면 compose 호스트의 .env 에 수동으로 추가해야 함.
  local_be="http://host.docker.internal:${PORT}"
  urls="$(env_get WHISPER_URLS)"
  if [[ -z "$urls" ]]; then
    env_set WHISPER_URLS "$local_be"
  elif ! grep -qF "$local_be" <<<"$urls"; then
    env_set WHISPER_URLS "${urls},${local_be}"
  fi
fi

# 같은 호스트에 compose 의 whisper-shim 이 있으면 재기동해서 /health 캐시 (10s TTL)
# + inflight 카운터 클리어 + 새 backend 즉시 인지. 멀티노드 케이스면 자동 skip.
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
if command -v docker &>/dev/null \
   && [[ -f "${PROJECT_DIR}/docker-compose.yml" ]] \
   && docker compose --project-directory "$PROJECT_DIR" ps -q whisper-shim 2>/dev/null | grep -q .; then
  info "whisper-shim 컨테이너 감지 — 재기동 (health 캐시 + inflight 리프레시)"
  docker compose --project-directory "$PROJECT_DIR" restart whisper-shim || warn "whisper-shim 재기동 실패"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

=== done ===
  systemd:  $(systemctl is-active whisper 2>/dev/null || echo unknown) — journalctl -u whisper -f
  model:    ${MODEL} (lazy-load 첫 호출 시 ~3 GB 다운로드 → ${DATA_ROOT})
            prewarm: ./scripts/download-whisper-models.sh   # 첫 호출 latency 회피
  device:   ${DEVICE} / compute: ${COMPUTE_TYPE}
  shim:     LibreChat → whisper-shim → 이 systemd. 호스트 1대면 자동 매핑 완료.
  멀티노드: 다른 GPU 호스트에서도 install 후 compose 호스트의 .env 에 추가:
            WHISPER_URLS=http://host.docker.internal:${PORT},http://${IP:-<this-host-ip>}:${PORT}
            (그리고 'docker compose up -d whisper-shim' 으로 shim 재시작)
EOF
