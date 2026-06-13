#!/usr/bin/env bash
# Usage: build-push-images.sh [--ns NS] [--tag TAG] [--no-push] [--push-only] [--multi-arch] [SERVICE...]
#
# KloudChat 자체 빌드 이미지(boanlab/kloudchat-*) 를 빌드하고 Docker Hub 로 push 한다.
# compose 파일은 이 이미지들을 pull 만 하므로(배포 전용), 빌드/퍼블리시는 여기서 전담한다.
# vLLM 은 업스트림 이미지라 제외.
#
#   기본            전체 이미지를 host 아키텍처로 build → push.
#   SERVICE...      특정 이미지 short-name 만 대상 (예: comfyui-shim, 여럿 가능). 생략 시 전체.
#                   가능: librechat rag-api comfyui-shim crawl4ai-shim whisper-shim
#                         code-interpreter deep-research slide-export litellm super-agent-shim
#                   amd64 전용(명시 선택 시만, 'build all' 제외): comfyui whisper
#                   — GPU 미디어 백엔드. arm64 는 systemd 라 빌드 안 함. comfyui ~13GB.
#   --no-push       build 만 (로컬 사용).
#   --push-only     build 생략, 로컬 이미지만 push.
#   --multi-arch    linux/amd64,linux/arm64 동시 빌드(buildx) → push. 혼합 노드
#                   (amd64 + arm64 GB10) 배포용. buildx + QEMU 필요, 항상 push.
#   --ns NS         네임스페이스 override (기본 .env 의 KLOUDCHAT_IMAGE_NS=boanlab).
#   --tag TAG       태그 override (기본 .env 의 KLOUDCHAT_IMAGE_TAG=latest).
#
# 사전: Docker Hub 에 push 하려면 `docker login` 선행. push 권한 없으면 실패 시 안내.
# 로컬 빌드만(push 안 함): build-push-images.sh --no-push  (setup.sh 는 항상 pull 하므로 로컬본을 덮어쓴다)
# 한 이미지만 멀티아키 재배포: build-push-images.sh --multi-arch comfyui-shim
set -euo pipefail

__SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$__SCRIPT_DIR/lib.sh"
cd "$__SCRIPT_DIR/.."

# 빌드 대상: "이미지 short-name | dockerfile | build context | 플랫폼(선택)".
# 최종 이미지명 = <NS>/kloudchat-<short>:<TAG>. compose 의 image: 와 1:1 이어야 한다.
# 플랫폼 필드가 비면 host arch(기본) / amd64+arm64(--multi-arch). 값이 있으면 그걸 강제한다.
BUILD_TABLE=(
  "librechat|Dockerfile.librechat|."
  "rag-api|Dockerfile.rag|."
  "comfyui-shim|Dockerfile.comfyui-shim|."
  "crawl4ai-shim|Dockerfile.crawl4ai-shim|."
  "whisper-shim|Dockerfile.whisper-shim|."
  "code-interpreter|Dockerfile.code-interpreter|."
  "deep-research|Dockerfile.deep-research-mcp|."
  "slide-export|Dockerfile|slide-export"
  "litellm|Dockerfile.litellm|."
  "super-agent-shim|Dockerfile.super-agent-shim|."
)

# GPU 미디어 백엔드 (amd64 전용 — arm64 는 systemd). comfyui ~13GB 라 무거워서 'build all'
# 에 미포함, 명시 선택 시에만. 플랫폼 linux/amd64 강제(--multi-arch 여도 arm64 시도 안 함).
MEDIA_TABLE=(
  "comfyui|Dockerfile.comfyui|.|linux/amd64"
  "whisper|Dockerfile.whisper|.|linux/amd64"
)

NS=""; TAG=""; DO_BUILD=1; DO_PUSH=1; MULTI=0; SELECTED=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ns)         NS="${2:?--ns 값 필요}"; shift 2 ;;
    --tag)        TAG="${2:?--tag 값 필요}"; shift 2 ;;
    --no-push)    DO_PUSH=0; shift ;;
    --push-only)  DO_BUILD=0; shift ;;
    --multi-arch) MULTI=1; shift ;;
    -h|--help) sed -n '2,/^set -/p' "$0" | sed 's/^# \{0,1\}//;/^set -/d'; exit 0 ;;
    -*) err "unknown option: $1"; exit 2 ;;
    *) SELECTED+=("$1"); shift ;;   # 특정 이미지 short-name
  esac
done

