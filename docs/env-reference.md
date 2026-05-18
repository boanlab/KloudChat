# 환경변수 레퍼런스

> 이 문서: `.env` 의 모든 변수가 무엇이고 누가 채우는지(사용자 vs `gen-env.sh` vs `setup.sh`). 처음 띄우는 거면 [README](../README.md) 만 보면 됩니다 — 시나리오별로 실제 채워야 하는 키만 안내합니다.

`./scripts/gen-env.sh` 가 `.env.example` 을 복사하면서 `change-me-*` 패턴을 랜덤 시크릿으로 치환해 `.env` 를 생성합니다. 외부 키 (OpenRouter / HF_TOKEN) 와 백엔드 토폴로지 (`OLLAMA_URLS`, `COMFYUI_URLS`) 는 직접 채워야 합니다.

`.env` 는 4섹션:

1. **사용자 입력** — 필수 키 + 외부 연동
2. **서비스 자격증명** — `gen-env.sh` 가 채움 (DB pw, JWT, MEILI, master keys 등)
3. **setup.sh 발급** — `LITELLM_SERVICE_KEY`, `RAG_OPENAI_API_KEY` (수동 편집 불필요)
4. **내부 endpoint** — 보통 기본값 유지

## 1. 사용자 입력

| 변수 | 종류 |
|---|---|
| `APP_TITLE` | 브랜딩 |
| `WELCOME_BACK_MESSAGE` | 브랜딩 |
| `HELP_AND_FAQ_URL` | 브랜딩 |
| `CUSTOM_FOOTER` | 브랜딩 |
| `DOMAIN_CLIENT` | 공개 URL |
| `DOMAIN_SERVER` | 공개 URL |
| `OLLAMA_URLS` | 백엔드 토폴로지 |
| `COMFYUI_URLS` | 백엔드 토폴로지 |
| `WHISPER_URLS` | 백엔드 토폴로지 (선택) |
| `OPENROUTER_API_KEY` | 외부 키 (선택) |
| `HF_TOKEN` | 외부 키 (선택) |
| `ALLOW_REGISTRATION` | 인증 정책 |
| `ALLOW_PASSWORD_RESET` | 인증 정책 |
| `EMAIL_SERVICE` | SMTP 설정 |
| `EMAIL_USERNAME` | SMTP 설정 |
| `EMAIL_PASSWORD` | SMTP 설정 |
| `EMAIL_FROM` | SMTP 설정 |
| `EMAIL_FROM_NAME` | SMTP 설정 |

**필수**: `OPENROUTER_API_KEY` 또는 reachable `OLLAMA_URLS` 노드 ≥1 — 둘 다 없으면 `setup.sh` 가 거부.

### 브랜딩

`APP_TITLE` 은 LibreChat 좌상단 + 브라우저 탭 타이틀, 기본 `KloudChat`. `WELCOME_BACK_MESSAGE` 는 로그인 페이지 헤딩 — 모든 locale 에 같은 값으로 들어가며, 빈 값이면 LibreChat 기본 문구.

`HELP_AND_FAQ_URL` 은 우상단 메뉴의 "Help & FAQ" 링크 URL. `/` 면 메뉴 자체가 숨겨집니다 (기본).

`CUSTOM_FOOTER` 는 채팅창 하단 푸터 ("LibreChat v0.8.5 - ..." 자리). 빈 값이면 LibreChat 기본 표시, 공백 1개면 사실상 숨김. LibreChat 은 yaml `interface.customFooter` 가 아닌 이 env 변수에서만 읽습니다.

### 공개 URL

`DOMAIN_CLIENT` / `DOMAIN_SERVER` 는 LibreChat 이 외부 노출용 base URL 로 쓰는 값 — 이메일 본문 링크 (비밀번호 reset / 이메일 인증), OAuth 콜백, 이미지 base path 에 사용됩니다. 비어 있으면 reset 메일이 `undefined/reset/...` 로 렌더링되니 실배포 도메인으로 채워야 합니다. 보통 둘 다 같은 값 (예: `https://chat.example.com`).

### 백엔드 토폴로지

기본은 compose 호스트와 같은 머신에 Ollama/ComfyUI/Whisper 가 native 로 설치된 단일 호스트 셋업 — `gen-env.sh` 가 만드는 `.env.example` 디폴트가 그 형태입니다:

```dotenv
OLLAMA_URLS=http://host.docker.internal:11434
COMFYUI_URLS=http://host.docker.internal:8188
WHISPER_URLS=http://host.docker.internal:9000
```

GPU 가 별도 노드(들)에 있거나 멀티 노드 분산이 필요하면 csv 로 확장 (각 노드에 install 스크립트 동일하게 적용):

