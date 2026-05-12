#!/bin/bash
# KloudChat LiteLLM CLI management tool.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

usage() {
  cat <<EOF
Usage: manage.sh <resource> <action> [options]

Resources:
  team    team management
  user    user management
  key     virtual key management

team actions:
  create  --alias <name> --budget <amount> --duration <window> --tpm <TPM> --rpm <RPM> --models <a,b,c>
  list
  delete  --id <team_id>

user actions:
  create  --id <email> [--team <team_alias>] [--budget <amount>]   (default team: default)
          [--name <name> --username <username> --password <pw>]    ← full provisioning:
                                                                     LibreChat user + auto key
  list
  delete  --id <email>

key actions:
  issue   --user <email> [--team <team_alias>] [--alias <name>] [--budget <amount>]
                                                                  (default team: default)
  issue   --service <name> [--budget <amount>]                    (service-account key)
  list    [--user <email>]
  revoke  --key <sk-...>

Examples:
  manage.sh team create --alias research --budget 100 --models "ollama/*"
  manage.sh user create --id alice@lab.ac.kr --team research --budget 20
  manage.sh user create --id alice@example.com --name 'Alice' --username alice --password 'pw12345' --budget 100
                                                                  # LibreChat user + LiteLLM user + key in one shot
  manage.sh key issue --user alice@lab.ac.kr --team research --alias alice-key
  manage.sh key issue --service librechat --budget 9999
  manage.sh key list --user alice@lab.ac.kr
EOF
  exit 1
}

# ---- team ----
cmd_team_create() {
  # Use an explicit model list rather than the wildcard (ollama/*) — LiteLLM
  # cannot map the wildcard back to model_list when responding with a user
  # key, so only ollama/llama2 ends up exposed.
  local alias="" budget=9999 duration="1mo" tpm=100000 rpm=500
  local models="ollama/qwen3.5:9b,ollama/qwen3.5:35b,ollama/gemma4:26b,ollama/qwen3-coder-next:q4_K_M,ollama/qwen3-coder-next:q8_0"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --alias)    alias="$2";    shift 2 ;;
      --budget)   budget="$2";   shift 2 ;;
      --duration) duration="$2"; shift 2 ;;
      --tpm)      tpm="$2";      shift 2 ;;
      --rpm)      rpm="$2";      shift 2 ;;
      --models)   models="$2";   shift 2 ;;
      *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
  done
  [[ -z "$alias" ]] && { echo "--alias is required" >&2; exit 1; }

  # Reuse an existing team with the same alias.
  local existing
  existing=$(team_id_by_alias "$alias")
  if [[ -n "$existing" ]]; then
    echo "team already exists: $alias (id=$existing)"
    echo "$existing"
    return 0
  fi

  # Convert the comma-separated models list to a JSON array.
  local models_json
  models_json=$(echo "$models" | tr ',' '\n' | jq -R . | jq -s .)

  local payload result team_id
  payload=$(jq -n \
    --arg a "$alias" \
    --argjson b "$budget" \
    --arg d "$duration" \
    --argjson tpm "$tpm" \
    --argjson rpm "$rpm" \
    --argjson m "$models_json" \
    '{team_alias:$a, max_budget:$b, budget_duration:$d, tpm_limit:$tpm, rpm_limit:$rpm, models:$m}')

  result=$(litellm_post "/team/new" "$payload")
  team_id=$(echo "$result" | jq -r '.team_id')

  # Append to local cache.
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
  while [[ $# -gt 0 ]]; do
    case "$1" in --id) id="$2"; shift 2 ;; *) shift ;; esac
  done
  [[ -z "$id" ]] && { echo "--id is required" >&2; exit 1; }
  litellm_post "/team/delete" "{\"team_ids\":[\"$id\"]}" | jq .
}

