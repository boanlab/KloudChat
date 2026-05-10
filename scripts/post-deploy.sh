#!/usr/bin/env bash
# KloudChat 배포 후 runtime 패치 스크립트
# 컨테이너 기동 + init.sh 실행 후 1회 실행
#
# 적용 항목:
#   1. scripts/config.env 자동 생성 (없을 때)
#   2. SD.Next 활성 체크포인트 sd_xl_base_1.0 으로 설정
#   3. code-interpreter 컨테이너에 NanumGothic 한글 폰트 배포
#   4. LibreChat streamAudio.js 의 TTS thinking 필터 패치 (Qwen 등 reasoning 모델 대응)
#
# ⚠️ 컨테이너 재생성 시 (docker compose down + up, image 업그레이드, --force-recreate)
#    위 2~4 항목은 휘발되니 재실행 필요.
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
# 2. SD.Next 활성 체크포인트 설정
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
  warn "sdnext 컨테이너 없음 — amd64 환경이 아니거나 아직 기동 전"
fi

# ----------------------------------------------------------
# 3. code-interpreter 한글 폰트
# ----------------------------------------------------------
FONT_FILE="$PROJECT_DIR/fonts/NanumGothic-Regular.ttf"
if [[ -f "$FONT_FILE" ]] && docker ps --format '{{.Names}}' | grep -q '^code-interpreter$'; then
  docker cp "$FONT_FILE" \
    code-interpreter:/usr/share/fonts/truetype/NanumGothic-Regular.ttf
  docker exec code-interpreter sh -c \
    "rm -rf /root/.cache/matplotlib /home/*/.cache/matplotlib 2>/dev/null" >/dev/null || true
  ok "NanumGothic 폰트 → code-interpreter:/usr/share/fonts/truetype/"
  echo "    (한글 차트 시 사용자 코드에 다음 두 줄 포함 필요:"
  echo "       fm.fontManager.addfont('/usr/share/fonts/truetype/NanumGothic-Regular.ttf')"
  echo "       plt.rcParams['font.family'] = 'NanumGothic')"
elif [[ ! -f "$FONT_FILE" ]]; then
  warn "fonts/NanumGothic-Regular.ttf 없음 — 한글 차트 미지원"
fi

# ----------------------------------------------------------
# 4. LibreChat TTS thinking 필터 패치
# ----------------------------------------------------------
if docker ps --format '{{.Names}}' | grep -q '^LibreChat$'; then
  TARGET="/app/api/server/services/Files/Audio/streamAudio.js"
  if docker exec LibreChat grep -q 'parseTextParts(message.content, true)' "$TARGET" 2>/dev/null; then
    ok "TTS thinking 필터 이미 적용됨"
  else
    docker exec LibreChat sed -i \
      's|parseTextParts(message.content)|parseTextParts(message.content, true)|g' \
      "$TARGET" >/dev/null 2>&1 || true
    docker restart LibreChat >/dev/null
    ok "TTS thinking 필터 패치 적용 + LibreChat 재시작"
  fi
else
  warn "LibreChat 컨테이너 없음 — TTS 패치 건너뜀"
fi

# ----------------------------------------------------------
# 다음 단계 안내
# ----------------------------------------------------------
echo
echo "=== 다음 단계 ==="
echo
echo "1. 첫 admin 사용자 + LiteLLM 키 한 번에 (manage.sh 통합 흐름):"
echo "   ./scripts/manage.sh user create \\"
echo "     --id admin@example.com --name '관리자' --username admin --password '<8자이상비밀번호>' \\"
echo "     --budget 9999"
echo "   → 출력된 KEY: sk-... 를 LibreChat UI 의 API Key 칸에 입력"
echo
echo "3. 접속:"
echo "   LibreChat: http://localhost:8080"
echo "   LiteLLM admin UI: http://localhost:8000/ui (Username: admin, Password: \$LITELLM_MASTER_KEY)"
echo
echo "=== 완료 ==="
