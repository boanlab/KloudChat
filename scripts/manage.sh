#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<EOF
Usage: manage.sh <resource> <action> [opts]

team   create  --alias <n> [--budget --duration --tpm --rpm --models a,b,c]
       list / delete --id <team_id>

user   create  --id <email> [--team <alias>] [--budget]
               [--name <n> --username <u> --password <p>]   ← LibreChat 풀 프로비저닝
       list / delete --id <email>

key    issue   --user <email> [--team] [--alias] [--budget]
       issue   --service <name> [--budget]                  ← service-account
       list   [--user <email>]
       revoke --key <sk-...>
EOF
  exit 1
}

require_arg() { [[ -n "$2" ]] || { echo "$1 required" >&2; exit 1; }; }
require_email() {
  [[ "$2" =~ ^[^@]+@[^@]+\.[^@]+$ ]] || { echo "$1 must be email: $2" >&2; exit 1; }
}

cmd_team_create() {
  local alias="" budget=9999 duration=1mo tpm=100000 rpm=500
  local models; models="$(litellm_chat_models_csv)"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --alias)    alias="$2";    shift 2 ;;
      --budget)   budget="$2";   shift 2 ;;
      --duration) duration="$2"; shift 2 ;;
      --tpm)      tpm="$2";      shift 2 ;;
      --rpm)      rpm="$2";      shift 2 ;;
      --models)   models="$2";   shift 2 ;;
      *) echo "Unknown: $1" >&2; exit 1 ;;
    esac
  done
  require_arg --alias "$alias"

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
    jq --argjson r "$result" '. += [$r]' "$cache" > "${cache}.tmp" && mv "${cache}.tmp" "$cache"
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
  while [[ $# -gt 0 ]]; do case "$1" in --id) id="$2"; shift 2 ;; *) shift ;; esac; done
  require_arg --id "$id"
  litellm_post "/team/delete" "{\"team_ids\":[\"$id\"]}" | jq .
}

cmd_user_create() {
  local user_id="" team_alias="default" budget=9999
  local lc_name="" lc_username="" lc_password=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --id)       user_id="$2";     shift 2 ;;
      --team)     team_alias="$2";  shift 2 ;;
      --budget)   budget="$2";      shift 2 ;;
      --name)     lc_name="$2";     shift 2 ;;
      --username) lc_username="$2"; shift 2 ;;
      --password) lc_password="$2"; shift 2 ;;
      *) echo "Unknown: $1" >&2; exit 1 ;;
    esac
  done
  require_arg --id "$user_id"
  require_email --id "$user_id"

  local lc_count=0
  for v in "$lc_name" "$lc_username" "$lc_password"; do [[ -n "$v" ]] && lc_count=$((lc_count+1)); done
  (( lc_count != 0 && lc_count != 3 )) && { echo "--name/--username/--password 는 함께 지정." >&2; exit 1; }
  local full=$(( lc_count == 3 ))

  if (( full )); then
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^LibreChat$'; then
      local out
      out=$(docker exec -i LibreChat npm run create-user -- "$user_id" "$lc_name" "$lc_username" "$lc_password" <<< 'y' 2>&1 | tail -10)
      if echo "$out" | grep -q "User created successfully"; then
        echo "  ✓ LibreChat user: $user_id ($lc_username)"
      else
        echo "  ⚠ LibreChat user 생성 실패 (이미 존재 가능):" >&2
        echo "$out" | tail -3 | sed 's/^/    /' >&2
      fi
    else
      echo "  ⚠ LibreChat 미실행 — 건너뜀" >&2
    fi
  fi

  local payload; payload=$(jq -n --arg u "$user_id" '{user_id:$u, user_role:"internal_user"}')
  litellm_post "/user/new" "$payload" >/dev/null
  echo "user: $user_id"

  if [[ -n "$team_alias" ]]; then
    local team_id; team_id=$(team_id_by_alias "$team_alias")
    [[ -z "$team_id" ]] && { echo "team not found: $team_alias" >&2; exit 1; }
    payload=$(jq -n --arg tid "$team_id" --arg uid "$user_id" --argjson b "$budget" \
      '{team_id:$tid, max_budget_in_team:$b, member:{role:"user", user_id:$uid}}')
    litellm_post "/team/member_add" "$payload" >/dev/null
    echo "team: $team_alias (\$${budget}/mo)"
  fi

  if (( full )); then
    local out issued
    out=$(cmd_key_issue --user "$user_id" --team "$team_alias" --alias "${lc_username}-key" 2>&1)
    echo "$out"
    issued=$(echo "$out" | grep -oE 'sk-[A-Za-z0-9_-]+' | head -1)
    [[ -n "$issued" ]] && register_litellm_key_for_librechat_user "$user_id" "$lc_password" "$issued"
    create_default_agent_for_user "$user_id"
  fi
}

