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
# float16: 모든 NVIDIA GPU(Maxwell+) 지원 whisper 표준. int8_float16 은 일부
# GPU/CTranslate2 조합에서 미지원(런타임 500) → 호환성 우선 float16 기본. 저VRAM 필요
# 시 WHISPER_COMPUTE_TYPE=int8 등으로 override.
COMPUTE_TYPE="${WHISPER_COMPUTE_TYPE:-float16}"

REINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --reinstall) REINSTALL=1 ;;
    -h|--help)   echo "Usage: $(basename "$0") [--reinstall]"; exit 0 ;;
    *)           err "Unknown: $arg"; exit 2 ;;
  esac
done

require_supported_platform

# ── amd64: 컨테이너로 실행 (arm64/GB10 는 아래 systemd 경로) ──────────────────
# amd64 CUDA 휠 = GPU 추론(float16) 지원 → docker-compose.media.yml 로 기동. arm64
# (aarch64 ct2 CPU-only int8) 는 systemd 경로 유지.
if [[ "$(detect_arch)" == amd64 ]]; then
  command -v docker &>/dev/null || { err "docker 필요 — amd64 는 컨테이너로 실행."; exit 1; }
  COMPOSE_FILE="${PROJECT_DIR}/docker-compose.media.yml"
  if ! has_nvidia_gpu; then
    warn "NVIDIA GPU 없음 — CPU 모드(int8). 추론 느림."
    COMPUTE_TYPE="int8"
  fi

  hdr "Whisper (amd64 container $(detect_arch)) — DATA=$DATA_ROOT :$PORT model=$MODEL ct=$COMPUTE_TYPE"
  has_nvidia_gpu && info "GPU: $(get_gpu_name)"

  # weight 캐시(HF_HOME) 볼륨. /var/lib 하위는 보통 root 만 mkdir 가능.
  SUDO=""; [[ -w "$(dirname "$DATA_ROOT")" ]] || SUDO="sudo"
  $SUDO mkdir -p "$DATA_ROOT"

  # 기본: Docker Hub 의 boanlab/kloudchat-whisper pull. --reinstall 시 로컬 빌드(--no-cache)
  img_ns="$(env_get KLOUDCHAT_IMAGE_NS)"; img_ns="${img_ns:-boanlab}"
  img_tag="$(env_get KLOUDCHAT_IMAGE_TAG)"; img_tag="${img_tag:-latest}"
  wenv=(WHISPER_PORT="$PORT" WHISPER_DATA_ROOT="$DATA_ROOT" WHISPER_MODEL="$MODEL"
        WHISPER_DEVICE="$DEVICE" WHISPER_COMPUTE_TYPE="$COMPUTE_TYPE" HF_TOKEN="$(env_get HF_TOKEN)"
        KLOUDCHAT_IMAGE_NS="$img_ns" KLOUDCHAT_IMAGE_TAG="$img_tag")
  if (( REINSTALL )); then
    info "docker compose build --no-cache (로컬 빌드 — cuda+faster-whisper, 수 분)"
    env "${wenv[@]}" docker compose -f "$COMPOSE_FILE" build --no-cache whisper
  else
    info "docker compose pull (boanlab/kloudchat-whisper)"
    env "${wenv[@]}" docker compose -f "$COMPOSE_FILE" pull whisper \
      || { err "pull 실패 — 이미지 미퍼블리시? './scripts/build-push-images.sh whisper' 로 push 하거나 '--reinstall' 로 로컬 빌드."; exit 1; }
  fi
  env "${wenv[@]}" docker compose -f "$COMPOSE_FILE" up -d --no-build whisper

  for i in {1..60}; do
    curl -sf "http://localhost:${PORT}/health" &>/dev/null && break
    sleep 2
    (( i == 60 )) && { err "Whisper 응답 없음. docker compose -f $COMPOSE_FILE logs whisper"; exit 1; }
  done

  if [[ -f "$ENV_FILE" ]]; then
    shim_url="http://whisper-shim:9000"  # shim 은 항상 9000 listen (backend PORT 과 무관)
    cur="$(env_get WHISPER_URL)"
    if [[ -z "$cur" || "$cur" == "http://host.docker.internal:"* || "$cur" == "$shim_url" ]]; then
      env_set WHISPER_URL "$shim_url"
    else warn "WHISPER_URL=$cur (custom). shim 경유하려면 ${shim_url} 로 변경."; fi

    local_be="http://host.docker.internal:${PORT}"
    urls="$(env_get WHISPER_URLS)"
    if [[ -z "$urls" ]]; then env_set WHISPER_URLS "$local_be"
    elif ! grep -qF "$local_be" <<<"$urls"; then env_set WHISPER_URLS "${urls},${local_be}"; fi
  fi

  # 같은 호스트의 whisper-shim 재기동(health 캐시 + inflight 리프레시). 멀티노드면 자동 skip.
  if [[ -f "${PROJECT_DIR}/docker-compose.yml" ]] \
     && docker compose --project-directory "$PROJECT_DIR" ps -q whisper-shim 2>/dev/null | grep -q .; then
    info "whisper-shim 재기동 (health 캐시 + inflight 리프레시)"
    docker compose --project-directory "$PROJECT_DIR" restart whisper-shim || warn "whisper-shim 재기동 실패"
  fi

  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  ok "done"
  cat <<EOF
  container: docker compose -f docker-compose.media.yml ps whisper | logs whisper -f
  model:    ${MODEL} (lazy-load 첫 호출 시 ~3GB → ${DATA_ROOT}; prewarm: ./scripts/download-whisper-models.sh)
  device:   ${DEVICE} / compute: ${COMPUTE_TYPE}
  멀티노드: 다른 GPU 호스트도 install 후 compose 호스트 .env 에 추가 + 'docker compose restart whisper-shim':
            WHISPER_URLS=...,http://${IP:-<this-host-ip>}:${PORT}
