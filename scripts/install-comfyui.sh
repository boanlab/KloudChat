#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"
source "${SCRIPT_DIR}/lib.sh"

APP_ROOT="${COMFYUI_APP_ROOT:-/opt/comfyui}"
DATA_ROOT="${COMFYUI_DATA_ROOT:-/var/lib/comfyui}"
PORT="${COMFYUI_PORT:-8188}"
REF="${COMFYUI_REF:-master}"
USR="${COMFYUI_USER:-comfyui}"
GRP="${COMFYUI_GROUP:-comfyui}"
VENV="${APP_ROOT}/venv"
APP="${APP_ROOT}/app/ComfyUI"
OUTPUT="${DATA_ROOT}/output"

TORCH=2.7.1; TORCHVIS=0.22.1; TORCHAUD=2.7.1
TORCH_INDEX=https://download.pytorch.org/whl/cu128

REINSTALL=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --reinstall) REINSTALL=1 ;;
    --force)     FORCE=1 ;;
    -h|--help)   echo "Usage: $(basename "$0") [--reinstall] [--force]"; exit 0 ;;
    *)           err "Unknown: $arg"; exit 2 ;;
  esac
done

require_supported_platform

# 정책: ComfyUI = PRO5000 / PRO6000 / GB10 클래스에만 설치. RTX4090(24GB) /
# RTX5090(32GB) 는 flux fullset + LLM 동거 시 OOM 가능성 커서 차단. PRO5000
# (48GB) 는 ComfyUI 전용 운영 시 fit → 허용(단 같은 노드에 vLLM
# gemma-4-26b + KV + bge + Whisper 까지 다 올리면 빠듯 → 운영자가 NODES_VLLM 등
# 신중히 분리할 책임).
case "$(detect_gpu_class)" in
  rtx4090|rtx5090)
    if [[ $FORCE -ne 1 ]]; then
      err "$(detect_gpu_class) 노드에서 ComfyUI 설치 차단 (VRAM 부족 + LLM 와 OOM 경합). 이미지 생성은 PRO5000 / PRO6000 / GB10 노드로 라우팅. 강제 진행: --force."
      exit 3
    fi
    warn "$(detect_gpu_class) — ComfyUI 강제 설치 (--force). OOM 위험 감수."
    ;;
esac

# ── amd64: 컨테이너로 실행 (arm64/GB10 는 아래 systemd 경로) ──────────────────
# 정책 게이트(위 detect_gpu_class) = 공통 적용. amd64 는 docker-compose.media.yml 로
# ComfyUI 기동 — systemd(useradd/venv/unit) 경로 건너뜀.
if [[ "$(detect_arch)" == amd64 ]]; then
  command -v docker &>/dev/null || { err "docker 필요 — amd64 는 컨테이너로 실행."; exit 1; }
  COMPOSE_FILE="${PROJECT_DIR}/docker-compose.media.yml"
  MODELS_DIR="${COMFYUI_MODELS_DIR:-${DATA_ROOT}/models}"

  hdr "ComfyUI (amd64 container $(detect_arch)) — DATA=$DATA_ROOT MODELS=$MODELS_DIR :$PORT"
  if has_nvidia_gpu; then info "GPU: $(get_gpu_name)"
  else warn "NVIDIA GPU 없음 — 컨테이너 CPU fallback (이미지 생성 매우 느림)."; fi

  # 모델/출력/Manager 상태 = 호스트 볼륨. /var/lib 하위는 보통 root 만 mkdir 가능.
  SUDO=""; [[ -w "$(dirname "$DATA_ROOT")" ]] || SUDO="sudo"
  $SUDO mkdir -p "$MODELS_DIR" "$DATA_ROOT/output" "$DATA_ROOT/user"

  # 기본: Docker Hub 의 boanlab/kloudchat-comfyui pull. --reinstall 시 로컬 빌드(--no-cache)
  img_ns="$(env_get KLOUDCHAT_IMAGE_NS)"; img_ns="${img_ns:-boanlab}"
  img_tag="$(env_get KLOUDCHAT_IMAGE_TAG)"; img_tag="${img_tag:-latest}"
  cenv=(COMFYUI_PORT="$PORT" COMFYUI_DATA_ROOT="$DATA_ROOT" COMFYUI_MODELS_DIR="$MODELS_DIR"
        KLOUDCHAT_IMAGE_NS="$img_ns" KLOUDCHAT_IMAGE_TAG="$img_tag")
  if (( REINSTALL )); then
    info "docker compose build --no-cache (로컬 빌드 — torch cu128, 수~수십 분)"
    env "${cenv[@]}" docker compose -f "$COMPOSE_FILE" build --no-cache comfyui
  else
    info "docker compose pull (boanlab/kloudchat-comfyui)"
    env "${cenv[@]}" docker compose -f "$COMPOSE_FILE" pull comfyui \
      || { err "pull 실패 — 이미지 미퍼블리시? './scripts/build-push-images.sh comfyui' 로 push 하거나 '--reinstall' 로 로컬 빌드."; exit 1; }
  fi
  env "${cenv[@]}" docker compose -f "$COMPOSE_FILE" up -d --no-build comfyui

  for i in {1..120}; do
    curl -sf "http://localhost:${PORT}/system_stats" &>/dev/null && break
    sleep 2
    (( i == 120 )) && { err "ComfyUI 응답 없음. docker compose -f $COMPOSE_FILE logs comfyui"; exit 1; }
  done

  # COMFYUI_URLS = shim 이 호출할 backend(같은 호스트 가정). 사용자 커스텀이면 보존.
  if [[ -f "$ENV_FILE" ]]; then
    new="http://host.docker.internal:${PORT}"
    old="$(env_get COMFYUI_URLS)"
    # 기존 값 다르면 scheduler/운영자 관리값 — 덮어쓰지 않고 보존.
    if [[ -z "$old" || "$old" == "$new" ]]; then env_set COMFYUI_URLS "$new"; fi
  fi

  # 같은 호스트의 comfyui-shim 재기동(variant 캐시 리프레시). 멀티노드면 자동 skip.
  if [[ -f "${PROJECT_DIR}/docker-compose.yml" ]] \
     && docker compose --project-directory "$PROJECT_DIR" ps -q comfyui-shim 2>/dev/null | grep -q .; then
    info "comfyui-shim 재기동 (variant 캐시 리프레시)"
    docker compose --project-directory "$PROJECT_DIR" restart comfyui-shim || warn "comfyui-shim 재기동 실패"
  fi

  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  ok "done"
  cat <<EOF
  container: docker compose -f docker-compose.media.yml ps comfyui | logs comfyui -f
  models:   ${MODELS_DIR}   (다음: ./scripts/download-image-models.sh)
  분산 사용 시 compose 호스트 .env: COMFYUI_URLS=http://${IP:-<this-host-ip>}:${PORT}
