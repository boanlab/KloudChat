# 도구 (Tools)

> 이 문서: 에이전트가 실제로 호출할 수 있는 도구가 무엇이고 어디로 라우팅되는지. 모델별로 어떤 도구가 켜지고 꺼지는지(매트릭스). 새 도구 추가 절차. 모델 자체는 [모델 설정](models.md), 환경변수는 [env-reference](env-reference.md).

도구는 세 갈래:

- **Built-in** — LibreChat 이 자체 구현한 핵심 4개. 각자 별도 백엔드 컨테이너로 연결.
- **MCP servers** — `librechat.yaml` 의 `mcpServers` 에 stdio 로 등록한 보조 서버들.
- **이미지 백엔드** — `generate_image` 툴이 `model` arg 에 따라 ComfyUI 또는 외부 image 모델로 분기.

도구 부착:

- 모델별 결정 위치 — `scripts/manage.sh::upsert_shared_agent_catalog`.
- 카탈로그 — ADMIN 한 벌을 전 사용자에게 read-only 공유 (사용자별 복제 아님).
- 사용자 고유 이름 에이전트 — 보존. 단 카탈로그와 같은 이름의 복제본은 sync 시 정리.

## Built-in 도구

| 도구 | 백엔드 |
|---|---|
| `execute_code` | `code-interpreter` 컨테이너 |
| `file_search` | `rag_api` 컨테이너 |
| `web_search` | `searxng` 컨테이너 |
| `generate_image` | `comfyui-shim` (→ ComfyUI 또는 외부 image 모델) |

