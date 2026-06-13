// 제한 MCP 서버(deep_research / export_deck / generate_video / paperbanana / youtube)를
// 비-ADMIN 사용자가 본인 agent 에 부착·사용하지 못하게 한다. 관리자가 만든 공유 agent 의
// 실행에는 영향 없음(아래는 agent 생성/수정/복제 시점의 도구 인가 + 빌더 MCP 목록만 손댐).
//
//  (1) controllers/agents/v1.js : filterAuthorizedTools 가 create/update/duplicate 모두에서
//      도구를 인가하므로, userRole 을 받아 비-admin 의 제한 MCP 서버 도구를 거부한다(서버측 강제).
//  (2) controllers/mcp.js : getMCPTools 가 빌더의 'MCP 도구 추가' 목록을 만든다 — 비-admin 에겐
//      제한 서버를 목록에서 제거(UI 숨김).

const fs = require('fs');

const RESTRICTED = "['deep_research', 'export_deck', 'generate_video', 'paperbanana', 'youtube']";
const MARKER = 'KLOUDCHAT_MCP_ADMIN_ONLY';

function patch(path, edits) {
  let src = fs.readFileSync(path, 'utf8');
  if (src.includes(MARKER)) {
    console.log(`[mcp_admin_only] ${path} already patched, skipping`);
    return;
  }
  for (const [needle, replace] of edits) {
    if (!src.includes(needle)) {
      console.error(`[mcp_admin_only] NEEDLE not found in ${path}:\n${needle}`);
      process.exit(1);
    }
    src = src.replace(needle, replace);
  }
  fs.writeFileSync(path, src);
  console.log(`[mcp_admin_only] applied ${edits.length} edit(s) to ${path}`);
}

// ── (1) 서버측 강제: filterAuthorizedTools ────────────────────────────────
const V1 = '/app/api/server/controllers/agents/v1.js';
patch(V1, [
  // 함수 시그니처에 userRole 추가 + 제한셋 정의
  [
    `  configServers,\n}) => {\n  const filteredTools = [];`,
    `  configServers,\n  userRole,\n}) => {\n  const KC_RESTRICTED_MCP = new Set(${RESTRICTED}); // ${MARKER}\n  const filteredTools = [];`,
  ],
  // MCP 도구 파싱 후, 비-admin 의 제한 서버 도구는 거부 (existing/registry 보존보다 먼저)
  [
    `    if (registryUnavailable && existingToolSet?.has(tool)) {`,
    `    if (userRole !== 'ADMIN' && KC_RESTRICTED_MCP.has(parts[1])) {\n      continue; // ${MARKER}: 제한 MCP 서버는 ADMIN 만\n    }\n\n    if (registryUnavailable && existingToolSet?.has(tool)) {`,
  ],
  // 4개 호출 사이트에 userRole 전달
  [
    `    agentData.tools = await filterAuthorizedTools({\n      tools,\n      userId,\n      availableTools,\n      configServers,\n    });`,
    `    agentData.tools = await filterAuthorizedTools({\n      tools,\n      userId,\n      availableTools,\n      configServers,\n      userRole: req.user.role,\n    });`,
  ],
  [
    `        const approvedNew = await filterAuthorizedTools({\n          tools: newMCPTools,\n          userId: req.user.id,\n          availableTools,\n          configServers,\n        });`,
    `        const approvedNew = await filterAuthorizedTools({\n          tools: newMCPTools,\n          userId: req.user.id,\n          availableTools,\n          configServers,\n          userRole: req.user.role,\n        });`,
  ],
  [
    `      newAgentData.tools = await filterAuthorizedTools({\n        tools: newAgentData.tools,\n        userId,\n        availableTools,\n        existingTools: newAgentData.tools,\n        configServers,\n      });`,
    `      newAgentData.tools = await filterAuthorizedTools({\n        tools: newAgentData.tools,\n        userId,\n        availableTools,\n        existingTools: newAgentData.tools,\n        configServers,\n        userRole: req.user.role,\n      });`,
  ],
  [
    `      const filteredTools = await filterAuthorizedTools({\n        tools: updatedAgent.tools,\n        userId: req.user.id,\n        availableTools,\n        existingTools: updatedAgent.tools,\n        configServers,\n      });`,
    `      const filteredTools = await filterAuthorizedTools({\n        tools: updatedAgent.tools,\n        userId: req.user.id,\n        availableTools,\n        existingTools: updatedAgent.tools,\n        configServers,\n        userRole: req.user.role,\n      });`,
  ],
]);

// ── (2) 빌더 MCP 목록 숨김: getMCPTools ───────────────────────────────────
const MCP = '/app/api/server/controllers/mcp.js';
patch(MCP, [
  [
    `    const mcpConfig = await resolveAllMcpConfigs(userId, req.user);\n    const configuredServers = Object.keys(mcpConfig);`,
    `    const mcpConfig = await resolveAllMcpConfigs(userId, req.user);\n    // ${MARKER}: 비-admin 에겐 제한 MCP 서버를 빌더 목록에서 숨김.\n    const KC_RESTRICTED_MCP = ${RESTRICTED};\n    const _kcAll = Object.keys(mcpConfig);\n    const configuredServers = req.user?.role === 'ADMIN'\n      ? _kcAll\n      : _kcAll.filter((s) => !KC_RESTRICTED_MCP.includes(s));`,
  ],
]);

console.log('[mcp_admin_only] done');
