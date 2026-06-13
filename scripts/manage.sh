#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<'EOF'
Usage: manage.sh <resource> <action> [opts]

team   create  --alias <n> [--budget --duration --tpm --rpm --models a,b,c]
       list / delete --id <team_id>
       sync                                                ← 카탈로그 변경 후 모든 팀 model allowlist 재동기화

user   create  --id <email> [--team <alias>] [--budget]
               [--name <n> --username <u> --password <p>]   ← LibreChat 풀 프로비저닝
               [--admin]                                    ← 생성 후 role=ADMIN 부여
       list / delete --id <email>
       usage  [--user <email>]                             ← 사용자별 사용량(spend) vs 월 예산
       topup  --user <email> --amount <N>                  ← 월 한도 일시 상향($N; spend 유지 → 통계 정확, 다음달 자동 원복)

key    issue   --user <email> [--team] [--alias] [--budget]
       issue   --service <name> [--budget]                  ← service-account
       list   [--user <email>]                             ← LiteLLM 기준, 키 앞 20자만
       show   [--user <email>]                             ← 로컬 원장의 전체 평문 키
       revoke --key <sk-...>

agent  sync                                                ← 카탈로그 변경 후 모든 유저 에이전트 upsert
EOF
  exit 1
}

require_arg() { [[ -n "$2" ]] || { err "$1 required"; exit 1; }; }
require_email() {
  [[ "$2" =~ ^[^@]+@[^@]+\.[^@]+$ ]] || { err "$1 must be email: $2"; exit 1; }
}
# flag 뒤 값 누락 시 set -u 안전 종료. 사용: `--foo) need_val "$@"; foo="$2"; shift 2 ;;`
need_val() { [[ -n "${2:-}" ]] || { err "$1 requires a value"; exit 1; }; }

cmd_team_create() {
  local alias="" budget=9999 duration=1mo tpm=100000 rpm=500
  local models; models="$(litellm_chat_models_csv)"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --alias)    need_val "$@"; alias="$2";    shift 2 ;;
      --budget)   need_val "$@"; budget="$2";   shift 2 ;;
      --duration) need_val "$@"; duration="$2"; shift 2 ;;
      --tpm)      need_val "$@"; tpm="$2";      shift 2 ;;
      --rpm)      need_val "$@"; rpm="$2";      shift 2 ;;
      --models)   need_val "$@"; models="$2";   shift 2 ;;
      *) err "Unknown: $1"; exit 1 ;;
    esac
  done
  require_arg --alias "$alias"
  [[ -z "$models" ]] && { err "litellm chat 모델 목록이 비었습니다 — OPENROUTER_API_KEY 또는 vLLM 노드 필요 (--models 로 직접 지정 가능)"; exit 1; }

  local existing; existing=$(team_id_by_alias "$alias")
  if [[ -n "$existing" ]]; then
    echo "team exists: $alias (id=$existing)"; echo "$existing"; return 0
  fi

  local models_json; models_json=$(echo "$models" | tr ',' '\n' | jq -R . | jq -s .)
  local payload; payload=$(jq -n \
    --arg a "$alias" --argjson b "$budget" --arg d "$duration" \
    --argjson tpm "$tpm" --argjson rpm "$rpm" --argjson m "$models_json" \
    '{team_alias:$a, max_budget:$b, budget_duration:$d, tpm_limit:$tpm, rpm_limit:$rpm, models:$m}')
  local result; result=$(litellm_post "/team/new" "$payload")
  local team_id; team_id=$(echo "$result" | jq -r '.team_id')

  mkdir -p "${DATA_DIR}"
  local cache="${DATA_DIR}/teams.json"
  if [[ -f "$cache" ]]; then
    if jq --argjson r "$result" '. += [$r]' "$cache" > "${cache}.tmp"; then
      mv "${cache}.tmp" "$cache"
    else
      rm -f "${cache}.tmp"
      warn "teams.json 업데이트 실패 — jq 에러"
    fi
  else
    echo "[$result]" > "$cache"
  fi
  echo "team created: $alias (id=$team_id)"
  echo "$team_id"
}

cmd_team_list() {
  litellm_get "/team/list" | jq -r '.[] | "\(.team_id)\t\(.team_alias)\tbudget:\(.max_budget)$\tmodels:\(.models // [] | join(","))"'
}

cmd_team_delete() {
  local id=""
  while [[ $# -gt 0 ]]; do case "$1" in --id) need_val "$@"; id="$2"; shift 2 ;; *) shift ;; esac; done
  require_arg --id "$id"
  litellm_post "/team/delete" "{\"team_ids\":[\"$id\"]}" | jq .
}

# 전 팀의 model allowlist 를 현재 카탈로그로 동기화. lib.sh 카탈로그 변경 후
# 미실행 시 기존 팀이 새 모델을 401 거부.
cmd_team_sync() {
  local models; models="$(litellm_chat_models_csv)"
  [[ -z "$models" ]] && { err "litellm chat 모델 0건 — gen-litellm-config + restart 먼저"; exit 1; }
  local models_json; models_json=$(echo "$models" | tr ',' '\n' | jq -R . | jq -s .)
  local teams; teams=$(litellm_get "/team/list")
  local n=0 n_ok=0 n_fail=0
  while IFS=$'\t' read -r tid talias; do
    [[ -z "$tid" ]] && continue
    n=$((n+1))
    local payload; payload=$(jq -n --arg id "$tid" --argjson m "$models_json" '{team_id:$id, models:$m}')
    if litellm_post "/team/update" "$payload" >/dev/null 2>&1; then
      n_ok=$((n_ok+1)); ok "$talias ($tid)"
    else
      n_fail=$((n_fail+1)); err "$talias ($tid)"
    fi
  done < <(echo "$teams" | jq -r '.[] | "\(.team_id)\t\(.team_alias)"')
  echo "team/sync: $n_ok/$n updated, $n_fail failed (models: $(echo "$models" | tr ',' '\n' | wc -l))"
}

cmd_user_create() {
  local user_id="" team_alias="default" budget=9999 is_admin=0
  local lc_name="" lc_username="" lc_password=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --id)       need_val "$@"; user_id="$2";     shift 2 ;;
      --team)     need_val "$@"; team_alias="$2";  shift 2 ;;
      --budget)   need_val "$@"; budget="$2";      shift 2 ;;
      --name)     need_val "$@"; lc_name="$2";     shift 2 ;;
      --username) need_val "$@"; lc_username="$2"; shift 2 ;;
      --password) need_val "$@"; lc_password="$2"; shift 2 ;;
      --admin)    is_admin=1;                      shift ;;
      *) err "Unknown: $1"; exit 1 ;;
    esac
  done
  require_arg --id "$user_id"
  require_email --id "$user_id"

  local lc_count=0
  for v in "$lc_name" "$lc_username" "$lc_password"; do [[ -n "$v" ]] && lc_count=$((lc_count+1)); done
  (( lc_count != 0 && lc_count != 3 )) && { err "--name/--username/--password 는 함께 지정."; exit 1; }
  local full=$(( lc_count == 3 ))
  # --admin 은 LibreChat 사용자 레코드(role 갱신 대상) 있어야 의미.
  (( is_admin && ! full )) && { err "--admin 은 --name/--username/--password 와 함께 지정."; exit 1; }

  if (( full )); then
    if docker_on_node NODE_LIBRECHAT ps --format '{{.Names}}' 2>/dev/null | grep -q '^LibreChat$'; then
      local out
      out=$(docker_on_node NODE_LIBRECHAT exec -i LibreChat npm run create-user -- "$user_id" "$lc_name" "$lc_username" "$lc_password" <<< 'y' 2>&1 | tail -10)
      if echo "$out" | grep -q "User created successfully"; then
        ok "LibreChat user: $user_id ($lc_username)"
      else
        warn "LibreChat user 생성 실패 (이미 존재 가능):"
        echo "$out" | tail -3 | sed 's/^/    /' >&2
      fi
    else
      warn "LibreChat 미실행 (NODE_LIBRECHAT 도달 불가 또는 컨테이너 없음) — 건너뜀"
    fi

    # --admin: 방금 만든(혹은 기존) LibreChat 사용자 role 을 ADMIN 으로. create-user 가
    # 이미-존재로 실패했어도 멱등 적용 (agent sync 등이 ADMIN owner 요구).
    if (( is_admin )); then
      local mu mp email_js r
      mu=$(env_get MONGO_ROOT_USER); mp=$(env_get MONGO_ROOT_PASSWORD)
      if [[ -z "$mu" || -z "$mp" ]]; then
        warn "MONGO_ROOT_USER/PASSWORD 누락 — ADMIN role 부여 건너뜀"
      else
        email_js=$(jq -Rn --arg s "$user_id" '$s')
        r=$(docker_on_node NODE_LIBRECHAT exec chat-mongodb mongosh --quiet \
          -u "$mu" -p "$mp" --authenticationDatabase admin LibreChat --eval "
            var u = db.users.updateOne({email: $email_js}, {\$set:{role:'ADMIN'}});
            print(u.matchedCount ? 'OK' : 'NO_USER');" 2>/dev/null | tail -1)
        [[ "$r" == OK ]] && ok "ADMIN role 부여: $user_id" \
          || warn "ADMIN role 부여 실패 ($r) — LibreChat 사용자 미존재 가능"
      fi
    fi
  fi

  # user-레벨 max_budget 도 --budget 으로 명시 설정. 안 하면 litellm_settings 의
  # max_internal_user_budget(기본 $25) 적용, LiteLLM 은 min(user, key, team) 을
  # 한도로 사용 → --budget 이 $25 초과 시 $25 에 잘못 캡됨. /user/new 는 기존 유저면
  # no-op → 멱등 갱신 위해 /user/update 도 한 번 호출.
  local payload; payload=$(jq -n --arg u "$user_id" --argjson b "$budget" \
    '{user_id:$u, user_role:"internal_user", max_budget:$b, budget_duration:"1mo"}')
  litellm_post "/user/new" "$payload" >/dev/null 2>&1 || true
  litellm_post "/user/update" "$(jq -n --arg u "$user_id" --argjson b "$budget" \
    '{user_id:$u, max_budget:$b, budget_duration:"1mo"}')" >/dev/null 2>&1 || true
  echo "user: $user_id (\$${budget}/mo)"

  if [[ -n "$team_alias" ]]; then
    local team_id; team_id=$(team_id_by_alias "$team_alias")
    [[ -z "$team_id" ]] && { err "team not found: $team_alias"; exit 1; }
    payload=$(jq -n --arg tid "$team_id" --arg uid "$user_id" --argjson b "$budget" \
      '{team_id:$tid, max_budget_in_team:$b, member:{role:"user", user_id:$uid}}')
    litellm_post "/team/member_add" "$payload" >/dev/null
    echo "team: $team_alias (\$${budget}/mo)"
  fi

  if (( full )); then
    local out issued
    out=$(cmd_key_issue --user "$user_id" --team "$team_alias" --alias "${lc_username}-key" --budget "$budget" 2>&1)
    echo "$out"
    issued=$(echo "$out" | grep -oE 'sk-[A-Za-z0-9_-]+' | head -1)
    [[ -n "$issued" ]] && register_litellm_key_for_librechat_user "$user_id" "$lc_password" "$issued"
    # 에이전트는 ADMIN 글로벌 공유본 사용 → 사용자 생성 시 별도 작업 없음.
    # 카탈로그 부트스트랩/재공유는 setup.sh 의 'agent sync' 1회로 충분,
    # 카탈로그 변경 시에만 'manage.sh agent sync' 수동 재실행.
  fi
}

