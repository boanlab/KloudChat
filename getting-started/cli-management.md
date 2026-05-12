# CLI 관리 — 팀·사용자·키

LiteLLM 의 팀, 사용자, 가상 키를 UI 없이 CLI 로 관리합니다. 모든 명령은 `scripts/manage.sh` 를 통해 실행합니다.

## 사전 설정

`.env` 의 `LITELLM_MASTER_KEY` 가 자동으로 사용되므로 별도 설정 없이 사용 가능합니다.

```bash
# 의존성 확인
jq --version   # jq 필수
curl --version
```

## 팀 관리

```bash
# 팀 생성
./scripts/manage.sh team create \
  --alias research \
  --budget 100 \
  --tpm 50000 \
  --rpm 500 \
  --models "ollama/*"

# 팀 목록
./scripts/manage.sh team list

# 팀 삭제
./scripts/manage.sh team delete --id <team_id>
```

| 옵션 | 설명 | 기본값 |
|---|---|---|
| `--alias` | 팀 이름 | 필수 |
| `--budget` | 월 예산 ($) | 9999 |
| `--duration` | 예산 주기 | 1mo |
| `--tpm` | 분당 토큰 상한 | 100000 |
| `--rpm` | 분당 요청 상한 | 500 |
| `--models` | 허용 모델 (쉼표 구분) | `ollama/*` |

## 사용자 관리

### LiteLLM 사용자만 (단독 모드)

```bash
# 사용자 생성 + 팀 추가
./scripts/manage.sh user create \
  --id alice@example.com \
  --team research \
  --budget 25

# 사용자 목록
./scripts/manage.sh user list

# 사용자 삭제
./scripts/manage.sh user delete --id alice@example.com
```

### LibreChat 사용자 + LiteLLM 사용자 + 키 한 번에

`--name`, `--username`, `--password` 셋이 함께 제공되면 다음 작업이 자동으로 묶여 실행됩니다.

1. `docker exec LibreChat npm run create-user` 로 LibreChat 로그인 계정 생성
2. LiteLLM 사용자 생성 + 팀 추가 (위와 동일)
3. LiteLLM 키 자동 발급 (alias = `<username>-key`)
4. 발급된 키를 LibreChat 의 `keys` 컬렉션에 자동 등록 → 사용자 첫 채팅 시 API Key 입력 단계 생략
5. 두 개의 기본 agent 자동 생성 + default preset 지정 → 로그인 후 바로 채팅 가능
   - `KloudChat (Gemma4:26b)` (default) — 일반 대화, `execute_code` / `file_search` / `web_search`
   - `KloudChat (Qwen3.5:35b)` — `stable-diffusion` 포함 풀 스택 (이미지 생성용)
   - 분리 이유: gemma4 가 hyphen 함수명(`stable-diffusion`)을 OpenAI tool_calls 로 emit 못 해 도구 실행이 깨지므로, 이미지 생성은 qwen3.5 빌드로 라우팅

```bash
./scripts/manage.sh user create \
  --id alice@example.com \
  --name 'Alice' --username alice --password 'pw12345678' \
  --team research \
  --budget 100
```

출력에 `KEY: sk-...` 가 포함됩니다. 이 키는 LibreChat 에 이미 등록되어 있으므로 사용자에게 따로 전달할 필요 없이 로그인 정보 (이메일·비밀번호) 만 공유하면 됩니다.

| 옵션 | 설명 | 비고 |
|---|---|---|
| `--name` | LibreChat 표시명 | 셋 다 함께 제공 시 활성화 |
| `--username` | LibreChat 로그인 ID | 동일 |
| `--password` | LibreChat 비밀번호 (8자+) | 동일 |

## 가상 키 발급

각 사용자에게 발급하는 키는 LiteLLM API 접근에 사용됩니다. Claude Code, 직접 API 호출 등에 활용합니다.

```bash
# 사용자 키 발급
./scripts/manage.sh key issue \
  --user alice@example.com \
  --team research \
  --alias alice-key

# 서비스 키 발급 (LibreChat 등 내부 서비스용)
./scripts/manage.sh key issue \
  --service myservice \
  --budget 9999

# 키 목록 조회
./scripts/manage.sh key list
./scripts/manage.sh key list --user alice@example.com

# 키 폐기
./scripts/manage.sh key revoke --key sk-...
```

## 일괄 등록 예시

```bash
# 팀 생성
./scripts/manage.sh team create --alias research --budget 100 --models "ollama/*"
./scripts/manage.sh team create --alias default  --budget  50 --models "ollama/*"

# 사용자 등록
./scripts/manage.sh user create --id alice@example.com --team research --budget 20
./scripts/manage.sh user create --id bob@example.com   --team default  --budget 10

# 키 발급
./scripts/manage.sh key issue --user alice@example.com --team research
./scripts/manage.sh key issue --user bob@example.com   --team default
```

## Claude Code 로컬 연결

발급받은 키 (또는 마스터 키) 로 Claude Code 를 로컬 모델에 연결합니다.

```bash
# 마스터 키로 실행 (.env 에서 자동 읽음)
./scripts/claude-local.sh

# 특정 키로 실행
ANTHROPIC_AUTH_TOKEN=sk-... ./scripts/claude-local.sh
```

사용 모델은 `scripts/claude-local.sh` 에서 변경할 수 있습니다.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `ANTHROPIC_MODEL` | `ollama/qwen3-coder-next:q8_0` | 기본 모델 |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | `ollama/qwen3-coder-next:q4_K_M` | Sonnet 대체 |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | `ollama/gemma4:26b` | Haiku 대체 |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | `ollama/qwen3-coder-next:q8_0` | Opus 대체 |
