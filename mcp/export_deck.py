#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mcp>=1.2.0",
#   "httpx>=0.27.0",
#   "pymongo>=4.9",
# ]
# ///
"""KloudChat export_deck MCP (stdio) — Slide Studio 덱을 PDF/PPTX 로 내보낸다.

흐름: 현재 대화(conversationId)의 최신 text/html 아티팩트(= Slide Studio 가 만든
HTML 덱) 본문을 Mongo 에서 꺼내 → slide-export 사이드카로 16:9 PDF/PPTX 렌더 →
LibreChat 이 서빙하는 public/images/decks 에 저장 → 다운로드 링크를 돌려준다.

이 MCP 는 LibreChat 컨테이너 안에서 도므로 `public/images/decks/*.pdf` 에 직접 쓰면
`{DOMAIN_CLIENT}/images/decks/*.pdf` 로 바로 다운로드된다(별도 스토리지 불필요).

conversationId 는 export 시점엔 이미 존재하므로 placeholder 로 충분하다
(smart_search 의 첫메시지 공백 이슈와 무관).
"""
from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "")
# MONGO_URI 가 db 경로 없이 올 수 있어(get_default_database 실패) db 명을 명시.
MONGO_DB = os.environ.get("MONGO_DB") or "LibreChat"
SLIDE_EXPORT = os.environ.get("SLIDE_EXPORT_URL", "http://slide-export:8090").rstrip("/")
DOMAIN = os.environ.get("DOMAIN_CLIENT", "").rstrip("/")
CONV_ID = os.environ.get("LIBRECHAT_BODY_CONVERSATIONID", "").strip()
DECK_DIR = Path(os.environ.get("DECK_DIR", "/app/client/public/images/decks"))
# 내보낸 PDF 는 즉시 다운로드용 — TTL 지난 파일은 export 시점에 정리(누적 방지).
DECK_TTL_SEC = int(os.environ.get("DECK_TTL_SEC", "86400"))  # 기본 24h

# 아티팩트 래퍼 안의 HTML 문서를 직접 캡처(```html 펜스 유무 무관).
_HTML_RE = re.compile(
    r':::artifact\{[^}]*type="text/html"[^}]*\}.*?(<!doctype html.*?</html>)',
    re.IGNORECASE | re.DOTALL,
)

mcp = FastMCP("export-deck")


def _prune_old() -> None:
    """TTL 지난 deck-*.pdf 정리 (best-effort) — export 시점에 누적분 청소."""
    if not DECK_DIR.exists():
        return
    cutoff = time.time() - DECK_TTL_SEC
    for f in DECK_DIR.glob("deck-*.*"):  # pdf + pptx
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def _latest_deck_html() -> str | None:
    """현재 대화의 최신 assistant 메시지에서 text/html 아티팩트 HTML 을 추출."""
    db = MongoClient(MONGO_URI)[MONGO_DB]
    cur = (db.messages
           .find({"conversationId": CONV_ID, "isCreatedByUser": False,
                  "content.text": {"$regex": r"type=\"text/html\""}})
           .sort("createdAt", -1).limit(1))
    for msg in cur:
        text = "\n".join(p.get("text", "") for p in (msg.get("content") or [])
                         if isinstance(p, dict) and isinstance(p.get("text"), str))
        m = _HTML_RE.search(text)
        if m:
            return m.group(1)
    return None


async def _export(fmt: str, label: str) -> str:
    """현재 대화 최신 HTML 덱을 slide-export 의 /{fmt} 로 렌더 → 저장 → 링크."""
    if not CONV_ID:
        return "내보낼 대화를 찾지 못했습니다. 발표자료를 만든 대화에서 다시 시도해 주세요."
    if not MONGO_URI:
        return "export_deck 설정 오류(MONGO_URI 미설정)."

    html = _latest_deck_html()
    if not html:
        return f"이 대화에서 {label} 로 내보낼 HTML 발표자료를 찾지 못했습니다. 먼저 Slide Studio 로 발표자료를 만들어 주세요."

    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(f"{SLIDE_EXPORT}/{fmt}", json={"html": html})
        r.raise_for_status()
        data = r.content

    DECK_DIR.mkdir(parents=True, exist_ok=True)
    _prune_old()  # TTL 지난 누적 산출물 정리
    name = f"deck-{uuid.uuid4().hex[:10]}.{fmt}"
    (DECK_DIR / name).write_bytes(data)

    url = f"{DOMAIN}/images/decks/{name}" if DOMAIN else f"/images/decks/{name}"
    kb = round(len(data) / 1024)
    return f"발표자료를 {label} 로 내보냈습니다 ({kb} KB).\n\n[📥 {label} 다운로드]({url})"


@mcp.tool()
async def export_deck_pdf() -> str:
    """현재 대화의 Slide Studio 발표자료(HTML 덱)를 16:9 PDF 로 내보내고
    다운로드 링크를 반환한다. 사용자가 PDF 내보내기/다운로드를 요청할 때 호출."""
    return await _export("pdf", "PDF")


@mcp.tool()
async def export_deck_pptx() -> str:
    """현재 대화의 Slide Studio 발표자료(HTML 덱)를 16:9 PPTX(PowerPoint)로 내보내고
    다운로드 링크를 반환한다. 사용자가 PPTX/파워포인트 내보내기를 요청할 때 호출.
    슬라이드당 이미지 1장 방식이라 텍스트 편집은 안 되지만 디자인은 그대로다."""
    return await _export("pptx", "PPTX")


if __name__ == "__main__":
    mcp.run()
