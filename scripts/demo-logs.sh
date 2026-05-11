#!/bin/bash
# KloudChat 통합 데모 로그 — 색상·이모지 분류 + 흐름 가시화
# 사용법: ./demo-logs.sh
# 종료: Ctrl+C

# ANSI colors
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

# 헤더 출력
clear
cat <<'EOF'
╔════════════════════════════════════════════════════════════════╗
║  KloudChat — 실시간 시스템 흐름 모니터                         ║
║  온프레미스 AI 플랫폼                                          ║
╚════════════════════════════════════════════════════════════════╝
EOF
echo
echo "  🌐 LibreChat (오케스트레이션)    🧠 LiteLLM (게이트웨이)"
echo "  ⚡ Ollama (LLM 엔진)             📚 RAG API (문서)"
echo "  🔍 SearXNG (웹 검색)             🎨 SD.Next (이미지)"
echo "  💻 Code Interpreter              🎤 Whisper (STT)  🔊 TTS (openedai-speech)"
echo
echo "═══════════════════════════════════════════════════════════════════"

# LibreChat — 핵심 이벤트만
docker logs -f --tail 0 LibreChat 2>&1 | \
  stdbuf -oL grep -iE "Login|sendCompletion|onSearchResults|streamAudio|stable|tool|run_id|completion|message" | \
  stdbuf -oL grep -ivE "auth.json|getUserPluginAuth|Error scraping|Title generation|FIRECRAWL" | \
  format "$B" "🌐" "LibreChat" &

# LiteLLM — API 호출 + 200 응답
docker logs -f --tail 0 litellm 2>&1 | \
  stdbuf -oL grep -E "POST|GET|spend|model" | \
  stdbuf -oL grep -ivE "liveliness|prisma|migration" | \
  format "$G" "🧠" "LiteLLM  " &

# SD.Next — 이미지 생성 진행
docker logs -f --tail 0 sdnext 2>&1 | \
  stdbuf -oL grep -iE "Processing|Processed|model|VRAM|API|request" | \
  stdbuf -oL grep -ivE "GC:|TRACE|DEBUG" | \
  format "$M" "🎨" "SD.Next  " &

# RAG API
docker logs -f --tail 0 rag_api 2>&1 | \
  stdbuf -oL grep -iE "embed|chunk|query|retriev|upload|document|POST|GET" | \
  format "$C" "📚" "RAG API  " &

# SearXNG
docker logs -f --tail 0 searxng 2>&1 | \
  stdbuf -oL grep -iE "GET /search|query" | \
  format "$Y" "🔍" "SearXNG  " &

# Code Interpreter — 풀 보충 noise 제외 (업스트림 nsjail mount-ns 공유 이슈, 기능 영향 없음)
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

# Ollama (host systemd)
journalctl -u ollama -f -n 0 --no-pager 2>&1 | \
  stdbuf -oL grep -iE "llm load|loaded|gpu memory|prompt|generate|embedding" | \
  format "$G" "⚡" "Ollama   " &

trap 'echo; echo "─── 종료 ───"; kill $(jobs -p) 2>/dev/null; exit 0' INT TERM

wait
