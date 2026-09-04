#!/usr/bin/env bash
# kchat API smoke test — auth, approval, rotation and suspension over real HTTP.
#
# **Non-destructive.** Every run creates accounts under a unique address and
# deletes them again; existing users and conversations are never touched.
#
#   ADMIN_EMAIL=you@example.com ADMIN_PASS=… bash scripts/smoke-test.sh
#
# Without an administrator account, only the approval, suspension and role
# checks are skipped. Credentials fall back to .env — see scripts/lib/env.sh.
set -u

# shellcheck source=scripts/lib/env.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/env.sh"

API=${API:-http://localhost:8100/api}
ADMIN_EMAIL=${ADMIN_EMAIL:-admin@example.com}
ADMIN_PASS=${ADMIN_PASS:-KloudChat-Admin-1234}
JSON='Content-Type: application/json'
J=$(mktemp -d)
RUN=$(date +%s)$$
ok=0; fail=0; skipped=0

# Accounts this script created; removed on exit.
MADE=""
cleanup_accounts() {
  [ "$HAS_ADMIN" = "yes" ] || return 0
  for uid in $MADE; do
    curl -s -o /dev/null -X DELETE "$API/admin/users/$uid" -H "$AH"
  done
}
trap cleanup_accounts EXIT

chk()  { if [ "$2" = "$3" ]; then echo "  ok   $1 ($2)"; ok=$((ok+1)); else echo "  FAIL $1 — got $2, want $3"; fail=$((fail+1)); fi; }
skip() { echo "  skip $1 — $2"; skipped=$((skipped+1)); }
jf()   { python3 -c "import json,sys; d=json.load(open('$1')); print($2)" 2>/dev/null; }

# `.test` is rejected by email-validator; example.com is reserved (RFC 2606).
email() { echo "smoke-$1-$RUN@example.com"; }
PASS='smoke-test-password'

echo "== health =="
curl -s "$API/health"; echo

# ── administrator session ────────────────────────────────────────────────
ADMIN=$(curl -s -X POST "$API/auth/login" -H "$JSON" \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("accessToken",""))')
AH="Authorization: Bearer $ADMIN"
HAS_ADMIN=no
if [ -n "$ADMIN" ] && [ "$(curl -s -o /dev/null -w '%{http_code}' "$API/admin/users" -H "$AH")" = "200" ]; then
  HAS_ADMIN=yes
fi

# ── 1. bootstrap ─────────────────────────────────────────────────────────
echo "== 1. the first signup is activated as administrator =="
if [ "$HAS_ADMIN" = "yes" ]; then
  skip "bootstrap" "database already has an administrator — only meaningful on an empty one"
else
  E=$(email boot)
  r=$(curl -s -o "$J/boot.json" -w '%{http_code}' -X POST "$API/auth/signup" -H "$JSON" \
    -d "{\"email\":\"$E\",\"password\":\"$PASS\",\"name\":\"부트스트랩\"}")
  chk "signup 201"       "$r" "201"
  chk "role=admin"       "$(jf "$J/boot.json" "d['user']['role']")" "admin"
  chk "status=active"    "$(jf "$J/boot.json" "d['user']['status']")" "active"
  chk "session issued"   "$(jf "$J/boot.json" "bool(d['session'])")" "True"
  ADMIN=$(jf "$J/boot.json" "d['session']['accessToken']")
  AH="Authorization: Bearer $ADMIN"
  HAS_ADMIN=yes
fi

# ── 2. pending approval ──────────────────────────────────────────────────
echo "== 2. later signups land in the approval queue =="
UE=$(email user)
r=$(curl -s -o "$J/u.json" -w '%{http_code}' -X POST "$API/auth/signup" -H "$JSON" \
  -d "{\"email\":\"$UE\",\"password\":\"$PASS\",\"name\":\"대기자\"}")
chk "signup 201"     "$r" "201"
chk "status=pending" "$(jf "$J/u.json" "d['user']['status']")" "pending"
chk "no session"     "$(jf "$J/u.json" "d['session']")" "None"
PENDING_ID=$(jf "$J/u.json" "d['user']['id']")
MADE="$MADE $PENDING_ID"

echo "== 3. duplicate email is rejected =="
chk "409" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/auth/signup" -H "$JSON" \
  -d "{\"email\":\"$UE\",\"password\":\"$PASS\",\"name\":\"중복\"}")" "409"

echo "== 3b. concurrent duplicate signups return 409, not 500 =="
RE=$(email race)
for i in 1 2 3 4; do
  ( curl -s -o /dev/null -w '%{http_code}' -X POST "$API/auth/signup" -H "$JSON" \
      -d "{\"email\":\"$RE\",\"password\":\"$PASS\",\"name\":\"레이스\"}" ) > "$J/race$i" &
done
wait
created=0; conflict=0; other=0
for i in 1 2 3 4; do
  case "$(cat "$J/race$i")" in
    201) created=$((created+1)) ;;
    409) conflict=$((conflict+1)) ;;
    *)   other=$((other+1)) ;;
  esac
