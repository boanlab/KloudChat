# 환경변수 레퍼런스

> 이 문서: `.env` 의 모든 변수가 무엇이고 누가 채우는지(사용자 vs `gen-env.sh` vs `setup.sh`). 처음 띄우는 거면 [README](../README.md) 만 보면 됩니다 — 시나리오별로 실제 채워야 하는 키만 안내합니다.

`./scripts/gen-env.sh` 가 `.env.example` 을 복사하면서 `change-me-*` 패턴을 랜덤 시크릿으로 치환해 `.env` 를 생성합니다. 외부 키(OpenAI / Anthropic / Gemini / OpenRouter / HF_TOKEN)와 백엔드 토폴로지(`OLLAMA_URLS`, `COMFYUI_URLS`)는 직접 채워야 합니다.

`.env` 는 4섹션으로 묶여 있습니다:

1. **사용자 입력** — 필수 키 + 외부 연동
2. **서비스 자격증명** — `gen-env.sh` 가 채움 (DB pw, JWT, MEILI, master keys, …)
3. **setup.sh 발급** — `LITELLM_SERVICE_KEY`, `RAG_OPENAI_API_KEY` (수동 편집 불필요)
4. **내부 endpoint** — 보통 기본값 유지

## 1. 사용자 입력

| 변수 | 설명 | 비고 |
|---|---|---|
| `APP_TITLE` | LibreChat 좌상단 + 브라우저 탭 타이틀 | 기본 `KloudChat` |
| `WELCOME_BACK_MESSAGE` | 로그인 페이지 헤딩 ("다시 오신 것을 환영합니다" 자리). 모든 locale 같은 값 | 빈 값이면 LibreChat 기본 |
| `SIGNUP_HEADER` | 회원가입 페이지 헤딩 ("Create your account" 자리). 모든 locale 같은 값 | 빈 값이면 LibreChat 기본 |
| `HELP_AND_FAQ_URL` | 우상단 메뉴의 "Help & FAQ" 링크 URL. `/` 면 메뉴 자체 숨김 | `/` (숨김) |
| `CUSTOM_FOOTER` | 채팅창 하단 푸터 ("LibreChat v0.8.5 - ..." 자리). 빈 값이면 LibreChat 기본 표시, 공백 1개면 사실상 숨김. LibreChat 은 yaml `interface.customFooter` 아닌 이 env 변수에서만 읽음 | (LibreChat 기본) |
| `OLLAMA_URLS` | Ollama 백엔드(들) — csv. LiteLLM router 가 노드를 직접 호출, 모델 보유 노드 사이에서 `least-busy` LB | `setup.sh` 가 노드별 보유 모델 union 으로 discovery |
| `COMFYUI_URLS` | ComfyUI 백엔드(들) — csv. shim 이 alias→보유 노드 매핑(`/object_info` 캐시) 으로 후보 좁힌 뒤 `/queue` 깊이로 LB | union 디스커버리 — 어느 노드든 보유한 alias 는 그 노드로 라우팅 |
| `OPENAI_API_KEY` | OpenAI API 키 (선택) | 있으면 OpenAI 큐레이션 리스트가 native 로 자동 등록 |
| `ANTHROPIC_API_KEY` | Anthropic API 키 (선택) | 있으면 Anthropic 큐레이션 리스트가 native 로 |
| `GEMINI_API_KEY` | Google Gemini AI Studio 키 (선택) | 있으면 Google 큐레이션 리스트가 native 로 |
| `OPENROUTER_API_KEY` | OpenRouter 키 (선택) | native 없는 commercial 모델의 fallback + `MODEL_OR_FREE` 매핑된 Ollama 모델이 노드에 없을 때 OR free fallback (기본 비활성, 사용자 검증 후 `lib.sh` 에서 활성화) |
| `HF_TOKEN` | HuggingFace gated repo 토큰 | `flux-dev` 다운로드 전제 (다른 이미지 모델은 무관) |

