#!/bin/bash
R='\033[1;31m'; G='\033[1;32m'; Y='\033[1;33m'
B='\033[1;34m'; M='\033[1;35m'; C='\033[1;36m'; N='\033[0m'

format() {
  local color="$1" emoji="$2" name="$3" line ts
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    line=$(echo "$line" | sed -E 's/\x1b\[[0-9;]*[a-zA-Z]//g' | head -c 200)
    ts=$(date '+%H:%M:%S')
    printf "${color}[%s] %s %-10s${N}│ %s\n" "$ts" "$emoji" "$name" "$line"
  done
}

follow() {
  local color="$1" emoji="$2" name="$3" container="$4" inc="$5" exc="${6:-}"
  local pipe=(docker logs -f --tail 0 "$container")
  if [[ -n "$exc" ]]; then
    "${pipe[@]}" 2>&1 | stdbuf -oL grep -iE "$inc" | stdbuf -oL grep -ivE "$exc" | format "$color" "$emoji" "$name" &
  else
    "${pipe[@]}" 2>&1 | stdbuf -oL grep -iE "$inc" | format "$color" "$emoji" "$name" &
  fi
}

clear
echo "KloudChat live monitor — Ctrl+C 로 종료"
echo "  🌐 LibreChat  🧠 LiteLLM  ⚡ Ollama  📚 RAG  🔍 SearXNG  🎨 ComfyUI/Shim  💻 Code  🎙 Whisper"
echo "─────────────────────────────────────────────"

follow "$B" "🌐" "LibreChat" LibreChat        "Login|sendCompletion|onSearchResults|streamAudio|generate_image|tool|run_id|completion|message" "auth.json|getUserPluginAuth|Error scraping|Title generation|FIRECRAWL"
follow "$G" "🧠" "LiteLLM  " litellm          "POST|GET|spend|model" "liveliness|prisma|migration"
follow "$M" "🎨" "Shim     " comfyui-shim     "txt2img|img2img|model=|template=|variants"
follow "$C" "📚" "RAG API  " rag_api          "embed|chunk|query|retriev|upload|document|POST|GET"
follow "$Y" "🔍" "SearXNG  " searxng          "GET /search|query"
follow "$R" "💻" "CodeIntp " code-interpreter "exec|run|complete|sandbox|repl" "REPL ready timeout|REPL not ready|Failed to start REPL"

# 호스트 systemd 서비스 (compose 호스트와 같은 머신일 때만 보임 — 원격 GPU 노드는 ssh 로).
if command -v journalctl &>/dev/null; then
  follow_unit() {
    local color="$1" emoji="$2" name="$3" unit="$4" inc="$5"
    systemctl list-unit-files "${unit}.service" --no-legend 2>/dev/null | grep -q . || return 0
    journalctl -u "$unit" -f -n 0 --no-pager 2>&1 \
      | stdbuf -oL grep -iE "$inc" \
      | format "$color" "$emoji" "$name" &
  }
  follow_unit "$G" "⚡" "Ollama   " ollama  "llm load|loaded|gpu memory|prompt|generate|embedding"
  follow_unit "$M" "🎨" "ComfyUI  " comfyui "Prompt executed|got prompt|sampling|VAE|model|loading|VRAM"
  follow_unit "$C" "🎙" "Whisper  " whisper "POST|transcriptions|Loading WhisperModel"
fi

trap 'echo; echo "─── stop ───"; kill $(jobs -p) 2>/dev/null; exit 0' INT TERM
wait
