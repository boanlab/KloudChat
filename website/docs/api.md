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

키로 보낸 요청은 몇 분 안에 사용량 화면과 이번 달 크레딧에 반영되며, 화면별 사용량에서는 **API 키** 항목으로 구분됩니다. 무료 모델 요청도 요청 수로 집계됩니다. 계정에 설정된 허용 모델 제한이 키에도 동일하게 적용됩니다.

## 추론 단계

추론하는 모델은 생각하는 양을 단계로 정할 수 있습니다. OpenAI 형식에서는 `reasoning_effort`, Responses API에서는 `reasoning.effort`, Anthropic 형식에서는 `thinking` 을 보냅니다.

```bash
curl "$OPENAI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $OPENAI_API_KEY" -H "Content-Type: application/json" \
  -d '{"model": "z-ai/glm-5.3-flash", "reasoning_effort": "low",
       "messages": [{"role": "user", "content": "..."}]}'
```

모델마다 받는 단계가 다릅니다. `GET /llm/v1/models` 의 각 모델에 붙은 `reasoning` 을 확인하세요.

```json
{"id": "z-ai/glm-5.3-flash", "reasoning": {"efforts": ["low", "high", "max"], "default": "max", "mandatory": true}}
```

| 필드 | 뜻 |
|---|---|
| `efforts` | 받는 단계, 낮은 것부터 |
| `default` | 단계를 보내지 않았을 때의 단계 |
| `mandatory` | `true` 이면 추론을 끌 수 없습니다. 끄도록 보내면(`none`, `thinking` 의 `disabled`) 가장 낮은 단계로 바꿔 보냅니다. |

`reasoning` 이 `null` 인 모델은 단계를 받지 않으며, 보낸 단계는 무시됩니다. 목록에 없는 단계를 보내면 가까운 단계로 처리됩니다. 단계가 높을수록 출력 토큰과 크레딧이 늘어납니다.

## Super Agent 모델

`kloudchat/super-agent` 를 모델 이름으로 보내면 질문에 맞는 모델과 추론 단계를 서버가 골라 답합니다. `kloudchat/super-agent-strict-local` 은 내부 모델로만 답합니다. 채팅 한 번(요청 하나, 답 하나)에만 쓰며, 보고서와 슬라이드는 지원하지 않습니다. 두 id 는 설정 → **API 키** 와 `GET /llm/v1/models` 에 나옵니다.

```bash
curl "$OPENAI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $OPENAI_API_KEY" -H "Content-Type: application/json" \
  -d '{"model": "kloudchat/super-agent", "messages": [{"role": "user", "content": "안녕하세요"}]}'
```

```python
from openai import OpenAI

client = OpenAI(base_url="https://<서비스 주소>/llm/v1", api_key="<내 API 키>")
reply = client.chat.completions.create(
    model="kloudchat/super-agent",
    messages=[{"role": "user", "content": "안녕하세요"}],
)
print(reply.choices[0].message.content)
```

응답의 `model` 은 보낸 id 그대로이고, 실제로 답한 모델은 `kloudchat.route` 에 있습니다. 스트리밍에서는 usage 청크에 한 번 들어가며, 응답 헤더 `X-KloudChat-Route` 에도 같은 내용이 있습니다.

```json
{"model": "kloudchat/super-agent",
 "kloudchat": {"route": {"model": "local/qwen3.8-flash", "row": "simple", "effort": "none", "strictLocal": false, "fallback": false}}}
```

| 필드 | 뜻 |
|---|---|
| `model` | 실제로 답한 모델 |
| `row` | 고른 규칙 표의 행 |
| `effort` | 적용한 추론 단계 |
| `strictLocal` | 내부 모델이면 `true` |
| `fallback` | 규칙의 모델 대신 다른 모델로 갔으면 `true` |

`tools` 를 보내면 함수 호출을 받는 모델로만 보냅니다. 개인정보가 들어 있으면 내부 모델로 보내거나 가려서 보냅니다. `-strict-local` 에 쓸 내부 모델이 없으면 `503 strict_local_unavailable` 입니다. 관리자가 끄면 두 id 는 `404 model_not_found` 입니다.

## AI 에이전트 연동

![AI 에이전트 연동](/img/guide/agent-setup.png)

Claude Code, Codex 등의 코딩 에이전트를 기관 모델로 연결하는 방법을 안내합니다. 키를 발급하고 모델을 선택한 뒤, 운영체제별 탭(Linux · macOS · Windows)에서 명령을 복사합니다.

| 도구 | 환경변수 |
|---|---|
| Claude Code | `ANTHROPIC_BASE_URL=https://<서비스 주소>/llm`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL` |
| Codex · OpenAI 호환 | `OPENAI_BASE_URL=https://<서비스 주소>/llm/v1`, `OPENAI_API_KEY`, `OPENAI_MODEL` |

Anthropic 형식 주소에는 `/v1` 이 없고 OpenAI 형식 주소에는 포함된다는 점에 유의하세요.

:::tip 환경변수 범위
`export` 와 `$env:` 는 해당 터미널 세션에서만 유효합니다. 지속적으로 사용하려면 `~/.bashrc` 또는 `~/.zshrc` 에 등록하세요. 다른 프로젝트에서 별도의 OpenAI 키를 사용하는 경우 값이 충돌하지 않도록 터미널을 구분해 사용하세요.
:::

## 키 관리

- 키 값은 발급 시 한 번만 표시됩니다. 분실한 경우 새로 발급하세요.
- 사용하지 않는 키는 설정 → API 키에서 폐기하세요.
- 키를 대화창이나 문서에 입력하지 마세요.