**필수**: `OPENROUTER_API_KEY` 또는 reachable `OLLAMA_URLS` 노드 ≥1 — 둘 다 없으면 `setup.sh` 가 거부.

### 멀티 노드 토폴로지

`OLLAMA_URLS` / `COMFYUI_URLS` 에 콤마로 여러 URL 을 적으면 각각 다른 LB 가 동작합니다.

```dotenv
OLLAMA_URLS=http://host.docker.internal:11434,http://gpu-node-1:11434,http://gpu-node-2:11434
COMFYUI_URLS=http://gpu-node-1:8188,http://gpu-node-2:8188
```

- **Ollama**: LiteLLM router 가 각 노드를 직접 호출. discovery 가 노드별 `/api/tags` 를 union 으로 합치고, 모델 하나당 보유 노드 수만큼 같은 `model_name` deployment 를 등록 → router 가 `least-busy` 로 LB. 모델은 노드별로 자유롭게 pull (이기종 GPU OK), 같은 모델을 여러 노드에 pull 하면 자동 분산.
- **ComfyUI**: shim 이 alias 별 보유 노드 매핑을 `/object_info` 디스커버리로 캐시 (TTL `MODEL_DISCOVERY_TTL_SEC`, 기본 300s). 매 요청 alias 로 후보 노드 좁힌 뒤 `/queue` 깊이로 LB, `prompt_id → 노드` 매핑 in-memory 유지. 노드별로 다른 가중치 셋 OK.

`.env` 수정 후 `./scripts/gen-litellm-config.sh && docker compose restart litellm` (Ollama 토폴로지 변경 시) — `setup.sh` 가 자동 수행.

| `COMFYUI_URLS` 패턴 | 의미 |
|---|---|
| `http://host.docker.internal:8188` (기본) | compose 호스트와 같은 머신에 native 설치 |
| `http://gpu-node-1:8188` | 원격 GPU 노드 1대, 모든 요청이 해당 노드로 |
| `http://gpu-node-1:8188,http://gpu-node-2:8188` | 원격 GPU 노드 N대, shim 이 `/queue` 깊이로 분배 |

## 2. 서비스 자격증명 (gen-env.sh 자동 생성)

### LibreChat 핵심 시크릿

| 변수 | 설명 |
|---|---|
| `JWT_SECRET` | JWT 서명 시크릿 |
| `JWT_REFRESH_SECRET` | JWT 리프레시 토큰 시크릿 |
| `CREDS_KEY` | 자격증명 암호화 키 |
| `CREDS_IV` | 자격증명 암호화 IV |
| `MEILI_MASTER_KEY` | MeiliSearch 마스터 키 |

### MongoDB

| 변수 | 설명 |
|---|---|
| `MONGO_ROOT_USER` | MongoDB 루트 계정명 (기본 `kloudchat-librechat`) |
| `MONGO_ROOT_PASSWORD` | MongoDB 루트 비밀번호 |

### PostgreSQL — RAG pgvector

| 변수 | 설명 | 기본값 |
|---|---|---|
| `POSTGRES_DB` | DB 이름 | `kloudchat-ragdb` |
| `POSTGRES_USER` | DB 사용자 | `kloudchat-rag` |
| `POSTGRES_PASSWORD` | DB 비밀번호 | 자동 생성 |

### LiteLLM

| 변수 | 설명 |
|---|---|
| `LITELLM_DB_USER` | LiteLLM PostgreSQL 사용자 (`kloudchat-litellm`) |
| `LITELLM_DB_PASSWORD` | 위 PostgreSQL 비밀번호 |
| `LITELLM_MASTER_KEY` | LiteLLM 마스터 키 (모든 권한) |

### Code Interpreter + MinIO