EOF
  exit 0
fi

# ── arm64 (GB10): systemd 네이티브 설치 ──────────────────────────────────────
[[ $EUID -ne 0 ]] && exec sudo --preserve-env=WHISPER_APP_ROOT,WHISPER_DATA_ROOT,WHISPER_PORT,WHISPER_USER,WHISPER_GROUP,WHISPER_MODEL,WHISPER_DEVICE,WHISPER_COMPUTE_TYPE "$0" "$@"

hdr "Whisper installer ($(uname -sr) $(detect_arch)) — APP=$APP_ROOT DATA=$DATA_ROOT :$PORT"

if has_nvidia_gpu; then
  info "GPU: $(get_gpu_name)"
  has_gb10 && info "GB10 unified memory"
  # NOTE: GPU 존재 != CTranslate2 가 CUDA 로 추론 가능. aarch64(GB10 등) PyPI 휠은
  # CPU-only 라 float16/int8_float16 이 런타임 500. venv 설치 후 ct2 로 실제 지원
  # compute type probe 해서 폴백(아래 "compute type probe").
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

# 프로젝트 repo 의 app.py 를 설치 루트에 복사(버전 동기 목적).
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

# compute type probe — 설정된 COMPUTE_TYPE 이 ct2 실제 지원 타입인지 검증.
# GPU 있어도 ct2(특히 aarch64 CPU-only 휠)가 CUDA 못 잡으면 device 가 cpu 로
# 강등, cpu 는 float16/int8_float16 미지원 → 런타임 500. 미지원이면 안전한 int8 로
# 폴백(cpu·gpu 양쪽 유효). probe 실패 시(예외) 원래 값 유지.
eff_ct="$(run_as "$VENV/bin/python" - "$COMPUTE_TYPE" <<'PY' 2>/dev/null || true
import sys, ctranslate2 as c
want = sys.argv[1]
dev = "cuda" if c.get_cuda_device_count() > 0 else "cpu"
sup = set(c.get_supported_compute_types(dev))
print(want if want in sup else ("int8" if "int8" in sup else "float32"), dev)
PY
)"
if [[ -n "$eff_ct" ]]; then
  new_ct="${eff_ct%% *}"; probe_dev="${eff_ct##* }"
  if [[ "$new_ct" != "$COMPUTE_TYPE" ]]; then
    warn "ct2 device=${probe_dev} 에서 compute_type=${COMPUTE_TYPE} 미지원 → ${new_ct} 로 폴백 (런타임 500 방지)"
    COMPUTE_TYPE="$new_ct"
  else
    info "compute type probe: ${COMPUTE_TYPE} (device=${probe_dev}) 지원 확인"
  fi
