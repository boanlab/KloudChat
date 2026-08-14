import {
  Check,
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
  X,
} from 'lucide-react'
import { useState } from 'react'
import { Markdown } from '@/components/chat/Markdown'
import { Badge, Button, Dropdown, MenuItem, MenuLabel, Modal, Textarea } from '@/components/ui'
import { artifactsApi, downloadArtifact as download } from '@/lib/api'
import type { ArtifactVersionRow } from '@/lib/api'
import { fromMarkdown, toMarkdown } from '@/lib/reportMarkdown'
import { cn, formatTokens, relativeTime } from '@/lib/utils'
import type { ReportArtifact, ReportSection, Source } from '@/types'
import { useT } from '@/lib/useT'

const originIcon = { web: Link2, connector: Plug, file: Paperclip }

/** Sources sit beside the prose, not only at the end: an untraceable sentence
 *  is one that cannot be submitted. */
function SourceList({ sources, style }: { sources: Source[]; style: string }) {
  const t = useT()
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-faint">
        {t('{style} 형식 · {n}건').replace('{style}', style).replace('{n}', String(sources.length))}
      </p>
      {sources.map((src) => {
        const Icon = originIcon[src.origin]
        return (
          <div key={src.id} className="rounded-xl border border-line bg-panel p-3">
            <div className="flex items-start gap-2">
              <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-md bg-accent-soft text-[10px] font-semibold text-accent">
                {src.ordinal}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-medium">{src.title}</p>
                <p className="mt-0.5 text-[12px] text-muted">
                  {[src.author, src.publisher, src.year].filter(Boolean).join(' · ')}
                </p>
                {src.quote && (
                  <p className="mt-1.5 border-l-2 border-line-strong pl-2 text-[12px] text-muted">
                    {src.quote}
                  </p>
                )}
                <p className="mt-1.5 flex items-center gap-1.5 text-[11px] text-faint">
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
  onEditingChange,
}: {
  report: ReportArtifact
  onClose?: () => void
  /** Fires when a section opens or closes for editing, so the host can widen
   *  the panel — the document column is ~350px at the reading width. */
  onEditingChange?: (editing: boolean) => void
}) {
  const t = useT()
  const [activeId, setActiveId] = useState<string | null>(null)
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
    //: Document editing is one mode over the whole report. The title, the
    //: section headings and the space between sections belong to no section,
    //: so a per-section editor cannot reach them.
  const [editing, setEditing] = useState(false)
  //: Which section is open for a rewrite, and the instruction going with it.
  const [rewriting, setRewriting] = useState<string | null>(null)
  const [rewriteNote, setRewriteNote] = useState('')
  const [rewriteBusy, setRewriteBusy] = useState(false)
  const [rewriteError, setRewriteError] = useState<string | null>(null)

  const rewriteSection = async (sectionId: string) => {
    setRewriteBusy(true)
    setRewriteError(null)
    try {
      const row = await artifactsApi.rewriteSection(report.id, sectionId, rewriteNote)
      const data = (row.data ?? {}) as { sections?: ReportSection[] }
      if (data.sections) report.sections = data.sections
      report.version = row.version
      setRewriting(null)
    } catch (err) {
      setRewriteError(err instanceof Error ? err.message : t('다시 쓰지 못했습니다.'))
    } finally {
      setRewriteBusy(false)
    }
  }
  //: Every transition goes through here, so the host is never left with a
  //: widened panel and no editor in it.
  const openEditor = (open: boolean) => {
    setEditing(open)
    onEditingChange?.(open)
  }
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  //: Nothing to edit until the model stops: a mid-run save would freeze
  //: half-written prose and mark every section done.
  const writing = report.sections.some((s) => s.status !== 'done')

  const startEditing = () => {
    setSaveError(null)
    setDraft(toMarkdown(report))
    openEditor(true)
  }

  const saveDocument = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const parsed = fromMarkdown(draft, report.sections)
      if (parsed.sections.length === 0) {
        setSaveError(t('내용이 비어 있습니다. 저장하지 않았습니다.'))
        return
      }
      // An emptied `#` line is a deleted line, not a request for no title.
      const title = parsed.title || report.title
      // PATCHing `data` whole is what snapshots the previous revision
      // server-side, which is the way back from a bad edit.
      await artifactsApi.update(report.id, {
        data: { ...report, title, sections: parsed.sections },
        title,
        summary: t('문서 편집'),
      })
      // Local mutation, so the panel reflects the save without a refetch.
      report.title = title
      report.sections = parsed.sections
      openEditor(false)
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : t('저장하지 못했습니다.'))
    } finally {
      setSaving(false)
    }
  }
  const done = report.sections.filter((s) => s.status === 'done').length

  const scrollTo = (id: string) => {
    setActiveId(id)
    setTocOpen(false)
    document.getElementById(`sec-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="relative flex h-full min-h-0">
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
          <p className="text-[11px] font-semibold tracking-wide text-faint uppercase">{t('목차')}</p>
          <p className="mt-1 text-[11px] text-muted">
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
                'flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left text-[12px] transition-colors',
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
        <header className="flex items-center gap-2 border-b border-line px-4 py-2.5">
          <FileText size={15} className="shrink-0 text-accent" />
          <p className="min-w-0 flex-1 truncate text-[13px] font-medium">{report.title}</p>
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
            <Button size="sm" onClick={startEditing} disabled={writing} aria-label={t('문서 수정')}>
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
          <Button
            variant="ghost"
            size="icon"
            aria-label={t('버전 기록')}
            onClick={() => void openVersions()}
          >
            <History size={15} />
          </Button>
          <Button variant="ghost" size="icon" aria-label={t('인쇄')} onClick={() => window.print()}>
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
          {onClose && (
            <Button variant="ghost" size="icon" aria-label={t('닫기')} onClick={onClose}>
              <X size={15} />
            </Button>
          )}
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
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
                  <p className="text-[11px] font-medium text-faint">
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
                    className="min-h-0 w-full flex-1 resize-none rounded-xl border border-line bg-panel px-3 py-2 font-mono text-[13px] leading-relaxed outline-none focus:border-accent"
                    autoFocus
                  />
                </div>
                <div className="flex min-h-0 flex-col gap-1">
                  <p className="text-[11px] font-medium text-faint">{t('미리보기')}</p>
                  <div
                    aria-label={t('문서 미리보기')}
                    className="min-h-[10rem] flex-1 overflow-auto rounded-xl border border-line bg-elevated px-4 py-3"
                  >
                    {draft.trim() ? (
                      <Markdown>{draft}</Markdown>
                    ) : (
                      <p className="text-[13px] text-faint">{t('내용을 입력하면 여기에 결과가 보입니다.')}</p>
                    )}
                  </div>
                </div>
              </div>
              {saveError && <p className="text-[13px] text-danger">{saveError}</p>}
              <p className="text-[11px] text-faint">
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
                      }}
                    >
                      <RefreshCw size={12} />
                        {t('이 절만 다시 쓰기')}
                    </Button>
                  )}
                </div>
                {rewriting === s.id && (
                  <div className="mb-3 space-y-2 rounded-xl border border-line bg-elevated p-3">
                    <Textarea
                      rows={2}
                      autoFocus
                      value={rewriteNote}
                      onChange={(e) => setRewriteNote(e.target.value)}
                      placeholder={t('무엇을 바꿀지 적으세요. 비워 두면 처음부터 다시 씁니다.')}
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
                      <span className="text-[11px] text-faint">
                        {t('이전 내용은 버전 기록에 남습니다.')}
                      </span>
                    </div>
                    {rewriteError && <p className="text-[12px] text-danger">{rewriteError}</p>}
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
          {versions === null && <p className="text-[13px] text-faint">{t('불러오는 중…')}</p>}
          {versions?.length === 0 && (
            <p className="text-[13px] text-faint">{t('아직 저장된 이전 판이 없습니다.')}</p>
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
                <p className="text-[13px]">{summary || t('편집')}</p>
                <p className="text-[11px] text-faint">{relativeTime(createdAt)}</p>
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
