import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'

/** Size a slide is laid out at before scaling — the seed's 16:9 screen. */
const SLIDE_W = 1280
const SLIDE_H = 720

/** One slide of a deck 서식's sample, scaled into a 16:9 box; sandboxed with no permissions. */
export function SlideFrame({ id, n, lazy = true }: { id: string; n: number; lazy?: boolean }) {
  const box = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(0)

  useEffect(() => {
    const node = box.current
    if (!node) return
    const fit = () => setScale(node.clientWidth / SLIDE_W)
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  return (
    <div ref={box} className="relative aspect-video w-full overflow-hidden bg-white">
      <iframe
        src={`/api/design-templates/${id}/preview?slide=${n}`}
        sandbox=""
        tabIndex={-1}
        loading={lazy ? 'lazy' : 'eager'}
        title=""
        aria-hidden="true"
        className="pointer-events-none absolute left-0 top-0 origin-top-left"
        // Hidden until measured, or one frame at scale 1 paints over its neighbours.
        style={{ width: SLIDE_W, height: SLIDE_H, transform: `scale(${scale})`, visibility: scale ? 'visible' : 'hidden' }}
      />
    </div>
  )
}

/** A deck sample as a slide show: one large slide, arrows, ←/→ and a strip to jump from. */
export function DeckSlideViewer({ id, count }: { id: string; count: number }) {
  const t = useT()
  const [current, setCurrent] = useState(1)
  const strip = useRef<HTMLDivElement>(null)
  const go = (n: number) => setCurrent(Math.min(count, Math.max(1, n)))

  // ←/→ anywhere in the open dialog; typing fields keep their arrows.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return
      const target = e.target as HTMLElement | null
      if (target?.closest('input, textarea, select, [contenteditable="true"]')) return
      if (e.key === 'ArrowLeft') setCurrent((n) => Math.max(1, n - 1))
      else if (e.key === 'ArrowRight') setCurrent((n) => Math.min(count, n + 1))
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [count])

  // Keep the current thumbnail in sight when the strip scrolls (narrow screens).
  useEffect(() => {
    strip.current
      ?.querySelector<HTMLElement>(`[data-slide="${current}"]`)
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [current])

  const label = (n: number) => t('{n}번 슬라이드').replace('{n}', String(n))

  return (
    <div className="space-y-3">
      <div className="overflow-hidden rounded-control border border-line">
        <SlideFrame id={id} n={current} lazy={false} />
      </div>
      <div className="flex items-center justify-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => go(current - 1)} disabled={current <= 1} aria-label={t('이전 슬라이드')} title={t('이전 슬라이드')}>
          <ChevronLeft size={16} />
        </Button>
        <span className="min-w-[4.5rem] text-center text-sm tabular-nums text-muted" aria-live="polite">
          {current} / {count}
        </span>
        <Button variant="ghost" size="icon" onClick={() => go(current + 1)} disabled={current >= count} aria-label={t('다음 슬라이드')} title={t('다음 슬라이드')}>
          <ChevronRight size={16} />
        </Button>
      </div>
      {/* Shares the width on a wide screen; scrolls sideways on a phone. */}
      <div ref={strip} role="group" aria-label={t('슬라이드 목록')} className="flex gap-2 overflow-x-auto pb-1">
        {Array.from({ length: count }, (_, i) => i + 1).map((n) => (
          <button
            key={n}
            type="button"
            data-slide={n}
            onClick={() => go(n)}
            aria-label={label(n)}
            aria-current={n === current ? 'true' : undefined}
            className={cn(
              'w-24 shrink-0 overflow-hidden rounded-control border outline-none transition-colors sm:min-w-0 sm:flex-1 sm:shrink',
              'focus-visible:ring-2 focus-visible:ring-accent',
              n === current ? 'border-accent ring-1 ring-accent' : 'border-line hover:border-line-strong',
            )}
          >
            <SlideFrame id={id} n={n} />
          </button>
        ))}
      </div>
    </div>
  )
}
