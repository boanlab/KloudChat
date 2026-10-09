import { ArrowRight, ChevronLeft, ChevronRight, LayoutGrid, Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Modal } from '@/components/ui'
import { templateText, type DesignTemplateRow } from '@/lib/api'
import { Input } from '@/components/ui'
import { currentLang } from '@/lib/i18n'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { DeckSlideViewer, SlideFrame } from '@/components/chat/DeckSlideViewer'
import { PagedPreview } from '@/components/chat/PagedPreview'
import { useDesignTemplates, useStartTemplate } from '@/lib/useDesignTemplates'
import { useT } from '@/lib/useT'

/** Checks printed on a card before the rest folds away. */
const CHECKS_ON_CARD = 3

/** Cards per page: a 2x2 block. */
const PER_PAGE = 4

/** Live preview of a design template scaled to its box; sandboxed with no permissions. */
function ScaledPreview({
  id,
  deck,
  height,
  scrolls = false,
}: {
  id: string
  deck: boolean
  /** Frame height at full size, before scaling. */
  height: number
  /** Whether the page inside may be scrolled (the full view) or is a still (the card). */
  scrolls?: boolean
}) {
  const box = useRef<HTMLDivElement>(null)
  const width = deck ? 1280 : 820
  const [scale, setScale] = useState(0)

  useEffect(() => {
    const node = box.current
    if (!node) return
    const fit = () => setScale(node.clientWidth / width)
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(node)
    return () => observer.disconnect()
  }, [width])

  return (
    <div ref={box} className="relative w-full overflow-hidden" style={{ height: scale ? height * scale : undefined }}>
      <iframe
        src={`/api/design-templates/${id}/preview`}
        sandbox=""
        tabIndex={scrolls ? 0 : -1}
        loading="lazy"
        title=""
        className={cn('absolute left-0 top-0 origin-top-left bg-white', !scrolls && 'pointer-events-none')}
        // Hidden until measured, or one frame at scale 1 paints over the next card.
        style={{ width, height, transform: `scale(${scale})`, visibility: scale ? 'visible' : 'hidden' }}
      />
    </div>
  )
}

/** The card's preview strip; a click opens the whole page. */
function Thumbnail({ id, deck, onOpen }: { id: string; deck: boolean; onOpen: () => void }) {
  const t = useT()
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        'relative shrink-0 cursor-zoom-in overflow-hidden border-b border-line bg-elevated text-left',
        deck ? 'aspect-video' : 'h-36 py-1.5',
      )}
      aria-label={t('전체 미리보기')}
      title={t('전체 미리보기')}
    >
      {/* A deck shows its cover slide whole; a document its first page, as the page view lays it out. */}
      {deck ? <SlideFrame id={id} n={1} /> : <PagedPreview id={id} firstOnly />}
    </button>
  )
}

/** The whole page of a design template, as it prints. */
function FullPreview({
  row,
  english,
  open,
  onClose,
  footer,
}: {
  row: DesignTemplateRow
  english: boolean
  open: boolean
  onClose: () => void
  footer: React.ReactNode
}) {
  const text = templateText(row, english)
  const deck = row.kind === 'deck'
  if (deck && row.slideCount > 0) {
    return (
      <Modal open={open} onClose={onClose} title={text.name} description={text.description} width="max-w-4xl" footer={footer}>
        <DeckSlideViewer id={row.id} count={row.slideCount} />
      </Modal>
    )
  }
  return (
    <Modal open={open} onClose={onClose} title={text.name} description={text.description} width="max-w-4xl" footer={footer}>
      <div data-preview-scroll className="max-h-[75vh] overflow-y-auto rounded-control border border-line bg-elevated">
        {/* A document shows its pages as the page view lays them out; a deck its 16:9 slide. */}
        {deck ? <ScaledPreview id={row.id} deck height={720} scrolls /> : <PagedPreview id={row.id} />}
      </div>
    </Modal>
  )
}

