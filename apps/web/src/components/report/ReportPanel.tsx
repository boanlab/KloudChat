import {
  Check,
  Copy,
  Download,
  ExternalLink,
  FileText,
  History,
  Link2,
  ListTree,
  Loader2,
  RefreshCw,
  Paperclip,
  Pencil,
  Plug,
  Printer,
  Quote,
  Sparkles,
  X,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Markdown } from '@/components/chat/Markdown'
import { PanelControls } from '@/components/artifacts/PanelControls'
import { Badge, Button, Dropdown, MenuItem, MenuLabel, Modal, Textarea } from '@/components/ui'
import { artifactsApi, downloadArtifact as download, errorMessage } from '@/lib/api'
import type { ArtifactVersionRow } from '@/lib/api'
import { fromMarkdown, toMarkdown } from '@/lib/reportMarkdown'
import { cn, formatTokens, relativeTime } from '@/lib/utils'
import type { ReportArtifact, ReportSection, Source } from '@/types'
import { copyText } from '@/lib/clipboard'
import { useT } from '@/lib/useT'

/** A passage the reader picked out, and the section it belongs to. */
interface Picked {
  sectionId: string
  text: string
  /** Where to float the toolbar, relative to the scrolling document. */
  top: number
  left: number
}

const originIcon = { web: Link2, connector: Plug, file: Paperclip }

/**
 * The document as paper, portalled to `<body>`.
 *
 * `window.print()` prints the window, and the panel's document is nested inside
 * `overflow: auto` and a `100dvh` box — a print stylesheet cannot lift a
 * descendant out of a clipping container. So the printable tree lives at the
 * top level and `@media print` swaps which one is visible. No controls: paper
 * has no buttons.
 */
function PrintDocument({ report }: { report: ReportArtifact }) {
  const t = useT()
  return createPortal(
    <article data-print-doc lang="ko">
      <h1>{report.title}</h1>
      {report.sections.map((s) => (
        <section key={s.id}>
          <h2>{s.heading}</h2>
          <Markdown>{s.content}</Markdown>
        </section>
      ))}
      {/* Citations: what makes a printed report submittable. */}
      {report.sources.length > 0 && (
        <section>
          <h2>{t('참고문헌')}</h2>
          <ol>
            {report.sources.map((src) => (
              <li key={src.id}>
                {src.title}
                {[src.author, src.publisher, src.year].filter(Boolean).length > 0 &&
                  ` — ${[src.author, src.publisher, src.year].filter(Boolean).join(', ')}`}
                {src.url && <div className="print-url">{src.url}</div>}
              </li>
            ))}
          </ol>
        </section>
      )}
    </article>,
    document.body,
  )
}

