# 기여 가이드

## 기여 방법

버그 리포트, 기능 제안, 문서 개선, 코드 기여 모두 환영합니다.

### 버그 리포트

1. Issues 탭에서 기존 이슈를 먼저 검색합니다.
2. 없으면 새 이슈를 생성하고 다음을 포함합니다:
   - 재현 단계
   - 기대 동작 vs 실제 동작
   - `docker compose ps` 출력
   - 관련 서비스 로그: `docker compose logs <서비스명>`

### Pull Request

```bash
# 1. 포크 후 클론
git clone https://github.com/<your-username>/KloudChat.git
cd KloudChat

# 2. 브랜치 생성
git checkout -b feat/my-feature
# 또는
git checkout -b fix/issue-123

# 3. 변경 후 커밋
git add <파일>
git commit -m "feat: 기능 설명"

# 4. 푸시
git push origin feat/my-feature

# 5. GitHub에서 PR 생성
```

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
fix: litellm-config.yaml gemma4-26b 들여쓰기 오류 수정
docs: GPU 메모리 가이드 Whisper medium 항목 추가
```

## 기여 가능 영역

| 영역 | 내용 |
|---|---|
| `docker-compose.yml` | 서비스 추가·수정, healthcheck 개선 |
| `litellm-config.yaml` | 모델 추가, 예산 정책 |
| `scripts/` | 관리 스크립트 기능 추가 |
| `Dockerfile.rag` | 파일 형식 지원 추가 |
| `docs/` | 문서 오탈자, 내용 보강 |

## 로컬 테스트

```bash
# 설정 파일 YAML 유효성 검사
python3 -c "import yaml; yaml.safe_load(open('litellm-config.yaml'))" && echo "OK"
python3 -c "import yaml; yaml.safe_load(open('librechat.yaml'))" && echo "OK"

# 스크립트 문법 검사 (shellcheck 필요)
shellcheck scripts/manage.sh
shellcheck scripts/lib/common.sh
shellcheck scripts/deploy.sh

# 서비스 기동 테스트
docker compose config   # compose 파일 유효성 검사
docker compose up -d
docker compose ps
```

## 코드 스타일

**Shell 스크립트**
- `set -euo pipefail` 선두 필수
- 함수명: `snake_case`
- 변수: 지역 변수는 `local` 선언

**YAML**
- 들여쓰기 2 스페이스
- 문자열 값은 따옴표 명시

**문서 (Markdown)**
- 제목은 `#` 계층 구조 준수
- 코드 블록에 언어 명시 (` ```bash `, ` ```yaml `)
