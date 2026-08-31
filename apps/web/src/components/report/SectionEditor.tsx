import Image from '@tiptap/extension-image'
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table'
import TextAlign from '@tiptap/extension-text-align'
import { FontFamily, FontSize, TextStyle } from '@tiptap/extension-text-style'
import { EditorContent, useEditor, type Editor } from '@tiptap/react'
import { ChartNode, DiagramBlock, KpiBlock, StepsBlock } from '@/components/report/blocks'
import StarterKit from '@tiptap/starter-kit'
import { useEffect, useRef } from 'react'

/**
 * One section's body, editable in place.
 *
 * Tiptap over `contenteditable` by hand: tables and Korean IME composition are
 * the two places a hand-rolled editor goes wrong, and both are places where
 * going wrong means somebody's typing disappears. Every package here is MIT —
 * the paid `@tiptap-pro/*` extensions are deliberately not used, and nothing
 * in this toolbar needs them.
 *
 * The editor writes HTML, which is why a section carries `format`. The four
 * things this toolbar offers — size, face, alignment, and emphasis colour —
 * have no Markdown at all, so a section somebody has formatted cannot be
 * stored as Markdown without silently throwing the formatting away on save.
 * `services/richtext.py` converts back for the exporters.
 *
 * No `dangerouslySetInnerHTML` anywhere: content goes in through Tiptap's own
 * parser, which builds nodes from its schema and drops everything outside it.
 * The server sanitises again on save, because the browser is not the only
 * thing that can PATCH.
 */
export const EXTENSIONS = [
  StarterKit.configure({
    // The section's own heading is drawn by the wrapper above the body, so a
    // level-1 or level-2 heading typed in here would print the title twice.
    heading: { levels: [3, 4] },
  }),
  TextStyle,
  FontSize,
  FontFamily,
  TextAlign.configure({ types: ['heading', 'paragraph'] }),
  Table.configure({ resizable: true }),
  TableRow,
  TableHeader,
  TableCell,
  // Embedded rather than linked. A report is exported and mailed, and a
  // picture that lives at a URL is a picture that is missing by then.
  Image.configure({ allowBase64: true }),
  // Without these two the schema has no node for a strip of figures or a
  // numbered procedure, and the paragraph above is exactly what happens to
  // them: dropped on the way in, so the 서식 styles a selector that is not in
  // the document.
  KpiBlock,
  StepsBlock,
  DiagramBlock,
  ChartNode,
]

export function SectionEditor({
  html,
  editable,
  onReady,
  onChange,
}: {
  html: string
  editable: boolean
  /** Hands the live editor up so one toolbar can drive whichever has focus. */
  onReady?: (editor: Editor | null) => void
  onChange?: (html: string) => void
}) {
  /**
   * The document as Tiptap first parsed it.
   *
   * Not the string that was handed in. Tiptap reads the HTML into its own
   * schema and writes it back out in its own shape — attribute order, empty
   * paragraphs, table structure — so `getHTML()` differs from the input on a
   * document nobody has touched. Reporting that as a change put a 저장 button
   * on every report the moment it was opened, which teaches people that the
   * button means nothing.
   *
   * Captured on creation and compared against, so the first real keystroke is
   * the first thing anybody is told about.
   */
  const pristine = useRef<string | null>(null)

  const editor = useEditor({
    extensions: EXTENSIONS,
    content: html,
    editable,
    // Tiptap 3 mutates the DOM it is given; React has to be told to stop
    // owning that subtree or the two fight over every keystroke.
    immediatelyRender: false,
    onCreate: ({ editor: live }) => {
      pristine.current = live.getHTML()
    },
    onUpdate: ({ editor: live }) => {
      const next = live.getHTML()
      if (pristine.current === null || next === pristine.current) return
      onChange?.(next)
    },
    onFocus: ({ editor: live }) => onReady?.(live),
  })

  useEffect(() => {
    editor?.setEditable(editable)
  }, [editor, editable])

  useEffect(() => {
    // Only when the document changed underneath — a rewrite landing, a version
    // restored. Writing on every render would move the caret to the end on
    // every keystroke.
    if (editor && !editor.isFocused && editor.getHTML() !== html) {
      editor.commands.setContent(html, { emitUpdate: false })
      // The document underneath changed — a rewrite landed, a version was
      // restored. That is the new pristine state, not an edit somebody made.
      pristine.current = editor.getHTML()
    }
  }, [editor, html])

  useEffect(() => () => onReady?.(null), [onReady])

  // `doc-body` is the class the seeds reach the body through. `EditorContent`
  // renders a div of its own and moves the ProseMirror node into it, so a
  // template rule written as `section > *` lands on this div rather than on
  // anything a person typed — naming it is what lets the seed reach past it.
  return <EditorContent editor={editor} className="doc-body" />
}
