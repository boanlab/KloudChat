#!/usr/bin/env bash
# KloudChat integration smoke test — verifies the live stack end to end.
#
#   ./scripts/smoke-test.sh            quick: service health + model registry + chat round-trips
#   ./scripts/smoke-test.sh --full     also drives every functional agent through the LibreChat API
#                                       with demo-derived 시나리오 (US-1..10, docs/*-demo.md) and
#                                       checks native tool execution (ReAct 폴백 아님):
#                                         Super Agent — execute_code / fetch_url / time / web_search /
#                                                       usage / youtube
#                                         Image Studio — generate_image → attachment (flux, free)
#                                         Paper Banana — generate_diagram (paperbanana MCP)
#                                         Slide Studio — 자체완결 HTML 발표자료 :::artifact (도구 없음)
#                                         Deep Research — deep_research MCP + artifact wrapping
#                                         smart_search — 시드 fact → reformulate+hybrid+eval 회수
#                                         Note Taker — 오디오 픽스처 → whisper-shim 전사(STT)
#                                       MCP coverage: fetch_url/time/youtube/usage/paperbanana/
#                                       smart_search(stdio) + deep_research(streamable-http). free/local 이나
#                                       paperbanana(OR 이미지) 와 youtube whisper 폴백은 OR(유료)
#                                       호출 가능. --full 전체는 순차라 ~60분+ 소요.
#
# Reads ports/keys/admin creds from .env. Non-zero exit if any check fails.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

FULL=0
for a in "$@"; do case "$a" in
  --full) FULL=1 ;;
  -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) err "unknown arg: $a"; exit 2 ;;
esac; done

# litellm/librechat are reached on host publish ports. LITELLM_URL in .env may
# point at host.docker.internal (container-only), so use host-local bases here.
LC_URL="http://localhost:8080"
LL_HOST="http://localhost:8000"
KEY="$(env_get LITELLM_MASTER_KEY)"
EMAIL="$(env_get ADMIN_EMAIL)"; PW="$(env_get ADMIN_PW)"
# uaParser middleware rejects requests without a browser User-Agent.
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

PASS=0; FAIL=0
check() { # check "name" <0|1> ["detail"]
  if [[ "$2" == "1" ]]; then ok "$1${3:+ — $3}"; PASS=$((PASS+1));
  else err "$1${3:+ — $3}"; FAIL=$((FAIL+1)); fi
}
http_code() { curl -s -o /dev/null -w '%{http_code}' --max-time 12 -H "User-Agent: $UA" "$@" 2>/dev/null; }

hdr "1. 서비스 헬스"
check "litellm /health/liveliness" "$([[ $(http_code "$LL_HOST/health/liveliness") == 200 ]] && echo 1)"
check "librechat /health"          "$([[ $(http_code "$LC_URL/health") == 200 ]] && echo 1)"
unhealthy="$(docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null | grep -ciE 'unhealthy|restarting' || true)"
check "orchestration 컨테이너 정상(unhealthy 0)" "$([[ "${unhealthy:-0}" == 0 ]] && echo 1)" "unhealthy/restarting=$unhealthy"

hdr "2. LiteLLM 모델 레지스트리"
MODELS="$(curl -s --max-time 12 -H "Authorization: Bearer $KEY" -H "User-Agent: $UA" "$LL_HOST/v1/models" 2>/dev/null)"
has_model() { echo "$MODELS" | python3 -c "import sys,json;print('1' if any(m.get('id')=='$1' for m in (json.load(sys.stdin).get('data') or [])) else '')" 2>/dev/null; }
check "local/gemma-4-26b 등록"  "$(has_model local/gemma-4-26b)"
check "bge-m3 등록"             "$(has_model bge-m3)"
check "local/auto-route 등록(Super Agent)" "$(has_model local/auto-route)"
[[ "$(has_model local/qwen3.5:122b)" == 1 ]] && ok "local/qwen3.5:122b 등록" || warn "local/qwen3.5:122b 미등록 (Deep Research 노드 down?)"
[[ "$(has_model local/qwen3-coder-next)" == 1 ]] && ok "local/qwen3-coder-next 등록" || info "coder-next 미등록 (on-demand — 정상)"

