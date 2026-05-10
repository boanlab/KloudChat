#!/usr/bin/env bash
# KloudChat 전체 배포 스크립트 — 새 호스트에서 한 번에 실행
#
# 사용법:
#   git clone -b deploy https://github.com/gyeonghyeons/KloudChat.git
#   cd KloudChat
#   ./scripts/full-setup.sh                    # 대화형 (기본)
#   ./scripts/full-setup.sh --yes              # 모든 프롬프트 자동 yes
#   ./scripts/full-setup.sh --models all       # 모든 모델 다운로드
#   ./scripts/full-setup.sh --skip-models      # 모델 다운로드 건너뜀 (이미 받음)
#   ./scripts/full-setup.sh --no-amd64         # 음성·이미지 빼고 텍스트만
#
# 멱등성: 이미 적용된 단계는 자동으로 건너뜁니다.
set -euo pipefail

# ----------------------------------------------------------
# 색상 + 로그
# ----------------------------------------------------------
R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; B='\033[1;34m'; C='\033[0;36m'; N='\033[0m'
hdr() { echo; echo -e "${B}━━━ $* ━━━${N}"; }
ok()  { echo -e "${G}✓${N} $*"; }
warn(){ echo -e "${Y}⚠${N} $*"; }
err() { echo -e "${R}✗${N} $*" >&2; }
ask() {
  if [[ ${YES:-0} -eq 1 ]]; then return 0; fi
  read -rp "$(echo -e "${C}?${N} $1 [y/N] ")" ans
  [[ "$ans" =~ ^[Yy]$ ]]
}

# ----------------------------------------------------------
# 인자 파싱
# ----------------------------------------------------------
YES=0
MODELS_ARG=""
SKIP_MODELS=0
SKIP_AMD64_DOWNLOAD=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y)        YES=1 ;;
    --models)        shift; MODELS_ARG="${1:-}" ;;
    --models=*)      MODELS_ARG="${arg#*=}" ;;
    --skip-models)   SKIP_MODELS=1 ;;
    --no-amd64)      SKIP_AMD64_DOWNLOAD=1 ;;
    -h|--help)
      grep -E '^#( |$)' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) err "알 수 없는 옵션: $arg"; exit 1 ;;
  esac
done

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# ----------------------------------------------------------
# Step 0: 환경 점검
# ----------------------------------------------------------
hdr "0. 환경 점검"

# Docker
if ! command -v docker &>/dev/null; then
  err "Docker 가 없습니다. 'curl -fsSL https://get.docker.com | sh' 등으로 먼저 설치하세요."
  exit 1
fi
ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"

# Compose v2
if ! docker compose version &>/dev/null; then
  err "Docker Compose v2 가 없습니다."
  exit 1
fi
ok "Docker Compose $(docker compose version --short)"

# Docker 권한
if ! docker ps &>/dev/null; then
  warn "현재 사용자가 docker 그룹이 아닙니다. 'sudo usermod -aG docker \$USER' 후 재로그인 필요."
  exit 1
fi

# NVIDIA
if command -v nvidia-smi &>/dev/null; then
  GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits | head -1)
  ok "GPU: ${GPU_INFO}"
  GPU_VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1)
else
  warn "nvidia-smi 없음 → CPU 모드 (매우 느림)"
  GPU_VRAM=0
fi

# nvidia container runtime
if [[ ${GPU_VRAM:-0} -gt 0 ]]; then
  if ! docker info 2>/dev/null | grep -qi 'nvidia'; then
    warn "Docker 의 NVIDIA runtime 미등록. 'sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker' 권장"
  else
    ok "Docker NVIDIA runtime 등록됨"
  fi
fi

# 필수 도구
for tool in jq curl wget; do
  if command -v "$tool" &>/dev/null; then
    ok "$tool"
  else
    err "$tool 없음 → 'sudo apt install -y $tool'"
    exit 1
  fi
done

# 포트 점검
for port in 8080 8000 11434; do
  if ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE ":${port}\$"; then
    warn "포트 :${port} 점유 중 — KloudChat 기동 시 충돌 가능"
  fi
done

# 디스크
DISK_FREE_GB=$(df -BG "$PROJECT_DIR" | awk 'NR==2 {print $4}' | tr -d 'G')
if [[ "$DISK_FREE_GB" -lt 100 ]]; then
  warn "디스크 여유 ${DISK_FREE_GB}GB — 모델·이미지 합쳐 100GB+ 필요할 수 있음"
