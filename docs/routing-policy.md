# 라우팅 정책 인덱스

**KloudChat** 의 *모델 라우팅 / 도구 호출 / agent kind 별 instruction* 결정 지점과 변경 방법을 한 곳에서 정리.

## 변경 우선순위

| 의도 | 어디서 | 적용 명령 |
|---|---|---|
| agent instruction (말투 / 트리거 / 정책 텍스트) 추가·수정 | [`routing/instructions.md`](../routing/instructions.md) | `./scripts/manage.sh agent sync` |
| 공통 운영 규칙 (honesty / deep_research 가이드 / execute_code 규칙) | [`scripts/agent-instructions-appendix.txt`](../scripts/agent-instructions-appendix.txt) | `agent sync` |
| 새 도구 부착 / 제거 | [`scripts/manage.sh`](../scripts/manage.sh) 의 `mcpToolsFor()` — `MCP_DEFAULT` / `MCP_RESEARCH` / `MCP_PAPERBANANA` 배열 + kind 인라인 분기(video→`generate_video`, ppt→`export_deck`, image·notetaker→없음) | `agent sync` |
| 모델 카탈로그 (commercial / vLLM) | [`scripts/lib.sh`](../scripts/lib.sh) 의 `OPENAI_MODELS` / `ANTHROPIC_MODELS` / `GOOGLE_MODELS` / `VLLM_MODELS` | `./scripts/setup.sh litellm` (config 재생성) |
| 새 대화 기본 | [`librechat.yaml`](../librechat.yaml) 의 `modelSpecs` — `prioritize:true` + 단일 super-agent spec(showIcon false)으로 새 대화 기본=Super Agent 만 설정(보이는 핀 없음). agent_id 는 실제 id 고정 — `agent sync` 가 label 로 자동 동기화 | `agent sync` + `librechat` 재기동 |
| AI Agent Store 카테고리 / 생성권한(ADMIN 전용) | [`scripts/manage.sh`](../scripts/manage.sh) 의 `categoryFor()` + `cmd_agent_sync` 카테고리·role 시드 | `agent sync` |
| Super Agent 활성 조건 | `scripts/lib.sh::super_agent_eligible` (`gemma-4-26b` chat backend 가용) | scheduler apply + `librechat` 재기동 |
| scheduler feature 우선순위 | [`scheduler/catalog.py::DEFAULT_PRIORITIES`](../scheduler/catalog.py) | `setup.sh scheduler apply` |
| workload → sidecar URL 매핑 (image-flux → COMFYUI_URLS 등) | [`scheduler/applier.py::_SIDECAR_URL_VARS`](../scheduler/applier.py) | `setup.sh scheduler apply` |
| Super Agent shim 분기 로직 (router skip / tool_call 우회 등) | [`super-agent-shim/app.py::chat_completions`](../super-agent-shim/app.py) | `docker compose -f docker-compose.litellm.yml restart super-agent-shim` |
| LibreChat artifact prompt (`:::artifact{}` 형식) | LibreChat 내장 (`generateArtifactsPrompt`) — Artifacts 토글 ON 시 자동 주입. 변경 불가 | 해당 없음 |

## `routing/instructions.md` 구조

- 섹션 분리: sentinel `== name ==`
- 조립 주체: `manage.sh::instructionsFor` — **agent kind / 부착 도구** 에 따라 조립

| 섹션 | 사용 시점 |
|---|---|
| `agent.image.header` / `agent.image.body` | `kind === 'image'` (Image Studio) |
| `agent.ppt` | `kind === 'ppt'` (Slide Studio — 단독, 도구 없음) |
| `agent.research.header` / `agent.research.body` | `kind === 'research'` (Deep Research) |
| `agent.default.header` (`{{tools_caps}}` 치환) | 그 외 (Super / commercial / local) |
| `policy.language` | 모든 agent |
| `policy.tool_calling` | image / research / default / paperbanana (`ppt` 는 도구 없어 제외) |
| `trigger.youtube` | `youtube` 도구 부착 시 (research/default) |
| `trigger.math` | `execute_code` 부착 시 (research/default) |

- **`paperbanana` kind** (Paper Banana): 섹션 아님 → `manage.sh::instructionsFor` 안의 인라인 instruction + `policy.language` + `policy.tool_calling`
- **artifact 게이팅**: instructions 아님 → artifacts 모드로 게이팅
  - Image/Slide/Video Studio: 기본 ON
  - 그 외: 토글 ON 시 LibreChat 내장 프롬프트 주입 (`patch_librechat_artifacts_toggle.js`)
- **조립 결과** = 위 섹션들 `\n\n` 연결 + `agent-instructions-appendix.txt` 끝에 append

## 자주 묻는 변경

### 새 도구 (MCP) 부착

