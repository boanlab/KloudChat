#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

APP_ROOT="${WHISPER_APP_ROOT:-/opt/whisper}"
DATA_ROOT="${WHISPER_DATA_ROOT:-/var/lib/whisper}"
USR="${WHISPER_USER:-whisper}"
VENV="${APP_ROOT}/venv"

usage() {
  cat <<EOF
Usage: $(basename "$0") [models...]

faster-whisper 가중치를 \${WHISPER_DATA_ROOT:-/var/lib/whisper} 에 미리 받음
(install-whisper.sh 직후 권장 — 첫 transcribe 호출의 수십 초 lazy-load 회피).

인자 없으면 GPU 유무로 권장 선택. faster-whisper 가 인식하는 이름이면 모두 가능:
  tiny       ~75 MB
  base       ~145 MB
  small      ~485 MB     CPU 권장
  medium     ~1.5 GB
  large-v3   ~3 GB       GPU 권장 (기본)
  turbo      ~1.6 GB     = large-v3-turbo, 속도/품질 균형
  all                    tiny + base + small + medium + large-v3 + turbo

오프라인/에어갭 호스트는 필수. 온라인이면 첫 호출 때 자동 다운로드되므로 선택.
EOF
  exit 0
}

for a in "$@"; do [[ "$a" =~ ^(-h|--help)$ ]] && usage; done

[[ -x "${VENV}/bin/python" ]] \
  || { err "venv 없음: ${VENV}/bin/python — 먼저 ./scripts/install-whisper.sh 실행."; exit 1; }
getent passwd "$USR" &>/dev/null \
  || { err "user '$USR' 없음 — install-whisper.sh 가 만듭니다."; exit 1; }
"${VENV}/bin/python" -c 'import faster_whisper' &>/dev/null \
  || { err "venv 에 faster_whisper 없음 — install-whisper.sh 재실행 필요."; exit 1; }

# DATA_ROOT 는 whisper:whisper 소유 → root 로 승격 후 whisper 로 드롭해야 씀.
if [[ $EUID -ne 0 && "$(id -un)" != "$USR" ]]; then
  exec sudo --preserve-env=WHISPER_APP_ROOT,WHISPER_DATA_ROOT,WHISPER_USER "$0" "$@"
fi

# alias → huggingface_hub 캐시 디렉토리명 (skip 판정용)
cache_dir_for() {
  case "$1" in
    turbo|large-v3-turbo) echo "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" ;;
    distil-large-v3)      echo "models--Systran--faster-distil-whisper-large-v3" ;;
    *)                    echo "models--Systran--faster-whisper-$1" ;;
  esac
}

pull() {
  local name="$1" dir
  dir="${DATA_ROOT}/$(cache_dir_for "$name")"
  if ls "$dir"/snapshots/*/model.bin &>/dev/null; then
    echo "[skip] $name"; return 0
  fi
  echo "[pull] $name"
  # device=cpu / compute_type=int8 → GPU 없이 다운로드 + 무결성 검증만. WhisperModel 을
  # 쓰는 이유는 app.py 와 동일 download_root 경로 매핑을 보장하기 위해 (cache layout drift 방지).
  sudo -u "$USR" "${VENV}/bin/python" - "$name" "$DATA_ROOT" <<'PY'
import sys
from faster_whisper import WhisperModel
name, root = sys.argv[1], sys.argv[2]
WhisperModel(name, device="cpu", compute_type="int8", download_root=root)
PY
  echo "[ok]   $name"
}

if [[ $# -eq 0 ]]; then
  if has_nvidia_gpu; then set -- large-v3; info "GPU 감지 → large-v3"
  else                    set -- small;    info "GPU 없음 → small (CPU 추론은 느림)"; fi
fi

for arg in "$@"; do
  case "$arg" in
    all) "$0" tiny base small medium large-v3 turbo ;;
    *)   pull "$arg" ;;
  esac
done

echo "=== done ==="
echo "  data: ${DATA_ROOT} ($(du -sh "${DATA_ROOT}" 2>/dev/null | awk '{print $1}'))"
