#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mcp>=1.2.0",
#   "httpx>=0.27.0",
#   "pymongo>=4.9",
# ]
# ///
"""KloudChat generate_video MCP (stdio) — Video Studio 용 텍스트→비디오.

도구 2개:
  - generate_video: comfyui-shim 의 /video/submit 으로 잡 제출 → ~2분 인라인 폴링(/video/fetch).
    그 안에 끝나면 mp4 를 저장하고 링크 반환, 미완이면 작업 ID(핸들)를 돌려준다.
  - check_video: 작업 ID 로 /video/fetch 폴링 → 완료 시 링크 반환.

완료된 mp4 는 LibreChat 이 서빙하는 public/images/videos 에 저장하면
`{DOMAIN_CLIENT}/images/videos/*.mp4` 로 바로 접근된다(별도 스토리지 불필요, export_deck 과 동형).
로컬 LTXV 는 프레임 길이가 8n+1 이어야 안정적이라 seconds → length 를 그 격자에 스냅한다.
"""
from __future__ import annotations

import asyncio
import base64
import os
import time
import uuid
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

SHIM_URL = os.environ.get("COMFYUI_SHIM_URL", "http://comfyui-shim:7860").rstrip("/")
DOMAIN = os.environ.get("DOMAIN_CLIENT", "").rstrip("/")
VIDEO_DIR = Path(os.environ.get("VIDEO_DIR", "/app/client/public/images/videos"))
DEFAULT_MODEL = os.environ.get("VIDEO_MODEL", "veo-lite")  # 기본 = OpenRouter(외부)
FRAME_RATE = int(os.environ.get("VIDEO_FRAME_RATE", "25"))
VIDEO_TTL_SEC = int(os.environ.get("VIDEO_TTL_SEC", "86400"))  # 24h 후 청소
# 비동기: generate_video 가 인라인으로 기다리는 상한(이 안에 끝나면 바로 링크, 아니면
# 핸들을 돌려주고 check_video 로 회수). 정체 잡에 연결을 오래 붙들지 않는다.
INLINE_WAIT_SEC = float(os.environ.get("VIDEO_INLINE_WAIT_SEC", "120"))
POLL_SEC = float(os.environ.get("VIDEO_POLL_SEC", "8"))
# per-user 과금 귀속 — 호출자 _id(LIBRECHAT_USER_ID)로 email 을 Mongo 에서 조회해 shim 에
# 넘기면, shim 이 x-litellm-end-user-id 헤더로 LiteLLM passthrough 과금을 그 user 에 귀속한다.
MONGO_URI = os.environ.get("MONGO_URI", "")
USER_ID = os.environ.get("LIBRECHAT_USER_ID", "").strip()

# 친화 alias(샤임 OR_VIDEO_ALIASES + 로컬). 모델 미지정/오타 시 기본으로 폴백.
KNOWN_MODELS = {
    "veo-lite", "veo-fast", "veo", "sora-2",  # OpenRouter(외부·유료)
    "ltx-video",                              # 로컬(무료, 활성화 후)
}
LOCAL_MODELS = {"ltx-video"}

mcp = FastMCP("generate-video")


_EMAIL_CACHE: dict[str, str] = {}


def _caller_email() -> str:
    """LIBRECHAT_USER_ID(_id) → 사용자 email (과금 귀속용). best-effort, 실패 시 ''."""
    if not USER_ID or not MONGO_URI:
        return ""
    if USER_ID in _EMAIL_CACHE:
        return _EMAIL_CACHE[USER_ID]
    email = ""
    try:
        from bson import ObjectId
        from pymongo import MongoClient
        db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)[
            os.environ.get("MONGO_DB") or "LibreChat"]
        u = db.users.find_one({"_id": ObjectId(USER_ID)}, {"email": 1})
        email = (u or {}).get("email", "") or ""
    except Exception:
        email = ""
    _EMAIL_CACHE[USER_ID] = email
    return email