# ---- user ----
cmd_user_create() {
  local user_id="" team_alias="default" budget=9999
  local lc_name="" lc_username="" lc_password=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --id)        user_id="$2";     shift 2 ;;
      --team)      team_alias="$2";  shift 2 ;;
      --budget)    budget="$2";      shift 2 ;;
      --name)      lc_name="$2";     shift 2 ;;
      --username)  lc_username="$2"; shift 2 ;;
      --password)  lc_password="$2"; shift 2 ;;
      *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
  done
  [[ -z "$user_id" ]] && { echo "--id is required" >&2; exit 1; }
  [[ "$user_id" =~ ^[^@]+@[^@]+\.[^@]+$ ]] || { echo "error: --id must be an email address: $user_id" >&2; exit 1; }

  # --name/--username/--password must be all-or-nothing (full provisioning mode).
  local lc_count=0
  for v in "$lc_name" "$lc_username" "$lc_password"; do
    [[ -n "$v" ]] && lc_count=$((lc_count+1))
  done
  if [[ $lc_count -ne 0 && $lc_count -ne 3 ]]; then
    echo "error: --name, --username, and --password must be supplied together (LibreChat user creation)" >&2
    exit 1
  fi
  local full_provision=0
  [[ $lc_count -eq 3 ]] && full_provision=1

  # Create the LibreChat user (full provisioning mode).
  if [[ $full_provision -eq 1 ]]; then
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^LibreChat$'; then
      echo "Creating LibreChat user..."
      local lc_output
      lc_output=$(docker exec -i LibreChat npm run create-user -- "$user_id" "$lc_name" "$lc_username" "$lc_password" <<< 'y' 2>&1 | tail -10)
      if echo "$lc_output" | grep -q "User created successfully"; then
        echo "  ✓ LibreChat user: $user_id ($lc_username)"
      else
        echo "  ⚠ LibreChat user creation failed (may already exist). Last output:" >&2
        echo "$lc_output" | tail -3 | sed 's/^/    /' >&2
      fi
    else
      echo "  ⚠ LibreChat container not running — skipping LibreChat step" >&2
    fi
  fi

  # Create the LiteLLM user.
  local payload result
  payload=$(jq -n --arg u "$user_id" '{user_id:$u, user_role:"internal_user"}')
  result=$(litellm_post "/user/new" "$payload")
  echo "user created: $user_id"

  # Add to team.
  if [[ -n "$team_alias" ]]; then
    local team_id
    team_id=$(team_id_by_alias "$team_alias")
    if [[ -z "$team_id" ]]; then
      echo "team not found: $team_alias" >&2; exit 1
    fi
    payload=$(jq -n \
      --arg tid "$team_id" \
      --arg uid "$user_id" \
      --argjson b "$budget" \
      '{team_id:$tid, max_budget_in_team:$b, member:{role:"user", user_id:$uid}}')
    litellm_post "/team/member_add" "$payload" > /dev/null
    echo "added to team: $team_alias (per-team budget \$${budget}/month)"
  fi

  # Auto-issue a key and register it in LibreChat (full provisioning mode).
  if [[ $full_provision -eq 1 ]]; then
    local key_alias="${lc_username}-key"
    local key_output issued_key
    key_output=$(cmd_key_issue --user "$user_id" --team "$team_alias" --alias "$key_alias" 2>&1)
    echo "$key_output"
    issued_key=$(echo "$key_output" | grep -oE 'sk-[A-Za-z0-9_-]+' | head -1)

    # Pre-register the key in LibreChat's keys collection so the user does
    # not have to paste sk-... on their first chat.
    if [[ -n "$issued_key" ]]; then
      register_litellm_key_for_librechat_user "$user_id" "$lc_password" "$issued_key"
    fi
  fi

  # Default agent (full provisioning mode + LibreChat/Mongo available).
  if [[ $full_provision -eq 1 ]]; then
    create_default_agent_for_user "$user_id"
  fi
}

# Store the user's endpoint key in LibreChat's keys collection up-front so
# the first chat works without a key-paste step.
# Uses REST 'PUT /api/keys'; LibreChat encrypts the value with its encryptV2.
register_litellm_key_for_librechat_user() {
  local user_email="$1" user_password="$2" api_key="$3"
  local lc_url="${LIBRECHAT_URL:-http://localhost:8080}"

  local jwt
  jwt=$(curl -s -X POST "${lc_url}/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg e "$user_email" --arg p "$user_password" '{email:$e, password:$p}')" \
    2>/dev/null | jq -r '.token // ""')

  if [[ -z "$jwt" ]]; then
    echo "  ⚠ LibreChat login failed — skipping auto key registration (user will paste sk-... manually)" >&2
    return 0
  fi

  # LibreChat's getUserKeyValues decrypts and then JSON.parses the stored
  # value, so it must be a JSON-stringified object ({"apiKey":"sk-..."}).
  # A raw string triggers an invalid_user_key error.
  local key_value_json
  key_value_json=$(jq -n --arg k "$api_key" '{apiKey:$k}' | jq -c .)

  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "${lc_url}/api/keys" \
    -H "Authorization: Bearer $jwt" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg n 'LiteLLM' --arg v "$key_value_json" '{name:$n, value:$v}')")

  if [[ "$code" == "201" ]]; then
    echo "LiteLLM key auto-registered with LibreChat (no key paste on first chat)"
  else
    echo "  ⚠ Auto key registration failed (HTTP $code) — user will paste sk-... manually" >&2
  fi
}

