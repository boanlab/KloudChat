#!/usr/bin/env bash
# kchat load test — concurrent reads from two accounts.
#
# **Non-destructive.** Creates two accounts, hits read-only paths, deletes
# them; no model calls, no credits spent.
#
#   bash scripts/load-test.sh                  # concurrency 20, 200 requests
#   CONCURRENCY=50 REQUESTS=500 bash scripts/load-test.sh
#
# Two verdicts: any 5xx fails; any of one account's session ids in the other's
# responses fails. Latency figures are for this machine only.
set -u

# shellcheck source=scripts/lib/env.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/env.sh"

API=${API:-http://localhost:8100/api}
ADMIN_EMAIL=${ADMIN_EMAIL:-admin@example.com}
ADMIN_PASS=${ADMIN_PASS:-KloudChat-Admin-1234}
CONCURRENCY=${CONCURRENCY:-20}
REQUESTS=${REQUESTS:-200}
JSON='Content-Type: application/json'

J=$(mktemp -d)
RUN=$(date +%s)$$
PASS='load-test-password'
MADE=""
fail=0

cleanup() {
  [ "${HAS_ADMIN:-no}" = "yes" ] && for uid in $MADE; do
    curl -s -o /dev/null -X DELETE "$API/admin/users/$uid" -H "$AH"
  done
  rm -rf "$J"
}
trap cleanup EXIT

say()  { echo "  $*"; }
bad()  { echo "  FAIL $*"; fail=$((fail+1)); }

ADMIN=$(curl -s -X POST "$API/auth/login" -H "$JSON" \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("accessToken",""))')
AH="Authorization: Bearer $ADMIN"
HAS_ADMIN=no
if [ -n "$ADMIN" ] && [ "$(curl -s -o /dev/null -w '%{http_code}' "$API/admin/users" -H "$AH")" = "200" ]; then
  HAS_ADMIN=yes
fi
if [ "$HAS_ADMIN" != "yes" ]; then
  echo "관리자 계정이 없어 계정을 만들 수 없습니다 — ADMIN_EMAIL/ADMIN_PASS 를 주세요." >&2
  exit 2
fi

# ── two accounts ─────────────────────────────────────────────────────────
# Isolation needs two.
make_user() {
  local email="load-$1-$RUN@example.com"
  curl -s -o "$J/$1.reg" -X POST "$API/auth/signup" -H "$JSON" \
    -d "{\"email\":\"$email\",\"password\":\"$PASS\",\"name\":\"load $1\"}"
  local uid
  uid=$(python3 -c "import json;print(json.load(open('$J/$1.reg'))['user']['id'])" 2>/dev/null)
  [ -n "$uid" ] && MADE="$MADE $uid"
  # Approval assigns credits; this script calls no model, so the minimum.
  curl -s -o /dev/null -X POST "$API/admin/users/$uid/approve" -H "$AH" -H "$JSON" \
    -d '{"monthlyCredits":100}'
  curl -s -X POST "$API/auth/login" -H "$JSON" \
    -d "{\"email\":\"$email\",\"password\":\"$PASS\"}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("accessToken",""))'
}

echo "== 계정 준비 =="
A=$(make_user a); B=$(make_user b)
[ -n "$A" ] && [ -n "$B" ] || { echo "계정을 만들지 못했습니다." >&2; exit 2; }
say "두 계정 준비됨"

# One session each; its id in the other's responses is a leak.
sid() {
  curl -s -X POST "$API/sessions" -H "$JSON" -H "Authorization: Bearer $1" \
    -d '{"kind":"chat","title":"부하 시험"}' \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))'
}
SA=$(sid "$A"); SB=$(sid "$B")
# An empty id would make `grep -q ""` match every response.
[ -n "$SA" ] && [ -n "$SB" ] || { echo "세션을 만들지 못했습니다 — A=$SA B=$SB" >&2; exit 2; }
say "세션 A=$SA B=$SB"

# ── requests ─────────────────────────────────────────────────────────────
echo "== 동시 $CONCURRENCY, 요청 $REQUESTS =="
: > "$J/codes"
: > "$J/times"

hit() {
  local token=$1 out=$2
  local result
  result=$(curl -s -o "$out" -w '%{http_code} %{time_total}' \
    "$API/sessions" -H "Authorization: Bearer $token")
  echo "$result" >> "$J/codes"
}
export -f hit

started=$(date +%s.%N)
running=0
for i in $(seq 1 "$REQUESTS"); do
  if [ $((i % 2)) -eq 0 ]; then hit "$A" "$J/body.a.$i" & else hit "$B" "$J/body.b.$i" & fi
  running=$((running+1))
  if [ "$running" -ge "$CONCURRENCY" ]; then wait -n 2>/dev/null || wait; running=$((running-1)); fi
done
wait
ended=$(date +%s.%N)

# ── verdict 1: 5xx ───────────────────────────────────────────────────────
python3 - "$J/codes" "$started" "$ended" <<'PY'
import sys, collections
codes, started, ended = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
rows = [line.split() for line in open(codes) if line.strip()]
status = collections.Counter(r[0] for r in rows)
times = sorted(float(r[1]) for r in rows)
n = len(times)
print(f"  {n}건, {ended - started:.1f}초, 초당 {n / max(ended - started, 1e-9):.1f}건")
print("  응답:", dict(status))
if n:
    p = lambda q: times[min(n - 1, int(n * q))] * 1000
    print(f"  중앙 {p(0.5):.0f}ms · p95 {p(0.95):.0f}ms · 최대 {times[-1] * 1000:.0f}ms")
PY

if grep -qE '^5[0-9][0-9] ' "$J/codes"; then
  bad "5xx 가 나왔습니다 — $(grep -cE '^5[0-9][0-9] ' "$J/codes")건"
else
  say "5xx 없음"
fi

# ── verdict 2: isolation ─────────────────────────────────────────────────
leaked=0
for f in "$J"/body.a.*; do grep -q "$SB" "$f" 2>/dev/null && leaked=$((leaked+1)); done
for f in "$J"/body.b.*; do grep -q "$SA" "$f" 2>/dev/null && leaked=$((leaked+1)); done
if [ "$leaked" -gt 0 ]; then
  bad "남의 세션이 $leaked 건의 응답에 섞였습니다"
else
  say "격리 유지됨 — 서로의 세션이 한 건도 보이지 않음"
fi

echo
if [ "$fail" -eq 0 ]; then echo "통과"; else echo "실패 $fail 건"; fi
exit "$fail"
