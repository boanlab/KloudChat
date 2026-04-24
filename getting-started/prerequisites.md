# 사전 요구사항

## 시스템

| 항목 | 최소 | 권장 |
|---|---|---|
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| CPU | 8코어 | 16코어 이상 |
| RAM | 32GB | 64GB |
| 디스크 | 100GB | 500GB (모델 포함) |
| GPU | NVIDIA 10GB VRAM | 24GB VRAM 이상 |

VRAM 요구사항은 [GPU 메모리 가이드](../docs/gpu-memory.md)를 참고하세요.

## 소프트웨어

### Docker & Docker Compose

```bash
# Docker 설치
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# 버전 확인 (Compose v2 필요)
docker compose version
```

### NVIDIA Container Toolkit

```bash
# NVIDIA 드라이버 확인
nvidia-smi

# Container Toolkit 설치
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

### Ollama

KloudChat은 Ollama를 Docker 내부가 아닌 **호스트에서 직접** 실행합니다.

```bash
# 자동 설치 (systemd override까지 설정)
sudo ./scripts/install-ollama.sh

# 또는 공식 설치 스크립트 수동 실행
curl -fsSL https://ollama.com/install.sh | sh
```

설치 후 Docker 컨테이너가 `host.docker.internal:11434`로 접근할 수 있도록
Ollama가 `0.0.0.0`에서 리슨해야 합니다. `install-ollama.sh`가 이를 자동으로 설정합니다.

### 유틸리티

```bash
# jq (스크립트 필수)
sudo apt install -y jq wget curl
```
