# Ollama 튜닝 가이드

Ollama 는 호스트에서 직접 실행되므로 systemd 의 환경변수 메커니즘으로 설정합니다.

## 설정 위치 — systemd override

```
/etc/systemd/system/ollama.service.d/override.conf
```

직접 편집하거나 `sudo systemctl edit ollama` 로 열 수 있습니다. 변경 후에는 반드시 적용합니다:

```bash
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

## 현재 설정 예시

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
Environment="OLLAMA_MODELS=/home/boan/.ollama/models"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_CONTEXT_LENGTH=8192"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_MAX_LOADED_MODELS=6"
Environment="CUDA_VISIBLE_DEVICES=0"
```

## 환경변수 레퍼런스

### CUDA_VISIBLE_DEVICES

CUDA가 인식할 GPU 인덱스를 지정합니다.

| 값 | 동작 |
|---|---|
| `0` | GPU 0 사용 (단일 GPU 환경 기본값) |
| `0,1` | 멀티 GPU |
| `` (빈 문자열) | GPU 숨김 → CPU 추론 (의도치 않은 경우 주의) |

### OLLAMA_FLASH_ATTENTION

Flash Attention 활성화 여부. 기본값 `false`.

긴 컨텍스트에서 KV 캐시 메모리 사용량을 줄이고 처리 속도를 높입니다.
CUDA compute capability 8.0 이상(Ampere·Ada·Blackwell)에서 지원됩니다.

```ini
Environment="OLLAMA_FLASH_ATTENTION=1"
```

### OLLAMA_CONTEXT_LENGTH

모델 로드 시 기본 컨텍스트 길이(토큰 수). 기본값은 VRAM 기반 자동 계산입니다.

VRAM 이 클수록 자동값이 수십만 토큰까지 설정되어 모델 로드가 느려지거나 불필요하게 많은 KV 캐시를 점유할 수 있으므로 명시적으로 지정하는 것을 권장합니다.

```ini
Environment="OLLAMA_CONTEXT_LENGTH=8192"   # 일반 채팅
Environment="OLLAMA_CONTEXT_LENGTH=32768"  # 긴 문서 처리
```

### OLLAMA_KEEP_ALIVE

마지막 요청 이후 모델을 메모리에 유지하는 시간. 기본값 `5m`.

| 값 | 동작 |
|---|---|
| `5m` | 5분 후 언로드 |
| `0` | 요청 완료 즉시 언로드 |
| `-1` | 영구 유지 (서버 재시작 전까지) |

VRAM 여유가 충분하면 `-1`로 설정해 재로드 지연을 없앱니다.

```ini
Environment="OLLAMA_KEEP_ALIVE=-1"
```

### OLLAMA_MAX_LOADED_MODELS

동시에 메모리에 유지할 최대 모델 수. 기본값은 GPU 있을 때 `3`.

VRAM 이 부족해지면 Ollama 가 LRU (가장 오래 미사용) 모델을 자동으로 내립니다. 보유 모델 수 이상으로 설정해도 무방합니다.

```ini
Environment="OLLAMA_MAX_LOADED_MODELS=6"
```

### OLLAMA_MAX_QUEUE

동시 처리 대기열 크기. 기본값 `512`. 사용자가 많을 경우 증가를 고려합니다.

### OLLAMA_NUM_PARALLEL

단일 모델의 동시 요청 처리 수. 기본값 `1`. 멀티유저 환경에서 응답성을 높이려면 늘립니다 (VRAM 과 트레이드오프).

```ini
Environment="OLLAMA_NUM_PARALLEL=4"
```

## GPU별 권장 설정

### GPU 추론 여부 확인

`ollama ps` 의 `size_vram` 값이 `0` 이면 CPU 로 추론 중입니다. 이 경우 `CUDA_VISIBLE_DEVICES` 가 비어 있거나 잘못 설정된 것이므로 확인합니다.

```bash
curl -s http://localhost:11434/api/ps | jq '.models[] | {name, size_vram}'
```

---

### GeForce RTX 5090

| 항목 | 값 |
|---|---|
| 아키텍처 | Blackwell (GB202) |
| VRAM | 32 GB GDDR7 |
| Compute Capability | 12.0 |
| nvidia-smi 메모리 표시 | 정상 |

- Flash Attention 지원 → `OLLAMA_FLASH_ATTENTION=1` 권장
- 32 GB로 qwen3.6:35b(~22 GB) 단독 탑재 가능, 동시 탑재는 9b + embed 조합 정도로 제한
- VRAM 여유가 적으므로 `OLLAMA_KEEP_ALIVE=5m` 또는 `OLLAMA_MAX_LOADED_MODELS=2` 조합 고려

```ini
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_CONTEXT_LENGTH=8192"
Environment="OLLAMA_KEEP_ALIVE=5m"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
```

---

### RTX PRO 6000 Blackwell

| 항목 | 값 |
|---|---|
| 아키텍처 | Blackwell (GB202) |
| VRAM | 96 GB GDDR7 |
| Compute Capability | 12.0 |
| nvidia-smi 메모리 표시 | 정상 |