hdr "3. 챗 라운드트립 (LiteLLM)"
chat() { # chat <model> [max_tokens] -> prints "1" if 200 + non-empty output (content OR reasoning).
  # 122b 는 reasoning 모델 — thinking 은 reasoning_content 로 나오는데 strip_reasoning 콜백이
  # 그걸 벗김 → 답변(content)까지 도달해야 비어있지 않음. thinking 에 토큰 다 쓰고 답변에
  # 못 가면 FAIL → reasoning 모델은 max_tokens 넉넉히 줘 답변까지 생성(120s 내).
  local mt="${2:-64}"
  curl -s --max-time 120 -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -H "User-Agent: $UA" \
    -X POST "$LL_HOST/v1/chat/completions" \
    -d "{\"model\":\"$1\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with the single word: ok\"}],\"max_tokens\":$mt,\"temperature\":0}" 2>/dev/null \
  | python3 -c "import sys,json
try:
 m=json.load(sys.stdin)['choices'][0]['message']
 print('1' if (m.get('content') or '').strip() or (m.get('reasoning_content') or '').strip() else '')
except: print('')" 2>/dev/null
}
check "gemma 챗 응답"               "$(chat local/gemma-4-26b)"
check "auto-route(Super Agent) 응답" "$(chat local/auto-route)"
[[ "$(has_model local/qwen3.5:122b)" == 1 ]] && check "122b 챗 응답" "$(chat local/qwen3.5:122b 1024)"

if [[ "$FULL" == 1 ]]; then
  hdr "4. LibreChat 에이전트 + 네이티브 도구 실행 (--full)"
  # python helper: login → list agents → start agent chat (async) → poll messages →
  # report tool_calls / attachments / final text. Free/local tools only.
  HELPER="$(mktemp /tmp/kc_smoke_XXXX.py)"
  # shared auth-token cache so the helper logs in once (not per call) — avoids
  # tripping LibreChat's login rate limiter over the ~60min run.
  export KC_SMOKE_TOKENF="$(mktemp /tmp/kc_smoke_tok_XXXX)"
  cat > "$HELPER" <<'PY'
import json,sys,os,urllib.request,urllib.error,uuid,time
BASE,EMAIL,PW=sys.argv[1],sys.argv[2],sys.argv[3]
AGENT_NAME,PROMPT,EXPECT,MAXW=sys.argv[4],sys.argv[5],sys.argv[6],int(sys.argv[7])
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
def req(path,tok=None,data=None,t=30):
    h={"User-Agent":UA,"Content-Type":"application/json"}
    if tok:h["Authorization"]="Bearer "+tok
    b=json.dumps(data).encode() if data is not None else None
    return json.load(urllib.request.urlopen(urllib.request.Request(BASE+path,b,h),timeout=t))
# Token is cached in KC_SMOKE_TOKENF and reused across every agent_tool call/retry —
# re-logging-in per call trips LibreChat's login rate limiter (≈7/5min). We only
# re-login when a request 401s (token expired, default 15min), so a 60min run logs
# in ~4×, well under the limit.
TOKENF=os.environ.get("KC_SMOKE_TOKENF")
def login():
    for _ in range(3):
        try:
            tok=req("/api/auth/login",data={"email":EMAIL,"password":PW})["token"]
            if TOKENF: open(TOKENF,"w").write(tok)
            return tok
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(20); continue
            raise
    raise RuntimeError("login rate-limited (429×3)")
def token():
    if TOKENF and os.path.exists(TOKENF):
        try:
            t=open(TOKENF).read().strip()
            if t: return t
        except Exception: pass
    return login()
