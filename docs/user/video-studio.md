# Video Studio 활용 가이드

> 텍스트 → 짧은 비디오 클립 생성 에이전트 (`local/qwen3.5:122b`). 프롬프트로 4~8초 클립을
> 만들어 **다운로드 링크 + 임베디드 플레이어(아티팩트)** 로 돌려준다. Artifacts 기본 ON이라
> 플레이어가 우측 패널에 바로 뜬다.
>
> 이미지는 [Image Studio](image-studio.md), 발표자료는 [Slide Studio](slide-studio.md),
> 일상 작업은 [Super Agent](super-agent.md) 로.

## 도구 구성

이미지(`generate_image`)와 달리 비디오는 렌더에 시간이 걸려, **파일을 만들어 링크로 돌려주는 MCP** 로 구성된다. 빌트인 도구는 쓰지 않는다.

**KloudChat 추가 MCP**

| MCP | 하는 일 |
|---|---|
| `generate_video` | 텍스트 → 비디오 잡 제출 후 ~2분 인라인 폴링. 완료되면 링크, 미완이면 **작업 ID** 반환 |
| `check_video` | 작업 ID 로 결과 회수 ("영상 확인해줘") |

> 렌더는 수십 초~수 분. 비동기라 오래 걸리면 "렌더링 중 + 작업 ID" 로 응답하고, 잠시 뒤 다시 물으면 `check_video` 로 회수한다. 운영/배포 내부는 [operator/internal/video-studio.md](../operator/internal/video-studio.md).

## 모델

`model` 인자(또는 사용자 지목)로 고른다. 기본은 로컬 무료, 품질이 필요하면 OpenRouter 모델을 **이름으로 지목**.

| alias | 백엔드 | 비용 | 비고 |
|---|---|---|---|
| `ltx-video` (기본) | 로컬 ComfyUI(LTX-Video) | 무료(온프레미스) | 짧은 저해상도, 외부 전송 없음 |
| `veo-lite` | OpenRouter — google/veo-3.1-lite | ~$0.08/s | 오디오, 저가 기본 |
| `veo-fast` | OpenRouter — google/veo-3.1-fast | ~$0.12/s | 오디오, 고품질 |
| `veo` | OpenRouter — google/veo-3.1 | ~$0.40/s | Veo 3.1 풀 |
| `sora-2` | OpenRouter — openai/sora-2-pro | ~$0.50/s | OpenAI 플래그십 |

> OpenRouter 모델은 품질이 높지만 **외부·유료**(초당 과금)이고 프롬프트가 OpenRouter 로 전송된다. 비용/프라이버시가 걱정되면 로컬 `ltx-video`(무료·온프레미스)를 쓰자.

---

## 1. 기본 생성 (로컬 ltx-video)

영상을 묘사할수록 좋다 — 피사체·동작·배경·조명/분위기·카메라 움직임을 담아서.

- `해질녘 해변을 걷는 사람, 카메라가 천천히 따라가는 4초 영상.`
- `빗방울이 유리창을 타고 흐르는 클로즈업, 뒤로 흐릿한 도시 네온, 잔잔한 분위기.`

## 2. 카메라 움직임 지정

- `눈 덮인 산맥 위를 나는 항공 트래킹 샷, 8초.`
- `커피잔으로 천천히 다가가는 돌리인(dolly-in), 따뜻한 아침 햇살.`

## 3. 고품질 / 오디오 (OpenRouter 모델 지목)

- `veo-fast 로: 도시 야경 타임랩스, 차량 불빛 궤적, 8초.`
- `sora로 만들어줘: 우주 정거장에서 지구를 바라보는 우주인, 시네마틱.`

## 4. 렌더 지연 시 회수 (check_video)

- *(생성 요청 → "렌더링 중 + 작업 ID" 응답을 받으면, 잠시 뒤)* `영상 확인해줘.` / `됐어?`

## 한계

- 길이는 OR 모델별 이산값: **Veo 4/6/8초(최대 8), Sora 2 Pro 4/8/12/16/20초(최대 20)**. 1분+ 단발 영상은 모델 한계로 불가(여러 클립 stitching 필요).
- 로컬 `ltx-video` 는 짧은 저해상도 클립 위주 — 품질은 상용 모델보다 낮다.
- 렌더는 수십 초~수 분 걸린다.

## 활용 팁

- 막연히 "동영상 만들어줘"보다 **피사체·장면·카메라**를 주면 결과가 크게 좋아진다. 너무 막연하면 에이전트가 한 번 되묻는다.
- 무료로 먼저 `ltx-video` 로 감을 잡고, 마음에 들면 `veo`/`sora` 로 고품질 재생성하는 흐름을 추천.
- OpenRouter 모델은 초당 과금 — 길이를 줄이면 비용도 준다. 사용량은 Super Agent 의 `usage` 도구로 확인.
