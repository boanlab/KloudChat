import { ExternalLink, FileText, Film, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, Modal } from '@/components/ui'
import { errorMessage, fileBlob, shelfApi } from '@/lib/api'
import { clock, type Located } from '@/lib/shelf'
import { useMediaQuery } from '@/lib/useMediaQuery'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { SOURCE_ICONS, SOURCE_KIND_LABELS } from './sourceKinds'

/** What a citation points at: a source by id or by the name an answer's list gives, the sentence
 * that cited it, and the page the list named. */
export interface SourceTarget {
  projectId: string
  fileId?: string
  name?: string
  quote?: string
  page?: number | null
  /** The citation number as the reader saw it. */
  n?: number
}

/**
 * The source viewer: the cited passage highlighted in the source's text, with the PDF open at
 * its page beside it, or the time code a video's transcript gives and a link to its player.
 */
export function SourceViewer({ target, onClose }: { target: SourceTarget | null; onClose: () => void }) {
  const t = useT()
  const phone = useMediaQuery('(max-width: 40rem)')
  const [found, setFound] = useState<Located | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pdf, setPdf] = useState<string | null>(null)
  const [showPdf, setShowPdf] = useState(false)
  const mark = useRef<HTMLElement>(null)

  useEffect(() => {
    if (!target) return
    let alive = true
    let url: string | null = null
    setFound(null)
    setError(null)
    setPdf(null)
    setShowPdf(false)
    shelfApi
      .locate(target.projectId, { fileId: target.fileId, name: target.name, q: target.quote, page: target.page })
      .then(async (located) => {
        if (!alive) return
        setFound(located)
        if (located.mime === 'application/pdf') {
          const blob = await fileBlob(located.fileId)
          url = URL.createObjectURL(blob)
          if (alive) setPdf(url)
          else URL.revokeObjectURL(url)
        }
      })
      .catch((err) => alive && setError(errorMessage(err, t('원문을 찾지 못했습니다.'))))
    return () => {
      alive = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [target, t])

  // The highlight in view once the text is drawn.
  useEffect(() => {
    if (found) mark.current?.scrollIntoView({ block: 'center' })
  }, [found, showPdf])

  if (!target) return null
  const Icon = found ? SOURCE_ICONS[found.kind] : FileText
  const title = found?.name ?? target.name ?? t('원문')
  const pdfPage = found?.page ?? target.page ?? null
  const passage = found ? split(found) : null
  const pdfShown = !!pdf && (!phone || showPdf)

  return (
    <Modal open onClose={onClose} title={title} width={pdf && !phone ? 'max-w-6xl' : 'max-w-3xl'}>
      <div data-testid="source-viewer" className="space-y-3">
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
          <Icon size={14} className="shrink-0 text-accent" />
          {found && <Badge>{t(SOURCE_KIND_LABELS[found.kind])}</Badge>}
          {target.n !== undefined && <Badge tone="accent">{t('출처 {n}').replace('{n}', String(target.n))}</Badge>}
          {pdfPage !== null && <span>{t('{n}쪽').replace('{n}', String(pdfPage))}</span>}
          {found?.seconds !== null && found?.seconds !== undefined && <span>{clock(found.seconds)}</span>}
          {found && !found.matched && target.quote && (
            <span className="text-warn">{t('인용한 문장과 꼭 맞는 대목을 찾지 못해 가까운 곳을 보여 줍니다.')}</span>
          )}
          <span className="ml-auto flex gap-1.5">
            {found?.kind === 'video' || found?.kind === 'audio' ? (
              <Link
                to={`/files?open=${found.fileId}${found.seconds !== null ? `&t=${Math.floor(found.seconds)}` : ''}`}
                className="inline-flex h-8 items-center gap-1.5 rounded-control border border-line px-2.5 text-sm font-medium hover:bg-elevated"
              >
                <Film size={13} />
                {found.seconds !== null
                  ? t('{time}부터 재생').replace('{time}', clock(found.seconds))
                  : t('재생')}
              </Link>
            ) : null}
            {found?.sourceUrl && (
              <a
                href={found.sourceUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex h-8 items-center gap-1.5 rounded-control border border-line px-2.5 text-sm font-medium hover:bg-elevated"
              >
                <ExternalLink size={13} />
                {t('원문 열기')}
              </a>
            )}
            {pdf && phone && (
              <Button size="sm" onClick={() => setShowPdf((v) => !v)}>
                {showPdf ? t('대목 보기') : t('PDF 쪽 보기')}
              </Button>
            )}
          </span>
        </div>
        {error && <p role="alert" className="text-base text-danger">{error}</p>}
        {!found && !error && (
          <p className="flex items-center gap-2 text-base text-muted">
            <Loader2 size={14} className="animate-spin" />
            {t('원문에서 인용한 대목을 찾는 중…')}
          </p>
        )}
        {found && passage && (
          <div className={cn('grid gap-3', pdfShown && !phone && 'grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]')}>
            {pdfShown && (
              <iframe
                title={t('{name} {n}쪽').replace('{name}', found.name).replace('{n}', String(pdfPage ?? 1))}
                src={`${pdf}#page=${pdfPage ?? 1}`}
                className="h-[70vh] w-full rounded-control border border-line bg-white"
              />
            )}
            {(!phone || !showPdf) && (
              <div
                className="max-h-[70vh] overflow-y-auto rounded-control border border-line bg-elevated px-3 py-2 text-base leading-relaxed whitespace-pre-wrap"
                data-testid="source-passage"
              >
                {found.windowStart > 0 && <span className="text-faint">…</span>}
                {passage.before}
                <mark
                  ref={mark}
                  data-testid="source-highlight"
                  className="rounded bg-warn/25 px-0.5 text-fg"
                >
                  {passage.focus}
                </mark>
                {passage.after}
                {found.windowStart + found.window.length < found.chars && <span className="text-faint">…</span>}
              </div>
            )}
          </div>
        )}
      </div>
    </Modal>
  )
}

/** The window around the passage, cut at the highlighted sentence. */
function split(found: Located) {
  const at = Math.max(0, found.focusStart - found.windowStart)
  const end = Math.max(at, found.focusEnd - found.windowStart)
  return {
    before: found.window.slice(0, at),
    focus: found.window.slice(at, end),
    after: found.window.slice(end),
  }
}
