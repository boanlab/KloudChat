#!/bin/bash
# KloudChat LiteLLM CLI 관리 도구
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"

usage() {
  cat <<EOF
사용법: manage.sh <resource> <action> [옵션]

Resources:
  team    팀 관리
  user    사용자 관리
  key     가상 키 관리

team actions:
  create  --alias <이름> --budget <금액> --duration <기간> --tpm <TPM> --rpm <RPM> --models <모델,목록>
  list
  delete  --id <team_id>

user actions:
  create  --id <이메일> [--team <team_alias>] [--budget <금액>]   (팀 기본값: default)
          [--name <이름> --username <username> --password <비번>]   ← LibreChat 사용자 + 자동 키 발급 동시
  list
  delete  --id <이메일>

key actions:
  issue   --user <이메일> [--team <team_alias>] [--alias <이름>] [--budget <금액>]   (팀 기본값: default)
  issue   --service <서비스명> [--budget <금액>]   (서비스 계정 키)
  list    [--user <이메일>]
  revoke  --key <sk-...>

예시:
  manage.sh team create --alias research --budget 100 --models "ollama/*"
  manage.sh user create --id alice@lab.ac.kr --team research --budget 20
  manage.sh user create --id prof@dankook.ac.kr --name '김교수' --username kim --password 'pw12345' --budget 100
                                                                        # LibreChat 사용자 + LiteLLM 사용자 + 키 한 번에
  manage.sh key issue --user alice@lab.ac.kr --team research --alias alice-key
  manage.sh key issue --service librechat --budget 9999
  manage.sh key list --user alice@lab.ac.kr
EOF
  exit 1
}

# ---- team ----
cmd_team_create() {
  local alias="" budget=9999 duration="1mo" tpm=100000 rpm=500 models="ollama/*"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --alias)    alias="$2";    shift 2 ;;
      --budget)   budget="$2";   shift 2 ;;
      --duration) duration="$2"; shift 2 ;;
      --tpm)      tpm="$2";      shift 2 ;;
      --rpm)      rpm="$2";      shift 2 ;;
      --models)   models="$2";   shift 2 ;;
      *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
    esac
  done
  [[ -z "$alias" ]] && { echo "--alias 필수" >&2; exit 1; }

  # 기존 팀 확인
  local existing
  existing=$(team_id_by_alias "$alias")
  if [[ -n "$existing" ]]; then
    echo "팀 이미 존재: $alias (id=$existing)"
    echo "$existing"
    return 0
  fi

  # models 배열 변환
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

  # 캐시 저장
  local cache="${DATA_DIR}/teams.json"
  if [[ -f "$cache" ]]; then
    jq --argjson r "$result" '. += [$r]' "$cache" > "${cache}.tmp" && mv "${cache}.tmp" "$cache"
  else
    echo "[$result]" > "$cache"
  fi

  echo "팀 생성 완료: $alias (id=$team_id)"
  echo "$team_id"
}

cmd_team_list() {
  litellm_get "/team/list" | jq -r '.[] | "\(.team_id)\t\(.team_alias)\t예산:\(.max_budget)$\t모델:\(.models // [] | join(","))"'
}

