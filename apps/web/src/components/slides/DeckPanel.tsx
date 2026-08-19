import {
  BadgeCheck,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Download,
  ExternalLink,
  Grid2x2,
  Loader2,
  Play,
  Presentation,
  Rows3,
  ShieldQuestion,
  StickyNote,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { PanelControls, usePanelWidth } from '@/components/artifacts/PanelControls'
import { Badge, Button, Dropdown, MenuItem, MenuLabel, Textarea } from '@/components/ui'
import { artifactsApi, downloadArtifact as download, errorMessage } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { DeckArtifact, FactCheck, Slide } from '@/types'
import { LintFindings } from '@/components/artifacts/LintFindings'
import { useT } from '@/lib/useT'

const verdictMeta = {
  supported: { icon: BadgeCheck, tone: 'success' as const, label: '근거 있음', color: 'text-success' },
  unsupported: { icon: TriangleAlert, tone: 'danger' as const, label: '근거 없음', color: 'text-danger' },
  uncertain: { icon: CircleHelp, tone: 'warn' as const, label: '확인 필요', color: 'text-warn' },
}

/**
 * Per-claim verdicts with the source behind each one. The server refuses to
 * issue a confident verdict without a source; this is where it is shown.
 */
function FactCheckResults({ check }: { check: FactCheck }) {
  const t = useT()
  if (check.claims.length === 0) {
    return (
      <p className="mt-3 rounded-card border border-line bg-panel p-3 text-sm text-muted">
        {t('검색으로 확인할 수 있는 주장이 이 장에는 없습니다. 의견과 정의는 판정하지 않습니다.')}
      </p>
    )
  }
  const weak = check.claims.filter((c) => c.verdict !== 'supported').length
  return (
    <div className="mt-3 rounded-card border border-line bg-panel p-3">
      <div className="mb-2 flex items-center gap-2">
        <ShieldQuestion size={13} className="shrink-0 text-accent" />
        <span className="text-xs font-semibold tracking-wide text-faint uppercase">{t('팩트체크')}</span>
        <Badge tone={weak > 0 ? 'warn' : 'success'}>
          {weak > 0 ? t('확인 필요 {n}').replace('{n}', String(weak)) : t('전부 근거 있음')}
        </Badge>
      </div>
      <div className="space-y-2.5">
        {check.claims.map((c) => {
          const meta = verdictMeta[c.verdict]
          const Icon = meta.icon
          return (
            <div key={c.id} className="flex items-start gap-2 text-sm">
              <Icon size={13} className={cn('mt-0.5 shrink-0', meta.color)} />
              <div className="min-w-0 flex-1">
                <p className="font-medium">{c.text}</p>
                <p className="mt-0.5 text-muted">{c.note}</p>
                {c.sourceUrl && (
                  <a
                    href={c.sourceUrl}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="mt-1 inline-flex items-center gap-1 text-xs text-accent hover:underline"
                  >
                    <ExternalLink size={10} />
                    {t('근거 열기')}
                  </a>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/**
 * One slide, drawn in the same rectangle the exporter uses. The geometry is
 * kept in step with `deck_export.py` — a preview that differs from the .pptx
 * is discovered in the room.
 */
function SlideView({ slide, scale = 1 }: { slide: Slide; scale?: number }) {
  const t = useT()
  const accent = slide.accent ?? 'var(--accent)'
  const px = (n: number) => `${n * scale}px`
  const pending = !slide.bullets?.length && !slide.body
  // Two columns are only two columns when there is enough to fill them; four
  // bullets split in half reads as a mistake.
  const twoColumn = slide.layout === 'two-column' && (slide.bullets?.length ?? 0) >= 5

  return (
    <div
      className="relative flex size-full flex-col overflow-hidden bg-white text-[#1a1a1a]"
      style={{ padding: px(28) }}
    >
      <div className="absolute top-0 left-0 h-full" style={{ width: px(4), background: accent }} />
      {slide.layout === 'title' ? (
        <div className="flex flex-1 flex-col justify-center" style={{ paddingLeft: px(16) }}>
          <h3 style={{ fontSize: px(28), fontWeight: 700, lineHeight: 1.2 }}>{slide.title}</h3>
          {slide.body && (
            <p style={{ fontSize: px(13), marginTop: px(12), color: '#666' }}>{slide.body}</p>
          )}
        </div>
      ) : slide.layout === 'quote' && slide.body ? (
        <div className="flex flex-1 flex-col justify-center" style={{ paddingLeft: px(16) }}>
          <p style={{ fontSize: px(20), fontWeight: 600, lineHeight: 1.4, color: accent }}>
            “{slide.body}”
          </p>
          <p style={{ fontSize: px(12), marginTop: px(10), color: '#666' }}>{slide.title}</p>
        </div>
      ) : (
        <div className="flex flex-1 flex-col" style={{ paddingLeft: px(16) }}>
          <h3 style={{ fontSize: px(19), fontWeight: 700, marginBottom: px(12) }}>{slide.title}</h3>
          {slide.bullets && (
            <ul
              style={{
                fontSize: px(13),
                lineHeight: 1.7,
                // A long list down one edge wastes the right half of the
                // rectangle and pushes the last item off the bottom. Splitting
                // it is the same content, read in the shape it fits.
                ...(twoColumn
                  ? { columnCount: 2, columnGap: px(20) }
                  : null),
              }}
            >
              {slide.bullets.map((b, i) => (
                <li key={i} className="flex gap-2" style={{ breakInside: 'avoid' }}>
                  <span style={{ color: accent }}>•</span>
                  <span>{b}</span>
                </li>
              ))}
            </ul>
          )}
          {slide.body && !slide.bullets?.length && (
            <p style={{ fontSize: px(12), color: '#555', marginTop: px(8), lineHeight: 1.6 }}>
              {slide.body}
            </p>
          )}
          {/* 아직 안 쓰인 장. 빈 흰 화면이면 다 만들어진 것처럼 보인다 */}
          {pending && (
            <p style={{ fontSize: px(12), color: '#aaa', marginTop: px(6) }}>{t('쓰는 중…')}</p>
          )}
        </div>
      )}
    </div>
  )
}

/** The editable text of a slide, as lines: the title, then the bullets. */
function toLines(slide: Slide): string {
  return [slide.title, ...(slide.bullets ?? []), slide.body ?? ''].filter(Boolean).join('\n')
}

/**
 * Full-screen rehearsal. A deck is checked by walking it at the speed it will
 * be shown at, which a 400px preview in a side panel cannot be — the text that
 * is too small to read from the back of the room is legible in a thumbnail.
 */
function PresentMode({
  deck,
  index,
  onIndex,
  onClose,
}: {
  deck: DeckArtifact
  index: number
  onIndex: (i: number) => void
  onClose: () => void
}) {
  const t = useT()
  const [showNotes, setShowNotes] = useState(true)
  const slide = deck.slides[index]

  /**
   * Keyboard, owned while presenting. Capture phase and stopped here: an
   * Escape left to bubble would also close the dialog the deck opened from.
   */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const keys = ['Escape', 'ArrowRight', 'ArrowLeft', ' ', 'n', 'N']
      if (!keys.includes(e.key)) return
      e.preventDefault()
      e.stopPropagation()
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowRight' || e.key === ' ')
        onIndex(Math.min(index + 1, deck.slides.length - 1))
      if (e.key === 'ArrowLeft') onIndex(Math.max(index - 1, 0))
      if (e.key.toLowerCase() === 'n') setShowNotes((s) => !s)
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [index, deck.slides.length, onIndex, onClose])

  if (!slide) return null
  /* Portalled to the body rather than left where the panel sits. The deck can
     be opened inside a dialog, and an animated ancestor makes `fixed` resolve
     against *that box* — which turns full-screen rehearsal into a slide shown
     in a 500px window. */
  return createPortal(
    <div role="dialog" aria-label={t('발표 모드')} className="fixed inset-0 z-50 flex flex-col bg-black">
      <div className="flex items-center gap-2 px-4 py-2 text-white/70">
        <Presentation size={14} />
        <span className="text-base">{deck.title}</span>
        <span className="ml-auto text-base tabular-nums">
          {index + 1} / {deck.slides.length}
        </span>
        <button
          onClick={() => setShowNotes((s) => !s)}
          className="rounded-control px-2 py-1 text-sm transition-colors hover:bg-white/10"
        >
          {t('노트')} (N)
        </button>
        <button
          onClick={onClose}
          aria-label={t('발표 끝내기')}
          className="rounded-control p-1.5 transition-colors hover:bg-white/10"
        >
          <X size={16} />
        </button>
      </div>
      <div className="flex min-h-0 flex-1 items-center justify-center px-6 pb-4">
        <div className="aspect-video max-h-full w-full max-w-6xl overflow-hidden rounded-control shadow-float">
          <SlideView slide={slide} scale={2.4} />
        </div>
      </div>
      {showNotes && (
        <div className="max-h-40 overflow-y-auto border-t border-white/10 px-6 py-3 text-base leading-relaxed text-white/75">
          {slide.notes || <span className="text-white/35">{t('노트 없음')}</span>}
        </div>
      )}
      <div className="flex items-center justify-center gap-2 pb-4 text-white/70">
        <button
          onClick={() => onIndex(Math.max(index - 1, 0))}
          disabled={index === 0}
          aria-label={t('이전 장')}
          className="rounded-control p-2 transition-colors hover:bg-white/10 disabled:opacity-30"
        >
          <ChevronLeft size={18} />
        </button>
        <span className="text-sm">{t('← → 로 넘기고 Esc 로 끝냅니다')}</span>
        <button
          onClick={() => onIndex(Math.min(index + 1, deck.slides.length - 1))}
          disabled={index >= deck.slides.length - 1}
          aria-label={t('다음 장')}
          className="rounded-control p-2 transition-colors hover:bg-white/10 disabled:opacity-30"
        >
          <ChevronRight size={18} />
        </button>
      </div>
    </div>,
    document.body,
  )
}

/**
 * Stage width → `scale`. `SlideView` sizes everything off it, so a fixed value
 * tuned for a 460px desktop stage overflows a 210px phone one and the preview
 * stops matching the `.pptx`.
 */
function useStageScale() {
  const ref = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1.15)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => {
      const width = entry.contentRect.width
      if (width > 0) setScale(Math.max(0.45, width / 400))
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return { ref, scale }
}

export function DeckPanel({
  deck,
  onClose,
  onWideChange,
}: {
  deck: DeckArtifact
  onClose?: () => void
  /** Fires when the reader asks for room. A deck is checked by looking at it,
   *  and the stage beside a transcript is about 330px wide. */
  onWideChange?: (wide: boolean) => void
}) {
  const t = useT()
  const width = usePanelWidth(onWideChange)
  const stage = useStageScale()
  const [selected, setSelected] = useState(0)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [presenting, setPresenting] = useState(false)
  //: The rail shows the deck either as pictures or as an outline. Both answer
  //: different questions — "which slide was the chart on" and "does the
  //: argument run in the right order".
  const [rail, setRail] = useState<'thumbs' | 'outline'>('thumbs')
  //: Below lg the rail becomes a drawer, the same way the report's contents
  //: do. Beside the stage it is 132px of a 390px screen, which leaves the
  //: slide 119px — a picture of a slide rather than the slide.
  const [railOpen, setRailOpen] = useState(false)

  const runFactCheck = async (slideId: string) => {
    setChecking(true)
    setError(null)
    try {
      const row = await artifactsApi.factcheckSlide(deck.id, slideId)
      const next = (row.data as { slides?: Slide[] } | null)?.slides
      if (next) deck.slides = next
      deck.version = row.version
    } catch (err) {
      setError(errorMessage(err, t('확인하지 못했습니다.')))
    } finally {
      setChecking(false)
    }
  }
  // Slides arrive one at a time, so the selection can point past the end.
  const index = Math.min(selected, Math.max(deck.slides.length - 1, 0))
  const slide = deck.slides[index] as Slide | undefined
  // A deck still being written has no server id: export would 404 and an edit
  // would be overwritten by the next slide event.
  const weakSlides = deck.slides
    .map((s, i) => (s.factCheck?.claims.some((c) => c.verdict !== 'supported') ? i : -1))
    .filter((i) => i >= 0)
  const writing = deck.slides.length === 0 || deck.slides.some((s) => !s.bullets?.length && !s.body)

  useEffect(() => {
    if (writing) setEditing(false)
  }, [writing])

  //: The deck as it stood when this edit began, for the same comparison the
  //: report panel makes.
  const baseline = useRef('')

  /**
   * Refetch before editing. The panel opens ahead of the store's refresh, and
   * an editor opened in that gap would baseline on older text and save into a
   * phantom conflict.
   */
  const startEditing = async () => {
    if (!slide) return
    setError(null)
    const latest = await artifactsApi.get(deck.id).catch(() => null)
    const onServer = (latest?.data as { slides?: Slide[] } | null)?.slides
    if (latest && onServer) {
      deck.slides = onServer
      deck.version = latest.version
    }
    const current = deck.slides[index]
    if (!current) return
    baseline.current = JSON.stringify(deck.slides)
    setDraft(toLines(current))
    setNotes(current.notes ?? '')
    setEditing(true)
  }

  const save = async () => {
    if (!slide) return
    const lines = draft
      .split('\n')
      .map((l) => l.replace(/^\s*[-*•]\s*/, '').trim())
      .filter(Boolean)
    if (lines.length === 0) {
      setError(t('내용이 비어 있습니다. 저장하지 않았습니다.'))
      return
    }

    const [title, ...rest] = lines
    // Layout follows the shape that arrived: on a quote slide one line is a
    // quotation and several are bullets, since quote renders only the first.
    const edited: Slide =
      slide.layout === 'quote' && rest.length <= 1
        ? { ...slide, title, body: rest[0] ?? '', bullets: undefined, notes }
        : slide.layout === 'title'
          ? { ...slide, title, body: rest.join(' '), notes }
          : { ...slide, layout: 'bullets', title, bullets: rest, body: undefined, notes }

    const slides = deck.slides.map((s, i) => (i === index ? edited : s))
    setSaving(true)
    setError(null)
    try {
      // Same check the report panel makes, and for the same reason: this PATCH
      // carries every slide, so saving over somebody else's edit throws their
      // work away silently. Compared by content — a version number from a
      // list fetched minutes ago says nothing about who edited what.
      const latest = await artifactsApi.get(deck.id).catch(() => null)
      const onServer = (latest?.data as { slides?: Slide[] } | null)?.slides
      if (onServer && JSON.stringify(onServer) !== baseline.current) {
        setError(
          t('이 덱은 다른 곳에서 이미 수정되었습니다. 새로고침해 최신 내용을 받은 뒤 다시 저장하세요.'),
        )
        return
      }
      // PATCHing `data` as one deck is what snapshots the previous revision
      // server-side, which is the way back from a bad edit.
      const row = await artifactsApi.update(deck.id, {
        data: { kind: 'deck', theme: deck.theme, slides },
        summary: t('{n}장 편집').replace('{n}', String(index + 1)),
        // The version the check above read. See ReportPanel for why.
        expectedVersion: latest?.version ?? deck.version,
      })
      deck.slides = slides
      // Kept in step with the server, or the next save on this panel sends a
      // version that is one behind and is refused as somebody else's edit.
      deck.version = row.version
      baseline.current = JSON.stringify(slides)
      setEditing(false)
    } catch (err) {
      setError(errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      setSaving(false)
    }
  }

  const go = (i: number) => {
    setSelected(Math.max(0, Math.min(i, deck.slides.length - 1)))
    setEditing(false)
    // Picking one is the end of the errand: what you wanted to see is the
    // stage the drawer is covering.
    setRailOpen(false)
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 접히는 머리말. 390px 에서는 이 줄이 화면보다 넓고, flex 는 그럴 때
          자식을 줄여서 "내보내기" 를 한 자씩 네 줄로 세운다. 제목은 줄어들되
          버튼은 줄어들지 않는 것이 옳은 순서다. */}
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
        <Presentation size={15} className="shrink-0 text-accent" />
        <p className="min-w-0 flex-1 truncate text-base font-medium max-sm:basis-full">
          {deck.title}
        </p>
        {/* 장수와 테마는 참고 정보다. 좁으면 먼저 접는다. */}
        <Badge className="max-sm:hidden">
          {t('{n}장').replace('{n}', String(deck.slides.length))}
        </Badge>
        {/* 장마다 눌러 보지 않아도 확인이 필요한 곳이 몇 군데인지 보이게 한다 */}
        {weakSlides.length > 0 && (
          <button
            onClick={() => go(weakSlides[0])}
            title={t('{list}번 장').replace('{list}', weakSlides.map((i) => i + 1).join(', '))}
          >
            <Badge tone="warn">
              <TriangleAlert size={10} />
              {t('확인 필요 {n}장').replace('{n}', String(weakSlides.length))}
            </Badge>
          </button>
        )}
        <Badge className="max-sm:hidden">{deck.theme}</Badge>
        <LintFindings findings={deck.lint} artifact={deck} />
        <Button
          size="sm"
          className="lg:hidden"
          aria-label={t('장 목록')}
          title={t('장 목록을 엽니다')}
          onClick={() => setRailOpen((o) => !o)}
        >
          <Rows3 size={13} />
          {deck.slides.length ? index + 1 : 0}/{deck.slides.length}
        </Button>
        {/* 발표 모드. 덱은 방에서 보이는 크기로 한 번 넘겨 봐야 끝난다 */}
        <Button size="sm" disabled={writing} onClick={() => setPresenting(true)}>
          <Play size={13} />
          {t('발표')}
        </Button>
        <Dropdown
          align="right"
          trigger={() => (
            <Button size="sm" disabled={writing}>
              <Download size={14} />
              {t('내보내기')}
            </Button>
          )}
        >
          <MenuLabel>{t('형식 선택')}</MenuLabel>
          <MenuItem hint="PPTX" onClick={() => void download(deck.id, 'pptx', deck.title)}>
            PowerPoint
          </MenuItem>
          <MenuItem hint="PDF" onClick={() => void download(deck.id, 'pdf', deck.title)}>
            {t('PDF (발표용)')}
          </MenuItem>
          <MenuItem hint="MD" onClick={() => void download(deck.id, 'md', deck.title)}>
            {t('텍스트 (노트 포함)')}
          </MenuItem>
        </Dropdown>
        <PanelControls wide={width.wide} onToggleWide={width.toggle} onClose={onClose} />
      </header>

      <div className="relative flex min-h-0 flex-1">
        {railOpen && (
          <button
            aria-label={t('장 목록 닫기')}
            className="absolute inset-0 z-10 bg-black/30 lg:hidden"
            onClick={() => setRailOpen(false)}
          />
        )}
        {/* ── 장 목록 레일 ───────────────────────────────────────────────
            Slides are ordered argument, and the order is the thing under
            revision for as long as the deck exists. A rail keeps the whole
            sequence beside the slide being worked on rather than below it. */}
        <nav
          className={cn(
            'w-[132px] shrink-0 flex-col border-r border-line bg-sidebar/40',
            railOpen ? 'absolute inset-y-0 left-0 z-20 flex shadow-overlay' : 'hidden lg:flex',
          )}
        >
          <div className="flex items-center gap-0.5 border-b border-line px-1.5 py-1.5">
            {(
              [
                { id: 'thumbs', icon: Grid2x2, label: '그림으로' },
                { id: 'outline', icon: Rows3, label: '차례로' },
              ] as const
            ).map((v) => (
              <button
                key={v.id}
                onClick={() => setRail(v.id)}
                aria-pressed={rail === v.id}
                aria-label={t(v.label)}
                title={t(v.label)}
                className={cn(
                  'grid size-6 place-items-center rounded-control transition-colors',
                  rail === v.id ? 'bg-elevated text-fg' : 'text-faint hover:text-fg',
                )}
              >
                <v.icon size={13} />
              </button>
            ))}
            <span className="ml-auto pr-1 text-xs text-faint tabular-nums">
              {deck.slides.length ? index + 1 : 0}/{deck.slides.length}
            </span>
          </div>
          <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-1.5">
            {deck.slides.map((s, i) => {
              const weak = s.factCheck?.claims.some((c) => c.verdict !== 'supported')
              return rail === 'thumbs' ? (
                <button
                  key={s.id}
                  onClick={() => go(i)}
                  aria-label={t('{n}번 장').replace('{n}', String(i + 1))}
                  aria-current={i === index}
                  className={cn(
                    'relative block aspect-video w-full overflow-hidden rounded-control border-2 bg-white transition-colors',
                    i === index ? 'border-accent' : 'border-line hover:border-line-strong',
                  )}
                >
                  <SlideView slide={s} scale={0.3} />
                  <span className="absolute bottom-0.5 left-0.5 rounded bg-black/55 px-1 text-2xs font-medium text-white tabular-nums">
                    {i + 1}
                  </span>
                  {weak && (
                    <span className="absolute top-0.5 right-0.5 grid size-3.5 place-items-center rounded-full bg-warn text-white">
                      <TriangleAlert size={9} />
                    </span>
                  )}
                </button>
              ) : (
                <button
                  key={s.id}
                  onClick={() => go(i)}
                  aria-current={i === index}
                  className={cn(
                    'flex w-full items-start gap-1.5 rounded-control px-1.5 py-1 text-left text-xs leading-snug transition-colors',
                    i === index ? 'bg-elevated text-fg' : 'text-muted hover:bg-elevated hover:text-fg',
                  )}
                >
                  <span className="shrink-0 text-faint tabular-nums">{i + 1}</span>
                  <span className="min-w-0 flex-1 line-clamp-2">{s.title}</span>
                  {weak && <TriangleAlert size={10} className="mt-0.5 shrink-0 text-warn" />}
                </button>
              )
            })}
          </div>
        </nav>

        {/* ── 무대 ─────────────────────────────────────────────────────── */}
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          <div className="border-b border-line bg-elevated/40 p-4">
            <div className="mx-auto flex max-w-lg items-center gap-2">
              <button
                onClick={() => go(index - 1)}
                disabled={index === 0}
                aria-label={t('이전 장')}
                className="grid size-7 shrink-0 place-items-center rounded-control text-muted transition-colors hover:bg-elevated hover:text-fg disabled:opacity-30"
              >
                <ChevronLeft size={16} />
              </button>
              <div
                ref={stage.ref}
                className="aspect-video min-w-0 flex-1 overflow-hidden rounded-card border border-line shadow-raised"
              >
                {slide ? (
                  <SlideView slide={slide} scale={stage.scale} />
                ) : (
                  <div className="grid size-full place-items-center bg-white text-base text-[#999]">
                    {t('구성을 잡는 중…')}
                  </div>
                )}
              </div>
              <button
                onClick={() => go(index + 1)}
                disabled={index >= deck.slides.length - 1}
                aria-label={t('다음 장')}
                className="grid size-7 shrink-0 place-items-center rounded-control text-muted transition-colors hover:bg-elevated hover:text-fg disabled:opacity-30"
              >
                <ChevronRight size={16} />
              </button>
            </div>

            {slide && (
              <div className="mx-auto mt-3 max-w-lg">
                <div className="flex items-center gap-2">
                  <StickyNote size={13} className="shrink-0 text-faint" />
                  <span className="flex-1 text-xs font-semibold tracking-wide text-faint uppercase">
                    {t('발표 노트')}
                  </span>
                  {!editing && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={writing || checking}
                      onClick={() => void runFactCheck(slide.id)}
                    >
                      {checking ? (
                        <Loader2 size={13} className="animate-spin" />
                      ) : (
                        <ShieldQuestion size={13} />
                      )}
                      {t('팩트체크')}
                    </Button>
                  )}
                  {editing ? (
                    <>
                      <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
                        {t('취소')}
                      </Button>
                      <Button variant="primary" size="sm" onClick={() => void save()} disabled={saving}>
                        {saving && <Loader2 size={13} className="animate-spin" />}
                        {t('저장')}
                      </Button>
                    </>
                  ) : (
                    <Button variant="ghost" size="sm" onClick={() => void startEditing()} disabled={writing}>
                      {t('텍스트 수정')}
                    </Button>
                  )}
                </div>

                {editing ? (
                  <div className="mt-2 space-y-2">
                    <Textarea
                      rows={5}
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      aria-label={t('슬라이드 텍스트')}
                    />
                    <p className="text-xs text-faint">{t('첫 줄이 제목, 나머지 줄이 각각 한 항목')}</p>
                    <Textarea
                      rows={3}
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      placeholder={t('발표 노트')}
                      aria-label={t('발표 노트')}
                    />
                    {error && <p className="text-sm text-danger">{error}</p>}
                  </div>
                ) : (
                  <>
                    <p className="mt-1.5 text-base text-muted">
                      {slide.notes || <span className="text-faint">{t('노트 없음')}</span>}
                    </p>
                    {slide.factCheck?.status === 'done' && (
                      <FactCheckResults check={slide.factCheck} />
                    )}
                    {error && !editing && <p className="mt-2 text-sm text-danger">{error}</p>}
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {presenting && (
        <PresentMode
          deck={deck}
          index={index}
          onIndex={setSelected}
          onClose={() => setPresenting(false)}
        />
      )}
    </div>
  )
}