```dotenv
OLLAMA_URLS=http://gpu-node-1:11434,http://gpu-node-2:11434
COMFYUI_URLS=http://gpu-node-1:8188,http://gpu-node-2:8188
WHISPER_URLS=http://gpu-node-1:9000,http://gpu-node-2:9000
```

**Ollama** — LiteLLM router 가 각 노드를 직접 호출. discovery 가 노드별 `/api/tags` 를 union 으로 합치고, 모델 하나당 보유 노드 수만큼 같은 `model_name` deployment 를 등록 → router 가 `least-busy` 로 LB. 모델은 노드별로 자유롭게 pull (이기종 GPU OK), 같은 모델을 여러 노드에 pull 하면 자동 분산.

**ComfyUI** — shim 이 alias 별 보유 노드 매핑을 `/object_info` 디스커버리로 캐시 (TTL `MODEL_DISCOVERY_TTL_SEC`, 기본 300s). 매 요청 alias 로 후보 노드 좁힌 뒤 `/queue` 깊이로 LB, `prompt_id → 노드` 매핑 in-memory 유지. 노드별로 다른 가중치 셋 OK.

**Whisper** — shim 이 `/health` 로 reachable 노드만 추리고 (10s 캐시), in-flight 카운터 + 같은 호스트 ollama VRAM 점유로 LB. 모든 노드가 동일 `WHISPER_MODEL` 을 서빙한다고 가정 — 노드별로 다른 모델은 지원 안 함. `WHISPER_URL` (단수) 은 항상 `http://whisper-shim:9000` 유지하고 backend 만 `WHISPER_URLS` 로 관리.

`.env` 수정 후 `./scripts/gen-litellm-config.sh && docker compose restart litellm` (Ollama 토폴로지 변경 시) — `setup.sh` 가 자동 수행.

| `COMFYUI_URLS` 패턴 | 의미 |
|---|---|
| `http://host.docker.internal:8188` | compose 호스트 native |
| `http://gpu-node-1:8188` | 원격 GPU 노드 1대 |
| 여러 URL csv | 원격 GPU 노드 N대, shim 분배 |

### 외부 키

`OPENROUTER_API_KEY` 가 있으면 commercial 카탈로그 (gpt-*/claude-*/gemini-*) 가 OR 경유로 등록. 없으면 commercial 모델 전부 미등록.

`HF_TOKEN` 은 HuggingFace gated repo 토큰 — `flux-dev` 다운로드 전제 (다른 이미지 모델은 무관).

### 인증 + 이메일

LibreChat 가 `/app/.env` 를 직접 로드해 읽는 변수들 (docker-compose 의 `environment:` 와 별개).

`ALLOW_REGISTRATION=false` 권장 — 회원가입 페이지에서 신규 가입을 차단하고 관리자가 `./scripts/manage.sh user create` 로만 발급. `true` 면 누구나 가입 가능.

`ALLOW_PASSWORD_RESET=true` 면 로그인 화면의 "Forgot password?" 가 동작 — 실제 메일 발송하려면 아래 `EMAIL_*` 채워야 합니다.

