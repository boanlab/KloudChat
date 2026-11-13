/** What each office preview (`HwpPreview`, `DocxPreview`, `PptxPreview`) is given: the
 *  file's bytes, and where to say it is drawn or could not be. */
export interface OfficePreviewProps {
  bytes: ArrayBuffer
  name: string
  /** A label for the drawn pages, for assistive technology. */
  label: string
  onReady: () => void
  onFail: (reason: string) => void
}

export const reasonOf = (err: unknown) => (err instanceof Error ? err.message : String(err))

const SVG_NS = 'http://www.w3.org/2000/svg'
/** Elements a page drawing never needs and that could run or fetch something. */
const DROPPED = new Set([
  'script', 'foreignobject', 'iframe', 'object', 'embed', 'animate', 'animatemotion',
  'animatetransform', 'set', 'handler', 'listener',
])

/**
 * One page's SVG, as rhwp draws it, made safe to put into the page: no scripts, handlers,
 * embedded documents or animation, and links only to its own parts or to pictures it carries.
 */
export function safePage(markup: string): SVGSVGElement | null {
  const parsed = new DOMParser().parseFromString(markup, 'image/svg+xml')
  const root = parsed.documentElement
  if (root.namespaceURI !== SVG_NS || root.localName !== 'svg') return null
  for (const element of Array.from(root.querySelectorAll('*'))) {
    if (DROPPED.has(element.localName.toLowerCase()) || element.namespaceURI !== SVG_NS) {
      element.remove()
      continue
    }
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.localName.toLowerCase()
      const value = attribute.value.trim().toLowerCase()
      const link = name === 'href' || name === 'src'
      if (name.startsWith('on') || value.includes('javascript:') ||
        (link && !value.startsWith('#') && !value.startsWith('data:image/'))) {
        element.removeAttributeNode(attribute)
      }
    }
  }
  for (const attribute of Array.from(root.attributes)) {
    if (attribute.localName.toLowerCase().startsWith('on')) root.removeAttributeNode(attribute)
  }
  return document.importNode(root, true) as unknown as SVGSVGElement
}
