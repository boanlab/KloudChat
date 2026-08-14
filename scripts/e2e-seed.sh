#!/usr/bin/env bash
# Seeds the account the Playwright suite signs in as.
#
# The suite needs one *active* account. On an instance that already has an
# administrator, a new signup lands in the pending queue, and emptying the
# database would destroy live data. So this script is non-destructive:
#
#   1) sign in as the real administrator
#   2) sign the e2e account up if it does not exist
#   3) approve it with administrator rights
#
# Safe to run repeatedly.
#
#   ADMIN_EMAIL=you@example.com ADMIN_PASS=… bash scripts/e2e-seed.sh
#
# The seeded workspace objects are Korean because the interface is Korean-first
# and the coverage specs assert against rendered labels.
set -euo pipefail

# shellcheck source=scripts/lib/env.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/env.sh"

API=${API:-http://localhost:8100/api}
ADMIN_EMAIL=${ADMIN_EMAIL:-admin@example.com}
ADMIN_PASS=${ADMIN_PASS:-KloudChat-Admin-1234}
# Must match E2E_ADMIN in apps/web/e2e/helpers.ts.
E2E_EMAIL=${E2E_EMAIL:-e2e-personas@example.com}
E2E_PASS=${E2E_PASS:-personas-playwright-pass}
JSON='Content-Type: application/json'

admin_token=$(curl -s -X POST "$API/auth/login" -H "$JSON" \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("accessToken",""))')

if [ -z "$admin_token" ]; then
  echo "administrator sign-in failed — check ADMIN_EMAIL / ADMIN_PASS" >&2
  exit 1
fi

# Signup returns 409 if the account already exists. Either way, carry on.
curl -s -o /dev/null -X POST "$API/auth/signup" -H "$JSON" \
  -d "{\"email\":\"$E2E_EMAIL\",\"password\":\"$E2E_PASS\",\"name\":\"E2E 관리자\"}" || true

uid=$(curl -s "$API/admin/users" -H "Authorization: Bearer $admin_token" \
  | python3 -c "
import json,sys
print(next((u['id'] for u in json.load(sys.stdin) if u['email']=='$E2E_EMAIL'), ''))")

if [ -z "$uid" ]; then
  echo "e2e account not found" >&2
  exit 1
fi

# Approval is idempotent — an already-active account comes back unchanged.
status=$(curl -s -X POST "$API/admin/users/$uid/approve" \
  -H "Authorization: Bearer $admin_token" -H "$JSON" -d '{"monthlyCredits":2000000}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')

# The persona suite also exercises the admin screens (the pending queue among
# them). Anything that is not the bootstrap account defaults to role `user`, so
# promote it explicitly.
curl -s -o /dev/null -X POST "$API/admin/users/$uid/role" \
  -H "Authorization: Bearer $admin_token" -H "$JSON" -d '{"role":"admin"}'

echo "e2e account ready: $E2E_EMAIL ($status, admin)"

# auth.spec.ts signs in with its own account (e2e-admin@) and walks into the
# app. On anything but an empty database that account is pending too, so
# activate it here as well.
for extra in e2e-admin@example.com; do
  xid=$(curl -s "$API/admin/users" -H "Authorization: Bearer $admin_token" \
    | NEEDLE="$extra" python3 -c '
import json, os, sys
print(next((u["id"] for u in json.load(sys.stdin) if u["email"] == os.environ["NEEDLE"]), ""))')
  if [ -n "$xid" ]; then
    curl -s -o /dev/null -X POST "$API/admin/users/$xid/approve" \
      -H "Authorization: Bearer $admin_token" -H "$JSON" -d '{"monthlyCredits":2000000}'
    # That suite reaches the approval screen too, so it needs admin rights.
    curl -s -o /dev/null -X POST "$API/admin/users/$xid/role" \
      -H "Authorization: Bearer $admin_token" -H "$JSON" -d '{"role":"admin"}'
    echo "  activated $extra (admin)"
  fi
done

# ── workspace seed ──────────────────────────────────────────────────────
# The coverage suite needs *something* on each screen before it can assert
# that an affordance exists. Representative data is inserted through the real
# API rather than the database.
#
# Existence is checked by name. "Skip if there is at least one" would let data
# left over from another test suppress the item this suite actually needs.
tok=$(curl -s -X POST "$API/auth/login" -H "$JSON" \
  -d "{\"email\":\"$E2E_EMAIL\",\"password\":\"$E2E_PASS\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["accessToken"])')
A="Authorization: Bearer $tok"

has_named() {
  curl -s "$API/$1" -H "$A" | NEEDLE="$2" python3 -c '
import json, os, sys
print(any(r.get("name") == os.environ["NEEDLE"] for r in json.load(sys.stdin)))'
}

seed() {  # seed <resource> <name> <json>
  if [ "$(has_named "$1" "$2")" = "False" ]; then
    curl -s -o /dev/null -X POST "$API/$1" -H "$A" -H "$JSON" -d "$3"
    echo "  + $1: $2"
  fi
}

seed projects 학위논문 '{"name":"학위논문","emoji":"📚","description":"박사 학위논문 집필","instructions":"인용은 APA 형식으로. 수치에는 반드시 단위를 붙인다."}'
seed skills 실험\ 로그\ 요약 '{"name":"실험 로그 요약","description":"학습 로그에서 핵심 지표만 뽑는다","whenToUse":"사용자가 학습 로그를 붙여넣고 요약을 요청할 때","body":"1. epoch/loss/metric 열을 찾는다\n2. 최고 성능 지점을 표로 정리한다","kinds":["chat","report"]}'
seed skills 인용\ 형식\ 맞추기 '{"name":"인용 형식 맞추기","description":"출처를 APA 형식으로 정리한다","whenToUse":"보고서에 인용을 넣을 때","body":"저자, 연도, 제목, 출처 순으로 적는다.","kinds":["report","slides"]}'
seed memory 답변\ 길이\ 선호 '{"name":"답변 길이 선호","type":"feedback","description":"짧은 답을 선호","body":"사용자는 서론 없이 결론부터 짧게 답하는 것을 선호한다.","pinned":true}'
seed agents 논문\ 리뷰어 '{"name":"논문 리뷰어","description":"초록과 방법론을 검토한다","model":"local/qwen3.6-27b","systemPrompt":"당신은 논문 리뷰어입니다. 주장과 근거의 연결을 먼저 봅니다.","kinds":["chat","report"]}'

# Connectors are idempotent by slug rather than by name — the server rejects a
# duplicate install.
curl -s -o /dev/null -X POST "$API/connectors/install/time" -H "$A"

echo "workspace seed complete"
