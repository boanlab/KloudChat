# 도구 (Tools)

> 이 문서: 에이전트가 실제로 호출할 수 있는 도구가 무엇이고 어디로 라우팅되는지. 모델별로 어떤 도구가 켜지고 꺼지는지(매트릭스). 새 도구 추가 절차. 모델 자체는 [모델 설정](models.md), 환경변수는 [env-reference](env-reference.md).

도구는 세 갈래:

- **Built-in** — LibreChat 이 자체 구현한 핵심 4개. 각자 별도 백엔드 컨테이너로 연결.
- **MCP servers** — `librechat.yaml` 의 `mcpServers` 에 stdio 로 등록한 보조 서버들.
- **이미지 백엔드** — `generate_image` 툴이 `model` arg 에 따라 ComfyUI 또는 외부 image 모델로 분기.

도구 부착은 `scripts/manage.sh create_default_agent_for_user` 에서 모델별로 결정됩니다. 사용자가 LibreChat UI 에서 직접 만든 에이전트는 건드리지 않습니다.

## Built-in 도구

| 도구 | 백엔드 |
|---|---|
| `execute_code` | `code-interpreter` 컨테이너 |
| `file_search` | `rag_api` 컨테이너 |
| `web_search` | `searxng` 컨테이너 |
| `generate_image` | `comfyui-shim` (→ ComfyUI 또는 외부 image 모델) |

`execute_code` 는 LibreCodeInterpreter Python 샌드박스. 모델이 emit 한 코드를 실행하고 결과·이미지·파일을 반환합니다. 30s 실행 제한 + 512 MB 메모리, 산출물은 MinIO 에 저장.

`file_search` 는 pgvector + MeiliSearch Hybrid Search. 사용자가 업로드한 파일을 bge-m3 임베딩으로 검색·발췌해서 응답에 인용. HWP / PDF / DOCX / MD 등 지원 (rag-patches 가 HWP 분기 추가).

`web_search` 는 SearXNG 인스턴스 고정 (`webSearch.searchProvider: "searxng"`). 모델이 의도적으로 웹 결과를 가져올 때만 호출.