function Checks({ checks }: { checks: string[] }) {
  const t = useT()
  if (checks.length === 0) return null
  const rest = checks.slice(CHECKS_ON_CARD)
  const line = (text: string) => (
    <li key={text} className="flex gap-1.5">
      <span aria-hidden>·</span>
      <span>{text}</span>
    </li>
  )
  return (
    <details className="rounded-control bg-elevated p-2 text-xs leading-relaxed">
      <summary className="cursor-pointer font-medium text-muted transition-colors hover:text-fg">
        {t('이 양식이 확인하는 것 {n}개').replace('{n}', String(checks.length))}
      </summary>
      <ul lang="ko" className="mt-1 space-y-0.5 text-faint">
        {checks.slice(0, CHECKS_ON_CARD).map(line)}
      </ul>
      {rest.length > 0 && (
        <ul lang="ko" className="mt-0.5 space-y-0.5 text-faint">
          {rest.map(line)}
        </ul>
      )}
    </details>
  )
}

/** Design template card, shared by the gallery and the designs screen. */
export function DesignTemplateCard({
  row,
  english,
  onPick,
  chosen,
}: {
  row: DesignTemplateRow
  english: boolean
  onPick: (row: DesignTemplateRow) => void
  /** The template the session already uses. */
  chosen?: boolean
}) {
  const t = useT()
  const text = templateText(row, english)
  const [previewing, setPreviewing] = useState(false)
  const start = (
    <Button
      size="sm"
      disabled={chosen}
      onClick={() => {
        setPreviewing(false)
        onPick(row)
      }}
    >
      {chosen ? t('고른 양식') : t('이 양식으로 시작')}
    </Button>
  )
  return (
    <div
      className={cn(
        'group flex h-full flex-col overflow-hidden rounded-card border bg-panel transition-colors',
        chosen ? 'border-accent' : 'border-line hover:border-line-strong',
      )}
    >
      {row.hasPreview && (
        <>
          <Thumbnail id={row.id} deck={row.kind === 'deck'} onOpen={() => setPreviewing(true)} />
          <FullPreview
            row={row}
            english={english}
            open={previewing}
            onClose={() => setPreviewing(false)}
            footer={start}
          />
        </>
      )}
      {/* The middle scrolls; the foot stays put. */}
      <div className="flex min-h-0 flex-1 flex-col p-3">
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
          <div className="flex items-start justify-between gap-2">
            <p className="text-base font-medium">{text.name}</p>
            <Badge>{text.category}</Badge>
          </div>
          <p className="line-clamp-2 text-sm text-muted">{text.description}</p>
          <Checks checks={row.checks} />
          {/* What the person brings. */}
          {text.fills.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {text.fills.map((fill) => (
                <span
                  key={fill}
                  className="rounded-full border border-line px-2 py-0.5 text-xs text-faint"
                >
                  {fill}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="pt-2">
          <Foot chosen={chosen}>{start}</Foot>
        </div>
      </div>
    </div>
  )
}

/** Action row at the foot of a card. */
function Foot({
  chosen,
  children,
}: {
  chosen?: boolean
  children: React.ReactNode
}) {
  const t = useT()
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {children}
      {chosen && <span className="text-xs text-faint">{t('이미 고른 양식입니다')}</span>}
    </div>
  )
}

/** 양식 picker for one surface. */
export function DesignGalleryModal({
  kind,
  sessionId,
  open,
  onClose,
}: {
  kind: SessionKind
  /** Session whose template is already chosen, if any. */
  sessionId?: string | null
  open: boolean
  onClose: () => void
}) {
  const t = useT()
  const [category, setCategory] = useState<string | 'all'>('all')
  const rows = useDesignTemplates(open)
  const start = useStartTemplate()
  const pendingTemplate = useStore((s) => s.pendingTemplate)
  const sessions = useStore((s) => s.sessions)

  const english = currentLang() === 'en'
  const forSurface = useMemo(
    () =>
      rows
        .filter((r) => r.surface === kind)
        .map((r) => ({ row: r, text: templateText(r, english) })),
    [rows, kind, english],
  )

  const categories = useMemo(
    () => [...new Set(forSurface.map((c) => c.text.category))],
    [forSurface],
  )
  const visible =
    category === 'all' ? forSurface : forSurface.filter((c) => c.text.category === category)

  const [query, setQuery] = useState('')
  const needle = query.trim().toLowerCase()
  const found = needle
    ? visible.filter(({ text }) =>
        `${text.name} ${text.description} ${text.category} ${text.fills.join(' ')}`
          .toLowerCase()
          .includes(needle),
      )
    : visible

  const [page, setPage] = useState(0)
  const pages = Math.max(1, Math.ceil(found.length / PER_PAGE))
  // Clamped, not reset, when the result count shrinks.
  const at = Math.min(page, pages - 1)
  const shown = found.slice(at * PER_PAGE, at * PER_PAGE + PER_PAGE)

  // The session's template: an unsent pick outranks the server's row.
  const worn =
    (pendingTemplate?.surface === kind ? pendingTemplate.id : null) ??
    sessions.find((row) => row.id === sessionId)?.renderTemplateId ??
    null

  const pick = (row: DesignTemplateRow) => {
    if (row.id === worn) return
    start(row)
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('양식 고르기')}
      description={t('결과물의 모양을 고릅니다.')}
      width="max-w-3xl"
    >
        <div className="mb-3 flex items-baseline justify-between gap-2">
          <h3 className="text-base font-semibold">{t('어떤 모양으로 받을까요?')}</h3>
          <Link
            to="/customize?tab=templates"
            onClick={onClose}
            className="inline-flex shrink-0 items-center gap-1 text-sm text-muted hover:text-fg"
          >
            {t('모두 보기')}
            <ArrowRight size={13} aria-hidden />
          </Link>
        </div>
        <div className="mb-3 flex items-center gap-2">
          <div className="flex min-w-0 flex-1 flex-wrap gap-1.5">
            {categories.length > 1 &&
              (['all', ...categories] as const).map((c) => (
                <button
                  key={c}
                  onClick={() => {
                    setCategory(c)
                    setPage(0)
                  }}
                  className={cn(
                    'rounded-full border px-2.5 py-1 text-sm transition-colors',
                    category === c
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line text-muted hover:text-fg',
                  )}
                >
                  {c === 'all' ? t('전체') : c}
                </button>
              ))}
          </div>
          <div className="relative shrink-0">
            <Search
              size={13}
              className="pointer-events-none absolute top-1/2 left-2 -translate-y-1/2 text-faint"
            />
            <Input
              aria-label={t('양식 검색')}
              placeholder={t('양식 또는 용도 검색')}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                setPage(0)
              }}
              className="h-8 w-44 pl-7 text-sm"
            />
          </div>
        </div>

        {/* Fixed-height window so the dialog does not resize between pages.
            The scrolling element and the grid are separate so an expanded
            card can grow its row. */}
        <div className="h-[58vh] min-h-80 overflow-y-auto pr-1">
        <div className="grid content-start gap-3 sm:grid-cols-2">
          {shown.map(({ row }) => (
            <DesignTemplateCard
              key={row.id}
              row={row}
              english={english}
              onPick={pick}
              chosen={row.id === worn}
            />
          ))}
          {shown.length === 0 && (
            <p className="col-span-full py-8 text-center text-sm text-faint">
              {t('조건에 맞는 양식이 없습니다.')}
            </p>
          )}
        </div>
        </div>

      {pages > 1 && (
        <div className="mt-3 flex items-center justify-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            aria-label={t('이전 쪽')}
            disabled={at === 0}
            onClick={() => setPage(at - 1)}
          >
            <ChevronLeft size={14} />
          </Button>
          <span className="text-sm text-muted tabular-nums">
            {at + 1} / {pages}
          </span>
          <Button
            size="sm"
            variant="ghost"
            aria-label={t('다음 쪽')}
            disabled={at >= pages - 1}
            onClick={() => setPage(at + 1)}
          >
            <ChevronRight size={14} />
          </Button>
        </div>
      )}
    </Modal>
  )
}

/** Whether a surface offers the picker; one gate for the gallery and composer buttons. Only
 * the writing surfaces have 서식: image, audio and video ones were retired. */
export function offersTemplates(kind: SessionKind): boolean {
  return kind === 'report' || kind === 'slides'
}

/** Button that opens the gallery, shown where the surface has anything to offer. */
export function DesignGallery({
  kind,
  sessionId,
}: {
  kind: SessionKind
  sessionId?: string | null
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const has = useStore((s) => s.designTemplates.some((row) => row.surface === kind))
  if (!offersTemplates(kind) || !has) return null
  return (
    <>
      <Button variant="secondary" size="sm" onClick={() => setOpen(true)}>
        <LayoutGrid size={14} />
        {t('양식 고르기')}
      </Button>
      <DesignGalleryModal
        kind={kind}
        sessionId={sessionId}
        open={open}
        onClose={() => setOpen(false)}
      />
    </>
  )
}
