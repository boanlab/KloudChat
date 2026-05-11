# 기여 가이드

KloudChat 은 온프레미스 AI 플랫폼을 위한 오픈소스 프로젝트입니다. 버그 리포트, 기능 제안, 문서 개선, 코드 기여 모두 환영합니다.

## 시작 전 확인

- [README](README.md) — 지원 환경 매트릭스
- [설치 가이드](getting-started/installation.md)
- [아키텍처 상세](docs/overview.md)

## 버그 리포트

1. Issues 탭에서 기존 이슈를 먼저 검색합니다.
2. 없으면 새 이슈를 생성하고 다음을 포함합니다:
   - 환경 (OS·아키텍처·GPU): `./scripts/deploy.sh ps` 출력 첫 줄에 표시됩니다.
   - 재현 단계
   - 기대 동작 vs 실제 동작
   - `./scripts/deploy.sh ps` 출력
   - 관련 서비스 로그: `./scripts/deploy.sh logs <서비스명>`

## Pull Request

```bash
# 1. 포크 후 클론
git clone https://github.com/<your-username>/KloudChat.git
cd KloudChat

# 2. 브랜치 생성
git checkout -b feat/my-feature       # 새 기능
git checkout -b fix/issue-123         # 버그 수정

# 3. 변경 후 커밋
git add <파일>
git commit -m "feat: 기능 설명"

# 4. 푸시 + PR 생성
git push origin feat/my-feature
```

가능하면 두 환경 이상에서 테스트해 주세요 (예: Linux x86_64 + Mac, 또는 Linux x86_64 + DGX Spark).

## 커밋 메시지 규칙

```
<type>: <요약>

type:
  feat     새 기능
  fix      버그 수정
  docs     문서
  refactor 리팩터링
  chore    빌드·설정 변경
```

예시:

```
feat: TTS 한국어 voice 추가 (xtts_v2 korean)
fix: litellm-config.yaml gemma3:27b 들여쓰기 오류 수정
docs: GPU 메모리 가이드 Whisper medium 항목 추가
chore: install-ollama.sh macOS 분기 추가
```

## 기여 가능 영역

| 영역 | 내용 |
|---|---|
| `docker-compose*.yml` | 서비스 추가·수정, healthcheck 개선 |
| `litellm-config.yaml` | 모델 추가, 예산 정책 |
| `scripts/` | 관리 스크립트 기능 추가 |
| `scripts/lib/platform.sh` | OS·하드웨어 감지 헬퍼 보강 (FreeBSD / WSL 등) |
| `Dockerfile.*` | 파일 형식 / 폰트 / 업스트림 patch 추가 |
| `docs/`, `getting-started/` | 문서 오탈자, 환경별 가이드 보강 |

## 로컬 테스트

```bash
# 1. 정적 검사
shellcheck scripts/*.sh scripts/lib/*.sh
python3 -c "import yaml; yaml.safe_load(open('litellm-config.yaml'))" && echo OK
python3 -c "import yaml; yaml.safe_load(open('librechat.yaml'))"      && echo OK

# 2. compose 파일 유효성
./scripts/deploy.sh config >/dev/null && echo "compose OK"

# 3. 환경 감지 확인 (어떤 분기로 떨어지는지 출력)
source scripts/lib/platform.sh && platform_summary
./scripts/deploy.sh ps | head -1   # 첫 줄에 환경 분기 사유가 표시됨

# 4. 실제 기동 (PR 머지 전 권장)
./scripts/deploy.sh up -d
./scripts/deploy.sh ps
```

## 코드 스타일

### Shell 스크립트
- `#!/usr/bin/env bash` + `set -euo pipefail` 선두 필수
- OS·아키텍처 분기는 `scripts/lib/platform.sh` 헬퍼를 사용 (직접 `uname` 비교 지양)
- 함수명: `snake_case`
- 변수: 지역 변수는 `local` 선언
- 외부 도구 호출 전 `command -v <도구>` 로 존재 확인

### YAML
- 들여쓰기 2 스페이스
- 문자열 값은 따옴표 명시

### Markdown
- 제목은 `#` 계층 구조 준수
- 코드 블록에 언어 명시 (` ```bash `, ` ```yaml `)
- 환경 의존 기능은 적용 범위를 명시 (예: "Linux + amd64 + NVIDIA GPU 전용")

## 라이선스

KloudChat 은 [MIT 라이선스](LICENSE) 하에 배포됩니다. 기여하신 코드는 동일 라이선스로 공개됨에 동의하는 것으로 간주됩니다.
