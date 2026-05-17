#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<EOF
Usage: manage.sh <resource> <action> [opts]

team   create  --alias <n> [--budget --duration --tpm --rpm --models a,b,c]
       list / delete --id <team_id>
       sync                                                ← 카탈로그 변경 후 모든 팀 model allowlist 재동기화

user   create  --id <email> [--team <alias>] [--budget]
               [--name <n> --username <u> --password <p>]   ← LibreChat 풀 프로비저닝
       list / delete --id <email>

key    issue   --user <email> [--team] [--alias] [--budget]
       issue   --service <name> [--budget]                  ← service-account
       list   [--user <email>]
       revoke --key <sk-...>

agent  sync                                                ← 카탈로그 변경 후 모든 유저 에이전트 upsert + 레거시 마이그레이션
EOF
  exit 1
}

require_arg() { [[ -n "$2" ]] || { echo "$1 required" >&2; exit 1; }; }
require_email() {
  [[ "$2" =~ ^[^@]+@[^@]+\.[^@]+$ ]] || { echo "$1 must be email: $2" >&2; exit 1; }
}
# flag 뒤 값 누락 시 set -u 안전 종료. 사용: `--foo) need_val "$@"; foo="$2"; shift 2 ;;`
need_val() { [[ -n "${2:-}" ]] || { echo "$1 requires a value" >&2; exit 1; }; }

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
    if jq --argjson r "$result" '. += [$r]' "$cache" > "${cache}.tmp"; then
      mv "${cache}.tmp" "$cache"
    else
      rm -f "${cache}.tmp"
      echo "  ⚠ teams.json 업데이트 실패 — jq 에러" >&2
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
# 이거 안 돌리면 기존 팀이 새 모델을 401 거부.
cmd_team_sync() {
  local models; models="$(litellm_chat_models_csv)"
  [[ -z "$models" ]] && { echo "litellm chat 모델 0건 — gen-litellm-config + restart 먼저" >&2; exit 1; }
  local models_json; models_json=$(echo "$models" | tr ',' '\n' | jq -R . | jq -s .)
  local teams; teams=$(litellm_get "/team/list")
  local n=0 ok=0 fail=0
  while IFS=$'\t' read -r tid talias; do
    [[ -z "$tid" ]] && continue
    n=$((n+1))
    local payload; payload=$(jq -n --arg id "$tid" --argjson m "$models_json" '{team_id:$id, models:$m}')
    if litellm_post "/team/update" "$payload" >/dev/null 2>&1; then
      ok=$((ok+1)); echo "  ✓ $talias ($tid)"
    else
      fail=$((fail+1)); echo "  ✗ $talias ($tid)" >&2
    fi
  done < <(echo "$teams" | jq -r '.[] | "\(.team_id)\t\(.team_alias)"')
  echo "team/sync: $ok/$n updated, $fail failed (models: $(echo "$models" | tr ',' '\n' | wc -l))"
}