EOF
  exit 0
fi

# ── arm64 (GB10): systemd 네이티브 설치 ──────────────────────────────────────
[[ $EUID -ne 0 ]] && exec sudo --preserve-env=COMFYUI_APP_ROOT,COMFYUI_DATA_ROOT,COMFYUI_PORT,COMFYUI_REF,COMFYUI_USER,COMFYUI_GROUP "$0" "$@"

hdr "ComfyUI installer ($(uname -sr) $(detect_arch)) — APP=$APP_ROOT DATA=$DATA_ROOT :$PORT"

if has_nvidia_gpu; then
  info "GPU: $(get_gpu_name)"
  has_gb10 && info "GB10 unified memory"
else
  warn "NVIDIA GPU 없음 — CPU fallback. https://developer.nvidia.com/cuda-downloads"
fi

# Blackwell 카드 = torch 2.9 계열 고정 — SM 12.0 dispatch 안정성 확보.
case "$(detect_gpu_class)" in
  gb10|pro6000|pro5000|rtx5090)
    TORCH=2.9.1; TORCHVIS=0.24.1; TORCHAUD=2.9.1
    info "Blackwell class — torch=${TORCH}"
    ;;
esac

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

mkdir -p "$APP_ROOT" "$APP_ROOT/app" "$DATA_ROOT" "$OUTPUT"
chown -R "$USR:$GRP" "$APP_ROOT" "$DATA_ROOT"

run_as() { sudo -u "$USR" "$@"; }

if [[ -d "$VENV" ]] && run_as "$VENV/bin/python" -c 'import torch' &>/dev/null; then
  cur_torch="$(run_as "$VENV/bin/python" -c 'import torch; print(torch.__version__)' 2>/dev/null || echo '?')"
  if (( REINSTALL )); then
    info "--reinstall — venv 재설치 진행 (torch=$cur_torch)"
    SKIP_PIP=0
  elif [[ -t 0 ]]; then
    read -rp "venv 존재 (torch=$cur_torch). Reinstall? [y/N] " ans || ans=""
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
    # `[[ ... ]] && cmd` 는 [[ ]] 가 false 일 때 함수 마지막 statement 가 exit 1 →
    # set -e 가 호출자를 죽임(reqs="" 인 GGUF 호출이 정확히 그 경우). if/then 으로 명시.
    if [[ -n "$reqs" && -f "$dest/$reqs" ]]; then
      run_as "$VENV/bin/pip" install -r "$dest/$reqs"
    fi
  }
  install_node ComfyUI-Manager https://github.com/ltdrdata/ComfyUI-Manager.git requirements.txt
  install_node ComfyUI-GGUF    https://github.com/city96/ComfyUI-GGUF.git      ""
  # Video Studio(LTXV) 의 mp4 합성 노드 VHS_VideoCombine. LTXV 코어 노드
  # (EmptyLTXVLatentVideo/LTXVConditioning/LTXVScheduler) = 최신 ComfyUI 코어 내장.
  install_node ComfyUI-VideoHelperSuite https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git requirements.txt
  run_as "$VENV/bin/pip" install --upgrade gguf
