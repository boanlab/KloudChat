// LibreChat 의 resolveAgentCapabilities() 폴백 조건 패치.
//
// 원본 (api/server/services/ToolService.js):
//   if (capabilities.size === 0 && isEphemeralAgentId(agentId)) {
//     capabilities = new Set(appConfig.endpoints?.agents?.capabilities ?? defaultAgentCapabilities);
//   }
//
// 문제: ephemeral 이 아닌 DB-backed agent (mongo 의 agents collection 에 저장된 모든 일반 agent)
//       의 경우 endpointsConfig?.agents?.capabilities 가 비어있으면 폴백 안 함 → 빈 Set 반환 →
//       'tools' capability false → image-generation / dalle / wolfram 등 모든 plugin tool 이
//       loadAgentTools 의 areToolsEnabled 필터에서 silent skip → LLM 이 그 툴을 못 봄.
//
// 우리는 librechat.yaml 의 endpoints.agents.capabilities 에 명시적으로 9개를 박았지만,
// req-level endpointsConfig 가 yaml 의 capabilities 를 안 잇는 듯하여 빈 Set 가 그대로 옴.
// 폴백 조건에서 ephemeral 제한만 제거하면 appConfig.endpoints.agents.capabilities (yaml)
// 또는 defaultAgentCapabilities 로 폴백 → 모든 agent 가 정상 capability 를 받음.

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
