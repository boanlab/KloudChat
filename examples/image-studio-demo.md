# Image Studio 데모 프롬프트

> functional 에이전트 Image Studio (텍스트 → 이미지) 시연용 프롬프트.
> Super Agent 는 [super-agent-demo.md](super-agent-demo.md), Deep Research 는
> [deep-research-demo.md](deep-research-demo.md), Paper Banana 는
> [paper-banana-demo.md](paper-banana-demo.md), Slide Studio 는
> [slide-studio-demo.md](slide-studio-demo.md) 참고.

`generate_image` 로 이미지를 생성한다. 모델별 특성:

| 모델 | 비용 | 속도 | 용도 |
|---|---|---|---|
| `flux-schnell` (기본) | 무료 | ~90s | 일반 생성 |
| `flux-dev` | 무료 | ~2-3분 | 고품질 (명시해야 사용) |
| `nano-banana` | ~$0.04 | 빠름 | 빠른 반복 (명시) |
| `nano-banana-2` | ~$0.05 | — | Pro 품질 (명시) |
| `gpt-image-2` | ~$0.10-0.30 | — | **이미지 내 텍스트** 최적 (명시) |

> 유료 모델은 사용자가 **이름을 명시**해야만 쓴다 (자동 업그레이드 안 함). 프롬프트엔 시각 키워드 7개 이상이 좋다.

## 1. 기본 생성 (flux-schnell)

- `노을 지는 해변, 야자수 실루엣, 따뜻한 주황빛 하늘, 잔잔한 파도, photorealistic, 8k 디테일`
- `비 내리는 사이버펑크 도시 야경, 네온사인, 젖은 아스팔트 반사, 보라+청록 색감, 영화적 조명`

## 2. 일러스트 / 인포그래픽

- `태양계 8개 행성을 크기 순으로 나란히 보여주는 교육용 인포그래픽, 플랫 일러스트, 라벨 공간`
- `귀여운 로봇 마스코트 캐릭터, 둥근 형태, 파스텔 색감, 화이트 배경, 스티커 스타일`

## 3. 스타일 지정

- `수채화 스타일의 가을 단풍 숲길, 부드러운 번짐, 따뜻한 톤`
- `미니멀 플랫 아이콘: 커피잔, 단색 배경, 벡터 느낌`
- `1980년대 레트로 신스웨이브 포스터, 그리드 지평선, 석양, 네온 그리드`

## 4. 고품질 (flux-dev 명시)

- `flux-dev 로 만들어줘: 한복 입은 여성 인물 초상, 전통 문양 배경, 섬세한 자수 디테일, 고품질`

## 5. 이미지 내 텍스트 (gpt-image-2 — 유료, 명시)

- `gpt-image-2 로: "KloudChat" 로고가 또렷하게 박힌 모던한 테크 컨퍼런스 배너, 깔끔한 타이포그래피`

## 6. 라우팅 시연 (비-이미지 요청)

- `파이썬으로 퀵소트 짜줘` → Image Studio 는 코드/연구 요청이면 **Super Agent 로 안내**한다 (도구 분리 시연)

## 데모 팁

- 기본은 무료 `flux-schnell`. "고품질로"라고 하면 `flux-dev` 를 명시 유도.
- 유료 모델(nano-banana, gpt-image-2)은 비용을 미리 안내하고 사용자가 명시할 때만.
- prompt + negative_prompt 둘 다 스키마 필수 — 시각 키워드를 충분히.
- 자기 영역 밖(텍스트/코드/연구) 요청은 Super Agent 로 안내하도록 instruction 됨 → 역할 분리도 시연 포인트.
