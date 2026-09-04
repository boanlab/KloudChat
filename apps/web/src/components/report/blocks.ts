import { Node } from '@tiptap/core'

// Tiptap atoms for the structured report blocks; without a node Tiptap drops
// the markup on edit. `priority: 60` beats StarterKit's claim on `ol`/`figure`.
function pairsFrom(element: HTMLElement, selector: string): [string, string][] {
  return Array.from(element.querySelectorAll(selector)).map((row) => [
    row.querySelector('strong')?.textContent?.trim() ?? '',
    row.querySelector('span')?.textContent?.trim() ?? '',
  ])
}

export const KpiBlock = Node.create({
  name: 'kpiBlock',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: false,
  addAttributes: () => ({ pairs: { default: [] as [string, string][] } }),
  parseHTML: () => [
    {
      tag: 'div.kpi',
      getAttrs: (node) => ({ pairs: pairsFrom(node as HTMLElement, ':scope > div') }),
    },
  ],
  renderHTML: ({ node }) => [
    'div',
    { class: 'kpi' },
    ...(node.attrs.pairs as [string, string][]).map(([value, label]) => [
      'div',
      {},
      ['strong', {}, value],
      ['span', {}, label],
    ]),
  ],
})

export const StepsBlock = Node.create({
  name: 'stepsBlock',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: false,
  addAttributes: () => ({ pairs: { default: [] as [string, string][] } }),
  parseHTML: () => [
    {
      tag: 'ol.steps',
      priority: 60,
      getAttrs: (node) => ({ pairs: pairsFrom(node as HTMLElement, ':scope > li') }),
    },
  ],
  renderHTML: ({ node }) => [
    'ol',
    { class: 'steps' },
    ...(node.attrs.pairs as [string, string][]).map(([name, detail]) => [
      'li',
      {},
      ['strong', {}, name],
      ...(detail ? [['span', {}, detail]] : []),
    ]),
  ],
})

/**
 * Mermaid diagram: carries its source (so `richtext` can write the fence back)
 * and the stored picture. Mermaid does not run inside the editor.
 */
export const DiagramBlock = Node.create({
  name: 'diagramBlock',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: false,
  addAttributes: () => ({
    source: { default: '' },
    src: { default: '' },
  }),
  parseHTML: () => [
    {
      tag: 'figure.diagram',
      priority: 60,
      getAttrs: (node) => {
        const el = node as HTMLElement
        return {
          source: el.getAttribute('data-source') ?? '',
          src: el.querySelector('img')?.getAttribute('src') ?? '',
        }
      },
    },
  ],
  renderHTML: ({ node }) => [
    'figure',
    { class: 'diagram', 'data-source': node.attrs.source },
    ...(node.attrs.src ? [['img', { src: node.attrs.src, alt: '' }]] : []),
  ],
})

/** Chart: carries the fence source, not the drawing. */
export const ChartNode = Node.create({
  name: 'chartBlock',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: false,
  addAttributes: () => ({ source: { default: '' } }),
  parseHTML: () => [
    {
      tag: 'figure.chart',
      priority: 60,
      getAttrs: (node) => ({ source: (node as HTMLElement).getAttribute('data-source') ?? '' }),
    },
  ],
  renderHTML: ({ node }) => [
    'figure',
    { class: 'chart', 'data-source': node.attrs.source },
  ],
})

// `section.cards`, not `div.cards`: `richtext` reads the block back with a
// lazy close, and a wrapper sharing its children's tag would end at the first card.
function cardsFrom(element: HTMLElement): { title: string; items: string[] }[] {
  return Array.from(element.querySelectorAll(':scope > div')).map((card) => ({
    title: card.querySelector('h3, h4')?.textContent?.trim() ?? '',
    items: Array.from(card.querySelectorAll('li, p')).map(
      (line) => line.textContent?.trim() ?? '',
    ),
  }))
}

export const CardsBlock = Node.create({
  name: 'cardsBlock',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: false,
  addAttributes: () => ({ cards: { default: [] as { title: string; items: string[] }[] } }),
  parseHTML: () => [
    {
      tag: 'section.cards',
      priority: 60,
      getAttrs: (node) => ({ cards: cardsFrom(node as HTMLElement) }),
    },
  ],
  renderHTML: ({ node }) => [
    'section',
    { class: 'cards' },
    ...(node.attrs.cards as { title: string; items: string[] }[]).map((card) => [
      'div',
      {},
      ['h3', {}, card.title],
      ...(card.items.length
        ? [['ul', {}, ...card.items.map((line) => ['li', {}, line])]]
        : []),
    ]),
  ],
})

export const CalloutBlock = Node.create({
  name: 'calloutBlock',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: false,
  addAttributes: () => ({ title: { default: '' }, lines: { default: [] as string[] } }),
  parseHTML: () => [
    {
      tag: 'section.callout',
      priority: 60,
      getAttrs: (node) => {
        const el = node as HTMLElement
        return {
          title: el.querySelector('h3, h4')?.textContent?.trim() ?? '',
          lines: Array.from(el.querySelectorAll('p')).map(
            (line) => line.textContent?.trim() ?? '',
          ),
        }
      },
    },
  ],
  renderHTML: ({ node }) => [
    'section',
    { class: 'callout' },
    ['h3', {}, node.attrs.title],
    ...(node.attrs.lines as string[]).map((line) => ['p', {}, line]),
  ],
})