register_litellm_key_for_librechat_user() {
  local email="$1" pw="$2" key="$3"
  local lc_url="${LIBRECHAT_URL:-http://localhost:8080}"
  local jwt
  jwt=$(curl -s -X POST "${lc_url}/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg e "$email" --arg p "$pw" '{email:$e, password:$p}')" \
    2>/dev/null | jq -r '.token // ""')
  [[ -z "$jwt" ]] && { echo "  ⚠ LibreChat login 실패 — 키 수동 입력 필요" >&2; return 0; }

  local val_json; val_json=$(jq -n --arg k "$key" '{apiKey:$k}' | jq -c .)
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${lc_url}/api/keys" \
    -H "Authorization: Bearer $jwt" -H 'Content-Type: application/json' \
    -d "$(jq -n --arg n LiteLLM --arg v "$val_json" '{name:$n, value:$v}')")
  [[ "$code" == "201" ]] && echo "LiteLLM 키 LibreChat 자동 등록" \
    || echo "  ⚠ 키 자동 등록 실패 (HTTP $code)" >&2
}

create_default_agent_for_user() {
  local email="$1"
  local mu mp; mu=$(env_get MONGO_ROOT_USER); mp=$(env_get MONGO_ROOT_PASSWORD)
  [[ -z "$mu" || -z "$mp" ]] && { echo "  ⚠ MONGO_ROOT_USER/PASSWORD 누락 — agent 건너뜀" >&2; return 0; }
  docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^chat-mongodb$' \
    || { echo "  ⚠ chat-mongodb 미실행 — agent 건너뜀" >&2; return 0; }

  local gemma_model qwen35b_model qwen9b_model gptoss20b_model gptoss120b_model
  if gpu_supports_gemma4; then gemma_model=ollama/gemma4:26b
  else                         gemma_model=ollama/gemma3:27b; fi
  qwen35b_model=ollama/qwen3.5:35b
  qwen9b_model=ollama/qwen3.5:9b
  gptoss20b_model=openai/gpt-oss:20b
  gptoss120b_model=openai/gpt-oss:120b
  local opus47_model=anthropic/claude-opus-4.7
  local opus46_model=anthropic/claude-opus-4.6
  local sonnet46_model=anthropic/claude-sonnet-4.6
  local gpt55_model=openai/gpt-5.5
  local gemma_name="채팅 (${gemma_model#*/})"
  local qwen35b_name="채팅 (${qwen35b_model#*/})"
  local qwen9b_name="채팅 (${qwen9b_model#*/})"
  local gptoss20b_name="채팅 (${gptoss20b_model#*/})"
  local gptoss120b_name="채팅 (${gptoss120b_model#*/})"
  local opus47_name="채팅 (${opus47_model#*/})"
  local opus46_name="채팅 (${opus46_model#*/})"
  local sonnet46_name="채팅 (${sonnet46_model#*/})"
  local gpt55_name="채팅 (${gpt55_model#*/})"
  local include_120b=0; gpu_supports_120b && include_120b=1

  # 이미지 에이전트는 qwen3.5:35b 드라이버 — tool-calling 안정성 위해.
  local img_llm="$qwen35b_model"
  local img_tools="['image-generation','file_search']"

  local result
  result=$(docker exec chat-mongodb mongosh --quiet \
    -u "$mu" -p "$mp" --authenticationDatabase admin LibreChat --eval "
      var u = db.users.findOne({email: '$email'}, {_id:1});
      if (!u) { print('NO_USER'); quit(); }
      var now = new Date();
      var roleA = db.accessroles.findOne({accessRoleId: 'agent_owner'});
      var roleR = db.accessroles.findOne({accessRoleId: 'remoteAgent_owner'});
      function rid(p) { return p + Math.random().toString(36).slice(2,14) + Math.random().toString(36).slice(2,12); }
      function createAgent(name, model, tools, instructions) {
        instructions = instructions || '';
        var ex = db.agents.findOne({author: u._id, name: name}, {_id:1, id:1});
        if (ex) { print('EXISTS:' + name + ':' + ex.id); return ex.id; }
        var id = rid('agent_');
        var ver = { name:name, description:'', instructions:instructions, provider:'LiteLLM', model:model, tools:tools,
                    artifacts:'', category:'general', support_contact:{name:'',email:''},
                    agent_ids:[], edges:[], conversation_starters:[], is_promoted:false,
                    mcpServerNames:[], tool_options:{}, createdAt:now, updatedAt:now };
        var ins = db.agents.insertOne({
          id:id, name:name, description:'', instructions:instructions, provider:'LiteLLM', model:model, artifacts:'',
          tools:tools, tool_kwargs:[], author:u._id, agent_ids:[], edges:[], conversation_starters:[],
          versions:[ver], category:'general', support_contact:{name:'',email:''},
          is_promoted:false, mcpServerNames:[], tool_options:{},
          end_after_tools:false, hide_sequential_outputs:false, createdAt:now, updatedAt:now, __v:0
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
      var gemmaId   = createAgent('${gemma_name}',   '${gemma_model}',   ['execute_code','file_search','web_search']);
      var qwen35bId = createAgent('${qwen35b_name}', '${qwen35b_model}', ['execute_code','file_search','web_search']);
      var qwen9bId  = createAgent('${qwen9b_name}',  '${qwen9b_model}',  ['execute_code','file_search','web_search']);
      createAgent('${gptoss20b_name}', '${gptoss20b_model}', ['execute_code','file_search','web_search']);
      if (${include_120b}) {
        createAgent('${gptoss120b_name}', '${gptoss120b_model}', ['execute_code','file_search','web_search']);
      }
      createAgent('${opus47_name}',   '${opus47_model}',   ['execute_code','file_search','web_search']);
      createAgent('${opus46_name}',   '${opus46_model}',   ['execute_code','file_search','web_search']);
      createAgent('${sonnet46_name}', '${sonnet46_model}', ['execute_code','file_search','web_search']);
      createAgent('${gpt55_name}',    '${gpt55_model}',    ['execute_code','file_search','web_search']);

      // 이미지 에이전트별 system instructions — LibreChat SD 툴 패치된 model 인자 강제.
      function imgInstr(alias, blurb) {
        return 'You are an image generation specialist. When the user asks for an image, ALWAYS call the image-generation tool with model=\"' + alias + '\". '
             + 'Do NOT use any other model. ' + blurb
             + ' Generate detailed, descriptive prompts (>=7 keywords) and reasonable negative_prompts.';
      }
      createAgent('이미지 (sdxl)',         '${img_llm}', ${img_tools}, imgInstr('sdxl',         'SDXL is fast, photorealistic baseline.'));
      createAgent('이미지 (qwen-image)',   '${img_llm}', ${img_tools}, imgInstr('qwen-image',   'Qwen-Image excels at Asian text/scenes.'));
      createAgent('이미지 (flux-dev)',     '${img_llm}', ${img_tools}, imgInstr('flux-dev',     'Flux-dev is highest quality, slower.'));
      createAgent('이미지 (flux-schnell)', '${img_llm}', ${img_tools}, imgInstr('flux-schnell', 'Flux-schnell is fast (4 steps), good for iteration.'));
      var defId = qwen35bId || gemmaId || qwen9bId;
      var defTitle = qwen35bId ? '${qwen35b_name}' : (gemmaId ? '${gemma_name}' : '${qwen9b_name}');
      if (defId) {
        var us = u._id.toString();
        db.presets.updateMany({user:us, defaultPreset:true}, {\$unset:{defaultPreset:'', order:''}});
        db.presets.insertOne({
          presetId: rid('preset_'), title:defTitle, user:us, defaultPreset:true, order:1,
          endpoint:'agents', agent_id:defId, createdAt:now, updatedAt:now, __v:0
        });
        print('DEFAULT_PRESET:' + defTitle);
      }
    " 2>&1)

  if echo "$result" | grep -q NO_USER; then
    echo "  ⚠ default agent 실패 — LibreChat user _id 없음" >&2; return 0
  fi
  while IFS= read -r line; do
    case "$line" in
      CREATED:*)        echo "agent: ${line#CREATED:}" ;;
      EXISTS:*)         echo "agent (exists): ${line#EXISTS:}" ;;
      DEFAULT_PRESET:*) echo "default preset: ${line#DEFAULT_PRESET:}" ;;
      WARN_NO_ACL_ROLES) echo "  ⚠ ACL roles 없음 — 'My Agents' 미노출 가능" >&2 ;;
    esac
  done <<< "$result"
}