fi

# HF_TOKEN 은 systemd unit 인라인 금지 — unit 파일은 0644(world-readable). lazy-load
# 다운로드용 rate-limit/xet CDN 가속 토큰을 0640 secret file 로 분리해 EnvironmentFile 로 주입.
# .env 에서 토큰 제거 후 재실행 시 stale 토큰 잔존 방지 → 빈 값일 땐 secret 파일 삭제.
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
  shim_url="http://whisper-shim:9000"  # shim 은 항상 9000 listen (backend PORT 과 무관)
  cur="$(env_get WHISPER_URL)"
  if [[ -z "$cur" || "$cur" == "http://host.docker.internal:"* || "$cur" == "$shim_url" ]]; then
    env_set WHISPER_URL "$shim_url"
  else
    warn "WHISPER_URL=$cur (custom). shim 경유하려면 ${shim_url} 로 변경."
  fi

  # WHISPER_URLS = shim 이 라우팅할 backend 목록. 이 호스트(compose 호스트와 같은 머신 가정)를
  # idempotent 하게 추가. 다른 GPU 노드면 compose 호스트의 .env 에 수동 추가 필요.
  local_be="http://host.docker.internal:${PORT}"
  urls="$(env_get WHISPER_URLS)"
  if [[ -z "$urls" ]]; then
    env_set WHISPER_URLS "$local_be"
  elif ! grep -qF "$local_be" <<<"$urls"; then
    env_set WHISPER_URLS "${urls},${local_be}"
  fi
fi

# 같은 호스트에 compose 의 whisper-shim 있으면 재기동 → /health 캐시(10s TTL)
# + inflight 카운터 클리어 + 새 backend 즉시 인지. 멀티노드 케이스면 자동 skip.
if command -v docker &>/dev/null \
   && [[ -f "${PROJECT_DIR}/docker-compose.yml" ]] \
   && docker compose --project-directory "$PROJECT_DIR" ps -q whisper-shim 2>/dev/null | grep -q .; then
  info "whisper-shim 컨테이너 감지 — 재기동 (health 캐시 + inflight 리프레시)"
  docker compose --project-directory "$PROJECT_DIR" restart whisper-shim || warn "whisper-shim 재기동 실패"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
ok "done"
cat <<EOF
  systemd:  $(systemctl is-active whisper 2>/dev/null || echo unknown) — journalctl -u whisper -f
  model:    ${MODEL} (lazy-load 첫 호출 시 ~3 GB 다운로드 → ${DATA_ROOT})
            prewarm: ./scripts/download-whisper-models.sh   # 첫 호출 latency 회피
  device:   ${DEVICE} / compute: ${COMPUTE_TYPE}
  shim:     LibreChat → whisper-shim → 이 systemd. 같은 호스트의 shim 자동 재기동됨.
  멀티노드: 다른 GPU 호스트에서도 install 후 compose 호스트의 .env 에 추가 +
            그쪽에서 'docker compose restart whisper-shim':
            WHISPER_URLS=http://host.docker.internal:${PORT},http://${IP:-<this-host-ip>}:${PORT}
EOF
