# 설치 가이드

> **빠른 경로:** `git clone` 후 `sudo ./scripts/install-ollama.sh` → `./scripts/full-setup.sh --yes` 두 줄이면 아래 1~9단계가 자동으로 끝납니다.
> 이 문서는 단계별 수동 진행이 필요한 경우 (스크립트가 실패한 경우, 일부 단계만 다시 돌릴 경우 등) 의 레퍼런스입니다.

## 1. 저장소 클론

```bash
git clone https://github.com/boanlab/KloudChat.git
cd KloudChat
```

## 2. Ollama 설치

KloudChat은 Ollama를 호스트에서 직접 실행합니다. Docker 컨테이너가
`host.docker.internal:11434`로 접근할 수 있도록 자동 설치 스크립트를 사용합니다.

```bash
sudo ./scripts/install-ollama.sh
```

스크립트가 수행하는 작업:
- Ollama 바이너리 설치 (공식 설치 스크립트)
- systemd override로 `0.0.0.0` 바인딩 설정
- `.env`의 `OLLAMA_API_BASE` 자동 갱신

이미 Ollama가 설치된 경우 스크립트가 기존 설정을 유지하면서 누락된 항목만 추가합니다.

## 3. 환경변수 설정

```bash
./scripts/gen-env.sh
```

`.env.example`의 `change-me-*` 값을 랜덤 시크릿으로 교체해 `.env`를 생성합니다.
이미 `.env`가 있으면 건너뜁니다. 재생성이 필요하면 `--force` 옵션을 사용합니다.

## 4. Ollama 모델 준비

docker compose 실행 전에 모델을 미리 내려받습니다.

```bash
# 기본값: qwen3.5:9b + nomic-embed-text
./scripts/download-ollama-models.sh

# 전체 모델
./scripts/download-ollama-models.sh all

# 옵션 확인
./scripts/download-ollama-models.sh --help
```

사용할 모델은 [모델 설정 가이드](../docs/models.md)를 참고하세요.

## 5. 이미지 생성 모델 다운로드 (amd64 전용)

SD.Next 서비스를 사용할 경우에만 필요합니다.

```bash
# 기본: SDXL + VAE (~7GB)
./scripts/download-sdnext-models.sh

# 옵션 확인
./scripts/download-sdnext-models.sh --help
```

## 6. RAG API 이미지 빌드

HWP 파일 지원을 위해 커스텀 이미지를 빌드합니다.

```bash
docker compose build rag_api
```

## 7. 서비스 시작

`scripts/deploy.sh`가 아키텍처를 자동 감지해 적절한 서비스를 시작합니다.

```bash
# 전체 서비스 시작 (아키텍처 자동 감지)
./scripts/deploy.sh up -d
```

- **amd64 + NVIDIA GPU**: 공통 서비스 (TTS 포함) + Whisper / SD.Next
- **arm64 또는 GPU 없는 amd64**: 공통 서비스 + TTS 만 (Whisper / SD.Next 자동 제외 — CUDA 전용)

서비스 상태 확인 시에도 동일하게 사용합니다.

```bash
./scripts/deploy.sh ps
./scripts/deploy.sh logs -f api
```

서비스 상태 확인:

```bash
docker compose ps
docker compose logs -f librechat  # LibreChat 로그
docker compose logs -f litellm   # LiteLLM 로그
```

## 8. 초기화

서비스가 완전히 기동된 후 1회 실행합니다.

```bash
./scripts/init.sh
```

다음 작업이 자동으로 처리됩니다:
- LiteLLM `admin` / `default` 팀 생성
- LibreChat용 서비스 키 발급 → `.env`의 `LITELLM_SERVICE_KEY` 자동 갱신

## 9. LibreChat 재시작

서비스 키가 `.env`에 반영됐으므로 LibreChat을 재시작합니다.

```bash
docker compose restart librechat
```

## 10. 접속 확인 + 사용자 생성

| 서비스 | URL |
|---|---|
| LibreChat | http://localhost:8080 |
| LiteLLM | http://localhost:8000 |

회원가입이 막혀 있거나 (`librechat.yaml` 의 `registration.allowedDomains`)
관리자가 직접 계정을 만드는 경우, 다음 한 줄로 LibreChat 사용자 + LiteLLM 사용자 + 키 발급을 동시에 처리합니다.

```bash
./scripts/manage.sh user create \
  --id admin@example.com --name '관리자' --username admin --password '비번8자이상' \
  --budget 9999
# → 출력된 sk-... 를 사용자에게 전달 → 본인이 LibreChat UI 의 API Key 칸에 입력
```

LiteLLM 사용자만 생성하려면 `--name/--username/--password` 를 빼면 됩니다 (기존 동작).
상세 옵션은 [CLI 관리](cli-management.md) 참고.

## 업그레이드

```bash
./scripts/deploy.sh pull
./scripts/deploy.sh up -d
```
