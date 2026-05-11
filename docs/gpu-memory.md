# GPU 메모리 가이드

## 서비스별 VRAM 점유

| 서비스 | 모델 | VRAM | 상시 점유 |
|---|---|---|---|
| Ollama | qwen3.5:9b (Q4) | ~5GB | 대화 후 2분 유지 |
| Ollama | qwen3.5:35b (Q4) | ~20GB | 대화 후 2분 유지 |
| Ollama | gemma4:26b (Q4) | ~16GB | 대화 후 2분 유지 |
| Ollama | qwen3-coder-next (Q4_K_M) | ~51GB | 대화 후 2분 유지 |
| Ollama | qwen3-coder-next (Q8_0) | ~84GB | 대화 후 2분 유지 |
| Ollama | nomic-embed-text | ~500MB | RAG 요청 시 |
| Whisper | large-v3 | ~6GB | 요청 시만 |
| Whisper | medium | ~3GB | 요청 시만 |
| TTS (xtts_v2) | ~1.8B | ~3GB (CPU 모드) | warm 5분 |
| TTS (piper) | ~30M | <1GB (CPU 모드) | warm 5분 |
| SD.Next | SDXL | ~10GB | 요청 시만 |
| SD.Next | SDXL-Turbo | ~8GB | 요청 시만 |

> TTS(`openedai-speech`)는 multi-arch + CPU 동작이라 GPU VRAM 소비 0. 표 위 항목은 모델 자체 메모리 footprint 참고용.

모든 서비스는 동일한 물리 GPU VRAM을 공유합니다. 격리나 예약 없이 선착순으로 점유합니다.

## 시나리오별 필요 VRAM

| 시나리오 | 구성 | 필요 VRAM | 맞는 GPU |
|---|---|---|---|
| 텍스트 채팅 + TTS (소형 모델) | qwen3.5:9b + TTS | ~7GB | RTX 3080 10GB |
| 텍스트 채팅 + TTS (대형 모델) | qwen3.5:35b + TTS | ~22GB | RTX 3090 / 4090 24GB |
| 텍스트 채팅 + STT + TTS | qwen3.5:35b + Whisper(medium) + TTS | ~25GB | A100 40GB |
| 텍스트 채팅 + STT + TTS + 이미지 생성 | qwen3.5:35b + Whisper(large) + TTS + SD.Next(SDXL) | ~38GB | A100 80GB |
| 코딩 특화 채팅 + TTS (Q4) | qwen3-coder-next(Q4) + TTS | ~53GB | A100 80GB |
| 코딩 특화 채팅 + TTS (Q8, 고품질) | qwen3-coder-next(Q8) + TTS | ~86GB | H100 80GB×2 |

## Ollama 메모리 관리

Ollama 환경변수 설정(`KEEP_ALIVE`, `MAX_LOADED_MODELS` 등)은
[Ollama 튜닝 가이드](ollama-tuning.md)를 참고하세요.

## VRAM 부족 시 조치

**Whisper 모델 경량화** — 한국어 인식률 차이 미미

```yaml
# docker-compose.yml whisper 서비스
environment:
  ASR_MODEL: medium    # large-v3(~6GB) → medium(~3GB)
```

**이미지 생성 서비스 필요 시만 기동**

```bash
# 평소에는 중지
docker compose stop sdnext

# 이미지 생성 필요할 때만 시작
docker compose start sdnext
```

**Ollama 모델 즉시 언로드**

```bash
# 특정 모델 강제 언로드 (호스트에서 직접 실행)
ollama stop qwen3.5:35b        # 또는 qwen3-coder-next:q4_K_M 등
```
