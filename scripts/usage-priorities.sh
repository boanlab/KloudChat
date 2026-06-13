#!/usr/bin/env bash
# Usage: usage-priorities.sh [--days N] [--apply] [-h]
#
# 실사용(LiteLLM 모델별 요청수·토큰·처리시간) 집계 → 리포트 + 그 빈도로 scheduler
# 우선순위 재산정. scheduler 의 --priorities 는 "사용 빈도 랭킹"으로 해석되므로
# (catalog.DEFAULT_PRIORITIES 주석) 최근 트래픽 많은 워크로드부터 VRAM 우선 할당.
#
#   기본(검토)   사용량 리포트 + 도출된 우선순위 + 적용 명령만 출력. 아무것도 안 바꿈.
#   --apply      python -m scheduler apply 를 도출 우선순위로 즉시 실행(컨테이너 재배치).
#   --days N      집계 윈도우(기본 7일).
#
# 우선순위 재정렬 대상 = vLLM 로컬 워크로드(chat=gemma-4-26b/auto-route, deep-research=
# qwen3.5:122b, coding=qwen3-coder-next, rag=bge-m3) — scheduler 가 배치하는 것들.
# 리포트 표엔 OR 상용 모델 + Image/Video Studio(로컬 FLUX/LTXV + 외부 nano-banana/Veo/Sora,
# 과금 passthrough 로 집계)도 포함. 단 이미지·비디오는 같은 ComfyUI(image-flux 워크로드)
# 공유라 별도 placement 결정 없음 → 우선순위 재정렬엔 미포함(리포트 전용).
#
# ⚠ --apply 는 vLLM 컨테이너 재기동(가중치 재로드 gemma ~3-5분, 122b ~5-7분) 유발
#   가능. 저트래픽 시간대 + 변화 충분히 클 때만. diff 없으면 no-op.
set -euo pipefail

__SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$__SCRIPT_DIR/lib.sh"

DAYS=7
APPLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --days) DAYS="${2:?--days 값 필요}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h|--help) sed -n '2,/^set -/p' "$0" | sed 's/^# \{0,1\}//;/^set -/d'; exit 0 ;;
    *) err "unknown: $1"; exit 2 ;;
  esac
done

LITELLM_URL="$(env_get LITELLM_URL)"; LITELLM_URL="${LITELLM_URL:-http://localhost:8000}"
KEY="$(env_get LITELLM_MASTER_KEY)"
[[ -n "$KEY" ]] || { err "LITELLM_MASTER_KEY 미설정 (.env)"; exit 1; }

START="$(date -d "-${DAYS} days" +%F 2>/dev/null || date -v-"${DAYS}"d +%F)"

# DEFAULT_PRIORITIES 를 catalog 에서 가져와 base 로 (코드와 동기). 실패 시 폴백.
BASE_CSV="$(python3 -c 'from scheduler.catalog import DEFAULT_PRIORITIES; print(",".join(DEFAULT_PRIORITIES))' 2>/dev/null \
  || echo 'chat,rag,agents-chain,artifacts,image,deep-research,vision-ocr,coding,mcp-deep-research,web-search,code-interpreter,mcp-fetch-url,mcp-time,mcp-usage,mcp-youtube')"

hdr "사용량 리포트 — 최근 ${DAYS}일 (≥ ${START}) · LiteLLM ${LITELLM_URL}"

# /spend/logs raw(모델별 토큰·처리시간 포함) 집계. 사람용 표는 stderr, DERIVED=<csv>
# 한 줄만 stdout 으로 흘려 bash 가 수신.
DERIVED="$(curl -fsS "${LITELLM_URL}/spend/logs" -H "Authorization: Bearer ${KEY}" 2>/dev/null \
  | START="$START" BASE_CSV="$BASE_CSV" python3 -c '
import json, os, sys
from collections import defaultdict

# vLLM 로컬 워크로드 → 그 워크로드를 굴리는 LiteLLM model_name (substring 매칭).
FEATURE_MODELS = {
    "chat":          ["local/gemma-4-26b", "local/auto-route"],
    "deep-research": ["local/qwen3.5:122b"],
    "coding":        ["local/qwen3-coder-next"],
    "rag":           ["bge-m3"],
}
start = os.environ["START"]
base = os.environ["BASE_CSV"].split(",")

try:
    rows = json.load(sys.stdin)
except Exception:
    print("LiteLLM /spend/logs 파싱 실패", file=sys.stderr); print("DERIVED="); sys.exit(0)
rows = rows if isinstance(rows, list) else rows.get("data", [])

