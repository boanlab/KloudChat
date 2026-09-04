/**
 * Storage key for one diagram's source: first 16 hex chars of SHA-256 over the
 * trimmed source with trailing whitespace stripped per line. Must match
 * `report_export.diagram_key` on the server exactly.
 */
export async function diagramKey(source: string): Promise<string> {
  const normalised = (source ?? '')
    .trim()
    .split('\n')
    .map((line) => line.replace(/\s+$/, ''))
    .join('\n')
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(normalised))
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
    .slice(0, 16)
}
