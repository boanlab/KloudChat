/** Bracket placeholders 「[담당자]」; mirrors `services/placeholders.py` (test-checked). Not links, citations or check boxes. */
export const PLACEHOLDER = /(?<!\[)\[(?!(?:미정|참고|붙임|별첨|첨부|예시|주의|요약|결론|그림|표|공지|안내)\]|여기에|(?:그림|표|사진|도표|도식)\s)([가-힣](?:[가-힣 ·]{0,8}[가-힣])?)\](?!\(|\[|\])/g

const OWN_NAME = /^[가-힣](?:[가-힣 ·]{0,8}[가-힣])?$/

/** What a blank head field shows: its own name in brackets, 「보고자(부서)」 → 「[보고자·부서]」. */
export function blankFor(label: string): string {
  const name = label.split(/\s+/).join(' ').replace(/\s{0,4}\(([^()]{1,20})\)/g, '·$1').replace(/^[ ·]+|[ ·]+$/g, '')
  return OWN_NAME.test(name) ? `[${name}]` : '[내용]'
}

/** Whether a value is a placeholder and nothing else. */
export const isBlank = (value: string) => {
  const text = value.trim()
  const found = [...text.matchAll(PLACEHOLDER)]
  return found.length === 1 && found[0][0] === text
}

/** A placeholder's mark in the paged preview and print: highlighted, so the person sees what to fill. */
export const PLACEHOLDER_CSS = '.doc-placeholder { background: #fff3bf; color: #5c4400; border-radius: 2px; padding: 0 0.1em; -webkit-print-color-adjust: exact; print-color-adjust: exact; }'

// Elements whose text is never marked: code is literal, a link's words are its own.
const SKIP_TAGS = /^(?:code|pre|a|script|style|textarea|title)$/i

/** HTML with the placeholders in its text wrapped in `span.doc-placeholder`; tags untouched. */
export function markPlaceholders(html: string): string {
  let skipping = 0
  let marked = false
  return html.split(/(<[^>]{0,4000}>)/).map((part) => {
    const tag = /^<(\/?)([A-Za-z][A-Za-z0-9-]*)/.exec(part)
    if (tag) {
      if (SKIP_TAGS.test(tag[2]) && !part.endsWith('/>')) skipping = Math.max(0, skipping + (tag[1] ? -1 : 1))
      // Text already inside a mark (a blank head field) is not marked twice.
      marked = !tag[1] && part.includes('doc-placeholder')
      return part
    }
    return skipping || marked ? part : part.replace(PLACEHOLDER, '<span class="doc-placeholder">$&</span>')
  }).join('')
}

type HastNode = {
  type: string
  tagName?: string
  value?: string
  properties?: Record<string, unknown>
  children?: HastNode[]
}

function split(value: string): HastNode[] | null {
  const out: HastNode[] = []
  let at = 0
  for (const found of value.matchAll(PLACEHOLDER)) {
    const start = found.index ?? 0
    if (start > at) out.push({ type: 'text', value: value.slice(at, start) })
    out.push({ type: 'element', tagName: 'span', properties: { className: ['doc-placeholder'] }, children: [{ type: 'text', value: found[0] }] })
    at = start + found[0].length
  }
  if (!out.length) return null
  if (at < value.length) out.push({ type: 'text', value: value.slice(at) })
  return out
}

function walk(node: HastNode) {
  if (!node.children || (node.tagName && SKIP_TAGS.test(node.tagName))) return
  node.children = node.children.flatMap((child) => {
    if (child.type === 'text' && child.value) return split(child.value) ?? [child]
    walk(child)
    return [child]
  })
}

/** A rehype plugin: each placeholder in the rendered text becomes `span.doc-placeholder`. */
export function rehypePlaceholders() {
  return (tree: HastNode) => walk(tree)
}
