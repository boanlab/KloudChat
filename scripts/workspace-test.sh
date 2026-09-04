#!/usr/bin/env bash
# Workspace API smoke test — projects, files, artifacts, skills, memories,
# agents and connectors.
#
# Requires a running stack and an existing administrator account.
#
#   ADMIN_EMAIL=you@example.com ADMIN_PASS=… bash scripts/workspace-test.sh
#
# Payloads are Korean on purpose: multibyte text through upload extraction,
# prompt assembly and JSON round-tripping.
set -u

# shellcheck source=scripts/lib/env.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/env.sh"

API=${API:-http://localhost:8100/api}
# ADMIN_* as in the other scripts; EMAIL / PASS still accepted.
EMAIL=${ADMIN_EMAIL:-${EMAIL:-admin@example.com}}
PASS=${ADMIN_PASS:-${PASS:-KloudChat-Admin-1234}}
J=$(mktemp -d); ok=0; fail=0
chk() { if [ "$2" = "$3" ]; then echo "  ok   $1 ($2)"; ok=$((ok+1)); else echo "  FAIL $1 — got $2, want $3"; fail=$((fail+1)); fi; }
jq_() { python3 -c "import json,sys;d=json.load(open('$1'));print($2)"; }

TOK=$(curl -s -X POST "$API/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["accessToken"])')
A="Authorization: Bearer $TOK"
JSON='Content-Type: application/json'

echo "== projects =="
curl -s -o "$J/p.json" -w '' -X POST "$API/projects" -H "$A" -H "$JSON" \
  -d '{"name":"스펙트럼 자기지도","description":"라만 스펙트럼 SSL","emoji":"🧪","instructions":"모든 답변에서 단위를 명시하고, 수치에는 출처를 붙인다."}'
PID=$(jq_ "$J/p.json" "d['id']")
chk "create" "$(jq_ "$J/p.json" "d['name']")" "스펙트럼 자기지도"
chk "instructions stored" "$(jq_ "$J/p.json" "'출처' in d['instructions']")" "True"
# Re-runnable: asserts the new project is visible, not a count.
chk "appears in list" "$(curl -s "$API/projects" -H "$A" | python3 -c "import json,sys;print(any(p['id']=='$PID' for p in json.load(sys.stdin)))")" "True"

echo "== file upload and extraction =="
printf 'wavelength_nm,intensity\n532,0.81\n633,0.44\n785,0.12\n' > "$J/spec.csv"
curl -s -o "$J/f.json" -X POST "$API/files" -H "$A" -F "file=@$J/spec.csv" -F "project_id=$PID"
FID=$(jq_ "$J/f.json" "d['id']")
chk "csv extracted"    "$(jq_ "$J/f.json" "'532' in d['preview']")" "True"
chk "token estimate"   "$(jq_ "$J/f.json" "d['tokens']>0")" "True"
chk "no error"         "$(jq_ "$J/f.json" "d['error'] is None")" "True"
printf 'not really a pdf' > "$J/fake.pdf"
curl -s -o "$J/f2.json" -X POST "$API/files" -H "$A" -F "file=@$J/fake.pdf"
# A corrupt upload is stored with its error recorded, not rejected.
chk "corrupt pdf stored with an error" "$(jq_ "$J/f2.json" "d['error'] is not None")" "True"
chk "project file list" "$(curl -s "$API/files?project_id=$PID" -H "$A" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)))')" "1"
chk "download" "$(curl -s -o /dev/null -w '%{http_code}' "$API/files/$FID/content" -H "$A")" "200"

echo "== skills =="
curl -s -o "$J/s.json" -X POST "$API/skills" -H "$A" -H "$JSON" \
  -d '{"name":"단위 검산","description":"수치의 단위를 검산한다","whenToUse":"수치가 포함된 답을 낼 때","body":"1. 모든 수치의 단위를 명시한다\n2. 차원이 맞는지 확인한다","kinds":["chat","report"]}'
SKID=$(jq_ "$J/s.json" "d['id']")
chk "create" "$(jq_ "$J/s.json" "d['enabled']")" "True"
chk "toggle" "$(curl -s -X POST "$API/skills/$SKID/toggle" -H "$A" | python3 -c 'import json,sys;print(json.load(sys.stdin)["enabled"])')" "False"
curl -s -o /dev/null -X POST "$API/skills/$SKID/toggle" -H "$A"

echo "== memories =="
curl -s -o "$J/m.json" -X POST "$API/memory" -H "$A" -H "$JSON" \
  -d '{"name":"소속","description":"사용자 소속","type":"user","body":"예시조직 연구개발팀 소속이다.","pinned":true}'
MID=$(jq_ "$J/m.json" "d['id']")
chk "create"     "$(jq_ "$J/m.json" "d['pinned']")" "True"
chk "pin toggle" "$(curl -s -X POST "$API/memory/$MID/pin" -H "$A" | python3 -c 'import json,sys;print(json.load(sys.stdin)["pinned"])')" "False"
curl -s -o /dev/null -X POST "$API/memory/$MID/pin" -H "$A"

echo "== agents =="
curl -s -o "$J/a.json" -X POST "$API/agents" -H "$A" -H "$JSON" \
  -d '{"name":"검산 도우미","description":"계산을 반드시 검증","model":"local/qwen3.6-27b","systemPrompt":"당신은 계산 검증 전문가입니다. 모든 수치를 execute_code 로 확인합니다.","tools":["execute_code"],"kinds":["chat"]}'
chk "create"          "$(jq_ "$J/a.json" "d['name']")" "검산 도우미"
chk "tool allow-list" "$(jq_ "$J/a.json" "d['tools']")" "['execute_code']"

echo "== artifacts =="
curl -s -o "$J/ar.json" -X POST "$API/artifacts" -H "$A" -H "$JSON" \
  -d '{"kind":"code","title":"피크 검출","data":{"language":"python","content":"print(1)"}}'
ARID=$(jq_ "$J/ar.json" "d['id']")
chk "v1"          "$(jq_ "$J/ar.json" "d['version']")" "1"
chk "edit yields v2" "$(curl -s -X PATCH "$API/artifacts/$ARID" -H "$A" -H "$JSON" -d '{"data":{"language":"python","content":"print(2)"},"summary":"출력 변경"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"])')" "2"

