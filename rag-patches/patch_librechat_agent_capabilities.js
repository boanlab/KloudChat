// LibreChat ToolService.js 의 resolveAgentCapabilities() 폴백 조건 패치.
//
// 원본: `if (capabilities.size === 0 && isEphemeralAgentId(agentId))` → ephemeral
// 에이전트만 default capability 폴백 수신. DB-backed agent (mongo agents 컬렉션
// 저장 일반 에이전트) 는 endpointsConfig capabilities 비어있으면 빈 Set
// 그대로 → 'tools' false → plugin tool 전부 loadAgentTools 에서 silent skip.
//
// 패치: ephemeral 조건 제거 → 모든 에이전트가 appConfig.endpoints.agents.capabilities
// (librechat.yaml) 또는 defaultAgentCapabilities 로 폴백.

const fs = require('fs');

const PATH = '/app/api/server/services/ToolService.js';
const MARKER = 'KLOUDCHAT_AGENT_CAP_PATCH';

let src = fs.readFileSync(PATH, 'utf8');
if (src.includes(MARKER)) {
  console.log('[patch_librechat_agent_capabilities] already applied, skipping');
  process.exit(0);
}

const NEEDLE  = 'if (capabilities.size === 0 && isEphemeralAgentId(agentId)) {';
const REPLACE = 'if (capabilities.size === 0) { // ' + MARKER;

if (!src.includes(NEEDLE)) {
  console.error('[patch_librechat_agent_capabilities] NEEDLE not found — LibreChat upstream changed?');
  process.exit(1);
}
src = src.replace(NEEDLE, REPLACE);
fs.writeFileSync(PATH, src);
console.log('[patch_librechat_agent_capabilities] applied — DB-backed agents now fall back to default capabilities');