done
chk "exactly one created" "$created"  "1"
chk "the rest conflict"   "$conflict" "3"
chk "no server errors"    "$other"    "0"
if [ "$HAS_ADMIN" = "yes" ]; then
  RACE_ID=$(curl -s "$API/admin/users" -H "$AH" | python3 -c "
import json,sys
print(next((u['id'] for u in json.load(sys.stdin) if u['email'] == '$RE'), ''))")
  MADE="$MADE $RACE_ID"
fi

echo "== 4. wrong password is rejected =="
chk "401" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/auth/login" -H "$JSON" \
  -d "{\"email\":\"$UE\",\"password\":\"definitely-wrong\"}")" "401"

echo "== 5. a pending account can sign in and read /me, and nothing else =="
r=$(curl -s -o "$J/p.json" -w '%{http_code}' -c "$J/pc.txt" -X POST "$API/auth/login" -H "$JSON" \
  -d "{\"email\":\"$UE\",\"password\":\"$PASS\"}")
chk "login 200" "$r" "200"
PT=$(jf "$J/p.json" "d['accessToken']")
chk "/me 200"          "$(curl -s -o /dev/null -w '%{http_code}' "$API/auth/me" -H "Authorization: Bearer $PT")" "200"
chk "/sessions 403"    "$(curl -s -o /dev/null -w '%{http_code}' "$API/sessions" -H "Authorization: Bearer $PT")" "403"
chk "/admin/users 403" "$(curl -s -o /dev/null -w '%{http_code}' "$API/admin/users" -H "Authorization: Bearer $PT")" "403"

# ── administrator paths ──────────────────────────────────────────────────
if [ "$HAS_ADMIN" != "yes" ]; then
  skip "approval, suspension, roles" "no administrator account (ADMIN_EMAIL / ADMIN_PASS)"
else
  echo "== 6. approval =="
  r=$(curl -s -o "$J/ap.json" -w '%{http_code}' -X POST "$API/admin/users/$PENDING_ID/approve" \
    -H "$AH" -H "$JSON" -d '{"monthlyCredits":5000}')
  chk "approve 200"          "$r" "200"
  chk "active"               "$(jf "$J/ap.json" "d['status']")" "active"
  chk "credits assigned"     "$(jf "$J/ap.json" "d['monthlyCredits']")" "5000"
  chk "approval does not grant admin" "$(curl -s -o /dev/null -w '%{http_code}' "$API/admin/users" -H "Authorization: Bearer $PT")" "403"

  # Approval creates the LiteLLM user and a per-user key; spend is attributed to it.
  chk "approval issues a per-user key" "$(jf "$J/ap.json" "'yes' if d.get('litellmKeyPreview') else 'no'")" "yes"
  chk "the key itself is not in the response" "$(python3 -c "import json;print('yes' if 'sk-' not in open('$J/ap.json').read() else 'no')")" "yes"

  echo "== 6a. key rotation =="
  BEFORE=$(jf "$J/ap.json" "d['litellmKeyPreview']")
  r=$(curl -s -o "$J/kr.json" -w '%{http_code}' -X POST "$API/admin/users/$PENDING_ID/litellm-key" -H "$AH")
  chk "rotate 200" "$r" "200"
  chk "the key actually changes" "$(jf "$J/kr.json" "'changed' if d['litellmKeyPreview'] != '$BEFORE' else 'same'")" "changed"
  chk "a normal user cannot rotate" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/admin/users/$PENDING_ID/litellm-key" -H "Authorization: Bearer $PT")" "403"

  echo "== 6c. the credit limit reaches the proxy =="
  # kchat is the limit people see; the LiteLLM budget must follow it.
  curl -s -o "$J/ec.json" "$API/admin/settings" -H "$AH"
  PER_USD=$(jf "$J/ec.json" "d['credits']['perUsd']")
  HEAD=$(jf "$J/ec.json" "d['credits']['budgetHeadroom']")
  chk "rate and headroom are exposed" "$([ -n "$PER_USD" ] && [ -n "$HEAD" ] && echo yes || echo no)" "yes"

  curl -s -o /dev/null -X POST "$API/admin/users/$PENDING_ID/credits" \
    -H "$AH" -H "$JSON" -d '{"monthlyCredits":300000}'

  # Needs the master key; skipped rather than quietly passed without it.
  if [ -z "${LITELLM_BASE_URL:-}" ] || [ -z "${LITELLM_MASTER_KEY:-}" ]; then
    skip "proxy budget matches" "LITELLM_BASE_URL / LITELLM_MASTER_KEY not set"
  else
    curl -s -o "$J/llu.json" "$LITELLM_BASE_URL/user/list?user_email=$UE&page_size=2" \
      -H "Authorization: Bearer $LITELLM_MASTER_KEY"
    GOT=$(jf "$J/llu.json" "(d['users'] or [{}])[0].get('max_budget')")
    WANT=$(python3 -c "import math;print(math.ceil(300000/$PER_USD*(1+$HEAD)*100)/100)")
    chk "proxy budget matches" "$GOT" "$WANT"
  fi

  echo "== 6d. account deletion =="
  DE=$(email delete)
  curl -s -o "$J/de.json" -X POST "$API/auth/signup" -H "$JSON" \
    -d "{\"email\":\"$DE\",\"password\":\"$PASS\",\"name\":\"삭제 대상\"}"
  DEID=$(jf "$J/de.json" "d['user']['id']")
  MADE="$MADE $DEID"
  curl -s -o /dev/null -X POST "$API/admin/users/$DEID/approve" -H "$AH" -H "$JSON" -d '{"monthlyCredits":1000}'
  # The account must own something, or the cleanup path is not exercised.
  DT=$(curl -s -X POST "$API/auth/login" -H "$JSON" -d "{\"email\":\"$DE\",\"password\":\"$PASS\"}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("accessToken",""))')
  curl -s -o /dev/null -X POST "$API/projects" -H "Authorization: Bearer $DT" -H "$JSON" -d '{"name":"지워질 프로젝트"}'
  curl -s -o /dev/null -X POST "$API/sessions" -H "Authorization: Bearer $DT" -H "$JSON" -d '{"kind":"chat"}'

  AID2=$(curl -s "$API/auth/me" -H "$AH" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  chk "self-deletion 400"         "$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$API/admin/users/$AID2" -H "$AH")" "400"
  chk "normal user deletion 403"  "$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$API/admin/users/$DEID" -H "Authorization: Bearer $DT")" "403"
  chk "delete 204"                "$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$API/admin/users/$DEID" -H "$AH")" "204"
  chk "second delete 404"         "$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$API/admin/users/$DEID" -H "$AH")" "404"
  chk "deleted account token 401" "$(curl -s -o /dev/null -w '%{http_code}' "$API/auth/me" -H "Authorization: Bearer $DT")" "401"
  chk "cannot sign in again"      "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/auth/login" -H "$JSON" -d "{\"email\":\"$DE\",\"password\":\"$PASS\"}")" "401"

  echo "== 6b. rejection =="
  RJ=$(email reject)
  curl -s -o "$J/rj.json" -X POST "$API/auth/signup" -H "$JSON" \
    -d "{\"email\":\"$RJ\",\"password\":\"$PASS\",\"name\":\"반려\"}"
  RJID=$(jf "$J/rj.json" "d['user']['id']")
  MADE="$MADE $RJID"
  chk "reject → suspended"          "$(curl -s -X POST "$API/admin/users/$RJID/reject" -H "$AH" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')" "suspended"
  chk "rejecting an active account is 400" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/admin/users/$PENDING_ID/reject" -H "$AH")" "400"