# Create default LibreChat agents for the new user. We ship two presets:
#   * 'KloudChat (Gemma4:26b)' — general chat, no image gen (default preset)
#   * 'KloudChat (Qwen3.5:35b)' — full toolset including stable-diffusion
# gemma4:26b emits hyphenated tool names ('stable-diffusion') as ReAct text instead
# of OpenAI tool_calls, which LibreChat can't execute, so we route image generation
# through the qwen3.5:35b build and let users pick Gemma4 for everything else.
create_default_agent_for_user() {
  local user_email="$1"
  local mongo_user mongo_pass
  mongo_user=$(env_get MONGO_ROOT_USER)
  mongo_pass=$(env_get MONGO_ROOT_PASSWORD)
  if [[ -z "$mongo_user" || -z "$mongo_pass" ]]; then
    echo "  ⚠ Skipping default agent (MONGO_ROOT_USER/PASSWORD missing from .env)" >&2
    return 0
  fi
  if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^chat-mongodb$'; then
    echo "  ⚠ Skipping default agent (chat-mongodb container not running)" >&2
    return 0
  fi

  local result
  result=$(docker exec chat-mongodb mongosh --quiet \
    -u "$mongo_user" -p "$mongo_pass" --authenticationDatabase admin \
    LibreChat --eval "
      var u = db.users.findOne({email: '$user_email'}, {_id:1});
      if (!u) { print('NO_USER'); quit(); }
      var now = new Date();
      var roleAgent       = db.accessroles.findOne({accessRoleId: 'agent_owner'});
      var roleRemoteAgent = db.accessroles.findOne({accessRoleId: 'remoteAgent_owner'});

      function createAgent(displayName, model, tools) {
        var existing = db.agents.findOne({author: u._id, name: displayName}, {_id:1, id:1});
        if (existing) {
          print('EXISTS:' + displayName + ':' + existing.id);
          return existing.id;
        }
        var agentId = 'agent_' + Math.random().toString(36).slice(2,14) + Math.random().toString(36).slice(2,12);
        var versionEntry = {
          name: displayName, description: '', instructions: '',
          provider: 'LiteLLM', model: model,
          tools: tools,
          artifacts: '', category: 'general',
          support_contact: {name:'', email:''},
          agent_ids: [], edges: [], conversation_starters: [],
          is_promoted: false, mcpServerNames: [], tool_options: {},
          createdAt: now, updatedAt: now
        };
        var ins = db.agents.insertOne({
          id: agentId, name: displayName, description: '', instructions: '',
          provider: 'LiteLLM', model: model, artifacts: '',
          tools: tools,
          tool_kwargs: [], author: u._id,
          agent_ids: [], edges: [], conversation_starters: [],
          versions: [versionEntry], category: 'general',
          support_contact: {name:'', email:''},
          is_promoted: false, mcpServerNames: [], tool_options: {},
          end_after_tools: false, hide_sequential_outputs: false,
          createdAt: now, updatedAt: now, __v: 0
        });
        // ACL — two resourceType rows (agent + remoteAgent) so the agent shows
        // under 'My Agents' when inserted directly via Mongo.
        if (roleAgent && roleRemoteAgent) {
          db.aclentries.insertMany([
            { principalModel: 'User', principalType: 'user', principalId: u._id,
              resourceType: 'agent', resourceId: ins.insertedId,
              permBits: 15, roleId: roleAgent._id,
              grantedAt: now, grantedBy: u._id,
              createdAt: now, updatedAt: now, __v: 0 },
            { principalModel: 'User', principalType: 'user', principalId: u._id,
              resourceType: 'remoteAgent', resourceId: ins.insertedId,
              permBits: 15, roleId: roleRemoteAgent._id,
              grantedAt: now, grantedBy: u._id,
              createdAt: now, updatedAt: now, __v: 0 }
          ]);
        } else {
          print('WARN_NO_ACL_ROLES');
        }
        print('CREATED:' + displayName + ':' + agentId);
        return agentId;
      }

      var gemmaId = createAgent('KloudChat (Gemma4:26b)', 'ollama/gemma4:26b',
        ['execute_code','file_search','web_search']);
      var qwenId  = createAgent('KloudChat (Qwen3.5:35b)', 'ollama/qwen3.5:35b',
        ['stable-diffusion','execute_code','file_search','web_search']);

      // Default preset — prefer the Gemma4 build for new chats; fall back to
      // Qwen3.5 if Gemma4 didn't get created for some reason.
      var defaultAgentId = gemmaId || qwenId;
      var defaultTitle = gemmaId ? 'KloudChat (Gemma4:26b)' : 'KloudChat (Qwen3.5:35b)';
      if (defaultAgentId) {
        var userIdStr = u._id.toString();
        db.presets.updateMany({user: userIdStr, defaultPreset: true}, {\$unset: {defaultPreset: '', order: ''}});
        var presetId = 'preset_' + Math.random().toString(36).slice(2,14) + Math.random().toString(36).slice(2,12);
        db.presets.insertOne({
          presetId: presetId,
          title: defaultTitle,
          user: userIdStr,
          defaultPreset: true,
          order: 1,
          endpoint: 'agents',
          agent_id: defaultAgentId,
          createdAt: now, updatedAt: now, __v: 0
        });
        print('DEFAULT_PRESET:' + defaultTitle);
      }
    " 2>&1)

  if echo "$result" | grep -q 'NO_USER'; then
    echo "  ⚠ Default agent creation failed: LibreChat user _id not found" >&2
    return 0
  fi
  local line
  while IFS= read -r line; do
    case "$line" in
      CREATED:*)        echo "Agent created: ${line#CREATED:}" ;;
      EXISTS:*)         echo "Agent already exists: ${line#EXISTS:} (reused)" ;;
      DEFAULT_PRESET:*) echo "Default preset → ${line#DEFAULT_PRESET:}" ;;
      WARN_NO_ACL_ROLES) echo "  ⚠ ACL roles missing — agent may not appear under 'My Agents'" >&2 ;;
    esac
  done <<< "$result"
}