echo "== connectors (MCP) =="
chk "catalogue" "$(curl -s "$API/connectors/catalog" -H "$A" | python3 -c 'import json,sys;print(len(json.load(sys.stdin))>0)')" "True"
curl -s -o "$J/c.json" -X POST "$API/connectors/install/time" -H "$A"
chk "install time"  "$(jq_ "$J/c.json" "d['slug']")" "time"
chk "tools synced"  "$(jq_ "$J/c.json" "len(d['tools'])>0")" "True"
chk "connected"     "$(jq_ "$J/c.json" "d['status']")" "connected"

echo "== isolation =="
UTOK=$(curl -s -X POST "$API/auth/login" -H "$JSON" -d '{"email":"tester@example.com","password":"kchat-test-2026"}' | python3 -c 'import json,sys;print(json.load(sys.stdin).get("accessToken",""))')
if [ -n "$UTOK" ]; then
  # 404 (existence not disclosed) and 403 (account pending) are both "cannot read".
  code=$(curl -s -o /dev/null -w '%{http_code}' "$API/projects/$PID" -H "Authorization: Bearer $UTOK")
  chk "another user cannot read the project" "$(case $code in 403|404) echo blocked;; *) echo "$code";; esac)" "blocked"
fi
chk "anonymous project read is 401" "$(curl -s -o /dev/null -w '%{http_code}' "$API/projects/$PID")" "401"

echo
echo "passed=$ok failed=$fail"
