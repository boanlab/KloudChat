#!/bin/sh
# LibreChat 컨테이너 startup wrapper — 빌드 번들 in-place 패치.
# - i18n 문자열 치환 (WELCOME_BACK_MESSAGE, SIGNUP_HEADER)
# - 언어 셀렉터 축약 (auto/en-US/ko-KR)
# - "Upload to Provider" 메뉴 숨김
# .orig 백업본에서 복원 후 다시 패치하므로 멱등.
set -e

PATCH_PY="$(dirname "$0")/librechat-patch.py"
ASSETS=/app/client/dist/assets

# .orig 백업(최초 1회) + 매 startup 복원.
for f in "$ASSETS"/locales.*.js "$ASSETS"/index.*.js; do
  case "$f" in *'*'*) continue ;; esac   # no-match 글로브 그대로 통과 시 스킵
  [ -f "${f}.orig" ] || cp "$f" "${f}.orig"
  cp "${f}.orig" "$f"
done

# 사전 압축본(.gz/.br)은 빌드 시점 그대로라 stale — 정적 서버가 plain .js 대신 응답하면 패치 무효.
# 삭제해서 .js 로 fallback 시킴 (express 가 필요 시 on-the-fly gzip).
rm -f "$ASSETS"/locales.*.js.gz "$ASSETS"/locales.*.js.br \
      "$ASSETS"/index.*.js.gz   "$ASSETS"/index.*.js.br

python3 "$PATCH_PY" "$ASSETS" || echo "warn: librechat-patch.py 실패 — 브랜딩/메뉴 패치 미적용" >&2

exec "$@"