cmd_user_list() {
  litellm_get "/user/list" | jq -r '
    (if type == "array" then . else (.users // .data // []) end)[]
    | "\(.user_id)\t\(.user_role)\tspend:\(.spend // 0)$"'
}

cmd_user_delete() {
  local id=""
  while [[ $# -gt 0 ]]; do case "$1" in --id) id="$2"; shift 2 ;; *) shift ;; esac; done
  require_arg --id "$id"
  litellm_post "/user/delete" "{\"user_ids\":[\"$id\"]}" | jq .
}

cmd_key_issue() {
  local user_id="" team_alias="default" key_alias="" budget=9999 service=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --user)    user_id="$2";    shift 2 ;;
      --team)    team_alias="$2"; shift 2 ;;
      --alias)   key_alias="$2";  shift 2 ;;
      --budget)  budget="$2";     shift 2 ;;
      --service) service="$2";    shift 2 ;;
      *) echo "Unknown: $1" >&2; exit 1 ;;
    esac
  done

  if [[ -n "$service" ]]; then
    local payload; payload=$(jq -n --arg a "${service}-service-key" --argjson b "$budget" \
      '{key_alias:$a, max_budget:$b, budget_duration:"1mo", user_role:"internal_user"}')
    local result; result=$(litellm_post "/key/generate" "$payload")
    local key; key=$(echo "$result" | jq -r '.key')
    echo "service key: $service"; echo "KEY: $key"
    if [[ "$service" == "librechat" ]]; then
      env_set LITELLM_SERVICE_KEY "$key"
      env_set RAG_OPENAI_API_KEY  "$key"
    fi
    return 0
  fi

  require_arg "--user or --service" "$user_id"
  require_email --user "$user_id"

  local team_id=""
  if [[ -n "$team_alias" ]]; then
    team_id=$(team_id_by_alias "$team_alias")
    [[ -z "$team_id" ]] && { echo "team not found: $team_alias" >&2; exit 1; }
  fi
  [[ -z "$key_alias" ]] && key_alias="${user_id%%@*}-key"

  local payload; payload=$(jq -n \
    --arg u "$user_id" --arg t "$team_id" --arg a "$key_alias" --argjson b "$budget" \
    '{user_id:$u, team_id:$t, key_alias:$a, max_budget:$b, budget_duration:"1mo"}')
  local result; result=$(litellm_post "/key/generate" "$payload")
  echo "key: $key_alias"
  echo "KEY: $(echo "$result" | jq -r '.key')"
}

