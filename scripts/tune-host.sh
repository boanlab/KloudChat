#!/usr/bin/env bash
# Usage: tune-host.sh [--check]
#
# LLM 서빙 호스트 sysctl 튜닝. 모델 weight mmap 이 file cache GB 단위로 잡고 있어
# 기본 swappiness=60 이면 idle 구간에 file cache 가 회수되고 다음 추론 때 cold start.
# GB10 unified memory 노드는 file cache 압박이 특히 심함.
#
#   --check   적용 없이 현재값만 표시
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --check)    CHECK_ONLY=1 ;;
    -h|--help)  grep -E '^# ' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)          err "Unknown: $arg"; exit 2 ;;
  esac
done

CONF=/etc/sysctl.d/99-kloudchat-tuning.conf

hdr "Host tuning (sysctl)"

CUR_SWAPPINESS="$(cat /proc/sys/vm/swappiness)"
echo "  current vm.swappiness = $CUR_SWAPPINESS"

if has_gb10; then
  info "GB10 unified memory 감지 — swappiness 낮춤 권장"
fi

if (( CHECK_ONLY )); then
  [[ -f "$CONF" ]] && { ok "$CONF 존재"; cat "$CONF" | sed 's/^/    /'; } \
                   || warn "$CONF 없음 — 미적용 상태"
  exit 0
fi

[[ $EUID -ne 0 ]] && exec sudo "$0" "$@"

cat > "$CONF" <<'EOF'
# KloudChat host tuning — LLM 서빙용
# 모델 weight 가 mmap 으로 file cache 를 점유하므로 anon 보다 file cache 보호.
vm.swappiness = 10
EOF
ok "$CONF 작성"

sysctl --system >/dev/null
NEW="$(cat /proc/sys/vm/swappiness)"
if [[ "$NEW" == "10" ]]; then
  ok "vm.swappiness = $NEW (적용됨, 재부팅 후에도 유지)"
else
  err "적용 실패 — 현재값 $NEW. 다른 sysctl 파일이 override 중일 수 있음"
  err "  확인: sysctl --system 2>&1 | grep swappiness"
  exit 1
fi

echo
info "기존 swap-out 페이지는 자동 swap-in 안 됨. 강제로 비우려면:"
echo "    sudo swapoff -a && sudo swapon -a   # swap used 가 free RAM 보다 작을 때만 안전"
