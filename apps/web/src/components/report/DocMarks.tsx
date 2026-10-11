import { ChevronDown, ChevronUp, CircleAlert, Loader2 } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui'
import { findingProbe } from '@/lib/docMarks'
import { FINDING_LABEL } from '@/lib/stages'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import type { DocMarks, DocMarkSpan } from '@/types'

/** Stage names in words; others shown as named. */
const STAGE_LABEL: Record<string, string> = {
  'check:grounding': '근거 확인',
  'check:grounding-again': '근거 다시 확인',
  'check:answer-grounding': '근거 확인',
  verify: '검증',
}

type Target = {
  key: string
  kind: 'placeholder' | 'ungrounded' | 'finding'
  /** For a finding: the element its sentence starts in. */
  el: HTMLElement
  /** From the top of the scrolled content. */
  top: number
  height: number
  left: number
  sectionId: string | null
  text: string
  /** Index among identical blanks in its section. */
  nth: number
  tip: string
}

function spanTip(span: DocMarkSpan, t: (s: string) => string) {
  const stage = span.stage ? t(STAGE_LABEL[span.stage] ?? span.stage) : ''
  if (span.kind === 'ungrounded-replaced') {
    return [
      t('자료에 없음: 원래 값 {original} → {text}')
        .replace('{original}', span.original || '?')
        .replace('{text}', span.text),
      stage,
    ].filter(Boolean).join(' · ')
  }
  const why = span.reason || (span.code ? t(FINDING_LABEL[span.code] ?? span.code) : t('확인이 필요한 문장'))
  return [why, stage].filter(Boolean).join(' · ')
}

/** Section text searched without whitespace. */
function locate(scope: HTMLElement, needle: string): HTMLElement | null {
  const probe = findingProbe(needle)
  if (!probe) return null
  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT)
  const nodes: { node: Text; at: number }[] = []
  let text = ''
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    nodes.push({ node: node as Text, at: text.length })
    text += (node.nodeValue ?? '').replace(/\s+/g, '')
  }
  const found = text.indexOf(probe)
  if (found < 0) return null
  const hit = [...nodes].reverse().find((n) => n.at <= found)
  return hit?.node.parentElement ?? null
}

