import { Extension, Node, type Editor } from '@tiptap/core'
import Image from '@tiptap/extension-image'
import { TaskItem, TaskList } from '@tiptap/extension-list'
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table'
import type { Node as PmNode } from '@tiptap/pm/model'
import { Plugin, PluginKey, type EditorState, type Transaction } from '@tiptap/pm/state'
import { Decoration, DecorationSet, type EditorView } from '@tiptap/pm/view'
import StarterKit from '@tiptap/starter-kit'
import { figureColumns, isTotal } from '@/components/report/figureColumns'
import { fenceHtml } from '@/components/report/reportHtml'
import { newSectionId } from '@/lib/reportDoc'

// All MIT (no `@tiptap-pro`). The schema holds only what Markdown can write back:
// `lib/reportDoc.ts` reads stored Markdown into it and serialises it out again.

/** Pandoc's `<!-- pagebreak -->`, which the exporters read. */
const PageBreak = Node.create({
  name: 'pageBreak',
  group: 'block',
  atom: true,
  selectable: true,
  parseHTML: () => [{ tag: 'div[data-page-break="true"]' }],
  renderHTML: () => ['div', { 'data-page-break': 'true', class: 'page-break' }],
})

export interface FenceStorage {
  /** Drawn picture per mermaid source. */
  pictures: Map<string, string>
  listeners: Set<() => void>
  /** Double-click on a drawn block: its source is edited as Markdown. */
  onEdit: (() => void) | null
}

declare module '@tiptap/core' {
  interface Storage {
    fenceBlock: FenceStorage
  }
}

const escape = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const FENCE_NAMES: Record<string, string> = {
  mermaid: '도식', chart: '차트', kpi: '핵심 수치', steps: '단계', cards: '카드', callout: '강조 상자',
}

/** A drawn fence (mermaid, chart, KPI strip…): shown drawn, its source edited in 「Markdown 원문」. */
const FenceBlock = Node.create({
  name: 'fenceBlock',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: false,
  addAttributes: () => ({ lang: { default: 'mermaid' }, source: { default: '' } }),
  addStorage: (): FenceStorage => ({ pictures: new Map(), listeners: new Set(), onEdit: null }),
  parseHTML: () => [{
    tag: 'div[data-fence]',
    getAttrs: (node) => ({
      lang: (node as HTMLElement).getAttribute('data-fence') ?? 'mermaid',
      source: (node as HTMLElement).getAttribute('data-source') ?? '',
    }),
  }],
  renderHTML: ({ node }) => ['div', { 'data-fence': node.attrs.lang, 'data-source': node.attrs.source }],
  addNodeView() {
    return ({ node, editor }) => {
      let current = node
      const dom = document.createElement('div')
      dom.className = 'fence-block'
      dom.contentEditable = 'false'
      const storage = (editor as Editor).storage.fenceBlock
      const render = () => {
        const lang = String(current.attrs.lang)
        const source = String(current.attrs.source)
        dom.dataset.fence = lang
        dom.title = `${FENCE_NAMES[lang] ?? lang} · 두 번 누르면 Markdown 원문에서 고칩니다`
        const drawn = fenceHtml(lang, source, escape, storage.pictures)
        const empty = lang === 'mermaid' && !storage.pictures.get(source)
        dom.innerHTML = empty
          ? `<figure class="diagram fence-pending"><figcaption>${FENCE_NAMES.mermaid} · 그리는 중…</figcaption></figure>`
          : drawn || `<pre class="fence-source"><code>${escape(source)}</code></pre>`
      }
      render()
      storage.listeners.add(render)
      dom.addEventListener('dblclick', () => storage.onEdit?.())
      return {
        dom,
        update: (next) => {
          if (next.type !== current.type) return false
          current = next
          render()
          return true
        },
        ignoreMutation: () => true,
        destroy: () => storage.listeners.delete(render),
      }
    }
  },
})

/** Markdown the editor cannot show (raw HTML, definitions): kept verbatim. */
const RawBlock = Node.create({
  name: 'rawBlock',
  group: 'block',
  atom: true,
  selectable: true,
  addAttributes: () => ({ source: { default: '' } }),
  parseHTML: () => [{ tag: 'pre[data-raw]', getAttrs: (node) => ({ source: (node as HTMLElement).textContent ?? '' }) }],
  renderHTML: ({ node }) => ['pre', { 'data-raw': '', class: 'raw-markdown', title: 'Markdown 원문에서 고칩니다' }, node.attrs.source],
})

