/** A mermaid `mindmap` as a tree, for reading it as text rather than as a radial picture. */

export interface MindNode {
  label: string
  children: MindNode[]
}

/** True when the mermaid source is a mind map. */
export function isMindmap(source: string): boolean {
  return /^\s*mindmap\s*$/m.test(source.split('\n').find((line) => line.trim() && !line.trim().startsWith('%%')) ?? '')
}

// Mermaid node shapes around a label: ((circle)), (rounded), [square], {{hexagon}}, ))bang((, )cloud(.
const SHAPED = /^[^\s()[\]{}]*?(\(\(|\(|\[|\{\{|\)\)|\))(.*?)(\)\)|\)|\]|\}\}|\(\(|\()$/

/** A node line's label: the shape and any id before it dropped, quotes and markdown-string ticks too. */
function labelOf(line: string): string {
  let text = line.replace(/:::.*$/, '').trim()
  const shaped = SHAPED.exec(text)
  if (shaped) text = shaped[2].trim()
  text = text.replace(/^"(.*)"$/, '$1').replace(/^`(.*)`$/, '$1')
  return text.trim()
}

/** The tree by indentation, or null when the source is not a mind map or has no root. */
export function parseMindmap(source: string): MindNode | null {
  if (!isMindmap(source)) return null
  const lines = source.split('\n')
  const start = lines.findIndex((line) => /^\s*mindmap\s*$/.test(line))
  let root: MindNode | null = null
  const stack: { indent: number; node: MindNode }[] = []
  for (const line of lines.slice(start + 1)) {
    const trimmed = line.trim()
    // Comments, icons and classes decorate the node above; they are not nodes.
    if (!trimmed || trimmed.startsWith('%%') || trimmed.startsWith('::icon') || trimmed.startsWith(':::')) continue
    const label = labelOf(trimmed)
    if (!label) continue
    const indent = line.length - line.trimStart().length
    const node: MindNode = { label, children: [] }
    if (!root) {
      root = node
      stack.push({ indent, node })
      continue
    }
    while (stack.length > 1 && stack[stack.length - 1].indent >= indent) stack.pop()
    // A second line at the root's indent has no parent in mermaid; it is kept under the root.
    stack[stack.length - 1].node.children.push(node)
    stack.push({ indent, node })
  }
  return root
}

function count(node: MindNode): number {
  return 1 + node.children.reduce((sum, child) => sum + count(child), 0)
}

/** Labels a mind map picture holds at reading size on a page; past this its deepest level is left out. */
const PICTURE_NODES = 40

/** Source to draw as a picture: a large mind map is cut to two levels below the root; others are unchanged. */
export function pictureSource(source: string): string {
  const root = parseMindmap(source)
  if (!root || count(root) <= PICTURE_NODES) return source
  // Brackets would read as a node shape again.
  const plain = (label: string) => label.replace(/[()[\]{}]/g, ' ').replace(/\s+/g, ' ').trim() || '·'
  const lines = ['mindmap', `  root((${plain(root.label)}))`]
  for (const branch of root.children) {
    lines.push(`    ${plain(branch.label)}`)
    for (const child of branch.children) lines.push(`      ${plain(child.label)}`)
  }
  return lines.join('\n')
}
