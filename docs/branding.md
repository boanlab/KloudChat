# 브랜딩 커스터마이징

> 이 문서: 로고 / 파비콘 / PWA 아이콘 / 채팅 엔드포인트 아이콘 교체법. LibreChat 빌드 자산을 bind-mount 로 덮어쓰는 방식.

## 교체 가능한 자산

`branding/` 의 7개 파일을 교체한 뒤 LibreChat 컨테이너 재시작하면 반영됩니다. `docker-compose.yml` 의 LibreChat 서비스 volumes 가 각 파일을 컨테이너 안 빌드 경로에 read-only bind-mount.

| 파일 | 컨테이너 안 경로 | 용도 |
|---|---|---|
| `logo.svg` | `/app/client/dist/assets/logo.svg` | 사이드바 좌상단 로고 |
| `favicon-16x16.png` | `/app/client/dist/assets/favicon-16x16.png` | 브라우저 탭 (작은) |
| `favicon-32x32.png` | `/app/client/dist/assets/favicon-32x32.png` | 브라우저 탭 (큰) |
| `apple-touch-icon-180x180.png` | `/app/client/dist/assets/apple-touch-icon-180x180.png` | iOS 홈화면 |
| `icon-192x192.png` | `/app/client/dist/assets/icon-192x192.png` | PWA 설치 아이콘 |
| `maskable-icon.png` | `/app/client/dist/assets/maskable-icon.png` | PWA maskable (Android) |
| `endpoint-icon.png` | `/app/client/public/images/endpoint-icon.png` | 채팅 메시지 옆 AI 아바타 |

`endpoint-icon.png` 는 `librechat.yaml` 의 `endpoints.custom[].iconURL: "/images/endpoint-icon.png"` 가 참조.

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

| 자산 | 크기 / 포맷 | 비고 |
|---|---|---|
| `logo.svg` | 임의 viewBox, 가로 ~120px 보임 | 단색 vs 그라데이션 자유. 다크모드 동작 고려 (currentColor 권장) |
| `favicon-16x16.png` | 16×16 PNG | 단순한 형태 |
| `favicon-32x32.png` | 32×32 PNG | 동 |
| `apple-touch-icon-180x180.png` | 180×180 PNG | 모서리 둥글지 않게 (iOS 가 마스킹) |
| `icon-192x192.png` | 192×192 PNG | 투명 배경 OK |
| `maskable-icon.png` | 512×512 PNG | 안전 영역 (중앙 80%) 안에 핵심 요소 — Android 가 다양한 모양으로 마스킹 |
| `endpoint-icon.png` | 128×128 ~ 256×256 PNG | 채팅 말풍선 옆에 작게 표시 |

## 같이 바꾸면 좋은 것

| 항목 | 위치 |
|---|---|
| 브라우저 탭 + 좌상단 타이틀 | `.env` 의 `APP_TITLE` |
| 로그인 페이지 헤딩 | `.env` 의 `WELCOME_BACK_MESSAGE` (startup 시 LibreChat 번들 i18n 치환) |
| 회원가입 페이지 헤딩 | `.env` 의 `SIGNUP_HEADER` (동일 메커니즘) |
| 우상단 "Help & FAQ" 메뉴 숨김 | `.env` 의 `HELP_AND_FAQ_URL=/` (기본). URL 채우면 표시됨 |
| 채팅 홈 타글라인 ("모두를 위한 AI...") | `librechat.yaml` 의 `interface.customWelcome` (빈 문자열로 숨김) |
| 채팅창 하단 푸터 ("LibreChat v0.8.5 - ...") | `.env` 의 `CUSTOM_FOOTER` (공백 1개로 숨김. LibreChat 은 yaml 아닌 env 에서 읽음) |
| 엔드포인트 표시 이름 | `librechat.yaml` 의 `endpoints.custom[].modelDisplayLabel` |
| 언어 셀렉터 옵션 | `scripts/librechat-patch.py` 가 startup 시 ko-KR + en-US + auto 만 노출 — 다른 언어 추가/제거는 스크립트 수정 |

## 트러블슈팅

| 증상 | 해결 |
|---|---|
| 교체했는데 옛 이미지 그대로 | 브라우저 캐시. 강력 새로고침 (Ctrl+Shift+R) 또는 시크릿 창 |
| LibreChat 컨테이너 시작 실패 | `branding/<파일>` 이 없으면 bind-mount 실패. `docker compose logs librechat` 확인 후 누락 파일 보충 |
| PWA 설치 후 아이콘 변경 안 됨 | PWA 캐시. 앱 삭제 후 재설치 |
| LibreChat 업그레이드 후 깨짐 | LibreChat 이 자산 경로/이름 변경 가능. `docker exec LibreChat ls /app/client/dist/assets/` 로 새 이름 확인 → `docker-compose.yml` 의 bind-mount target 수정 |