const RawInline = Node.create({
  name: 'rawInline',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  addAttributes: () => ({ source: { default: '' } }),
  parseHTML: () => [{ tag: 'code[data-raw]', getAttrs: (node) => ({ source: (node as HTMLElement).textContent ?? '' }) }],
  renderHTML: ({ node }) => ['code', { 'data-raw': '', class: 'raw-markdown' }, node.attrs.source],
})

const sectionKey = new PluginKey('sectionIds')

/**
 * Section ids on `##` headings. Every `##` has one, and only one heading has a given
 * id: a split or pasted heading gets a new id, a `###` keeps one only as the level-2
 * section it was read as. Stored on the node so a moved or edited section keeps it.
 */
const SectionIds = Extension.create({
  name: 'sectionIds',
  addGlobalAttributes: () => [{
    types: ['heading'],
    attributes: {
      sectionId: {
        default: null,
        // Copied with the node on a split; the plugin settles duplicates.
        keepOnSplit: false,
        parseHTML: (element) => element.getAttribute('data-section-id'),
        renderHTML: (attributes) => (attributes.sectionId ? { 'data-section-id': attributes.sectionId } : {}),
      },
    },
  }],
  addProseMirrorPlugins: () => [new Plugin({
    key: sectionKey,
    appendTransaction: (transactions: readonly Transaction[], _old: EditorState, state: EditorState) => {
      if (!transactions.some((tr) => tr.docChanged)) return null
      const seen = new Set<string>()
      let tr: Transaction | null = null
      state.doc.forEach((node, pos) => {
        if (node.type.name !== 'heading') return
        const id = node.attrs.sectionId as string | null
        const level = Number(node.attrs.level)
        if (level === 2 && (!id || seen.has(id))) {
          const fresh = newSectionId()
          tr = (tr ?? state.tr).setNodeMarkup(pos, undefined, { ...node.attrs, sectionId: fresh })
          seen.add(fresh)
        } else if (id && (level > 3 || seen.has(id))) {
          tr = (tr ?? state.tr).setNodeMarkup(pos, undefined, { ...node.attrs, sectionId: null })
        } else if (id) seen.add(id)
      })
      return tr ? (tr as Transaction).setMeta('addToHistory', false) : null
    },
  })],
})

/** A section heading's number (「1.」) and where its content headings' counters start. */
export type SectionLabel = { label: string; reset: string }

const labelsKey = new PluginKey('sectionLabels')

/** Format heading numbers (「1.」, 「가.」) on section headings, and the counters their content headings count from, as the page view draws them; nothing saved. */
const SectionLabels = Extension.create({
  name: 'sectionLabels',
  addProseMirrorPlugins: () => [new Plugin<{ labels: Record<string, SectionLabel>; set: DecorationSet }>({
    key: labelsKey,
    state: {
      init: () => ({ labels: {}, set: DecorationSet.empty }),
      apply: (tr, old, _before, state) => {
        const labels = (tr.getMeta(labelsKey) as Record<string, SectionLabel> | undefined) ?? old.labels
        if (!tr.docChanged && labels === old.labels) return old
        const found: Decoration[] = []
        state.doc.forEach((node, pos) => {
          const at = node.type.name === 'heading' ? labels[node.attrs.sectionId as string] : undefined
          if (!at) return
          found.push(Decoration.node(pos, pos + node.nodeSize, {
            ...(at.label ? { 'data-num': at.label } : {}),
            // a reset on the heading reaches the headings after it, up to the next section
            style: `counter-reset: ${at.reset}`,
          }))
        })
        return { labels, set: DecorationSet.create(state.doc, found) }
      },
    },
    props: { decorations(state) { return this.getState(state)?.set } },
  })],
})

/** Sets the heading labels `SectionLabels` draws. */
export function setSectionLabels(editor: Editor, labels: Record<string, SectionLabel>) {
  editor.view.dispatch(editor.state.tr.setMeta(labelsKey, labels).setMeta('addToHistory', false))
}

