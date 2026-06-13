# Deep Research 활용 가이드

> 학술 심층 조사 전용 에이전트 (`local/qwen3.5:122b`). arxiv/scholar/openalex/crossref/pubmed 를
> 다단계로 훑어 **인라인 인용**(URL + 원문 인용)이 달린 종합 보고를 만든다.
>
> 일상 질문·복합 작업은 [Super Agent](super-agent.md), 이미지는 [Image Studio](image-studio.md),
> 발표자료는 [Slide Studio](slide-studio.md) 로.

## 도구 구성

`deep_research`(학술 sweep) → `fetch_url`(인용 검증) → `file_search`/`smart_search`(업로드 문서 대조) → `execute_code`(수치 재검증) 순으로 동작한다.

**LibreChat 빌트인**

| 도구 | 하는 일 |
|---|---|
| `execute_code` | 보고된 수치(effect size·p-value 등) 직접 재계산 검증 |
| `file_search` | 업로드 논문/문서 RAG |

> `web_search` 는 **일부러 제외** — `deep_research` 가 다중 소스 검색을 내부에서 다단계로 수행하므로 중복이고, 빼야 모델이 그쪽으로 폴백하지 않고 `deep_research` 를 제대로 쓴다.

**KloudChat 추가 MCP**

| MCP | 하는 일 |
|---|---|
| `deep_research` | ReAct 학술 사이드카 — arxiv/scholar/openalex/crossref/pubmed 다단계 sweep |
| `fetch_url` | 인용 URL 직독으로 원문 검증 |
| `smart_search` | 업로드 문서 정밀 검색(hybrid + rerank)으로 문헌 대조 ([상세](../operator/smart-search.md)) |

> ⏱ `deep_research` 는 다단계라 **5-15분** 걸린다. "지금 돌리면 몇 분 걸린다"고 미리 알아두자.

---

## 1. 문헌 종합 + 비교

- `최근 5년 대규모 언어모델의 환각(hallucination) 완화 기법을 학술 문헌 기반으로 정리하고, 각 접근의 효과를 인용과 함께 비교해줘.`
- `Transformer 이후 attention 효율화 연구(FlashAttention, Linear/Sparse Attention 등)의 계보를 arxiv 기준으로 정리하고 핵심 trade-off 를 표로 만들어줘.`

## 2. 도메인 리서치 (보안)

- `제로트러스트(Zero Trust) 보안 모델의 학술적 정의와 실제 도입 사례 연구를 인용과 함께 종합하고, 한계점도 정리해줘.`
- `eBPF 기반 런타임 보안 모니터링 연구 동향을 최근 논문 중심으로 조사해줘 (출처 링크 필수).`

## 3. 수치 재검증 (execute_code 연계)

- `특정 중재(intervention)의 메타분석에서 보고된 effect size 와 p-value 를 찾아, 보고 수치를 직접 재계산으로 검증하고 불일치가 있으면 지적해줘.`

## 4. 업로드 문서 대조 (file_search / smart_search 연계)

*(논문 PDF 첨부 후)*

- `이 논문의 핵심 주장을 관련 후속 연구와 대조해서, 지지/반박하는 근거를 인용과 함께 정리해줘.`

## 5. 최신성 검증 (fetch_url 연계)

- `이 주제의 2024년 이후 최신 연구를 찾아서, 각 논문의 기여를 원문 인용과 함께 1줄씩 요약해줘.`

## 활용 팁

- 시간이 걸리니 짧은 단발 질문보다 **인용·비교·검증이 필요한 묵직한 질문**이 진가를 보인다.
- 진행 중 ReAct 단계(검색→읽기→재검색)가 응답/로그에 드러난다 — 어떻게 조사하는지 따라가며 볼 수 있다.
- 자기 영역(학술 조사) 밖 요청은 [Super Agent](super-agent.md) 로 안내된다.
- 빠른 사실 확인 한두 개면 Deep Research 대신 Super Agent 의 `web_search` 가 빠르다.
