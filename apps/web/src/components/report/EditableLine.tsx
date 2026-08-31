import { useEffect, useRef, type ElementType } from 'react'

/**
 * One line of a document that is text and nothing else — a title, a heading.
 *
 * `contentEditable` rather than an editor. Tiptap owns the bodies because a
 * body has tables, lists and marks in it; a heading has none of those, and
 * mounting a rich-text editor per heading would cost a schema, a toolbar's
 * worth of commands and a second selection model to say the same thing a
 * `contenteditable` span says.
 *
 * The gap this closes is small and total: the page view rendered these as
 * plain React text, so a typo in a title could be fixed in the web view's
 * Markdown editor and nowhere else. In a view that looks like paper and lets
 * you type into the paragraphs, a heading that refuses the caret reads as
 * broken rather than as deliberate.
 *
 * Three details, each from a way `contenteditable` goes wrong:
 *
 * * **The DOM is written once.** React must not re-render the text while
 *   somebody is typing into it — that moves the caret to the end on every
 *   keystroke. The value goes in through a ref on mount and on outside
 *   changes only.
 * * **Enter ends the line.** A heading is one line; the default would insert a
 *   `<br>` and quietly make it two.
 * * **Paste arrives as text.** Pasting a styled heading from a browser would
 *   otherwise bring its markup with it, and this element has nowhere to put it.
 */
export function EditableLine({
  as: Tag = 'h2',
  value,
  editable,
  onChange,
  placeholder,
}: {
  as?: ElementType
  value: string
  editable: boolean
  /** Called on blur and on Enter, never per keystroke. */
  onChange: (next: string) => void
  placeholder?: string
}) {
  const node = useRef<HTMLElement>(null)

  useEffect(() => {
    const el = node.current
    // Only when the document changed underneath — a rewrite landing, a version
    // restored. Writing while focused would move the caret.
    if (el && document.activeElement !== el && el.textContent !== value) {
      el.textContent = value
    }
  }, [value])

  const commit = () => {
    const next = (node.current?.textContent ?? '').replace(/\s+/g, ' ').trim()
    if (next !== value) onChange(next)
  }

  return (
    <Tag
      ref={node}
      contentEditable={editable}
      suppressContentEditableWarning
      spellCheck={false}
      data-placeholder={placeholder}
      onBlur={commit}
      onKeyDown={(e: React.KeyboardEvent) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          ;(e.target as HTMLElement).blur()
        }
        if (e.key === 'Escape') {
          if (node.current) node.current.textContent = value
          ;(e.target as HTMLElement).blur()
        }
      }}
      onPaste={(e: React.ClipboardEvent) => {
        e.preventDefault()
        const text = e.clipboardData.getData('text/plain').replace(/\s+/g, ' ')
        document.execCommand('insertText', false, text)
      }}
      style={editable ? { cursor: 'text' } : undefined}
    />
  )
}
