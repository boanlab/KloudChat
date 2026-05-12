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

> **DGX Spark (GB10)** 는 ComfyUI 컨테이너가 정상 동작합니다 (arm64+CUDA 빌드). 통합 메모리 아키텍처라 `nvidia-smi memory.total` 가 `[N/A]` 로 표시되며 KloudChat 스크립트는 이 경우 시스템 RAM 을 VRAM 으로 간주합니다. Whisper 만 amd64-only 라 자동 제외됩니다.

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

NVIDIA GPU 가 있는 Linux (amd64 또는 arm64) 에서 ComfyUI / Whisper 컨테이너를 띄우려면 필요합니다. DGX Spark (aarch64) 도 ComfyUI 컨테이너를 정상 사용하려면 이 단계가 필요합니다.

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

## DGX Spark (NVIDIA Grace Blackwell — GB10)

DGX Spark 는 Linux aarch64 + GB10 통합 메모리 환경입니다. 일반 Linux 설치와 동일하지만 몇 가지 특수성이 있습니다.

- **이미지 생성 (ComfyUI) 지원** — KloudChat 의 ComfyUI 컨테이너는 직접 빌드 (Dockerfile.comfyui, nvidia/cuda arm64 베이스) 라 DGX Spark 에서 정상 동작합니다. SDXL · Qwen-Image · Qwen-Image-Edit 모두 사용 가능.
- **Whisper (STT) 미지원** — 업스트림 이미지가 amd64-only 라 컨테이너로는 동작 불가.
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