cmd_key_list() {
  local user_id=""
  while [[ $# -gt 0 ]]; do case "$1" in --user) user_id="$2"; shift 2 ;; *) shift ;; esac; done
  local ep="/key/list"; [[ -n "$user_id" ]] && ep+="?user_id=${user_id}"
  litellm_get "$ep" | jq -r '.keys[] | "\(.key_alias // "unnamed")\t\(.key[0:20])...\tbudget:\(.max_budget)$\tspend:\(.spend // 0)$"'
}

cmd_key_revoke() {
  local key=""
  while [[ $# -gt 0 ]]; do case "$1" in --key) key="$2"; shift 2 ;; *) shift ;; esac; done
  require_arg --key "$key"
  litellm_post "/key/delete" "{\"keys\":[\"$key\"]}" | jq .
}

(( $# < 2 )) && usage
resource="$1"; action="$2"; shift 2
case "${resource}/${action}" in
  team/create)  cmd_team_create "$@" ;;
  team/list)    cmd_team_list ;;
  team/delete)  cmd_team_delete "$@" ;;
  user/create)  cmd_user_create "$@" ;;
  user/list)    cmd_user_list ;;
  user/delete)  cmd_user_delete "$@" ;;
  key/issue)    cmd_key_issue "$@" ;;
  key/list)     cmd_key_list "$@" ;;
  key/revoke)   cmd_key_revoke "$@" ;;
  *)            echo "Unknown: ${resource} ${action}" >&2; usage ;;
esac