agg = defaultdict(lambda: {"n": 0, "in": 0, "out": 0, "ms": 0.0, "nms": 0, "sp": 0.0})
# 한 로그 → 집계 키. 미디어 생성은 LiteLLM 완료가 아니라 과금 passthrough 로 잡는다:
#  - /localbill(api_base=billsink): 로컬 이미지=spend 0.02 고정, 로컬 비디오=0.04×초
#  - /orvideo/submit(api_base=openrouter…videos, spend>0): OR Veo/Sora 비디오 (job 폴은 spend 0 → 스킵)
#  - 외부 이미지 모델(nano-banana 등) 완료 → Image Studio
EXT_IMAGE = {"nano-banana", "nano-banana-2", "gpt-image-2"}
def classify(r):
    ct = str(r.get("call_type") or "")
    ab = str(r.get("api_base") or "")
    sp = r.get("spend") or 0
    if "pass_through" in ct:
        if "billsink" in ab:
            return "Image Studio" if abs(sp - 0.02) < 1e-4 else "Video Studio"
        if "openrouter" in ab and "video" in ab:
            return "Video Studio" if sp > 0 else None   # job 폴 스킵
        return None
    mg = r.get("model_group") or r.get("model")
    if not mg:
        return None
    return "Image Studio" if mg in EXT_IMAGE else mg

oldest = None
for r in rows:
    st = (r.get("startTime") or "")[:10]
    if not st:
        continue
    if oldest is None or st < oldest:
        oldest = st
    if st < start:
        continue
    mg = classify(r)
    if not mg:
        continue
    a = agg[mg]
    a["n"]   += 1
    a["in"]  += int(r.get("prompt_tokens") or 0)
    a["out"] += int(r.get("completion_tokens") or 0)
    dur = r.get("request_duration_ms")
    if dur:
        a["ms"] += float(dur); a["nms"] += 1
    a["sp"] += float(r.get("spend") or 0)

def htok(x):
    return f"{x/1e6:.2f}M" if x >= 1e6 else (f"{x/1e3:.1f}K" if x >= 1e3 else str(x))

# 모델/스튜디오별 표 (요청수 desc). Image/Video Studio 는 토큰 무의미라 billed $ 가 핵심.
print("  %-34s%9s%10s%10s%9s%10s" % ("model / studio", "requests", "in tok", "out tok", "avg ms", "billed $"), file=sys.stderr)
for mg, a in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
    avg = (a["ms"]/a["nms"]) if a["nms"] else 0
    print("  %-34s%9d%10s%10s%9.0f%10s" % (mg, a["n"], htok(a["in"]), htok(a["out"]), avg, "$%.2f" % a["sp"]), file=sys.stderr)
if not agg:
    print("  (윈도우 내 트래픽 없음)", file=sys.stderr)
if oldest and oldest > start:
    print(f"  ⚠ raw 로그가 {oldest} 부터만 남아있어 윈도우가 잘렸을 수 있음", file=sys.stderr)

# feature 별 요청수 → 랭킹 (substring 매칭)
feat_req = {}
for f, pats in FEATURE_MODELS.items():
    feat_req[f] = sum(a["n"] for mg, a in agg.items() if any(p in mg for p in pats))

movable = list(FEATURE_MODELS)
order = {f: i for i, f in enumerate(base)}
movable_sorted = sorted(movable, key=lambda f: (-feat_req.get(f, 0), order.get(f, 99)))
derived = movable_sorted + [f for f in base if f not in movable]

print(file=sys.stderr)
print("  워크로드 우선순위(요청수):", file=sys.stderr)
for f in movable_sorted:
    print(f"    {f:<14}{feat_req.get(f,0):>8}", file=sys.stderr)

print("DERIVED=" + ",".join(derived))
' )"

DERIVED="$(grep '^DERIVED=' <<<"$DERIVED" | cut -d= -f2-)"
[[ -n "$DERIVED" ]] || { err "우선순위 도출 실패 (LiteLLM 도달/응답 확인)"; exit 1; }

echo
ok "도출된 scheduler 우선순위:"
echo "  $DERIVED"
[[ "$DERIVED" == "$BASE_CSV" ]] && info "기본(코드) 우선순위와 동일 — 재배치 불필요."
echo

if (( APPLY )); then
  warn "scheduler apply 실행 — vLLM 재배치 가능(가중치 재로드). diff 없으면 no-op."
  python3 -m scheduler apply -y --priorities "$DERIVED"
  ok "적용 완료. litellm 반영: ./scripts/gen-litellm-config.sh && docker compose -f docker-compose.litellm.yml restart litellm"
else
  info "검토 모드(미적용). 관리자가 직접 트리거:"
  echo "    영향 미리보기:  python3 -m scheduler plan --priorities \"$DERIVED\""
  echo "    적용:           ./scripts/usage-priorities.sh --days ${DAYS} --apply"
fi
