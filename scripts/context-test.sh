#!/usr/bin/env bash
# Context assembly check (docs/architecture.md §7) — does a workspace setting
# actually reach the answer?
#
# This asserts on *behaviour*, not on storage: every check makes a real model
# call, so it is slow and needs a backend.
#
#   ADMIN_EMAIL=you@example.com ADMIN_PASS=… bash scripts/context-test.sh
#
# Prompts and assertions are Korean because the instance is Korean-first: the
# behaviour under test is whether an instruction written in Korean survives
# prompt assembly and comes back in the answer.
set -u

# shellcheck source=scripts/lib/env.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/env.sh"

API=${API:-http://localhost:8100/api}
# `ADMIN_*` is what smoke-test.sh and e2e-seed.sh take, and having two names for
# one credential across four scripts is how a correct run gets read as a broken
# app — twenty of twenty-two checks "failed" here once, on a login that never
# happened. The old names still work.
EMAIL=${ADMIN_EMAIL:-${EMAIL:-admin@example.com}}
PASS=${ADMIN_PASS:-${PASS:-KloudChat-Admin-1234}}
MODEL=${MODEL:-local/qwen3.6-27b}
J=$(mktemp -d); ok=0; fail=0
chk() { if [ "$2" = "$3" ]; then echo "  ok   $1"; ok=$((ok+1)); else echo "  FAIL $1 — got $2, want $3"; fail=$((fail+1)); fi; }
TOK=$(curl -s -X POST "$API/auth/login" -H 'Content-Type: application/json' -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["accessToken"])')
A="Authorization: Bearer $TOK"; JSON='Content-Type: application/json'
jq_() { python3 -c "import json,sys;d=json.load(open('$1'));print($2)"; }

# Everything this script creates. Left behind, one project, agent, skill and
# memory accumulate per run: the workspace fills with identically named copies,
# and the memory context — capped at 40 entries — starts dropping the ones the
# user actually wrote. So it cleans up after itself.
CREATED=()
track() { CREATED+=("$1"); }
cleanup() {
  for ref in "${CREATED[@]}"; do
    curl -s -o /dev/null -X DELETE "$API/${ref%%:*}/${ref##*:}" -H "$A"
  done
}
trap cleanup EXIT

# Extract just the final answer text from an SSE stream.
say() {
  curl -sN -X POST "$API/sessions/$1/messages" -H "$A" -H "$JSON" -d "$2" \
    | python3 -c "
import json,sys
out=[]; steps=[]
for line in sys.stdin:
    if not line.startswith('data: '): continue
    e=json.loads(line[6:])
    if e['type']=='delta': out.append(e['text'])
    elif e['type']=='step' and e['status']!='running': steps.append(e['label'])
print(json.dumps({'text':''.join(out),'steps':steps}))"
}
mksession() { curl -s -X POST "$API/sessions" -H "$A" -H "$JSON" -d "$1" | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])'; }

echo "== 1. project instructions change the answer =="
PID=$(curl -s -X POST "$API/projects" -H "$A" -H "$JSON" \
  -d '{"name":"단위 엄격","instructions":"모든 답변을 반드시 \"[프로젝트]\" 라는 말머리로 시작하라."}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
track "projects:$PID"
SID=$(mksession "{\"kind\":\"chat\",\"model\":\"$MODEL\",\"projectId\":\"$PID\"}")
say "$SID" '{"content":"안녕하세요"}' > "$J/r1.json"
chk "the instructed prefix is applied" "$(jq_ "$J/r1.json" "'[프로젝트]' in d['text']")" "True"

echo "== 2. memories reach the model =="
track "memory:$(curl -s -X POST "$API/memory" -H "$A" -H "$JSON" \
  -d '{"name":"연구 주제","type":"user","body":"사용자의 연구 주제는 라만 스펙트럼 자기지도 학습이다.","pinned":true}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')"
SID2=$(mksession "{\"kind\":\"chat\",\"model\":\"$MODEL\"}")
say "$SID2" '{"content":"내 연구 주제가 뭐라고 알고 있어? 한 문장으로."}' > "$J/r2.json"
chk "it answers with the remembered fact" "$(jq_ "$J/r2.json" "'라만' in d['text'] or '스펙트럼' in d['text']")" "True"

echo "== 3. skills change the procedure =="
track "skills:$(curl -s -X POST "$API/skills" -H "$A" -H "$JSON" \
  -d '{"name":"삼단 요약","whenToUse":"무언가를 설명할 때","body":"설명은 반드시 정확히 세 줄로, 각 줄을 \"- \" 로 시작해서 쓴다.","kinds":["chat"]}' \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')"
