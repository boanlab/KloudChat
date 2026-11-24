/** An answer cut into the blocks a voice conversation reads: a paragraph, a whole list, a whole table, a code block.
 * Each block carries `source` (the block as written, cleaned of code, link addresses and citation numbers) for the
 * server to put into spoken words, and `text` (rule-cleaned) to read when that fails. */
import { plainText, speakableText } from './speech'

export type BlockKind = 'para' | 'list' | 'table' | 'code'

export interface SpokenBlock {
  kind: BlockKind
  /** The block's own words, for the rewrite. Empty for code: nothing of it is read. */
  source: string
  /** What is read when the rewrite does not come back: always a sentence or more. */
  text: string
}

const CODE_LINE = '코드는 화면에 적어 두었습니다.'
const TABLE_LINE = '표는 화면에 적어 두었습니다.'
const MORE_LINE = '그 밖에 몇 가지가 더 있습니다.'
/** A request carries at most this much of a block. */
const SOURCE_MAX = 4000
const TEXT_MAX = 560
/** A paragraph longer than this is read in pieces, each cut at a sentence. */
const PARA_MAX = 280
/** The first sound of an answer starts at the first complete sentence of this length, not the paragraph's end. */
const EARLY_MIN = 20
/** Items of a list read in full when the rewrite fails. */
const LIST_HEAD = 4

