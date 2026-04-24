# 설치 가이드

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

- **amd64**: 공통 서비스 + Whisper / Kokoro / SD.Next
- **arm64**: 공통 서비스만 (Whisper / Kokoro / SD.Next 제외)

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

## 10. 접속 확인

| 서비스 | URL |
|---|---|
| LibreChat | http://localhost:8080 |
| LiteLLM | http://localhost:8000 |

LibreChat에서 첫 회원가입 시 생성되는 계정이 관리자가 됩니다.

## 업그레이드

```bash
./scripts/deploy.sh pull
./scripts/deploy.sh up -d
```