SID3=$(mksession "{\"kind\":\"chat\",\"model\":\"$MODEL\"}")
say "$SID3" '{"content":"광합성을 설명해줘."}' > "$J/r3.json"
chk "the skill's procedure is followed" "$(jq_ "$J/r3.json" "d['text'].count('- ')>=3")" "True"

echo "== 4. the agent system prompt is applied =="
AID=$(curl -s -X POST "$API/agents" -H "$A" -H "$JSON" \
  -d "{\"name\":\"해적\",\"model\":\"$MODEL\",\"systemPrompt\":\"당신은 해적입니다. 모든 문장을 '아르!' 로 끝냅니다.\",\"kinds\":[\"chat\"]}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
track "agents:$AID"
SID4=$(mksession "{\"kind\":\"chat\",\"agentId\":\"$AID\"}")
say "$SID4" '{"content":"안녕?"}' > "$J/r4.json"
chk "the agent persona survives" "$(jq_ "$J/r4.json" "'아르' in d['text']")" "True"

echo "== 5. attachment contents are read =="
printf '실험 A 의 최종 macro-F1 은 0.8317 이다. 이 값은 다른 어디에도 없다.\n' > "$J/secret.txt"
FID=$(curl -s -X POST "$API/files" -H "$A" -F "file=@$J/secret.txt" | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
SID5=$(mksession "{\"kind\":\"chat\",\"model\":\"$MODEL\"}")
say "$SID5" "{\"content\":\"첨부한 파일에서 실험 A 의 macro-F1 값을 그대로 알려줘.\",\"attachments\":[\"$FID\"]}" > "$J/r5.json"
chk "it quotes the attachment" "$(jq_ "$J/r5.json" "'0.8317' in d['text']")" "True"

echo "== 6. an unreadable attachment is admitted to =="
printf 'binary junk' > "$J/bad.pdf"
curl -s -o "$J/bad.json" -X POST "$API/files" -H "$A" -F "file=@$J/bad.pdf"
BID=$(jq_ "$J/bad.json" "d['id']")
SID6=$(mksession "{\"kind\":\"chat\",\"model\":\"$MODEL\"}")
say "$SID6" "{\"content\":\"첨부 파일 내용을 요약해줘.\",\"attachments\":[\"$BID\"]}" > "$J/r6.json"
# Matching exact wording would break every time the model rephrases. The two
# things worth asserting are "it did not invent contents" and "the error is in
# the user's language".
chk "it does not invent contents" "$(jq_ "$J/r6.json" "'binary junk' not in d['text']")" "True"
chk "it says it could not read the file" "$(jq_ "$J/r6.json" "any(w in d['text'] for w in ['없','못','실패','오류','다시'])")" "True"
# A library exception must not surface verbatim ("Stream has ended unexpectedly").
HANGUL=$(python3 - "$J/bad.json" <<'PYEOF'
import json, re, sys
print(bool(re.search(r"[가-힣]", json.load(open(sys.argv[1])).get("error") or "")))
PYEOF
)
chk "the upload error is in Korean" "$HANGUL" "True"

echo "== 7. an MCP connector tool is called =="
curl -s -o /dev/null -X POST "$API/connectors/install/time" -H "$A"
# Install is idempotent and returns the existing row unchanged — including the
# enabled flag a previous run may have cleared — so being enabled has to be
# asserted separately.
CID=$(curl -s "$API/connectors" -H "$A" | python3 -c "
import json,sys
print(next((c['id'] for c in json.load(sys.stdin) if c['slug']=='time'), ''))")
curl -s -o /dev/null -X PATCH "$API/connectors/$CID" -H "$A" -H "$JSON" -d '{"enabled":true}'
SID7=$(mksession "{\"kind\":\"chat\",\"model\":\"$MODEL\"}")
say "$SID7" '{"content":"시간 도구를 써서 지금 서울의 현재 시각을 알려줘."}' > "$J/r7.json"
chk "the MCP tool step is visible" "$(jq_ "$J/r7.json" "any('시간' in s for s in d['steps'])")" "True"

echo "== 7b. the web search toggle actually searches =="
# Broken wiring — not broken code — shows up only here. Forget the compose
# overlay and SEARXNG_URL is empty, the tool drops quietly out of the list, and
# the model just says "web search is not permitted". The UI looks fine.
SID7B=$(mksession "{\"kind\":\"chat\",\"model\":\"$MODEL\"}")
say "$SID7B" '{"content":"오픈소스 LLM 서빙 엔진을 검색해서 알려줘.","webSearch":true}' > "$J/r7b.json"
chk "the search step is visible" "$(jq_ "$J/r7b.json" "any('검색' in s for s in d['steps'])")" "True"
chk "it does not claim it is not permitted" "$(jq_ "$J/r7b.json" "'허용' not in d['text']")" "True"

echo "== 7c. code becomes an artifact =="
# The artifacts screen, the version history and the panel were all built while
# nothing ever created one, so it was permanently empty. Code written in chat
# is what fills that screen.
BEFORE_ART=$(curl -s "$API/artifacts" -H "$A" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)))')
SID7C=$(mksession "{\"kind\":\"chat\",\"model\":\"$MODEL\"}")
say "$SID7C" '{"content":"CSV 를 읽어 열별 평균과 표준편차를 출력하는 파이썬 스크립트를 20줄 이상으로 써줘. 함수로 나누고 예외 처리도 넣어줘."}' > "$J/r7c.json"
AFTER_ART=$(curl -s "$API/artifacts" -H "$A" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)))')
chk "a long code block becomes an artifact" "$([ "$AFTER_ART" -gt "$BEFORE_ART" ] && echo yes || echo no)" "yes"
chk "it is stored as kind=code" "$(curl -s "$API/artifacts" -H "$A" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(d[0]['kind'] if d else 'none')")" "code"

