# 사전 요구사항

이 문서는 환경별로 KloudChat 을 띄우기 위한 호스트 준비 사항을 설명합니다.
지원 환경 전체 매트릭스는 [README](../README.md#지원-환경) 참고.

## 하드웨어

| 항목 | 최소 | 권장 |
|---|---|---|
| CPU | 8 코어 | 16 코어 이상 |
| RAM | 32 GB | 64 GB |
| 디스크 | 100 GB | 500 GB (모델 포함) |
| GPU (선택) | NVIDIA 10 GB VRAM | 24 GB VRAM 이상 |

VRAM 요구사항은 [GPU 메모리 가이드](../docs/gpu-memory.md) 참고.

> **macOS** 에서는 Whisper(STT) 와 SD.Next(이미지) 컨테이너가 빠지므로 GPU 가 필요 없습니다.
> Ollama 는 Metal GPU 가속을 자동 사용합니다.

> **DGX Spark (GB10)** 는 통합 메모리 아키텍처라 `nvidia-smi memory.total` 가 `[N/A]` 로 표시됩니다.
> KloudChat 스크립트는 이 경우 시스템 RAM 을 VRAM 으로 간주합니다.

---

## Linux (x86_64 / aarch64)

### Docker & Docker Compose v2

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# Compose v2 확인 (Docker 20.10+ 동봉)
docker compose version
```

### NVIDIA Container Toolkit (GPU 사용 시)

x86_64 + NVIDIA GPU 에서 Whisper / SD.Next 컨테이너를 띄우려면 필요합니다.
DGX Spark (aarch64) 의 경우 Ollama 만 GPU 를 직접 사용하므로 이 단계는 선택입니다.

```bash
nvidia-smi   # 드라이버 확인

curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 동작 확인
docker run --rm --gpus all nvidia/cuda:12.0-base-ubuntu22.04 nvidia-smi
```

### Ollama (호스트 직접 실행)

```bash
sudo ./scripts/install-ollama.sh
```

스크립트가 수행하는 작업:
- Ollama 바이너리 설치 (공식 `install.sh`)
- systemd override 로 `OLLAMA_HOST=0.0.0.0:11434` 설정 (Docker → host.docker.internal 접근)
- 서비스 활성화 + 응답 확인
- `.env` 의 `OLLAMA_API_BASE` 자동 갱신

### 유틸리티

```bash
# Debian / Ubuntu
sudo apt install -y jq curl wget

# Fedora / RHEL
sudo dnf install -y jq curl wget
```

---

## macOS (Intel / Apple Silicon)

> macOS 에서는 Whisper(STT) 와 SD.Next(이미지) 가 자동 제외됩니다.
> 채팅 · RAG · 웹 검색 · 코드 인터프리터 · TTS 는 모두 동작합니다.

### Docker Desktop

[Docker Desktop for Mac](https://docs.docker.com/desktop/install/mac-install/) 을 설치하고 실행해 둡니다.

```bash
docker compose version   # v2 가 동봉되어 있는지 확인
```

### Homebrew (권장)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install jq curl wget
```

### Ollama (호스트 직접 실행)

```bash
./scripts/install-ollama.sh    # sudo 불필요
```

스크립트가 수행하는 작업:
- Ollama 설치 (Homebrew 가 있으면 `brew install ollama`, 없으면 공식 `install.sh`)
- `launchctl setenv OLLAMA_HOST 0.0.0.0:11434` 등록
- 이미 떠 있는 Ollama.app 가 있으면 재시작 (환경변수 반영)
- `.env` 의 `OLLAMA_API_BASE` 자동 갱신

재부팅 후에도 환경변수를 유지하려면 [Ollama 튜닝 가이드](../docs/ollama-tuning.md) 의 macOS — launchctl / LaunchAgent 섹션을 참고하세요.

---

## DGX Spark (NVIDIA Grace Blackwell — GB10)

DGX Spark 는 Linux aarch64 + GB10 통합 메모리 환경입니다. 일반 Linux 설치와 동일하지만 몇 가지 특수성이 있습니다.

- **CUDA 컨테이너 (Whisper / SD.Next) 미지원** — 두 이미지가 amd64-only 라 컨테이너로는 동작 불가. 텍스트 채팅·RAG·TTS 는 정상 동작합니다.
- **`nvidia-smi memory.total = [N/A]`** — `setup.sh` 는 자동으로 시스템 RAM (~120 GB) 을 VRAM 으로 간주해 모델을 추천합니다.
- **`CUDA_VISIBLE_DEVICES`** 를 비우면 GPU 가 숨겨져 CPU 추론으로 폴백되므로 `0` 으로 명시 (`docs/ollama-tuning.md` GB10 섹션 참고).

이외에는 일반 Linux 설치 절차 ([위 섹션](#linux-x86_64--aarch64)) 와 동일합니다.

---

## Docker 동작 확인 (모든 환경 공통)

```bash
docker run --rm hello-world
docker compose version
```

준비가 끝났으면 [설치 가이드](installation.md) 로 넘어갑니다.
