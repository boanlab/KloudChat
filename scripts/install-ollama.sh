#!/usr/bin/env bash
# Ollama 로컬 설치 스크립트 (이 머신에서 직접 실행)
#   - Ollama 바이너리 설치 (공식 설치 스크립트 사용)
#   - systemd override: 0.0.0.0 바인딩으로 Docker 컨테이너 접근 허용
#   - .env 의 OLLAMA_API_BASE 를 host.docker.internal 로 업데이트
#   - GPU(CUDA) 감지 후 드라이버 경고 출력
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"

# ---- 색상 ----
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ---- 루트 확인 ----
if [[ $EUID -ne 0 ]]; then
  error "이 스크립트는 root 권한으로 실행해야 합니다."
  echo "  sudo $0"
  exit 1
fi

# ---- 아키텍처 확인 ----
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64 | aarch64) ;;
  *) error "지원하지 않는 아키텍처: $ARCH"; exit 1 ;;
esac

echo "=== Ollama 설치 스크립트 ==="
echo "  아키텍처: $ARCH"
echo "  OS: $(grep -oP '(?<=^PRETTY_NAME=").*(?=")' /etc/os-release 2>/dev/null || uname -s)"
echo

# ---- GPU 감지 ----
check_gpu() {
  if command -v nvidia-smi &>/dev/null; then
    info "NVIDIA GPU 감지됨:"
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null \
      | while IFS=, read -r name drv mem; do
          echo "    GPU: ${name} | 드라이버: ${drv} | VRAM: ${mem}"
        done
  else
    warn "NVIDIA GPU 를 찾을 수 없습니다. CPU 모드로 동작합니다."
    warn "GPU 사용을 원하면 CUDA 드라이버를 먼저 설치하세요:"
    warn "  https://developer.nvidia.com/cuda-downloads"
  fi
  echo
}

# ---- Ollama 바이너리 설치 ----
install_ollama() {
  if command -v ollama &>/dev/null; then
    local ver
    ver="$(ollama --version 2>/dev/null | awk '{print $NF}' || echo '알 수 없음')"
    info "Ollama 가 이미 설치되어 있습니다 (버전: ${ver})"
    read -rp "재설치(업그레이드)하시겠습니까? [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] || { info "설치를 건너뜁니다."; return 0; }
  fi

  info "Ollama 공식 설치 스크립트 실행 중..."
  curl -fsSL https://ollama.com/install.sh | sh
  info "Ollama 설치 완료"
}

# ---- ollama 홈 디렉토리 생성 ----
# 공식 설치 스크립트가 ollama 유저는 만들지만 홈 디렉토리는 생성하지 않아
# 서비스 기동 시 키 파일 저장에 실패하는 문제를 방지
setup_ollama_home() {
  local home_dir="/usr/share/ollama"
  if [[ ! -d "$home_dir" ]]; then
    mkdir -p "$home_dir"
    info "ollama 홈 디렉토리 생성: ${home_dir}"
  fi
  chown -R ollama:ollama "$home_dir"
  info "ollama 홈 디렉토리 소유권 설정: ollama:ollama ${home_dir}"
}

# ---- systemd override: 0.0.0.0 바인딩 ----
# Docker 컨테이너가 host.docker.internal:11434 으로 접근하려면
# Ollama 가 루프백(127.0.0.1)이 아닌 0.0.0.0 에서 리슨해야 합니다.
#
# 기존 override.conf 가 있으면 기존 값을 유지한 채 누락된 항목만 추가합니다.
# (OLLAMA_MODELS 등 커스텀 설정이 날아가지 않도록 보호)
configure_systemd() {
  local OVERRIDE_DIR="/etc/systemd/system/ollama.service.d"
  local OVERRIDE_FILE="${OVERRIDE_DIR}/override.conf"

  mkdir -p "$OVERRIDE_DIR"

  if [[ -f "$OVERRIDE_FILE" ]]; then
    info "기존 systemd override 발견: ${OVERRIDE_FILE}"
    cat "$OVERRIDE_FILE"
    echo
  fi

  declare -A DESIRED=(
    [OLLAMA_HOST]="0.0.0.0:11434"
    [OLLAMA_NUM_PARALLEL]="4"
    [OLLAMA_KEEP_ALIVE]="5m"
  )

  local changed=0
  for key in "${!DESIRED[@]}"; do
    if grep -qE "^Environment=\"${key}=" "$OVERRIDE_FILE" 2>/dev/null; then
      info "유지: ${key}=$(grep -oP "(?<=${key}=)[^\"]+" "$OVERRIDE_FILE" || echo '(기존값)')"
    else
      if ! grep -q '^\[Service\]' "$OVERRIDE_FILE" 2>/dev/null; then
        echo '[Service]' >> "$OVERRIDE_FILE"
      fi
      echo "Environment=\"${key}=${DESIRED[$key]}\"" >> "$OVERRIDE_FILE"
      info "추가: ${key}=${DESIRED[$key]}"
      changed=1
    fi
  done

  if [[ $changed -eq 0 ]]; then
    info "override.conf 변경 없음 (모든 항목 이미 존재)."
  else
    info "systemd override 업데이트 완료: ${OVERRIDE_FILE}"
  fi
}

# ---- 서비스 활성화 및 재시작 ----
enable_service() {
  info "systemd 데몬 재로드 중..."
  systemctl daemon-reload

  if systemctl is-enabled ollama &>/dev/null; then
    info "Ollama 서비스 재시작 중..."
    systemctl restart ollama
  else
    info "Ollama 서비스 활성화 및 시작 중..."
    systemctl enable --now ollama
  fi

  local retries=15
  info "Ollama 응답 대기 중..."
  for ((i=1; i<=retries; i++)); do
    if curl -sf http://localhost:11434/api/tags &>/dev/null; then
      info "Ollama 서비스 정상 기동 확인"
      return 0
    fi
    sleep 2
  done
  error "Ollama 서비스가 응답하지 않습니다."
  echo "  journalctl -u ollama -n 30 --no-pager"
  exit 1
}

# ---- 방화벽 안내 ----
firewall_hint() {
  # Docker → host 접근은 host.docker.internal 로 처리하므로 11434 포트를 외부에 열 필요 없음
  if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    warn "ufw 가 활성화되어 있습니다."
    warn "11434 포트를 외부에 노출하지 마세요."
    warn "  Docker → host 접근: host.docker.internal:11434 (ufw 규칙 불필요)"
  fi
}

# ---- .env 의 키=값 업데이트 (없으면 추가) ----
update_env() {
  local key="$1" val="$2"
  if [[ ! -f "$ENV_FILE" ]]; then
    warn ".env 파일이 없습니다. gen-env.sh 를 먼저 실행하세요."
    warn "  수동 설정: echo '${key}=${val}' >> .env"
    return 0
  fi
  if grep -qE "^${key}=" "$ENV_FILE"; then
    local old
    old="$(grep -E "^${key}=" "$ENV_FILE" | cut -d= -f2-)"
    if [[ "$old" == "$val" ]]; then
      info ".env 유지: ${key}=${val}"
      return 0
    fi
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    info ".env 업데이트: ${key}  ${old} → ${val}"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
    info ".env 추가: ${key}=${val}"
  fi
}

# ---- 완료 안내 ----
print_summary() {
  echo
  echo "=== 설치 완료 ==="
  echo "  Ollama 버전 : $(ollama --version 2>/dev/null || echo '확인 불가')"
  echo "  서비스 상태 : $(systemctl is-active ollama)"
  echo "  바인딩 주소 : 0.0.0.0:11434"
  echo "  Docker 접근 : host.docker.internal:11434"
  echo
  echo "다음 단계:"
  echo "  1. 모델 다운로드:  sudo -u \${SUDO_USER:-\$USER} ./scripts/download-ollama-models.sh"
  echo "  2. 서비스 기동:    ./scripts/deploy.sh up -d"
  echo "  3. 초기화:         ./scripts/init.sh"
  echo
  echo "서비스 로그:  journalctl -u ollama -f"
  echo "상태 확인:    systemctl status ollama"
}

# ---- 실행 ----
check_gpu
install_ollama
setup_ollama_home
configure_systemd
enable_service
firewall_hint
update_env "OLLAMA_API_BASE" "http://host.docker.internal:11434"
print_summary