# Promoting short snippets as well would bury the list in fragments.
B2=$(curl -s "$API/artifacts" -H "$A" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)))')
SID7D=$(mksession "{\"kind\":\"chat\",\"model\":\"$MODEL\"}")
say "$SID7D" '{"content":"파이썬에서 리스트를 뒤집는 한 줄 코드만 보여줘. 설명 없이."}' > "$J/r7d.json"
A2=$(curl -s "$API/artifacts" -H "$A" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)))')
chk "a short snippet is not an artifact" "$([ "$A2" -eq "$B2" ] && echo same || echo grew)" "same"

echo "== 7e. a comparison choice persists in the conversation =="
# While the choice lived only in the browser it vanished on reload, and the
# comparison turn's `content` was empty — so the next turn's history contained
# an assistant that had said nothing at all.
SID7E=$(mksession '{"kind":"chat"}')
curl -sN -X POST "$API/sessions/$SID7E/compare" -H "$A" -H "$JSON" \
  -d '{"content":"광합성을 한 문장으로","models":["local/qwen3.6-27b","local/glm-4.7-flash"]}' > /dev/null
curl -s "$API/sessions/$SID7E/messages" -H "$A" > "$J/cmp.json"
chk "the comparison turn's content is not empty" "$(jq_ "$J/cmp.json" "
str(any(m['content'] for m in d if m.get('variants')))")" "True"
chk "exactly one variant is chosen by default" "$(jq_ "$J/cmp.json" "
str(sum(1 for m in d if m.get('variants') for v in m['variants'] if v.get('chosen')))")" "1"
MID=$(jq_ "$J/cmp.json" "next(m['id'] for m in d if m.get('variants'))")
curl -s -o /dev/null -X POST "$API/sessions/$SID7E/messages/$MID/variant" -H "$A" -H "$JSON" \
  -d '{"model":"local/glm-4.7-flash"}'
