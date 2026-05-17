# 브랜딩 커스터마이징

> 이 문서: 로고 / 파비콘 / PWA 아이콘 / 채팅 엔드포인트 아이콘 교체법. LibreChat 빌드 자산을 bind-mount 로 덮어쓰는 방식.

## 교체 가능한 자산

`branding/` 의 7개 파일을 교체한 뒤 LibreChat 컨테이너 재시작하면 반영됩니다. `docker-compose.yml` 의 LibreChat 서비스 volumes 가 각 파일을 컨테이너 안 빌드 경로에 read-only bind-mount.

| 파일 | 용도 |
|---|---|
| `logo.svg` | 사이드바 좌상단 로고 |
| `favicon-16x16.png` | 브라우저 탭 (작은) |
| `favicon-32x32.png` | 브라우저 탭 (큰) |
| `apple-touch-icon-180x180.png` | iOS 홈화면 |
| `icon-192x192.png` | PWA 설치 아이콘 |
| `maskable-icon.png` | PWA maskable (Android) |
| `endpoint-icon.png` | 채팅 메시지 옆 AI 아바타 |

`logo.svg`, `favicon-*`, `apple-touch-icon-*`, `icon-*`, `maskable-icon.png` 은 `/app/client/dist/assets/<파일명>` 으로 마운트. `endpoint-icon.png` 는 `/app/client/public/images/endpoint-icon.png` — `librechat.yaml` 의 `endpoints.custom[].iconURL: "/images/endpoint-icon.png"` 가 참조.

## 교체 절차

```bash
# 1. 디자이너에게 받은 파일을 branding/ 에 같은 이름으로 덮어쓰기
cp ~/Downloads/my-logo.svg branding/logo.svg
cp ~/Downloads/my-favicon-32.png branding/favicon-32x32.png
# ... 필요한 만큼

# 2. LibreChat 컨테이너 재시작 (브라우저 캐시도 강력 새로고침 Ctrl+Shift+R)
docker compose restart librechat
```

## 권장 사양

| 자산 | 크기 |
|---|---|
| `logo.svg` | 가로 ~120px |
| `favicon-16x16.png` | 16×16 |
| `favicon-32x32.png` | 32×32 |
| `apple-touch-icon-180x180.png` | 180×180 |
| `icon-192x192.png` | 192×192 |
| `maskable-icon.png` | 512×512 |
| `endpoint-icon.png` | 128×128 ~ 256×256 |

`logo.svg` 는 임의 viewBox — 가로 ~120px 영역에 표시됩니다. 단색 / 그라데이션 자유, 다크모드 동작 고려 (currentColor 권장).

`favicon-*` 은 작은 크기에 맞춰 단순한 형태가 좋음. `apple-touch-icon` 은 모서리 둥글지 않게 (iOS 가 자동 마스킹). `icon-192x192` 는 PWA 설치 아이콘 — 투명 배경 OK. `maskable-icon` 은 Android 가 다양한 모양으로 마스킹하므로 핵심 요소를 안전 영역 (중앙 80%) 안에 배치. `endpoint-icon` 은 채팅 말풍선 옆에 작게 표시.

## 같이 바꾸면 좋은 것

| 항목 | 위치 |
|---|---|
| 브라우저 탭 + 좌상단 타이틀 | `.env` `APP_TITLE` |
| 로그인 페이지 헤딩 | `.env` `WELCOME_BACK_MESSAGE` |
| 회원가입 페이지 헤딩 | `.env` `SIGNUP_HEADER` |
| Help & FAQ 메뉴 | `.env` `HELP_AND_FAQ_URL` |
| 채팅 홈 타글라인 | `librechat.yaml` `interface.customWelcome` |
| 채팅창 하단 푸터 | `.env` `CUSTOM_FOOTER` |
| 엔드포인트 표시 이름 | `librechat.yaml` `endpoints.custom[].modelDisplayLabel` |
| 언어 셀렉터 옵션 | `scripts/librechat-patch.py` |

`WELCOME_BACK_MESSAGE` / `SIGNUP_HEADER` 는 startup 시 LibreChat 번들 i18n 을 치환하는 식. `HELP_AND_FAQ_URL=/` (기본) 이면 메뉴 자체가 숨겨지고, URL 을 채우면 표시됩니다. 타글라인은 빈 문자열로 숨김. `CUSTOM_FOOTER` 는 LibreChat 이 yaml 아닌 env 에서만 읽으며, 공백 1개로 사실상 숨김 가능. 언어 셀렉터는 patch 가 startup 시 ko-KR + en-US + auto 만 노출하도록 잘라냄 — 추가/제거는 스크립트 수정.

## 트러블슈팅

| 증상 | 원인 |
|---|---|
| 교체했는데 옛 이미지 | 브라우저 캐시 |
| 컨테이너 시작 실패 | bind-mount 대상 누락 |
| PWA 아이콘 그대로 | PWA 캐시 |
| 업그레이드 후 깨짐 | LibreChat 자산 경로 변경 |

**브라우저 캐시** — Ctrl+Shift+R 또는 시크릿 창.

**bind-mount 누락** — `branding/<파일>` 이 없으면 mount 실패. `docker compose logs librechat` 으로 어떤 파일인지 확인 후 보충.

**PWA 캐시** — 앱 삭제 후 재설치.

**자산 경로 변경** — LibreChat 업그레이드 시 자산 경로/이름이 바뀔 수 있음. `docker exec LibreChat ls /app/client/dist/assets/` 로 새 이름 확인 → `docker-compose.yml` 의 bind-mount target 수정.