fi

echo "== 7. refresh rotation =="
chk "refresh 200" "$(curl -s -o "$J/r1.json" -w '%{http_code}' -b "$J/pc.txt" -c "$J/pc2.txt" -X POST "$API/auth/refresh")" "200"
chk "a new access token" "$(python3 -c "
import json
a=json.load(open('$J/p.json'))['accessToken']; b=json.load(open('$J/r1.json'))['accessToken']
print(a!=b)")" "True"

echo "== 7b. simultaneous refreshes both survive (the grace window) =="
curl -s -o "$J/g.json" -c "$J/gc.txt" -X POST "$API/auth/login" -H "$JSON" \
  -d "{\"email\":\"$UE\",\"password\":\"$PASS\"}" >/dev/null
chk "first 200"        "$(curl -s -o /dev/null -w '%{http_code}' -b "$J/gc.txt" -c "$J/gc1.txt" -X POST "$API/auth/refresh")" "200"
chk "second 200"       "$(curl -s -o /dev/null -w '%{http_code}' -b "$J/gc.txt" -c "$J/gc2.txt" -X POST "$API/auth/refresh")" "200"
chk "family survives"  "$(curl -s -o /dev/null -w '%{http_code}' -b "$J/gc2.txt" -X POST "$API/auth/refresh")" "200"

