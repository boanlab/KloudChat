import { Check, Copy } from 'lucide-react'
import { useEffect, useState } from 'react'
import { copyText } from '@/lib/clipboard'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

/** A small copy button beside an id, address or link; says 복사됨 for a moment. */
export function CopyButton({
  text,
  label,
  className,
}: {
  text: string
  /** What is copied, for the button's name (「IP 주소」). */
  label: string
  className?: string
}) {
  const t = useT()
  const [state, setState] = useState<'idle' | 'done' | 'failed'>('idle')
  useEffect(() => {
    if (state === 'idle') return
    const timer = window.setTimeout(() => setState('idle'), 1600)
    return () => window.clearTimeout(timer)
  }, [state])
  const name =
    state === 'done'
      ? t('{what} 복사됨').replace('{what}', label)
      : state === 'failed'
        ? t('복사하지 못했습니다')
        : t('{what} 복사').replace('{what}', label)
  return (
    <button
      type="button"
      onClick={(e) => {
        // Inside a clickable row: copy, do not open the row.
        e.stopPropagation()
        void copyText(text).then((ok) => setState(ok ? 'done' : 'failed'))
      }}
      onKeyDown={(e) => e.stopPropagation()}
      aria-label={name}
      title={name}
      className={cn(
        'inline-grid size-6 shrink-0 place-items-center rounded-control align-middle text-faint transition-colors hover:bg-elevated hover:text-fg focus-visible:outline-2 focus-visible:outline-accent',
        state === 'done' && 'text-success',
        state === 'failed' && 'text-danger',
        className,
      )}
    >
      {state === 'done' ? <Check size={12} aria-hidden /> : <Copy size={12} aria-hidden />}
      <span className="sr-only" role="status">
        {state === 'idle' ? '' : name}
      </span>
    </button>
  )
}
