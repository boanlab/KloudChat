# Paper Banana 데모 프롬프트

> functional 에이전트 Paper Banana (학술 논문용 figure 생성) 시연용 프롬프트.
> Super Agent 는 [super-agent-demo.md](super-agent-demo.md), Image Studio 는
> [image-studio-demo.md](image-studio-demo.md), Deep Research 는
> [deep-research-demo.md](deep-research-demo.md), Slide Studio 는
> [slide-studio-demo.md](slide-studio-demo.md) 참고.

`paperbanana` MCP 의 3개 도구로 **publication-quality figure** 를 만든다. 프레임워크([llmsresearch/paperbanana](https://github.com/llmsresearch/paperbanana))가 planner → stylist → visualizer → critic 의 multi-agent 렌더 파이프라인을 돌려 이미지를 반환한다 — 모델은 figure 를 *설명*만 하고 직접 ASCII/텍스트로 그리지 않는다.

| 도구 | 용도 |
|---|---|
| `generate_diagram` | method / architecture / conceptual 다이어그램 (텍스트 설명 → 그림) |
| `generate_plot` | 사용자가 주거나 업로드한 데이터 → 통계 차트 |
| `evaluate_diagram` | 기존 figure 를 기준(reference)에 대해 비평 |

`AI Agent Store` 의 **Research** 카테고리에서 선택 (Top Picks 에는 없음). 첫 호출 시 `uvx` 가 `paperbanana[mcp]` 를 설치(30–60s+)하고, 모든 LLM/이미지 호출은 OpenRouter 경유 (VLM `gemini-2.5-flash` + 이미지 `nano-banana-2`). 통계·데이터 플롯은 `file_search` 로 업로드한 CSV/표를 참조한다.

> ⏱ 다단계 refine 이라 figure 1개에 **수 분**(timeout 15분) 걸린다. 데모 시 "지금 돌리면
> 몇 분 걸린다"고 미리 안내.

## 1. 방법론 / 아키텍처 다이어그램 (generate_diagram)

- `Transformer 인코더-디코더 구조를 논문용 다이어그램으로 그려줘. 멀티헤드 어텐션, 피드포워드, 잔차연결+레이어노름, 포지셔널 인코딩을 명확히 표기.`
- `제안 모델 파이프라인 다이어그램: 입력 이미지 → CNN 백본 → 특징 피라미드 → 트랜스포머 디코더 → 객체 박스/클래스 출력. 각 단계 라벨과 데이터 흐름 화살표 포함.`
- `연합학습(Federated Learning) 시스템 아키텍처: 중앙 서버, N개 클라이언트, 로컬 학습 → 그래디언트 집계 → 글로벌 모델 배포의 라운드 흐름을 도식화.`

## 2. 개념도 / 분류 체계 (generate_diagram)

- `머신러닝 패러다임 분류 체계(taxonomy)를 트리 다이어그램으로: 지도/비지도/강화학습 아래 대표 알고리즘을 계층적으로.`
- `우리 연구의 기여를 보여주는 개념도: 기존 접근의 한계 → 제안 방법의 핵심 아이디어 → 기대 효과를 좌→우 인과 흐름으로.`

## 3. 통계 플롯 — 데이터 직접 제공 (generate_plot)

- `세 모델의 정확도를 막대그래프로: ResNet50=76.1, ViT-B=81.8, ConvNeXt-B=83.8 (단위 %). y축 라벨, 값 표기, 논문 흑백 인쇄에도 구분되게.`
- `학습 에폭별 train/val loss 추이를 라인 차트로: epoch 1~10, train=[2.1,1.4,...], val=[2.3,1.6,...]. 과적합 시점이 보이게 두 선을 구분.`

## 4. 업로드 데이터 기반 플롯 (file_search + generate_plot)

- *(CSV 첨부)* `첨부한 results.csv 의 모델별 F1 점수를 비교 막대그래프로 만들어줘. 신뢰구간(error bar) 있으면 같이 표기.`
- *(표 첨부)* `첨부 데이터로 하이퍼파라미터(학습률) 대비 검증 정확도 산점도를 그려, 최적 구간을 강조해줘.`

## 5. Figure 비평 / 개선 (evaluate_diagram)

- *(figure 이미지 첨부)* `첨부한 아키텍처 그림을 학회 figure 기준으로 비평해줘 — 가독성, 라벨 명확성, 흐름의 직관성 측면에서 개선점을 알려줘.`
- *(두 figure 비교)* `초안 figure 를 reference 스타일에 맞춰 무엇을 고치면 좋을지 평가해줘.`

## 데모 팁

- **무엇을** 그릴지 구체적으로: 노드/단계 이름, 흐름 방향, 라벨, 단위를 프롬프트에 명시할수록 결과가 좋다.
- 통계 플롯은 **실제 수치**가 필요하다 — 프롬프트에 숫자를 주거나 CSV/표를 첨부(`file_search`). 숫자를 지어내지 않는다.
- 결과는 **이미지 figure** 로 반환된다(다운로드 가능). 모델은 그림을 텍스트로 그리지 않고 짧게 설명만 한다.
- 첫 figure 는 `uvx` 설치 + multi-agent 렌더로 시간이 더 걸린다 — 두 번째부터 빨라진다.
- 논문 figure 가 목적이므로 흑백 인쇄/색맹 가독성, 명확한 라벨을 요구하면 품질이 올라간다.
