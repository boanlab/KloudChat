# 설치 가이드

> **빠른 경로:** `git clone` 후 `sudo ./scripts/install-ollama.sh` → `./scripts/setup.sh --yes` 두 줄이면 1~9 단계가 자동으로 끝납니다. 이 문서는 단계별 수동 진행이 필요한 경우 (스크립트가 실패한 경우, 일부 단계만 다시 돌릴 경우 등) 의 레퍼런스입니다.

`scripts/setup.sh` 와 `scripts/deploy.sh` 는 아키텍처·GPU 를 자동 감지해서 분기합니다. 별도로 환경을 명시할 필요가 없습니다.

## 1. 저장소 클론

```bash
git clone https://github.com/boanlab/KloudChat.git
cd KloudChat
```

## 2. Ollama 설치

KloudChat 은 Ollama 를 호스트에서 직접 실행합니다. Docker 컨테이너는 `host.docker.internal:11434` 으로 접근하므로 `OLLAMA_HOST=0.0.0.0` 바인딩이 필요합니다.

```bash
# systemd override + 0.0.0.0 바인딩
sudo ./scripts/install-ollama.sh
```

이미 Ollama 가 설치돼 있으면 기존 설정을 유지하면서 누락된 항목만 추가합니다.

## 3. 환경변수 설정

```bash
./scripts/gen-env.sh
```

`.env.example` 의 `change-me-*` 값을 랜덤 시크릿으로 교체해 `.env` 를 생성합니다. 이미 `.env` 가 있으면 건너뜁니다. 재생성은 `--force`.

## 4. Ollama 모델 준비

docker compose 실행 전에 모델을 미리 내려받습니다.

```bash
# 기본값: qwen3.5:9b + bge-m3 (embed)
./scripts/download-ollama-models.sh

# 전체 모델
./scripts/download-ollama-models.sh all

# 옵션 확인
./scripts/download-ollama-models.sh --help
```

사용할 모델은 [모델 설정 가이드](../docs/models.md) 참고.

## 5. 이미지 생성 모델 다운로드 (Linux + NVIDIA GPU, amd64/arm64)

ComfyUI 컨테이너가 활성화되는 환경 (Linux + NVIDIA GPU + nvidia container runtime) 에서만 의미가 있습니다. GPU 없는 호스트는 이 단계를 건너뛰세요.

```bash
./scripts/download-image-models.sh            # SDXL + Qwen-Image + Qwen-Image-Edit (~50GB)
./scripts/download-image-models.sh --help     # alias 별 선택
```

상세 표·alias 는 [모델 설정 가이드 — 이미지 생성](../docs/models.md#이미지-생성-모델-comfyui--a1111-shim) 참고.

## 6. 커스텀 이미지 빌드

다섯 가지 커스텀 이미지를 빌드합니다 (마지막 두 개는 NVIDIA GPU 환경에서만). `setup.sh` 는 이 단계를 자동으로 수행합니다.

```bash
./scripts/deploy.sh build rag_api            # HWP 파일 변환 (~1~3분)
./scripts/deploy.sh build librechat          # 소스 빌드 (~5~10분)
./scripts/deploy.sh build code-interpreter   # NanumGothic 한글 폰트 (~1분)
./scripts/deploy.sh build comfyui            # ComfyUI + PyTorch CUDA (~10~15분, GPU 환경만)
./scripts/deploy.sh build comfyui-shim       # FastAPI A1111 어댑터 (~30초, GPU 환경만)
```

## 7. 서비스 시작

`scripts/deploy.sh` 가 환경을 자동 감지해 적절한 compose 조합을 선택합니다.

```bash
./scripts/deploy.sh up -d
./scripts/deploy.sh ps
./scripts/deploy.sh logs -f librechat
```

분기 규칙:

| 환경 | 사용되는 compose 파일 | 비고 |
|---|---|---|
| Linux + amd64 + NVIDIA + nvidia runtime | base + `gpu.yml` + `amd64.yml` | ComfyUI + Whisper 포함 |
| Linux + arm64 + NVIDIA + nvidia runtime (DGX Spark) | base + `gpu.yml` | ComfyUI 포함, Whisper 제외 (이미지가 amd64-only) |
| 그 외 (GPU 없는 호스트) | base 만 | ComfyUI / Whisper 자동 제외, TTS 는 동작 |

## 8. 초기화

서비스가 완전히 기동된 후 1회 실행합니다.

```bash
./scripts/init.sh
```

다음 작업이 자동으로 처리됩니다:
- LiteLLM `admin` / `default` 팀 생성
- LibreChat 용 서비스 키 발급 → `.env` 의 `LITELLM_SERVICE_KEY` 자동 갱신

## 9. LibreChat 재시작

서비스 키가 `.env` 에 반영됐으므로 LibreChat 을 재시작합니다.

```bash
./scripts/deploy.sh restart librechat
```

## 10. 접속 확인 + 사용자 생성

| 서비스 | URL |
|---|---|
| LibreChat | http://localhost:8080 |
| LiteLLM | http://localhost:8000 |

회원가입이 막혀 있거나 (`librechat.yaml` 의 `registration.allowedDomains`) 관리자가 직접 계정을 만드는 경우, 다음 한 줄로 LibreChat 사용자 + LiteLLM 사용자 + 키 발급 + LibreChat keys 자동 등록 + 두 개의 기본 agent (Gemma4 / Qwen3.5) + default preset 생성을 동시에 처리합니다.

```bash
./scripts/manage.sh user create \
  --id admin@example.com --name '관리자' --username admin --password '비번8자이상' \
  --budget 9999
```

`--name/--username/--password` 를 빼면 LiteLLM 사용자만 생성되며 키 자동 등록 / agent 생성도 함께 생략됩니다. 상세 옵션은 [CLI 관리](cli-management.md) 참고.

## 업그레이드

```bash
./scripts/deploy.sh pull
./scripts/deploy.sh up -d
```
