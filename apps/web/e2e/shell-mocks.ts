/**
 * Reads the signed-in app shell makes on every page, answered empty, for specs that
 * mock the API themselves and count any other request as unexpected. Today that is
 * the 「문의·신고」 reply badge (`useIssueBadge` in AppShell).
 */
export function shellRead(method: string, path: string): { json: unknown } | null {
  if (method === 'GET' && path === '/issues/unread') return { json: { count: 0 } }
  return null
}
