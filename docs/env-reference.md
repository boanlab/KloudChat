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
| `LITELLM_SERVICE_KEY` | LibreChat → LiteLLM 서비스 키 | `init.sh` 자동 생성 |
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
| `OLLAMA_API_BASE` | 호스트 Ollama 접근 URL | `http://host.docker.internal:11434` |

## RAG 임베딩

| 변수 | 설명 | 기본값 |
|---|---|---|
| `EMBEDDINGS_PROVIDER` | 임베딩 공급자 | `ollama` |
| `EMBEDDINGS_MODEL` | 임베딩 모델명 | `bge-m3` |

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

## STT — Whisper (Linux + amd64 + NVIDIA GPU 전용)

| 변수 | 설명 | 값 |
|---|---|---|
| `STT_PROVIDER` | STT 공급자 | `openAI` |
| `STT_BASE_URL` | Whisper 서버 URL | `http://whisper:9000` |
| `STT_API_KEY` | 더미 키 (인증 불필요) | `dummy` |
| `STT_MODEL` | Whisper 모델명 | `whisper-1` |

## TTS — openedai-speech (multi-arch, CPU 가능)

| 변수 | 설명 | 값 |
|---|---|---|
| `TTS_PROVIDER` | TTS 공급자 | `openAI` |
| `TTS_BASE_URL` | TTS 서버 URL | `http://tts:8000` |
| `TTS_API_KEY` | 더미 키 | `dummy` |
| `TTS_MODEL` | 모델명 (`tts-1` 영어/piper, `tts-1-hd` 다국어/xtts_v2) | `tts-1-hd` |
| `TTS_VOICE` | 기본 음성 | `korean` |

지원 voice 매핑은 `tts-config/voice_to_speaker.yaml` 에서 정의 — 한국어 (xtts_v2): `korean`, `korean-female` / 영어 (piper, 빠름): `alloy`, `echo`, `nova`, `shimmer` / 영어 HD (xtts_v2): `english-hd`

## 이미지 생성 — SD.Next (Linux + amd64 + NVIDIA GPU 전용)

| 변수 | 설명 | 값 |
|---|---|---|
| `SD_WEBUI_URL` | SD.Next 서버 URL | `http://sdnext:7860` |

SD.Next는 A1111 호환 API(`/sdapi/v1/txt2img`)를 제공하므로 LibreChat과 직접 연동됩니다.

## 클라우드 LLM (선택)

클라우드 공급자 추가 시 아래 두 파일의 주석을 함께 해제해야 합니다.
- `docker-compose.yml` — litellm 서비스 `environment` 섹션
- `litellm-config.yaml` — `model_list` 섹션

| 변수 | 설명 |
|---|---|
| `OPENAI_API_KEY` | OpenAI API 키 |
| `ANTHROPIC_API_KEY` | Anthropic API 키 |
| `GEMINI_API_KEY` | Google Gemini API 키 |