cmd_user_create() {
  local user_id="" team_alias="default" budget=9999
  local lc_name="" lc_username="" lc_password=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --id)       need_val "$@"; user_id="$2";     shift 2 ;;
      --team)     need_val "$@"; team_alias="$2";  shift 2 ;;
      --budget)   need_val "$@"; budget="$2";      shift 2 ;;
      --name)     need_val "$@"; lc_name="$2";     shift 2 ;;
      --username) need_val "$@"; lc_username="$2"; shift 2 ;;
      --password) need_val "$@"; lc_password="$2"; shift 2 ;;
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

  # 모델 1개당 에이전트 1개. 이름 prefix = 능력 요약 (Text / Text+Code / Text+Image / Text+Image+Code).
  # image-generation backend: ollama→ComfyUI, openai→gpt-image-2 (OR), google→nano-banana (OR),
  # anthropic→자사 image API 없음 → 툴 자체 제외.
  local pulled; pulled="$(ollama_union_models 2>/dev/null || true)"
  local agent_specs='[]'
  local m tag

  # external — OpenRouter 키 필수.
  if has_openrouter; then
    for m in "${OPENAI_MODELS[@]}"; do
      local prefix="Text + Image"
      case "$m" in
        gpt-5|gpt-5.5) prefix="Text + Image + Code" ;;
      esac
      agent_specs=$(jq -c --arg n "$prefix ($m)" --arg mm "openai/$m" \
        '. + [{name:$n, model:$mm, kind:"external"}]' <<< "$agent_specs")
    done
    for m in "${ANTHROPIC_MODELS[@]}"; do
      local prefix="Text"
      case "$m" in
        claude-opus-*|claude-sonnet-*) prefix="Text + Code" ;;
      esac
      agent_specs=$(jq -c --arg n "$prefix ($m)" --arg mm "anthropic/$m" \
        '. + [{name:$n, model:$mm, kind:"external"}]' <<< "$agent_specs")
    done
    for m in "${GOOGLE_MODELS[@]}"; do
      agent_specs=$(jq -c --arg n "Text + Image ($m)" --arg mm "google/$m" \
        '. + [{name:$n, model:$mm, kind:"external"}]' <<< "$agent_specs")
    done
  fi

  # local — ollama union 보유 모델만. prefix 는 모델 특성 + TOOL_EXCLUDE 정책 반영.
  for m in "${OLLAMA_CHAT_CATALOG[@]}"; do
    tag="$m"; [[ "$tag" != *:* ]] && tag="${tag}:latest"
    grep -qxF "$tag" <<<"$pulled" || continue
    local prefix="Text + Image"
    case "$m" in
      qwen3-coder-next:*)         prefix="Text + Image + Code" ;;
      qwen3.5:9b|llama3.1:8b)     prefix="Text" ;;
    esac
    agent_specs=$(jq -c --arg n "$prefix ($m)" --arg mm "ollama/$m" \
      '. + [{name:$n, model:$mm, kind:"local"}]' <<< "$agent_specs")
  done

  # default 모델 (priority 첫 매치). union 비었으면 빈 문자열 — 그 경우 첫 spec 으로 fallback.
  local default_model; default_model="$(ollama_default_chat_model 2>/dev/null || true)"
  local default_name=""
  [[ -n "$default_model" ]] && default_name="Text + Image ($default_model)"

  local email_js; email_js=$(jq -Rn --arg e "$email" '$e')
  local result
  result=$(docker exec chat-mongodb mongosh --quiet \
    -u "$mu" -p "$mp" --authenticationDatabase admin LibreChat --eval "
      var u = db.users.findOne({email: $email_js}, {_id:1});
      if (!u) { print('NO_USER'); quit(); }
      var now = new Date();
      var roleA = db.accessroles.findOne({accessRoleId: 'agent_owner'});
      var roleR = db.accessroles.findOne({accessRoleId: 'remoteAgent_owner'});

      // 공통 MCP 도구 (모든 에이전트). sys__all__sys = LibreChat 의 mcp_all + _mcp_ delimiter.
      var MCP_COMMON = [
        'sys__all__sys_mcp_fetch_url',     // URL fetch + Markdown 변환
        'sys__all__sys_mcp_time',          // 현재 시간 / 타임존 변환
        'sys__all__sys_mcp_litellm_usage', // 본인 토큰 사용량/예산 (my_usage, budget_status)
      ];
      // 모델 크기별 분기. 작은 모델은 schema 부담을 피해 calculator 만, scholar/arxiv 제외.
      var MCP_MATH_BIG    = ['sys__all__sys_mcp_math'];              // sympy 173 tools
      var MCP_MATH_SMALL  = ['sys__all__sys_mcp_calculator'];        // 사칙연산 16 tools
      var MCP_SCHOLAR_BIG = ['sys__all__sys_mcp_semantic_scholar'];  // 33 tools
      var MCP_ARXIV_BIG   = ['sys__all__sys_mcp_arxiv'];             // 8 tools

      // 작은 모델 명단 (canonical model id — provider prefix 제거된 형태).
      var SMALL_MODELS = new Set([
        'qwen3.5:9b', 'llama3.1:8b',
        'gpt-5-mini', 'gpt-5-nano', 'claude-haiku-4.5', 'gemini-2.5-flash',
      ]);
      function mcpToolsFor(model) {
        var tag = model.replace(/^[^/]+\\//, '');
        var isSmall = SMALL_MODELS.has(tag);
        var math    = isSmall ? MCP_MATH_SMALL : MCP_MATH_BIG;
        var scholar = isSmall ? []             : MCP_SCHOLAR_BIG;
        var arxiv   = isSmall ? []             : MCP_ARXIV_BIG;
        return MCP_COMMON.concat(math).concat(scholar).concat(arxiv);
      }

      // 모든 에이전트의 base 빌트인. 분리는 TOOL_EXCLUDE / EXT_IMAGE_FOR_PROVIDER 가 처리.
      var BUILTIN_BASE = ['execute_code','file_search','web_search','image-generation'];

      // 모델별 빌트인 툴 제외 (작은 ollama 모델 정책).
      var TOOL_EXCLUDE = {
        'qwen3.5:9b':              ['execute_code', 'image-generation'],
        'llama3.1:8b':             ['execute_code', 'image-generation'],
      };

      // 외부 provider → 자사 image 모델. anthropic 누락 = image-generation 자동 제외.
      var EXT_IMAGE_FOR_PROVIDER = {
        'openai': 'gpt-image-2',
        'google': 'nano-banana',
      };

      // local 에이전트는 ComfyUI 로 갈 모델만 안내 — shim 의 MODEL_ALIASES.
      var IMAGE_INSTR_LOCAL =
        '\n\nFor image / picture / photo / diagram / illustration requests, call image-generation directly. ' +
        'Pick the model arg by intent:\n' +
        '  - model=\"flux-schnell\" — fast / draft / quick iteration (default)\n' +
        '  - model=\"flux-dev\" — when high quality is requested\n' +
        '  - model=\"qwen-image\" — text-in-image, Asian-language text, or complex multi-element composition\n' +
        'Required args: prompt (>=7 visual keywords for subject, style, lighting), negative_prompt (>=7 keywords).';

      // external — provider 자사 image 모델 한 가지만 안내.
      function imageInstrExt(imageModel) {
        return '\n\nFor image / picture / photo / diagram / illustration requests, call image-generation directly. ' +
               'Use model=\"' + imageModel + '\" (the only image model wired for this agent). ' +
               'Required args: prompt (>=7 visual keywords for subject, style, lighting), negative_prompt (>=7 keywords).';
      }

      function builtinFor(spec) {
        var tag  = spec.model.replace(/^[^/]+\\//, '');
        var skip = (TOOL_EXCLUDE[tag] || []).slice();
        // external 중 image 매핑 없는 provider (anthropic) 는 image-generation drop.
        if (spec.kind !== 'local') {
          var provider = spec.model.split('/')[0];
          if (!EXT_IMAGE_FOR_PROVIDER[provider]) {
            skip.push('image-generation');
          }
        }
        return BUILTIN_BASE.filter(function(t){ return skip.indexOf(t) === -1; });
      }

      function instructionsFor(spec, tools) {
        var caps = [];
        if (tools.indexOf('file_search')      !== -1) caps.push('file_search (RAG over user files)');
        if (tools.indexOf('web_search')       !== -1) caps.push('web_search');
        if (tools.indexOf('execute_code')     !== -1) caps.push('code execution');
        if (tools.indexOf('image-generation') !== -1) caps.push('image-generation');
        var instr = 'You are a helpful assistant with ' + caps.join(', ') + ' tools.';
        if (tools.indexOf('image-generation') !== -1) {
          if (spec.kind === 'local') {
            instr += IMAGE_INSTR_LOCAL;
          } else {
            var provider = spec.model.split('/')[0];
            var img = EXT_IMAGE_FOR_PROVIDER[provider];
            if (img) instr += imageInstrExt(img);
          }
        }
        return instr;
      }

      function rid(p) { return p + Math.random().toString(36).slice(2,14) + Math.random().toString(36).slice(2,12); }

      // 같은 이름이 있으면 tools/instructions/model 을 root + versions 양쪽에 동기화. 없으면 신규 insert.
      function upsertAgent(name, model, tools, instructions) {
        var ex = db.agents.findOne({author: u._id, name: name});
        if (ex) {
          var versions = Array.isArray(ex.versions) ? ex.versions : [];
          var vs = versions.map(function(v){
            v.tools = tools; v.instructions = instructions; v.model = model; v.updatedAt = now; return v;
          });
          db.agents.updateOne({_id: ex._id}, {\$set: {
            tools: tools, instructions: instructions, model: model,
            versions: vs, updatedAt: now
          }});
          print('UPDATED:' + name + ':' + ex.id);
          return ex.id;
        }
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

      function deleteAgent(doc) {
        db.aclentries.deleteMany({resourceId: doc._id});
        db.agents.deleteOne({_id: doc._id});
        print('DELETED:' + doc.name + ':' + doc.id);
      }

      var specs = ${agent_specs};
      var canonNames = specs.map(function(s){ return s.name; });

      function targetNameFor(d) {
        // canonical name prefix 가 가변이라 '(modelTag)' suffix 로 매칭.
        if (!d.model || !/^(ollama|openai|anthropic|google)\\//.test(d.model)) return null;
        var modelTag = d.model.replace(/^[^/]+\\//, '');
        var suffix = '(' + modelTag + ')';
        for (var i = 0; i < canonNames.length; i++) {
          if (canonNames[i].slice(-suffix.length) === suffix) return canonNames[i];
        }
        return null;
      }

      // 관리 에이전트만 동기화 — 사용자 수동 생성 에이전트는 건드리지 않음.
      // target 이름 매치 → in-place rename (preset agent_id 보존). 매치 없음 (모델
      // 단종 / 키 없음) → 삭제. 'Image (X)' 레거시 이름 → 삭제 (inline 라우팅으로 대체됨).
      var managed = db.agents.find({author: u._id,
        \$or: [
          {model: /^(ollama|openai|anthropic|google)\\//},
          {name: /^Image \\(/}
        ]
      }).toArray();
      managed.forEach(function(d){
        if (canonNames.indexOf(d.name) !== -1) return;  // 이미 맞는 이름

        if (/^Image \\(/.test(d.name)) { deleteAgent(d); return; }

        var target = targetNameFor(d);
        if (target) {
          var clash = db.agents.findOne({author: u._id, name: target, _id: {\$ne: d._id}});
          if (clash) { deleteAgent(d); print('LEGACY_DEDUP:' + d.name); return; }
          db.agents.updateOne({_id: d._id}, {\$set: {name: target, updatedAt: now}});
          if (Array.isArray(d.versions)) {
            var vs2 = d.versions.map(function(v){ v.name = target; return v; });
            db.agents.updateOne({_id: d._id}, {\$set: {versions: vs2}});
          }
          print('RENAMED:' + d.name + ' -> ' + target);
        } else {
          deleteAgent(d);
        }
      });

      // 카탈로그 upsert (kind 별 builtin + 모델 크기별 MCP).
      specs.forEach(function(s){
        var builtin = builtinFor(s);
        upsertAgent(s.name, s.model, builtin.concat(mcpToolsFor(s.model)), instructionsFor(s, builtin));
      });

      // default preset — priority 첫 매치, 없으면 첫 spec.
      var defaultName = '${default_name}' || (specs[0] ? specs[0].name : null);
      var defAgent = defaultName ? db.agents.findOne({author:u._id, name:defaultName}, {id:1}) : null;
      var us = u._id.toString();
      var existingDefault = db.presets.findOne({user:us, defaultPreset:true});
      var liveAgentIds = db.agents.find({author:u._id}, {id:1}).toArray().map(function(a){return a.id;});
      if (existingDefault) {
        if (existingDefault.agent_id && liveAgentIds.indexOf(existingDefault.agent_id) === -1 && defAgent) {
          // default 가 가리키던 agent 가 사라짐 → 새 default 로 재할당.
          db.presets.updateOne({_id: existingDefault._id},
            {\$set: {agent_id: defAgent.id, title: defaultName, updatedAt: now}});
          print('DEFAULT_PRESET_REASSIGNED:' + defaultName);
        } else {
          print('DEFAULT_PRESET_EXISTS:' + (existingDefault.title || ''));
        }
      } else if (defAgent) {
        db.presets.insertOne({
          presetId: rid('preset_'), title:defaultName, user:us, defaultPreset:true, order:1,
          endpoint:'agents', agent_id:defAgent.id, createdAt:now, updatedAt:now, __v:0
        });
        print('DEFAULT_PRESET:' + defaultName);
      }
    " 2>&1)

  if echo "$result" | grep -q NO_USER; then
    echo "  ⚠ default agent 실패 — LibreChat user _id 없음" >&2; return 0
  fi
  while IFS= read -r line; do
    case "$line" in
      CREATED:*)                    echo "agent created: ${line#CREATED:}" ;;
      UPDATED:*)                    echo "agent updated: ${line#UPDATED:}" ;;
      RENAMED:*)                    echo "agent renamed: ${line#RENAMED:}" ;;
      DELETED:*)                    echo "agent deleted: ${line#DELETED:}" ;;
      LEGACY_DEDUP:*)               echo "legacy dedup: ${line#LEGACY_DEDUP:}" ;;
      DEFAULT_PRESET:*)             echo "default preset: ${line#DEFAULT_PRESET:}" ;;
      DEFAULT_PRESET_REASSIGNED:*)  echo "default preset reassigned: ${line#DEFAULT_PRESET_REASSIGNED:}" ;;
      DEFAULT_PRESET_EXISTS:*)      ;;  # 멱등 — 조용히 무시
      WARN_NO_ACL_ROLES)            echo "  ⚠ ACL roles 없음 — 'My Agents' 미노출 가능" >&2 ;;
    esac
  done <<< "$result"
}