| 변수 | 설명 | 기본값 |
|---|---|---|
| `CODE_INTERPRETER_API_KEY` | 인증 키 (LibreChat 의 `LIBRECHAT_CODE_API_KEY` 와 같음) | 자동 생성 |
| `CODE_INTERPRETER_MINIO_USER` | MinIO 사용자 | `code-interpreter` |
| `CODE_INTERPRETER_MINIO_PASSWORD` | MinIO 비밀번호 | 자동 생성 |
| `CODE_INTERPRETER_MINIO_BUCKET` | MinIO 버킷 | `code-interpreter` |

## 3. setup.sh 가 발급

| 변수 | 설명 |
|---|---|
| `LITELLM_SERVICE_KEY` | LibreChat → LiteLLM 호출용 가상 키. `setup.sh` 5단계에서 `./scripts/manage.sh key issue --service librechat` 가 발급해 채움 |
| `RAG_OPENAI_API_KEY` | RAG API → LiteLLM 호출용. 동일 키 값 사용 |

수동으로 비워두면 됨. 재발급은 같은 명령 재실행.

## 4. 내부 endpoint (보통 기본값 유지)

| 변수 | 설명 | 기본값 |
|---|---|---|
| `RAG_PORT` | RAG API 컨테이너 포트 | `8000` |
| `EMBEDDINGS_PROVIDER` | RAG 임베딩 공급자 (LiteLLM 경유 — 항상 `openai`) | `openai` |
| `EMBEDDINGS_MODEL` | LiteLLM 의 model_name 과 일치 | `bge-m3` |
| `RAG_OPENAI_BASEURL` | OpenAI-호환 임베딩 endpoint | `http://litellm:8000/v1` |
| `SEARXNG_INSTANCE_URL` | LibreChat → SearXNG | `http://searxng:8080` |
| `LIBRECHAT_CODE_BASEURL` | LibreChat → 코드 인터프리터 | `http://code-interpreter:8000` |
| `SD_WEBUI_URL` | LibreChat → comfyui-shim (A1111 호환) | `http://comfyui-shim:7860` |
| `OR_IMAGE_MODELS` | shim 이 LiteLLM 경유 OR/native image API 로 라우팅할 alias 매핑. `<alias>=<litellm-model-name>` 콤마 csv. 비었으면 모든 image 요청을 ComfyUI 로 | `nano-banana=image-nano-banana,gpt-image-2=image-gpt-image-2` |
| `OLLAMA_VRAM_LOADED_THRESHOLD_BYTES` | shim 의 VRAM-aware 라우팅 임계. 노드의 ollama 가 이 값 초과로 VRAM 점유 중이면 다른 노드 우선 (대형 LLM 과 ComfyUI 공존 회피) | `32212254720` (30 GiB) |

## 모델 등록 동작 요약

| 조건 | 결과 |
|---|---|
| native key 있음 | 해당 provider 의 curated 리스트가 native 로 등록 (`<provider>/<id>`) |
| native key 없음 + OR 있음 | 동일 리스트가 OR fallback 으로 등록 (`openrouter/<provider>/<id>`) |
| Ollama 카탈로그 (`qwen3.5:9b`, `qwen3.6:35b`, `llama3.1:8b`, `llama3.3:70b`, `nemotron3:33b`, `qwen3-coder-next:q8_0`, `bge-m3`) | 어느 노드든 보유하면 `ollama_chat/<id>` 로 그 노드(들)에 deployment 등록 (router 가 `least-busy` LB). 어느 노드도 없으면 `MODEL_OR_FREE` 매핑 + OR 키 있을 때 OR free fallback (기본 비활성, 사용자 검증 후 `lib.sh` 에서 활성화) |
| 이미지 모델 | 어느 ComfyUI 노드든 가중치 보유 시 alias 활성. shim 이 요청 alias→보유 노드 후보로 라우팅. `flux-dev` 는 `HF_TOKEN` 으로 다운로드 게이팅 |

자세한 매트릭스는 [모델 설정](models.md) 참고.
