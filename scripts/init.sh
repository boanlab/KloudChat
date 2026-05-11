#!/bin/bash
# 서비스 최초 기동 후 1회 실행: 기본 팀·유저·서비스키 초기화
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

echo "=== KloudChat 초기화 ==="

# 1. LiteLLM 준비 대기
wait_for_litellm 120

# 2. 기본 팀 생성
echo
echo "--- 팀 생성 ---"
# --models 는 manage.sh 의 default 사용 (현재 5개 명시).
# 와일드카드 (`ollama/*`) 를 쓰면 LiteLLM 가 사용자 키 응답 시 모델 목록을 제대로
# expand 못 해서 ollama/llama2 한 개로 fallback 되는 이슈 있음.
bash "${SCRIPT_DIR}/manage.sh" team create \
  --alias admin \
  --budget 9999 \
  --tpm 100000 \
  --rpm 500

bash "${SCRIPT_DIR}/manage.sh" team create \
  --alias default \
  --budget 9999 \
  --tpm 100000 \
  --rpm 500

# 3. LibreChat 서비스 키 발급 → .env 자동 업데이트
echo
echo "--- LibreChat 서비스 키 발급 ---"
bash "${SCRIPT_DIR}/manage.sh" key issue --service librechat --budget 9999

# 4. 완료 안내
echo
echo "=== 초기화 완료 ==="
echo "다음 단계:"
echo "  - 유저 추가:  ./scripts/manage.sh user create --id <이메일> --team default"
echo "  - 키 발급:    ./scripts/manage.sh key issue --user <이메일> --team default"
echo "  - LibreChat 재시작(서비스 키 반영): docker compose restart librechat"