cmd_agent_sync() {
  local mu mp; mu=$(env_get MONGO_ROOT_USER); mp=$(env_get MONGO_ROOT_PASSWORD)
  [[ -z "$mu" || -z "$mp" ]] && { echo "MONGO_ROOT_USER/PASSWORD 누락" >&2; exit 1; }
  docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^chat-mongodb$' \
    || { echo "chat-mongodb 미실행" >&2; exit 1; }

  local emails
  emails=$(docker exec chat-mongodb mongosh --quiet \
    -u "$mu" -p "$mp" --authenticationDatabase admin LibreChat --eval \
    'db.users.find({}, {email:1, _id:0}).forEach(function(u){ if(u.email) print(u.email); })' 2>/dev/null)
  [[ -z "$emails" ]] && { echo "대상 유저 없음" >&2; return 0; }

  while IFS= read -r email; do
    [[ -z "$email" ]] && continue
    echo "== $email"
    create_default_agent_for_user "$email"
  done <<< "$emails"
}

cmd_user_list() {
  litellm_get "/user/list" | jq -r '
    (if type == "array" then . else (.users // .data // []) end)[]
    | "\(.user_id)\t\(.user_role)\tspend:\(.spend // 0)$"'
}

cmd_user_delete() {
  local id=""
  while [[ $# -gt 0 ]]; do case "$1" in --id) need_val "$@"; id="$2"; shift 2 ;; *) shift ;; esac; done
  require_arg --id "$id"
  litellm_post "/user/delete" "{\"user_ids\":[\"$id\"]}" | jq .
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
  while [[ $# -gt 0 ]]; do case "$1" in --user) need_val "$@"; user_id="$2"; shift 2 ;; *) shift ;; esac; done
  local ep="/key/list"; [[ -n "$user_id" ]] && ep+="?user_id=${user_id}"
  litellm_get "$ep" | jq -r '.keys[] | "\(.key_alias // "unnamed")\t\(.key[0:20])...\tbudget:\(.max_budget)$\tspend:\(.spend // 0)$"'
}

cmd_key_revoke() {
  local key=""
  while [[ $# -gt 0 ]]; do case "$1" in --key) need_val "$@"; key="$2"; shift 2 ;; *) shift ;; esac; done
  require_arg --key "$key"
  litellm_post "/key/delete" "{\"keys\":[\"$key\"]}" | jq .
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
  user/delete)  cmd_user_delete "$@" ;;
  key/issue)    cmd_key_issue "$@" ;;
  key/list)     cmd_key_list "$@" ;;
  key/revoke)   cmd_key_revoke "$@" ;;
  agent/sync)   cmd_agent_sync "$@" ;;
  *)            echo "Unknown: ${resource} ${action}" >&2; usage ;;
esac
