#!/bin/sh
# LibreChat 컨테이너 startup wrapper — 빌드 번들 in-place 패치.
# 실제 패치 항목은 librechat-patch.py 의 module docstring 참조 (i18n / 언어 셀렉터 /
# Upload-to-Provider 숨김 / agent builder 단순화·기본 모델 / 에러 wrapper 제거 등).
# .orig 백업본에서 복원 후 다시 패치하므로 멱등.
set -e

PATCH_PY="$(dirname "$0")/librechat-patch.py"
ASSETS=/app/client/dist/assets

# .orig 백업(최초 1회) + 매 startup 복원.
for f in "$ASSETS"/locales.*.js "$ASSETS"/index.*.js "$ASSETS"/AccountSettings.*.js; do
  case "$f" in *'*'*) continue ;; esac   # no-match 글로브 그대로 통과 시 스킵
  [ -f "${f}.orig" ] || cp "$f" "${f}.orig"
  cp "${f}.orig" "$f"
done

# index.html 브랜딩 — 링크 미리보기 카드(<title> + meta description)를 KloudChat 으로.
# LibreChat 번들엔 og: 태그가 없어 스크래퍼가 이 둘로 fallback 하므로 함께 og 태그도 주입.
# .orig 복원 후 재패치라 멱등. 정적 파일이므로 assets 글로브와 별개로 처리.
INDEX=/app/client/dist/index.html
if [ -f "$INDEX" ]; then
  [ -f "${INDEX}.orig" ] || cp "$INDEX" "${INDEX}.orig"
  cp "${INDEX}.orig" "$INDEX"
  KC_DESC="KloudChat - An open source chat application powered by BoanLab @ DKU"
  sed -i \
    -e "s#<title>LibreChat</title>#<title>KloudChat</title>#" \
    -e "s#<meta name=\"description\" content=\"[^\"]*\"#<meta name=\"description\" content=\"${KC_DESC}\"#" \
    -e "/<title>KloudChat<\/title>/a\\    <meta property=\"og:site_name\" content=\"KloudChat\" />\n    <meta property=\"og:title\" content=\"KloudChat\" />\n    <meta property=\"og:description\" content=\"${KC_DESC}\" />" \
    "$INDEX" || echo "warn: index.html 브랜딩 패치 실패" >&2
fi

# 사전 압축본(.gz/.br)은 빌드 시점 그대로라 stale — 정적 서버가 plain .js 대신 응답하면 패치 무효.
# 삭제해서 .js 로 fallback 시킴 (express 가 필요 시 on-the-fly gzip).
rm -f "$ASSETS"/locales.*.js.gz "$ASSETS"/locales.*.js.br \
      "$ASSETS"/index.*.js.gz   "$ASSETS"/index.*.js.br \
      "$ASSETS"/AccountSettings.*.js.gz "$ASSETS"/AccountSettings.*.js.br

python3 "$PATCH_PY" "$ASSETS" || echo "warn: librechat-patch.py 실패 — 브랜딩/메뉴 패치 미적용" >&2

exec "$@"