`EMAIL_*` 는 nodemailer SMTP 자격증명. `EMAIL_SERVICE=gmail` 이 기본 (다른 transport 도 가능 — outlook, sendgrid 등). Gmail 사용 시 2FA + [앱 비밀번호](https://support.google.com/accounts/answer/185833) 필요 (일반 계정 비밀번호 안 됨). `EMAIL_FROM` 은 발신 주소, `EMAIL_FROM_NAME` 은 메일 클라이언트에 표시되는 발신자 이름.

## 2. 서비스 자격증명 (gen-env.sh 자동 생성)

### LibreChat 핵심 시크릿

| 변수 | 용도 |
|---|---|
| `JWT_SECRET` | JWT 서명 |
| `JWT_REFRESH_SECRET` | JWT 리프레시 |
| `CREDS_KEY` | 자격증명 암호화 키 |
| `CREDS_IV` | 자격증명 암호화 IV |
| `MEILI_MASTER_KEY` | MeiliSearch 마스터 |

### MongoDB / PostgreSQL

| 변수 | 용도 |
|---|---|
| `MONGO_ROOT_USER` | MongoDB 루트 계정 |
| `MONGO_ROOT_PASSWORD` | MongoDB 루트 비밀번호 |
| `POSTGRES_DB` | RAG pgvector DB 이름 |
| `POSTGRES_USER` | RAG pgvector 사용자 |
| `POSTGRES_PASSWORD` | RAG pgvector 비밀번호 |

기본값은 각각 `kloudchat-librechat`, `kloudchat-ragdb`, `kloudchat-rag`. 비밀번호는 `gen-env.sh` 가 랜덤 생성.

### LiteLLM

| 변수 | 용도 |
|---|---|
| `LITELLM_DB_USER` | LiteLLM PostgreSQL 사용자 |
| `LITELLM_DB_PASSWORD` | LiteLLM PostgreSQL 비밀번호 |
| `LITELLM_MASTER_KEY` | LiteLLM 마스터 키 (모든 권한) |

기본 사용자 `kloudchat-litellm`. 마스터 키는 admin UI 로그인 + 가상 키 발급 권한.

### Code Interpreter + MinIO

| 변수 | 용도 |
|---|---|
| `CODE_INTERPRETER_API_KEY` | 인증 키 |
| `CODE_INTERPRETER_MINIO_USER` | MinIO 사용자 |
| `CODE_INTERPRETER_MINIO_PASSWORD` | MinIO 비밀번호 |
| `CODE_INTERPRETER_MINIO_BUCKET` | MinIO 버킷 |

API 키는 LibreChat 의 `LIBRECHAT_CODE_API_KEY` 와 같은 값. 기본 사용자/버킷은 `code-interpreter`.

## 3. setup.sh 가 발급

| 변수 | 발급 시점 |
|---|---|
| `LITELLM_SERVICE_KEY` | `setup.sh` 5단계 |
| `RAG_OPENAI_API_KEY` | 동일 키 값 |

LibreChat / RAG API → LiteLLM 호출용 가상 키. `./scripts/manage.sh key issue --service librechat` 가 발급해서 채웁니다. 수동으로 비워두면 됨 — 재발급은 같은 명령 재실행.

## 4. 내부 endpoint (보통 기본값 유지)

| 변수 | 기본값 |
|---|---|
| `RAG_PORT` | `8000` |
| `EMBEDDINGS_PROVIDER` | `openai` |
| `EMBEDDINGS_MODEL` | `bge-m3` |
| `RAG_OPENAI_BASEURL` | `http://litellm:8000/v1` |
| `SEARXNG_INSTANCE_URL` | `http://searxng:8080` |
| `LIBRECHAT_CODE_BASEURL` | `http://code-interpreter:8000` |
| `SD_WEBUI_URL` | `http://comfyui-shim:7860` |
| `WHISPER_URL` | `http://whisper-shim:9000` |
| `WHISPER_OR_MODEL` | `whisper-1` (OR 폴백 시) |

`EMBEDDINGS_PROVIDER` 는 RAG 임베딩 공급자 — LiteLLM 경유라 항상 `openai`. `EMBEDDINGS_MODEL` 은 LiteLLM 의 model_name 과 일치해야 함 (기본 `bge-m3`). Ollama 노드에 bge-m3 가 0대 + OR 키 있을 때 `setup.sh` 가 `text-embedding-3-small` 로 자동 swap (OR 경유 OpenAI 임베딩).

`WHISPER_URL` 은 youtube MCP 가 호출하는 shim endpoint (compose 내부 라우터). shim 의 backend 멀티노드 목록은 `WHISPER_URLS` 로 별도 관리.

### compose / shim 하드코딩 (`.env` 외 설정)

다음 두 값은 `.env` 에 없고 각각 `docker-compose.yml` 의 service environment 와 shim 의 Python default 로 박혀 있음 — 변경하려면 해당 위치 수정 후 컨테이너 recreate:

| 변수 | 위치 | 기본값 | 용도 |
|---|---|---|---|
| `OR_IMAGE_MODELS` | `docker-compose.yml` (`comfyui-shim`) | (비어있음) | shim 이 LiteLLM 경유 외부 image API 로 라우팅할 alias 매핑. 현재 비활성 — OR 의 image 모델들 응답 안정성 문제로 외부 image 전체 disable, ollama 에이전트만 ComfyUI 로 image 생성. |
| `OLLAMA_VRAM_LOADED_THRESHOLD_BYTES` | `comfyui-shim/app.py`, `whisper-shim/app.py` Python default | `32212254720` (30 GiB) | shim 의 VRAM-aware 라우팅 임계 — 노드의 ollama 가 이 값 초과로 VRAM 점유 중이면 다른 노드 우선 |

## 모델 등록 동작 요약

| 조건 | 결과 |
|---|---|
| OR 키 있음 | commercial 카탈로그 + OR 임베딩 (`text-embedding-3-small`) 등록 |
| OR 키 없음 | commercial + OR 임베딩 전부 미등록 |
| Ollama 모델 보유 노드 ≥1 | `ollama_chat/<id>` × 노드 수 |
| Ollama 보유 0 | 미등록 |
| Ollama bge-m3 0 + OR 키 | `setup.sh` 가 `EMBEDDINGS_MODEL=text-embedding-3-small` 자동 swap |
| ComfyUI 노드에 가중치 있음 | alias 활성 |

native API 직결은 미지원. `flux-dev` 는 `HF_TOKEN` 으로 다운로드 게이팅.

자세한 매트릭스는 [모델 설정](models.md) 참고.
