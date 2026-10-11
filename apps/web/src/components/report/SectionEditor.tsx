import Image from '@tiptap/extension-image'
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table'
import TextAlign from '@tiptap/extension-text-align'
import {
  BackgroundColor,
  Color,
  FontFamily,
  FontSize,
  TextStyle,
} from '@tiptap/extension-text-style'
import { EditorContent, useEditor, type Editor } from '@tiptap/react'
import { Extension, Node } from '@tiptap/core'
import type { Node as PmNode } from '@tiptap/pm/model'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'
import { figureColumns, isTotal } from '@/components/report/figureColumns'
import {
  CalloutBlock,
  CardsBlock,
  ChartNode,
  DiagramBlock,
  KpiBlock,
  StepsBlock,
} from '@/components/report/blocks'
import StarterKit from '@tiptap/starter-kit'
import { useEffect, useRef } from 'react'

/** Distinct from a horizontal rule. */
const PageBreak = Node.create({
  name: 'pageBreak',
  group: 'block',
  atom: true,
  selectable: true,
  parseHTML: () => [{ tag: 'div[data-page-break="true"]' }],
  renderHTML: () => ['div', { 'data-page-break': 'true', class: 'page-break' }],
})

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    paragraphLineHeight: {
      setLineHeight: (value: string) => ReturnType
      unsetLineHeight: () => ReturnType
    }
  }
}

const ParagraphLineHeight = Extension.create({
  name: 'paragraphLineHeight',
  addGlobalAttributes() {
    return [{
      types: ['paragraph', 'heading'],
      attributes: {
        lineHeight: {
          default: null,
          parseHTML: (element) => element.style.lineHeight || null,
          renderHTML: (attributes) => attributes.lineHeight
            ? { style: `line-height: ${attributes.lineHeight}` }
            : {},
        },
      },
    }]
  },
  addCommands() {
    return {
      setLineHeight: (value: string) => ({ chain }) =>
        chain().updateAttributes('paragraph', { lineHeight: value })
          .updateAttributes('heading', { lineHeight: value }).run(),
      unsetLineHeight: () => ({ chain }) =>
        chain().updateAttributes('paragraph', { lineHeight: null })
          .updateAttributes('heading', { lineHeight: null }).run(),
    }
  },
})

/** 개조식 roles `GAEJO_CSS` styles (※ note, 붙임 line), kept through editing. */
const GAEJO_PARAGRAPHS = ['gaejo-note', 'gaejo-attach']

const GaejoParagraph = Extension.create({
  name: 'gaejoParagraph',
  addGlobalAttributes() {
    return [{
      types: ['paragraph', 'listItem'],
      attributes: {
        gaejo: {
          default: null,
          parseHTML: (element) => GAEJO_PARAGRAPHS.find((name) => element.classList.contains(name)) ?? null,
          renderHTML: (attributes) => (attributes.gaejo ? { class: attributes.gaejo } : {}),
        },
      },
    }]
  },
})

// All MIT (no `@tiptap-pro`). Editor writes HTML (`format`); `services/richtext.py` converts back, server sanitises on save.
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

const EXTENSIONS = [
  StarterKit.configure({
    // Wrapper draws the section heading: levels 1-2 excluded.
    heading: { levels: [3, 4] },
  }),
  TextStyle,
  FontSize,
  FontFamily,
  // Must also pass the server's `_EDITABLE_STYLE` allowlist.
  Color,
  BackgroundColor,
  ParagraphLineHeight,
  GaejoParagraph,
  TextAlign.configure({ types: ['heading', 'paragraph'] }),
  Table.configure({ resizable: true }),
  FigureColumns,
  TableRow,
  TableHeader,
  TableCell,
  // Embedded, not linked: the export is mailed.
  Image.configure({ allowBase64: true }),
  PageBreak,
  KpiBlock,
  StepsBlock,
  DiagramBlock,
  ChartNode,
  CardsBlock,
  CalloutBlock,
]

export function SectionEditor({
  html,
  editable,
  onReady,
  onMount,
  onChange,
}: {
  html: string
  editable: boolean
  /** One toolbar drives the focused editor. */
  onReady?: (editor: Editor | null) => void
  /** For document-wide operations. */
  onMount?: (editor: Editor | null) => void
  onChange?: (html: string) => void
}) {
  // Against the last emitted serialisation: undo back to the original is a real update.
  const lastHtml = useRef<string | null>(null)
  const onReadyRef = useRef(onReady)
  const onMountRef = useRef(onMount)
  useEffect(() => {
    onReadyRef.current = onReady
  }, [onReady])
  useEffect(() => {
    onMountRef.current = onMount
  }, [onMount])

  const editor = useEditor({
    extensions: EXTENSIONS,
    content: html,
    editable,
    immediatelyRender: false,
    onCreate: ({ editor: live }) => {
      lastHtml.current = live.getHTML()
      onMountRef.current?.(live)
    },
    onUpdate: ({ editor: live }) => {
      const next = live.getHTML()
      if (lastHtml.current === null || next === lastHtml.current) return
      lastHtml.current = next
      onChange?.(next)
    },
    onFocus: ({ editor: live }) => onReady?.(live),
  })

  useEffect(() => {
    editor?.setEditable(editable)
  }, [editor, editable])

  useEffect(() => {
    // Outside changes only; writing while focused moves the caret.
    if (editor && !editor.isFocused && editor.getHTML() !== html) {
      editor.commands.setContent(html, { emitUpdate: false })
      lastHtml.current = editor.getHTML()
    }
  }, [editor, html])

  // Unmount only; callback identities change per render.
  useEffect(() => () => {
    onReadyRef.current?.(null)
    onMountRef.current?.(null)
  }, [])

  // Template seeds reach the body through `doc-body`.
  return <EditorContent editor={editor} className="doc-body" />
}