- Flash Attention 지원 → `OLLAMA_FLASH_ATTENTION=1` 권장
- 96 GB로 coder-q4(51 GB) + 35b(23 GB) + embed 동시 탑재 가능
- VRAM이 크므로 `OLLAMA_CONTEXT_LENGTH` 명시적 지정 권장 (자동값이 과도하게 높아질 수 있음)

```ini
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_CONTEXT_LENGTH=16384"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_MAX_LOADED_MODELS=4"
```

---

### NVIDIA A100 (40 GB / 80 GB)

| 항목 | 값 |
|---|---|
| 아키텍처 | Ampere |
| VRAM | 40 GB 또는 80 GB HBM2e |
| Compute Capability | 8.0 |
| nvidia-smi 메모리 표시 | 정상 |

- Flash Attention 지원 (compute 8.0 이상) → `OLLAMA_FLASH_ATTENTION=1` 권장
- HBM2e는 GDDR 대비 대역폭이 높아 대형 모델에서 처리 속도 우위
- 80 GB 모델: coder-q4(51 GB) + 9b(6.6 GB) + embed 동시 탑재 가능

**40 GB 권장 설정:**

```ini
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_CONTEXT_LENGTH=8192"
Environment="OLLAMA_KEEP_ALIVE=5m"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
```

**80 GB 권장 설정:**

```ini
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_CONTEXT_LENGTH=16384"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_MAX_LOADED_MODELS=4"
```

---

### NVIDIA H100 (80 GB)

| 항목 | 값 |
|---|---|
| 아키텍처 | Hopper |
| VRAM | 80 GB HBM3 (SXM5) / 80 GB HBM2e (PCIe) |
| Compute Capability | 9.0 |
| nvidia-smi 메모리 표시 | 정상 |

- Flash Attention 지원 (compute 9.0) → `OLLAMA_FLASH_ATTENTION=1` 권장
- HBM3(SXM5)는 A100 대비 약 2배 메모리 대역폭으로 대형 모델 처리에 유리
- 80 GB로 coder-q4(51 GB) + 9b(6.6 GB) + embed 동시 탑재 가능
- coder-q8(84 GB)은 단독으로도 초과하므로 단독 탑재 시 `OLLAMA_MAX_LOADED_MODELS=1` 지정

```ini
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_CONTEXT_LENGTH=16384"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_MAX_LOADED_MODELS=4"
```

---

### GB10 (Grace Blackwell, DGX Spark 등)

| 항목 | 값 |
|---|---|
| 아키텍처 | Blackwell (통합 메모리) |
| VRAM | ~120 GB (CPU·GPU 공유) |
| Compute Capability | 12.1 |
| nvidia-smi 메모리 표시 | `Not Supported` (정상) |

- CPU와 GPU가 메모리를 공유하는 iGPU 아키텍처로 `nvidia-smi` Memory-Usage가 표시되지 않는 것은 정상
- `CUDA_VISIBLE_DEVICES`를 빈 문자열로 설정하면 GPU가 숨겨져 CPU 추론으로 폴백되므로 반드시 `0`으로 명시
- VRAM 자동 계산값이 262,144 토큰까지 설정될 수 있으므로 `OLLAMA_CONTEXT_LENGTH` 명시 필요

```ini
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_CONTEXT_LENGTH=8192"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_MAX_LOADED_MODELS=6"
Environment="CUDA_VISIBLE_DEVICES=0"
```

## 모델별 메모리 점유

| 모델 | 크기 |
|---|---|
| qwen3.5:9b (Q4_K_M) | ~6 GB |
| llama3.1:8b (Q4) | ~5 GB |
| qwen3.6:35b (Q4) | ~22 GB |
| nemotron3:33b (Q4) | ~20 GB |
| llama3.3:70b (Q4) | ~40 GB |
| qwen3-coder-next:q8_0 | ~84 GB |
| bge-m3 | ~1.2 GB |

qwen3.5:9b / llama3.1:8b 는 경량 범용 (llama 는 영문 강함). qwen3.6:35b 는 주력 범용, nemotron3:33b 는 추론 강조. llama3.3:70b 는 대형 범용, qwen3-coder-next:q8_0 는 코딩 고품질. bge-m3 는 RAG 임베딩 (다국어).

GB10 가용 메모리 ~104 GB 기준 동시 탑재 가능한 조합 예시:

| 조합 | 합계 |
|---|---|
| 9b + llama-8b + 35b + nemotron3 + embed | ~53 GB |
| 9b + 35b + 70b + embed | ~68 GB ✅ |
| coder-q8 단독 + embed | ~85 GB ✅ |
| coder-q8 + 9b + embed | ~92 GB ✅ |

## 모델 수동 언로드

특정 모델을 즉시 내리려면:

```bash
ollama stop qwen3.6:35b
```

현재 로드된 모델 확인:

```bash
ollama ps
# 또는
curl -s http://localhost:11434/api/ps | jq '.models[] | {name, size_vram}'
```