/** 「빈칸 n개 · 확인할 곳 m개」 with prev/next: blanks highlighted, grounding blanks dotted, flagged sentences margin-marked; web-view blanks filled in place. */
export function DocMarksLayer({
  root,
  marks,
  scanKey,
  canFill,
  margin,
  onFill,
}: {
  /** Positioned scrolling container. */
  root: HTMLElement | null
  marks: DocMarks | null
  /** Changes whenever the render may have (version, view, pane). */
  scanKey: string
  /** Web view (not writing or editing): blanks clickable. */
  canFill: boolean
  /** Margin markers need web-view geometry. */
  margin: boolean
  onFill: (sectionId: string, text: string, nth: number, value: string) => Promise<boolean>
}) {
  const t = useT()
  const [targets, setTargets] = useState<Target[]>([])
  const [current, setCurrent] = useState(-1)
  const [tip, setTip] = useState<{ text: string; top: number; left: number } | null>(null)
  const [filling, setFilling] = useState<Target | null>(null)
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const layerRef = useRef<HTMLDivElement>(null)
  const signature = useRef('')

  const scan = useCallback(() => {
    if (!root) return
    const box = root.getBoundingClientRect()
    const place = (el: Element) => {
      const r = el.getBoundingClientRect()
      return { top: r.top - box.top + root.scrollTop, height: r.height, left: r.left - box.left + root.scrollLeft }
    }
    const found: Target[] = []
    const seen = new Map<string, number>()
    // Ungrounded replacements per section, consumed in order.
    const pending = new Map<string, DocMarkSpan[]>()
    for (const section of marks?.sections ?? []) {
      pending.set(section.sectionId, section.spans.filter((s) => s.kind === 'ungrounded-replaced'))
    }
    root.querySelectorAll<HTMLElement>('.doc-placeholder').forEach((el, index) => {
      if (el.closest('[data-doc-marks]')) return
      const text = (el.textContent ?? '').trim()
      const sectionEl = el.closest<HTMLElement>('section[id^="sec-"]')
      const sectionId = sectionEl ? sectionEl.id.slice(4) : null
      const countKey = `${sectionId}|${text}`
      const nth = seen.get(countKey) ?? 0
      seen.set(countKey, nth + 1)
      const queue = sectionId ? pending.get(sectionId) : undefined
      const at = queue?.findIndex((s) => s.text === text) ?? -1
      const span = at >= 0 ? queue!.splice(at, 1)[0] : null
      const fillable = canFill && sectionId !== null && !el.classList.contains('tb-blank')
      const tipText = span ? spanTip(span, t) : ''
      el.classList.toggle('doc-ungrounded', !!span)
      if (tipText) el.dataset.markTip = tipText
      else delete el.dataset.markTip
      if (fillable) {
        el.dataset.markFill = ''
        el.tabIndex = 0
        el.setAttribute('role', 'button')
        el.setAttribute(
          'aria-label',
          [t('빈칸 {text}, 눌러서 채우기').replace('{text}', text), tipText].filter(Boolean).join('. '),
        )
      } else {
        delete el.dataset.markFill
        el.removeAttribute('tabindex')
        el.removeAttribute('role')
        if (tipText) el.setAttribute('aria-label', `${text}. ${tipText}`)
        else el.removeAttribute('aria-label')
      }
      found.push({
        key: `p${index}`, kind: span ? 'ungrounded' : 'placeholder', el, ...place(el),
        sectionId, text, nth, tip: tipText,
      })
    })
    for (const section of marks?.sections ?? []) {
      const scope = root.querySelector<HTMLElement>(`#sec-${CSS.escape(section.sectionId)}`) ?? (margin ? null : root)
      if (!scope) continue
      section.spans.filter((s) => s.kind === 'check-finding').forEach((span, i) => {
        const el = locate(scope, span.text)
        if (!el) return
        found.push({
          key: `f${section.sectionId}-${i}`, kind: 'finding', el, ...place(el),
          sectionId: section.sectionId, text: span.text, nth: i, tip: spanTip(span, t),
        })
      })
    }
    found.sort((a, b) => a.top - b.top || a.left - b.left)
    const next = found.map((x) => `${x.key}:${Math.round(x.top)}:${Math.round(x.left)}:${x.tip}`).join('|')
    if (next !== signature.current) {
      signature.current = next
      setTargets(found)
    }
  }, [root, marks, canFill, margin, t])

  // Rescan on document change or panel resize.
  useLayoutEffect(() => {
    scan()
  }, [scan, scanKey])
  useEffect(() => {
    if (!root) return
    let timer: ReturnType<typeof setTimeout> | null = null
    const soon = () => {
      if (timer) clearTimeout(timer)
      timer = setTimeout(scan, 120)
    }
    const mutations = new MutationObserver((records) => {
      if (records.every((r) => (r.target as Element).closest?.('[data-doc-marks]'))) return
      soon()
    })
    mutations.observe(root, { childList: true, subtree: true, characterData: true })
    const sizes = new ResizeObserver(soon)
    sizes.observe(root)
    return () => {
      if (timer) clearTimeout(timer)
      mutations.disconnect()
      sizes.disconnect()
    }
  }, [root, scan])

  useEffect(() => {
    if (!root) return
    const blankOf = (event: Event) =>
      (event.target as HTMLElement | null)?.closest?.<HTMLElement>('.doc-placeholder[data-mark-fill]') ?? null
    const open = (el: HTMLElement) => {
      const target = targets.find((x) => x.el === el)
      if (!target) return
      setFilling(target)
      setValue('')
      setError(null)
      setTip(null)
    }
    const onClick = (event: MouseEvent) => {
      const el = blankOf(event)
      if (el) open(el)
    }
    const onKey = (event: KeyboardEvent) => {
      const el = blankOf(event)
      if (el && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault()
        open(el)
      }
    }
    const box = () => root.getBoundingClientRect()
    const onOver = (event: Event) => {
      const el = (event.target as HTMLElement | null)?.closest?.<HTMLElement>('[data-mark-tip]')
      if (!el || el.closest('[data-doc-marks]')) return
      const r = el.getBoundingClientRect()
      const b = box()
      setTip({ text: el.dataset.markTip ?? '', top: r.bottom - b.top + root.scrollTop + 4, left: r.left - b.left })
    }
    const onOut = (event: Event) => {
      if ((event.target as HTMLElement | null)?.closest?.('[data-mark-tip]')) setTip(null)
    }
    root.addEventListener('click', onClick)
    root.addEventListener('keydown', onKey)
    root.addEventListener('mouseover', onOver)
    root.addEventListener('focusin', onOver)
    root.addEventListener('mouseout', onOut)
    root.addEventListener('focusout', onOut)
    return () => {
      root.removeEventListener('click', onClick)
      root.removeEventListener('keydown', onKey)
      root.removeEventListener('mouseover', onOver)
      root.removeEventListener('focusin', onOver)
      root.removeEventListener('mouseout', onOut)
      root.removeEventListener('focusout', onOut)
    }
  }, [root, targets])

  useEffect(() => {
    const el = targets[current]?.el
    if (!el || targets[current].kind === 'finding') return
    el.classList.add('doc-mark-current')
    return () => el.classList.remove('doc-mark-current')
  }, [targets, current])

  if (!root || targets.length === 0) return null

  const blanks = targets.filter((x) => x.kind === 'placeholder').length
  const checks = targets.length - blanks
  const go = (by: 1 | -1) => {
    const next = current < 0 ? (by === 1 ? 0 : targets.length - 1) : (current + by + targets.length) % targets.length
    setCurrent(next)
    const target = targets[next]
    target.el.scrollIntoView({ block: 'center', behavior: 'smooth' })
    if (target.kind === 'finding') {
      layerRef.current?.querySelector<HTMLElement>(`[data-marker="${target.key}"]`)?.focus({ preventScroll: true })
    } else if (target.el.tabIndex >= 0) {
      target.el.focus({ preventScroll: true })
    }
  }
  const save = async () => {
    if (!filling || !filling.sectionId || !value.trim()) return
    setSaving(true)
    setError(null)
    const ok = await onFill(filling.sectionId, filling.text, filling.nth, value.trim())
    setSaving(false)
    if (ok) setFilling(null)
    else setError(t('저장하지 못했습니다.'))
  }
  // Gutter just left of the column.
  const column = root.querySelector<HTMLElement>('article.doc-web')
  const gutter = column ? column.offsetLeft + 2 : 4

  return (
    <>
      <nav
        data-doc-marks=""
        aria-label={t('문서 표시 이동')}
        className="sticky top-0 z-20 flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line bg-panel/95 px-4 py-1.5 text-sm backdrop-blur max-sm:px-2"
      >
        <span className="flex min-w-0 items-center gap-1.5" aria-live="polite">
          <span className="inline-block size-2.5 shrink-0 rounded-sm bg-warn/40" aria-hidden />
          <span>{t('빈칸 {n}개').replace('{n}', String(blanks))}</span>
          <span className="text-faint" aria-hidden>·</span>
          <CircleAlert size={12} className="shrink-0 text-danger" aria-hidden />
          <span>{t('확인할 곳 {n}개').replace('{n}', String(checks))}</span>
        </span>
        <span className="ml-auto flex items-center gap-1">
          {current >= 0 && (
            <span className="text-xs tabular-nums text-faint">{current + 1}/{targets.length}</span>
          )}
          <Button size="icon" variant="ghost" aria-label={t('이전 표시')} onClick={() => go(-1)}>
            <ChevronUp size={14} />
          </Button>
          <Button size="icon" variant="ghost" aria-label={t('다음 표시')} onClick={() => go(1)}>
            <ChevronDown size={14} />
          </Button>
        </span>
      </nav>
      <div ref={layerRef} data-doc-marks="" className="pointer-events-none absolute inset-x-0 top-0 z-10">
        {margin && targets.filter((x) => x.kind === 'finding').map((x) => (
          <button
            key={x.key}
            type="button"
            data-marker={x.key}
            data-mark-tip={x.tip}
            aria-label={t('확인할 곳: {why}').replace('{why}', x.tip)}
            onClick={() => x.el.scrollIntoView({ block: 'center', behavior: 'smooth' })}
            className={cn(
              'pointer-events-auto absolute grid size-5 -translate-x-full place-items-center rounded-full text-danger hover:bg-danger/10 focus-visible:outline-2 focus-visible:outline-accent',
              targets[current]?.key === x.key && 'bg-danger/15',
            )}
            style={{ top: x.top, left: gutter }}
            onMouseEnter={(event) => {
              const r = event.currentTarget
              setTip({ text: x.tip, top: r.offsetTop + r.offsetHeight + 4, left: Math.max(4, r.offsetLeft - 20) })
            }}
            onMouseLeave={() => setTip(null)}
            onFocus={(event) => {
              const r = event.currentTarget
              setTip({ text: x.tip, top: r.offsetTop + r.offsetHeight + 4, left: Math.max(4, r.offsetLeft - 20) })
            }}
            onBlur={() => setTip(null)}
          >
            <CircleAlert size={13} />
          </button>
        ))}
        {tip && tip.text && !filling && (
          <div
            aria-hidden
            className="absolute max-w-xs rounded-control border border-line bg-panel px-2.5 py-1.5 text-sm text-fg shadow-overlay"
            style={{ top: tip.top, left: Math.max(4, Math.min(tip.left, root.clientWidth - 260)) }}
          >
            {tip.text}
          </div>
        )}
        {filling && (
          <div
            role="dialog"
            aria-label={t('{text} 채우기').replace('{text}', filling.text)}
            className="pointer-events-auto absolute w-72 max-w-[calc(100%-1rem)] rounded-card border border-line bg-panel p-2.5 shadow-overlay"
            style={{
              top: filling.top + filling.height + 6,
              left: Math.max(8, Math.min(filling.left, root.clientWidth - 296)),
            }}
          >
            <label className="block text-sm text-muted">
              {t('{text} 자리에 들어갈 값').replace('{text}', filling.text)}
              <input
                autoFocus
                value={value}
                onChange={(event) => setValue(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') { event.preventDefault(); void save() }
                  if (event.key === 'Escape') { event.stopPropagation(); setFilling(null); filling.el.focus() }
                }}
                className="mt-1 w-full rounded-control border border-line bg-transparent px-2 py-1 text-base text-fg focus:border-accent focus:outline-none"
              />
            </label>
            {filling.tip && <p className="mt-1 text-xs text-faint">{filling.tip}</p>}
            {error && <p role="alert" className="mt-1 text-sm text-danger">{error}</p>}
            <div className="mt-2 flex justify-end gap-1.5">
              <Button size="sm" variant="ghost" onClick={() => { setFilling(null); filling.el.focus() }}>{t('취소')}</Button>
              <Button size="sm" variant="primary" disabled={saving || !value.trim()} onClick={() => void save()}>
                {saving && <Loader2 size={13} className="animate-spin" />}
                {t('채우기')}
              </Button>
            </div>
          </div>
        )}
      </div>
    </>
  )
}
