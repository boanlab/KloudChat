# Multi-arch (amd64 + arm64) CUDA-capable whisper server.
# faster-whisper + FastAPI 로 OpenAI-compatible /v1/audio/transcriptions 노출.
# GPU 가용 시 cuda, 아니면 cpu 로 자동 폴백 (WHISPER_DEVICE=auto).
FROM nvidia/cuda:12.6.0-cudnn-runtime-ubuntu22.04

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv ffmpeg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir --upgrade pip && \
    python3 -m pip install --no-cache-dir \
      "faster-whisper>=1.0" "fastapi>=0.110" "uvicorn[standard]>=0.27" \
      "python-multipart>=0.0.9"

WORKDIR /app
COPY app.py .

ENV WHISPER_MODEL=large-v3 \
    WHISPER_DEVICE=auto \
    WHISPER_COMPUTE_TYPE=float16 \
    WHISPER_PORT=9000 \
    HF_HOME=/var/lib/whisper

EXPOSE 9000
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000"]