`generate_image` 은 텍스트→이미지 또는 이미지 편집. `model` arg 에 따라 백엔드가 분기 — [이미지 백엔드](#이미지-백엔드) 참고.

이 4개는 `librechat.yaml.endpoints.agents.capabilities` 에 명시되어 있어야 DB-backed agent 가 쓸 수 있습니다. LibreChat 의 `resolveAgentCapabilities` 가 ephemeral agent 에만 default 폴백을 해서, 명시 안 하면 DB agent 는 전부 차단됩니다.

UI 토글 (`interface.runCode` / `fileSearch` / `webSearch` / `fileCitations`) 은 채팅창 입력란 옆 버튼의 표시 여부만 통제 — 에이전트 도구 권한과는 독립.

## MCP servers

| 서버 | 실행 |
|---|---|
| `fetch_url` | `uvx mcp-server-fetch` |
| `time` | `uvx mcp-server-time` |
| `math` | `uvx mcp-sympy` |
| `math_basic` | `uvx mcp-server-math` |
| `usage` | `uv run --script /app/mcp/usage.py` |
| `youtube` | `uv run --script /app/mcp/youtube.py` |

`librechat.yaml` 의 `mcpServers` 섹션에 stdio 자식 프로세스로 등록. 에이전트의 `tools` 배열에 `sys__all__sys_mcp_<servername>` 항목을 추가하면 그 서버가 노출하는 모든 tool 이 자동 부착. 첫 호출 시 uvx 가 패키지 다운로드 (30–60s 지연 정상, 이후 캐시).

**`fetch_url`** — 1 tool. URL → Markdown 변환. 전 에이전트 공통.

**`time`** — 2 tools. 현재 시간 / 타임존 변환. 전 에이전트 공통 ("오늘" 환각 방지).

**`math`** — 173 tools. sympy 심볼릭/수치 (미적분, 방정식, 행렬, 단위 변환). 큰 모델 전용 — schema 가 커서 작은 모델은 호출 emit 실패.

**`math_basic`** — 16 tools. 사칙연산 + power / sqrt / sum / product / compare. math 의 작은 모델용 대체.

**`usage`** — 2 tools. `my_usage(months_back)`, `budget_status()`. 사용자 본인의 LiteLLM virtual key 사용량/예산 조회 (월 단위). 응답에 다른 사용자 user_id 가 섞이면 `CrossUserDataError` 로 거부 (defensive). `mcp/usage.py` 가 PEP-723 inline deps 로 mcp + httpx 자동 설치.

**`youtube`** — 1 tool. `transcript(url, language?)`. YouTube 영상 텍스트 반환 — 자막 우선 (`youtube-transcript-api`), 자막 없으면 `yt-dlp` 로 audio 받아 whisper 전사 (`WHISPER_URL` 의 호스트 systemd 서비스 → 실패 시 LiteLLM `whisper-1` OR 폴백). `mcp/youtube.py` PEP-723 inline deps. `WHISPER_URL` 미설정 + OR 키 없으면 자막 있는 영상만 동작.

### 큰/작은 모델 분리 정책

`math` 는 schema 가 173개라 작은 모델 (≤10B 활성 param) 한테 호출 emit 실패가 잦습니다. `SMALL_MODELS` Set 에 명시된 모델만 `math_basic` (16 tools) 로 다운그레이드:

```javascript
// scripts/manage.sh
var SMALL_MODELS = new Set([
  'qwen3.5:9b', 'llama3.1:8b',
  'gpt-5-mini', 'gpt-5-nano', 'claude-haiku-4.5', 'gemini-2.5-flash',
]);
```

### `usage` 의 startup: false

이 서버만 `startup: false`. LibreChat 의 MCP `startup: true` 경로는 app-level (서버 부팅) 에 connection 을 만드는데, 그 단계엔 user 객체가 없어 `{{LIBRECHAT_USER_EMAIL}}` placeholder 가 빈 값으로 고정됩니다. `false` 면 user-scoped 연결만 만들어서 호출자별로 치환이 동작.

```yaml
usage:
  type: stdio
  startup: false
  command: uv
  args: [run, --script, /app/mcp/usage.py]
  env:
    LITELLM_URL: "http://litellm:8000"
    LITELLM_MASTER_KEY: "${LITELLM_MASTER_KEY}"
    LIBRECHAT_USER_EMAIL: "{{LIBRECHAT_USER_EMAIL}}"
```

## 이미지 백엔드

| `model` arg | 백엔드 |
|---|---|
| `flux-schnell` | ComfyUI |
| `flux-dev` | ComfyUI (gated, `HF_TOKEN` 필요) |
| `qwen-image` | ComfyUI |
| `qwen-image-edit` | ComfyUI |
| `gpt-image-2` | OpenRouter → OpenAI |
| `nano-banana` | OpenRouter → Google |

ComfyUI alias 는 가중치 파일이 다르고 용도가 갈립니다 — `flux-schnell` 은 빠른 iteration (4 step), `flux-dev` 는 최고 품질 (~20 step, gated), `qwen-image` 는 텍스트→이미지 (한글 강함), `qwen-image-edit` 는 이미지 편집.

외부 image 모델은 `comfyui-shim` 의 `OR_IMAGE_MODELS` env (`<alias>=<litellm-model-name>` csv) 가 매핑을 관리. 매핑에 있으면 LiteLLM `/v1/chat/completions` (`modalities=["image","text"]`) 로, 없으면 ComfyUI 로 분기. 미지정 시 `DEFAULT_MODEL=qwen-image`.

## 모델별 도구 매트릭스

에이전트는 모델 1개당 1개씩 자동 생성됩니다 (`./scripts/manage.sh agent sync`). 이름 prefix 가 부착된 도구를 요약:

| 이름 prefix | execute_code | generate_image |
|---|---|---|
| `Text` | ✗ | ✗ |
| `Text + Code` | ✓ | ✗ |
| `Text + Image` | ✓ | ✓ |
| `Text + Image + Code` | ✓ | ✓ |

`file_search` / `web_search` 는 전 prefix 공통 부착.

MCP 측은 `COMMON` (= `fetch_url`, `time`, `usage`) 가 전 에이전트 공통, 거기에 모델 크기별로 `math_basic` 또는 `math` 가 추가됩니다.

prefix 별 해당 모델:
- `Text` — claude-haiku-4.5
- `Text + Code` — claude-opus-4.7/4.6, claude-sonnet-4.6
- `Text + Image` — gpt-5-mini, gpt-5-nano, gemini-3.1-pro-preview/2.5-pro/flash
- `Text + Image + Code` — gpt-5.5, gpt-5, 전 ollama 모델 (qwen3.5:9b, qwen3.6:35b, llama3.1:8b, llama3.3:70b, nemotron3:33b, qwen3-coder-next:q8_0)

### 빌트인 도구 제외 정책 (TOOL_EXCLUDE)

기본은 "전 모델 전 도구". `TOOL_EXCLUDE` 는 emit 실패가 실제로 발생한 (모델, 도구) 조합만 등록 — 현재 알려진 케이스 없음. anthropic 계열은 자사 image API 가 없어 `generate_image` 가 `EXT_IMAGE_FOR_PROVIDER` 매핑 부재로 자동 제외 (별개 경로).

```javascript
// scripts/manage.sh
var TOOL_EXCLUDE = {};  // 필요 시 'model-id': ['tool1', 'tool2'] 추가
```

### 외부 LLM provider 별 image 매핑

native 자사 image 모델이 있는 provider 만 `generate_image` 부착. Anthropic 은 자사 image API 가 없어서 툴 자체 제외 (이름도 `Text + Code` 로 빠짐).

```javascript
// scripts/manage.sh
var EXT_IMAGE_FOR_PROVIDER = {
  'openai': 'gpt-image-2',
  'google': 'nano-banana',
  // 'anthropic': undefined → generate_image 제외됨
};
```

## 디버깅

| 증상 | 확인 |
|---|---|
| 도구 UI 미노출 | `librechat.yaml.endpoints.agents.capabilities` 에 명시 |
| MCP silent skip | `docker compose logs librechat -f` 에서 spawn 실패 / `getToolDefinition` undefined |
| 도구 안 부르고 텍스트로 흘림 | Llama 3.x 의 `<\|python_tag\|>` 누수 — sanitizer callback |
| `usage` 빈 이메일 | yaml `startup: false` 누락 |
| generate_image 항상 ComfyUI | `OR_IMAGE_MODELS` 매핑 누락 |

**도구 UI 미노출** — DB-backed agent 의 capabilities 폴백 버그. `librechat.yaml.endpoints.agents.capabilities` 에 항목 명시 안 하면 plugin tool 전부 차단됨.

**MCP silent skip** — `uvx` 첫 호출 시 패키지 다운로드라 30–60s 지연이 정상. 그 이상 걸리면 패키지 이름 / 인자 / 네트워크 확인. `getToolDefinition` undefined 면 [LibreChat tool rename 4-layer 함정](https://librechat.ai) 류 — source + `@librechat/api` dist 같이 패치돼야 함.

**도구 안 부르고 텍스트로 흘림** — Llama 3.x 가 OpenAI tool_calls 대신 `<|python_tag|>{...}` raw text 로 함수 호출을 leak. LiteLLM `callbacks/sanitize_python_tag.py` 가 자동 재구성하지만 callback 등록이 빠지면 동작 안 함.

**`usage` 빈 이메일** — yaml 에 `startup: false` 빠짐. app-level init 단계에 user 컨텍스트가 없어서 `{{LIBRECHAT_USER_EMAIL}}` placeholder 치환이 실패.

**generate_image 항상 ComfyUI** — `comfyui-shim.environment.OR_IMAGE_MODELS` env 매핑이 비어있거나 `model` arg 가 매핑에 없는 값. shim 이 매핑 못 찾으면 ComfyUI 로 폴백.
