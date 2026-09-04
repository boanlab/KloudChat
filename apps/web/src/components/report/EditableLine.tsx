import { useEffect, useRef, type ElementType } from 'react'

/**
 * Single-line `contentEditable` heading. The DOM is written only on outside
 * changes (never while focused), Enter commits, and paste is plain text.
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
    // Writing while focused would move the caret.
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
