#!/bin/sh
# LibreChat 컨테이너 startup wrapper — 빌드 번들 in-place 패치.
# 실제 패치 항목은 librechat-patch.py 의 module docstring 참조 (i18n / 언어 셀렉터 /
# Upload-to-Provider 숨김 / agent builder 단순화·기본 모델 / 에러 wrapper 제거 등).
# .orig 백업본 복원 후 재패치라 멱등.
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
# LibreChat 번들엔 og: 태그 없어 스크래퍼가 이 둘로 fallback → og 태그도 함께 주입.
# .orig 복원 후 재패치라 멱등. 정적 파일이라 assets 글로브와 별개 처리.
KC_DESC="KloudChat - An open source chat application powered by BoanLab @ DKU"

INDEX=/app/client/dist/index.html
if [ -f "$INDEX" ]; then
  [ -f "${INDEX}.orig" ] || cp "$INDEX" "${INDEX}.orig"
  cp "${INDEX}.orig" "$INDEX"
  sed -i \
    -e "s#<title>LibreChat</title>#<title>KloudChat</title>#" \
    -e "s#<meta name=\"description\" content=\"[^\"]*\"#<meta name=\"description\" content=\"${KC_DESC}\"#" \
    -e "/<title>KloudChat<\/title>/a\\    <meta property=\"og:site_name\" content=\"KloudChat\" />\n    <meta property=\"og:title\" content=\"KloudChat\" />\n    <meta property=\"og:description\" content=\"${KC_DESC}\" />" \
    "$INDEX" || echo "warn: index.html 브랜딩 패치 실패" >&2
fi

# PWA manifest 브랜딩 — 모바일 홈화면/standalone 앱 이름(name/short_name)이 헤더로 노출.
# 기본 "LibreChat" → "KloudChat" + 빈 description 채움. .orig 복원 후 재패치라 멱등.
# 사전 압축본(.gz/.br) 은 stale → 삭제(정적 서버가 plain 으로 fallback).
MANIFEST=/app/client/dist/manifest.webmanifest
if [ -f "$MANIFEST" ]; then
  [ -f "${MANIFEST}.orig" ] || cp "$MANIFEST" "${MANIFEST}.orig"
  cp "${MANIFEST}.orig" "$MANIFEST"
  sed -i \
    -e 's#"name":"LibreChat"#"name":"KloudChat"#' \
    -e 's#"short_name":"LibreChat"#"short_name":"KloudChat"#' \
    -e "s#\"description\":\"\"#\"description\":\"${KC_DESC}\"#" \
    "$MANIFEST" || echo "warn: manifest 브랜딩 패치 실패" >&2
  rm -f "${MANIFEST}.gz" "${MANIFEST}.br"
fi

# 사전 압축본(.gz/.br)은 빌드 시점 그대로라 stale — 정적 서버가 plain .js 대신 응답하면 패치 무효.
# 삭제 → .js 로 fallback (express 가 필요 시 on-the-fly gzip).
rm -f "$ASSETS"/locales.*.js.gz "$ASSETS"/locales.*.js.br \
      "$ASSETS"/index.*.js.gz   "$ASSETS"/index.*.js.br \
      "$ASSETS"/AccountSettings.*.js.gz "$ASSETS"/AccountSettings.*.js.br

python3 "$PATCH_PY" "$ASSETS" || echo "warn: librechat-patch.py 실패 — 브랜딩/메뉴 패치 미적용" >&2

# 서버 패치 (런타임): 비-ADMIN 이 만든 agent 는 category=my_agents 강제 — Agent Store
# 의 'My Agents' 카테고리(manage.sh 시딩, order 5)로 자동 분류. ADMIN 은 요청값 유지
# (자유 선택). createAgentHandler 의 `agentData.author = userId;` 직후 1줄 삽입.
# 마커(KLOUDCHAT_FORCE_CATEGORY) 체크로 멱등 — 재시작 시 중복 삽입 안 함.
V1=/app/api/server/controllers/agents/v1.js
[ -f "$V1" ] && python3 - "$V1" <<'PY' || echo "warn: v1.js category 패치 스킵 (파일 없음/실패)" >&2
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "KLOUDCHAT_FORCE_CATEGORY" in s:
    sys.exit(0)  # 이미 패치됨 (멱등)
anchor = "agentData.author = userId;"
n = s.count(anchor)
if n != 1:
    sys.stderr.write(f"warn: v1.js anchor {n}건 (기대 1) — 패턴 stale, category 패치 미적용\n")
    sys.exit(0)
inject = (anchor +
          "\n    if (req.user?.role !== 'ADMIN') { agentData.category = 'my_agents'; }"
          " // KLOUDCHAT_FORCE_CATEGORY")
open(p, "w", encoding="utf-8").write(s.replace(anchor, inject, 1))
PY

exec "$@"