def _snap_length(seconds: float) -> int:
    """seconds(@FRAME_RATE) → LTXV 가 요구하는 8n+1 프레임 길이로 스냅(클램프 1~6초)."""
    seconds = max(1.0, min(6.0, seconds))
    frames = seconds * FRAME_RATE
    n = max(1, round((frames - 1) / 8))
    return 8 * n + 1


def _prune_old() -> None:
    """TTL 지난 vid-*.mp4 정리 (best-effort) — 생성 시점에 누적분 청소."""
    if not VIDEO_DIR.exists():
        return
    cutoff = time.time() - VIDEO_TTL_SEC
    for f in VIDEO_DIR.glob("vid-*.mp4"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def _save_and_link(res: dict, chosen: str) -> str:
    """완료된 fetch 결과의 mp4 를 public 에 저장하고 재생용 출력을 만든다.

    LibreChat 은 채팅 본문의 raw <video> 를 sanitize(skipHtml)로 버린다 → 임베디드
    플레이어는 아티팩트(우측 패널, Slide Studio 와 동일 메커니즘)로 띄우고, 본문엔
    항상 동작하는 다운로드 링크를 같이 남긴다."""
    raw = base64.b64decode(res["video"])
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    _prune_old()
    name = f"vid-{uuid.uuid4().hex[:10]}.mp4"
    (VIDEO_DIR / name).write_bytes(raw)
    url = f"{DOMAIN}/images/videos/{name}" if DOMAIN else f"/images/videos/{name}"
    mb = round(len(raw) / 1024 / 1024, 1)
    # muted: 오디오 있는 클립의 autoplay 차단 회피(무음 자동재생). full-bleed 고정 사이징:
    # 메타데이터 로드 전 flex 0-size 로 안 보이는 문제 회피.
    player = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
        "html,body{margin:0;height:100%;background:#000}"
        "video{position:fixed;inset:0;width:100%;height:100%;object-fit:contain}"
        "</style></head><body>"
        f"<video src=\"{url}\" controls autoplay muted loop playsinline></video>"
        "</body></html>"
    )
    return (
        f"비디오를 생성했습니다 ({chosen}, {mb} MB).\n\n"
        f"[▶️ 영상 보기/다운로드]({url})\n\n"
        f":::artifact{{identifier=\"kc-video\" type=\"text/html\" title=\"생성된 영상\"}}\n"
        f"```html\n{player}\n```\n:::"
    )


async def _fetch_until(handle: str, budget_sec: float) -> dict:
    """budget 동안 /video/fetch 폴링. terminal(completed/failed) 이거나 시간초과 시 최신 res 반환."""
    deadline = time.monotonic() + budget_sec
    last: dict = {"status": "pending"}
    async with httpx.AsyncClient(timeout=150) as c:
        while True:
            r = await c.post(f"{SHIM_URL}/video/fetch", json={"handle": handle})
            r.raise_for_status()
            last = r.json()
            if last.get("status") in ("completed", "failed"):
                return last
            if time.monotonic() >= deadline:
                return last
            await asyncio.sleep(POLL_SEC)


