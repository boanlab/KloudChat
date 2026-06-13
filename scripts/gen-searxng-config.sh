#!/usr/bin/env bash
# searxng/settings.yml.example → searxng/settings.yml 초기화.
# .example 의 __SEARXNG_SECRET_KEY__ sentinel → .env 의 SEARXNG_SECRET_KEY 치환.
# SearXNG 는 secret_key 를 env var 로 override 불가(settings_loader 가
# SEARXNG_SETTINGS_PATH/SEARXNG_DISABLE_ETC_SETTINGS 만 인식) → 호스트에서 한 번
# 합쳐 settings.yml 생성 후 bind-mount.
#
# live 파일 존재 시 멱등 스킵 — engines/UI 등 사용자 커스텀 보존. --force 로 재생성.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
source "${SCRIPT_DIR}/lib.sh"

ENV_FILE="${PROJECT_DIR}/.env"
CONFIG_FILE="${PROJECT_DIR}/searxng/settings.yml"
CONFIG_EXAMPLE="${PROJECT_DIR}/searxng/settings.yml.example"
SENTINEL='__SEARXNG_SECRET_KEY__'

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force)   FORCE=1 ;;
    -h|--help) echo "Usage: $(basename "$0") [--force]"; exit 0 ;;
    *)         err "Unknown: $arg"; exit 2 ;;
  esac
done

[[ -f "$CONFIG_EXAMPLE" ]] || { err "$CONFIG_EXAMPLE 없음."; exit 1; }
if [[ -f "$CONFIG_FILE" && $FORCE -eq 0 ]]; then
  info "$CONFIG_FILE 이미 존재 — --force 로 재생성."
  exit 0
fi

[[ -f "$ENV_FILE" ]] || { err "$ENV_FILE 없음. ./scripts/gen-env.sh 먼저."; exit 1; }
# searxng/ 는 컨테이너(uid 977) 한 번 뜨면 977 소유 → --force 재생성 시 mv 가
# Permission denied. 통째 재생성이라 기존 파일 read 불필요(need_read=0), 디렉터리 쓰기만 확인.
assert_regen_writable "$CONFIG_FILE" 0 || exit 1
# `source` 안 함 — 다른 값에 공백(Gmail app pw 등) 있으면 syntax 에러. 단일 키만 추출.
SEARXNG_SECRET_KEY="$(grep -E '^SEARXNG_SECRET_KEY=' "$ENV_FILE" | tail -n1 | cut -d= -f2-)"
[[ -n "$SEARXNG_SECRET_KEY" ]] || { err ".env 의 SEARXNG_SECRET_KEY 비어있음."; exit 1; }
[[ "$SEARXNG_SECRET_KEY" != change-me-* ]] || { err "SEARXNG_SECRET_KEY 가 placeholder 그대로 — gen-env.sh 재실행."; exit 1; }

# sed 구분자 = secret 에 거의 안 나타나는 글자. 만약 대비 |, /, # 모두 회피.
# secret 은 gen-env.sh 의 openssl rand -hex 출력이라 [0-9a-f] 만 — 충돌 없음.
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
sed "s|${SENTINEL}|${SEARXNG_SECRET_KEY}|" "$CONFIG_EXAMPLE" > "$tmp"

if grep -qF "$SENTINEL" "$tmp"; then
  err "sentinel 치환 실패 — .example 의 $SENTINEL 형식 확인."
  exit 1
fi
mv "$tmp" "$CONFIG_FILE"
trap - EXIT
info "$CONFIG_FILE ← $CONFIG_EXAMPLE (secret injected)"
