# Video Studio — 동작 구조 · 과금 · 로컬 설정 (운영)

> 사용자 가이드는 [docs/user/video-studio.md](../../user/video-studio.md). 이 문서는 라우팅·과금·로컬 LTXV 배포 등 운영 내용.

## 동작 구조

- **MCP 구성 이유**: 이미지(Image Studio)는 LibreChat 빌트인 `generate_image` 사용. 비디오는 그 경로(A1111/PNG) 사용 불가 → **파일을 만들어 링크로 돌려주는 MCP** 로 구성.
- **라우팅**: `comfyui-shim` 이 `model` 로 로컬 ComfyUI ↔ OpenRouter 를 한곳에서 라우팅.

```
Video Studio
  ├─ generate_video MCP   잡 제출 → ~2분 인라인 폴링 → 미완이면 작업 ID 반환
  └─ check_video MCP      작업 ID 로 결과 회수
        POST comfyui-shim/video/submit {model, prompt, …}  → {handle}
        POST comfyui-shim/video/fetch  {handle}            → {status, video?}
          ├─ veo/sora  → LiteLLM passthrough → OpenRouter Video API (submit/job 분리, 과금 귀속)
          └─ ltx-video → ComfyUI (/prompt → /history → /view)
        완료 시 mp4 → public/images/videos/  → {DOMAIN_CLIENT}/images/videos/… 링크
```

**비동기인 이유**

- Veo 등은 렌더 시간 편차가 큼(수십 초 ~ 수 분) → 제출과 회수를 분리해 연결을 길게 붙들지 않음.
- 핸들 인코딩: OpenRouter `or:<alias>:<jobid>`, 로컬 `comfy:<promptid>@<backend>`.

## 과금 (per-user, 근사)

- **OR passthrough**: OR 호출은 LiteLLM 의 OpenRouter passthrough 경유 → shim 은 OR 키를 직접 쥐지 않고 LiteLLM 에만 인증, OR 키 주입·egress 는 LiteLLM 담당.
- **submit 과금**: `/orvideo/submit/<model>/<dur>` 경로별 `cost_per_request = rate[model] × 초` 1회 과금, `x-litellm-end-user-id`(호출 user email)로 귀속.
- **폴/다운로드**: `/orvideo/job/...` 과금 0.
- **rate 기준**: **OR `pricing_skus`(오디오 기본 tier)**.

  | 모델 | rate ($/s) |
  |---|---|
  | veo-lite | 0.08 |
  | veo-fast | 0.12 |
  | veo | 0.40 |
  | sora-2 | 0.50 |

- **근사 주의**: 해상도/오디오 옵션 미노출(OR 모델 기본값) → 해당 tier 가격으로 근사. 정확한 청구는 해상도/오디오에 따라 달라질 수 있음(`litellm-config` 의 rate 로 조정).

**로컬 LTX-Video 과금(명목)**

- 외부 egress 없음. 사용량 가시화 위해 shim 의 `_bill_local` 이 생성 완료 후 LiteLLM passthrough `/localbill/video/<초>` 로 1회 과금.
  - `cost_per_request = 0.04 × 초`(**OR 동급 50%**), `x-litellm-end-user-id` 로 귀속.
  - best-effort — 실패해도 생성물엔 영향 없음.
- 이 spend 는 `mcp/usage.py` my_usage 와 `usage-priorities.sh` 의 **Video Studio** 행에 합산([scheduler.md](../scheduler.md#실사용-기반-우선순위-usage-prioritiessh)).

## 로컬 LTX-Video 설정

각 GPU 노드에 필요:

```bash
./scripts/download-image-models.sh ltx-video flux-shared   # DiT+VAE(~6 GB) + t5xxl 인코더
./scripts/install-comfyui.sh                               # ComfyUI-VideoHelperSuite(VHS) 노드
docker compose pull comfyui-shim && docker compose up -d comfyui-shim   # shim 최신 이미지로 재기동
```

- **T5 인코더 별도**: `ltx-video-2b-v0.9.5.safetensors` 는 DiT+VAE 만 담고 텍스트 인코더는
  없다 — 워크플로가 `clip/t5xxl_fp16.safetensors`(flux 와 공유, `CLIPLoader type=ltxv`)를
  따로 로드한다. 그래서 `flux-shared`(t5xxl) 다운로드가 LTXV 에도 필수다.
- VHS 노드: `install-comfyui.sh` 는 venv 가 이미 있으면 비대화형에서 노드 설치를 건너뛴다
  (`--reinstall` 로 강제하거나 VHS repo 만 `custom_nodes/` 에 clone + venv pip install).
- 노드 배치: 비디오는 `COMFYUI_URLS` 의 least-loaded 백엔드로 라우팅된다 — 챗 두뇌(8001)가
  없는 빈 노드를 우선하도록 그 노드를 csv 앞에 둔다.
- LTXV 는 프레임 길이가 `8n+1` 이어야 안정적 — MCP 가 초→프레임으로 스냅한다(기본 97 = 4초@25fps).
- ComfyUI 실행 에러는 shim 이 502/`failed` 로 전달하며, 상세는 노드의 `/history/<id>` 에 남는다.

## 같이 보면 좋은 문서

- [도구/MCP](../tools.md) · [scheduler](../scheduler.md) · [모델 설정](../models.md)