fi
ok "디스크 여유 ${DISK_FREE_GB}GB"

# 아키텍처 (amd64 / arm64)
ARCH=$(uname -m)
case "$ARCH" in
  x86_64)  ARCH_NAME="amd64" ;;
  aarch64|arm64) ARCH_NAME="arm64" ;;
  *) err "지원하지 않는 아키텍처: $ARCH"; exit 1 ;;
esac
ok "아키텍처: $ARCH_NAME"

# ----------------------------------------------------------
# Step 1: Ollama 호스트 설치
# ----------------------------------------------------------
hdr "1. Ollama (호스트 설치)"

if command -v ollama &>/dev/null && systemctl is-active ollama &>/dev/null; then
  ok "Ollama 이미 설치 + 실행 중 (버전: $(ollama --version 2>/dev/null | awk '{print $NF}'))"
else
  if ask "sudo 권한으로 ./scripts/install-ollama.sh 실행할까요?"; then
    sudo ./scripts/install-ollama.sh
  else
    err "Ollama 미설치 → 종료. 별도로 설치 후 다시 실행하세요."
    exit 1
  fi
fi

# ----------------------------------------------------------
# Step 2: .env 생성
# ----------------------------------------------------------
hdr "2. 환경변수 (.env) 생성"

if [[ -f .env ]]; then
  ok ".env 이미 존재 (재생성 원하면 수동으로 제거 후 ./scripts/gen-env.sh)"
else
  ./scripts/gen-env.sh
  ok ".env 생성됨"
fi

# ----------------------------------------------------------
# Step 3: 모델 다운로드
# ----------------------------------------------------------
hdr "3. Ollama 모델 다운로드"

if [[ $SKIP_MODELS -eq 1 ]]; then
  warn "--skip-models — 모델 다운로드 건너뜀"
else
  # GPU VRAM 에 따라 자동 추천
  if [[ -z "$MODELS_ARG" ]]; then
    if   [[ ${GPU_VRAM:-0} -ge 90000 ]]; then SUGGESTED="all"
    elif [[ ${GPU_VRAM:-0} -ge 45000 ]]; then SUGGESTED="qwen3-9b qwen3-35b embed"
    elif [[ ${GPU_VRAM:-0} -ge 20000 ]]; then SUGGESTED="qwen3-9b embed"
    else                                       SUGGESTED="qwen3-9b embed"
    fi
    echo "GPU VRAM 기반 추천: ${SUGGESTED}"
    if ask "이 조합 다운로드?"; then MODELS_ARG="$SUGGESTED"
    else
      read -rp "직접 입력 (예: qwen3-9b embed): " MODELS_ARG
    fi
  fi
  ./scripts/download-ollama-models.sh $MODELS_ARG
  ok "Ollama 모델 다운로드 완료"
fi

