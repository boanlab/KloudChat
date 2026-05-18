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

| 서버 | 전송 | 실행 |
|---|---|---|
| `fetch_url` | stdio | `uvx mcp-server-fetch` |
| `time` | stdio | `uvx mcp-server-time` |
| `math` | stdio | `uvx mcp-sympy` |
| `math_basic` | stdio | `uvx mcp-server-math` |
| `usage` | stdio | `uv run --script /app/mcp/usage.py` |
| `youtube` | stdio | `uv run --script /app/mcp/youtube.py` |
| `deep_research` | streamable-http | `deep-research` 사이드카 컨테이너 (`Dockerfile.deep-research-mcp`) |

stdio 6개는 LibreChat 가 자식 프로세스로 spawn — `librechat.yaml` 의 `mcpServers` 섹션 정의. 에이전트의 `tools` 배열에 `sys__all__sys_mcp_<servername>` 항목을 추가하면 그 서버가 노출하는 모든 tool 이 자동 부착. 첫 호출 시 uvx 가 패키지 다운로드 (30–60s 지연 정상, 이후 캐시). `deep_research` 만 LibreChat 가 spawn 하지 않고 별도 컨테이너의 HTTP MCP — alpine/musl 의 LibreChat 이미지에서 playwright wheel 이 호환 안 되기 때문에 debian-slim 사이드카 분리.

**`fetch_url`** — 1 tool. URL → Markdown 변환. 전 에이전트 공통.

**`time`** — 2 tools. 현재 시간 / 타임존 변환. 전 에이전트 공통 ("오늘" 환각 방지).

**`math`** — 173 tools. sympy 심볼릭/수치 (미적분, 방정식, 행렬, 단위 변환). 큰 모델 전용 — schema 가 커서 작은 모델은 호출 emit 실패.

**`math_basic`** — 16 tools. 사칙연산 + power / sqrt / sum / product / compare. math 의 작은 모델용 대체.

**`usage`** — 2 tools. `my_usage(months_back)`, `budget_status()`. 사용자 본인의 LiteLLM virtual key 사용량/예산 조회 (월 단위). 응답에 다른 사용자 user_id 가 섞이면 `CrossUserDataError` 로 거부 (defensive). `mcp/usage.py` 가 PEP-723 inline deps 로 mcp + httpx 자동 설치.

**`youtube`** — 1 tool. `transcript(url, language?)`. YouTube 영상 텍스트 반환 — 자막 우선 (`youtube-transcript-api`), 자막 없으면 `yt-dlp` 로 audio 받아 whisper 전사 (`WHISPER_URL` 의 `whisper-shim` → `WHISPER_URLS` 의 호스트 systemd backend 중 하나 → 실패 시 LiteLLM `whisper-1` OR 폴백). `WHISPER_URLS` 콤마 구분으로 멀티 GPU 노드 분산 (shim 이 inflight + ollama VRAM 기준 라우팅). `mcp/youtube.py` PEP-723 inline deps. backend 미가용 + OR 키 없으면 자막 있는 영상만 동작.

**`deep_research`** — 8 tools. LearningCircuit `local-deep-research` 의 ReAct strategy multi-source 검색 + iterative reasoning. searxng 를 검색 엔진으로 쓰고, LiteLLM 경유 `ollama/nemotron3:33b` 로 reasoning (`docker-compose.yml.deep-research.LDR_LLM_MODEL` 로 변경). `deep-research` 사이드카가 `mcp-proxy` 로 ldr-mcp 의 stdio 를 streamable-http (`http://deep-research:8081/mcp`) 로 wrap. `librechat.yaml.mcpSettings.allowedDomains` 에 `deep-research` 등록돼 있어야 SSRF guard 통과. multi-iter ReAct 라 1 query 당 수 분 소요 가능 — 빠른 응답이 필요하면 LDR 모델을 `ollama/qwen3.5:9b` 등 경량으로 교체.

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

`generate_image` 는 **ollama 에이전트 전용** — ComfyUI 로만 라우팅. 외부 provider (openai/anthropic/google) 에이전트는 image tool 비부착 (`manage.sh` 의 `builtinFor` 가 외부 provider 에선 자동 drop).