curl -s "$API/sessions/$SID7E/messages" -H "$A" > "$J/cmp2.json"
chk "changing the choice is stored" "$(jq_ "$J/cmp2.json" "
next(v['model'] for m in d if m.get('variants') for v in m['variants'] if v.get('chosen'))")" "local/glm-4.7-flash"
chk "the body becomes the chosen answer" "$(jq_ "$J/cmp2.json" "
str(next(m['content'] == next(v['content'] for v in m['variants'] if v['chosen']) for m in d if m.get('variants')))")" "True"

echo "== 7f. a report is actually written =="
# This surface was a mock-up for a long time. Checks that outline → per-section
# writing → artifact really runs.
RSID=$(mksession "{\"kind\":\"report\",\"model\":\"$MODEL\"}")
curl -sN -X POST "$API/sessions/$RSID/messages" -H "$A" -H "$JSON" \
  -d '{"content":"전이학습이 소량 데이터에서 효과적인 이유를 다룬 짧은 기술 검토 보고서."}' > "$J/rep.txt"
python3 - "$J/rep.txt" > "$J/rep.json" <<'PYEOF'
import json, sys
secs, art = {}, None
for line in open(sys.argv[1]):
    if not line.startswith('data: '): continue
    e = json.loads(line[6:])
    if e['type'] == 'section': secs[e['sectionId']] = e
    elif e['type'] == 'artifact': art = e['artifactId']
print(json.dumps({'sections': len(secs), 'written': sum(1 for s in secs.values() if s['done'] and s['content']), 'artifact': art}))
PYEOF
chk "at least three sections"    "$(jq_ "$J/rep.json" "d['sections']>=3")" "True"
chk "every section is filled in" "$(jq_ "$J/rep.json" "d['written']==d['sections']")" "True"
chk "a report artifact is created" "$(jq_ "$J/rep.json" "bool(d['artifact'])")" "True"
RAID=$(jq_ "$J/rep.json" "d['artifact']")
for FMT in docx pdf md; do
  SZ=$(curl -s "$API/artifacts/$RAID/export?format=$FMT" -H "$A" -o "$J/out.$FMT" -w '%{size_download}')
  chk "$FMT export" "$([ "$SZ" -gt 2000 ] && echo ok || echo "too small ($SZ)")" "ok"
done
chk "the PDF carries Korean text" "$(python3 -c "
d=open('$J/out.pdf','rb').read()
print('yes' if b'%PDF' == d[:4] and len(d) > 5000 else 'no')")" "yes"

echo "== 8. the agent tool allow-list is enforced =="
LOCK=$(curl -s -X POST "$API/agents" -H "$A" -H "$JSON" \
  -d "{\"name\":\"계산 전용\",\"model\":\"$MODEL\",\"systemPrompt\":\"계산만 한다.\",\"tools\":[\"execute_code\"],\"kinds\":[\"chat\"]}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
track "agents:$LOCK"
SID8=$(mksession "{\"kind\":\"chat\",\"agentId\":\"$LOCK\"}")
say "$SID8" '{"content":"웹에서 오늘 뉴스를 검색해줘.","webSearch":true}' > "$J/r8.json"
chk "a tool outside the allow-list never runs" "$(jq_ "$J/r8.json" "not any('검색' in s for s in d['steps'])")" "True"
chk "it says it has no such tool"              "$(jq_ "$J/r8.json" "any(w in d['text'] for w in ['없','못','불가'])")" "True"

echo
echo "passed=$ok failed=$fail"