const LIST_ITEM = /^\s*(?:[-*+•]|\d+[.)])\s+/
const STRUCTURE = /^\s*(?:[-*+•]\s|\d+[.)]\s|#{1,6}\s|\||```|>)/
const SENTENCE_END = /[.!?…。！？]+["'”’)\]]*(?=\s)/g
const HAS_WORD = /[\p{L}\p{N}]/u

const endsSentence = (text: string) => /[.!?…。！？]["'”’)\]]*$/.test(text)
const sentence = (text: string) => (endsSentence(text) ? text : `${text}.`)

/** Sentence-sized pieces of one paragraph, none longer than `PARA_MAX` unless a sentence is. */
function pieces(text: string): string[] {
  const out: string[] = []
  let held = ''
  let from = 0
  const take = (part: string) => {
    if (held && held.length + part.length + 1 > PARA_MAX) {
      out.push(held)
      held = part
    } else {
      held = held ? `${held} ${part}` : part
    }
  }
  SENTENCE_END.lastIndex = 0
  for (let m = SENTENCE_END.exec(text); m; m = SENTENCE_END.exec(text)) {
    const end = m.index + m[0].length
    take(text.slice(from, end).trim())
    from = end
  }
  const rest = text.slice(from).trim()
  if (rest) take(rest)
  if (held) out.push(held)
  return out
}

interface Open {
  kind: 'para' | 'list' | 'table'
  lines: string[]
}

export class BlockFeed {
  private pos = 0
  private fence = false
  private cur: Open | null = null
  private heading = ''
  private said = false
  /** Characters of the open paragraph already read from its first sentences. */
  private early = 0

  /** `text`: the whole answer so far. `final`: nothing more follows. */
  push(text: string, final = false): SpokenBlock[] {
    const out: SpokenBlock[] = []
    let at = text.indexOf('\n', this.pos)
    while (at !== -1) {
      this.line(text.slice(this.pos, at), out)
      this.pos = at + 1
      at = text.indexOf('\n', this.pos)
    }
    if (final) {
      const rest = text.slice(this.pos)
      this.pos = text.length
      if (rest.trim()) this.line(rest, out)
      this.close(out)
      this.fenceEnd(out)
      if (this.heading) this.say(out, { kind: 'para', source: this.heading, text: speakableText(this.heading) })
      this.heading = ''
    } else if (!this.said) {
      this.first(text.slice(this.pos), out)
    }
    return out
  }

  private say(out: SpokenBlock[], block: SpokenBlock) {
    if (!HAS_WORD.test(block.text)) return
    this.said = true
    out.push(block)
  }

  /** Before anything was read: the open paragraph's complete sentences are read at once. */
  private first(partial: string, out: SpokenBlock[]) {
    if (this.fence || (this.cur && this.cur.kind !== 'para')) return
    const tail = partial.trim()
    const starts = tail && !STRUCTURE.test(partial)
    if (!this.cur && !starts) return
    const joined = plainText([...(this.cur?.lines ?? []), ...(starts ? [tail] : [])].join(' '))
    let cut = -1
    SENTENCE_END.lastIndex = 0
    for (let m = SENTENCE_END.exec(joined); m; m = SENTENCE_END.exec(joined)) cut = m.index + m[0].length
    if (cut < 0 || joined.slice(this.early, cut).trim().length < EARLY_MIN) return
    const lead = this.heading ? `${this.heading}. ` : ''
    this.heading = ''
    for (const part of pieces(joined.slice(this.early, cut).trim())) {
      this.say(out, { kind: 'para', source: lead + part, text: speakableText(lead + part) })
    }
    this.early = cut
  }

  private line(raw: string, out: SpokenBlock[]) {
    const line = raw.trim()
    if (this.fence) {
      if (line.startsWith('```')) this.fenceEnd(out)
      return
    }
    if (line.startsWith('```')) {
      this.close(out)
      this.fence = true
      return
    }
    if (!line) {
      if (this.cur?.kind !== 'list') this.close(out)
      return
    }
    if (/^[-*_=\s|:]{3,}$/.test(line) && !line.includes('|')) {
      this.close(out)
      return
    }
    if (line.startsWith('|')) {
      this.into('table', out).lines.push(line)
      return
    }
    const head = /^#{1,6}\s+(.*)$/.exec(line)
    if (head) {
      this.close(out)
      this.heading = plainText(head[1])
      return
    }
    if (LIST_ITEM.test(raw)) {
      this.into('list', out).lines.push(line.replace(LIST_ITEM, ''))
      return
    }
    if (this.cur?.kind === 'list' && /^\s+\S/.test(raw)) {
      const items = this.cur.lines
      items[items.length - 1] = `${items[items.length - 1]} ${line}`
      return
    }
    this.into('para', out).lines.push(line.replace(/^>\s?/, ''))
  }

  /** The open block as `kind`: a block of another kind is read first. */
  private into(kind: Open['kind'], out: SpokenBlock[]): Open {
    if (this.cur?.kind === kind) return this.cur
    this.close(out)
    this.cur = { kind, lines: [] }
    return this.cur
  }

  private fenceEnd(out: SpokenBlock[]) {
    if (!this.fence) return
    this.fence = false
    this.say(out, { kind: 'code', source: '', text: CODE_LINE })
  }

  /** The open block is complete: it is read. */
  private close(out: SpokenBlock[]) {
    const open = this.cur
    this.cur = null
    const lead = this.heading
    if (!open) return
    this.heading = ''
    if (open.kind === 'para') {
      const body = plainText(open.lines.join(' ')).slice(this.early).trim()
      this.early = 0
      if (!HAS_WORD.test(body)) return
      const prefix = lead ? `${lead}. ` : ''
      for (const part of pieces(body)) {
        this.say(out, { kind: 'para', source: prefix + part, text: speakableText(prefix + part) })
      }
      return
    }
    this.early = 0
    if (open.kind === 'table') {
      const rows = open.lines
        .filter((row) => !/^\|?[\s:|-]+\|?$/.test(row))
        .map((row) => row.replace(/^\||\|$/g, '').split('|').map((cell) => plainText(cell)).join(' | '))
      const source = (lead ? `${lead}\n` : '') + rows.join('\n')
      this.say(out, { kind: 'table', source: source.slice(0, SOURCE_MAX), text: (lead ? `${speakableText(lead)}. ` : '') + TABLE_LINE })
      return
    }
    const items = open.lines.map((item) => plainText(item)).filter((item) => HAS_WORD.test(item))
    if (!items.length) return
    const source = (lead ? `${lead}\n` : '') + items.map((item) => `- ${item}`).join('\n')
    const head = items.slice(0, LIST_HEAD).map((item) => sentence(speakableText(item)))
    const text = [lead ? sentence(speakableText(lead)) : '', ...head, items.length > LIST_HEAD ? MORE_LINE : '']
      .filter(Boolean)
      .join(' ')
    this.say(out, { kind: 'list', source: source.slice(0, SOURCE_MAX), text: text.slice(0, TEXT_MAX) })
  }
}
