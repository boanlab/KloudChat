# 환경변수 레퍼런스

`./scripts/gen-env.sh`로 `.env`를 자동 생성합니다. `change-me-*` 값은 자동으로 랜덤 시크릿으로 교체되며, 나머지는 `.env.example` 기본값을 그대로 사용합니다.

## LibreChat

| 변수 | 설명 | 기본값 |
|---|---|---|
| `RAG_PORT` | RAG API 포트 | `8000` |
| `JWT_SECRET` | JWT 서명 시크릿 | 필수 (자동 생성) |
| `JWT_REFRESH_SECRET` | JWT 리프레시 토큰 시크릿 | 필수 (자동 생성) |
| `CREDS_KEY` | 자격증명 암호화 키 | 필수 (자동 생성) |
| `CREDS_IV` | 자격증명 암호화 IV | 필수 (자동 생성) |
| `MEILI_MASTER_KEY` | MeiliSearch 마스터 키 | 필수 (자동 생성) |
| `LITELLM_SERVICE_KEY` | LibreChat → LiteLLM 서비스 키 | `setup.sh` 자동 생성 |
| `ALLOW_REGISTRATION` | 회원가입 허용 여부 | `true` |

## MongoDB

| 변수 | 설명 |
|---|---|
| `MONGO_ROOT_USER` | MongoDB 루트 계정명 |
| `MONGO_ROOT_PASSWORD` | MongoDB 루트 비밀번호 |

## PostgreSQL (RAG)

| 변수 | 설명 | 기본값 |
|---|---|---|
| `POSTGRES_DB` | RAG DB 이름 | `kloudchat-ragdb` |
| `POSTGRES_USER` | RAG DB 사용자 | `kloudchat-rag` |
| `POSTGRES_PASSWORD` | RAG DB 비밀번호 | 필수 |

## Ollama

| 변수 | 설명 | 기본값 |
|---|---|---|
| `OLLAMA_URLS` | Ollama 백엔드(들) — csv. nginx (`ollama-lb`) 가 `least_conn` 으로 LB | `http://host.docker.internal:11434` |

### 멀티 노드 Ollama

`OLLAMA_URLS` 에 콤마로 여러 URL 을 적으면 nginx (`ollama-lb`) 가 `least_conn` 으로 분산합니다. LiteLLM 은 단일 endpoint (`http://ollama-lb:11434`) 만 호출하므로 model_list 도 모델당 1 entry. 매 노드에 `install-ollama.sh` + `download-ollama-models.sh` 로 동일 모델 세트를 pull 해 둬야 합니다.

```dotenv
OLLAMA_URLS=http://host.docker.internal:11434,http://gpu-node-1:11434,http://gpu-node-2:11434
```

`.env` 수정 후 `./scripts/gen-nginx-config.sh` → `docker compose restart ollama-lb`. `setup.sh` 는 자동 수행.

## RAG 임베딩

| 변수 | 설명 | 기본값 |
|---|---|---|
| `EMBEDDINGS_PROVIDER` | 임베딩 공급자 — `openai` (LiteLLM 경유, KloudChat 표준) | `openai` |
| `EMBEDDINGS_MODEL` | 임베딩 모델명 (LiteLLM 의 `model_name` 과 동일해야 함) | `bge-m3` |
| `RAG_OPENAI_BASEURL` | OpenAI-호환 임베딩 엔드포인트 | `http://litellm:8000/v1` |
| `RAG_OPENAI_API_KEY` | 위 엔드포인트 인증 키 (= `LITELLM_SERVICE_KEY`) | `setup.sh` 자동 생성 |

## LiteLLM

| 변수 | 설명 |
|---|---|
| `LITELLM_MASTER_KEY` | LiteLLM 마스터 키 (모든 권한) |
| `LITELLM_DB_USER` | LiteLLM PostgreSQL 사용자 |
| `LITELLM_DB_PASSWORD` | LiteLLM PostgreSQL 비밀번호 |

## SearXNG