# NS/TAG: flag > .env > 기본.
NS="${NS:-$(env_get KLOUDCHAT_IMAGE_NS 2>/dev/null || true)}"; NS="${NS:-boanlab}"
TAG="${TAG:-$(env_get KLOUDCHAT_IMAGE_TAG 2>/dev/null || true)}"; TAG="${TAG:-latest}"
img_of() { echo "${NS}/kloudchat-${1}:${TAG}"; }

# SERVICE 인자 있으면 그 short-name 들로 좁힌다 (없으면 BUILD_TABLE 전체 — MEDIA 는 제외).
# 명시 선택은 BUILD_TABLE + MEDIA_TABLE 양쪽에서 찾는다. 미존재명은 거부.
if (( ${#SELECTED[@]} )); then
  _filtered=()
  for want in "${SELECTED[@]}"; do
    _hit=0
    for e in "${BUILD_TABLE[@]}" "${MEDIA_TABLE[@]}"; do
      IFS='|' read -r s _ <<<"$e"
      [[ "$s" == "$want" ]] && { _filtered+=("$e"); _hit=1; break; }
    done
    (( _hit )) || { err "알 수 없는 이미지: '$want' (가능: $(for e in "${BUILD_TABLE[@]}" "${MEDIA_TABLE[@]}"; do IFS='|' read -r s _ <<<"$e"; printf '%s ' "$s"; done))"; exit 2; }
  done
  BUILD_TABLE=("${_filtered[@]}")
fi

hdr "KloudChat 이미지 ${NS}/kloudchat-*:${TAG}  (build=${DO_BUILD} push=${DO_PUSH} multi-arch=${MULTI})"
for e in "${BUILD_TABLE[@]}"; do IFS='|' read -r s _ _ <<<"$e"; echo "  $(img_of "$s")"; done

if (( MULTI )); then
  (( DO_PUSH )) || { err "--multi-arch 는 push 필수(멀티플랫폼은 로컬 적재 불가). --no-push 와 같이 못 씀"; exit 2; }
  docker buildx version >/dev/null 2>&1 || { err "docker buildx 필요 (멀티아키)"; exit 1; }
  if ! docker buildx inspect kloudchat-builder >/dev/null 2>&1; then
    info "buildx 빌더 생성 (docker-container 드라이버)"
    docker buildx create --name kloudchat-builder --driver docker-container --bootstrap >/dev/null
  fi
  PLAT="linux/amd64,linux/arm64"
  for e in "${BUILD_TABLE[@]}"; do
    IFS='|' read -r short df ctx plat <<<"$e"; img="$(img_of "$short")"
    platforms="${plat:-$PLAT}"   # 엔트리에 플랫폼 명시(amd64 전용 미디어)면 그것만.
    hdr "buildx ${img}  [${platforms}]"
    docker buildx build --builder kloudchat-builder --platform "$platforms" \
      -t "$img" -f "${ctx}/${df}" --push "$ctx"
  done
  ok "멀티아키 build+push 완료 (${#BUILD_TABLE[@]}개)"
  exit 0
fi

if (( DO_BUILD )); then
  hdr "build (host arch)"
  host_plat="linux/$(detect_arch)"
  for e in "${BUILD_TABLE[@]}"; do
    IFS='|' read -r short df ctx plat <<<"$e"; img="$(img_of "$short")"
    # 플랫폼 강제(amd64 전용 미디어) 엔트리는 호스트 arch 가 맞을 때만 빌드.
    if [[ -n "$plat" && "$plat" != *"$host_plat"* ]]; then
      warn "$short 는 ${plat} 전용 — 호스트(${host_plat})에서 빌드 불가, 건너뜀 (amd64 노드에서 실행)"
      continue
    fi
    echo "  → build $img"
    docker build -t "$img" -f "${ctx}/${df}" "$ctx"
  done
  ok "build 완료"
fi

if (( DO_PUSH )); then
  hdr "push → Docker Hub"
  docker info 2>/dev/null | grep -q "Username:" || warn "docker login 미확인 — push 실패 시 'docker login' 먼저."
  host_plat="linux/$(detect_arch)"
  for e in "${BUILD_TABLE[@]}"; do
    IFS='|' read -r short _ _ plat <<<"$e"; img="$(img_of "$short")"
    # 빌드 루프와 동일 가드 — 호스트 arch 에서 못 만든 플랫폼 강제 엔트리는 push 도 건너뛴다.
    if [[ -n "$plat" && "$plat" != *"$host_plat"* ]]; then
      warn "$short 는 ${plat} 전용 — 호스트(${host_plat}) 빌드물 없음, push 건너뜀"
      continue
    fi
    echo "  → $img"
    docker push "$img" || { err "push 실패: $img — 'docker login' + ${NS} push 권한 확인"; exit 1; }
  done
  ok "push 완료"
fi
