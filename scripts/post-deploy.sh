#!/usr/bin/env bash
# KloudChat 배포 후 runtime 패치 스크립트
# 컨테이너 기동 + init.sh 실행 후 1회 실행
#
# 적용 항목 (runtime 만 — 영구 patch 는 Dockerfile.* 에서 빌드 시 처리):
#   1. scripts/config.env 자동 생성 (없을 때)
#   2. SD.Next 활성 체크포인트 sd_xl_base_1.0 으로 설정 (runtime API call)
#
# 영구화된 항목 (이 스크립트 더이상 처리 안 함):
#   - LibreChat streamAudio.js TTS thinking 필터 → Dockerfile.librechat
#   - LibreChat client bundle .hwp 업로드 patch  → Dockerfile.librechat
#   - code-interpreter NanumGothic 한글 폰트     → Dockerfile.code-interpreter
#   - rag_api .hwp 파일 분기                     → Dockerfile.rag
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()  { echo -e "${GREEN}✓${NC} $*"; }
warn(){ echo -e "${YELLOW}!${NC} $*"; }

echo "=== KloudChat post-deploy 패치 ==="
echo

# ----------------------------------------------------------
# 1. scripts/config.env 자동 생성
# ----------------------------------------------------------
if [[ ! -f scripts/config.env ]]; then
  if [[ -f scripts/config.env.example ]]; then
    cp scripts/config.env.example scripts/config.env
    ok "scripts/config.env 생성 (manage.sh / init.sh 사용 가능)"
  else
    warn "scripts/config.env.example 없음 — manage.sh 가 동작 안 할 수 있음"
  fi
else
  ok "scripts/config.env 이미 존재"
fi

# ----------------------------------------------------------
# 2. SD.Next 활성 체크포인트 설정 (runtime API — SD.Next 가 첫 기동 후 모델 선택 필요)
# sdnext volume 에 SD.Next 설정이 persist 되긴 하지만 첫 셋업 시 한 번은 호출 필요.
# ----------------------------------------------------------
if docker ps --format '{{.Names}}' | grep -q '^sdnext$'; then
  echo "  SD.Next 활성 체크포인트 설정 중..."
  for i in 1 2 3 4 5; do
    if docker exec sdnext curl -s --max-time 3 -X POST \
        http://localhost:7860/sdapi/v1/options \
        -H "Content-Type: application/json" \
        -d '{"sd_model_checkpoint":"sd_xl_base_1.0"}' >/dev/null 2>&1; then
      ok "SD.Next 활성 모델: sd_xl_base_1.0"
      break
    fi
    sleep 5
  done
else
  warn "sdnext 컨테이너 없음 — amd64 + NVIDIA GPU 환경이 아니거나 아직 기동 전"
fi

# ----------------------------------------------------------
# 다음 단계 안내
# ----------------------------------------------------------
echo
echo "=== 다음 단계 ==="
echo
echo "1. 첫 admin 사용자 + LiteLLM 키 + agent + preset 한 명령으로 (manage.sh 통합 흐름):"
echo "   ./scripts/manage.sh user create \\"
echo "     --id admin@example.com --name '관리자' --username admin --password '<8자이상비밀번호>' \\"
echo "     --budget 9999"
echo "   → 출력된 KEY: sk-... 는 LibreChat 의 keys 컬렉션에 자동 등록되어 사용자 첫 채팅 시 입력 불필요"
echo
echo "2. 접속:"
echo "   LibreChat: http://localhost:8080"
echo "   LiteLLM admin UI: http://localhost:8000/ui (Username: admin, Password: \$LITELLM_MASTER_KEY)"
echo
echo "=== 완료 ==="
