import { sha256Digest } from './cryptoFallback'

/** First 16 hex of SHA-256 over the trimmed, line-trimmed source; must match `report_export.diagram_key`. */
export async function diagramKey(source: string): Promise<string> {
  const normalised = (source ?? '')
    .trim()
    .split('\n')
    .map((line) => line.replace(/\s+$/, ''))
    .join('\n')
  const digest = await sha256Digest(new TextEncoder().encode(normalised))
  return Array.from(digest)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
    .slice(0, 16)
}