register_litellm_key_for_librechat_user() {
  local email="$1" pw="$2" key="$3"
  # LIBRECHAT_URL 은 lib.sh 가 .env / NODE_LIBRECHAT 호스트 기준으로 자동 유추.
  local lc_url="$LIBRECHAT_URL"
  local jwt
  jwt=$(curl -s -X POST "${lc_url}/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg e "$email" --arg p "$pw" '{email:$e, password:$p}')" \
    2>/dev/null | jq -r '.token // ""')
  [[ -z "$jwt" ]] && { warn "LibreChat login 실패 — 키 수동 입력 필요"; return 0; }

  local val_json; val_json=$(jq -n --arg k "$key" '{apiKey:$k}' | jq -c .)
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${lc_url}/api/keys" \
    -H "Authorization: Bearer $jwt" -H 'Content-Type: application/json' \
    -d "$(jq -n --arg n LiteLLM --arg v "$val_json" '{name:$n, value:$v}')")
  [[ "$code" == "201" ]] && ok "LiteLLM 키 LibreChat 자동 등록" \
    || warn "키 자동 등록 실패 (HTTP $code)"
}

# ADMIN 한 사용자(email) 소유로 공유 에이전트 카탈로그 upsert. 모델별 일반
# 에이전트 + functional 에이전트(Super/Image/Slide/Note Taker/Deep Research/Video/Paper Banana)
# 생성·갱신, 카테고리 매핑, 잔존 defaultPreset 제거. cmd_agent_sync 가 ADMIN 에게
# 1회 호출 → 이후 전 사용자에게 read-only 공유. (사용자별 복제 아님.)
upsert_shared_agent_catalog() {
  local email="$1"
  local mu mp; mu=$(env_get MONGO_ROOT_USER); mp=$(env_get MONGO_ROOT_PASSWORD)
  [[ -z "$mu" || -z "$mp" ]] && { warn "MONGO_ROOT_USER/PASSWORD 누락 — agent 건너뜀"; return 0; }
  docker_on_node NODE_LIBRECHAT ps --format '{{.Names}}' 2>/dev/null | grep -q '^chat-mongodb$' \
    || { warn "chat-mongodb 미실행 (NODE_LIBRECHAT 도달 불가) — agent 건너뜀"; return 0; }

  # 일반 agent 이름 = 모델 ID (`openai/gpt-5-mini`, `local/gemma-4-26b` 등) — dropdown 이
  # LiteLLM 라우트와 1:1. functional agent 7종 (Super Agent / Image Studio / Video Studio /
  # Slide Studio / Note Taker / Deep Research / Paper Banana) 만 별도 이름으로 도구 셋 분기.
  local agent_specs='[]'
  local m

  # 에이전트 표시이름 = 각 모델 정식 명칭(대소문자). spec.name / DISPLAY_ORDER / rename
  # 매핑이 공통 참조. 미등록 키는 slug 그대로 폴백.
  local -A AGENT_DISPLAY=(
    [openai/gpt-5.5]="GPT-5.5" [openai/gpt-5]="GPT-5" [openai/gpt-5-mini]="GPT-5 mini" [openai/gpt-5-nano]="GPT-5 nano"
    [anthropic/claude-opus-4.8]="Claude Opus 4.8" [anthropic/claude-opus-4.7]="Claude Opus 4.7" [anthropic/claude-opus-4.6]="Claude Opus 4.6"
    [anthropic/claude-sonnet-4.6]="Claude Sonnet 4.6" [anthropic/claude-haiku-4.5]="Claude Haiku 4.5"
    [google/gemini-3.1-pro-preview]="Gemini 3.1 Pro" [google/gemini-2.5-pro]="Gemini 2.5 Pro" [google/gemini-2.5-flash]="Gemini 2.5 Flash"
    [google/gemma-4-31b-it]="Gemma 4 31B" [deepseek/deepseek-v4-pro]="DeepSeek V4 Pro" [x-ai/grok-4.3]="Grok 4.3"
    [perplexity/sonar]="Perplexity Sonar" [meta/llama-4-maverick]="Llama 4 Maverick" [qwen/qwen3.5-397b-a17b]="Qwen3.5 397B A17B"
    [nano-banana]="Nano Banana" [nano-banana-2]="Nano Banana 2" [gpt-image-2]="GPT Image 2"
    [veo-lite]="Veo 3.1 Lite" [veo-fast]="Veo 3.1 Fast" [veo]="Veo 3.1" [sora-2]="Sora 2 Pro"
  )
  disp_name() { echo "${AGENT_DISPLAY[$1]:-$1}"; }

  # external commercial — OpenRouter 키 필수. kind default. desc 에 토큰 단가 노출.
  if has_openrouter; then
    _or_chat_desc() { printf 'OpenRouter 상용 LLM · 입력 $%s / 출력 $%s (per 1M tokens)' "${MODEL_PRICE_IN_PM[$1]:-?}" "${MODEL_PRICE_OUT_PM[$1]:-?}"; }
    _add_chat() {  # $1=provider  $2..=model id
      local prov="$1" m; shift
      for m in "$@"; do
        agent_specs=$(jq -c --arg n "$(disp_name "$prov/$m")" --arg mm "$prov/$m" --arg d "$(_or_chat_desc "$m")" \
          '. + [{name:$n, model:$mm, kind:"default", is_promoted:false, desc:$d}]' <<< "$agent_specs")
      done
    }
    _add_chat openai     "${OPENAI_MODELS[@]}"
    _add_chat anthropic  "${ANTHROPIC_MODELS[@]}"
    _add_chat google     "${GOOGLE_MODELS[@]}"
    _add_chat deepseek   "${DEEPSEEK_MODELS[@]}"
    _add_chat x-ai       "${XAI_MODELS[@]}"
    _add_chat perplexity "${PERPLEXITY_MODELS[@]}"
    _add_chat meta       "${META_MODELS[@]}"
    _add_chat qwen       "${QWEN_MODELS[@]}"

    # 유료 이미지/비디오 모델별 agent — 브레인이 122b(Studio 와 동일) → 로컬 챗
    # 두뇌 있어야 동작. 따라서 super_agent_eligible(VLLM_GEMMA26_URL) AND OR키 둘 다
    # 있을 때만 생성 — GPU 없는 OR 전용 배포는 브레인 없음 → 미생성(깨진 에이전트
    # 노출 방지). kind 로 도구 부착, lockModel 로 생성 모델 고정(instructions 강제).
    if super_agent_eligible; then
      # 두뇌 = 122b: gemma 는 복잡 프롬프트에서 generate_image/video 툴콜 신뢰도가 낮음
      # (ReAct-JSON 텍스트 누수), 122b 는 안정적. 일관성 위해 image/video 모두 122b.
      # (둘 다 vision 지원 → 이미지 입력/편집 보존). 트레이드오프는 122b reasoning 으로 약간
      # 느린 것뿐 — 이미지/영상 생성 시간 대비 무시 가능.
      _add_gen() {  # $1=kind(image|video)  $2=alias  $3=desc
        agent_specs=$(jq -c --arg n "$(disp_name "$2")" --arg mm "local/qwen3.5:122b" --arg k "$1" --arg lm "$2" --arg d "$3" \
          '. + [{name:$n, model:$mm, kind:$k, is_promoted:false, lockModel:$lm, desc:$d}]' <<< "$agent_specs")
      }
      _add_gen image nano-banana   "OpenRouter 상용 이미지(Gemini 2.5 Flash Image) · 약 \$0.04/장"
      _add_gen image nano-banana-2 "OpenRouter 상용 이미지(Gemini 3.1 Flash Image) · 약 \$0.05/장"
      _add_gen image gpt-image-2   "OpenRouter 상용 이미지(GPT Image 2) · 약 \$0.1–0.3/장"
      _add_gen video veo-lite      "OpenRouter 상용 비디오(Google Veo 3.1 Lite) · 약 \$0.08/초~"
      _add_gen video veo-fast      "OpenRouter 상용 비디오(Google Veo 3.1 Fast) · 약 \$0.12/초~"
      _add_gen video veo           "OpenRouter 상용 비디오(Google Veo 3.1) · 약 \$0.40/초~"
      _add_gen video sora-2        "OpenRouter 상용 비디오(OpenAI Sora 2 Pro) · 약 \$0.50/초~"
    fi
  fi

  # plain `local/<m>` default agent 미생성 — gemma-4-26b 는 아래 functional
  # agent (Super Agent / Image Studio / Slide Studio) 의 base, coder-next 는 외부 코딩
  # 클라이언트(Claude Code/Codex) 전용 → LiteLLM 등록만 유지, UI/agent 에는 미노출.

  # Super Agent — 챗 두뇌(gemma-4-26b) 가용 시 promoted default (single-stage).
  # 같은 조건에서 functional 에이전트(Image/Slide/Note Taker/Deep Research/Video/Paper
  # Banana)도 함께 등장. Super/Image/Video/Slide/Note Taker/Deep Research = promoted
  # (AI Agent Store Top Picks), Paper Banana 만 비promoted. 새 대화 기본 = Super Agent.
  if super_agent_eligible; then
    agent_specs=$(jq -c --arg n "Super Agent" --arg mm "local/auto-route" \
      '. + [{name:$n, model:$mm, kind:"super", is_promoted:true}]' <<< "$agent_specs")
    # Image/Video Studio — 이미지/비디오 백엔드(로컬 ComfyUI 또는 OR 외부) 있어야 등장.
    # GPU 없으면 comfyui-shim 이 외부 OR 모델(nano-banana/veo-lite 등)로 라우팅(유료).
    if image_gen_eligible; then
      # Image/Video Studio 두뇌 = 122b: gemma 의 generate_image/video 툴콜 신뢰도 낮음,
      # 122b 는 안정적. vision 유지(122b 도 멀티모달). 일관성 위해 둘 다 122b.
      agent_specs=$(jq -c --arg n "Image Studio" --arg mm "local/qwen3.5:122b" \
        '. + [{name:$n, model:$mm, kind:"image", is_promoted:true}]' <<< "$agent_specs")
      agent_specs=$(jq -c --arg n "Video Studio" --arg mm "local/qwen3.5:122b" \
        '. + [{name:$n, model:$mm, kind:"video", is_promoted:true}]' <<< "$agent_specs")
    fi
    # Deep Research = 122b (추론 깊이 우선). plain alias (local/qwen3.5:122b) 를 backend 로
    # 사용 — 단일 122b deployment → 모든 reasoning step (짧은 tool-routing 호출 ~ 누적 input
    # 호출) 이 같은 노드의 같은 ctx budget 공유 → max_completion_tokens/ctx reject 없음.
    agent_specs=$(jq -c --arg n "Deep Research" --arg mm "local/qwen3.5:122b" \
      '. + [{name:$n, model:$mm, kind:"research", is_promoted:true}]' <<< "$agent_specs")
    # Slide Studio — 122b 가 전문 발표 디자이너 프롬프트(Architect→Designer)로 자체완결형
    # HTML 발표자료(인라인 CSS/JS/SVG, 16:9 네비)를 아티팩트로 직접 저작. 도구 없음
    # (instructionsFor/builtinFor/mcpToolsFor 의 ppt 분기 참조).
    agent_specs=$(jq -c --arg n "Slide Studio" --arg mm "local/qwen3.5:122b" \
      '. + [{name:$n, model:$mm, kind:"ppt", is_promoted:true}]' <<< "$agent_specs")
    # Paper Banana — gemma-4-26b 가 paperbanana MCP(학술 다이어그램/플롯 생성, OpenRouter
    # 경유 VLM+이미지) 호출. Research 카테고리. PaperBanana 프레임워크가 multi-agent
    # render 전담 → 에이전트는 도구 호출만. is_promoted:false — 스토어 Research
    # 카테고리엔 남기되 Top Picks(featured)에선 제외.
    agent_specs=$(jq -c --arg n "Paper Banana" --arg mm "local/gemma-4-26b" \
      '. + [{name:$n, model:$mm, kind:"paperbanana", is_promoted:false}]' <<< "$agent_specs")
    # Note Taker — 오디오 "텍스트로 업로드" 시 내장 STT(→whisper-shim)가 전사해 컨텍스트로
    # 첨부, gemma 가 회의록/강의노트로 정리. 전사 백엔드(whisper)는 GPU 전용(OR 폴백 없음)
    # → whisper_eligible 일 때만 등장 — 없으면 코어 기능(전사) 불가 → 미생성.
    if whisper_eligible; then
      agent_specs=$(jq -c --arg n "Note Taker" --arg mm "local/gemma-4-26b" \
        '. + [{name:$n, model:$mm, kind:"notetaker", is_promoted:true}]' <<< "$agent_specs")
    fi
  fi

  local email_js; email_js=$(jq -Rn --arg e "$email" '$e')

  # appendix.txt 의 섹션별 본문 추출 (`=== name ===` sentinel) — agent kind / 부착 도구 에
  # 따라 *해당 섹션만* 조립해서 instructions 끝에 append (모든 agent 에 전체 부착 시
  # 불필요한 가이드가 instruction 을 부풀려 attention 분산).
  local appendix_path="${SCRIPT_DIR}/agent-instructions-appendix.txt"
  __appendix_section() {
    [[ -f "$appendix_path" ]] || return 0
    awk -v name="$1" '
      /^=== / { in_sec = ($0 == "=== " name " ==="); next }
      in_sec { print }
    ' "$appendix_path" | awk 'NF || started { started=1; print }' | sed -E '$ { /^$/d }'
  }
  local APX_HONESTY APX_EXEC APX_RESEARCH APX_IMAGE
  APX_HONESTY=$( jq -Rn --arg s "$(__appendix_section KLOUDCHAT_TOOL_HONESTY)"     '$s')
  APX_EXEC=$(    jq -Rn --arg s "$(__appendix_section KLOUDCHAT_EXECUTE_CODE_RULES)" '$s')
  APX_RESEARCH=$(jq -Rn --arg s "$(__appendix_section KLOUDCHAT_DEEP_RESEARCH)"    '$s')
  APX_IMAGE=$(   jq -Rn --arg s "$(__appendix_section KLOUDCHAT_IMAGE_GEN)"        '$s')

  # routing/instructions.md = agent instructions 의 single source of truth. 섹션
  # sentinel `== name ==` 으로 분리된 본문을 awk 로 추출 → jq 로 JS literal 변환.
  local routing_path="${PROJECT_DIR}/routing/instructions.md"
  [[ -f "$routing_path" ]] || { err "routing/instructions.md 누락"; return 1; }
  __routing_section() {
    awk -v name="$1" '
      /^== / { in_sec = ($0 == "== " name " =="); next }
      in_sec { print }
    ' "$routing_path" | awk 'NF || started { started=1; print }' | sed -E '$ { /^$/d }'
  }
  local SEC_IMG_H SEC_IMG_B SEC_DEF_H SEC_RES_H SEC_RES_B SEC_PPT SEC_NOTE SEC_VID
  local SEC_LANG SEC_TOOL SEC_YT SEC_MATH
  SEC_IMG_H=$(jq -Rn --arg s "$(__routing_section agent.image.header)"   '$s')
  SEC_IMG_B=$(jq -Rn --arg s "$(__routing_section agent.image.body)"     '$s')
  SEC_VID=$(  jq -Rn --arg s "$(__routing_section agent.video)"          '$s')
  SEC_DEF_H=$(jq -Rn --arg s "$(__routing_section agent.default.header)" '$s')
  SEC_RES_H=$(jq -Rn --arg s "$(__routing_section agent.research.header)" '$s')
  SEC_RES_B=$(jq -Rn --arg s "$(__routing_section agent.research.body)"  '$s')
  SEC_PPT=$(  jq -Rn --arg s "$(__routing_section agent.ppt)"            '$s')
  SEC_NOTE=$( jq -Rn --arg s "$(__routing_section agent.notetaker)"      '$s')
  SEC_LANG=$( jq -Rn --arg s "$(__routing_section policy.language)"       '$s')
  SEC_TOOL=$( jq -Rn --arg s "$(__routing_section policy.tool_calling)"   '$s')
  SEC_YT=$(   jq -Rn --arg s "$(__routing_section trigger.youtube)"       '$s')
  SEC_MATH=$( jq -Rn --arg s "$(__routing_section trigger.math)"          '$s')

  # AI Agent Store/드롭다운 표시 순서 (index 0 = 상단). 마켓 카테고리·드롭다운 둘 다
  # {updatedAt:-1} 정렬 → 이 한 배열이 양쪽 결정(Productivity = Image→Video→
  # Slide→Note Taker, Research = Deep Research→Paper Banana):
  #   Super Agent → Image Studio → Video Studio → Slide Studio → Note Taker →
  #   Deep Research → Paper Banana → openai(오름차순) → anthropic → google.
  # 상용 모델 배열은 내림차순(고성능 우선) 유지 → 역순으로 펼쳐 오름차순 생성.
  # Productivity studios → 상용 LLM(provider 그룹: openai→anthropic→google→그외) →
  # 모델별 이미지/비디오 agent(Closed Models 의 그외 꼬리) 순.
  # 표시이름(disp_name)으로 — rename 후에도 rank 유지.
  local _disp=("Super Agent" "Image Studio" "Video Studio" "Slide Studio" "Note Taker" "Deep Research" "Paper Banana") _i _p
  for ((_i=${#OPENAI_MODELS[@]}-1; _i>=0; _i--));     do _disp+=("$(disp_name "openai/${OPENAI_MODELS[$_i]}")"); done
  for ((_i=${#ANTHROPIC_MODELS[@]}-1; _i>=0; _i--));  do _disp+=("$(disp_name "anthropic/${ANTHROPIC_MODELS[$_i]}")"); done
  for ((_i=${#GOOGLE_MODELS[@]}-1; _i>=0; _i--));     do _disp+=("$(disp_name "google/${GOOGLE_MODELS[$_i]}")"); done
  for ((_i=${#DEEPSEEK_MODELS[@]}-1; _i>=0; _i--));   do _disp+=("$(disp_name "deepseek/${DEEPSEEK_MODELS[$_i]}")"); done
  for ((_i=${#XAI_MODELS[@]}-1; _i>=0; _i--));        do _disp+=("$(disp_name "x-ai/${XAI_MODELS[$_i]}")"); done
  for ((_i=${#PERPLEXITY_MODELS[@]}-1; _i>=0; _i--)); do _disp+=("$(disp_name "perplexity/${PERPLEXITY_MODELS[$_i]}")"); done
  for ((_i=${#META_MODELS[@]}-1; _i>=0; _i--));       do _disp+=("$(disp_name "meta/${META_MODELS[$_i]}")"); done
  for ((_i=${#QWEN_MODELS[@]}-1; _i>=0; _i--));       do _disp+=("$(disp_name "qwen/${QWEN_MODELS[$_i]}")"); done
  for _p in nano-banana nano-banana-2 gpt-image-2 veo veo-fast veo-lite sora-2; do _disp+=("$(disp_name "$_p")"); done
  local display_order_json; display_order_json=$(printf '%s\n' "${_disp[@]}" | jq -R . | jq -cs .)
  local whisper_on; whisper_eligible && whisper_on=true || whisper_on=false  # youtube 도구 게이팅(JS WHISPER_ON)

  local result
  result=$(docker_on_node NODE_LIBRECHAT exec chat-mongodb mongosh --quiet \
    -u "$mu" -p "$mp" --authenticationDatabase admin LibreChat --eval "
      var u = db.users.findOne({email: $email_js}, {_id:1});
      if (!u) { print('NO_USER'); quit(); }
      var now = new Date();
      // appendix 섹션 본문 (조건부 조립). agent kind / 부착 도구 따라 선택해 instructions
      // 끝에 append. 비어있는 섹션은 noop.
      var APPENDIX = {
        honesty:  $APX_HONESTY,
        exec:     $APX_EXEC,
        research: $APX_RESEARCH,
        image:    $APX_IMAGE
      };
      function appendixFor(spec, tools) {
        var parts = [];
        // honesty = 도구 결과 사용하는 모든 agent. image-only 는 도구 셋 좁아 noise.
        if (spec.kind !== 'image') parts.push(APPENDIX.honesty);
        // execute_code 부착 시만 print() 강제 규칙.
        if (tools.indexOf('execute_code') !== -1) parts.push(APPENDIX.exec);
        // deep_research 가이드는 research kind 만 (Super Agent / commercial 은 도구 미부착).
        if (spec.kind === 'research') parts.push(APPENDIX.research);
        // image gen 가이드는 image kind 만.
        if (spec.kind === 'image') parts.push(APPENDIX.image);
        return parts.filter(function(p){ return p && p.length > 0; })
                    .map(function(p){ return '\n\n' + p; })
                    .join('');
      }
      var roleA = db.accessroles.findOne({accessRoleId: 'agent_owner'});
      var roleR = db.accessroles.findOne({accessRoleId: 'remoteAgent_owner'});

      // MCP 도구는 kind 별로 분기 부착. sys__all__sys = LibreChat 의 mcp_all + _mcp_ delimiter.
      // default/super: 일상 보조 4종. research: 인용 fetch + deep_research. image: 없음.
      // youtube 는 whisper(STT) 백엔드 있을 때만 부착 — 무자막 영상 전사가 whisper 의존이고
      // OR 폴백을 안 두기로 했으므로(GPU 전용), 없으면 도구 자체를 빼 깨진 호출을 막는다.
      var WHISPER_ON = ${whisper_on};
      var MCP_DEFAULT = [
        'sys__all__sys_mcp_fetch_url',     // URL fetch + Markdown 변환
        'sys__all__sys_mcp_time',          // 현재 시간 / 타임존 변환
        'sys__all__sys_mcp_usage',         // 본인 토큰 사용량/예산 (my_usage, budget_status)
      ].concat(WHISPER_ON ? ['sys__all__sys_mcp_youtube'] : [])
       .concat(['sys__all__sys_mcp_smart_search']);  // 업로드 문서 정밀 검색
      var MCP_RESEARCH = [
        'sys__all__sys_mcp_fetch_url',     // 인용 URL 직독
        'sys__all__sys_mcp_deep_research', // ReAct 학술 사이드카 (5-15분, arxiv/scholar 등)
        'sys__all__sys_mcp_smart_search',  // 업로드 문서 정밀 검색 (문서 대조용)
      ];
      var MCP_PAPERBANANA = [
        'sys__all__sys_mcp_paperbanana',   // 학술 다이어그램/플롯 생성 (generate_diagram/plot/evaluate)
      ];
      // math / math_basic MCP excluded — symbolic + arithmetic go through execute_code.
      function mcpToolsFor(spec) {
        if (spec.kind === 'image')       return [];
        if (spec.kind === 'video')       return ['sys__all__sys_mcp_generate_video']; // 텍스트→비디오(comfyui-shim→LTXV)
        if (spec.kind === 'ppt')         return ['sys__all__sys_mcp_export_deck']; // 저작은 도구없이 HTML 직접, export_deck 만 PDF 내보내기용
        if (spec.kind === 'notetaker')   return []; // 전사는 LibreChat 내장 STT(텍스트로 업로드 → whisper-shim)가 업로드 시 처리 — 전사문이 컨텍스트로 들어옴, 전용 MCP 불필요
        if (spec.kind === 'research')    return MCP_RESEARCH.slice();
        if (spec.kind === 'paperbanana') return MCP_PAPERBANANA.slice();
        return MCP_DEFAULT.slice();
      }

      var BUILTIN_DEFAULT = ['execute_code', 'file_search', 'web_search'];

      function builtinFor(spec) {
        // image: 빌트인 generate_image (A1111 → comfyui-shim) 동기 생성.
        if (spec.kind === 'image') return ['generate_image'];
        // video: 생성은 generate_video MCP 전담 — 빌트인 불필요.
        if (spec.kind === 'video') return [];
        // ppt: 모델이 자체완결형 HTML 발표자료를 아티팩트로 직접 저작 — 도구 불필요.
        if (spec.kind === 'ppt')   return [];
        // notetaker: 전사는 내장 STT 가 처리(전사문이 컨텍스트로 첨부됨). file_search 는
        // 보조로 유지(회의 참고 PDF 등 전사 외 문서 대조용).
        if (spec.kind === 'notetaker') return ['file_search'];
        // research: web_search 제외 — deep_research(LDR)가 SearXNG 다중소스(arxiv/
        // scholar/openalex/crossref/pubmed) 검색을 내부에서 다단계로 수행하므로 별도
        // web_search 는 중복이고, 122b 가 그쪽으로 폴백해 deep_research 를 안 쓰게 만든다.
        // fetch_url(인용검증)/file_search(문서대조)는 보조로 유지(MCP_RESEARCH + builtin).
        if (spec.kind === 'research') return ['execute_code', 'file_search'];
        // paperbanana: 그림 생성은 MCP(paperbanana)가 전담. file_search 만 — 업로드한
        // 데이터/논문 PDF 를 참조해 figure 근거로 쓰기 위함. web_search/generate_image 불필요.
        if (spec.kind === 'paperbanana') return ['file_search'];
        return BUILTIN_DEFAULT.slice();
      }

      // 모든 instruction 텍스트는 routing/instructions.md 의 sentinel 섹션에서 read.
      // 변경 시 그 파일만 손대고 sync 재호출.
      var SEC = {
        image_header:    $SEC_IMG_H,
        image_body:      $SEC_IMG_B,
        default_header:  $SEC_DEF_H,
        research_header: $SEC_RES_H,
        research_body:   $SEC_RES_B,
        ppt:             $SEC_PPT,
        notetaker:       $SEC_NOTE,
        video:           $SEC_VID,
        language:        $SEC_LANG,
        tool_calling:    $SEC_TOOL,
        trig_youtube:    $SEC_YT,
        trig_math:       $SEC_MATH
      };

      function instructionsFor(spec, tools) {
        if (spec.kind === 'image') {
          var imgInstr = SEC.image_header;
          imgInstr += '\n\n' + SEC.language;
          // policy.tool_calling — image 도 native function-call 강제(ReAct {"action":...}
          // 텍스트 폴백 억제). image body 가 generate_image 인자를 산문 묘사하므로 특히 필요.
          imgInstr += '\n\n' + SEC.tool_calling;
          imgInstr += '\n\n' + SEC.image_body;
          imgInstr += appendixFor(spec, tools);
          return imgInstr;
        }
        if (spec.kind === 'video') {
          // image 와 동일: native function-call 강제(generate_video 인자를 산문 묘사하므로
          // ReAct 텍스트 폴백 억제 필요).
          var vidInstr = SEC.video;
          vidInstr += '\n\n' + SEC.language;
          vidInstr += '\n\n' + SEC.tool_calling;
          vidInstr += appendixFor(spec, tools);
          return vidInstr;
        }
        if (spec.kind === 'ppt') {
          // 도구 없이 HTML 아티팩트 직접 저작 → tool_calling/appendix 불필요.
          return SEC.ppt + '\n\n' + SEC.language;
        }
        if (spec.kind === 'notetaker') {
          return SEC.notetaker + '\n\n' + SEC.language + '\n\n' + SEC.tool_calling;
        }
        if (spec.kind === 'paperbanana') {
          var pbInstr = 'You are Paper Banana — you produce publication-quality figures for '
            + 'academic papers via the paperbanana tools: generate_diagram (method / architecture / '
            + 'conceptual diagrams from a text description), generate_plot (statistical charts from '
            + 'data the user provides or uploads), evaluate_diagram (critique a figure against a '
            + 'reference). For any figure / diagram / plot / chart request, call the matching tool '
            + 'with the described method or data; the tool runs a multi-agent render pipeline and '
            + 'returns the image. Keep your reply brief — describe the figure, never draw it as '
            + 'ASCII / text / base64.';
          pbInstr += '\n\n' + SEC.language;
          pbInstr += '\n\n' + SEC.tool_calling;
          pbInstr += appendixFor(spec, tools);
          return pbInstr;
        }

        var caps = [];
        if (tools.indexOf('file_search')    !== -1) caps.push('file_search (RAG over user files)');
        if (tools.indexOf('web_search')     !== -1) caps.push('web_search');
        if (tools.indexOf('execute_code')   !== -1) caps.push('code execution');
        var headerTpl = (spec.kind === 'research') ? SEC.research_header : SEC.default_header;
        var instr = headerTpl.replace('{{tools_caps}}', caps.join(', '));
        instr += '\n\n' + SEC.language;
        instr += '\n\n' + SEC.tool_calling;
        if (tools.indexOf('sys__all__sys_mcp_youtube') !== -1) {
          instr += '\n\n' + SEC.trig_youtube;
        }
        if (tools.indexOf('execute_code') !== -1) {
          instr += '\n\n' + SEC.trig_math;
        }
        // artifact 프롬프트는 instructions 가 아니라 artifacts 모드로 게이팅된다 —
        // image/ppt/video 는 기본 ON, 그 외는 토글 ON 시 (patch_librechat_artifacts_toggle.js).
        if (spec.kind === 'research') instr += '\n\n' + SEC.research_body;
        instr += appendixFor(spec, tools);
        return instr;
      }

      function rid(p) { return p + Math.random().toString(36).slice(2,14) + Math.random().toString(36).slice(2,12); }

      // 같은 이름이면 tools/instructions/model 을 root + versions 동기화 (없으면 insert).
      // dropdown 순서는 DISPLAY_ORDER rank 만큼 updatedAt 을 미래로 띄워 강제한다(상용은
      // 각 오름차순). createdAt 은 미래 표시 회피.
      // 에이전트 → AI Agent Store 카테고리 매핑: 로컬 스튜디오(Image/Video/Slide/Note Taker)
      // →Productivity, Deep Research/Paper Banana→Research, 상용 LLM + 상용 이미지/비디오
      // (lockModel 지정 = Nano Banana / Veo / Sora 등)→Closed Models(commercial),
      // Super·그 외 local→General.
      function categoryFor(model, kind, lockModel) {
        if (kind === 'notetaker')   return 'productivity';
        if (kind === 'image')       return lockModel ? 'commercial' : 'productivity';  // 상용 이미지 모델→Closed Models, 로컬 Image Studio→Productivity
        if (kind === 'video')       return lockModel ? 'commercial' : 'productivity';  // 상용 비디오 모델→Closed Models, 로컬 Video Studio→Productivity
        if (kind === 'ppt')         return 'productivity';  // Slide Studio
        if (kind === 'research')    return 'research';      // Deep Research
        if (kind === 'paperbanana') return 'research';
        if (model.indexOf('openai/')     === 0 ||
            model.indexOf('anthropic/')  === 0 ||
            model.indexOf('google/')     === 0 ||
            model.indexOf('deepseek/')   === 0 ||
            model.indexOf('x-ai/')       === 0 ||
            model.indexOf('perplexity/') === 0 ||
            model.indexOf('meta/')       === 0 ||
            model.indexOf('qwen/')       === 0) return 'commercial';
        return 'general';
      }

      // 추천 프롬프트 칩(UI). Slide Studio 는 템플릿 카테고리를 채팅으로 고르므로,
      // 대표 템플릿을 starter 로 노출해 UI 에서 발견 가능하게 한다.
      function startersFor(kind) {
        if (kind === 'ppt') return [
          '비즈니스 템플릿으로 분기 실적 발표자료 만들어줘',
          '테크 템플릿으로 신기술 소개 발표자료 만들어줘',
          '교육 템플릿으로 강의 슬라이드 만들어줘',
          '창의 템플릿으로 신제품 피칭 자료 만들어줘',
          '마케팅 템플릿으로 캠페인 제안 발표자료 만들어줘',
          '미니멀 템플릿으로 깔끔한 발표자료 만들어줘'
        ];
        if (kind === 'notetaker') return [
          '녹음 파일을 첨부해 「텍스트로 업로드」로 올린 뒤 회의록으로 정리해줘',
          '강의 녹음으로 강의노트 만들어줘',
          '녹음 내용을 보고서로 작성해줘'
        ];
        if (kind === 'video') return [
          '해질녘 해변을 걷는 사람, 카메라가 천천히 따라가는 4초 영상 만들어줘',
          '비 내리는 네온 도시 거리를 항공샷으로 보여주는 클립',
          '벚꽃잎이 바람에 흩날리는 슬로우모션 영상'
        ];
        return [];
      }
      // 에이전트 설명(AI Agent Store 카드/선택 화면). 간략한 명사형으로. (큰따옴표 금지)
      function descFor(kind) {
        if (kind === 'ppt') return 'AI 발표자료 생성 · 자체완결 HTML 슬라이드(16:9) · 피칭/보고/강의 톤 자동 · PDF·PPTX 내보내기';
        if (kind === 'super') return '만능 AI 어시스턴트 · 코드 실행 · 실시간 웹 검색 · 유튜브 요약 · 문서 검색 · 아티팩트 제작';
        if (kind === 'image') return '텍스트→이미지 생성(로컬 FLUX·무료) · 일러스트 · 포스터 · 컨셉아트 · 배경';
        if (kind === 'video') return '텍스트→비디오 생성 · Google Veo · OpenAI Sora · 짧은 클립 · 컨셉 영상 · 모션 b-roll';
        if (kind === 'research') return '학술 심층 조사 · arxiv·scholar 다단계 검색 · 인용 정리 · 수치 재검증 · 논문 대조';
        if (kind === 'paperbanana') return '논문용 다이어그램·통계 플롯 생성 · 아키텍처/방법론 다이어그램 · publication-quality';
        if (kind === 'notetaker') return '음성·녹음 → 문서 정리 · 회의록 · 강의노트 · 보고서 · 한국어/영어 자동 전사';
        return '';
      }
      // 아티팩트 기본 모드. image/ppt/video 는 'default'(기본 ON — 패치가 배지 명시 ON 외엔
      // 이 DB 값을 따름). 그 외 ''(토글로만 ON).
      function artifactsFor(kind) {
        return (kind === 'image' || kind === 'ppt' || kind === 'video') ? 'default' : '';
      }
      function upsertAgent(name, model, tools, instructions, is_promoted, kind, descIn, lockModel) {
        is_promoted = !!is_promoted;
        var cat = categoryFor(model, kind, lockModel);
        var starters = startersFor(kind);
        var desc = descIn || descFor(kind);
        var art = artifactsFor(kind);
        // DISPLAY_ORDER(index 0 = 상단) 기준 rank 로 updatedAt 을 띄운다 — rank 가
        // 작을수록 updatedAt 이 늦어(미래) AI Agent Store/드롭다운 최상단에 정렬된다.
        var rank = DISPLAY_ORDER.indexOf(name);
        if (rank === -1) rank = DISPLAY_ORDER.length + 50;  // 카탈로그 외 → 최하단
        var t = new Date(now.getTime() + (1000 - rank) * 1000);
        var ex = db.agents.findOne({author: u._id, name: name});
        if (ex) {
          var versions = Array.isArray(ex.versions) ? ex.versions : [];
          var vs = versions.map(function(v){
            v.tools = tools; v.instructions = instructions; v.model = model; v.is_promoted = is_promoted; v.category = cat; v.conversation_starters = starters; v.description = desc; v.updatedAt = t; return v;
          });
          db.agents.updateOne({_id: ex._id}, {\$set: {
            tools: tools, instructions: instructions, model: model,
            is_promoted: is_promoted, artifacts: art, category: cat,
            conversation_starters: starters, description: desc, versions: vs, updatedAt: t
          }});
          print('UPDATED:' + name + ':' + ex.id);
          return ex.id;
        }
        var id = rid('agent_');
        var ver = { name:name, description:desc, instructions:instructions, provider:'LiteLLM', model:model, tools:tools,
                    artifacts:art, category:cat, support_contact:{name:'',email:''},
                    agent_ids:[], edges:[], conversation_starters:starters, is_promoted:is_promoted,
                    mcpServerNames:[], tool_options:{}, createdAt:now, updatedAt:t };
        var ins = db.agents.insertOne({
          id:id, name:name, description:desc, instructions:instructions, provider:'LiteLLM', model:model, artifacts:art,
          tools:tools, tool_kwargs:[], author:u._id, agent_ids:[], edges:[], conversation_starters:starters,
          versions:[ver], category:cat, support_contact:{name:'',email:''},
          is_promoted:is_promoted, mcpServerNames:[], tool_options:{},
          end_after_tools:false, hide_sequential_outputs:false, createdAt:now, updatedAt:t, __v:0
        });
        if (roleA && roleR) {
          db.aclentries.insertMany([
            { principalModel:'User', principalType:'user', principalId:u._id,
              resourceType:'agent', resourceId:ins.insertedId, permBits:15, roleId:roleA._id,
              grantedAt:now, grantedBy:u._id, createdAt:now, updatedAt:now, __v:0 },
            { principalModel:'User', principalType:'user', principalId:u._id,
              resourceType:'remoteAgent', resourceId:ins.insertedId, permBits:15, roleId:roleR._id,
              grantedAt:now, grantedBy:u._id, createdAt:now, updatedAt:now, __v:0 }
          ]);
        } else { print('WARN_NO_ACL_ROLES'); }
        print('CREATED:' + name + ':' + id);
        return id;
      }

      function deleteAgent(doc) {
        db.aclentries.deleteMany({resourceId: doc._id});
        db.agents.deleteOne({_id: doc._id});
        print('DELETED:' + doc.name + ':' + doc.id);
      }

      var specs = ${agent_specs};
      var DISPLAY_ORDER = ${display_order_json};
      var canonNames = specs.map(function(s){ return s.name; });

      function targetNameFor(d) {
        if (!d.model) return null;
        // commercial: 모델이 유일하게 매핑되는 spec → 그 spec 의 정식명칭.
        var bym = specs.filter(function(s){ return s.model === d.model && !s.lockModel; });
        if (bym.length === 1) return bym[0].name;
        // image/video: 과거 alias 이름이 어느 spec 의 lockModel 이면 그 spec 의 정식명칭.
        var byl = specs.filter(function(s){ return s.lockModel && s.lockModel === d.name; });
        if (byl.length === 1) return byl[0].name;
        // 레거시 'Text (modelTag)' 접미 패턴 호환.
        var pmatch = d.name && d.name.match(/\\(([^)]+)\\)$/);
        if (pmatch) {
          var modelTag = pmatch[1];
          for (var i = 0; i < canonNames.length; i++) {
            if (canonNames[i] === modelTag) return canonNames[i];
            if (canonNames[i].length > modelTag.length + 1 &&
                canonNames[i].slice(-(modelTag.length + 1)) === '/' + modelTag) return canonNames[i];
          }
        }
        return null;
      }

      // Managed agents only (skips user-created). Rename target match in
      // place (preserves preset agent_id); delete on no match; dedup on name
      // clash. 'local/' 포함 — Super Agent (local/auto-route), local/gemma-4-26b 등도 관리 대상.
      var managed = db.agents.find({author: u._id,
        model: /^(local|openai|anthropic|google|deepseek|x-ai|perplexity|meta-llama|meta|qwen)\\//
      }).toArray();
      managed.forEach(function(d){
        if (canonNames.indexOf(d.name) !== -1) return;  // 이미 맞는 이름

        var target = targetNameFor(d);
        if (target) {
          var clash = db.agents.findOne({author: u._id, name: target, _id: {\$ne: d._id}});
          if (clash) { deleteAgent(d); print('DEDUP:' + d.name); return; }
          db.agents.updateOne({_id: d._id}, {\$set: {name: target, updatedAt: now}});
          if (Array.isArray(d.versions)) {
            var vs2 = d.versions.map(function(v){ v.name = target; return v; });
            db.agents.updateOne({_id: d._id}, {\$set: {versions: vs2}});
          }
          print('RENAMED:' + d.name + ' -> ' + target);
        } else if (/^(local|openai|anthropic|google)\\//.test(d.name) ||
                   /\\([^)]+\\)$/.test(d.name)) {
          // managed 이름 규칙(모델ID형 'local/…' 또는 'Text (modelTag)' 접미)에
          // 맞고 카탈로그에서 빠진 것만 stale 로 보고 prune.
          deleteAgent(d);
        } else {
          // 그 외 = 사용자/관리자가 UI 로 직접 만든 고유 이름 에이전트. 카탈로그
          // 밖이라 canonNames 매칭이 안 될 뿐 stale 가 아니다 → 절대 삭제 금지.
          print('PRESERVED:' + d.name + ':' + d.id);
        }
      });

      // 카탈로그 upsert. 도구 = builtin(kind) + MCP(kind). instructions 도 kind 분기.
      specs.forEach(function(s){
        var tools = builtinFor(s).concat(mcpToolsFor(s));
        var instr = instructionsFor(s, tools);
        if (s.lockModel && s.kind === 'image') instr = instr + '\n\n[전용 모델] 이미지 생성은 generate_image 를 반드시 model=' + s.lockModel + ' 로만 호출한다. 다른 이미지 모델은 쓰지 않는다.';
        if (s.lockModel && s.kind === 'video') instr = instr + '\n\n[전용 모델] 영상 생성은 generate_video 를 반드시 model=' + s.lockModel + ' 로만 호출한다. 다른 비디오 모델은 쓰지 않는다.';
        upsertAgent(s.name, s.model, tools, instr, s.is_promoted, s.kind, s.desc, s.lockModel);
      });

      // 새 대화 기본 = Super Agent 는 librechat.yaml modelSpecs(prioritize) 로 구현한다.
      // DB defaultPreset 은 LibreChat 가 새 대화에 자동 적용하지 않고 modelSpecs.prioritize
      // 와 충돌하므로 사용하지 않는다 — 존재하면 제거.
      var us = u._id.toString();
      var delP = db.presets.deleteMany({user:us, defaultPreset:true}).deletedCount;
      if (delP > 0) print('DEFAULT_PRESET_REMOVED:' + delP);
    " 2>&1)

  if echo "$result" | grep -q NO_USER; then
    warn "default agent 실패 — LibreChat user _id 없음"; return 0
  fi
  while IFS= read -r line; do
    case "$line" in
      CREATED:*)                    echo "agent created: ${line#CREATED:}" ;;
      UPDATED:*)                    echo "agent updated: ${line#UPDATED:}" ;;
      RENAMED:*)                    echo "agent renamed: ${line#RENAMED:}" ;;
      DELETED:*)                    echo "agent deleted: ${line#DELETED:}" ;;
      PRESERVED:*)                  echo "custom agent preserved (삭제 안 함): ${line#PRESERVED:}" ;;
      DEDUP:*)                      echo "dedup: ${line#DEDUP:}" ;;
      DEFAULT_PRESET_REMOVED:*)     echo "default preset removed: ${line#DEFAULT_PRESET_REMOVED:}" ;;
      WARN_NO_ACL_ROLES)            warn "ACL roles 없음 — 'My Agents' 미노출 가능" ;;
    esac
  done <<< "$result"
}

cmd_agent_sync() {
  local mu mp; mu=$(env_get MONGO_ROOT_USER); mp=$(env_get MONGO_ROOT_PASSWORD)
  [[ -z "$mu" || -z "$mp" ]] && { err "MONGO_ROOT_USER/PASSWORD 누락"; exit 1; }
  docker_on_node NODE_LIBRECHAT ps --format '{{.Names}}' 2>/dev/null | grep -q '^chat-mongodb$' \
    || { err "chat-mongodb 미실행 (NODE_LIBRECHAT 도달 불가)"; exit 1; }

  # 에이전트는 ADMIN 한 벌만 생성하고 전 사용자에게 read-only 공유 (사용자별 복제 X).
  # 1) ADMIN owner 로 카탈로그 upsert  2) admin 의 각 agent 에 public viewer ACL(permBits 1)
  #    = LibreChat 의 글로벌 공유 (getListAgents 가 public ACL 로 노출, 편집 불가)
  # 3) 비-ADMIN 사용자가 소유한 managed 카탈로그 복제본 정리.
  local admin_email
  admin_email=$(docker_on_node NODE_LIBRECHAT exec chat-mongodb mongosh --quiet \
    -u "$mu" -p "$mp" --authenticationDatabase admin LibreChat --eval \
    'var a=db.users.findOne({role:"ADMIN"},{email:1}); if(a&&a.email) print(a.email);' 2>/dev/null | grep -E '@' | head -1)
  [[ -z "$admin_email" ]] && { warn "ADMIN 사용자 없음 — 에이전트 동기화 스킵 (먼저 admin 생성)"; return 0; }
  info "글로벌 카탈로그 owner: $admin_email (전 사용자 read-only)"
  upsert_shared_agent_catalog "$admin_email"

  docker_on_node NODE_LIBRECHAT exec chat-mongodb mongosh --quiet \
    -u "$mu" -p "$mp" --authenticationDatabase admin LibreChat --eval '
    var now=new Date();

    // AI Agent Store 카테고리 재구성. custom:true + label 에 영문 리터럴 직접 기입
    // (번들 렌더 로직: label 이 "com_" 로 시작 안 하면 그대로 노출). order 로 정렬.
    // active = General/Productivity/Education/Research/Closed Models. Super Agent 는
    // general, 로컬 Image/Video/Slide Studio·Note Taker 는 Productivity, Deep Research/
    // Paper Banana 는 Research, 상용 LLM·상용 이미지/비디오는 Closed Models(commercial).
    // 나머지 기본 카테고리(image/development 등)는 비활성.
    var wantActive=[
      {value:"general",      label:"General",        order:0},
      {value:"productivity", label:"Productivity",   order:1},
      {value:"education",    label:"Education",       order:2},
      {value:"research",     label:"Research",       order:3},
      {value:"commercial",   label:"Closed Models",  order:4},
      {value:"my_agents",    label:"My Agents",      order:5}
    ];
    wantActive.forEach(function(c){
      db.agentcategories.updateOne({value:c.value},
        {"$set":{value:c.value,label:c.label,order:c.order,isActive:true,custom:true,updatedAt:now},
         "$setOnInsert":{createdAt:now}},{upsert:true});
    });
    // 나머지 카테고리는 전부 비활성 (image/development 포함).
    db.agentcategories.updateMany(
      {value:{"$nin":["general","productivity","research","commercial","education","my_agents"]}},
      {"$set":{isActive:false}});
    print("CATEGORIES_SET");

    // agent 권한: ADMIN·USER 모두 생성 가능(CREATE:true). 단 USER 는 공유/공개 불가
    // (SHARE·SHARE_PUBLIC:false — 본인 전용 agent 만), ADMIN 만 공유/공개 가능(카탈로그
    // 운영). librechat.yaml 이 fail-closed 베이스라인(생성O·공유X)으로 양쪽 시드 → 여기서
    // ADMIN 만 SHARE 승격하고 USER 는 명시적으로 공유 차단(멱등).
    db.roles.updateOne({name:"ADMIN"},{"$set":{
      "permissions.AGENTS.CREATE":true,
      "permissions.AGENTS.SHARE":true,
      "permissions.AGENTS.SHARE_PUBLIC":true}});
    db.roles.updateOne({name:"USER"}, {"$set":{
      "permissions.AGENTS.CREATE":true,
      "permissions.AGENTS.SHARE":false,
      "permissions.AGENTS.SHARE_PUBLIC":false}});
    print("AGENT_PERMS admin=create+share user=create+no-share");

    var admin=db.users.findOne({role:"ADMIN"});
    var vrole=db.accessroles.findOne({accessRoleId:"agent_viewer",resourceType:"agent"});
    if(!admin||!vrole){ print("SKIP_SHARE (admin/agent_viewer 없음)"); }
    else {
      var shared=0;
      db.agents.find({author:admin._id}).forEach(function(ag){
        db.aclentries.updateOne(
          {principalType:"public",resourceType:"agent",resourceId:ag._id},
          {"$set":{principalType:"public",resourceType:"agent",resourceId:ag._id,permBits:1,roleId:vrole._id,grantedBy:admin._id,grantedAt:now,updatedAt:now},"$setOnInsert":{createdAt:now}},
          {upsert:true});
        shared++;
      });
      var others=db.users.find({role:{"$ne":"ADMIN"}},{_id:1}).toArray().map(function(u){return u._id;});
      // managed 기본 카탈로그(=admin 소유)와 같은 이름의 비-ADMIN 복제본만 제거한다
      // (사용자별 복제본). 사용자가 직접 만든 고유 이름 에이전트는 보존.
      var managedNames=db.agents.find({author:admin._id},{name:1}).toArray().map(function(a){return a.name;});
      var clones=db.agents.find({author:{"$in":others}, name:{"$in":managedNames}},{_id:1}).toArray().map(function(a){return a._id;});
      var dA=db.aclentries.deleteMany({resourceType:{"$in":["agent","remoteAgent"]},resourceId:{"$in":clones}}).deletedCount;
      var dG=db.agents.deleteMany({_id:{"$in":clones}}).deletedCount;

      // 새 대화 기본 = Super Agent 는 librechat.yaml modelSpecs 로 구현하므로 전 사용자
      // defaultPreset 은 제거한다 (modelSpecs.prioritize 와 충돌 + 자동 적용도 안 됨).
      var delP=db.presets.deleteMany({defaultPreset:true}).deletedCount;
      print("SHARED:"+shared+" CLONES_DELETED:"+dG+" ACL_DELETED:"+dA+" DEFAULT_PRESET_REMOVED:"+delP);
    }' 2>&1 | grep -E "SHARED:|SKIP_SHARE|CATEGORIES_SET|AGENT_PERMS|DEFAULT_PRESET_REMOVED"
  ok "글로벌 read-only 공유 완료"

  # librechat.yaml modelSpecs 의 모든 pin agent_id 를 현재 DB id 로 동기화.
  # LibreChat 는 modelSpecs 내부 ${ENV} 치환 미지원 → 실제 id 를 박아야 하는데,
  # MongoDB wipe/재설정 시 id 신규 발급되면 pin 깨짐 → 각 spec 의 label(=에이전트
  # 이름)로 현재 id 조회해 그 spec 의 agent_id 줄을 모두 교체(awk, 주석/포맷 보존).
  local lc_yaml="${SCRIPT_DIR}/../librechat.yaml"
  if [[ ! -f "$lc_yaml" ]] || ! grep -qE '^\s*agent_id: ' "$lc_yaml"; then
    info "librechat.yaml modelSpecs agent_id 없음 — 동기화 스킵 (modelSpecs 미사용)"
    return 0
  fi
  # label<TAB>id 맵 (functional 에이전트 전부).
  local id_map; id_map=$(docker_on_node NODE_LIBRECHAT exec chat-mongodb mongosh --quiet \
    -u "$mu" -p "$mp" --authenticationDatabase admin LibreChat --eval \
    'db.agents.find({author:db.users.findOne({role:"ADMIN"})._id},{name:1,id:1}).forEach(function(a){if(a.id)print(a.name+"\t"+a.id)});' 2>/dev/null | grep -P '\tagent_' || true)
  if [[ -z "$id_map" ]]; then warn "에이전트 id 맵 조회 실패 — modelSpecs 미갱신"; return 0; fi
  # modelSpecs 블록 내 각 spec 의 label 직후 agent_id 를 맵 값으로 치환.
  # 또한 새 대화 기본인 Super Agent 미생성(GPU 없는 OR 전용 배포)이면 prioritize
  # false 로 내려 깨진 기본값 핀 차단(있으면 true 로 복구 — 멱등·양방향).
  local tmp; tmp=$(mktemp)
  awk -F'\t' '
    FNR==NR { if (NF==2) map[$1]=$2; next }
    /^[A-Za-z_]/ { inms = ($0 ~ /^modelSpecs:/) ? 1 : 0 }
    {
      if (inms && $0 ~ /^[[:space:]]*prioritize:[[:space:]]*(true|false)/) {
        sub(/prioritize:[[:space:]]*(true|false)/, ("Super Agent" in map) ? "prioritize: true" : "prioritize: false")
      }
      if (inms && $0 ~ /^[[:space:]]*label:[[:space:]]*"/) {
        s=$0; sub(/^[^"]*"/,"",s); sub(/".*$/,"",s); curlabel=s
      }
      if (inms && $0 ~ /^[[:space:]]*agent_id:[[:space:]]*"/ && (curlabel in map)) {
        sub(/agent_id:[[:space:]]*"[^"]*"/, "agent_id: \"" map[curlabel] "\"")
      }
      print
    }
  ' <(printf '%s\n' "$id_map") "$lc_yaml" > "$tmp"
  if [[ -s "$tmp" ]] && ! diff -q "$lc_yaml" "$tmp" >/dev/null 2>&1; then
    cat "$tmp" > "$lc_yaml"; rm -f "$tmp"
    ok "modelSpecs pin agent_id 갱신 (LibreChat 재시작)"
    docker_on_node NODE_LIBRECHAT restart LibreChat >/dev/null 2>&1 \
      || warn "LibreChat 재시작 실패 — 'docker restart LibreChat' 수동 실행"
  else
    rm -f "$tmp"; ok "modelSpecs pin agent_id 최신"
  fi
}

cmd_user_list() {
  litellm_get "/user/list" | jq -r '
    (if type == "array" then . else (.users // .data // []) end)[]
    | "\(.user_id)\t\(.user_role)\tspend:\(.spend // 0)$"'
}

# 사용자별 사용량(이번 달 spend) vs 월 예산(max_budget). --user 로 한 명만.
# spend 는 budget_duration(1mo)마다 리셋 — RESET 컬럼 = 다음 리셋일.
cmd_user_usage() {
  local user_id=""
  while [[ $# -gt 0 ]]; do case "$1" in --user) need_val "$@"; user_id="$2"; shift 2 ;; *) shift ;; esac; done
  reconcile_topups
  local out; out="$( { printf 'USER\tSPEND\tBUDGET\tUSED\tRESET\n'
    litellm_get "/user/list" | jq -r --arg u "$user_id" '
      (if type == "array" then . else (.users // .data // []) end)[]
      | select($u == "" or .user_id == $u)
      | [ .user_id,
          "$" + ((.spend // 0)|(.*100|round/100)|tostring),
          (if .max_budget == null then "unlimited" else "$" + (.max_budget|tostring) end),
          (if (.max_budget // 0) > 0 then (((.spend // 0)/.max_budget*100)|floor|tostring)+"%" else "-" end),
          ((.budget_reset_at // "-")[0:10]) ] | @tsv'; } )"
  # column 없으면(드묾) raw TSV 로 폴백 — 파이프 우측 fallback 은 입력 못 받음 → 분기로.
  if command -v column >/dev/null 2>&1; then printf '%s\n' "$out" | column -t -s "$(printf '\t')"
  else printf '%s\n' "$out"; fi
}

# 만료된 topup(예산 임시상향) 자동 복구. expires_at(상향 당시 budget_reset_at) 경과 시
# = 그 사이 budget_duration 리셋 발생 → original_budget 으로 복원.
# user usage/list/topup 진입 시 lazy 실행(원장 비었으면 no-op). 즉시성 필요하면 월초 cron.
reconcile_topups() {
  local f="${DATA_DIR}/topups.json"
  [[ -s "$f" ]] || return 0
  local now; now=$(date +%s)
  local keep='[]' e uid orig exp exp_s changed=0
  while IFS= read -r e; do
    uid=$(jq -r '.user_id' <<<"$e"); orig=$(jq -r '.original_budget' <<<"$e"); exp=$(jq -r '.expires_at' <<<"$e")
    exp_s=$(date -d "$exp" +%s 2>/dev/null || echo 0)
    if (( exp_s > 0 && now >= exp_s )); then
      if [[ "$orig" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then   # 원장 손상 시 max_budget:null(무제한) 방지
        litellm_post "/user/update" "$(jq -n --arg u "$uid" --argjson b "$orig" '{user_id:$u, max_budget:$b}')" >/dev/null 2>&1 \
          && info "topup 만료 → 복구: $uid 월 한도 \$$orig"
        changed=1
      else
        warn "topup 원장의 original_budget 비정상('$orig') — $uid 항목 보존, 수동 확인 필요"
        keep=$(jq -c --argjson x "$e" '. + [$x]' <<<"$keep")
      fi
    else
      keep=$(jq -c --argjson x "$e" '. + [$x]' <<<"$keep")
    fi
  done < <(jq -c '.[]' "$f" 2>/dev/null)
  if (( changed )); then echo "$keep" > "$f"; chmod 600 "$f"; fi
  return 0
}

# 일시 budget 충전 — 월 한도(max_budget)를 amount 만큼 상향. spend(실사용량)는 그대로
# 유지해 통계 정확. original_budget·만료일(=현재 budget_reset_at)을 원장
# (data/ledger/topups.json)에 기록 → 월 리셋 후 reconcile_topups 가 원래 한도로 자동
# 원복(영구 상향 아님). 같은 달 재충전은 누적, original 은 첫 충전값 유지.
cmd_user_topup() {
  local user_id="" amount=""
  while [[ $# -gt 0 ]]; do case "$1" in
    --user)   need_val "$@"; user_id="$2"; shift 2 ;;
    --amount) need_val "$@"; amount="$2"; shift 2 ;;
    *) shift ;; esac; done
  require_arg --user "$user_id"
  require_arg --amount "$amount"
  [[ "$amount" =~ ^[0-9]+(\.[0-9]+)?$ ]] || { err "--amount 는 양수 (\$ 단위): $amount"; return 1; }
  reconcile_topups
  local uinfo; uinfo=$(litellm_get "/user/info?user_id=${user_id}") || { err "user 조회 실패: $user_id"; return 1; }
  local curbud spend reset
  curbud=$(echo "$uinfo" | jq -r 'if .user_info.max_budget == null then "" else (.user_info.max_budget|tostring) end')
  [[ -z "$curbud" ]] && { err "이 사용자는 월 한도(max_budget)가 없어(무제한) 충전 대상이 아님: $user_id"; return 1; }
  spend=$(echo "$uinfo" | jq -r '.user_info.spend // 0')
  reset=$(echo "$uinfo" | jq -r '.user_info.budget_reset_at // empty')
  local expires
  if [[ -n "$reset" && "$reset" != "null" ]]; then expires="$reset"
  else expires="$(date -d "$(date +%Y-%m-01) +1 month" +%Y-%m-%dT00:00:00Z)"; fi
  local f="${DATA_DIR}/topups.json"; mkdir -p "$DATA_DIR"; [[ -s "$f" ]] || echo '[]' > "$f"
  # original = 진행 중 topup 의 첫 한도(있으면) 아니면 현재 한도.
  local original; original=$(jq -r --arg u "$user_id" 'first(.[] | select(.user_id==$u) | .original_budget) // empty' "$f")
  [[ -z "$original" || "$original" == "null" ]] && original="$curbud"
  local new; new=$(awk -v c="$curbud" -v a="$amount" 'BEGIN{printf "%.2f", c+a}')
  litellm_post "/user/update" "$(jq -n --arg u "$user_id" --argjson b "$new" '{user_id:$u, max_budget:$b}')" >/dev/null \
    || { err "충전 실패: $user_id"; return 1; }
  jq --arg u "$user_id" --argjson o "$original" --arg e "$expires" \
     'map(select(.user_id != $u)) + [{user_id:$u, original_budget:$o, expires_at:$e}]' "$f" > "$f.tmp" \
     && mv "$f.tmp" "$f" && chmod 600 "$f"
  ok "충전: ${user_id} +\$${amount}  (월 한도 \$${curbud} → \$${new}; spend \$${spend} 유지; ${expires%%T*} 리셋 후 \$${original} 으로 자동 원복)"
}

cmd_user_delete() {
  local id=""
  while [[ $# -gt 0 ]]; do case "$1" in --id) need_val "$@"; id="$2"; shift 2 ;; *) shift ;; esac; done
  require_arg --id "$id"
  litellm_post "/user/delete" "{\"user_ids\":[\"$id\"]}" | jq .
}

# data/ledger/keys.json 원장에 발급된 평문 키를 append. LiteLLM 은 hash 만
# 저장 → 발급 후 평문 재확인은 여기가 유일 출처. data/ 는
# gitignore, 파일은 600 으로 잠금.
record_issued_key() {
  local user_id="$1" key_alias="$2" team_id="$3" key="$4" budget="$5"
  mkdir -p "$DATA_DIR"
  local cache="${DATA_DIR}/keys.json"
  [[ -f "$cache" ]] || { echo '[]' > "$cache"; chmod 600 "$cache"; }
  local entry; entry=$(jq -n \
    --arg u "$user_id" --arg a "$key_alias" --arg t "$team_id" \
    --arg k "$key" --argjson b "$budget" --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{user_id:$u, key_alias:$a, team_id:$t, key:$k, max_budget:$b, issued_at:$ts}')
  if jq --argjson e "$entry" '. += [$e]' "$cache" > "${cache}.tmp"; then
    mv "${cache}.tmp" "$cache"; chmod 600 "$cache"
  else
    rm -f "${cache}.tmp"; warn "keys.json 기록 실패 — jq 에러"
  fi
}

cmd_key_issue() {
  local user_id="" team_alias="default" key_alias="" budget=9999 service=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --user)    need_val "$@"; user_id="$2";    shift 2 ;;
      --team)    need_val "$@"; team_alias="$2"; shift 2 ;;
      --alias)   need_val "$@"; key_alias="$2";  shift 2 ;;
      --budget)  need_val "$@"; budget="$2";     shift 2 ;;
      --service) need_val "$@"; service="$2";    shift 2 ;;
      *) err "Unknown: $1"; exit 1 ;;
    esac
  done

  if [[ -n "$service" ]]; then
    local alias="${service}-service-key"
    # LiteLLM 은 key hash 만 저장. plaintext 는 issue 시점에 .env 로 박힘.
    # alias 충돌로 reissue 가 HTTP 400 → .env 의 키가 아직 유효하면 skip.
    local env_var=""
    case "$service" in
      librechat) env_var="LITELLM_SERVICE_KEY" ;;
    esac
    if [[ -n "$env_var" ]]; then
      local cur; cur="$(env_get "$env_var")"
      if [[ -n "$cur" ]]; then
        local info cur_alias
        info="$(litellm_get "/key/info?key=$cur" 2>/dev/null || true)"
        cur_alias="$(echo "$info" | jq -r '.info.key_alias // empty' 2>/dev/null)"
        if [[ "$cur_alias" == "$alias" ]]; then
          echo "service key: $service (alive, alias=$alias) — skip"
          return 0
        fi
      fi
    fi

    local payload; payload=$(jq -n --arg a "$alias" --argjson b "$budget" \
      '{key_alias:$a, max_budget:$b, budget_duration:"1mo", user_role:"internal_user"}')
    local result
    if ! result=$(litellm_post "/key/generate" "$payload" 2>&1); then
      if echo "$result" | grep -q 'already exists'; then
        # alias exists in LiteLLM but .env is out of sync — manual rotation
        # required (DB has no plaintext to recover).
        err "service key alias '$alias' 가 LiteLLM 에 이미 있는데 .env 의 ${env_var:-키} 와 동기화 안 됨."
        err "  → ./scripts/manage.sh key list 로 확인 후 회전: key revoke --key <stale>"
        err "    또는 alias 다른 걸로: key issue --service $service --alias <new>"
        return 1
      fi
      err "$result"; return 1
    fi
    local key; key=$(echo "$result" | jq -r '.key')
    echo "service key: $service"; echo "KEY: $key"
    record_issued_key "" "$alias" "" "$key" "$budget"
    if [[ "$service" == "librechat" ]]; then
      env_set LITELLM_SERVICE_KEY "$key"
      env_set RAG_OPENAI_API_KEY  "$key"
      # NODE_LIBRECHAT 이 원격이면 갱신된 .env push + librechat/rag_api restart. 아니면
      # 컨테이너가 옛 키로 계속 동작 → 사용자 키 발급이 한 번에 안 통함.
      local lc_host; lc_host="$(env_get NODE_LIBRECHAT)"
      if [[ -n "$lc_host" ]] && ! is_local_host "$lc_host"; then
        rsync_push_file "$lc_host" ".env" \
          && ssh_run "$lc_host" "docker compose restart librechat rag_api" \
          && ok "service key NODE_LIBRECHAT 로 push + librechat/rag_api restart" \
          || warn ".env push 또는 restart 실패 — NODE_LIBRECHAT 에서 'docker compose restart librechat rag_api' 수동 실행"
      fi
    fi
    return 0
  fi

  require_arg "--user or --service" "$user_id"
  require_email --user "$user_id"

  local team_id=""
  if [[ -n "$team_alias" ]]; then
    team_id=$(team_id_by_alias "$team_alias")
    [[ -z "$team_id" ]] && { err "team not found: $team_alias"; exit 1; }
  fi
  [[ -z "$key_alias" ]] && key_alias="${user_id%%@*}-key"

  local payload; payload=$(jq -n \
    --arg u "$user_id" --arg t "$team_id" --arg a "$key_alias" --argjson b "$budget" \
    '{user_id:$u, team_id:$t, key_alias:$a, max_budget:$b, budget_duration:"1mo"}')
  local result; result=$(litellm_post "/key/generate" "$payload")
  local key; key=$(echo "$result" | jq -r '.key')
  echo "key: $key_alias"
  echo "KEY: $key"
  record_issued_key "$user_id" "$key_alias" "$team_id" "$key" "$budget"
}

cmd_key_list() {
  local user_id=""
  while [[ $# -gt 0 ]]; do case "$1" in --user) need_val "$@"; user_id="$2"; shift 2 ;; *) shift ;; esac; done
  # return_full_object=true 없으면 .keys[] 가 해시 문자열 → 객체 인덱싱 실패.
  local ep="/key/list?return_full_object=true"; [[ -n "$user_id" ]] && ep+="&user_id=${user_id}"
  litellm_get "$ep" | jq -r '.keys[]
    | "\(.key_alias // "unnamed")\t\((.token // "?")[0:20])...\tuser:\(.user_id // "-")\tbudget:\(.max_budget)$\tspend:\(.spend // 0)$"'
}

# 원장(data/ledger/keys.json)에 저장된 평문 키 표시. --user 로 필터.
# LiteLLM 의 key list 는 hash 만 알아 앞 20자만, 여기는 전체 평문.
cmd_key_show() {
  local user_id=""
  while [[ $# -gt 0 ]]; do case "$1" in --user) need_val "$@"; user_id="$2"; shift 2 ;; *) shift ;; esac; done
  local cache="${DATA_DIR}/keys.json"
  [[ -f "$cache" ]] || { err "저장된 키 없음 ($cache 미생성) — key issue 후부터 기록됨"; return 1; }
  local rows; rows=$(jq -r --arg u "$user_id" \
    '[.[] | select($u == "" or .user_id == $u)][]
     | "\(.user_id // "-")\t\(.key_alias)\t\(.key)\tbudget:\(.max_budget)$\t\(.issued_at)"' "$cache")
  [[ -z "$rows" ]] && { err "일치하는 저장 키 없음${user_id:+ (user=$user_id)}"; return 1; }
  echo "$rows"
}

cmd_key_revoke() {
  local key=""
  while [[ $# -gt 0 ]]; do case "$1" in --key) need_val "$@"; key="$2"; shift 2 ;; *) shift ;; esac; done
  require_arg --key "$key"
  litellm_post "/key/delete" "{\"keys\":[\"$key\"]}" | jq .
  # 원장에서도 제거 — 회수된 키가 key show 에 남지 않도록.
  local cache="${DATA_DIR}/keys.json"
  if [[ -f "$cache" ]] && jq --arg k "$key" 'map(select(.key != $k))' "$cache" > "${cache}.tmp" 2>/dev/null; then
    mv "${cache}.tmp" "$cache"; chmod 600 "$cache"
  else
    rm -f "${cache}.tmp"
  fi
}

(( $# < 2 )) && usage
resource="$1"; action="$2"; shift 2
case "${resource}/${action}" in
  team/create)  cmd_team_create "$@" ;;
  team/list)    cmd_team_list ;;
  team/delete)  cmd_team_delete "$@" ;;
  team/sync)    cmd_team_sync ;;
  user/create)  cmd_user_create "$@" ;;
  user/list)    cmd_user_list ;;
  user/usage)   cmd_user_usage "$@" ;;
  user/topup)   cmd_user_topup "$@" ;;
  user/delete)  cmd_user_delete "$@" ;;
  key/issue)    cmd_key_issue "$@" ;;
  key/list)     cmd_key_list "$@" ;;
  key/show)     cmd_key_show "$@" ;;
  key/revoke)   cmd_key_revoke "$@" ;;
  agent/sync)   cmd_agent_sync "$@" ;;
  *)            err "Unknown: ${resource} ${action}"; usage ;;
esac