/** Reference list, shown beside the prose as well as at the end. */
function SourceList({ sources, style }: { sources: Source[]; style: string }) {
  const t = useT()
  return (
    <div className="space-y-2">
      <p className="text-xs text-faint">
        {t('{style} 형식 · {n}건').replace('{style}', style).replace('{n}', String(sources.length))}
      </p>
      {sources.map((src) => {
        const Icon = originIcon[src.origin]
        return (
          <div key={src.id} className="rounded-xl border border-line bg-panel p-3">
            <div className="flex items-start gap-2">
              <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-md bg-accent-soft text-2xs font-semibold text-accent">
                {src.ordinal}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-base font-medium">{src.title}</p>
                <p className="mt-0.5 text-sm text-muted">
                  {[src.author, src.publisher, src.year].filter(Boolean).join(' · ')}
                </p>
                {src.quote && (
                  <p className="mt-1.5 border-l-2 border-line-strong pl-2 text-sm text-muted">
                    {src.quote}
                  </p>
                )}
                <p className="mt-1.5 flex items-center gap-1.5 text-xs text-faint">
                  <Icon size={11} />
                  {src.originLabel}
                  {src.url && (
                    <a
                      href={src.url}
                      target="_blank"
                      rel="noreferrer"
                      className="flex items-center gap-0.5 text-accent hover:underline"
                    >
                      {t('원문')} <ExternalLink size={9} />
                    </a>
                  )}
                </p>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function statusIcon(status: ReportSection['status']) {
  if (status === 'done') return <Check size={12} className="text-success" />
  if (status === 'streaming') return <Loader2 size={12} className="animate-spin text-accent" />
  return <span className="block size-1.5 rounded-full bg-line-strong" />
}

/**
 * Sections render as each one finishes rather than waiting for the document.
 * The table of contents doubles as the progress readout: pending sections are
 * visible from the start, greyed until written.
 */
export function ReportPanel({
  report,
  onClose,
  onWideChange,
}: {
  report: ReportArtifact
  onClose?: () => void
  /** Fires when the panel needs the extra width — an open editor, or the
   *  reader asking for focus mode. The document column is ~350px otherwise. */
  onWideChange?: (wide: boolean) => void
}) {
  const t = useT()
  const [activeId, setActiveId] = useState<string | null>(null)
  //: Focus mode — 350px beside a transcript is not a reading width.
  const [focus, setFocus] = useState(false)
  const [pane, setPane] = useState<'document' | 'sources'>('document')
  // Below lg the rail becomes a drawer rather than vanishing: it carries the
  // only signal that the report is still being written.
  const [tocOpen, setTocOpen] = useState(false)
  const [showVersions, setShowVersions] = useState(false)
  //: Real history, fetched when the dialog opens — the version number alone
  //: would print N identical rows.
  const [versions, setVersions] = useState<ArtifactVersionRow[] | null>(null)
  const [restoring, setRestoring] = useState<number | null>(null)

  const openVersions = async () => {
    setShowVersions(true)
    setVersions(null)
    setVersions(await artifactsApi.versions(report.id).catch(() => []))
  }

  const restore = async (version: number) => {
    setRestoring(version)
    try {
      const row = await artifactsApi.restore(report.id, version)
      const data = (row.data ?? {}) as { sections?: ReportSection[] }
      report.title = row.title
      report.version = row.version
      if (data.sections) report.sections = data.sections
      setShowVersions(false)
    } finally {
      setRestoring(null)
    }
  }
  //: Whole-document edit mode. Title, headings and the space between sections
  //: belong to no section, so a per-section editor cannot reach them.
  const [editing, setEditing] = useState(false)
  //: Which section is open for a rewrite, and the instruction going with it.
  const [rewriting, setRewriting] = useState<string | null>(null)
  const [rewriteNote, setRewriteNote] = useState('')
  //: The passage the instruction is about, when the reader started from a
  //: selection. Kept apart from the note so it renders as what it is — a
  //: quotation the reader can drop — instead of prefilled text they have to
  //: type around.
  const [rewriteQuote, setRewriteQuote] = useState('')
  const [rewriteBusy, setRewriteBusy] = useState(false)
  const [rewriteError, setRewriteError] = useState<string | null>(null)

  const rewriteSection = async (sectionId: string) => {
    setRewriteBusy(true)
    setRewriteError(null)
    try {
      const note = rewriteQuote
        ? t('이 부분을 고쳐 주세요: “{quote}”\n{note}')
            .replace('{quote}', rewriteQuote)
            .replace('{note}', rewriteNote)
            .trim()
        : rewriteNote
      const row = await artifactsApi.rewriteSection(report.id, sectionId, note)
      const data = (row.data ?? {}) as { sections?: ReportSection[] }
      if (data.sections) report.sections = data.sections
      report.version = row.version
      setRewriting(null)
    } catch (err) {
      setRewriteError(errorMessage(err, t('다시 쓰지 못했습니다.')))
    } finally {
      setRewriteBusy(false)
    }
  }
  //: Every transition goes through here, so the host is never left with a
  //: widened panel and no editor in it.
  const openEditor = (open: boolean) => {
    setEditing(open)
    onWideChange?.(open || focus)
  }
  const toggleFocus = () => {
    setFocus(!focus)
    // Still wide while an editor is open, whichever way focus mode just went.
    onWideChange?.(!focus || editing)
  }
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  //: Nothing to edit until the model stops: a mid-run save would freeze
  //: half-written prose and mark every section done.
  const writing = report.sections.some((s) => s.status !== 'done')

  //: The document as it stood when the editor opened. Saving compares against
  //: this, not against a version number — the store's copy of the version can
  //: be minutes old for reasons that have nothing to do with anybody editing.
  const baseline = useRef('')

  /** Same reasoning as the deck panel: open the editor on the current text. */
  const startEditing = async () => {
    setSaveError(null)
    const latest = await artifactsApi.get(report.id).catch(() => null)
    const onServer = (latest?.data as { sections?: ReportSection[] } | null)?.sections
    if (latest && onServer) {
      report.title = latest.title
      report.sections = onServer
      report.version = latest.version
    }
    const current = toMarkdown(report)
    baseline.current = current
    setDraft(current)
    openEditor(true)
  }

  const saveDocument = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      /**
       * Has anybody else written to this since the editor opened?
       *
       * A PATCH sends the whole document, so a second save would replace the
       * first person's paragraphs silently.
       *
       * Compared by content, not version number: the panel's version may come
       * from an old list, and refusing on a stale number breaks solo editing.
       *
       * A check before a write, not a locked write — a save landing between the
       * two still wins.
       */
      const latest = await artifactsApi.get(report.id).catch(() => null)
      const latestData = (latest?.data ?? null) as { sections?: ReportSection[] } | null
      if (latest && latestData?.sections) {
        const onServer = toMarkdown({
          ...report,
          title: latest.title,
          sections: latestData.sections,
        })
        if (onServer !== baseline.current) {
          setSaveError(
            t('이 보고서는 다른 곳에서 이미 수정되었습니다. 새로고침해 최신 내용을 받은 뒤 다시 저장하세요.'),
          )
          return
        }
      }
      const parsed = fromMarkdown(draft, report.sections)
      if (parsed.sections.length === 0) {
        setSaveError(t('내용이 비어 있습니다. 저장하지 않았습니다.'))
        return
      }
      // An emptied `#` line is a deleted line, not a request for no title.
      const title = parsed.title || report.title
      // PATCHing `data` whole is what snapshots the previous revision
      // server-side, which is the way back from a bad edit.
      const row = await artifactsApi.update(report.id, {
        data: { ...report, title, sections: parsed.sections },
        title,
        summary: t('문서 편집'),
        // The version just read, not the one the panel is holding: the store's
        // copy comes from a list that may be minutes old, and conditioning on
        // that refuses saves nobody else touched. Read, compare, then write
        // against what the read saw — which is the window this closes.
        expectedVersion: latest?.version ?? report.version,
      })
      // Local mutation, so the panel reflects the save without a refetch. The
      // version comes back from the write; without it the header kept showing
      // the version the document carried going into this save.
      report.title = title
      report.sections = parsed.sections
      report.version = row.version
      baseline.current = toMarkdown({ ...report, title, sections: parsed.sections })
      openEditor(false)
    } catch (err) {
      setSaveError(errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      setSaving(false)
    }
  }
  const done = report.sections.filter((s) => s.status === 'done').length

  /**
   * Selection → rewrite. What was highlighted goes in as the quotation the
   * instruction is about, so the reader does not re-describe it in prose.
   */
  const [picked, setPicked] = useState<Picked | null>(null)
  const docRef = useRef<HTMLDivElement>(null)
  const handleRef = useRef<HTMLDivElement>(null)

  const readSelection = (e: React.MouseEvent) => {
    // Releasing the mouse *on the handle* is the user reaching for it, not a
    // new selection. Chrome collapses the range on that release, so reading it
    // here would tear the handle down between mouseup and click — the button
    // would be gone by the time its own click arrived.
    if (handleRef.current?.contains(e.target as Node)) return
    const sel = window.getSelection()
    const text = sel?.toString().trim() ?? ''
    const host = docRef.current
    if (!sel || sel.rangeCount === 0 || text.length < 2 || !host) {
      setPicked(null)
      return
    }
    const range = sel.getRangeAt(0)
    // Only prose inside a finished section: headings, the sources pane and the
    // editor all have their own handles.
    const node = range.commonAncestorContainer
    const element = node.nodeType === 1 ? (node as Element) : node.parentElement
    const section = element?.closest<HTMLElement>('section[id^="sec-"]')
    if (!section || !host.contains(section)) {
      setPicked(null)
      return
    }
    const rect = range.getBoundingClientRect()
    const hostRect = host.getBoundingClientRect()
    setPicked({
      sectionId: section.id.replace(/^sec-/, ''),
      text,
      // The scroller's own coordinates, not the viewport's: the handle belongs
      // to the sentence, so it has to travel with it rather than being torn
      // down on the first scroll — including the one the browser performs to
      // bring the handle itself into view.
      top: rect.top - hostRect.top + host.scrollTop - 40,
      left: Math.max(8, rect.left - hostRect.left),
    })
  }

  // Any edit mode change drops a stale bubble: the passage it points at is
  // about to be replaced by a textarea.
  useEffect(() => {
    setPicked(null)
  }, [editing, pane])

  const rewritePicked = () => {
    if (!picked) return
    setRewriting(picked.sectionId)
    setRewriteError(null)
    setRewriteNote('')
    setRewriteQuote(picked.text)
    setPicked(null)
    window.getSelection()?.removeAllRanges()
  }

  const scrollTo = (id: string) => {
    setActiveId(id)
    setTocOpen(false)
    document.getElementById(`sec-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="relative flex h-full min-h-0">
      {/* Mounted with the panel, not on the print click: `window.print()` is
          synchronous, so a tree created in that handler is not on screen when
          the browser takes its snapshot. */}
      <PrintDocument report={report} />
      {tocOpen && (
        <button
          aria-label={t('목차 닫기')}
          className="absolute inset-0 z-10 bg-black/30 lg:hidden"
          onClick={() => setTocOpen(false)}
        />
      )}

      {/* 목차 */}
      <nav
        className={cn(
          'w-52 shrink-0 flex-col border-r border-line bg-panel',
          tocOpen ? 'absolute inset-y-0 left-0 z-20 flex shadow-xl' : 'hidden lg:flex',
        )}
      >
        <div className="border-b border-line px-3 py-2.5">
          <p className="text-xs font-semibold tracking-wide text-faint uppercase">{t('목차')}</p>
          <p className="mt-1 text-xs text-muted">
            {t('{done}/{total} 섹션 · {words} 단어').replace('{done}', String(done)).replace('{total}', String(report.sections.length)).replace('{words}', formatTokens(report.wordCount))}
          </p>
          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-elevated">
            <div
              className="h-full rounded-full bg-accent transition-[width] duration-500"
              style={{ width: `${(done / report.sections.length) * 100}%` }}
            />
          </div>
        </div>
        <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto p-2">
          {report.sections.map((s) => (
            <button
              key={s.id}
              onClick={() => s.status !== 'pending' && scrollTo(s.id)}
              disabled={s.status === 'pending'}
              className={cn(
                'flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition-colors',
                s.status === 'pending'
                  ? 'cursor-default text-faint'
                  : activeId === s.id
                    ? 'bg-elevated text-fg'
                    : 'text-muted hover:bg-elevated hover:text-fg',
                s.level === 2 && 'pl-5',
              )}
            >
              <span className="mt-1 grid size-3 shrink-0 place-items-center">
                {statusIcon(s.status)}
              </span>
              <span className="min-w-0 flex-1 truncate">{s.heading}</span>
            </button>
          ))}
        </div>
      </nav>

      {/* 본문 */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* 덱과 같은 이유로 접힌다. 이쪽은 버튼이 하나 더 많다. */}
        <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
          <FileText size={15} className="shrink-0 text-accent" />
          <p className="min-w-0 flex-1 truncate text-base font-medium max-sm:basis-full">
            {report.title}
          </p>
          <Button
            size="sm"
            className="lg:hidden"
            aria-label={t('목차')}
            onClick={() => setTocOpen((o) => !o)}
          >
            <ListTree size={13} />
            {t('목차')} {done}/{report.sections.length}
          </Button>
          <Badge>v{report.version}</Badge>
          {/* 편집 진입점. 항상 보이는 자리에 둔다 — hover 로만 드러나면 보고서가
              편집 가능하다는 것을 알아낼 방법이 마우스를 훑는 것뿐이 된다. */}
          {editing ? (
            <>
              <Button variant="primary" size="sm" disabled={saving} onClick={() => void saveDocument()}>
                <Check size={13} />
                {saving ? t('저장 중…') : t('저장')}
              </Button>
              <Button size="sm" onClick={() => openEditor(false)}>
                {t('취소')}
              </Button>
            </>
          ) : (
            <Button size="sm" onClick={() => void startEditing()} disabled={writing} aria-label={t('문서 수정')}>
              <Pencil size={13} />
              {t('수정')}
            </Button>
          )}
          <Button
            size="sm"
            variant={pane === 'sources' ? 'primary' : 'secondary'}
            aria-label={t('출처')}
            onClick={() => setPane((p) => (p === 'sources' ? 'document' : 'sources'))}
          >
            <Quote size={13} />
            {t('출처')} {report.sources.length}
          </Button>
          {/* 저장 시점. 되돌릴 수 있다는 사실이 편집 버튼 옆에 붙어 있어야,
              고치기 전에 "잘못 고치면 어쩌지" 를 묻지 않는다. */}
          <Button size="sm" aria-label={t('버전 기록')} onClick={() => void openVersions()}>
            <History size={13} />
            {t('저장 시점')} v{report.version}
          </Button>
          <PanelControls wide={focus} onToggleWide={onWideChange && toggleFocus} />
          <Button
            variant="ghost"
            size="icon"
            aria-label={t('인쇄')}
            title={t('이 보고서를 인쇄합니다')}
            onClick={() => window.print()}
          >
            <Printer size={15} />
          </Button>
          <Dropdown
            align="right"
            trigger={() => (
              <Button size="sm">
                <Download size={14} />
                {t('내보내기')}
              </Button>
            )}
          >
            <MenuLabel>{t('형식 선택')}</MenuLabel>
            {/* Built server-side from the stored sections, so the file matches
                what this panel shows rather than a fresh run of the model. */}
            <MenuItem hint="PDF" onClick={() => void download(report.id, 'pdf', report.title)}>
              PDF
            </MenuItem>
            <MenuItem hint="DOCX" onClick={() => void download(report.id, 'docx', report.title)}>
              {t('Word 문서')}
            </MenuItem>
            {/* Ahead of Markdown on purpose: a Korean submission box usually
                takes this and nothing else. */}
            <MenuItem hint="HWPX" onClick={() => void download(report.id, 'hwpx', report.title)}>
              {t('한글 문서')}
            </MenuItem>
            <MenuItem hint="MD" onClick={() => void download(report.id, 'md', report.title)}>
              {t('마크다운 원문')}
            </MenuItem>
          </Dropdown>
          <PanelControls wide={focus} onClose={onClose} />
        </header>

        <div
          ref={docRef}
          className="relative min-h-0 flex-1 overflow-y-auto"
          onMouseUp={readSelection}
        >
          {picked && (
            <div
              ref={handleRef}
              className="animate-fade-up absolute z-30 flex items-center gap-1 rounded-xl border border-line bg-panel p-1 shadow-xl"
              style={{ top: picked.top, left: picked.left }}
              // The bubble is a tool for the selection; a click that clears it
              // before the handler runs is a click that does nothing.
              onMouseDown={(e) => e.preventDefault()}
            >
              <Button size="sm" variant="ghost" onClick={rewritePicked}>
                <Sparkles size={13} className="text-accent" />
                {t('이 부분 고치기')}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label={t('선택 복사')}
                onClick={() => {
                  void copyText(picked.text)
                  setPicked(null)
                }}
              >
                <Copy size={13} />
              </Button>
            </div>
          )}
          {pane === 'sources' ? (
            <div className="mx-auto max-w-2xl px-6 py-6">
              <h2 className="mb-3 text-lg font-semibold">{t('참고문헌')}</h2>
              <SourceList sources={report.sources} style={report.citationStyle} />
            </div>
          ) : editing ? (
            /* Source on the left, live render on the right. What is edited stays
               Markdown: the report is stored that way and all four exporters read
               it that way, so editing the rendered form would need a converter
               whose round-trip is the thing most likely to lose a heading or a
               list number. The preview is the same `Markdown` the saved document
               renders with, so the right-hand side is what lands in the file. */
            /* Container query, not a viewport one: this lives in a side panel
               whose width the user drags, so `lg:` would split a 380px panel into
               two unusable columns on a wide screen. */
            <div className="@container flex h-full min-h-0 flex-col gap-2 px-4 py-4">
              <div className="grid min-h-0 flex-1 items-stretch gap-3 @lg:grid-cols-2">
                <div className="flex min-h-0 flex-col gap-1">
                  <p className="text-xs font-medium text-faint">
                    원본 (Markdown) · <code>{t('# 제목')}</code>, <code>## 절 제목</code>,{' '}
                    <code>{t('### 소제목')}</code>
                  </p>
                  <textarea
                    aria-label={t('문서 원본')}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Escape') openEditor(false)
                      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void saveDocument()
                    }}
                    className="min-h-0 w-full flex-1 resize-none rounded-xl border border-line bg-panel px-3 py-2 font-mono text-base leading-relaxed outline-none focus:border-accent"
                    autoFocus
                  />
                </div>
                <div className="flex min-h-0 flex-col gap-1">
                  <p className="text-xs font-medium text-faint">{t('미리보기')}</p>
                  <div
                    aria-label={t('문서 미리보기')}
                    className="min-h-[10rem] flex-1 overflow-auto rounded-xl border border-line bg-elevated px-4 py-3"
                  >
                    {draft.trim() ? (
                      <Markdown>{draft}</Markdown>
                    ) : (
                      <p className="text-base text-faint">{t('내용을 입력하면 여기에 결과가 보입니다.')}</p>
                    )}
                  </div>
                </div>
              </div>
              {saveError && <p className="text-base text-danger">{saveError}</p>}
              <p className="text-xs text-faint">
                {t('⌘/Ctrl+Enter 저장 · Esc 취소 · 저장하면 이전 판은 버전 기록에 남습니다')}
              </p>
            </div>
          ) : (
          <article className="mx-auto max-w-2xl px-6 py-6">
            <h1 className="mb-6 text-2xl font-semibold tracking-tight">{report.title}</h1>
            {report.sections.map((s) => (
              <section key={s.id} id={`sec-${s.id}`} className="mb-8 scroll-mt-4">
                <div className="group/sec mb-2 flex items-center gap-2">
                  <h2
                    className={cn('text-lg font-semibold', s.status === 'pending' && 'text-faint')}
                  >
                    {s.heading}
                  </h2>
                  {/* 절 하나만 다시 쓴다. 지도교수 피드백이 넷째 절에 오면
                      나머지 다섯을 다시 쓰는 것은 아무도 원하지 않는다. */}
                  {s.status === 'done' && !editing && (
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={t('{name} 다시 쓰기').replace('{name}', s.heading)}
                      className="opacity-0 transition-opacity group-hover/sec:opacity-100 focus:opacity-100"
                      onClick={() => {
                        setRewriting(s.id)
                        setRewriteNote('')
                        setRewriteQuote('')
                      }}
                    >
                      <RefreshCw size={12} />
                        {t('이 절만 다시 쓰기')}
                    </Button>
                  )}
                </div>
                {rewriting === s.id && (
                  <div className="mb-3 space-y-2 rounded-xl border border-line bg-elevated p-3">
                    {/* 고칠 대목을 먼저 보여 준다. 지시만 남으면 무엇에 대한
                        지시였는지는 보낸 사람 머릿속에만 있다. */}
                    {rewriteQuote && (
                      <div
                        aria-label={t('고칠 대목')}
                        className="flex items-start gap-2 rounded-lg border border-accent/25 bg-accent-soft/60 px-2.5 py-1.5"
                      >
                        <Quote size={12} className="mt-0.5 shrink-0 text-accent" />
                        <p className="min-w-0 flex-1 text-sm leading-relaxed text-muted">
                          {rewriteQuote.length > 220
                            ? `${rewriteQuote.slice(0, 220)}…`
                            : rewriteQuote}
                        </p>
                        <button
                          onClick={() => setRewriteQuote('')}
                          aria-label={t('선택 해제')}
                          className="shrink-0 text-faint hover:text-fg"
                        >
                          <X size={12} />
                        </button>
                      </div>
                    )}
                    <Textarea
                      rows={2}
                      autoFocus
                      value={rewriteNote}
                      onChange={(e) => setRewriteNote(e.target.value)}
                      placeholder={
                        rewriteQuote
                          ? t('이 대목을 어떻게 바꿀지 적으세요. 예: 근거를 붙여서 두 문장으로.')
                          : t('무엇을 바꿀지 적으세요. 비워 두면 처음부터 다시 씁니다.')
                      }
                      aria-label={t('다시 쓰기 지시')}
                    />
                    <div className="flex items-center gap-2">
                      <Button
                        variant="primary"
                        size="sm"
                        disabled={rewriteBusy}
                        onClick={() => void rewriteSection(s.id)}
                      >
                        {rewriteBusy && <Loader2 size={13} className="animate-spin" />}
                        {t('다시 쓰기')}
                      </Button>
                      <Button size="sm" onClick={() => setRewriting(null)} disabled={rewriteBusy}>
                        {t('취소')}
                      </Button>
                      <span className="text-xs text-faint">
                        {t('이전 내용은 버전 기록에 남습니다.')}
                      </span>
                    </div>
                    {rewriteError && <p className="text-sm text-danger">{rewriteError}</p>}
                  </div>
                )}
                {s.status === 'pending' ? (
                  <div className="space-y-2">
                    {[100, 92, 74].map((w) => (
                      <div
                        key={w}
                        className="h-3 rounded bg-elevated"
                        style={{ width: `${w}%` }}
                      />
                    ))}
                  </div>
                ) : (
                  <>
                    <Markdown>{s.content}</Markdown>
                    {s.status === 'streaming' && (
                      <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-blink bg-accent" />
                    )}
                  </>
                )}
              </section>
            ))}
          </article>
          )}
        </div>
      </div>

      <Modal
        open={showVersions}
        onClose={() => setShowVersions(false)}
        title={t('버전 기록')}
        description={`${report.title} · ${t('현재')} v${report.version}`}
      >
        <div className="space-y-1.5">
          {versions === null && <p className="text-base text-faint">{t('불러오는 중…')}</p>}
          {versions?.length === 0 && (
            <p className="text-base text-faint">{t('아직 저장된 이전 판이 없습니다.')}</p>
          )}
          {/* Only superseded revisions come back — the current one is the
              document on screen, and offering to restore it would be a button
              that does nothing. */}
          {versions?.map(({ version: v, summary, createdAt }) => (
            <div
              key={v}
              className="flex items-center gap-3 rounded-xl border border-line px-3 py-2.5"
            >
              <Badge>v{v}</Badge>
              <div className="min-w-0 flex-1">
                <p className="text-base">{summary || t('편집')}</p>
                <p className="text-xs text-faint">{relativeTime(createdAt)}</p>
              </div>
              <Button
                size="sm"
                disabled={restoring !== null}
                aria-label={t('v{n} 로 되돌리기').replace('{n}', String(v))}
                onClick={() => void restore(v)}
              >
                {restoring === v ? t('되돌리는 중…') : t('되돌리기')}
              </Button>
            </div>
          ))}
        </div>
      </Modal>
    </div>
  )
}