const PLACEHOLDER = /(?<!\[)\[(?!(?:미정|참고|붙임|별첨|첨부|예시|주의|요약|결론|그림|표|공지|안내)\]|여기에|(?:그림|표|사진|도표|도식)\s)([가-힣](?:[가-힣 ·]{0,8}[가-힣])?)\](?!\(|\[|\])/g

/** Bracket blanks (「[담당자]」) marked as the page view marks them; nothing saved. */
const Placeholders = Extension.create({
  name: 'placeholders',
  addProseMirrorPlugins: () => {
    const mark = (doc: PmNode) => {
      const found: Decoration[] = []
      doc.descendants((node, pos) => {
        if (!node.isText || !node.text || node.marks.some((m) => m.type.name === 'code')) return
        for (const hit of node.text.matchAll(PLACEHOLDER)) {
          const from = pos + (hit.index ?? 0)
          found.push(Decoration.inline(from, from + hit[0].length, { class: 'doc-placeholder' }))
        }
      })
      return DecorationSet.create(doc, found)
    }
    return [new Plugin({
      key: new PluginKey('placeholders'),
      state: { init: (_, state) => mark(state.doc), apply: (tr, old) => (tr.docChanged ? mark(tr.doc) : old) },
      props: { decorations(state) { return this.getState(state) } },
    })]
  },
})

/** `.fig`/`.total` decorations as the preview and exporters draw them; nothing saved. */
const FigureColumns = Extension.create({
  name: 'figureColumns',
  addProseMirrorPlugins() {
    const mark = (doc: PmNode) => {
      const found: Decoration[] = []
      doc.descendants((node, pos) => {
        if (node.type.name !== 'table') return true
        const rows: { pos: number; node: PmNode }[] = []
        node.forEach((row, offset) => rows.push({ pos: pos + 1 + offset, node: row }))
        const text = rows.map(({ node: row }) => {
          const cells: string[] = []
          row.forEach((cell) => cells.push(cell.textContent))
          return cells
        })
        const columns = figureColumns(text)
        rows.forEach(({ pos: rowPos, node: row }, at) => {
          if (at && isTotal(text[at])) found.push(Decoration.node(rowPos, rowPos + row.nodeSize, { class: 'total' }))
          row.forEach((cell, offset, index) => {
            if (columns.has(index)) found.push(Decoration.node(rowPos + 1 + offset, rowPos + 1 + offset + cell.nodeSize, { class: 'fig' }))
          })
        })
        return false
      })
      return DecorationSet.create(doc, found)
    }
    return [new Plugin({
      key: new PluginKey('figureColumns'),
      state: {
        init: (_, state) => mark(state.doc),
        apply: (tr, old) => (tr.docChanged ? mark(tr.doc) : old),
      },
      props: { decorations(state) { return this.getState(state) } },
    })]
  },
})

/** ProseMirror's own read of a pending DOM change and selection (`DOMObserver.flush`). */
function syncSelection(view: EditorView) {
  ;(view as unknown as { domObserver?: { flush?: () => void } }).domObserver?.flush?.()
}

/**
 * Typing over a selection that spans blocks (a section boundary, say) is done here,
 * not by the browser: a native edit merges the blocks with inline styles (the
 * heading's weight comes back as bold). An IME composition clears the range first.
 */
const CrossBlockInput = Extension.create({
  name: 'crossBlockInput',
  // Before the keymaps: they act on the selection this reads.
  priority: 1000,
  addProseMirrorPlugins: () => [new Plugin({
    key: new PluginKey('crossBlockInput'),
    props: {
      // Inside the page's shadow root `selectionchange` can arrive after the next key:
      // the browser's selection is read first, so Enter splits where the caret is.
      handleKeyDown: (view) => {
        syncSelection(view)
        return false
      },
      handleDOMEvents: {
        beforeinput: (view, event) => {
          syncSelection(view)
          const input = event as InputEvent
          const { selection } = view.state
          if (selection.empty || selection.$from.sameParent(selection.$to)) return false
          if (input.inputType === 'insertText' && input.data) {
            input.preventDefault()
            view.dispatch(view.state.tr.insertText(input.data).scrollIntoView())
            return true
          }
          if (input.inputType.startsWith('delete')) {
            input.preventDefault()
            view.dispatch(view.state.tr.deleteSelection().scrollIntoView())
            return true
          }
          return false
        },
        compositionstart: (view) => {
          const { selection } = view.state
          if (!selection.empty && !selection.$from.sameParent(selection.$to)) {
            view.dispatch(view.state.tr.deleteSelection())
          }
          return false
        },
      },
    },
  })],
})

/** The web report editor's schema: Markdown features only. */
export const REPORT_EXTENSIONS = [
  StarterKit.configure({
    // `#` is the title, outside the editor.
    heading: { levels: [2, 3, 4, 5, 6] },
    // No underline in Markdown.
    underline: false,
    link: { openOnClick: false, autolink: true, linkOnPaste: true, defaultProtocol: 'https' },
  }),
  SectionIds,
  SectionLabels,
  CrossBlockInput,
  TaskList,
  TaskItem.configure({ nested: true }),
  // GFM tables: a header row, no merged cells.
  Table.configure({ resizable: false, allowTableNodeSelection: true }),
  TableRow,
  TableHeader,
  TableCell,
  FigureColumns,
  Placeholders,
  // Embedded, not linked: the export is mailed.
  Image.configure({ inline: true, allowBase64: true }),
  PageBreak,
  FenceBlock,
  RawBlock,
  RawInline,
]

/** Editor CSS inside the page's shadow root (global CSS cannot reach it). */
export const REPORT_EDITOR_CSS = `
  .ProseMirror { outline: none; }
  .ProseMirror .page-break { position: relative; display: block; height: 24px; margin: 14px 0; border-top: 1px dashed #9ca3af; cursor: pointer; }
  .ProseMirror .page-break::after { content: '쪽 나누기'; position: absolute; top: -9px; right: 8px; padding: 0 6px; background: white; color: #777; font-size: 11px; line-height: 18px; }
  .ProseMirror .ProseMirror-selectednode { outline: 2px solid var(--accent, #5b5bd6); outline-offset: 2px; }
  .ProseMirror h2[data-num]::before, .ProseMirror h3[data-num]::before { content: attr(data-num) ' '; }
  .ProseMirror h2:empty::after { content: '절 제목'; color: #9ca3af; }
  .ProseMirror ul[data-type="taskList"] { list-style: none; padding-left: 0.2em; }
  .ProseMirror ul[data-type="taskList"] li { display: flex; gap: 0.5em; align-items: flex-start; }
  .ProseMirror ul[data-type="taskList"] li > label { margin-top: 0.25em; }
  .ProseMirror ul[data-type="taskList"] li > div { flex: 1; }
  .ProseMirror .fence-block { margin: 6mm 0; cursor: pointer; }
  .ProseMirror .fence-block .fence-pending { min-height: 30mm; display: grid; place-items: center; border: 1px dashed #c7c7d1; border-radius: 4px; color: #777; }
  .ProseMirror .fence-source, .ProseMirror pre { white-space: pre-wrap; font-family: 'D2Coding', Consolas, monospace; font-size: 0.85em; background: #f6f6f8; border-radius: 4px; padding: 3mm; }
  .ProseMirror .raw-markdown { font-family: 'D2Coding', Consolas, monospace; font-size: 0.85em; color: #666; background: #f6f6f8; }
  .ProseMirror a { color: var(--accent, #1e3a8a); text-decoration: underline; }
  .ProseMirror img { cursor: default; }
  .ProseMirror .doc-placeholder { background: #fff3bf; color: #5c4400; border-radius: 2px; }
`

/** Where `needle` occurs in each paragraph or heading, across marks; an inline atom (a picture) breaks a match. */
export function textMatches(doc: PmNode, needle: string): { from: number; to: number }[] {
  const found: { from: number; to: number }[] = []
  if (!needle) return found
  doc.descendants((node, pos) => {
    if (!node.isTextblock) return true
    let text = ''
    const at: number[] = []
    node.forEach((child, offset) => {
      const start = pos + 1 + offset
      if (child.isText && child.text) {
        for (let index = 0; index < child.text.length; index += 1) at.push(start + index)
        text += child.text
      } else {
        at.push(start)
        text += '\ufffc'
      }
    })
    let index = 0
    while ((index = text.indexOf(needle, index)) >= 0) {
      found.push({ from: at[index], to: at[index + needle.length - 1] + 1 })
      index += needle.length
    }
    return false
  })
  return found
}