try:
    tok=token()
    try: ags=req("/api/agents",tok)
    except urllib.error.HTTPError as e:
        if e.code in (401,403): tok=login(); ags=req("/api/agents",tok)
        else: raise
    ags=ags.get("data",ags) if isinstance(ags,dict) else ags
    aid=next((a["id"] for a in ags if a.get("name")==AGENT_NAME),None)
    if not aid: print("FAIL|agent not found: "+AGENT_NAME); sys.exit()
    cid=req("/api/agents/chat",tok,{"text":PROMPT,"agent_id":aid,"endpoint":"agents",
        "conversationId":None,"parentMessageId":"00000000-0000-0000-0000-000000000000",
        "messageId":str(uuid.uuid4()),"isCreatedByUser":True})["conversationId"]
    m=None; t0=time.time()
    while time.time()-t0<MAXW:
        try:
            msgs=req("/api/messages/"+cid,tok)
            ai=[x for x in (msgs if isinstance(msgs,list) else []) if not x.get("isCreatedByUser")]
            if ai and not ai[-1].get("unfinished") and (ai[-1].get("content") or ai[-1].get("attachments")):
                m=ai[-1]; break
        except urllib.error.HTTPError as e:
            if e.code in (401,403): tok=login()  # token expired mid-poll → refresh
        except Exception: pass
        time.sleep(5)
    if not m: print("FAIL|timeout (no finished AI message)"); sys.exit()
    tools=[(p.get("tool_call") or {}).get("name") for p in (m.get("content") or []) if p.get("type") in("tool_call","tool_use")]
    texts=" ".join(p.get("text","") if isinstance(p.get("text"),str) else "" for p in (m.get("content") or []))
    atts=m.get("attachments") or []
    # An 'error' content part (e.g. langgraph recursion-limit, MCP unavailable) means the
    # turn failed even if the tool name appears — don't let that pass as a tool execution.
    err=[p.get("error") for p in (m.get("content") or []) if p.get("type")=="error"]
    if EXPECT=="image":
        ok = (bool(atts) or any("generate_image" in (t or "") for t in tools)) and not err
    elif EXPECT=="deck":
        # Slide Studio: 자체완결형 HTML 발표자료를 :::artifact 로 직접 저작(도구 없음).
        ok = (":::artifact" in texts) and ("<section" in texts) and ("</html>" in texts) and not err
    else:
        # MCP tools are namespaced (e.g. detailed_research_mcp_deep_research) → substring match.
        ok = any(EXPECT in (t or "") for t in tools) and not err
    detail=f"tools={tools} att={len(atts)} err={(err[0] if err else '')[:60]!r} text={texts[:40]!r}"
    print(("PASS" if ok else "FAIL")+"|"+detail)
except Exception as e:
    print("FAIL|exception: "+repr(e)[:120])
