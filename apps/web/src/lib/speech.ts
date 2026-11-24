/** What a voice conversation reads aloud: an answer in markdown, cut into sentences of speakable text. */
import { forSpeech } from './speechKo'

const CITATION = /\[\^?\d+(?:\s*[,\-–]\s*\d+)*\]/g
const LINK = /!?\[([^\]]*)\]\([^)]*\)/g
const BARE_URL = /https?:\/\/\S+/g

/** One markdown chunk as plain text: no code, tables, links, citation numbers or markup; wording untouched. */
export function plainText(markdown: string): string {
  const lines = markdown.split('\n').flatMap((raw) => {
    const line = raw.trim()
    // Tables and rules are looked at, not listened to.
    if (!line || line.startsWith('|') || /^[-*_=\s|:]{3,}$/.test(line)) return []
    return [
      line
        .replace(/^#{1,6}\s+/, '')
        .replace(/^>\s?/, '')
        .replace(/^(?:[-*+•]|\d+[.)])\s+/, ''),
    ]
  })
  const plain = lines
    .join(' ')
    .replace(LINK, '$1')
    .replace(BARE_URL, '')
    .replace(CITATION, '')
    .replace(/`+/g, '')
    .replace(/(\*\*|__|~~)/g, '')
    .replace(/(^|\s)[*_]+|[*_]+(\s|$)/g, '$1$2')
    .replace(/\s+/g, ' ')
    .trim()
  return plain
}

/** One markdown chunk as spoken text: plain, with symbols, numbers and English made sayable. */
export function speakableText(markdown: string): string {
  return forSpeech(plainText(markdown))
}

const BOUNDARY = /[.!?…。！？]+["'”’)\]]*\s|\n/g
const HAS_WORD = /[\p{L}\p{N}]/u
/** A request is a sentence or two: shorter ones wait for the next, longer ones are cut. */
const MIN_CHARS = 24
const MAX_CHARS = 280

/** Feeds an answer as it grows and returns the speakable pieces that are complete. */
export class SentenceFeed {
  private pos = 0
  private fence = false
  private held = ''

  /** `text`: the whole answer so far. `final`: nothing more follows. */
  push(text: string, final = false): string[] {
    const out: string[] = []
    const take = (chunk: string) => {
      if (/^\s*```/.test(chunk)) {
        this.fence = !this.fence
        return
      }
      if (this.fence) return
      const spoken = speakableText(chunk)
      if (!HAS_WORD.test(spoken)) return
      this.held = this.held ? `${this.held} ${spoken}` : spoken
      if (this.held.length >= MIN_CHARS) this.flush(out)
    }
    BOUNDARY.lastIndex = this.pos
    for (let m = BOUNDARY.exec(text); m; m = BOUNDARY.exec(text)) {
      const end = m.index + m[0].length
      take(text.slice(this.pos, end))
      this.pos = end
    }
    if (final) {
      take(text.slice(this.pos))
      this.pos = text.length
      this.flush(out)
    }
    return out
  }

  private flush(out: string[]) {
    let rest = this.held
    this.held = ''
    while (rest.length > MAX_CHARS) {
      const cut = rest.lastIndexOf(' ', MAX_CHARS)
      const at = cut > MAX_CHARS / 2 ? cut : MAX_CHARS
      out.push(rest.slice(0, at).trim())
      rest = rest.slice(at).trim()
    }
    if (rest) out.push(rest)
  }
}
