# Super Agent 활용 가이드

> 만능 단일패스 에이전트 (`local/auto-route`). 채팅·코드 실행·웹 검색·문서 RAG·아티팩트를
> 한 대화에서 도구로 오케스트레이션한다. 모든 질문을 `local/gemma-4-26b` 단일 두뇌로
> 처리하고, **필요한 도구를 스스로 호출해 깊이를 조절**한다.
>
> 이미지 생성은 [Image Studio](image-studio.md), 비디오는 [Video Studio](video-studio.md),
> 학술 심층조사는 [Deep Research](deep-research.md), 발표자료는 [Slide Studio](slide-studio.md),
> 녹음 정리는 [Note Taker](note-taker.md) 가 담당 — Super Agent 엔 없다.

## 도구 구성

**LibreChat 빌트인**

| 도구 | 하는 일 |
|---|---|
| `web_search` | 실시간 웹 검색 (SearXNG / crawl4ai) |
| `execute_code` | 코드 인터프리터 — 계산·데이터 분석·차트(`print()` + matplotlib 이미지) |
| `file_search` | 업로드 파일 RAG (기본 dense 검색) |
| artifacts | 인터랙티브 HTML / 차트 결과물 (우측 패널) |
| memory | 대화 간 사용자 개인화(지속 기억) |

**KloudChat 추가 MCP**

| MCP | 하는 일 |
|---|---|
| `fetch_url` | URL 직독 + Markdown 변환 |
| `smart_search` | 업로드 문서 **정밀 검색**(reformulation + hybrid + rerank) — 기본 `file_search` 가 놓치는 동의어·우회표현 회수 ([상세](../operator/internal/smart-search.md)) |
| `time` | 현재 시간 / 타임존 변환 |
| `usage` | 본인 토큰 사용량 / 예산 조회 |
| `youtube` | 유튜브 텍스트 추출 (자막 또는 whisper) — *whisper 백엔드(`WHISPER_URLS`)가 있는 배포에서만 부착; GPU 없는 OR 전용 배포엔 미노출* |

---

## 1. 기본 대화 (단일 모델 응답)

간단한 질문부터 깊은 설명까지 `local/gemma-4-26b` 단일 모델이 모두 처리한다.

- `안녕! 오늘 기분 어때?`
- `양자컴퓨터의 오류 정정(error correction)을 비전공자에게 3단계로 설명해줘.`

## 2. 웹 검색 (web_search)

- `이번 주 한국 IT 보안 분야 주요 뉴스 3개를 출처 링크와 함께 요약해줘.`
- `현재 NVIDIA 최신 데이터센터 GPU 라인업과 각 VRAM 용량을 표로 정리해줘.`

## 3. 코드 실행 (execute_code)

코드 인터프리터로 실제 실행 + 차트를 artifact 로 렌더한다.

- `1부터 10000까지 소수의 개수를 직접 계산해서 알려줘.`
- `표준정규분포에서 10,000개를 샘플링해 히스토그램을 그려줘.` *(→ matplotlib 이미지 출력)*
- `CSV 한 줄씩 줄게. 매출 데이터를 받아서 월별 추세선 차트를 만들어줘.`

## 4. 문서 RAG (file_search / smart_search)

*(채팅창에 PDF / 문서 첨부 후)*

- `방금 올린 문서의 핵심을 5줄로 요약하고, 'X' 항목이 어디에 나오는지 인용해줘.`
- `이 보고서에서 위험 요소로 언급된 부분만 뽑아서 표로 만들어줘.`

> 동의어·우회표현으로 물으면 `smart_search` 가 재정식화·하이브리드 검색으로 회수율을 높인다.
> 예: `이 매뉴얼에서 "장애 복구" 관련 절차를 빠짐없이 찾아 정리해줘.`

## 5. URL 직독 (fetch_url)

- `https://arxiv.org/abs/1706.03762 이 페이지 내용을 읽고 핵심 기여를 요약해줘.`
- `이 블로그 글(URL) 읽고 한국어로 3문단 요약 + 비판점 2가지 적어줘.`

## 6. YouTube 요약 (youtube)

> ⚠️ `youtube` 도구는 whisper(STT) 백엔드(`WHISPER_URLS`)가 있는 배포에서만 부착된다. 없으면 동작하지 않는다.

- `이 유튜브 영상 핵심을 타임라인별로 정리해줘: <YouTube URL>` *(자막 없으면 whisper 로 추출)*

## 7. 아티팩트 (인터랙티브 결과물)

- `간단한 To-Do 리스트 웹앱을 단일 HTML 로 만들어줘 (추가/삭제/완료 체크 가능).`
- `구구단 연습 게임을 인터랙티브 아티팩트로 만들어줘.`

> 16:9 발표 **덱**은 전용 에이전트 [Slide Studio](slide-studio.md) 담당. Super Agent 의 아티팩트는 단일 HTML 앱/위젯에 적합하다.

## 8. 시간 / 타임존 (time)

- `지금 서울 시간 기준으로, 샌프란시스코·런던·도쿄는 각각 몇 시인지 알려줘.`

## 9. 사용량 / 예산 (usage)

- `내가 지금까지 토큰을 얼마나 썼고 남은 예산은 얼마야?`

## 10. 메모리 / 개인화 (대화 간 기억)

첫 대화에서 알려주고 → **새 대화**에서 기억하는지 확인.

1. `나는 보안 연구를 하고 Python 을 주로 써. 답변은 항상 한국어로 해줘.`
2. *(새 대화 열고)* `내 전공에 맞는 주말 사이드 프로젝트 아이디어 3개 추천해줘.` *(→ 보안/Python/한국어 반영)*

## 11. ⭐ 복합 작업 (도구 오케스트레이션)

한 프롬프트에서 `web_search` → `execute_code` → artifact 를 연쇄로 쓰게 만드는 게 Super Agent 의 진가다.

- `최근 5년 한국 전기차 등록 대수를 웹에서 찾아서, 그 숫자로 연도별 증가율을 계산하고, 추세 차트까지 만들어줘.`
- `오늘 주요 환율(USD/EUR/JPY 대 KRW)을 검색해서, 100만원을 각 통화로 환산한 표를 만들어줘.`

## 활용 팁

- **막연하면 Super Agent 부터** — 무슨 도구가 필요한지 몰라도 알아서 조합한다. 전문 작업(이미지/비디오/발표/심층조사/녹음)만 전용 스튜디오로.
- 복합 질문일수록 진가가 드러난다 — "찾아서 + 계산해서 + 그려줘" 식으로 한 번에 시키면 도구를 연쇄한다.
- 문서를 첨부하면 `file_search`/`smart_search` 가 자동 동원된다 — 인용을 함께 요구하면 근거가 분명해진다.