- **`execute_code`** — LibreCodeInterpreter Python 샌드박스. 모델이 emit 한 코드를 실행하고 결과·이미지·파일 반환. 30s 실행 제한 + 512 MB 메모리, 산출물은 MinIO 저장.
- **`file_search`** — pgvector + MeiliSearch Hybrid Search. 업로드 파일을 bge-m3 임베딩으로 검색·발췌해 응답에 인용. HWP / PDF / DOCX / MD 등 지원 (rag-patches 가 HWP 분기 추가).
- **`web_search`** — SearXNG 인스턴스 고정 (`webSearch.searchProvider: "searxng"`). 페이지 본문 추출은 self-hosted `crawl4ai-shim` 이 Firecrawl 호환 endpoint 로 수신 (`webSearch.scraperType: "firecrawl"`, `firecrawlApiUrl: ${FIRECRAWL_BASE_URL}`) — 외부 Firecrawl Cloud 의존성 없음. 모델이 의도적으로 웹 결과를 가져올 때만 호출.
- **`generate_image`** — 텍스트→이미지 또는 이미지 편집. `model` arg 에 따라 백엔드 분기 — [이미지 백엔드](#이미지-백엔드) 참고.

주의:

- **capabilities 명시 필수** — 이 4개는 `librechat.yaml.endpoints.agents.capabilities` 에 명시돼야 DB-backed agent 사용 가능. LibreChat 의 `resolveAgentCapabilities` 가 ephemeral agent 에만 default 폴백 → 미명시 시 DB agent 전부 차단.
- **UI 토글과 독립** — `interface.runCode` / `fileSearch` / `webSearch` / `fileCitations` 토글은 입력란 옆 버튼 표시 여부만 통제. 에이전트 도구 권한과는 무관.

## MCP servers

| 서버 | 전송 | 실행 |
|---|---|---|
| `fetch_url` | stdio | `uvx mcp-server-fetch` (공통) |
| `time` | stdio | `uvx mcp-server-time` (공통) |
| `usage` | stdio | `uv run --script /app/mcp/usage.py` (공통) |
| `youtube` | stdio | `uv run --script /app/mcp/youtube.py` (공통) |
| `smart_search` | stdio | `uv run --script /app/mcp/smart_search.py` (공통) |
| `export_deck` | stdio | `uv run --script /app/mcp/export_deck.py` (Slide Studio 전용) |
| `generate_video` | stdio | `uv run --script /app/mcp/generate_video.py` (Video Studio 전용) |
| `paperbanana` | stdio | `uvx --from paperbanana[mcp] paperbanana-mcp` (Paper Banana 전용) |
| `deep_research` | streamable-http | `deep-research` 사이드카 컨테이너 (`Dockerfile.deep-research-mcp`) |

- **stdio 8개** — LibreChat 가 자식 프로세스로 spawn (`librechat.yaml` 의 `mcpServers` 섹션 정의).
  - `fetch_url`/`time`/`usage`/`youtube`/`smart_search` — 일반·Super 에이전트 공통.
  - `export_deck`/`generate_video`/`paperbanana` — 각각 Slide/Video Studio·Paper Banana 전용.
- **부착 방법** — 에이전트의 `tools` 배열에 `sys__all__sys_mcp_<servername>` 항목 추가 → 그 서버가 노출하는 모든 tool 자동 부착.
- **첫 호출 지연** — uvx 패키지 다운로드로 30–60s 지연 정상 (이후 캐시).
- **`deep_research` 만 예외** — LibreChat spawn 아님, 별도 컨테이너의 HTTP MCP. 사유: alpine/musl 의 LibreChat 이미지에서 playwright wheel 비호환 → debian-slim 사이드카 분리.

**`fetch_url`** — 1 tool. URL → Markdown 변환. 전 에이전트 공통.

**`time`** — 2 tools. 현재 시간 / 타임존 변환. 전 에이전트 공통 ("오늘" 환각 방지).

**수학** — 별도 MCP 없음. 사칙연산·sympy 심볼릭·단위 변환 등은 `execute_code` 의 Python 으로 처리 — `import sympy; print(sympy.solve(...))`.

**`usage`** — 2 tools. `my_usage(months_back)`, `budget_status()`. 사용자 본인의 LiteLLM virtual key 사용량/예산 조회 (월 단위). 응답에 다른 사용자 user_id 가 섞이면 `CrossUserDataError` 로 거부 (defensive). `mcp/usage.py` 가 PEP-723 inline deps 로 mcp + httpx 자동 설치.

**`youtube`** — 1 tool. `transcript(url, language?)`. YouTube 영상 텍스트 반환.

- **전사 경로** — 자막 우선 (`youtube-transcript-api`), 자막 없으면 `yt-dlp` 로 audio 받아 whisper 전사 (`WHISPER_URL` 의 `whisper-shim` → `WHISPER_URLS` 의 호스트 systemd backend 중 하나).
- **멀티 GPU 분산** — `WHISPER_URLS` 콤마 구분으로 노드 분산 (shim 이 inflight 카운터 기준 라우팅).
- **deps** — `mcp/youtube.py` PEP-723 inline.
- **GPU 전용 (OR STT 폴백 없음)** — `WHISPER_URLS` 가 비면 이 도구 자체가 어떤 에이전트에도 미부착 (`whisper_eligible` 게이팅).

**`smart_search`** — 1 tool. `smart_search(query, top_k)`. 업로드 문서 정밀 검색 — 질의 재구성 + 다중 질의 + pgvector 검색 + rerank + 적합도 평가를 한 번에. 일반·Super·Deep Research 에이전트 공통.

**`export_deck`** — 2 tools. `export_deck_pdf()`, `export_deck_pptx()`. Slide Studio 가 저작한 HTML 발표자료를 PDF / PPTX 로 내보낸다 (저작 자체는 도구 없이 HTML 아티팩트로 직접). Slide Studio 전용.

**`generate_video`** — 2 tools. `generate_video(prompt, model, …)`, `check_video(job_id)`. 텍스트→비디오. 기본은 로컬 LTX-Video, `model` 명시 시 OpenRouter Veo/Sora. `comfyui-shim` 의 `video/submit`·`video/fetch` 로 라우팅 (제출→폴링→링크 반환). Video Studio 전용.

**`paperbanana`** — 3 tools. `generate_diagram` (method / architecture / conceptual 다이어그램), `generate_plot` (사용자 데이터 기반 통계 차트), `evaluate_diagram` (figure 비평).

- **렌더 파이프라인** — `paperbanana[mcp]` (llmsresearch/paperbanana) 가 planner/stylist/visualizer/critic multi-agent 로 publication-quality figure 생성.
- **모델 (OpenRouter 경유)** — VLM=`google/gemini-2.5-flash`, 이미지=`google/gemini-3.1-flash-image-preview` (nano-banana-2).
- **타임아웃** — 다단계 refine 으로 수 분 소요 → `timeout: 900000`.
- **전용** — Paper Banana 에이전트 (`mcpServers.paperbanana`, OPENROUTER_API_KEY 필요).

**`deep_research`** — 8 tools. LearningCircuit `local-deep-research` 의 ReAct strategy multi-source 검색 + iterative reasoning.

- **검색 엔진** — searxng (default `LDR_SEARCH_TOOL=searxng`). KloudChat science 탭이 arxiv+scholar+openalex+crossref+pubmed 묶음이라 0-result 위험 적음.
- **reasoning** — LiteLLM 경유 plain `local/qwen3.5:122b`.
- **전송** — `deep-research` 사이드카가 `mcp-proxy` 로 ldr-mcp 의 stdio 를 streamable-http (`http://deep-research:8081/mcp`) 로 wrap. `librechat.yaml.mcpSettings.allowedDomains` 에 `deep-research` 등록돼야 SSRF guard 통과.
- **타임아웃** — `librechat.yaml.mcpServers.deep_research.timeout: 1800000` (30분). quick_research 1-5min, detailed_research 5-15min 이라 충분 헤드룸.
- **검색 폭** — `LDR_SEARCH_ITERATIONS=2` / `LDR_SEARCH_QUESTIONS_PER_ITERATION=1` (env default, model arg 로 override). qwen3.5:122b 가 verbose 질문 생성 경향이라 작게.

### `usage` 의 startup: false

이 서버만 `startup: false`.

- **`startup: true` 문제** — app-level (서버 부팅) 에 connection 생성 → 그 단계엔 user 객체 없음 → `{{LIBRECHAT_USER_EMAIL}}` placeholder 가 빈 값으로 고정.
- **`false` 효과** — user-scoped 연결만 생성 → 호출자별 치환 동작.

```yaml
usage:
  type: stdio
  startup: false
  command: uv
  args: [run, --script, /app/mcp/usage.py]
  env:
    LITELLM_URL: "${LITELLM_URL}"
    LITELLM_MASTER_KEY: "${LITELLM_MASTER_KEY}"
    LIBRECHAT_USER_EMAIL: "{{LIBRECHAT_USER_EMAIL}}"
```

## 이미지 백엔드

`generate_image` 는 **Image Studio 전용**.

- **분기 라우팅** — alias 에 따라 로컬 ComfyUI 또는 OpenRouter 경유 외부 모델.
- **비부착 범위** — Super Agent / 일반 agent (local + 외부 provider) 모두 image tool 미부착 (`manage.sh` 의 `builtinFor` 가 `kind === 'image'` 일 때만 부착).
- **사용법** — 이미지 생성 시 dropdown 에서 Image Studio 로 전환.

| `model` arg | 백엔드 | 특성 |
|---|---|---|
| `flux-schnell` | 로컬 ComfyUI | 4 step, MIT, 무료, GB10 ~90초 (GPU 있을 때 DEFAULT) |
| `flux-dev` | 로컬 ComfyUI | 20 step, gated (`HF_TOKEN`), 무료, ~2-3분 |
| `nano-banana` | OpenRouter → Gemini 2.5 Flash Image | ~5-10초, ~$0.04/장, 멀티이미지 편집 강점 |
| `nano-banana-2` | OpenRouter → Gemini 3.1 Flash Image Preview | Pro 급 품질을 Flash 속도로, ~$0.05/장 |
| `gpt-image-2` | OpenRouter → OpenAI GPT-5.4 Image 2 | text-in-image / instruction-following 최강, ~$0.10-0.30/장 |

기본 모델·노출:

- **미지정 시 기본** — backend 유무로 분기 (`comfyui-shim` `flux-schnell if BACKENDS else nano-banana`). 로컬 ComfyUI 있으면 `flux-schnell`(무료), 없으면(GPU 없는 OR 전용 배포) 외부·유료 `nano-banana`(~$0.04/장).
- **enum 노출** — agent enum 은 5종 모두.
- **외부 모델 가드** — 사용자 명시 요청 시에만 호출되도록 IMAGE_INSTR 규칙으로 가드 (`scripts/agent-instructions-appendix.txt::KLOUDCHAT_IMAGE_GEN`).

외부 모델 라우팅:

- **호출** — `comfyui-shim` 의 `EXTERNAL_IMAGE_ALIASES` 가 LiteLLM 의 `/v1/chat/completions` 로 `modalities=["image","text"]` (OpenRouter image-gen 인터페이스) 호출.
- **응답 처리** — `message.images[0].image_url.url` (data URL) 에서 base64 만 strip → A1111 response shape 으로 wrap.
- **ComfyUI VRAM admission 건너뜀**.

**비용 귀속** — 모든 생성을 호출 사용자(`end_user`)에 귀속해 `mcp/usage.py` 의 my_usage(`/customer/daily/activity?end_user_ids=`)에 합산한다. 두 경로:

- **외부 모델**(nano-banana 등): `rag-patches/patch_librechat_sd_model.js` 가 payload 에 `user = this.req.user.email` 주입 → shim `_call_external_image` 가 LiteLLM `user=` 로 전달 → 실제 OR 과금이 `end_user` 로 기록.
- **로컬 ComfyUI**(FLUX 이미지 / LTXV 비디오): 워크플로엔 user 가 안 들어가지만, 생성 완료 후 shim `_bill_local` 이 passthrough(`/localbill/image` $0.02 · `/localbill/video/<초>` $0.04/초, **명목 단가 = OR 동급 50%**)로 같은 `end_user` 에 기록. best-effort — 과금 실패해도 생성물엔 영향 없음.

### shim pre-flight VRAM check

- **측정** — 매 요청마다 후보 노드의 `/system_stats` `vram_free` + ComfyUI 자기 PyTorch reserve 를 측정해 모델별 필요량 이상일 때만 forward.
  - 모델별 필요량 — flux-schnell 3 GiB, flux-dev 5 GiB, 기본 12 GiB (flux GGUF 의 실측 peak 기준).
- **unified memory 노드** (`vram_total == ram_total`) — `system.ram_free` 도 후보 신호로 써 더 큰 값 채택. 사유: vLLM cudaMallocAsync reserve + OS page cache 가 `vram_free` 를 underreport 하지만, page cache 는 ComfyUI 의 GPU mem 요청 시 즉시 evict 되므로 사실상 evictable 가용량.
- **부족 시** — `CAPACITY_POLL_INTERVAL_SEC` (3s) 간격 polling, `CAPACITY_WAIT_TIMEOUT_SEC` (300s) 까지 대기 후 503 반환.
- **probe 실패 시** — best-effort 통과.

## agent 별 도구 매트릭스

에이전트는 `./scripts/manage.sh agent sync` 가 ADMIN 소유 단일 카탈로그로 생성해 전 사용자에게 read-only 공유. 두 분류:

- **일반 agent** — 모델 1개당 1개. 이름은 LiteLLM 라우트와 동일한 모델 ID (`openai/gpt-5-mini`, `local/gemma-4-26b` 등). provider 무관하게 동일 도구 셋 부착.
- **Functional agent** — Super Agent 가용 시 (chat `local/gemma-4-26b` backend 잡힘) 함께 등장. 도구 셋 의도적으로 좁음. (export_deck / generate_video / 내장 STT 는 표 컬럼에 없어 각주 표기.)

| agent | execute_code | file_search | web_search | generate_image | fetch_url | time | youtube | usage | smart_search | deep_research |
|---|---|---|---|---|---|---|---|---|---|---|
| **Super Agent** | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ |
| **Image Studio** | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **Slide Studio** ¹ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **Note Taker** ³ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **Deep Research** | ✓ | ✓ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | ✓ | ✓ |
| **Video Studio** ⁴ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **Paper Banana** ² | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| `local/<id>` | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ |
| `openai/<id>` | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ |
| `anthropic/<id>` | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ |
| `google/<id>` | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ |

- **¹ Slide Studio** (`local/qwen3.5:122b`) — builtin 도구 없음. `export_deck` MCP(PDF/PPTX 내보내기)만 부착. 본문은 전문 발표 디자이너 프롬프트로 **자체완결형 HTML 발표자료(인라인 CSS/JS/SVG)** 를 `:::artifact{type="text/html"}` 로 직접 저작 → 우측 패널 렌더.
- **² Paper Banana** (`local/gemma-4-26b`) — `file_search` + `paperbanana` MCP(`generate_diagram` / `generate_plot` / `evaluate_diagram`). 학술 논문용 publication-quality figure 생성(multi-agent 렌더, OpenRouter VLM gemini-2.5-flash + 이미지 nano-banana-2). Research 카테고리, Top Picks(promoted) 제외.
- **³ Note Taker** (`local/gemma-4-26b`) — `file_search`. 오디오를 「텍스트로 업로드」하면 내장 STT(whisper-shim)가 업로드 시 전사 → 그 전사문으로 회의록/강의노트/보고서 작성. Productivity 카테고리.
- **⁴ Video Studio** (`local/qwen3.5:122b`) — `generate_video` MCP(텍스트→비디오, 기본=로컬 LTX-Video, `model` 명시 시 OpenRouter Veo/Sora 외부·유료). Productivity 카테고리, Top Picks 포함. 자세히는 [video-studio.md](video-studio.md).

기본 정책: **공통 풀 없음, kind 별 명시 부착**.

- **일반 agent** — provider 무관하게 8종 (builtin 3 + MCP 5) 동일.
- **functional agent** — 의도된 모드. 사용자는 dropdown 으로 모드 전환.

### AI Agent Store / 운영 정책

`agent sync` 가 함께 설정하는 항목 (모두 멱등, MongoDB wipe 후 재실행으로 복원):

- **생성 권한** — agent 생성은 **ADMIN 전용**. `librechat.yaml interface.agents.CREATE:false` 로 양쪽을 막고 sync 가 ADMIN role 만 `CREATE:true` 로 복구한다 (LibreChat 은 interface 권한을 USER/ADMIN 에 동일 적용해 yaml 만으로는 역할 분리가 안 됨). 일반 사용자는 공유 카탈로그 **사용(USE)만** 가능.
- **공유** — ADMIN 카탈로그 각 agent 에 public viewer ACL(`permBits 1`) 부여 → 전 사용자 read-only 노출. 비-ADMIN 의 같은 이름 복제본은 정리.
- **새 대화 기본** — `librechat.yaml` 의 `modelSpecs` 가 `prioritize:true` + 단일 super-agent spec(showIcon false)으로 새 대화 기본=Super Agent 만 설정한다(보이는 핀 없음). `addedEndpoints:[agents]` 로 My Agents/상용 LLM 노출. `agent_id` 는 실제 id 고정 — `agent sync` 가 label 로 자동 동기화.
- **AI Agent Store 카테고리** — `categoryFor()` 가 매핑: 상용 LLM(openai/anthropic/google/deepseek/x-ai/perplexity/meta/qwen) + 상용 이미지/비디오(모델별 agent — Nano Banana / Veo / Sora 등, `lockModel` 지정)→Closed Models, 로컬 Image/Video/Slide Studio·Note Taker→Productivity, Deep Research/Paper Banana→Research, Super Agent + 로컬 agent→General. 활성 카테고리는 순서대로 General → Productivity → Education → Research → Closed Models 5종(기능별 image/development 탭은 제거·통합). Education 은 사용자가 직접 만든 에이전트용 빈 카테고리.

이유:
- `generate_image` 가 모든 agent 에 켜져 있으면 LLM 이 텍스트 응답에 부적절하게 image 호출을 끼워 넣는 빈도가 늘어남 → Image Studio 로 격리.
- `deep_research` 가 모든 agent 에 켜져 있으면 단순 검색에도 5-15분짜리 ReAct 가 트리거됨 (qwen3.5:122b sub-agent 점유) → Deep Research 로 격리.
- 일반 agent 의 `time` / `youtube` / `usage` 는 토큰 비용 작고 (~450 합) 호출 시 결과도 짧아 부담 없음 → 공통 유지.

생성 대상 모델 (`manage.sh` 의 spec 빌드):
- **`local/<id>`** — `VLLM_MODELS` 중 vLLM URL 이 셋팅된 모델. `qwen3-coder-next` 는 LiteLLM 등록만 유지하고 LibreChat agent/dropdown 에는 안 띄운다 — Claude Code / Codex CLI 같은 외부 코딩 클라이언트 전용이다. chat 모델 `gemma-4-26b` 는 Super Agent 의 base 이고, qwen3.5:122b 는 Image Studio / Video Studio / Deep Research / Slide Studio functional agent 가 흡수한다.
- **`openai/<id>`, `anthropic/<id>`, `google/<id>`** — OpenRouter 키 있을 때 `OPENAI_MODELS` / `ANTHROPIC_MODELS` / `GOOGLE_MODELS` 카탈로그 전부.

**Dropdown 정렬** — Super → Image → Video → Slide → Note Taker → Deep Research → Paper Banana → local → openai → anthropic → google. `manage.sh` 의 `DISPLAY_ORDER` 배열 인덱스(0=상단)만큼 `updatedAt` 을 미래로 띄워 LibreChat 의 `{updatedAt:-1, _id:1}` 정렬을 카테고리화 (상용은 고성능 우선 오름차순).

### 모든 에이전트 공통 instructions (appendix)

모든 default agent (Super Agent 포함) 는 베이스 instructions 끝에 `scripts/agent-instructions-appendix.txt` 내용을 자동 append. 이 appendix 가 ground truth:

- **KLOUDCHAT_HONESTY_RULES** — deep_research 의 `sources: []` 일 때 본문 인용 번호 / URL 환각 금지, 일부 sources 만 반환 시 그 범위로만 인용. tool 결과 URL 은 verbatim 복사. execute_code 는 반드시 `print()` (generator/REPL 표현식만으로는 stdout 비어있음).
- **KLOUDCHAT_DEEP_RESEARCH** — `search_engine` 명시 안 함 (LDR default 가 SearXNG science 탭이라 broad), `iterations` ≤ 3, `questions_per_iteration` 1 권장, `query` 는 영어 키워드 위주 (한국어 자료가 필요한 경우만 한국어).
- **KLOUDCHAT_IMAGE_GEN** — 사용자가 장수 명시 안 했으면 `generate_image` 1장만 호출 (ComfyUI 순차 처리, N장 = 시간 N배). 일반 요청은 `flux-schnell`, 복잡 구도/고품질 요구는 `flux-dev`. prompt 는 영어 7개+ 키워드, "Error making API request" 류는 즉시 retry 말고 사용자에게 보고.

- **마커** — 각 섹션에 `KLOUDCHAT_<NAME>` 마커 → grep 검색 용이.
- **동기화 단위** — `manage.sh agent sync` 가 매 실행마다 appendix 전체를 base instructions 끝에 통째로 append (마커 단위 부분 동기화 아님).

## 디버깅

| 증상 | 확인 |
|---|---|
| 도구 UI 미노출 | `librechat.yaml.endpoints.agents.capabilities` 에 명시 |
| MCP silent skip | `docker compose logs librechat -f` 에서 spawn 실패 / `getToolDefinition` undefined |
| 도구 안 부르고 텍스트로 흘림 | 로컬 모델의 `<\|python_tag\|>` / JSON 펜스 누수 — sanitizer callback |
| `usage` 빈 이메일 | yaml `startup: false` 누락 |
| 일반 agent 에서 image 요청 시 안 됨 | image tool 은 Image Studio 전용. dropdown 에서 Image Studio 로 전환 |
| Deep Research 가 일상 질문에 5-15분 점유 | Deep Research 는 학술 ReAct 전용 모드. 일반 검색은 Super Agent (web_search) 로 |

**도구 UI 미노출** — DB-backed agent 의 capabilities 폴백 버그. `librechat.yaml.endpoints.agents.capabilities` 에 항목 명시 안 하면 plugin tool 전부 차단됨.

**MCP silent skip** — `uvx` 첫 호출 시 패키지 다운로드라 30–60s 지연이 정상. 그 이상 걸리면 패키지 이름 / 인자 / 네트워크 확인. `getToolDefinition` undefined 면 LibreChat tool rename 4-layer 함정 (source 3개 + `@librechat/api` dist) 류 — `rag-patches/patch_librechat_sd_model.js` 참고.

**도구 안 부르고 텍스트로 흘림** — 로컬 모델이 OpenAI tool_calls 대신 `<|python_tag|>{...}` / ```json 펜스 / bare-JSON raw text 로 함수 호출을 leak. LiteLLM `litellm-callbacks/sanitize_python_tag.py` 가 자동 재구성하지만 callback 등록이 빠지면 동작 안 함.

**`usage` 빈 이메일** — yaml 에 `startup: false` 빠짐. app-level init 단계에 user 컨텍스트가 없어서 `{{LIBRECHAT_USER_EMAIL}}` placeholder 치환이 실패.