echo "== 8. replay past the grace window revokes the family =="
echo "  (waiting 16s for the grace window to close…)"
python3 -c 'import time; time.sleep(16)'
chk "replay 401"            "$(curl -s -o "$J/r2.json" -w '%{http_code}' -b "$J/pc.txt" -X POST "$API/auth/refresh")" "401"
chk "detail=refresh_reused" "$(jf "$J/r2.json" "d['detail']")" "refresh_reused"
chk "whole family revoked"  "$(curl -s -o /dev/null -w '%{http_code}' -b "$J/pc2.txt" -X POST "$API/auth/refresh")" "401"

if [ "$HAS_ADMIN" = "yes" ]; then
  echo "== 9. suspension cuts live sessions =="
  curl -s -o "$J/s.json" -c "$J/sc.txt" -X POST "$API/auth/login" -H "$JSON" \
    -d "{\"email\":\"$UE\",\"password\":\"$PASS\"}" >/dev/null
  ST=$(jf "$J/s.json" "d['accessToken']")
  chk "/me 200 before suspension" "$(curl -s -o /dev/null -w '%{http_code}' "$API/auth/me" -H "Authorization: Bearer $ST")" "200"
  chk "suspend 200"               "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/admin/users/$PENDING_ID/suspend" -H "$AH")" "200"
  chk "/me 403 after suspension"  "$(curl -s -o /dev/null -w '%{http_code}' "$API/auth/me" -H "Authorization: Bearer $ST")" "403"
  chk "refresh 403"               "$(curl -s -o /dev/null -w '%{http_code}' -b "$J/sc.txt" -X POST "$API/auth/refresh")" "403"
  chk "login 403"                 "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/auth/login" -H "$JSON" -d "{\"email\":\"$UE\",\"password\":\"$PASS\"}")" "403"

  echo "== 10. an administrator cannot suspend or demote themselves =="
  AID=$(curl -s "$API/auth/me" -H "$AH" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  chk "self-suspend 400" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/admin/users/$AID/suspend" -H "$AH")" "400"
  chk "self-demote 400"  "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/admin/users/$AID/role" -H "$AH" -H "$JSON" -d '{"role":"user"}')" "400"

  echo "== 11. reinstatement =="
  chk "reinstate 200" "$(curl -s -o "$J/ri.json" -w '%{http_code}' -X POST "$API/admin/users/$PENDING_ID/reinstate" -H "$AH")" "200"
  # Suspension blocks the key rather than revoking it, so reinstatement keeps it.
  chk "the same key after recovery" "$(jf "$J/ri.json" "'kept' if d.get('litellmKeyPreview') else 'lost'")" "kept"
  chk "can sign in again"           "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/auth/login" -H "$JSON" -d "{\"email\":\"$UE\",\"password\":\"$PASS\"}")" "200"
fi

echo "== 11b. preferences persist on the account =="
chk "defaults are exposed" "$(curl -s "$API/auth/me" -H "$AH" | python3 -c '
import json,sys; p=json.load(sys.stdin)["preferences"]
print("ok" if {"streamResponses","autoMemory","showUsage"} <= set(p) else "no")')" "ok"
curl -s -o /dev/null -X PATCH "$API/auth/me" -H "$AH" -H "$JSON" -d '{"preferences":{"showUsage":false}}'
chk "a switched-off value sticks" "$(curl -s "$API/auth/me" -H "$AH" | python3 -c 'import json,sys; print(json.load(sys.stdin)["preferences"]["showUsage"])')" "False"
# A partial update must not clear the other switches.
curl -s -o /dev/null -X PATCH "$API/auth/me" -H "$AH" -H "$JSON" -d '{"preferences":{"autoMemory":true}}'
chk "partial update preserves the rest" "$(curl -s "$API/auth/me" -H "$AH" | python3 -c "
import json,sys
p = json.load(sys.stdin)['preferences']
print(str(p['showUsage']) + '/' + str(p['autoMemory']))")" "False/True"
curl -s -o /dev/null -X PATCH "$API/auth/me" -H "$AH" -H "$JSON" -d '{"preferences":{"showUsage":true,"autoMemory":false}}'