cmd_user_list() {
  # Newer LiteLLM versions wrap the response as {users:[...], total:...};
  # older versions return a bare array.
  litellm_get "/user/list" \
    | jq -r '(if type == "array" then . else (.users // .data // []) end)[]
             | "\(.user_id)\t\(.user_role)\tspend:\(.spend // 0)$"'
}

cmd_user_delete() {
  local id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in --id) id="$2"; shift 2 ;; *) shift ;; esac
  done
  [[ -z "$id" ]] && { echo "--id is required" >&2; exit 1; }
  litellm_post "/user/delete" "{\"user_ids\":[\"$id\"]}" | jq .
}

# ---- key ----
cmd_key_issue() {
  local user_id="" team_alias="default" key_alias="" budget=9999 service=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --user)    user_id="$2";    shift 2 ;;
      --team)    team_alias="$2"; shift 2 ;;
      --alias)   key_alias="$2";  shift 2 ;;
      --budget)  budget="$2";     shift 2 ;;
      --service) service="$2";    shift 2 ;;
      *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
  done

  local payload result key_val

  if [[ -n "$service" ]]; then
    # Service-account key (LibreChat, Claude Code, ...).
    payload=$(jq -n \
      --arg a "${service}-service-key" \
      --argjson b "$budget" \
      '{key_alias:$a, max_budget:$b, budget_duration:"1mo", user_role:"internal_user"}')
    result=$(litellm_post "/key/generate" "$payload")
    key_val=$(echo "$result" | jq -r '.key')
    echo "service key issued: $service"
    echo "KEY: $key_val"

    # Auto-update .env's LITELLM_SERVICE_KEY for the librechat service.
    if [[ "$service" == "librechat" ]]; then
      env_set LITELLM_SERVICE_KEY "$key_val"
      echo ".env LITELLM_SERVICE_KEY updated"
    fi
    return 0
  fi

  [[ -z "$user_id" ]] && { echo "--user or --service is required" >&2; exit 1; }
  [[ "$user_id" =~ ^[^@]+@[^@]+\.[^@]+$ ]] || { echo "error: --user must be an email address: $user_id" >&2; exit 1; }

  local team_id=""
  if [[ -n "$team_alias" ]]; then
    team_id=$(team_id_by_alias "$team_alias")
    [[ -z "$team_id" ]] && { echo "team not found: $team_alias" >&2; exit 1; }
  fi

  [[ -z "$key_alias" ]] && key_alias="${user_id%%@*}-key"

  payload=$(jq -n \
    --arg u "$user_id" \
    --arg t "$team_id" \
    --arg a "$key_alias" \
    --argjson b "$budget" \
    '{user_id:$u, team_id:$t, key_alias:$a, max_budget:$b, budget_duration:"1mo"}')

  result=$(litellm_post "/key/generate" "$payload")
  key_val=$(echo "$result" | jq -r '.key')
  echo "key issued: $key_alias"
  echo "KEY: $key_val"
}

cmd_key_list() {
  local user_id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in --user) user_id="$2"; shift 2 ;; *) shift ;; esac
  done
  local endpoint="/key/list"
  [[ -n "$user_id" ]] && endpoint+="?user_id=${user_id}"
  litellm_get "$endpoint" | jq -r '.keys[] | "\(.key_alias // "unnamed")\t\(.key[0:20])...\tbudget:\(.max_budget)$\tspend:\(.spend // 0)$"'
}

cmd_key_revoke() {
  local key=""
  while [[ $# -gt 0 ]]; do
    case "$1" in --key) key="$2"; shift 2 ;; *) shift ;; esac
  done
  [[ -z "$key" ]] && { echo "--key is required" >&2; exit 1; }
  litellm_post "/key/delete" "{\"keys\":[\"$key\"]}" | jq .
}

# ---- routing ----
[[ $# -lt 2 ]] && usage
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
  *)            echo "Unknown command: ${resource} ${action}"; usage ;;
esac
