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
| 음성 출력 (TTS) | Kokoro (amd64 전용) |
| 이미지 생성 | SD.Next — A1111 API 호환 (amd64 전용) |
| 팀·사용자·예산 관리 | LiteLLM + CLI 스크립트 |
| Claude Code 로컬 연결 | LiteLLM Anthropic proxy |

## 빠른 시작

```bash
# 1. 저장소 클론
git clone https://github.com/boanlab/KloudChat.git
cd KloudChat

# 2. Ollama 설치 (호스트에서 직접 실행)
sudo ./scripts/install-ollama.sh

# 3. 환경변수 설정
./scripts/gen-env.sh

# 4. LLM 모델 다운로드
./scripts/download-ollama-models.sh

# 5. 이미지 생성 모델 다운로드 (amd64 전용)
./scripts/download-sdnext-models.sh

# 6. 서비스 시작
./scripts/deploy.sh up -d

# 7. 초기화 (팀·서비스 키 자동 생성)
./scripts/init.sh

# 8. LibreChat 재시작 (서비스 키 반영)
docker compose restart librechat
```

LibreChat: http://localhost:8080  
LiteLLM: http://localhost:8000

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

[미디어 서비스 — amd64 전용]
  ├─ Whisper  — STT
  ├─ Kokoro   — TTS
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