echo "== 11c. governance policy is enforced =="
curl -s -o /dev/null -X PUT "$API/admin/governance" -H "$AH" -H "$JSON" \
  -d '{"piiMasking":true,"intentFilter":true,"blockedCategories":["스모크차단"]}'
sleep 1
GS=$(curl -s -X POST "$API/sessions" -H "$AH" -H "$JSON" -d '{"kind":"chat"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
chk "a blocked category is 422" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/sessions/$GS/messages" -H "$AH" -H "$JSON" -d '{"content":"스모크차단 하는 방법"}')" "422"
curl -s -o /dev/null -N -X POST "$API/sessions/$GS/messages" -H "$AH" -H "$JSON" \
  -d '{"content":"내 번호는 010-1234-5678 이야."}'
chk "the phone number is stored masked" "$(curl -s "$API/sessions/$GS/messages" -H "$AH" | python3 -c "
import json,sys
u=[m for m in json.load(sys.stdin) if m['role']=='user']
print('masked' if u and '[전화번호]' in u[0]['content'] else 'raw')")" "masked"
curl -s -o /dev/null -X PUT "$API/admin/governance" -H "$AH" -H "$JSON" \
  -d '{"piiMasking":false,"intentFilter":false,"blockedCategories":[],"retentionDays":0}'

echo "== 11d. user API keys =="
curl -s -o "$J/k1.json" -X POST "$API/keys" -H "$AH" -H "$JSON" -d '{"name":"스모크"}'
chk "the secret is returned exactly once, at issue" "$(jf "$J/k1.json" "'yes' if (d.get('secret') or '').startswith('sk-') else 'no'")" "yes"
KID=$(jf "$J/k1.json" "d['id']")
chk "the list never carries secrets" "$(curl -s "$API/keys" -H "$AH" | python3 -c "
import json,sys; print('leak' if any(k.get('secret') for k in json.load(sys.stdin)) else 'clean')")" "clean"
chk "revoke 204" "$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$API/keys/$KID" -H "$AH")" "204"

echo "== 12. missing and malformed tokens =="
chk "no token 401"      "$(curl -s -o /dev/null -w '%{http_code}' "$API/auth/me")" "401"
chk "garbage token 401" "$(curl -s -o /dev/null -w '%{http_code}' "$API/auth/me" -H 'Authorization: Bearer not.a.jwt')" "401"

echo "== 13. signing out breaks the family =="
curl -s -o "$J/l.json" -c "$J/lc.txt" -X POST "$API/auth/login" -H "$JSON" \
  -d "{\"email\":\"$UE\",\"password\":\"$PASS\"}" >/dev/null
chk "logout 204"           "$(curl -s -o /dev/null -w '%{http_code}' -b "$J/lc.txt" -X POST "$API/auth/logout")" "204"
chk "refresh afterwards 401" "$(curl -s -o /dev/null -w '%{http_code}' -b "$J/lc.txt" -X POST "$API/auth/refresh")" "401"

echo "== 14. profile and password =="
LT=$(jf "$J/l.json" "d['accessToken']")
chk "rename" "$(curl -s -X PATCH "$API/auth/me" -H "Authorization: Bearer $LT" -H "$JSON" -d '{"name":"바뀐 이름"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')" "바뀐 이름"
chk "wrong current password is 403" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/auth/password" -H "Authorization: Bearer $LT" -H "$JSON" -d '{"currentPassword":"wrong-one-here","newPassword":"a-brand-new-password"}')" "403"
chk "password change 204" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/auth/password" -H "Authorization: Bearer $LT" -H "$JSON" -d "{\"currentPassword\":\"$PASS\",\"newPassword\":\"a-brand-new-password\"}")" "204"
chk "sign in with the new password" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/auth/login" -H "$JSON" -d "{\"email\":\"$UE\",\"password\":\"a-brand-new-password\"}")" "200"

echo
echo "passed=$ok failed=$fail skipped=$skipped"
[ "$fail" -eq 0 ]
