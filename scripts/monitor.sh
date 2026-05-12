#!/bin/bash
# KloudChat live log monitor — colour-coded, emoji-tagged stream from every service.
# Usage: ./scripts/monitor.sh
# Stop:  Ctrl+C

# ANSI colours
R='\033[1;31m'; G='\033[1;32m'; Y='\033[1;33m'; B='\033[1;34m'
M='\033[1;35m'; C='\033[1;36m'; W='\033[1;37m'; N='\033[0m'

format() {
  local color="$1" emoji="$2" name="$3"
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    line=$(echo "$line" | sed -E 's/\x1b\[[0-9;]*[a-zA-Z]//g' | head -c 200)
    ts=$(date '+%H:%M:%S')
    printf "${color}[%s] %s %-10s${N}│ %s\n" "$ts" "$emoji" "$name" "$line"
  done
}

# Header
clear
cat <<'EOF'
╔════════════════════════════════════════════════════════════════╗
║  KloudChat — live system flow monitor                          ║
║  Stop: Ctrl+C                                                  ║
╚════════════════════════════════════════════════════════════════╝
EOF
echo
echo "  🌐 LibreChat (orchestration)     🧠 LiteLLM (gateway)"
echo "  ⚡ Ollama (LLM engine)           📚 RAG API (documents)"
echo "  🔍 SearXNG (web search)          🎨 ComfyUI + shim (image gen)"
echo "  💻 Code Interpreter              🎤 Whisper (STT)  🔊 TTS (openedai-speech)"
echo
echo "═══════════════════════════════════════════════════════════════════"

# LibreChat — high-level events only
docker logs -f --tail 0 LibreChat 2>&1 | \
  stdbuf -oL grep -iE "Login|sendCompletion|onSearchResults|streamAudio|stable|tool|run_id|completion|message" | \
  stdbuf -oL grep -ivE "auth.json|getUserPluginAuth|Error scraping|Title generation|FIRECRAWL" | \
  format "$B" "🌐" "LibreChat" &

# LiteLLM — API calls and 200 responses
docker logs -f --tail 0 litellm 2>&1 | \
  stdbuf -oL grep -E "POST|GET|spend|model" | \
  stdbuf -oL grep -ivE "liveliness|prisma|migration" | \
  format "$G" "🧠" "LiteLLM  " &

# ComfyUI — image-generation progress
docker logs -f --tail 0 comfyui 2>&1 | \
  stdbuf -oL grep -iE "Prompt executed|got prompt|sampling|VAE|model|loading|VRAM" | \
  stdbuf -oL grep -ivE "TRACE|DEBUG" | \
  format "$M" "🎨" "ComfyUI  " &

# comfyui-shim — A1111 adapter calls
docker logs -f --tail 0 comfyui-shim 2>&1 | \
  stdbuf -oL grep -iE "txt2img|img2img|model=|template=" | \
  format "$M" "🎨" "Shim     " &

# RAG API
docker logs -f --tail 0 rag_api 2>&1 | \
  stdbuf -oL grep -iE "embed|chunk|query|retriev|upload|document|POST|GET" | \
  format "$C" "📚" "RAG API  " &

# SearXNG
docker logs -f --tail 0 searxng 2>&1 | \
  stdbuf -oL grep -iE "GET /search|query" | \
  format "$Y" "🔍" "SearXNG  " &

# Code Interpreter — drop pool-refill noise (upstream nsjail mount-ns issue, harmless)
docker logs -f --tail 0 code-interpreter 2>&1 | \
  stdbuf -oL grep -iE "exec|run|complete|sandbox|repl" | \
  stdbuf -oL grep -ivE "REPL ready timeout|REPL not ready|Failed to start REPL" | \
  format "$R" "💻" "CodeIntp " &

# Whisper
docker logs -f --tail 0 whisper 2>&1 | \
  stdbuf -oL grep -iE "POST|transcri|asr|audio" | \
  stdbuf -oL grep -v "%|Application startup|Uvicorn" | \
  format "$C" "🎤" "Whisper  " &

# TTS (openedai-speech: piper + xtts_v2)
docker logs -f --tail 0 tts 2>&1 | \
  stdbuf -oL grep -iE "POST|speech|tts|generated|synthe|voice" | \
  format "$M" "🔊" "TTS      " &

# Ollama — Linux uses systemd, macOS writes to /tmp/ollama.log (install-ollama.sh)
if command -v journalctl &>/dev/null; then
  journalctl -u ollama -f -n 0 --no-pager 2>&1 | \
    stdbuf -oL grep -iE "llm load|loaded|gpu memory|prompt|generate|embedding" | \
    format "$G" "⚡" "Ollama   " &
elif [[ -f /tmp/ollama.log ]]; then
  tail -F /tmp/ollama.log 2>/dev/null | \
    stdbuf -oL grep -iE "llm load|loaded|gpu memory|prompt|generate|embedding" | \
    format "$G" "⚡" "Ollama   " &
fi

trap 'echo; echo "─── stop ───"; kill $(jobs -p) 2>/dev/null; exit 0' INT TERM

wait
