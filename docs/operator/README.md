# 운영자 문서 — 배포 · 운영 · 튜닝

KloudChat 을 띄우고 굴리는 사람을 위한 문서. **실제 운영에 바로 쓰는 정보 위주**로 정리했고,
아키텍처 심화·벤치마크·컴포넌트 내부구조 같은 깊은 내용은 [`internal/`](internal/) 로 분리했다.

## 처음이라면 이 순서로

1. [사전 요구사항](prerequisites.md) — 하드웨어/소프트웨어 체크리스트
2. [환경변수 레퍼런스](env-reference.md) — `.env` 채우기 (토폴로지 + 키)
3. `./setup.sh all` 로 기동 → [장애 대응 § 첫 실행 후 점검](troubleshooting.md#첫-실행-후-점검--이게-떴으면-ok) 으로 확인
4. 막히면 [장애 대응](troubleshooting.md)

## 전체 구조 한눈에

두 개의 docker compose 스택으로 나뉜다 (같은 노드면 포트 publish, 다른 노드면 `.env` 의 `LITELLM_URL` 로 연결).

- `docker-compose.yml` (`kloudchat`) — **LibreChat 측** (UI :8080, mongodb·rag_api·pgvector·code-interpreter·각종 shim·deep-research)
- `docker-compose.litellm.yml` (`kloudchat-litellm`) — **LiteLLM 측** (게이트웨이 :8000, 모델별 deployment 등록 + 노드 LB, super-agent-shim, litellm-db)
- **GPU 노드** — vLLM(`gemma-4-26b`/`qwen3.5:122b`) · ComfyUI(FLUX/LTXV) · Whisper. LiteLLM 노드가 `VLLM_*_URL` 로 직접 호출

**배포 모드**

| 모드 | 동작 |
|---|---|
| GPU 있음 | 전 기능 — 로컬 vLLM + ComfyUI + Whisper, OpenRouter 는 폴백·상용 보강 |
| GPU 없음 (OR 전용) | 로컬 두뇌가 OR 동일모델로 직결. **Whisper 의존(Note Taker · 무자막 YouTube)만 비활성**, RAG 임베딩은 OR 로 자동 swap |

> 컴포넌트별 동작·요청 흐름·네트워크 상세 → [internal/overview.md](internal/overview.md).

## 운영 문서

**셋업 / 막힐 때**
- [사전 요구사항](prerequisites.md) — 하드웨어/소프트웨어 체크리스트, 멀티노드 ssh
- [장애 대응](troubleshooting.md) — 첫 점검 체크리스트, 컨테이너 restart loop, vLLM cold-start fail, 로그 위치, nuclear reset
- [환경변수 레퍼런스](env-reference.md) — `.env` 변수 전체 + 자동 생성 시크릿

**모델 / 멀티노드 운영**
- [모델 설정](models.md) — 카탈로그 + 라우팅 매트릭스 + 모델 추가법 + OR 폴백
- [scheduler](scheduler.md) — 멀티노드 vLLM placement 자동화 (단일노드면 안 봐도 됨)
- [vLLM 튜닝](vllm-tuning.md) — `gpu_memory_utilization` / `max_model_len` / ctx 옵션
- [GPU 메모리 가이드](gpu-memory.md) — 노드 클래스별 권장 워크로드, VRAM 점유

**도구 / 정책 / 브랜딩**
- [도구 (Tools)](tools.md) — Built-in / MCP / 이미지 백엔드 + 에이전트별 도구 매트릭스
- [라우팅 정책](routing-policy.md) — instruction / 도구 / 모델 / shim / scheduler 의 **변경 위치 인덱스**
- [브랜딩 커스터마이징](branding.md) — 로고 / 파비콘 / PWA

## 어디서 뭘 바꾸나 (빠른 맵)

| 하고 싶은 것 | 보는 곳 |
|---|---|
| 새 OR 상용 모델 추가 | [routing-policy](routing-policy.md#새-or-모델-추가-예-claude-opus-5) → [models](models.md) |
| 로컬 모델에 OR 폴백 엮기 | [routing-policy](routing-policy.md#로컬-모델에-or-폴백-엮기) |
| 에이전트에 MCP 도구 부착 | [routing-policy](routing-policy.md#새-도구-mcp-부착) → [tools](tools.md) |
| 에이전트 instruction 수정 | `routing/instructions.md` → `manage.sh agent sync` ([routing-policy](routing-policy.md)) |
| vLLM 메모리/컨텍스트 조정 | [vllm-tuning](vllm-tuning.md) |
| 노드 추가 / 모델 재배치 | [scheduler](scheduler.md) |
| 로고·파비콘 교체 | [branding](branding.md) |
| 안 뜨거나 깨질 때 | [troubleshooting](troubleshooting.md) |

## 심화 · 레퍼런스 ([internal/](internal/))

일상 운영엔 불필요하지만 깊게 파거나 디버깅할 때 참고:

- [overview.md](internal/overview.md) — 아키텍처 상세 (컴포넌트별 역할 · 네트워크 · 요청 흐름)
- [performance.md](internal/performance.md) — 실측 throughput 벤치마크 매트릭스
- [slide-export.md](internal/slide-export.md) — 덱 PDF/PPTX export 서비스 + export_deck MCP
- [smart-search.md](internal/smart-search.md) — smart_search MCP (hybrid retrieve + rerank 배포)
- [video-studio.md](internal/video-studio.md) — Video Studio 동작 구조 · 과금 · 로컬 LTXV 설정
