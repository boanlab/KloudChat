# slide-export — 덱 PDF/PPTX 내보내기

> Slide Studio 가 만든 자체완결 HTML 발표자료를 **16:9 PDF / PPTX** 로 렌더한다. headless chromium 이라 인라인 SVG/flexbox/JS 폰트 사이징이 화면과 동일하게 나오고, 한글(Noto CJK)·그라데이션·차트·표·간지가 그대로 보존된다.

## 한눈에

```
HTML 덱 (:::artifact text/html)
   └─ POST /pdf | /pptx {html}  →  slide-export(playwright/chromium)
        ├─ 각 .slide 만 차례로 active 토글 → 1280×720 PNG 캡처 (N장)
        ├─ PDF : Pillow 로 이미지 1장/페이지 (96dpi → 13.333×7.5in)
        └─ PPTX: python-pptx 로 이미지 1장/슬라이드 (13.333×7.5in)
   └─ N 슬라이드 = N 페이지 보장 (둘 다 16:9)
```

- 두 형식 모두 **슬라이드 단위 캡처** — 덱의 `.slide` 를 하나씩 active 토글해 화면 그대로 캡처
- **개수 기반 캡처 채택 이유**: CSS page-break 방식이 일부 슬라이드를 누락(10장 덱 → 8페이지 버그)하던 문제 해소
- **트레이드오프**: 텍스트 선택성 상실(이미지 슬라이드), 단 그라데이션/SVG/한글 디자인은 화면과 동일

## 구성

| 파일 | 역할 |
|---|---|
| [`slide-export/app.py`](../../slide-export/app.py) | FastAPI: `POST /pdf {html}` → PDF, `POST /pptx {html}` → PPTX, `GET /health`. chromium 은 기동 시 1회 launch 후 재사용 |
| [`slide-export/Dockerfile`](../../slide-export/Dockerfile) | python-slim + chromium 런타임 libs + `fonts-noto-cjk`(한글) + playwright chromium |
| `docker-compose.yml::slide-export` | opt-in 서비스 (`profiles: [slide-export]`) |

## 띄우기 / 테스트

```bash
docker compose --profile slide-export up -d slide-export   # boanlab/kloudchat-slide-export pull → 기동
curl -s localhost:8090/health            # {"status":"ok"}
# 덱 HTML → PDF
python3 -c "import json,urllib.request as u; html=open('deck.html').read(); \
  open('deck.pdf','wb').write(u.urlopen(u.Request('http://localhost:8090/pdf', \
  data=json.dumps({'html':html}).encode(), headers={'Content-Type':'application/json'})).read())"
```

**검증됨(PoC)**:

- 6장짜리 덱 → **6페이지 PDF, 페이지당 960×540pt(16:9 정확)**
- 한글·그라데이션·카드·표·SVG 막대그래프·간지 모두 보존

## 사용자 트리거 — export_deck MCP (연결됨)

Slide Studio 에 `export_deck` MCP 도구 부착 → **"PDF로 내보내줘"** 한 번에 다운로드까지 동작.

[`mcp/export_deck.py`](../../mcp/export_deck.py) — `export_deck_pdf()` / `export_deck_pptx()` 흐름:
1. `{{LIBRECHAT_BODY_CONVERSATIONID}}` 로 Mongo 에서 그 대화의 **최신 `:::artifact{type=text/html}` 덱 HTML** 을 추출 (content[].text 의 `<!doctype html>…</html>`).
2. slide-export `/pdf` 또는 `/pptx` 로 렌더.
3. 결과를 LibreChat 이 서빙하는 `public/images/decks/` 에 저장 (MCP 가 LibreChat 컨테이너 안에서 돌므로 직접 쓰기 가능 — 별도 스토리지 불필요).
4. `{DOMAIN_CLIENT}/images/decks/<id>.{pdf,pptx}` 다운로드 링크를 마크다운으로 반환.

**연결 지점**:

- `librechat.yaml::mcpServers.export_deck` — stdio, `startup:false`, 도구 2개 노출
- `manage.sh` 의 ppt kind 가 `export_deck` 만 부착 (저작은 여전히 도구 없이 HTML 직접)
- `agent.ppt` 의 내보내기 지시문

| env | 값/출처 |
|---|---|
| `MONGO_URI` | docker-compose LibreChat |
| `SLIDE_EXPORT_URL` | docker-compose LibreChat |
| `DOMAIN_CLIENT` | docker-compose LibreChat |

**검증됨**:

- 실제 **10장 덱 → 10페이지 PDF + 10슬라이드 PPTX** (960×540pt / 13.333×7.5in, 16:9), 다운로드 링크 http 200
- 에이전트(122b)가 "PDF/PPTX로 내보내줘"에 `export_deck_pdf`/`export_deck_pptx` 실제 호출 확인

> **누적 정리**: `_export` 가 매 호출마다 `DECK_TTL_SEC`(기본 24h) 지난 `deck-*.*`(pdf/pptx)를 `_prune_old` 로 정리. 즉시 다운로드용이라 TTL 충분.

> **MONGO_URI denylist 주의**:
> - LibreChat 은 보안상 `MONGO_URI`(+`JWT_SECRET`/`CREDS_*`/`REDIS_*` 등)를 MCP `${}` 치환에서 **차단**
> - 그래서 export_deck 는 비-denylist 이름 `KC_MONGO_URI`(docker-compose 에서 동일 값 노출)를 `${KC_MONGO_URI}` 로 수령
> - MCP 에 다른 자격증명 env 필요 시 동일 우회 필요

## 다음 확장 — Google Drive

- PDF/PPTX: 완료
- 다음: 생성 파일을 Google Drive API 로 업로드(OAuth) — `export_deck_*` 옆에 Drive 업로드 옵션 추가

## 같이 보면 좋은 문서

- [Slide Studio 데모](../../examples/slide-studio-demo.md) · [도구/MCP](../operator/tools.md)