@mcp.tool()
async def generate_video(prompt: str, model: str = "", seconds: float = 4.0,
                         aspect_ratio: str = "16:9", resolution: str = "",
                         audio: bool = True, negative_prompt: str = "") -> str:
    """텍스트 프롬프트로 짧은 비디오 클립 생성을 시작한다(비동기).

    빠르면(약 2분 내) 바로 재생 링크를, 더 걸리면 '렌더링 중 + 작업 ID' 를 반환한다 —
    그 경우 잠시 뒤 check_video(그 작업 ID) 로 결과를 받는다(사용자에게 '잠시 후 영상
    확인해줘'라고 안내).
    model: 기본 veo-lite(OpenRouter, 외부·유료). 선택지 — veo-lite/veo-fast/veo(Google Veo
    3.1), sora-2(OpenAI Sora 2 Pro). 로컬 무료는 ltx-video(활성화 후).
    resolution: 720p/1080p/4K (모델별 지원: veo-lite 720p·1080p, veo-fast 720p·1080p·4K,
    veo 1080p·4K, sora-2 720p·1080p; 미지정=1080p). 해상도가 높을수록 비싸다.
    audio: 사운드 생성 여부(Veo만; Sora 는 항상 포함). 끄면 더 싸다.
    프롬프트는 영어가 품질이 가장 좋다 — 피사체·동작·배경·조명/분위기·카메라 무빙을 구체적으로.
    seconds 는 모델 허용값으로 스냅 — Veo 4/6/8(최대 8초), Sora 4/8/12/16/20(최대 20초).
    1분 등 그 이상은 단발 불가(모델 한계). 사용자가 영상/비디오 생성을 요청할 때 호출.
    """
    if not prompt.strip():
        return "어떤 영상을 만들지 한 줄로 설명해 주세요 (예: '해질녘 해변을 걷는 사람, 카메라 천천히 추적')."

    chosen = (model or DEFAULT_MODEL).strip().lower()
    if chosen not in KNOWN_MODELS:
        chosen = DEFAULT_MODEL

    payload: dict = {"prompt": prompt, "negative_prompt": negative_prompt, "model": chosen}
    em = _caller_email()
    if em:
        payload["end_user"] = em   # shim 이 x-litellm-end-user-id 헤더로 → per-user 과금
    if chosen in LOCAL_MODELS:
        payload["length"] = _snap_length(seconds)
        payload["frame_rate"] = FRAME_RATE
    else:
        payload["duration"] = max(1, round(seconds))
        payload["aspect_ratio"] = aspect_ratio
        if resolution:
            payload["resolution"] = resolution
        payload["audio"] = bool(audio)

    # 1) 제출 → 핸들.
    try:
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(f"{SHIM_URL}/video/submit", json=payload)
    except Exception as e:  # noqa: BLE001
        return f"비디오 백엔드에 연결하지 못했습니다 ({e!r}). 잠시 후 다시 시도해 주세요."
    if r.status_code >= 400:
        return f"비디오 생성을 시작하지 못했습니다 ({chosen}, {r.status_code}).\n\n{r.text[:300]}"
    handle = r.json().get("handle")
    if not handle:
        return "비디오 작업 생성에 실패했습니다. 잠시 후 다시 시도해 주세요."

    # 2) 짧게 인라인 대기.
    res = await _fetch_until(handle, INLINE_WAIT_SEC)
    st = res.get("status")
    if st == "completed":
        return _save_and_link(res, chosen)
    if st == "failed":
        return f"비디오 생성에 실패했습니다 ({chosen}).\n\n{res.get('error', '')[:300]}"
    # 3) 아직 렌더 중 → 핸들 핸드오프.
    return (f"영상 렌더링을 시작했습니다 ({chosen}). 보통 1~5분 걸립니다 — 잠시 후 "
            f"'영상 확인해줘'라고 하시면 받아옵니다.\n\n작업 ID: `{handle}`")


@mcp.tool()
async def check_video(job_id: str) -> str:
    """진행 중인 비디오 작업(generate_video 가 돌려준 작업 ID)의 결과를 확인한다.
    완료면 재생 링크를, 아직이면 '렌더링 중' 을, 실패면 사유를 반환한다."""
    if not job_id.strip():
        return "확인할 작업 ID 가 없습니다. 먼저 영상 생성을 시작해 주세요."
    try:
        res = await _fetch_until(job_id.strip(), INLINE_WAIT_SEC)
    except Exception as e:  # noqa: BLE001
        return f"작업 상태를 확인하지 못했습니다 ({e!r}). 잠시 후 다시 시도해 주세요."
    st = res.get("status")
    if st == "completed":
        return _save_and_link(res, "video")
    if st == "failed":
        return f"비디오 생성에 실패했습니다.\n\n{res.get('error', '')[:300]}"
    return (f"아직 렌더링 중입니다. 잠시 후 다시 '영상 확인해줘'라고 해주세요.\n\n"
            f"작업 ID: `{job_id.strip()}`")


if __name__ == "__main__":
    mcp.run()