PY
  agent_tool() { # agent_tool "Agent Name" "prompt" expect_tool max_wait
    # Local models can be flaky at native tool-calling — retry once so a single
    # transient ReAct-fallback doesn't fail the smoke run (a hard break fails both).
    local r=""
    for try in 1 2 3; do
      r="$(python3 "$HELPER" "$LC_URL" "$EMAIL" "$PW" "$1" "$2" "$3" "$4" 2>/dev/null)"
      [[ "${r%%|*}" == PASS ]] && break
    done
    check "[$1] $3 실행" "$([[ "${r%%|*}" == PASS ]] && echo 1)" "${r#*|}"
  }
  # ── demo 시나리오 기반 (docs/*-demo.md). 각 시나리오는 해당 에이전트의 대표 도구를
  #    실제 구동하는지(ReAct 폴백 아님) 검증. 빠른 도구 → 느린 생성 순서.
  #    EXPECT 는 tool_call 이름 substring (MCP 는 <tool>_mcp_<server> 로 네임스페이스).
  #
  # US-1 [Super Agent] execute_code — 통계 평균/표준편차 (trigger.math: 계산은 print())
  agent_tool "Super Agent"   "통계 과제야. 데이터 [12, 15, 9, 20, 18, 14] 의 평균과 표준편차를 계산해줘" execute_code 120
  # US-2 [Super Agent] fetch_url — URL 직독. 모델이 알 수 없는 내용(랜덤 zen 문장)이라 도구 강제.
  agent_tool "Super Agent"   "https://api.github.com/zen 를 열어서 거기 표시되는 한 문장을 그대로 알려줘" fetch_url 150
  # US-3 [Super Agent] time — 현재 시각/타임존 (모델 추측 금지 → 도구 강제)
  agent_tool "Super Agent"   "지금 서울·런던·도쿄 현지 시각을 각각 알려줘" time 90
  # US-4 [Super Agent] web_search — 실시간 웹 검색 (searxng → crawl4ai-shim)
  agent_tool "Super Agent"   "오늘 기준 최신 인공지능 뉴스 한 가지를 웹에서 검색해서 알려줘" web_search 180
  # US-5 [Super Agent] usage — 본인 LiteLLM 사용량/예산 (모델이 알 수 없어 도구 강제, 커스텀 stdio MCP)
  agent_tool "Super Agent"   "이번 달에 내가 토큰을 얼마나 썼고 예산은 얼마나 남았어?" usage 90
  # US-6 [Super Agent] youtube — 영상 자막 추출 후 요약 (자막 있으면 빠름, 없으면 whisper 폴백으로 느림)
  agent_tool "Super Agent"   "이 유튜브 영상 핵심을 요약해줘: https://www.youtube.com/watch?v=dQw4w9WgXcQ" youtube 600
  # US-7 [Image Studio] generate_image — 동아리 포스터 일러스트 (flux-schnell, 무료 로컬)
  agent_tool "Image Studio"  "코딩 동아리 모집 포스터에 쓸 미래적인 일러스트 그려줘" image 300
  # US-8 [Paper Banana] generate_diagram — 논문용 아키텍처 다이어그램 (paperbanana MCP, OR 경유 = 유료)
  agent_tool "Paper Banana"  "Transformer 인코더-디코더 구조를 논문용 아키텍처 다이어그램으로 그려줘" generate_diagram 900
  # US-9 [Slide Studio] 자체완결형 HTML 발표자료를 :::artifact 로 직접 저작 (도구 없음). 122b ~10분.
  agent_tool "Slide Studio"  "'딥러닝 기초' 발표자료를 10장 슬라이드로 만들어줘" deck 900
  # US-10 [Deep Research] deep_research — 학술 다단계 조사 + 인용. 122b(~14 tok/s) ReAct
  # 루프 다회 반복이라 가장 느림 — 캡 60분(느린 노드/긴 sweep 여유).
  agent_tool "Deep Research" "기말 리포트 주제로, LLM 환각(hallucination) 완화 최신 연구를 출처와 함께 정리해줘" deep_research 3600
  rm -f "$HELPER" "$KC_SMOKE_TOKENF"

  hdr "5. 아티팩트 (shim detox + :::artifact 래핑)"
  ART="$(curl -s --max-time 120 -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -H "User-Agent: $UA" \
    -X POST "$LL_HOST/v1/chat/completions" \
    -d "{\"model\":\"local/auto-route\",\"messages\":[{\"role\":\"system\",\"content\":\"The assistant can create and reference artifacts during conversations.\"},{\"role\":\"user\",\"content\":\"간단한 카운터 웹앱 만들어줘\"}],\"max_tokens\":3000,\"temperature\":0.3}" 2>/dev/null \
    | python3 -c "import sys,json;t=json.load(sys.stdin)['choices'][0]['message'].get('content') or '';print('1' if ':::artifact' in t else '')" 2>/dev/null)"
  check "auto-route 아티팩트 → :::artifact 래핑" "$ART"

  hdr "6. smart_search MCP (정밀 문서검색 — reformulate+hybrid+eval)"
  # 알려진 fact 를 테스트 user_id 로 pgvector 에 시드 → 파이프라인 회수 여부 →
  # 정리. 업로드/에이전트 호출 비결정성 회피 → retrieve 품질만 결정적으로 검증.
  PG_USER="$(env_get POSTGRES_USER)"; PG_DB="$(env_get POSTGRES_DB)"
  SS_UID="smoketest-$$"
  SS_FACT="KloudChat 스모크테스트 전용 비밀 식별코드는 ZEBRA-7741 이며 다른 의미는 없다."
  SS_VEC="$(curl -s --max-time 20 -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
    "$LL_HOST/v1/embeddings" -d "{\"model\":\"bge-m3\",\"input\":\"$SS_FACT\"}" 2>/dev/null \
    | python3 -c "import sys,json
