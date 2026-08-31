#!/usr/bin/env bash
# kchat 부하 시험 — 같은 순간에 여러 사람이 쓸 때 무슨 일이 벌어지는지 잰다.
#
# 이 저장소의 다른 검사는 전부 한 번에 하나씩 부른다. 그것이 지금까지 잡아 온
# 모든 결함의 모양이고, 제품의 모양은 아니다. 금요일 오후 다섯 시에 한 부서
# 아홉 명이 동시에 내보내기를 누른다. 그때 깨지는 것은 순차 검사가 볼 수 없는
# 방식으로 깨진다 — 두 요청이 변수를 나눠 쓰면 각자 앞뒤가 맞는 문서 두 개가
# 나오고, 그중 하나는 남의 것이다.
#
# **Non-destructive.** 계정을 하나 만들어 쓰고 끝나면 지운다. 읽기만 하는
# 경로를 두드리며, 모델을 부르지 않는다 — 부하를 재려고 크레딧을 태우지
# 않는다.
#
#   bash scripts/load-test.sh                  # 동시 20, 요청 200
#   CONCURRENCY=50 REQUESTS=500 bash scripts/load-test.sh
#
# 판정은 두 가지뿐이고 둘 다 정직하게 실패한다:
#
#   * 5xx 가 하나라도 나오면 실패한다. 부하에서의 500 은 느린 것이 아니라
#     틀린 것이다.
#   * 남의 데이터가 섞이면 실패한다. 계정 두 개로 각자의 세션 목록을 동시에
#     읽고, 서로의 세션 id 가 하나라도 보이면 그 자리에서 멈춘다.
#
# 무엇을 주장하지 않는가: 이 수치는 이 기계의 것이다. p95 가 몇 밀리초인지는
# 하드웨어와 그 순간의 부하에 달렸고, 여기서 뽑은 숫자를 다른 기계의 약속으로
# 읽으면 안 된다. 이 스크립트가 말하는 것은 "동시에 두드려도 틀리지 않는다"
# 이지 "빠르다" 가 아니다.
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

# ── 계정 둘 ──────────────────────────────────────────────────────────────
# 하나로는 격리를 잴 수 없다. 두 사람이 있어야 "남의 것이 보이는가" 를 물을 수
# 있고, 그 질문이 이 스크립트의 절반이다.
make_user() {
  local email="load-$1-$RUN@example.com"
  curl -s -o "$J/$1.reg" -X POST "$API/auth/signup" -H "$JSON" \
    -d "{\"email\":\"$email\",\"password\":\"$PASS\",\"name\":\"load $1\"}"
  local uid
  uid=$(python3 -c "import json;print(json.load(open('$J/$1.reg'))['user']['id'])" 2>/dev/null)
  [ -n "$uid" ] && MADE="$MADE $uid"
  # 승인과 크레딧 배정은 한 동작이다 — 배정 없이 승인된 계정은 로그인은 되고
  # 아무것도 못 한다. 이 스크립트는 모델을 부르지 않으므로 최소한만 준다.
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

# 각자 자기 세션을 하나씩 갖는다. 이 id 가 상대의 응답에 나타나면 유출이다.
sid() {
  curl -s -X POST "$API/sessions" -H "$JSON" -H "Authorization: Bearer $1" \
    -d '{"kind":"chat","title":"부하 시험"}' \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))'
}
SA=$(sid "$A"); SB=$(sid "$B")
# 빈 id 로 넘어가면 뒤의 grep -q "" 가 모든 응답에 매치해 유출을 발명한다.
# 검사가 스스로 만들어 낸 실패는 실패보다 나쁘다.
[ -n "$SA" ] && [ -n "$SB" ] || { echo "세션을 만들지 못했습니다 — A=$SA B=$SB" >&2; exit 2; }
say "세션 A=$SA B=$SB"

# ── 두드리기 ─────────────────────────────────────────────────────────────
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

# ── 판정 1: 5xx ──────────────────────────────────────────────────────────
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

# ── 판정 2: 격리 ─────────────────────────────────────────────────────────
# A 의 응답에 B 의 세션이, B 의 응답에 A 의 세션이 보이면 유출이다.
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
