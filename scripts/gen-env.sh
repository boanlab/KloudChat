#!/bin/bash
# .env 자동 생성 — .env.example의 change-me-* 값을 랜덤 시크릿으로 교체
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"
ENV_EXAMPLE="${PROJECT_DIR}/.env.example"

# ---- 사용법 ----
usage() {
  cat <<EOF
사용법: $(basename "$0") [옵션]

.env.example을 기반으로 .env를 생성합니다.
change-me-* 값은 자동으로 랜덤 시크릿으로 교체됩니다.

옵션:
  --force    기존 .env가 있어도 덮어쓰기
  -h, --help 도움말
EOF
  exit 0
}

# ---- 랜덤 시크릿 생성 ----
gen_secret() {
  local bytes="${1:-16}"
  if command -v openssl &>/dev/null; then
    openssl rand -hex "$bytes"
  else
    od -vN "$bytes" -An -tx1 /dev/urandom | tr -d ' \n'
  fi
}

# ---- 인자 파싱 ----
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force)   FORCE=1 ;;
    -h|--help) usage ;;
    *) echo "알 수 없는 옵션: $arg" >&2; usage ;;
  esac
done

# ---- 사전 확인 ----
if [[ ! -f "$ENV_EXAMPLE" ]]; then
  echo "오류: .env.example 파일을 찾을 수 없습니다: ${ENV_EXAMPLE}" >&2
  exit 1
fi

if [[ -f "$ENV_FILE" && $FORCE -eq 0 ]]; then
  echo "[건너뜀] .env 이미 존재합니다. 덮어쓰려면 --force 옵션을 사용하세요."
  exit 0
fi

# ---- .env.example 읽어서 .env 생성 ----
echo "==> 시크릿 생성 중..."

declare -A GENERATED

while IFS= read -r line; do
  # change-me-* 값을 가진 KEY=... 라인 처리
  if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=change-me- ]]; then
    key="${BASH_REMATCH[1]}"
    case "$key" in
      CREDS_KEY)    secret="$(gen_secret 32)" ;;  # AES-256: 정확히 32바이트 필요
      CREDS_IV)     secret="$(gen_secret 16)" ;;  # AES IV: 정확히 16바이트 필요
      *MASTER_KEY*) secret="$(gen_secret 32)" ;;  # LiteLLM·MeiliSearch 마스터키
      JWT_*)        secret="$(gen_secret 32)" ;;  # JWT 서명키: 32바이트 권장
      *)            secret="$(gen_secret 16)" ;;
    esac
    GENERATED["$key"]="$secret"
    echo "${key}=${secret}"
  else
    echo "$line"
  fi
done < "$ENV_EXAMPLE" > "$ENV_FILE"

# ---- 결과 출력 ----
echo
echo "=== .env 생성 완료 ==="
for key in "${!GENERATED[@]}"; do
  printf "  %-24s %s\n" "${key}" "${GENERATED[$key]}"
done | sort
echo
echo "저장 위치: ${ENV_FILE}"
echo
echo "다음 단계:"
echo "  1. ./scripts/download-ollama-models.sh   # LLM 모델 다운로드"
echo "  2. ./scripts/download-sdnext-models.sh   # 이미지 생성 모델 다운로드 (amd64 전용)"
echo "  3. ./scripts/deploy.sh up -d             # 서비스 시작"
echo "  4. ./scripts/init.sh                     # 팀·키 초기화"