fi

link_into_app() {
  local link="$1" target="$2"
  [[ -L "$link" && "$(readlink "$link")" == "$target" ]] && return 0
  # Treat dir as empty if it only contains ComfyUI's `put_*_here` placeholders.
  # 실제 결과물 있으면 rm 대신 관리 경로($target)로 이전 후 심링크(비파괴적).
  if [[ -d "$link" && ! -L "$link" ]] && \
     find "$link" -mindepth 1 -not -name 'put_*_here' -print -quit | grep -q .; then
    info "${link} 기존 결과물 발견 — ${target} 로 이전 후 심링크."
    mkdir -p "$target"
    # placeholder 는 남기고 나머지만 이동. 동명 파일 덮어쓰지 않음(-n).
    find "$link" -mindepth 1 -maxdepth 1 -not -name 'put_*_here' -exec mv -n -t "$target" {} +
    chown -R "$USR:$GRP" "$target"
  fi
  rm -rf "$link"; ln -s "$target" "$link"
}
link_into_app "$APP/output" "$OUTPUT"

# dynamic VRAM 본래 목적 = 작은 VRAM 카드(RTX4090 24GB 등)에서 flux 같은 큰
# 모델을 swap 으로 구동. 이 프로젝트 정책상 ComfyUI 가 도는 노드는
# PRO5000(48GB) / PRO6000(90GB usable) / GB10(96GB usable) 세 종류뿐이고
# 셋 다 flux fullset(~22 GB) 단독 fit. 세 클래스 모두 dynamic
# VRAM 이득 사실상 0(또는 음수):
#   - GB10           : unified-memory 라 GPU↔CPU 이동이 실질적 데이터 복사
#                      없음. scheduler 의 usable_vram_gb cap 과 dynamic 의
#                      reserve 추정이 서로 다른 회계 → page cache drift /
#                      co-residency noise.
#   - PRO6000 (sm_120 + cu128 torch 2.9.1) : dynamic staging 의 BF16
#                      dynamic-copy 경로가 SIGTRAP (code=dumped, status
#                      =5/TRAP) — t5xxl 가 GPU 로 옮겨지는 단계에서 driver/
#                      kernel 호환성 문제로 추정.
#   - PRO5000 (sm_120 동일) : 같은 SIGTRAP 위험 잠재. 또 48GB 라
#                      마진 작아 dynamic 의 메모리 회계 noise 가 OOM 으로
#                      직결 가능 → 결정적 로딩이 더 안전.
# 결론: ComfyUI 가 도는 모든 노드에서 --disable-dynamic-vram 강제. estimate-
# based 로딩이 fallback 으로 동작. (RTX4090/RTX5090 에서 --force 로 강제 설치 시
# 작은 VRAM 이라 dynamic swap 도움될 수도 있으나, --force 자체가
# 정책 외 사용이라 동일 플래그 유지.)
EXTRA_ARGS="--disable-dynamic-vram"

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
ExecStart=${VENV}/bin/python ${APP}/main.py --listen 0.0.0.0 --port ${PORT} ${EXTRA_ARGS}
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
  (( i == 60 )) && { err "ComfyUI 응답 없음. journalctl -u comfyui -n 50"; exit 1; }
done

if [[ -f "$ENV_FILE" ]]; then
  new="http://host.docker.internal:${PORT}"
  old="$(env_get COMFYUI_URLS)"
  # 기존 값이 다르면 scheduler/운영자 관리값 — 덮어쓰지 않고 보존.
  if [[ -z "$old" || "$old" == "$new" ]]; then env_set COMFYUI_URLS "$new"; fi
fi

# 같은 호스트에 comfyui-shim 컨테이너 있으면 재기동 → /object_info variant
# 캐시 리프레시. compose 가 다른 호스트면 자동 skip — 그쪽은 사용자가
# 'docker compose restart comfyui-shim' 으로 수동 갱신.
if command -v docker &>/dev/null \
   && [[ -f "${PROJECT_DIR}/docker-compose.yml" ]] \
   && docker compose --project-directory "$PROJECT_DIR" ps -q comfyui-shim 2>/dev/null | grep -q .; then
  info "comfyui-shim 컨테이너 감지 — 재기동 (variant 캐시 리프레시)"
  docker compose --project-directory "$PROJECT_DIR" restart comfyui-shim || warn "comfyui-shim 재기동 실패"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
ok "done"
cat <<EOF
  systemd: $(systemctl is-active comfyui 2>/dev/null || echo unknown) — journalctl -u comfyui -f
  models:  ${APP}/models
  다음:    ./scripts/download-image-models.sh
  분산 사용 시 compose 호스트 .env: COMFYUI_URLS=http://${IP:-<this-host-ip>}:${PORT}
EOF
