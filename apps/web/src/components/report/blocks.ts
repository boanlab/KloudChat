import { Node } from '@tiptap/core'

/**
 * The two structured blocks, as nodes Tiptap knows.
 *
 * Without these they do not exist in the page view at all. Tiptap parses the
 * HTML it is handed into its own schema and drops everything the schema has no
 * node for, so a `<div class="kpi">` went in and nothing came out — the 서식
 * styled a selector that was never in the document. That is the same failure
 * the table had before `extension-table`, and it is invisible from the web
 * view, which renders Markdown and never asks Tiptap anything.
 *
 * Both are atoms. A strip of figures and a numbered procedure are written in
 * the web view's Markdown editor, where the fence is the source of truth, and
 * making their insides editable here would give one block two authors with no
 * way to reconcile them. As atoms they round-trip exactly: parsed from the
 * markup, rendered back as the same markup, and carried through a save
 * untouched by an edit made elsewhere in the section.
 *
 * `priority` is above the default 50 on the procedure. Its tag is `ol`, which
 * StarterKit's own ordered list also claims — without it the list wins, the
 * class is dropped, and the block silently degrades into an ordinary list.
 */
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
 * A mermaid diagram or chart, as a node that survives being saved.
 *
 * This one is not cosmetic. Without it, a mermaid fence had no Tiptap node, so
 * opening the page view and typing one character anywhere in the section
 * deleted every diagram and chart in it — the section is stored as HTML the
 * moment it is edited, and what Tiptap could not parse was already gone.
 *
 * The node carries its own **source**, not just the picture. A picture alone
 * would survive the save and the export and still lose the diagram: nobody
 * could change a figure in it afterwards, here or in the web view, because the
 * text it was drawn from would no longer exist anywhere. `richtext` reads the
 * source back out and writes the fence again, so the round trip is lossless.
 *
 * The picture is whatever a reader has already had drawn for this diagram —
 * mermaid does not run in here, because rendering into a subtree ProseMirror
 * believes it owns is how an editor and a renderer end up fighting over the
 * same nodes. Undrawn, the figure is empty and the picture arrives the next
 * time somebody opens the web view.
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


/**
 * A chart, as a node that survives being saved.
 *
 * It carries its **source** — the fence text — and not the drawing. The
 * drawing is arithmetic anybody can redo; the numbers are the document. A node
 * holding a rendered chart would survive a save and still lose the chart,
 * because nothing left could be changed or re-exported.
 *
 * The same shape the diagram node takes, and for the same reason: without a
 * node here, one keystroke anywhere in the section deletes every chart in it,
 * silently, on the way into Tiptap.
 */
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
