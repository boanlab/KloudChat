#!/bin/sh
# LibreChat 컨테이너 startup wrapper.
# 1) WELCOME_BACK_MESSAGE / SIGNUP_HEADER env 으로 i18n 문자열 치환
# 2) 언어 선택 셀렉터에 ko-KR + en-US (+ auto) 만 노출
# 매 startup 시 .orig 백업본에서 복원 후 다시 패치하므로 멱등.
set -e

PATCH_PY="$(dirname "$0")/librechat-patch.py"

LOCALES="$(ls /app/client/dist/assets/locales.*.js 2>/dev/null | head -1 || true)"
INDEX="$(ls /app/client/dist/assets/index.*.js 2>/dev/null | head -1 || true)"

for f in "$LOCALES" "$INDEX"; do
  [ -n "$f" ] || continue
  [ -f "${f}.orig" ] || cp "$f" "${f}.orig"
  cp "${f}.orig" "$f"
done

[ -n "$LOCALES" ] && [ -n "$INDEX" ] && python3 "$PATCH_PY" "$LOCALES" "$INDEX" || true

exec "$@"