# SD.Next 모델 (amd64 + GPU)
if [[ "$ARCH_NAME" == "amd64" && ${GPU_VRAM:-0} -gt 0 && $SKIP_AMD64_DOWNLOAD -eq 0 ]]; then
  if ls sdnext/models/Stable-diffusion/*.safetensors &>/dev/null; then
    ok "SDXL 모델 이미 존재"
  else
    if ask "SDXL 모델(~7GB) 다운로드?"; then
      # 폴더 권한 (이전 컨테이너가 root 소유로 만들었을 수 있음)
      if [[ -d sdnext && ! -w sdnext/models ]]; then
        warn "sdnext/ 가 root 소유 → sudo chown 으로 본인 권한"
        sudo chown -R "$(id -u):$(id -g)" sdnext
      fi
      ./scripts/download-sdnext-models.sh
    fi
  fi
fi

# ----------------------------------------------------------
# Step 4: rag_api 이미지 빌드
# ----------------------------------------------------------
hdr "4. rag_api 이미지 빌드"

if docker image inspect kloudchat-rag_api:latest &>/dev/null; then
  ok "rag_api 이미지 이미 빌드됨 (재빌드 원하면 docker rmi 후 재실행)"
else
  # buildx 호환성 이슈 회피용 레거시 빌더
  DOCKER_BUILDKIT=0 docker compose build rag_api
  ok "rag_api 빌드 완료"
fi

# ----------------------------------------------------------
# Step 5: 컨테이너 기동
# ----------------------------------------------------------
hdr "5. 서비스 기동"

./scripts/deploy.sh up -d

# 헬스 대기
echo "  컨테이너 헬스 체크 대기 중 (~3분)..."
for i in $(seq 1 30); do
  HEALTHY=$(docker compose ps --format json 2>/dev/null | python3 -c "
import json, sys
try:
    healthy = sum(1 for line in sys.stdin if line.strip() and 'healthy' in json.loads(line).get('Health',''))
    print(healthy)
except: print(0)" 2>/dev/null || echo 0)
  printf "\r    [%2ds] %s healthy 컨테이너" "$((i*10))" "${HEALTHY}"
  sleep 10
done
echo

# ----------------------------------------------------------
# Step 6: init.sh (LiteLLM 팀·서비스키)
# ----------------------------------------------------------
hdr "6. LiteLLM 팀·서비스 키 초기화"

if [[ ! -f scripts/config.env ]]; then
  cp scripts/config.env.example scripts/config.env
  ok "scripts/config.env 자동 생성"
fi

./scripts/init.sh

# ----------------------------------------------------------
# Step 7: post-deploy (runtime 패치)
# ----------------------------------------------------------
hdr "7. Runtime 패치 (post-deploy.sh)"

./scripts/post-deploy.sh

# ----------------------------------------------------------
# Step 8: LibreChat 재시작 (서비스키 반영)
# ----------------------------------------------------------
hdr "8. LibreChat 재시작 (서비스키 반영)"

docker compose restart librechat
echo "  LibreChat 헬스 대기..."
for i in 1 2 3 4 5; do
  sleep 8
  status=$(docker inspect LibreChat --format '{{.State.Health.Status}}' 2>/dev/null || echo unknown)
  echo "    [$((i*8))s] $status"
  [[ "$status" == "healthy" ]] && break
done

# ----------------------------------------------------------
# Step 9: 검증 + 안내
# ----------------------------------------------------------
hdr "9. 검증"

# 컨테이너 상태
echo
docker compose ps --format 'table {{.Name}}\t{{.State}}\t{{.Status}}' 2>/dev/null \
  || docker ps --format 'table {{.Names}}\t{{.Status}}'

# 엔드포인트
echo
LC_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8080 || echo "fail")
LL_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8000/health/liveliness || echo "fail")
echo "  LibreChat (http://localhost:8080):     ${LC_CODE}"
echo "  LiteLLM   (http://localhost:8000):     ${LL_CODE}"

# Ollama 모델
echo
echo "  Ollama 모델:"
ollama list 2>/dev/null | tail -n +2 | awk '{printf "    - %s (%s %s)\n", $1, $3, $4}'

cat <<EOF

${G}━━━ 설치 완료 ━━━${N}

다음 단계:

  1. 첫 admin 사용자 (회원가입 비활성 우회):
     ${C}docker exec -i LibreChat npm run create-user -- \\
       admin@dankook.ac.kr "관리자" admin <8자이상비밀번호> <<< 'y'${N}

  2. LiteLLM 사용자 + 키 발급:
     ${C}./scripts/manage.sh user create --id admin@dankook.ac.kr --budget 9999${N}
     ${C}./scripts/manage.sh key issue --user admin@dankook.ac.kr --alias admin-key${N}
     → 출력된 sk-... 를 LibreChat UI 에서 LiteLLM 엔드포인트의 API Key 칸에 입력

  3. 접속:
     - LibreChat       : http://localhost:8080
     - LiteLLM admin UI: http://localhost:8000/ui  (Username: admin / Password: \$LITELLM_MASTER_KEY)

알려진 한계 (별도 작업 필요):
  - STT (Whisper)    : onerahmet 이미지가 OpenAI API 비호환 → 시연 시 STT 제외 또는 fedirz/faster-whisper-server 로 교체
  - TTS 한국어        : Kokoro 가 한국어 음성 미지원 → 영어/일본어 음성으로 우회 또는 MeloTTS 등으로 교체
  - HWP 파일 RAG     : pyhwp 가 Python 3.10 호환성 문제 → PDF/DOCX 사용

문제 발생 시 docker logs <container> 또는 ./scripts/post-deploy.sh 재실행.
EOF
