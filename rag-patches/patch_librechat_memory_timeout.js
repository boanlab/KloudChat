// LibreChat 메모리(개인화) 처리 타임아웃 3s → 기본 30s 로 증가.
//
// 원본 동작
// ---------
// /app/api/server/controllers/agents/client.js:
//   async awaitMemoryWithTimeout(memoryPromise, timeoutMs = 3000) { ... }
// → 메모리 추출(LLM 호출, 우리는 local/gemma-4-26b)이 3초 초과 시 절단 →
//   "Memory processing timed out after 3 seconds" 후 개인화 사실 미저장.
//   로컬 추출은 보통 3초 초과 → 메모리 기능 사실상 미동작.
//
// 패치
// ----
// 기본 타임아웃 30s 로. 메모리 처리 = 응답 스트림과 별개(best-effort, 비동기) →
// 타임아웃 늘려도 사용자 응답 지연 없음. KC_MEMORY_TIMEOUT_MS 로 override 가능.
// warn 메시지 'after 3 seconds' 하드코딩도 실제 timeoutMs 로 정정.

const fs = require('fs');

const SERVER_PATH = '/app/api/server/controllers/agents/client.js';
const MARKER_TAG = 'KLOUDCHAT_MEMORY_TIMEOUT_PATCH';

const NEEDLE = 'async awaitMemoryWithTimeout(memoryPromise, timeoutMs = 3000) {';
const REPLACE =
  'async awaitMemoryWithTimeout(memoryPromise, timeoutMs = Number(process.env.KC_MEMORY_TIMEOUT_MS) || 30000) { // ' +
  MARKER_TAG;

const WARN_NEEDLE =
  "logger.warn('[AgentClient] Memory processing timed out after 3 seconds');";
const WARN_REPLACE =
  'logger.warn(`[AgentClient] Memory processing timed out after ${timeoutMs}ms`);';

const src = fs.readFileSync(SERVER_PATH, 'utf8');
if (src.includes(MARKER_TAG)) {
  console.log('[patch_librechat_memory_timeout] already applied, skipping');
  process.exit(0);
}
if (!src.includes(NEEDLE)) {
  console.error('[patch_librechat_memory_timeout] NEEDLE not found — LibreChat upstream changed?');
  process.exit(1);
}
let out = src.replace(NEEDLE, REPLACE);
if (out.includes(WARN_NEEDLE)) {
  out = out.replace(WARN_NEEDLE, WARN_REPLACE);
}
fs.writeFileSync(SERVER_PATH, out);
console.log('[patch_librechat_memory_timeout] applied (default 30s, override KC_MEMORY_TIMEOUT_MS)');
