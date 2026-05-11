# KloudChat

온프레미스 환경에서 운영하는 오픈소스 기반 AI 플랫폼.
LibreChat + LiteLLM + Ollama를 중심으로 RAG, 웹 검색, 음성, 이미지 생성을 통합합니다.

## 기능

| 기능 | 컴포넌트 |
|---|---|
| 멀티 LLM 채팅 | LibreChat + LiteLLM + Ollama |
| RAG (문서 기반 답변) | pgvector + MeiliSearch (Hybrid Search) |
| 웹 검색 | SearXNG |
| 코드 실행 샌드박스 | LibreCodeInterpreter |
| HWP/PDF/DOCX 파일 업로드 | LibreChat RAG API |
| 음성 입력 (STT) | Whisper (amd64 전용) |
| 음성 출력 (TTS) | openedai-speech (piper + xtts_v2, 다국어/한국어 지원, multi-arch) |
| 이미지 생성 | SD.Next — A1111 API 호환 (amd64 전용) |
| 팀·사용자·예산 관리 | LiteLLM + CLI 스크립트 |
| Claude Code 로컬 연결 | LiteLLM Anthropic proxy |

## 빠른 시작

```bash
# 1. 저장소 클론
git clone https://github.com/boanlab/KloudChat.git
cd KloudChat

# 2. Ollama 호스트 설치 (sudo 1회)
sudo ./scripts/install-ollama.sh

# 3. 한 명령으로 전체 셋업
#    env 생성 → 모델 다운로드 → rag_api 빌드 → 서비스 기동 → 초기화 → 재시작 자동
#    GPU VRAM 자동 감지해서 적합한 모델 셋 추천 (모델 다운로드가 시간의 대부분, 30~60분 예상)
./scripts/full-setup.sh --yes

# 4. (옵션) admin 사용자 생성 + LiteLLM 키 발급 한 번에
./scripts/manage.sh user create \
  --id admin@example.com --name '관리자' --username admin --password '비번8자이상' \
  --budget 9999
# → 출력된 sk-... 를 LibreChat UI 의 API Key 칸에 입력
```

LibreChat: http://localhost:8080  
LiteLLM: http://localhost:8000

> 단계별 수동 진행을 원하거나 `full-setup.sh` 가 중간에 실패한 경우는
> [설치 가이드](getting-started/installation.md) 의 8단계 절차 참고.
> 사용자·팀·키 관리 상세는 [CLI 관리](getting-started/cli-management.md) 참고.

## 아키텍처

```
[사용자]
  └─ 채팅 / 에이전트 / 음성 → LibreChat (:8080)

[LLM 게이트웨이]
  └─ LiteLLM (:8000) ─→ Ollama (host:11434, GPU)

[데이터 레이어]
  ├─ pgvector      — 시맨틱 검색
  ├─ MeiliSearch   — BM25 키워드 검색 (Hybrid RAG)
  └─ MongoDB       — 대화 이력

[검색 / 코드 실행]
  ├─ SearXNG          — 웹 검색
  └─ code-interpreter — 코드 실행 샌드박스

[음성 — multi-arch]
  └─ tts      — TTS (openedai-speech, piper + xtts_v2)

[GPU 가속 서비스 — amd64 + NVIDIA GPU 전용]
  ├─ Whisper  — STT
  └─ SD.Next  — 이미지 생성 (A1111 API 호환)
```

## 문서

- [사전 요구사항](getting-started/prerequisites.md)
- [설치 가이드](getting-started/installation.md)
- [CLI 관리 (팀·유저·키)](getting-started/cli-management.md)
- [환경변수 레퍼런스](docs/env-reference.md)
- [모델 설정](docs/models.md)
- [GPU 메모리 가이드](docs/gpu-memory.md)
- [Ollama 튜닝 가이드](docs/ollama-tuning.md)
- [아키텍처 상세](docs/overview.md)
- [기여 가이드](CONTRIBUTING.md)

## 라이선스

MIT
