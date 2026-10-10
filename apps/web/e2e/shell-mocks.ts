/**
 * Reads the signed-in app shell makes on every page, answered empty, for specs that
 * mock the API themselves and count any other request as unexpected: the 「문의·신고」
 * reply badge (`useIssueBadge`) and the bot badges (`useBotAttentionBadge`) in AppShell.
 */
export function shellRead(method: string, path: string): { json: unknown } | null {
  if (method === 'GET' && path === '/issues/unread') return { json: { count: 0 } }
  if (method === 'GET' && path === '/publications/attention') return { json: { bots: 0, reviews: 0 } }
  return null
}