| 변수 | 설명 | 기본값 |
|---|---|---|
| `SEARXNG_INSTANCE_URL` | LibreChat에서 접근하는 SearXNG URL | `http://searxng:8080` |

## 코드 인터프리터

| 변수 | 설명 | 기본값 |
|---|---|---|
| `CODE_INTERPRETER_API_KEY` | 코드 인터프리터 인증 키 | 필수 (자동 생성) |
| `CODE_INTERPRETER_MINIO_USER` | MinIO 사용자명 | `code-interpreter` |
| `CODE_INTERPRETER_MINIO_PASSWORD` | MinIO 비밀번호 | 필수 (자동 생성) |
| `CODE_INTERPRETER_MINIO_BUCKET` | MinIO 버킷명 | `code-interpreter` |
| `LIBRECHAT_CODE_BASEURL` | LibreChat → 코드 인터프리터 URL | `http://code-interpreter:8000` |

`CODE_INTERPRETER_API_KEY`는 LibreChat의 `LIBRECHAT_CODE_API_KEY`와 동일한 값을 사용합니다 (docker-compose.yml에서 단일 변수로 공유).

## 이미지 생성 — ComfyUI + A1111 shim

| 변수 | 설명 | 값 |
|---|---|---|
| `SD_WEBUI_URL` | LibreChat → shim URL (A1111 호환 어댑터) | `http://comfyui-shim:7860` |
| `COMFYUI_URLS` | shim → ComfyUI 백엔드. 콤마로 여러 노드 지정 가능 | `http://host.docker.internal:8188` |

LibreChat 의 내장 stable-diffusion 툴은 A1111 형식 (`/sdapi/v1/txt2img`) 만 알아듣고 ComfyUI 는 워크플로 JSON 기반 자체 API 를 쓰기 때문에, 두 서비스 사이에 얇은 FastAPI 어댑터 (`comfyui-shim`) 를 둡니다. 사용 모델은 요청의 `override_settings.sd_model_checkpoint` 값으로 선택: `sdxl`, `qwen-image`, `qwen-image-edit`.

ComfyUI 자체는 항상 native (systemd) 로 실행 — `scripts/install-comfyui.sh` 로 각 GPU 노드에 설치.

### `COMFYUI_URLS` 패턴

| 값 | 의미 |
|---|---|
| `http://host.docker.internal:8188` (기본) | compose 호스트와 같은 머신에 native 설치된 ComfyUI |
| `http://gpu-node-1:8188` | 원격 GPU 노드 1대, 모든 요청이 해당 노드로 |
| `http://gpu-node-1:8188,http://gpu-node-2:8188` | 원격 GPU 노드 N대. shim 이 매 요청마다 `/queue` 깊이로 가장 한가한 노드 선택. `prompt_id → 노드` 매핑을 in-memory 로 유지해 polling/fetch 일관성 보장 |

가중치 다운로드: `./scripts/download-image-models.sh` ( `/opt/comfyui/app/ComfyUI/models` 로, 필요시 sudo 자동 prompt).

## 클라우드 LLM (선택)

API 키만 `.env` 에 채우면 LiteLLM 컨테이너에 자동으로 주입됩니다 (`docker-compose.yml`). 실제로 모델을 노출하려면 `litellm-config.yaml` 의 해당 `model_list` 항목 주석도 풀어야 합니다.

| 변수 | 설명 |
|---|---|
| `OPENAI_API_KEY` | OpenAI API 키 |
| `ANTHROPIC_API_KEY` | Anthropic API 키 |
| `GEMINI_API_KEY` | Google Gemini API 키 |
| `OPENROUTER_API_KEY` | OpenRouter API 키. `MODEL_OPENROUTER_FREE` (`scripts/lib.sh`) 에 매핑된 모델 (기본 `gpt-oss:20b` / `gpt-oss:120b`) 이 있으면 **필수** — LiteLLM 이 해당 모델들을 OR free tier 로 라우팅. 클라우드 모델 게이트웨이로도 사용 가능 |
