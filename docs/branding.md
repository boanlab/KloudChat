# 브랜딩 커스터마이징

> 이 문서: 로고 / 파비콘 / PWA 아이콘 / 채팅 엔드포인트 아이콘 교체법. LibreChat 빌드 자산을 bind-mount 로 덮어쓰는 방식.

## 교체 가능한 자산

- **`branding/` 의 7개 파일** 교체 후 LibreChat 컨테이너 재시작 시 반영.
- 각 파일은 `docker-compose.yml` 의 LibreChat 서비스 volumes 로 컨테이너 내 빌드 경로에 read-only **bind-mount**.

| 파일 | 용도 |
|---|---|
| `logo.svg` | 사이드바 좌상단 로고 |
| `favicon-16x16.png` | 브라우저 탭 (작은) |
| `favicon-32x32.png` | 브라우저 탭 (큰) |
| `apple-touch-icon-180x180.png` | iOS 홈화면 |
| `icon-192x192.png` | PWA 설치 아이콘 |
| `maskable-icon.png` | PWA maskable (Android) |
| `endpoint-icon.png` | 채팅 메시지 옆 AI 아바타 |

**마운트 경로:**

| 파일 | 컨테이너 내 경로 |
|---|---|
| `logo.svg`, `favicon-*`, `apple-touch-icon-*`, `icon-*`, `maskable-icon.png` | `/app/client/dist/assets/<파일명>` |
| `endpoint-icon.png` | `/app/client/public/images/endpoint-icon.png` |

- `endpoint-icon.png` 는 `librechat.yaml` 의 `endpoints.custom[].iconURL: "/images/endpoint-icon.png"` 가 참조.

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

- **`logo.svg`**: 임의 viewBox 가능 — 가로 ~120px 영역에 표시. 단색 / 그라데이션 자유. 다크모드 호환 위해 `currentColor` 사용 권장.
- **`favicon-*`**: 작은 크기에 맞춰 단순한 형태 권장.
- **`apple-touch-icon`**: 모서리 둥글지 않게 (iOS 가 자동 마스킹).
- **`icon-192x192`**: PWA 설치 아이콘 — 투명 배경 OK.
- **`maskable-icon`**: Android 가 다양한 모양으로 마스킹 — 핵심 요소를 안전 영역 (중앙 80%) 안에 배치.
- **`endpoint-icon`**: 채팅 말풍선 옆에 작게 표시.

## 같이 바꾸면 좋은 것

| 항목 | 위치 |
|---|---|
| 브라우저 탭 + 좌상단 타이틀 | `.env` `APP_TITLE` |
| 로그인 페이지 헤딩 | `.env` `WELCOME_BACK_MESSAGE` |
| Help & FAQ 메뉴 | `.env` `HELP_AND_FAQ_URL` |
| 채팅창 하단 푸터 | `.env` `CUSTOM_FOOTER` |
| 엔드포인트 표시 이름 | `librechat.yaml` `endpoints.custom[].modelDisplayLabel` |
| 언어 셀렉터 옵션 | `scripts/librechat-patch.py` |

- **`WELCOME_BACK_MESSAGE`**: startup 시 LibreChat 번들 i18n 치환 방식.
- **`HELP_AND_FAQ_URL`**: `/` (기본) 이면 메뉴 숨김, URL 채우면 표시.
- **`CUSTOM_FOOTER`**: LibreChat 이 yaml 아닌 env 에서만 읽음 — 공백 1개로 사실상 숨김 가능.
- **언어 셀렉터**: patch 가 startup 시 `ko-KR` + `en-US` + `auto` 만 노출하도록 잘라냄 — 추가/제거는 스크립트 수정.
