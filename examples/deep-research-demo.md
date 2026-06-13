# Deep Research 데모 프롬프트

> functional 에이전트 Deep Research (학술 심층 조사) 시연용 프롬프트.
> Super Agent 는 [super-agent-demo.md](super-agent-demo.md), Image Studio 는
> [image-studio-demo.md](image-studio-demo.md), Paper Banana 는
> [paper-banana-demo.md](paper-banana-demo.md), Slide Studio 는
> [slide-studio-demo.md](slide-studio-demo.md) 참고.

`deep_research`(ReAct 학술 sweep) → `fetch_url`(인용 검증) → `file_search`(업로드 문서) → `execute_code`(수치 재검증) 순으로 동작하며 **인라인 인용**(URL + 원문 인용)을 단다.

> ⏱ `deep_research` 는 arxiv/scholar/openalex/crossref/pubmed 를 다단계로 훑어 **5-15분**
> 걸린다. 데모 시 "지금 돌리면 몇 분 걸린다"고 미리 안내.

## 1. 문헌 종합 + 비교

- `최근 5년 대규모 언어모델의 환각(hallucination) 완화 기법을 학술 문헌 기반으로 정리하고, 각 접근의 효과를 인용과 함께 비교해줘.`
- `Transformer 이후 attention 효율화 연구(FlashAttention, Linear/Sparse Attention 등)의 계보를 arxiv 기준으로 정리하고 핵심 trade-off 를 표로 만들어줘.`

## 2. 도메인 리서치 (보안)

- `제로트러스트(Zero Trust) 보안 모델의 학술적 정의와 실제 도입 사례 연구를 인용과 함께 종합하고, 한계점도 정리해줘.`
- `eBPF 기반 런타임 보안 모니터링 연구 동향을 최근 논문 중심으로 조사해줘 (출처 링크 필수).`

## 3. 수치 재검증 (execute_code 연계)

- `특정 중재(intervention)의 메타분석에서 보고된 effect size 와 p-value 를 찾아, 보고 수치를 직접 재계산으로 검증하고 불일치가 있으면 지적해줘.`

## 4. 업로드 문서 대조 (file_search 연계)

*(논문 PDF 첨부 후)*

- `이 논문의 핵심 주장을 관련 후속 연구와 대조해서, 지지/반박하는 근거를 인용과 함께 정리해줘.`

## 5. 최신성 검증 (fetch_url 연계)

- `이 주제의 2024년 이후 최신 연구를 찾아서, 각 논문의 기여를 원문 인용과 함께 1줄씩 요약해줘.`

## 데모 팁

- 시간이 걸리니 짧은 단발 질문보다 **인용·비교·검증이 필요한 묵직한 질문**이 인상적.
- 진행 중 ReAct 단계(검색→읽기→재검색)가 응답/로그에 드러난다.
- 자기 영역 밖 요청은 Super Agent 로 안내하도록 instruction 됨 → 역할 분리도 시연 포인트.
