/** Empty answers for the shell's per-page reads (`useIssueBadge`, `useBotAttentionBadge`, `ContextGauge`), for specs that count other requests as unexpected. */
export function shellRead(method: string, path: string): { json: unknown } | null {
  if (method === 'GET' && path === '/issues/unread') return { json: { count: 0 } }
  if (method === 'GET' && path === '/publications/attention') return { json: { bots: 0, reviews: 0 } }
  // Members' published agents, listed with the shared agents.
  if (method === 'GET' && path === '/bots') return { json: [] }
  if (method === 'POST' && path === '/sessions/context-usage') return { json: contextUsage() }
  return null
}

/** The context gauge's answer (`POST /sessions/context-usage`); a spec may change the numbers. */
export function contextUsage(extra: Record<string, unknown> = {}) {
  return {
    model: 'fixture/model', statedWindow: 64_000, window: 64_000, foreignTokenizer: false, reserve: 8_192,
    used: 3_200, parts: { system: 3_000, instructions: 200, memory: 0, files: 0, history: 0, draft: 0, tools: 0 },
    summarised: false, summarisedMessages: 0, headroom: 52_000, lastTurn: null, routed: false, routedBasis: null,
    ...extra,
  }
}