try: e=json.load(sys.stdin)['data'][0]['embedding']; print('['+','.join(repr(float(x)) for x in e)+']')
except Exception: print('')" 2>/dev/null)"
  if [[ -n "$SS_VEC" && -n "$PG_USER" ]]; then
    docker exec vectordb psql -U "$PG_USER" -d "$PG_DB" -q -c \
      "INSERT INTO langchain_pg_embedding (uuid, collection_id, embedding, document, cmetadata)
       VALUES (gen_random_uuid(), NULL, '$SS_VEC'::vector, '$SS_FACT',
       jsonb_build_object('user_id','$SS_UID','file_id','smoke','source','smoke','digest','$SS_UID'));" >/dev/null 2>&1
    SS_OUT="$(docker exec -e LIBRECHAT_USER_ID="$SS_UID" LibreChat \
      uv run --quiet --with mcp --with httpx --with 'psycopg[binary]' python -c '
import asyncio, importlib.util
spec=importlib.util.spec_from_file_location("ss","/app/mcp/smart_search.py")
ss=importlib.util.module_from_spec(spec); spec.loader.exec_module(ss)
r=asyncio.run(ss.smart_search("스모크테스트 비밀 식별코드가 뭐야?", top_k=3))
print(" ".join(x["document"] for x in r))' 2>/dev/null)"
    docker exec vectordb psql -U "$PG_USER" -d "$PG_DB" -q -c \
      "DELETE FROM langchain_pg_embedding WHERE cmetadata->>'user_id'='$SS_UID';" >/dev/null 2>&1
    check "smart_search 파이프라인 → 시드 fact 회수" \
      "$([[ "$SS_OUT" == *ZEBRA-7741* ]] && echo 1)" \
      "$([[ "$SS_OUT" == *ZEBRA-7741* ]] && echo 'ZEBRA-7741 회수' || echo MISS)"
  else
    warn "smart_search 스킵 — bge-m3 임베딩 미응답 또는 POSTGRES_USER 미설정"
  fi

  hdr "7. Note Taker STT (오디오 → whisper-shim 전사)"
  # Note Taker 핵심 = 오디오 「텍스트로 업로드」 시 speech.stt→whisper-shim 전사.
  # 런타임에 실제 깨지는 곳은 whisper 백엔드(compute_type/GPU) → 공유 에이전트
  # 상태 안 건드리게 whisper-shim 으로 픽스처 직접 전사해 검증(전사문 비어있지
  # 않으면 통과). LibreChat speech.stt 배선은 부팅 시 config 검증으로 자기검증.
  NT_FIX="${BASH_SOURCE[0]%/*}/fixtures/nt-sample.mp3"
  # whisper-shim 의 네트워크(단일홈) 사용 — comfyui-shim 은 litellm 망까지 multi-home
  # 이라 {{range}} 가 이름 이어붙여 잘못된 network 생성. println+head 로 첫 망만.
  NT_NET="$(docker inspect whisper-shim --format '{{range $k,$v := .NetworkSettings.Networks}}{{println $k}}{{end}}' 2>/dev/null | head -1)"
  if [[ -f "$NT_FIX" && -n "$NT_NET" ]]; then
    NT_OUT="$(docker run --rm --network "$NT_NET" -v "$NT_FIX:/nt.mp3:ro" curlimages/curl:latest \
      -s --max-time 180 -X POST "http://whisper-shim:9000/v1/audio/transcriptions" \
      -F "file=@/nt.mp3;type=audio/mpeg" -F "model=whisper-1" 2>/dev/null)"
    NT_TXT="$(echo "$NT_OUT" | python3 -c "import sys,json
try: print('1' if (json.load(sys.stdin).get('text') or '').strip() else '')
except Exception: print('')" 2>/dev/null)"
    check "Note Taker STT → whisper 전사문 회수" "$NT_TXT" \
      "$([[ -n "$NT_TXT" ]] && echo '전사 OK' || echo "${NT_OUT:0:80}")"
  else
    warn "Note Taker STT 스킵 — 픽스처 또는 docker 네트워크 미확인"
  fi
fi

hdr "결과"
echo "  PASS=$PASS  FAIL=$FAIL"
[[ "$FAIL" == 0 ]] && { ok "smoke test 통과"; exit 0; } || { err "$FAIL 개 실패"; exit 1; }
