---
title: API와 코딩 도구 연동
---

# API와 코딩 도구 연동

기관이 운영하는 모델을 사용자의 코드나 코딩 도구에서 호출할 수 있습니다. 계정 메뉴에 **API 연동**과 **AI 에이전트 연동** 두 화면이 있습니다.

## API 연동

![API 연동](/img/guide/api-setup.png)

1. 설정 → **API 키**에서 키를 발급합니다.
2. 화면에 제시된 예제를 참고합니다. OpenAI SDK, 스트리밍, LiteLLM SDK(`openai/` 접두사), curl, 임베딩 예제가 제공됩니다.
3. 모델 이름은 **모델 목록에 표시된 id** 를 그대로 사용합니다.

```bash
# Linux / macOS — OpenAI 호환
export OPENAI_BASE_URL="https://<서비스 주소>/llm/v1"
export OPENAI_API_KEY="<내 API 키>"
```

```powershell
# Windows PowerShell
$env:OPENAI_BASE_URL = "https://<서비스 주소>/llm/v1"
$env:OPENAI_API_KEY = "<내 API 키>"
```

사용량은 계정 한도에 합산되며 사용량 화면의 **API 키별** 항목에 별도로 집계됩니다. 계정에 설정된 허용 모델 제한이 키에도 동일하게 적용됩니다.

## AI 에이전트 연동

![AI 에이전트 연동](/img/guide/agent-setup.png)

Claude Code, Codex 등의 코딩 에이전트를 기관 모델로 연결하는 방법을 안내합니다. 키를 발급하고 모델을 선택한 뒤, 운영체제별 탭(Linux · macOS · Windows)에서 명령을 복사합니다.

| 도구 | 환경변수 |
|---|---|
| Claude Code | `ANTHROPIC_BASE_URL=https://<서비스 주소>/llm`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL` |
| Codex · OpenAI 호환 | `OPENAI_BASE_URL=https://<서비스 주소>/llm/v1`, `OPENAI_API_KEY`, `OPENAI_MODEL` |

Anthropic 형식 주소에는 `/v1` 이 없고 OpenAI 형식 주소에는 포함된다는 점에 유의하십시오.

:::tip 환경변수 범위
`export` 와 `$env:` 는 해당 터미널 세션에서만 유효합니다. 지속적으로 사용하려면 `~/.bashrc` 또는 `~/.zshrc` 에 등록하십시오. 다른 프로젝트에서 별도의 OpenAI 키를 사용하는 경우 값이 충돌하지 않도록 터미널을 구분해 사용하십시오.
:::

## 키 관리

- 키 값은 발급 시 한 번만 표시됩니다. 분실한 경우 새로 발급하십시오.
- 사용하지 않는 키는 설정 → API 키에서 폐기하십시오.
- 키를 대화창이나 문서에 입력하지 마십시오.
