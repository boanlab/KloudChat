import { Info } from 'lucide-react'
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import type { RunRow } from '@/lib/runInfo'

/**
 * A small details panel opened by pressing ⓘ (click, tap, Enter or Space — never hover, so a
 * desktop and a phone behave alike). Closes on a click outside or Escape; Escape is taken here
 * first, so inside a menu it closes the panel and leaves the menu open.
 *
 * The panel is rendered in flow beside the button (`panel`), not floated: inside a scrolling menu
 * a floating panel would be clipped.
 */
export function useInfoPanel() {
  const [open, setOpen] = useState(false)
  const id = useId()
  const wrap = useRef<HTMLDivElement | null>(null)
  const button = useRef<HTMLButtonElement | null>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent | TouchEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false)
    }
    // Capture on the document: runs before a menu's own Escape handling and stops it.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopImmediatePropagation()
      setOpen(false)
      button.current?.focus()
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('touchstart', onDown)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('touchstart', onDown)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [open])
  return { open, id, wrap, button, toggle: () => setOpen((o) => !o) }
}

export function InfoButton({
  panel,
  label,
  className,
  testId,
}: {
  panel: ReturnType<typeof useInfoPanel>
  label: string
  className?: string
  testId?: string
}) {
  return (
    <button
      ref={panel.button}
      type="button"
      aria-label={label}
      aria-expanded={panel.open}
      aria-describedby={panel.open ? panel.id : undefined}
      data-testid={testId}
      // The row it sits in selects on click; this only opens the panel.
      onClick={(e) => {
        e.stopPropagation()
        panel.toggle()
      }}
      onMouseDown={(e) => e.stopPropagation()}
      className={cn(
        'grid size-8 shrink-0 place-items-center rounded-control text-faint transition-colors hover:bg-elevated hover:text-fg focus-visible:text-fg',
        panel.open && 'bg-elevated text-fg',
        className,
      )}
    >
      <Info size={14} aria-hidden />
    </button>
  )
}

/** The opened panel: a label/value list and one closing sentence. */
export function InfoPanel({
  panel,
  rows,
  className,
  testId,
  extra,
}: {
  panel: ReturnType<typeof useInfoPanel>
  rows: RunRow[]
  className?: string
  testId?: string
  extra?: ReactNode
}) {
  if (!panel.open) return null
  return (
    <div
      id={panel.id}
      role="note"
      data-testid={testId}
      onClick={(e) => e.stopPropagation()}
      className={cn('rounded-control border border-line bg-elevated px-3 py-2 text-sm text-fg', className)}
    >
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5">
        {rows.map((r) => (
          <div key={r.label} className="contents">
            <dt className="text-muted">{r.label}</dt>
            <dd className="min-w-0 break-words">{r.value}</dd>
          </div>
        ))}
      </dl>
      {extra}
    </div>
  )
}
