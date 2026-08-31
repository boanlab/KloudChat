/**
 * The name one diagram's source is stored under.
 *
 * Must agree with `report_export.diagram_key` exactly — the browser writes
 * under this key and the exporters look up under that one, and a mismatch is a
 * picture that is stored and never found.
 *
 * Whitespace-insensitive for the same reason it is there: the two ends
 * normalise differently, one reading text out of a rendered block and the
 * other out of stored Markdown, and a key that moved on a trailing space would
 * quietly break.
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