| `model` arg | 백엔드 |
|---|---|
| `flux-schnell` | ComfyUI |
| `flux-dev` | ComfyUI (gated, `HF_TOKEN` 필요) |
| `qwen-image` | ComfyUI |
| `qwen-image-edit` | ComfyUI |

ComfyUI alias 는 가중치 파일이 다르고 용도가 갈립니다 — `flux-schnell` 은 빠른 iteration (4 step), `flux-dev` 는 최고 품질 (~20 step, gated), `qwen-image` 는 텍스트→이미지 (한글 강함), `qwen-image-edit` 는 이미지 편집. 미지정 시 `DEFAULT_MODEL=qwen-image`.

## 모델별 도구 매트릭스

에이전트는 모델 1개당 1개씩 자동 생성됩니다 (`./scripts/manage.sh agent sync`). 이름 prefix 는 그 모델이 다룰 수 있는 modality / 능력을 사용자에게 한눈에 보여주는 라벨이고, 실제 부착되는 builtin 도구는 provider 가 결정:

| provider | execute_code | file_search | web_search | generate_image |
|---|---|---|---|---|
| ollama | ✓ | ✓ | ✓ | ✓ (→ ComfyUI) |
| openai | ✓ | ✓ | ✓ | ✗ |
| google | ✓ | ✓ | ✓ | ✗ |
| anthropic | ✓ | ✓ | ✓ | ✗ |

기본 정책은 "전 모델 전 도구 + ollama 에 이미지". 외부 provider 는 image API 자체 없거나 운영 안정성 문제로 image 비부착.

MCP 측은 `MCP_COMMON` (= `fetch_url`, `time`, `usage`, `youtube`, `deep_research`) 이 전 에이전트 공통, 모델 크기별로 `math_basic` 또는 `math` 가 추가됩니다.

prefix 라벨 매핑 (manage.sh 의 spec 생성 로직):
- `Text` — claude-haiku-4.5, gpt-5-mini, gpt-5-nano, gemini-3.1-pro-preview/2.5-pro/flash
- `Text + Code` — claude-opus-4.7/4.6, claude-sonnet-4.6, gpt-5.5, gpt-5
- `Text + Image + Code` — 전 ollama 모델 (qwen3.5:9b, qwen3.6:35b, llama3.1:8b, llama3.3:70b, nemotron3:33b, qwen3-coder-next:q8_0)

## 디버깅

| 증상 | 확인 |
|---|---|
| 도구 UI 미노출 | `librechat.yaml.endpoints.agents.capabilities` 에 명시 |
| MCP silent skip | `docker compose logs librechat -f` 에서 spawn 실패 / `getToolDefinition` undefined |
| 도구 안 부르고 텍스트로 흘림 | Llama 3.x 의 `<\|python_tag\|>` 누수 — sanitizer callback |
| `usage` 빈 이메일 | yaml `startup: false` 누락 |
| 외부 agent 에서 image 요청 시 안 됨 | image tool 은 ollama 에이전트 전용. ollama agent 로 전환 |

**도구 UI 미노출** — DB-backed agent 의 capabilities 폴백 버그. `librechat.yaml.endpoints.agents.capabilities` 에 항목 명시 안 하면 plugin tool 전부 차단됨.

**MCP silent skip** — `uvx` 첫 호출 시 패키지 다운로드라 30–60s 지연이 정상. 그 이상 걸리면 패키지 이름 / 인자 / 네트워크 확인. `getToolDefinition` undefined 면 LibreChat tool rename 4-layer 함정 (source 3개 + `@librechat/api` dist) 류 — `rag-patches/patch_librechat_sd_model.js` 참고.

**도구 안 부르고 텍스트로 흘림** — Llama 3.x 가 OpenAI tool_calls 대신 `<|python_tag|>{...}` raw text 로 함수 호출을 leak. LiteLLM `callbacks/sanitize_python_tag.py` 가 자동 재구성하지만 callback 등록이 빠지면 동작 안 함.

**`usage` 빈 이메일** — yaml 에 `startup: false` 빠짐. app-level init 단계에 user 컨텍스트가 없어서 `{{LIBRECHAT_USER_EMAIL}}` placeholder 치환이 실패.