1. `librechat.yaml::mcpServers` 에 서버 정의
2. `scripts/manage.sh` 의 `MCP_DEFAULT` / `MCP_RESEARCH` 배열에 `sys__all__sys_mcp_<name>` 추가
3. (선택) `routing/instructions.md` 에 `trigger.<name>` 섹션 + `manage.sh::instructionsFor` 의 조건문 한 줄
4. `agent sync`

### 새 OR 모델 추가 (예: claude-opus-5)

1. `scripts/lib.sh` 의 해당 provider 배열에 id 추가 — `ANTHROPIC_MODELS` / `OPENAI_MODELS` / `GOOGLE_MODELS` / `DEEPSEEK_MODELS` / `XAI_MODELS` / `PERPLEXITY_MODELS` / `META_MODELS` / `QWEN_MODELS` (없는 provider 면 배열 신설 + 두 gen 스크립트에 emit 루프 한 줄)
2. `scripts/lib.sh::MODEL_PRICE_IN_PM` / `OUT_PM` 에 가격
3. `./scripts/setup.sh litellm` (config 재생성 + 컨테이너 재기동)
4. `agent sync`

### 로컬 모델에 OR 폴백 엮기

1. `gen-litellm-config.sh::SECTION` 에 `emit_or_fallback "$(env_get VLLM_X_URL)" "<or-slug>" <in> <out>` 한 줄 (로컬 primary 배포 시만 OR twin emit)
2. `litellm-config.yaml` 의 `router_settings.fallbacks` 에 `{"local/<name>": ["<or-slug>"]}` 추가 (정적 섹션 — gen 이 보존)
3. twin 은 dropdown 미노출(gen-librechat 가 안 emit)이라 폴백 전용. 발동 = OR 유료 egress.

### Super Agent 비활성 / 활성 토글

- **판정 주체**: `lib.sh::super_agent_eligible` — backend 가용성만으로 판정 (별도 on/off env 없음)
- **활성 조건**: `gemma-4-26b` chat 이 잡혀야 활성
- **자동 비활성**: gemma 를 어느 노드에도 배치 못하면 자동 비활성

- 활성 보장: catalog 의 chat-gemma4-26b `min_replicas=1` (현재 default) — scheduler 가 gemma 를 반드시 배치
- 비활성하려면: gemma backend 를 빼면 됨 (vLLM `VLLM_GEMMA26_URL` 미설정)

### artifact 형식 자체 변경 (`:::artifact{}` → 다른 marker)

- **변경 불가**: LibreChat 내장 (`/app/api/services/Artifacts/update.js::ARTIFACT_START`)
- 우리 쪽은 해당 marker 만 추종 필요

## 시각화 — 호출 path

```
사용자 → LibreChat agent.X
  └─ LibreChat 이 system message 조립:
       agent.instructions (from manage.sh::instructionsFor — routing/instructions.md SoT)
       + appendix (agent-instructions-appendix.txt)
       + additional_instructions (LibreChat 내장 artifactsOpenAIPrompt)
  └─ LiteLLM /v1/chat/completions
       ├─ model=local/auto-route → super-agent-shim 단일패스 chat (gemma-4-26b)
       │    └─ artifacts 토글 ON → concise/detox 지시문 주입 (같은 gemma 경로)
       ├─ model=local/gemma-4-26b → vLLM router LB (least-busy)
       │    └─ 노드 다운/과부하 → router_settings.fallbacks → OR 동일모델 (google/gemma-4-26b-a4b-it)
       ├─ model=local/qwen3.5:122b → vLLM (Deep Research / 정밀, PLAIN)
       │    └─ 노드 다운/과부하 → OR qwen/qwen3.5-122b-a10b
       └─ model=openai|anthropic|google|deepseek|x-ai|perplexity|meta|qwen/... → OpenRouter 경유
  └─ truncate_to_ctx callback (clamp / stub-user / history trim)
  └─ vLLM / OR 응답
```

## 자주 막히는 곳

| 증상 | 원인 | 위치 |
|---|---|---|
| 새 agent 가 instruction 안 받음 | `agent sync` 안 호출 | `manage.sh agent sync` |
| 트리거 키워드 추가했는데 효과 X | sync 후에도 `additional_instructions` (LibreChat default) 가 *더 길어서* 모델이 묻힘 | `routing/instructions.md::trigger.*` 섹션 짧게 |
| Super Agent dropdown 사라짐 | scheduler 가 chat-gemma4-26b 빼서 `VLLM_GEMMA26_URL=""` | [troubleshooting.md::Super Agent dropdown 사라짐](troubleshooting.md) |
| 모델이 commercial 이름 (`openai/gpt-5`) 인데 routing 안 됨 | OR 키 미설정 | `.env::OPENROUTER_API_KEY` |