cmd_team_delete() {
  local id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in --id) id="$2"; shift 2 ;; *) shift ;; esac
  done
  [[ -z "$id" ]] && { echo "--id 필수" >&2; exit 1; }
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
      *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
    esac
  done
  [[ -z "$user_id" ]] && { echo "--id 필수" >&2; exit 1; }
  [[ "$user_id" =~ ^[^@]+@[^@]+\.[^@]+$ ]] || { echo "오류: 이메일 형식이 아닙니다: $user_id" >&2; exit 1; }

  # --name/--username/--password 셋이 일관되게 제공됐는지 확인 (full provisioning 모드)
  local lc_count=0
  for v in "$lc_name" "$lc_username" "$lc_password"; do
    [[ -n "$v" ]] && lc_count=$((lc_count+1))
  done
  if [[ $lc_count -ne 0 && $lc_count -ne 3 ]]; then
    echo "오류: --name, --username, --password 는 셋 다 함께 제공되어야 합니다 (LibreChat 사용자 생성용)" >&2
    exit 1
  fi
  local full_provision=0
  [[ $lc_count -eq 3 ]] && full_provision=1

  # LibreChat 사용자 생성 (full provisioning 모드)
  if [[ $full_provision -eq 1 ]]; then
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^LibreChat$'; then
      echo "LibreChat 사용자 생성 중..."
      local lc_output
      lc_output=$(docker exec -i LibreChat npm run create-user -- "$user_id" "$lc_name" "$lc_username" "$lc_password" <<< 'y' 2>&1 | tail -10)
      if echo "$lc_output" | grep -q "User created successfully"; then
        echo "  ✓ LibreChat 사용자: $user_id ($lc_username)"
      else
        echo "  ⚠ LibreChat 사용자 생성 실패 (이미 존재 가능성). 마지막 출력:" >&2
        echo "$lc_output" | tail -3 | sed 's/^/    /' >&2
      fi
    else
      echo "  ⚠ LibreChat 컨테이너 미실행 — LibreChat 단계 건너뜀" >&2
    fi
  fi

  # LiteLLM 유저 생성
  local payload result
  payload=$(jq -n --arg u "$user_id" '{user_id:$u, user_role:"internal_user"}')
  result=$(litellm_post "/user/new" "$payload")
  echo "유저 생성: $user_id"

  # 팀 추가
  if [[ -n "$team_alias" ]]; then
    local team_id
    team_id=$(team_id_by_alias "$team_alias")
    if [[ -z "$team_id" ]]; then
      echo "팀을 찾을 수 없음: $team_alias" >&2; exit 1
    fi
    payload=$(jq -n \
      --arg tid "$team_id" \
      --arg uid "$user_id" \
      --argjson b "$budget" \
      '{team_id:$tid, max_budget_in_team:$b, member:{role:"user", user_id:$uid}}')
    litellm_post "/team/member_add" "$payload" > /dev/null
    echo "팀 추가: $team_alias (팀 내 한도 \$${budget}/월)"
  fi

  # 자동 키 발급 (full provisioning 모드)
  if [[ $full_provision -eq 1 ]]; then
    local key_alias="${lc_username}-key"
    cmd_key_issue --user "$user_id" --team "$team_alias" --alias "$key_alias"
  fi
}

cmd_user_list() {
  litellm_get "/user/list" | jq -r '.[] | "\(.user_id)\t\(.user_role)\t소비:\(.spend // 0)$"'
}

cmd_user_delete() {
  local id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in --id) id="$2"; shift 2 ;; *) shift ;; esac
  done
  [[ -z "$id" ]] && { echo "--id 필수" >&2; exit 1; }
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
      *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
    esac
  done

  local payload result key_val

  if [[ -n "$service" ]]; then
    # 서비스 계정 키 (LibreChat, Claude Code 등)
    payload=$(jq -n \
      --arg a "${service}-service-key" \
      --argjson b "$budget" \
      '{key_alias:$a, max_budget:$b, budget_duration:"1mo", user_role:"internal_user"}')
    result=$(litellm_post "/key/generate" "$payload")
    key_val=$(echo "$result" | jq -r '.key')
    echo "서비스 키 발급: $service"
    echo "KEY: $key_val"

    # .env의 LITELLM_SERVICE_KEY 자동 갱신 (librechat 서비스인 경우)
    if [[ "$service" == "librechat" ]]; then
      local env_file="${SCRIPT_DIR}/../.env"
      if [[ -f "$env_file" ]]; then
        sed -i "s|^LITELLM_SERVICE_KEY=.*|LITELLM_SERVICE_KEY=${key_val}|" "$env_file"
        echo ".env LITELLM_SERVICE_KEY 업데이트 완료"
      fi
    fi
    return 0
  fi

  [[ -z "$user_id" ]] && { echo "--user 또는 --service 필수" >&2; exit 1; }
  [[ "$user_id" =~ ^[^@]+@[^@]+\.[^@]+$ ]] || { echo "오류: 이메일 형식이 아닙니다: $user_id" >&2; exit 1; }

  local team_id=""
  if [[ -n "$team_alias" ]]; then
    team_id=$(team_id_by_alias "$team_alias")
    [[ -z "$team_id" ]] && { echo "팀을 찾을 수 없음: $team_alias" >&2; exit 1; }
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
  echo "키 발급: $key_alias"
  echo "KEY: $key_val"
}

cmd_key_list() {
  local user_id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in --user) user_id="$2"; shift 2 ;; *) shift ;; esac
  done
  local endpoint="/key/list"
  [[ -n "$user_id" ]] && endpoint+="?user_id=${user_id}"
  litellm_get "$endpoint" | jq -r '.keys[] | "\(.key_alias // "unnamed")\t\(.key[0:20])...\t예산:\(.max_budget)$\t소비:\(.spend // 0)$"'
}

cmd_key_revoke() {
  local key=""
  while [[ $# -gt 0 ]]; do
    case "$1" in --key) key="$2"; shift 2 ;; *) shift ;; esac
  done
  [[ -z "$key" ]] && { echo "--key 필수" >&2; exit 1; }
  litellm_post "/key/delete" "{\"keys\":[\"$key\"]}" | jq .
}

# ---- 라우팅 ----
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
  *)            echo "알 수 없는 명령: ${resource} ${action}"; usage ;;
esac
