/**
 * Project-source citations in a chat answer. An answer that cites project material ends with
 * a source list whose items carry no address (「- [2] 03강 논리 시계.pdf · 3쪽」, as
 * `services/agent.py` writes them); its inline 「[2]」 are turned into `#cite-2-k` links the
 * answer body renders as buttons that open the source viewer at the cited passage. `k`
 * indexes `claims`, the sentence each citation closes, which the viewer looks for.
 */
import { createContext } from 'react'

export interface CitedSource {
  /** Document name as the list gives it. */
  name: string
  page: number | null
}

export interface CitationTarget {
  n: number
  source: CitedSource
  claim: string
}

/** Opens the source viewer for a citation; absent outside a project conversation. */
export const CitationContext = createContext<((target: CitationTarget) => void) | null>(null)

const LIST_HEAD = /^[ \t]{0,3}(?:#{1,6}[ \t]{0,3}|\*\*)[ \t]{0,2}(?:출처|참고[ \t]?자료|Sources?)[ \t]{0,2}(?:\*\*)?[ \t]{0,2}[:：]?[ \t]*$/m
const LIST_ITEM = /^[ \t]{0,3}[-*][ \t]{1,3}\[(\d{1,3})\][ \t]{1,3}(.+)$/

/** The answer's own-material sources by number (items with no web address). */
export function materialSources(content: string): Map<number, CitedSource> {
  const out = new Map<number, CitedSource>()
  const head = LIST_HEAD.exec(content)
  if (!head) return out
  for (const line of content.slice(head.index + head[0].length).split('\n')) {
    const m = LIST_ITEM.exec(line)
    if (!m) {
      if (line.trim()) break
      continue
    }
    const label = m[2].trim()
    if (/https?:\/\//.test(label)) continue
    const parts = label.split(' · ').map((part) => part.trim())
    const page = parts.map((part) => /^(\d{1,5})쪽$/.exec(part)).find(Boolean)
    out.set(Number(m[1]), { name: parts[0], page: page ? Number(page[1]) : null })
  }
  return out
}

const CITE = /(?<!\[)\[(\d{1,3}(?:[ \t]{0,2},[ \t]{0,2}\d{1,3}){0,8})\](?!\()/g

/** The sentence (or table cell) a citation at `at` closes. */
function claimBefore(text: string, at: number): string {
  const start = Math.max(text.lastIndexOf('\n', at - 1) + 1, 0)
  const head = text.slice(start, at).replace(/\[\d{1,3}(?:[ \t]{0,2},[ \t]{0,2}\d{1,3})*\]/g, ' ')
  const pieces = head.split(/(?<=[.!?。])\s+|\|/)
  const last = pieces.map((piece) => piece.trim()).filter((piece) => piece.length > 1)
  return (last.at(-1) ?? head).replace(/[*_#>`]/g, '').trim().slice(0, 400)
}

/** `content` with material citations as `#cite-n-k` links, and the claims they close. */
export function linkCitations(content: string): { content: string; claims: string[]; sources: Map<number, CitedSource> } {
  const sources = materialSources(content)
  const claims: string[] = []
  if (!sources.size) return { content, claims, sources }
  const head = LIST_HEAD.exec(content)
  const bodyEnd = head ? head.index : content.length
  const pieces = content.slice(0, bodyEnd).split('```')
  for (let i = 0; i < pieces.length; i += 2) {
    const piece = pieces[i]
    pieces[i] = piece.replace(CITE, (whole, spec: string, offset: number) => {
      const numbers = spec.split(',').map((x) => Number(x.trim()))
      if (!numbers.every((n) => sources.has(n))) return whole
      const claim = claimBefore(piece, offset)
      return numbers
        .map((n) => {
          claims.push(claim)
          return `[[${n}]](#cite-${n}-${claims.length - 1})`
        })
        .join('')
    })
  }
  return { content: pieces.join('```') + content.slice(bodyEnd), claims, sources }
}

/** `#cite-n-k` → `[n, k]`, or null. */
export function citationHref(href: string | undefined): [number, number] | null {
  const m = /^#cite-(\d{1,3})-(\d{1,4})$/.exec(href ?? '')
  return m ? [Number(m[1]), Number(m[2])] : null
}
