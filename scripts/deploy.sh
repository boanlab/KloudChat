#!/usr/bin/env bash
# 아키텍처를 자동 감지해 적절한 compose 파일을 선택하여 기동합니다.
#   arm64  →  docker-compose.yml 단독
#   amd64  →  docker-compose.yml + docker-compose.amd64.yml
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

ARCH="$(uname -m)"

case "$ARCH" in
  x86_64)
    echo "==> 아키텍처: amd64 — STT(whisper) + 이미지 생성(SD.Next) 포함하여 배포"
    COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.amd64.yml)
    ;;
  aarch64 | arm64)
    echo "==> 아키텍처: arm64 — STT(whisper) / 이미지 생성(SD.Next) 제외 (CUDA 전용). TTS 는 base 에 포함되어 동작."
    COMPOSE_FILES=(-f docker-compose.yml)
    ;;
  *)
    echo "알 수 없는 아키텍처: $ARCH" >&2
    exit 1
    ;;
esac

cd "$PROJECT_ROOT"
docker compose "${COMPOSE_FILES[@]}" "$@"
