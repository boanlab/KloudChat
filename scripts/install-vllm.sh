#!/usr/bin/env bash
# Usage: install-vllm.sh [--reinstall] [--image <tag>]
#
# Docker 이미지 pull + 모델 디렉토리 + GPU 런타임 검증. weight 다운은
# download-vllm-models.sh, launch 는 manage-vllm.sh.
#
# Env:
#   VLLM_IMAGE        이미지 override (default: arch 자동)
#   VLLM_MODELS_ROOT  모델 저장 위치 (default /var/lib/vllm/models)
#
# Flags:
#   --reinstall       image 재 pull
#   --image <tag>     1회 override (env VLLM_IMAGE 와 동일)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

REINSTALL=0
IMAGE_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reinstall) REINSTALL=1; shift ;;
    --image)     IMAGE_OVERRIDE="$2"; shift 2 ;;
    -h|--help)   grep -E '^# ' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)           err "Unknown: $1"; exit 1 ;;
  esac
done

hdr "0. Environment"
require_supported_platform
ok "OS / ARCH: $(detect_os) / $(detect_arch)"

has_nvidia_gpu || { err "NVIDIA GPU 미감지 — vLLM 은 GPU 필수."; exit 1; }
ok "GPU: $(get_gpu_name) (class=$(detect_gpu_class))"

command -v docker &>/dev/null || { err "Docker 없음."; exit 1; }
ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"

# `docker run --gpus all` 이 동작해야 vLLM 컨테이너가 GPU 접근.
hdr "1. GPU runtime 확인"
if docker info 2>/dev/null | grep -q "Runtimes:.*nvidia"; then
  ok "nvidia container runtime 등록됨"
else
  warn "nvidia container runtime 미감지 — nvidia-container-toolkit 설치 + docker restart 필요"
  echo "  → curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg"
  echo "  → curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb #deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] #g' | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list"
  echo "  → sudo apt update && sudo apt install -y nvidia-container-toolkit"
  echo "  → sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
  exit 1
fi

if docker run --rm --gpus all --entrypoint nvidia-smi nvcr.io/nvidia/cuda:12.6.3-base-ubuntu24.04 -L &>/dev/null; then
  ok "GPU passthrough 동작 확인"
else
  warn "GPU passthrough probe 실패 — 별도 베이스 이미지로 확인 필요"
fi

hdr "2. vLLM 이미지"
VLLM_IMAGE="${IMAGE_OVERRIDE:-${VLLM_IMAGE:-$(vllm_default_image)}}"
[[ -n "$VLLM_IMAGE" ]] || { err "VLLM_IMAGE 결정 실패 — --image 또는 env 지정 필요"; exit 1; }
echo "  image: $VLLM_IMAGE"

if (( REINSTALL )) || ! docker image inspect "$VLLM_IMAGE" &>/dev/null; then
  echo "  → pull (~10 GB)"
  docker pull "$VLLM_IMAGE"
fi

# Base image 위에 pytest layer 를 덮어쓴다 — 같은 tag 로 rebuild 라 compose 의
# image: ${VLLM_IMAGE} 가 그대로 작동. 자세한 사유는 Dockerfile.vllm.
echo "  → rebuild with Dockerfile.vllm (pytest layer)"
docker build --quiet \
  --build-arg "BASE_IMAGE=$VLLM_IMAGE" \
  -t "$VLLM_IMAGE" \
  -f "$(dirname "$SCRIPT_DIR")/Dockerfile.vllm" \
  "$(dirname "$SCRIPT_DIR")" >/dev/null

ok "image ready: $(docker image inspect "$VLLM_IMAGE" --format '{{.Size}}' | awk '{printf "%.1fGB",$1/1024/1024/1024}')"

# Persist detected image to local .env so docker-compose.vllm.yml uses it.
# compose default targets GB10 (arm64, *-aarch64 image); on the amd64 discrete
# cards that's the wrong arch and the container dies with "Failed to infer device
# type". Always-set is OK — on arm64 nodes the value matches so it's a no-op.
# Rsync 가 .env 를 덮어쓰는 다음 setup.sh vllm dispatch 에서 install-vllm.sh 가
# 다시 호출돼 같은 줄을 복원한다.
env_set VLLM_IMAGE "$VLLM_IMAGE"

hdr "3. 모델 디렉토리"
echo "  VLLM_MODELS_ROOT: $VLLM_MODELS_ROOT"
if [[ ! -d "$VLLM_MODELS_ROOT" ]]; then
  if [[ -w "$(dirname "$VLLM_MODELS_ROOT")" ]]; then
    mkdir -p "$VLLM_MODELS_ROOT"
  else
    sudo mkdir -p "$VLLM_MODELS_ROOT"
    sudo chown "$USER:$USER" "$VLLM_MODELS_ROOT"
  fi
fi
ok "dir ready: $VLLM_MODELS_ROOT ($(df -h "$VLLM_MODELS_ROOT" 2>/dev/null | awk 'NR==2 {print $4}' || echo '?') 여유)"

hdr "4. 다음 단계"
cat <<EOF

  ./scripts/download-vllm-models.sh                # GPU class 자동 감지
  ./scripts/manage-vllm.sh up                      # chat stack
  ./scripts/manage-vllm.sh up --coder              # coder stack (별도 노드)

  # .env 의 VLLM_*_URL 채우고 setup.sh 또는 gen-litellm-config.sh 재실행 →
  # LiteLLM 이 같은 model_name (local/...) 으로 자동 LB.

EOF
