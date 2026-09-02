import {
  Check,
  Code2,
  Copy,
  Download,
  ExternalLink,
  FileText,
  ImagePlus,
  ListPlus,
  Link2,
  ListTree,
  Loader2,
  RefreshCw,
  Paperclip,
  Palette,
  Pencil,
  Plug,
  FileType2,
  Printer,
  Quote,
  ShieldQuestion,
  Sparkles,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Markdown } from '@/components/chat/Markdown'
import {
  PanelControls,
  nextMode,
  type PanelMode,
} from '@/components/artifacts/PanelControls'
import { PicturePicker } from '@/components/artifacts/PicturePicker'
import { ArtifactRibbon, QuickAccess, RibbonGroup } from '@/components/artifacts/ArtifactRibbon'
import { usePanelNarrow } from '@/lib/usePanelNarrow'
import { Button, ConfirmDialog, Dropdown, Input, MenuItem, MenuLabel, Modal, Textarea } from '@/components/ui'
import { artifactsApi, downloadArtifact as download, errorMessage } from '@/lib/api'
import { fromMarkdown, toMarkdown } from '@/lib/reportMarkdown'
import { cn, formatTokens } from '@/lib/utils'
import type { LintFinding, ReportArtifact, ReportSection, Source } from '@/types'
import { copyText } from '@/lib/clipboard'
import { DocumentEditor } from '@/components/report/DocumentEditor'
import { SectionBody, sectionText } from '@/components/report/SectionBody'
import { FactCheckResults } from '@/components/artifacts/FactCheckResults'
import { LintFindings, byWhere, fixNote } from '@/components/artifacts/LintFindings'
import { VersionHistory } from '@/components/artifacts/VersionHistory'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

/**
 * Putting a picture into one section of a report.
 *
 * The page track has had this since it shipped and the report track — the
 * surface most of this product's writing happens on — had no way to put a
 * picture in a document at all. Not for want of machinery: a Markdown picture
 * line is read by `richtext` on the way in and by all three exporters on the
 * way out, so the server appends one and the `.docx`, `.pdf` and `.hwpx` carry
 * it without changing.
 *
 * The picture is chosen or made in the same dialog. See `PicturePicker`: the
 * old flow sent somebody to the image screen and back, which loses the section
 * they were filling.
 */
function AddSectionImage({ report }: { report: ReportArtifact }) {
  const t = useT()
  const [target, setTarget] = useState<string | null>(null)
  const [picked, setPicked] = useState<string | null>(null)
  const [caption, setCaption] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const refreshArtifact = useStore((s) => s.refreshArtifact)
  const loadArtifacts = useStore((s) => s.loadArtifacts)

  const sections = report.sections ?? []
  if (sections.length === 0) return null
  const chosen = sections.find((s) => s.id === target)

  const insert = async () => {
    if (!target || !picked) return
    setBusy(true)
    setError(null)
    try {
      await artifactsApi.addSectionImage(report.id, target, picked, caption.trim())
      await refreshArtifact(report.id)
      await loadArtifacts()
      setTarget(null)
      setPicked(null)
      setCaption('')
    } catch (err) {
      setError(errorMessage(err, t('그림을 넣지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Dropdown
        align="right"
        trigger={() => (
          <Button variant="ghost" size="icon" aria-label={t('그림 넣기')} title={t('그림 넣기')}>
            <ImagePlus size={15} />
          </Button>
        )}
      >
        <MenuLabel>{t('어느 절에 넣을까요?')}</MenuLabel>
        {sections.map((section, index) => (
          <MenuItem
            key={section.id}
            hint={String(index + 1)}
            onClick={() => {
              setPicked(null)
              setCaption('')
              setError(null)
              setTarget(section.id)
            }}
          >
            {section.heading || t('제목 없음')}
          </MenuItem>
        ))}
      </Dropdown>

      <Modal
        open={target !== null}
        onClose={() => setTarget(null)}
        title={t('{name}에 그림 넣기').replace('{name}', chosen?.heading || t('이 절'))}
        description={t('여기서 바로 만들거나 이미 만든 그림을 고를 수 있습니다. 링크가 아니라 파일 안에 담기므로 인쇄와 공유에서도 함께 보입니다.')}
        footer={
          <>
            <Button onClick={() => setTarget(null)} disabled={busy}>
              {t('취소')}
            </Button>
            <Button variant="primary" onClick={() => void insert()} disabled={busy || !picked}>
              {busy ? t('넣는 중…') : t('넣기')}
            </Button>
          </>
        }
      >
        <PicturePicker
          sessionId={report.sessionId}
          /* A figure in a report sits in a text column, not across a slide. */
          aspect="4:3"
          picked={picked}
          onPick={setPicked}
          caption={caption}
          onCaption={setCaption}
          about={chosen?.heading}
          title={report.title}
          context={chosen?.content}
        />
        {error && <p className="mt-2 text-base text-danger">{error}</p>}
      </Modal>
    </>
  )
}

/**
 * The section a finding was found under, or `undefined`.
 *
 * Matched on the heading, which is all a finding carries. Exact first, then
 * ignoring whitespace: a heading somebody has retyped in the page view differs
 * from the one the checks ran against by exactly that much, and refusing to fix
 * a finding because a heading gained a space is a worse answer than fixing the
 * section it obviously means.
 */
function sectionFor(sections: ReportSection[], where: string): ReportSection | undefined {
  if (!where) return undefined
  const exact = sections.find((s) => s.heading === where)
  if (exact) return exact
  const loose = (text: string) => text.replace(/\s+/g, '')
  return sections.find((s) => loose(s.heading) === loose(where))
}

/** A passage the reader picked out, and the section it belongs to. */
interface Picked {
  sectionId: string
  text: string
  /** Where to float the toolbar, relative to the scrolling document. */
  top: number
  left: number
}

const originIcon = { web: Link2, connector: Plug, file: Paperclip }
const citationStyles: ReportArtifact['citationStyle'][] = ['APA', 'MLA', 'Chicago', 'IEEE']

/** A reference as it will read in the exported document. */
export function citationText(src: Source, style: ReportArtifact['citationStyle']): string {
  const title = src.title.trim() || '제목 없음'
  const author = src.author?.trim() ?? ''
  const publisher = src.publisher?.trim() ?? ''
  const year = src.year?.trim() ?? ''
  const url = src.url?.trim() ?? ''
  if (style === 'IEEE') {
    const details = [publisher, year].filter(Boolean).join(', ')
    return `[${src.ordinal}] ${author ? `${author}, ` : ''}“${title}.”${details ? ` ${details}.` : ''}${url ? ` [온라인]. ${url}` : ''}`
  }
  if (style === 'MLA') {
    return [`${author ? `${author}.` : ''}`, `“${title}.”`, publisher, year, url]
      .filter(Boolean)
      .join(' ')
      .replace(/\.*$/, '.')
  }
  if (style === 'Chicago') {
    const details = [publisher, year].filter(Boolean).join(', ')
    return `${author ? `${author}. ` : ''}“${title}.”${details ? ` ${details}.` : ''}${url ? ` ${url}` : ''}`
  }
  return `${author ? `${author}. ` : ''}${year ? `(${year}). ` : ''}${title}${[publisher, url].filter(Boolean).length ? `. ${[publisher, url].filter(Boolean).join('. ')}` : ''}`
}

/** Sections that visibly carry this source's numbered marker. */
export function citedSections(source: Source, sections: ReportSection[]): ReportSection[] {
  const marker = new RegExp(`(?:\\[|［)\\s*${source.ordinal}\\s*(?:\\]|］)`)
  return sections.filter((section) => marker.test(sectionText(section)))
}

/** Every numbered marker found in the body, including numbers with no source. */
export function citationNumbers(sections: ReportSection[]): number[] {
  const found = new Set<number>()
  for (const section of sections) {
    for (const match of sectionText(section).matchAll(/(?:\[|［)\s*(\d+)\s*(?:\]|］)/g)) {
      found.add(Number(match[1]))
    }
  }
  return [...found].sort((left, right) => left - right)
}

export interface NumericEvidenceGap {
  section: ReportSection
  excerpts: string[]
}

/** Numeric claims that have no `[n]` marker in the same sentence. */
export function numericEvidenceGaps(sections: ReportSection[]): NumericEvidenceGap[] {
  const numericFact = /\d[\d,.]*\s*(?:%|％|원|명|건|개|배|년|월|일|시간|분|초|점|대|회|쪽|페이지|GB|MB|km|kg)/i
  const marker = /(?:\[|［)\s*\d+\s*(?:\]|］)/
  const gaps: NumericEvidenceGap[] = []
  for (const section of sections) {
    const excerpts = sectionText(section)
      .split(/(?<=[.!?。！？])\s+|\n+/)
      .map((sentence) => sentence.trim())
      .filter(
        (sentence) =>
          sentence.length > 0 &&
          numericFact.test(sentence) &&
          !marker.test(sentence) &&
          !/^\d+[.)]\s/.test(sentence),
      )
      .slice(0, 3)
    if (excerpts.length) gaps.push({ section, excerpts })
  }
  return gaps
}

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
          {/* No owner: printing is reading, and a print that wrote a picture
              back into the document would be a document that changed because
              somebody pressed 인쇄. The panel above has already stored it. */}
          <SectionBody section={s} />
        </section>
      ))}
      {/* Citations: what makes a printed report submittable. */}
      {report.sources.length > 0 && (
        <section>
          <h2>{t('참고문헌')}</h2>
          <ol>
            {report.sources.map((src) => (
              <li key={src.id}>{citationText(src, report.citationStyle)}</li>
            ))}
          </ol>
        </section>
      )}
    </article>,
    document.body,
  )
}

/** Reference list, shown beside the prose as well as at the end. */
function SourceList({
  sources,
  research,
  style,
  onStyle,
  sections,
  onJump,
  onRemove,
  saving,
}: {
  sources: Source[]
  research?: ReportArtifact['research']
  style: ReportArtifact['citationStyle']
  onStyle: (style: ReportArtifact['citationStyle']) => void
  sections: ReportSection[]
  onJump: (sectionId: string) => void
  onRemove: (source: Source) => void
  saving: boolean
}) {
  const t = useT()
  const numericGaps = numericEvidenceGaps(sections)
  const researchSummary = research && (
    <div className="rounded-card border border-line bg-elevated p-3" data-testid="research-log">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-medium">{t('조사 기록')}</p>
          <p className="mt-0.5 text-xs text-muted">
            {!research.enabled
              ? t('웹 검색을 사용하지 않았습니다.')
              : research.searched
                ? t('검색어 {queries}개 · 채택 {selected}건 · 제외 {excluded}건')
                    .replace('{queries}', String(research.queries.length))
                    .replace('{selected}', String(research.selected))
                    .replace('{excluded}', String(research.excluded))
                : t('웹 검색을 요청했지만 검색 서비스를 사용할 수 없었습니다.')}
          </p>
        </div>
        {research.searched && (
          <span className="rounded-control bg-success/10 px-2 py-1 text-xs text-success">
            {t('검색 완료')}
          </span>
        )}
      </div>
      {((research.projectSelected ?? 0) > 0 || (research.projectExcluded ?? 0) > 0) && (
        <div className="mt-2 flex flex-wrap gap-2 border-t border-line pt-2 text-xs text-muted">
          <span>{t('웹 검색 {n}건').replace('{n}', String(research.webSelected ?? 0))}</span>
          <span>{t('프로젝트 자료 {n}건 사용').replace('{n}', String(research.projectSelected ?? 0))}</span>
          {(research.projectExcluded ?? 0) > 0 && (
            <span className="text-danger">
              {t('분량 때문에 제외 {n}건').replace('{n}', String(research.projectExcluded))}
            </span>
          )}
        </div>
      )}
      {research.queries.length > 0 && (
        <ol className="mt-3 space-y-1 border-t border-line pt-2">
          {research.queries.map((query, index) => (
            <li key={`${index}-${query}`} className="flex gap-2 text-sm text-fg">
              <span className="text-faint">{index + 1}</span>
              <span>{query}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
  /*
   * Nothing to cite, said out loud.
   *
   * Pressing 출처 0 swapped the whole document away for a heading, a line
   * reading APA 형식 · 0건, and white space. Somebody who did that on their
   * first document lost the thing they were reading to look at nothing, with
   * no word about why it was empty or what would fill it — and the report they
   * had just written was produced with web search off, which is the answer.
   */
  if (sources.length === 0) {
    return (
      <div className="space-y-3">
        {researchSummary}
        <div className="rounded-card border border-dashed border-line px-4 py-8 text-center">
          <p className="text-base text-muted">{t('참고한 자료가 없습니다.')}</p>
          <p className="mt-1 text-sm text-faint">
            {t('웹 검색을 켜고 다시 쓰면 찾은 자료가 여기 출처로 붙습니다. 검색 없이 쓴 글에는 붙일 출처가 없습니다.')}
          </p>
          {numericGaps.length > 0 && (
            <div className="mt-4 flex flex-wrap justify-center gap-2 text-sm text-danger">
              <span>
                {t('근거 표시가 필요한 수치 문장 {count}개').replace(
                  '{count}',
                  String(numericGaps.reduce((sum, gap) => sum + gap.excerpts.length, 0)),
                )}
              </span>
              {numericGaps.map((gap) => (
                <button key={gap.section.id} type="button" onClick={() => onJump(gap.section.id)} className="underline">
                  {gap.section.heading}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    )
  }
  const usedNumbers = citationNumbers(sections)
  const knownNumbers = new Set(sources.map((source) => source.ordinal))
  const unknownNumbers = usedNumbers.filter((number) => !knownNumbers.has(number))
  const usedSources = sources.filter((source) => usedNumbers.includes(source.ordinal)).length
  return (
    <div className="space-y-2">
      {researchSummary}
      <div
        className={cn(
          'rounded-card border px-3 py-2 text-sm',
          unknownNumbers.length
            ? 'border-danger/30 bg-danger/10 text-danger'
            : 'border-line bg-elevated text-muted',
        )}
      >
        <span className="font-medium">{t('인용 점검')}</span>
        <span className="ml-2">
          {t('자료 {used}/{total}개 사용')
            .replace('{used}', String(usedSources))
            .replace('{total}', String(sources.length))}
        </span>
        {unknownNumbers.length > 0 && (
          <span className="ml-2">
            {t('목록에 없는 인용 {numbers}').replace(
              '{numbers}',
              unknownNumbers.map((number) => `[${number}]`).join(', '),
            )}
          </span>
        )}
        {numericGaps.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-danger/20 pt-2">
            <span>
              {t('근거 표시가 필요한 수치 문장 {count}개').replace(
                '{count}',
                String(numericGaps.reduce((sum, gap) => sum + gap.excerpts.length, 0)),
              )}
            </span>
            {numericGaps.map((gap) => (
              <button
                key={gap.section.id}
                type="button"
                title={gap.excerpts.join('\n')}
                onClick={() => onJump(gap.section.id)}
                className="rounded-control bg-panel px-2 py-1 text-xs underline decoration-danger/50 underline-offset-2"
              >
                {gap.section.heading}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-faint">{t('{n}건').replace('{n}', String(sources.length))}</p>
        <Dropdown
          align="right"
          trigger={() => (
            <Button size="sm" disabled={saving} aria-label={t('인용 형식')}>
              {saving && <Loader2 size={13} className="animate-spin" />}
              {style}
            </Button>
          )}
        >
          <MenuLabel>{t('인용 형식')}</MenuLabel>
          {citationStyles.map((candidate) => (
            <MenuItem
              key={candidate}
              checked={candidate === style}
              onClick={() => onStyle(candidate)}
            >
              {candidate}
            </MenuItem>
          ))}
        </Dropdown>
      </div>
      {sources.map((src) => {
        const Icon = originIcon[src.origin]
        const used = citedSections(src, sections)
        return (
          <div key={src.id} className="rounded-card border border-line bg-panel p-3">
            <div className="flex items-start gap-2">
              <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-control bg-accent-soft text-2xs font-semibold text-accent">
                {src.ordinal}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-base leading-relaxed">{citationText(src, style)}</p>
                {src.quote && (
                  <p className="mt-1.5 border-l-2 border-line-strong pl-2 text-sm text-muted">
                    {src.quote}
                  </p>
                )}
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <span className="text-xs text-faint">{t('본문에서 사용')}</span>
                  {used.length > 0 ? (
                    used.map((section) => (
                      <button
                        key={section.id}
                        type="button"
                        onClick={() => onJump(section.id)}
                        className="rounded-control bg-elevated px-2 py-1 text-xs text-fg hover:bg-accent-soft hover:text-accent"
                      >
                        {section.heading}
                      </button>
                    ))
                  ) : (
                    <span className="rounded-control border border-danger/30 bg-danger/10 px-2 py-1 text-xs text-danger">
                      {t('인용되지 않음')}
                    </span>
                  )}
                </div>
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
                  {used.length === 0 && (
                    <button
                      type="button"
                      aria-label={t('{title} 자료 삭제').replace('{title}', src.title)}
                      onClick={() => onRemove(src)}
                      className="ml-auto rounded-control p-1 text-faint hover:bg-danger/10 hover:text-danger"
                    >
                      <X size={12} />
                    </button>
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
 * The document, without the envelope it arrived in.
 *
 * `data` is the body; the row around it carries the facts *about* the body —
 * which version this is, when it was last written, which conversation it came
 * from. The panel holds the two merged into one object, and PATCHing that
 * object whole wrote a copy of the facts into the body. The store reads the
 * body last, so from the first save onward every screen showed the version and
 * the modified time the document had going *into* that save: a report rewritten
 * five times still read 저장 시점 v1, which is the one number a reader has for
 * telling this draft from the one they sent last week.
 *
 * `title` stays — the server reads it out of a snapshot when a restore puts one
 * back, and a snapshot without one restores under the wrong name.
 */
function documentBody(report: ReportArtifact): Record<string, unknown> {
  const body: Record<string, unknown> = { ...report }
  for (const fact of ['id', 'version', 'createdAt', 'updatedAt', 'sessionId', 'projectId', 'partial'])
    delete body[fact]
  return body
}

/**
 * Sections render as each one finishes rather than waiting for the document.
 * The table of contents doubles as the progress readout: pending sections are
 * visible from the start, greyed until written.
 */
export function ReportPanel({
  report,
  onClose,
  onModeChange,
  onDirtyChange,
}: {
  report: ReportArtifact
  onClose?: () => void
  /** How much room the document is asking for. The host owns whether there is
   *  any: the same report sits in a resizable side panel on one screen and in
   *  a fixed-width preview dialog on another. */
  onModeChange?: (mode: PanelMode) => void
  onDirtyChange?: (dirty: boolean) => void
}) {
  const t = useT()
  const [activeId, setActiveId] = useState<string | null>(null)
  //: How much room the document has. `wide` by default, which is the whole of
  //: the point. Measured on a 1600px screen before it was: the document's own
  //: paragraph was 436px, a Korean line of about twenty-six characters, while
  //: the transcript beside it held three sentences in 640px. The reading width
  //: existed the whole time behind a button somebody had to find, and a person
  //: who came to write a report was given a chat with a column of the report
  //: stapled to it.
  //:
  //: `full` folds the transcript away entirely — for reading, and for the
  //: writing that happens by hand rather than by asking. One more press on the
  //: same control, and one more press comes back.
  const [mode, setMode] = useState<PanelMode>('wide')
  const [ribbon, setRibbon] = useState<'home' | 'insert' | 'layout' | 'review' | 'view' | 'file'>('home')
  const [documentLayout, setDocumentLayout] = useState<'pages' | 'edit'>('pages')
  const [pageSettingsOpen, setPageSettingsOpen] = useState(false)
  const [pane, setPane] = useState<'document' | 'sources'>('document')
  const [citationSaving, setCitationSaving] = useState(false)
  const [citationError, setCitationError] = useState<string | null>(null)
  const [addingSource, setAddingSource] = useState(false)
  const [sourceDraft, setSourceDraft] = useState({ title: '', url: '', author: '', publisher: '', year: '' })
  // The parent holds the split and starts it narrow, so the default above has
  // to be announced rather than assumed. Once — this is an opening position,
  // not a thing to re-assert over a reader who has folded it back.
  useEffect(() => {
    onModeChange?.('wide')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  // The contents are a drawer at every width now, so nothing here asks how
  // wide the panel is. The `ref` stays: the drawer is positioned against it.
  const panel = usePanelNarrow<HTMLDivElement>()
  const [tocOpen, setTocOpen] = useState(false)
  //: Whole-document edit mode. Title, headings and the space between sections
  //: belong to no section, so a per-section editor cannot reach them.
  const [editing, setEditing] = useState(false)
  /**
   * 서식 줄이 들어앉을 리본 홈 칸.
   *
   * The document editor used to draw its own formatting bar directly under
   * this panel's header, its ribbon tabs and a ribbon row carrying two
   * buttons — so pressing 문서 수정 produced four stacked rows of chrome and
   * a nearly empty ribbon. The bar is portalled in here instead: the 홈 tab
   * *is* the formatting bar while the editor is open, which is both one row
   * fewer and where a word processor puts it.
   */
  const [toolbarSlot, setToolbarSlot] = useState<HTMLElement | null>(null)
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

  //: Which section is being checked, so only its button spins. Null when
  //: nothing is running — a document-wide spinner would say the whole report
  //: is being verified, and it never is.
  const [checking, setChecking] = useState<string | null>(null)
  const [checkError, setCheckError] = useState<string | null>(null)
  const bodyCitationNumbers = citationNumbers(report.sections)
  const knownCitationNumbers = new Set(report.sources.map((source) => source.ordinal))
  const evidenceWarningCount =
    bodyCitationNumbers.filter((number) => !knownCitationNumbers.has(number)).length +
    numericEvidenceGaps(report.sections).reduce((sum, gap) => sum + gap.excerpts.length, 0)

  const chooseCitationStyle = async (style: ReportArtifact['citationStyle']) => {
    if (style === report.citationStyle) return
    setCitationSaving(true)
    setCitationError(null)
    try {
      const row = await artifactsApi.update(report.id, {
        data: documentBody({ ...report, citationStyle: style }),
        summary: t('인용 형식 변경'),
        expectedVersion: report.version,
      })
      report.citationStyle = style
      report.version = row.version
    } catch (err) {
      setCitationError(errorMessage(err, t('인용 형식을 바꾸지 못했습니다.')))
    } finally {
      setCitationSaving(false)
    }
  }

  const saveSources = async (sources: Source[], summary: string) => {
    setCitationSaving(true)
    setCitationError(null)
    try {
      const row = await artifactsApi.update(report.id, {
        data: documentBody({ ...report, sources }),
        summary,
        expectedVersion: report.version,
      })
      report.sources = sources
      report.version = row.version
      return true
    } catch (err) {
      setCitationError(errorMessage(err, t('참고 자료를 저장하지 못했습니다.')))
      return false
    } finally {
      setCitationSaving(false)
    }
  }

  const addSource = async () => {
    const title = sourceDraft.title.trim()
    const url = sourceDraft.url.trim()
    if (!title || !/^https?:\/\//i.test(url)) return
    const ordinal = Math.max(0, ...report.sources.map((source) => source.ordinal)) + 1
    const source: Source = {
      id: `manual_${crypto.randomUUID()}`,
      ordinal,
      title,
      url,
      author: sourceDraft.author.trim() || undefined,
      publisher: sourceDraft.publisher.trim() || undefined,
      year: sourceDraft.year.trim() || undefined,
      origin: 'web',
      originLabel: t('직접 추가'),
    }
    if (await saveSources([...report.sources, source], t('참고 자료 추가'))) {
      setAddingSource(false)
      setSourceDraft({ title: '', url: '', author: '', publisher: '', year: '' })
    }
  }

  /**
   * One section's figures, against the web.
   *
   * Per section for the reason the deck runs per slide: a document-wide run is
   * a hundred unasked-for searches, and a hundred verdicts is not something a
   * reader can act on. What comes back annotates the section rather than
   * editing it, so there is no version snapshot and nothing to undo.
   */
  const factcheckSection = async (sectionId: string) => {
    setChecking(sectionId)
    setCheckError(null)
    try {
      const row = await artifactsApi.factcheckSection(report.id, sectionId)
      const data = (row.data ?? {}) as { sections?: ReportSection[] }
      if (data.sections) report.sections = data.sections
      report.version = row.version
    } catch (err) {
      setCheckError(errorMessage(err, t('확인하지 못했습니다.')))
    } finally {
      setChecking(null)
    }
  }

  /**
   * A weak verdict, handed to the revision path as an instruction.
   *
   * The check already knows which claim and why. Making the reader carry that
   * to the composer themselves — find the sentence, decide the wording, type
   * it — is the difference between a report that flags problems and one that
   * fixes them, and it is the reason the fact-check felt like a report card
   * rather than a tool.
   *
   * Sent as an ordinary message so the whole loop applies: it lands on the
   * section it belongs to, the previous text is snapshotted, and the turn
   * shows in the transcript like any other.
   */
  const fixClaim = async (
    section: ReportSection,
    claim: NonNullable<ReportSection['factCheck']>['claims'][number],
  ) => {
    const why =
      claim.verdict === 'unsupported'
        ? t('검색으로 뒷받침되지 않았습니다')
        : t('확인하지 못했습니다')
    await send(
      report.sessionId ?? '',
      'report',
      t('"{heading}" 절의 이 대목을 고쳐 주세요. {why}: "{claim}" — {note}')
        .replace('{heading}', section.heading)
        .replace('{why}', why)
        .replace('{claim}', claim.text)
        .replace('{note}', claim.note),
    )
  }

  /**
   * One finding from the checks, fixed.
   *
   * Rewrites the section it was found under, through the same path the reader
   * uses when they select a passage and ask for it again — so the document
   * changes, a snapshot is kept, and a rewrite that reads worse is one press of
   * 되돌리기 from undone.
   *
   * The first version of this sent a sentence to the conversation instead. That
   * looks like an action and is a request: the document does not change, and
   * the reader has to watch the transcript and work out for themselves whether
   * anything happened. It is the right shape only for a finding with nowhere to
   * send it — one about the document as a whole, which names no section — and
   * that is what it is kept for below.
   */
  const fixFinding = async (finding: LintFinding) => {
    const section = sectionFor(report.sections, finding.where)
    if (!section) {
      // Nothing to rewrite: the finding is about the document rather than a
      // part of it. The conversation is where a change of that size belongs.
      await send(
        report.sessionId ?? '',
        'report',
        t('보고서 전체에서 이 문제를 고쳐 주세요: {message}').replace(
          '{message}',
          finding.message,
        ),
      )
      return
    }
    const row = await artifactsApi.rewriteSection(
      report.id,
      section.id,
      t('검사에서 지적된 문제를 고쳐 주세요: {message}').replace('{message}', finding.message),
    )
    const data = (row.data ?? {}) as { sections?: ReportSection[] }
    // Written onto the object this panel holds as well as into the store — the
    // artifacts screen opens its modal on a copy it took when the card was
    // clicked, so a store refresh alone leaves the new text invisible exactly
    // where it was asked for. Same move `rewriteSection` makes.
    if (data.sections) report.sections = data.sections
    report.version = row.version
  }

  /**
   * Every finding at once, one rewrite per section.
   *
   * Not a loop over `fixFinding`. Three findings about one section would be
   * three rewrites of it, each working on what the last one produced — so the
   * second is asked to fix a sentence that is no longer there and, often
   * enough, writes the first fix back out. Grouped, a section is rewritten
   * once and told everything that was found in it.
   *
   * Sections are rewritten one after another rather than together: they share
   * a document and a version, and two rewrites in flight means the second
   * saves over the first.
   */
  const fixAllFindings = async (findings: LintFinding[]) => {
    const groups = byWhere(findings)
    const loose = groups.get('') ?? []
    const failed: string[] = []
    for (const [where, group] of groups) {
      if (!where) continue
      const section = sectionFor(report.sections, where)
      if (!section) {
        loose.push(...group)
        continue
      }
      try {
        const row = await artifactsApi.rewriteSection(
          report.id,
          section.id,
          fixNote(
            group,
            t('검사에서 지적된 문제를 고쳐 주세요: {message}'),
            t('검사에서 지적된 문제를 모두 고쳐 주세요:\n{list}'),
          ),
        )
        const data = (row.data ?? {}) as { sections?: ReportSection[] }
        if (data.sections) report.sections = data.sections
        report.version = row.version
      } catch {
        failed.push(where)
      }
    }
    // What no section owns goes to the conversation, the way one of them does
    // — as one message rather than as one message each.
    if (loose.length > 0) {
      await send(
        report.sessionId ?? '',
        'report',
        fixNote(
          loose,
          t('보고서 전체에서 이 문제를 고쳐 주세요: {message}'),
          t('보고서 전체에서 이 문제들을 고쳐 주세요:\n{list}'),
        ),
      )
    }
    if (failed.length > 0) {
      throw new Error(
        t('고치지 못한 절이 있습니다: {list}').replace('{list}', failed.join(', ')),
      )
    }
  }

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
    // An editor needs source and preview side by side, so it never runs in the
    // narrow position — but it does not take the transcript away from somebody
    // who had folded it open.
    const wanted: PanelMode = open && mode === 'narrow' ? 'wide' : mode
    setMode(wanted)
    onModeChange?.(wanted)
  }
  const cycleMode = () => {
    const next = editing && nextMode(mode) === 'narrow' ? 'wide' : nextMode(mode)
    setMode(next)
    onModeChange?.(next)
  }
  const [draft, setDraft] = useState('')
  const [discardAction, setDiscardAction] = useState<'cancel' | 'close' | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [recoveryCopied, setRecoveryCopied] = useState(false)
  const [restructuring, setRestructuring] = useState(false)
  //: The sections live on the artifact object rather than in state — every
  //: other edit here mutates it and the panel re-renders when the store
  //: refreshes. A structural edit changes nothing the store watches, so it
  //: needs a nudge of its own or the list stays as it was.
  const [, setTick] = useState(0)
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

  /**
   * Which way the document is being worked on.
   *
   * `web` is prose in the app's own typography: the fastest thing to read, to
   * scroll and to rewrite a section of, and where the fact-check verdicts sit.
   * `page` is the document in its 서식, at A4 width, with the page rules drawn
   * — and it is editable, because a 서식 that can only be looked at is a
   * printout rather than a document.
   *
   * A view, not a fork. Before this, choosing a 서식 at generation produced an
   * HTML artifact nothing could edit, and choosing none produced prose with no
   * shape; neither could become the other afterwards. One stored document, two
   * ways to work on it.
   */
  //: A document written into a 서식 opens as pages. It was made to be looked
  //: at that way, and opening it as plain prose would hide the shape somebody
  //: chose before they ever saw it.
  const [view, setView] = useState<'web' | 'page'>(report.templateId ? 'page' : 'web')
  //: The 서식 this document is written in.
  //:
  //: It used to be view state — which stylesheet the page view drew in, kept
  //: locally so trying one on wrote nothing. That made sense while every 서식
  //: carried its own typesetting and switching visibly changed the paper.
  //: The typesettings are one now, so ten options produced one screen and the
  //: control did nothing at all.
  //:
  //: What a 서식 decides today is the file: the exporter opens that 서식's
  //: `.docx` and the document comes out in its styles, page and theme. So the
  //: choice belongs on the document, and this is where it is made.
  const [templateId, setTemplateId] = useState(report.templateId || 'doc-report')
  const [templateSaving, setTemplateSaving] = useState(false)
  const [visualStyle, setVisualStyle] = useState(report.design?.visualStyle ?? 'editorial')
  const [documentAccent, setDocumentAccent] = useState(report.design?.accent ?? '#5b5bd6')

  /** Writes the 서식 onto the document, so the exported file carries it. */
  /**
   * Adding, removing and reordering sections.
   *
   * A report arrived with the outline the model chose and there was no way to
   * change it — not one control added a section, removed one, or moved one. The
   * only way to a different shape was asking for the whole report again, which
   * throws away every sentence already fixed by hand in the sections being
   * kept. The outline card cannot help either: it accepts or it does not.
   *
   * Saved as one PATCH of the whole document, checked against the server first,
   * so it is snapshotted and one click from undone like any other edit.
   */
  const restructureSections = async (next: ReportSection[], summary: string) => {
    setRestructuring(true)
    setSaveError(null)
    try {
      const latest = await artifactsApi.get(report.id).catch(() => null)
      const row = await artifactsApi.update(report.id, {
        data: documentBody({ ...report, sections: next }),
        summary,
        expectedVersion: latest?.version ?? report.version,
      })
      report.sections = next
      report.version = row.version
      setTick((n) => n + 1)
    } catch (err) {
      setSaveError(errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      setRestructuring(false)
    }
  }

  const addSection = (at: number) => {
    const blank: ReportSection = {
      id: `s${Date.now().toString(36)}`,
      heading: t('새 절'),
      level: 1,
      status: 'done',
      content: '',
    }
    const next = [...report.sections.slice(0, at), blank, ...report.sections.slice(at)]
    return restructureSections(next, t('{n}번째에 절 추가').replace('{n}', String(at + 1)))
  }

  const moveSection = (at: number, by: -1 | 1) => {
    const to = at + by
    if (to < 0 || to >= report.sections.length) return Promise.resolve()
    const next = [...report.sections]
    ;[next[at], next[to]] = [next[to], next[at]]
    return restructureSections(next, t('{n}번째 절 옮김').replace('{n}', String(at + 1)))
  }

  const duplicateSection = (at: number) => {
    const source = report.sections[at]
    const copy: ReportSection = {
      ...source,
      id: `s${Date.now().toString(36)}`,
      heading: t('{name} 사본').replace('{name}', source.heading || t('제목 없음')),
      // 검토 결과는 원문의 특정 문장을 가리킨다. 본문은 복제하되 그 판정까지
      // 새 절에 붙이면 수정 전 판정이 새 내용에도 유효하다고 오해하게 된다.
      factCheck: undefined,
    }
    const next = [
      ...report.sections.slice(0, at + 1),
      copy,
      ...report.sections.slice(at + 1),
    ]
    return restructureSections(next, t('{n}번째 절 복제').replace('{n}', String(at + 1)))
  }

  const removeSection = (at: number) => {
    // The last one is not removable: a report with no sections is a title and
    // nothing else, and the way to be rid of it is to delete the report.
    if (report.sections.length <= 1) {
      setSaveError(t('마지막 한 절은 지울 수 없습니다. 보고서 자체를 지우려면 결과물 목록에서 지우세요.'))
      return Promise.resolve()
    }
    const next = report.sections.filter((_, i) => i !== at)
    return restructureSections(next, t('{n}번째 절 지움').replace('{n}', String(at + 1)))
  }

  const chooseTemplate = async (id: string) => {
    if (id === templateId) return
    const was = templateId
    setTemplateId(id)
    setTemplateSaving(true)
    setSaveError(null)
    try {
      const latest = await artifactsApi.get(report.id).catch(() => null)
      const row = await artifactsApi.update(report.id, {
        data: documentBody({ ...report, templateId: id }),
        summary: t('서식 바꾸기'),
        expectedVersion: latest?.version ?? report.version,
      })
      report.templateId = id
      report.version = row.version
    } catch (err) {
      setTemplateId(was)
      setSaveError(errorMessage(err, t('서식을 바꾸지 못했습니다.')))
    } finally {
      setTemplateSaving(false)
    }
  }

  const chooseVisualStyle = async (next: NonNullable<ReportArtifact['design']>['visualStyle']) => {
    if (!next || next === visualStyle) return
    setTemplateSaving(true)
    setSaveError(null)
    try {
      const latest = await artifactsApi.get(report.id)
      const current = report.design
      const design = {
        accent: current?.accent ?? '#5b5bd6', ink: current?.ink ?? '#1a1a1a',
        muted: current?.muted ?? '#666666', font: current?.font ?? 'serif' as const,
        ...(current?.footer ? { footer: current.footer } : {}),
        ...(current?.logo ? { logo: current.logo } : {}), visualStyle: next,
      }
      const row = await artifactsApi.update(report.id, {
        data: documentBody({ ...report, design }), summary: t('문서 디자인 변경'), expectedVersion: latest.version,
      })
      report.design = design
      report.version = row.version
      setVisualStyle(next)
      setView('page')
      setTick((value) => value + 1)
    } catch (err) {
      setSaveError(errorMessage(err, t('디자인을 바꾸지 못했습니다.')))
    } finally {
      setTemplateSaving(false)
    }
  }

  const chooseDocumentAccent = async (accent: string) => {
    if (accent === documentAccent) return
    setTemplateSaving(true)
    setSaveError(null)
    try {
      const latest = await artifactsApi.get(report.id)
      const current = report.design
      const design = {
        accent, ink: current?.ink ?? '#1a1a1a', muted: current?.muted ?? '#666666',
        font: current?.font ?? 'serif' as const, visualStyle,
        ...(current?.footer ? { footer: current.footer } : {}),
        ...(current?.logo ? { logo: current.logo } : {}),
      }
      const row = await artifactsApi.update(report.id, { data: documentBody({ ...report, design }), summary: t('문서 색 변경'), expectedVersion: latest.version })
      report.design = design
      report.version = row.version
      setDocumentAccent(accent)
      setView('page')
      setTick((value) => value + 1)
    } catch (err) {
      setSaveError(errorMessage(err, t('색을 바꾸지 못했습니다.')))
    } finally {
      setTemplateSaving(false)
    }
  }
  /**
   * The 서식 this panel may offer.
   *
   * Filtered in a `useMemo`, not in the selector. A zustand selector runs on
   * every store read and is compared by identity, so one that builds a new
   * array each time never matches its previous snapshot — React re-renders,
   * reads again, gets another new array, and the loop only ends as the
   * "Maximum update depth exceeded" screen. The rest of this app reads the
   * store whole for exactly this reason.
   */
  const { designTemplates, ensureDesignTemplates, send } = useStore()
  //: The report surface never runs `loadWorkspace`, so nothing else fetches
  //: the catalogue this picker offers. Asked for here, where it is needed.
  useEffect(() => {
    void ensureDesignTemplates()
  }, [ensureDesignTemplates])
  const documentTemplates = useMemo(
    () => designTemplates.filter((row) => row.kind === 'document'),
    [designTemplates],
  )
  //: Sections the document editor has changed but nobody has saved yet.
  const [pageEdits, setPageEdits] = useState<ReportSection[] | null>(null)
  //: And the title, when that is what was retyped. Held apart because it goes
  //: back as the artifact's own title rather than as part of its data.
  const [pageTitle, setPageTitle] = useState<string | null>(null)
  const [pageSettingsEdits, setPageSettingsEdits] = useState<ReportArtifact['pageSettings'] | null>(null)
  const [reviewCommentEdits, setReviewCommentEdits] = useState<ReportArtifact['reviewComments'] | null>(null)
  const [pageSaving, setPageSaving] = useState(false)
  const pageSnapshot = (title: string, data: Partial<ReportArtifact>) => JSON.stringify({
    title,
    sections: data.sections ?? [],
    pageSettings: data.pageSettings ?? null,
    reviewComments: data.reviewComments ?? [],
  })
  const pageBaseline = useRef(pageSnapshot(report.title, report))
  const hasUnsavedEdit = (editing && draft !== baseline.current) || Boolean(pageEdits || pageTitle || pageSettingsEdits || reviewCommentEdits)
  useEffect(() => {
    onDirtyChange?.(hasUnsavedEdit)
    return () => onDirtyChange?.(false)
  }, [hasUnsavedEdit, onDirtyChange])
  useEffect(() => {
    if (!hasUnsavedEdit) return
    const protect = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', protect)
    return () => window.removeEventListener('beforeunload', protect)
  }, [hasUnsavedEdit])
  useEffect(() => {
    if (!pageEdits && !pageTitle && !pageSettingsEdits && !reviewCommentEdits) {
      pageBaseline.current = pageSnapshot(report.title, report)
    }
  }, [report.title, report.sections, report.pageSettings, report.reviewComments, pageEdits, pageTitle, pageSettingsEdits, reviewCommentEdits])
  /**
   * 저장하지 않은 편집은 길을 막는 이유가 되지 않는다.
   *
   * Five controls carried `disabled={hasUnsavedEdit}`, so a single keystroke
   * in the page editor greyed out the view toggle, the design menu, the
   * template menu and 내보내기 at once — with nothing on any of them saying
   * why, and the 저장 that would free them in a different row. None of these
   * is destructive; each one simply needs the pending text committed before
   * it reads the document. So commit it, then act.
   */
  const afterSaving = async (act: () => void | Promise<void>) => {
    if (pageEdits || pageTitle || pageSettingsEdits || reviewCommentEdits) await savePageEdits()
    else if (editing && draft !== baseline.current) await saveDocument()
    await act()
  }

  const discardOr = (action: 'cancel' | 'close') => {
    if (hasUnsavedEdit) return setDiscardAction(action)
    if (action === 'close') onClose?.()
    else openEditor(false)
  }

  /**
   * The page view's save. Separate from `saveDocument` because that one round-
   * trips through Markdown — which is exactly what a formatted section cannot
   * survive.
   */
  const savePageEdits = async () => {
    if (!pageEdits && !pageTitle && !pageSettingsEdits && !reviewCommentEdits) return
    setPageSaving(true)
    setSaveError(null)
    try {
      const sections = pageEdits ?? report.sections
      const title = pageTitle ?? report.title
      const latest = await artifactsApi.get(report.id).catch(() => null)
      const latestData = (latest?.data ?? null) as Partial<ReportArtifact> | null
      if (latest && latestData && pageSnapshot(latest.title, latestData) !== pageBaseline.current) {
        setSaveError(
          t('이 보고서는 다른 곳에서 이미 수정되었습니다. 새로고침해 최신 내용을 받은 뒤 다시 저장하세요.'),
        )
        return
      }
      const row = await artifactsApi.update(report.id, {
        data: documentBody({
          ...report, title, sections,
          ...(pageSettingsEdits ? { pageSettings: pageSettingsEdits } : {}),
          ...(reviewCommentEdits ? { reviewComments: reviewCommentEdits } : {}),
        }),
        title,
        summary: t('서식 편집'),
        expectedVersion: latest?.version ?? report.version,
      })
      report.sections = sections
      report.title = title
      report.version = row.version
      if (pageSettingsEdits) report.pageSettings = pageSettingsEdits
      if (reviewCommentEdits) report.reviewComments = reviewCommentEdits
      pageBaseline.current = pageSnapshot(title, {
        ...report,
        sections,
        ...(pageSettingsEdits ? { pageSettings: pageSettingsEdits } : {}),
        ...(reviewCommentEdits ? { reviewComments: reviewCommentEdits } : {}),
      })
      setPageEdits(null)
      setPageTitle(null)
      setPageSettingsEdits(null)
      setReviewCommentEdits(null)
    } catch (err) {
      setSaveError(errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      setPageSaving(false)
    }
  }

  const copyPageRecovery = async () => {
    await copyText(toMarkdown({
      title: pageTitle ?? report.title,
      sections: pageEdits ?? report.sections,
    }))
    setRecoveryCopied(true)
    window.setTimeout(() => setRecoveryCopied(false), 1800)
  }

  const reloadLatestPage = async () => {
    setPageSaving(true)
    try {
      const latest = await artifactsApi.get(report.id)
      const data = latest.data as Partial<ReportArtifact>
      report.title = latest.title
      report.version = latest.version
      report.sections = data.sections ?? []
      report.pageSettings = data.pageSettings
      report.reviewComments = data.reviewComments
      pageBaseline.current = pageSnapshot(latest.title, data)
      setPageEdits(null)
      setPageTitle(null)
      setPageSettingsEdits(null)
      setReviewCommentEdits(null)
      setSaveError(null)
      setTick((value) => value + 1)
    } catch (err) {
      setSaveError(errorMessage(err, t('최신 내용을 불러오지 못했습니다.')))
    } finally {
      setPageSaving(false)
    }
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
        data: documentBody({ ...report, title, sections: parsed.sections }),
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

  const copyDocumentRecovery = async () => {
    await copyText(draft)
    setRecoveryCopied(true)
    window.setTimeout(() => setRecoveryCopied(false), 1800)
  }

  const reloadLatestDocument = async () => {
    setSaving(true)
    try {
      const latest = await artifactsApi.get(report.id)
      const data = latest.data as Partial<ReportArtifact>
      const sections = data.sections ?? []
      report.title = latest.title
      report.sections = sections
      report.version = latest.version
      const current = toMarkdown({ title: latest.title, sections })
      baseline.current = current
      setDraft(current)
      setSaveError(null)
      setTick((value) => value + 1)
    } catch (err) {
      setSaveError(errorMessage(err, t('최신 내용을 불러오지 못했습니다.')))
    } finally {
      setSaving(false)
    }
  }
  //: Whether any section carries formatting Markdown cannot express. What it
  //: gates is the Markdown editor, not the document — see the 수정 button.
  const formatted = report.sections.some((s) => s.format === 'html')
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
    <div
      ref={panel.ref}
      className="relative flex h-full min-h-0"
      onKeyDown={(event) => {
        if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 's') return
        event.preventDefault()
        if (!hasUnsavedEdit) return
        if (editing) void saveDocument()
        else void savePageEdits()
      }}
    >
      <Modal
        open={addingSource}
        onClose={() => setAddingSource(false)}
        title={t('참고 자료 추가')}
        description={t('보고서에서 실제로 확인한 원문의 정보를 입력하세요. 추가한 뒤 본문에 표시된 번호를 붙이면 사용 위치도 연결됩니다.')}
        footer={
          <>
            <Button onClick={() => setAddingSource(false)} disabled={citationSaving}>{t('취소')}</Button>
            <Button
              variant="primary"
              disabled={
                citationSaving ||
                !sourceDraft.title.trim() ||
                !/^https?:\/\//i.test(sourceDraft.url.trim())
              }
              onClick={() => void addSource()}
            >
              {citationSaving ? t('저장 중…') : t('추가')}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <label className="block text-sm text-muted">
            {t('자료 제목')}
            <Input
              value={sourceDraft.title}
              onChange={(event) => setSourceDraft((draft) => ({ ...draft, title: event.target.value }))}
              placeholder={t('원문에 표시된 제목')}
              autoFocus
            />
          </label>
          <label className="block text-sm text-muted">
            {t('원문 주소')}
            <Input
              type="url"
              value={sourceDraft.url}
              onChange={(event) => setSourceDraft((draft) => ({ ...draft, url: event.target.value }))}
              placeholder="https://"
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-3">
            {([
              ['author', t('저자')],
              ['publisher', t('발행처')],
              ['year', t('연도')],
            ] as const).map(([key, label]) => (
              <label key={key} className="block text-sm text-muted">
                {label}
                <Input
                  value={sourceDraft[key]}
                  onChange={(event) => setSourceDraft((draft) => ({ ...draft, [key]: event.target.value }))}
                />
              </label>
            ))}
          </div>
        </div>
      </Modal>
      {/* Mounted with the panel, not on the print click: `window.print()` is
          synchronous, so a tree created in that handler is not on screen when
          the browser takes its snapshot. */}
      <PrintDocument report={report} />
      {tocOpen && (
        <button
          aria-label={t('목차 닫기')}
          className="absolute inset-0 z-10 bg-black/30"
          onClick={() => setTocOpen(false)}
        />
      )}

      {/* 목차 — 서랍이다. 늘 서 있는 칸이 아니다.
          다섯 줄짜리 목차가 208px 를 세로로 다 쓰고 있었고, 그 폭은 문서에서
          나온 것이다. 사람이 문서를 보러 와서 문서가 세 번째로 좁은 칸에 있는
          이유가 그것이었다. 필요할 때 부르고, 고르면 닫힌다. */}
      <nav
        className={cn(
          'w-52 shrink-0 flex-col border-r border-line bg-panel',
          tocOpen ? 'absolute inset-y-0 left-0 z-20 flex shadow-overlay' : 'hidden',
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
              onClick={() => {
                if (s.status === 'pending') return
                scrollTo(s.id)
                setTocOpen(false)
              }}
              disabled={s.status === 'pending'}
              className={cn(
                'flex w-full items-start gap-2 rounded-control px-2 py-1.5 text-left text-sm transition-colors',
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
        <header className="relative z-40 flex flex-wrap items-center gap-2 border-b border-line bg-panel px-4 py-2.5 max-sm:px-2">
          <div className="flex min-w-0 flex-1 items-center gap-2 max-sm:basis-full">
            <FileText size={15} className="shrink-0 text-accent" />
            <p className="min-w-0 flex-1 truncate whitespace-nowrap text-base font-medium" title={report.title}>
              {report.title}
            </p>
          </div>
          <QuickAccess label={t('빠른 도구')}>
            {editing && <>
              <Button size="sm" variant="ghost" disabled={saving} onClick={() => discardOr('cancel')} aria-label={t('편집 취소')}>
                <X size={13} />{t('취소')}
              </Button>
              <Button variant="primary" size="sm" disabled={saving} onClick={() => void saveDocument()} aria-label={t('저장')} aria-keyshortcuts="Control+S Meta+S">
                {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}{t('저장')}
              </Button>
            </>}
            {view === 'page' && (pageEdits || pageTitle || pageSettingsEdits || reviewCommentEdits) && (
              <Button size="sm" variant="primary" disabled={pageSaving} onClick={() => void savePageEdits()} aria-label={t('저장')} aria-keyshortcuts="Control+S Meta+S">
                {pageSaving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                {t('저장')}
              </Button>
            )}
            <PanelControls mode={mode} onCycle={onModeChange && cycleMode} onClose={() => discardOr('close')} />
          </QuickAccess>
          <ArtifactRibbon
            label={t('보고서 메뉴')}
            tabs={[
              { id: 'home', label: t('홈') }, { id: 'insert', label: t('삽입') },
              { id: 'layout', label: t('레이아웃') }, { id: 'review', label: t('검토') }, { id: 'view', label: t('보기') },
              { id: 'file', label: t('파일') },
            ] as const}
            active={ribbon}
            onChange={setRibbon}
          >
          {/* No `aria-label`: the words on the button are the name, and an
              `aria-label` of 목차 would replace them — announcing "목차" and
              swallowing the count that is the reason to look at it. */}
          {ribbon === 'view' && <RibbonGroup label={t('탐색')}><Button size="sm" onClick={() => setTocOpen((o) => !o)}>
            <ListTree size={13} />
            {t('목차')} {done}/{report.sections.length}
          </Button></RibbonGroup>}
          {/* `저장 시점 v3` 이 바로 옆에서 같은 숫자를 말한다. 둘 중 하나는
              읽는 사람에게 아무것도 더 주지 않으면서 줄 하나를 접히게 만든다. */}
          {ribbon === 'review' && <RibbonGroup label={t('문서 검사')}><LintFindings
            findings={report.lint}
            artifact={report}
            onFix={fixFinding}
            onFixAll={fixAllFindings}
          /></RibbonGroup>}
          {/* 편집 진입점. 항상 보이는 자리에 둔다 — hover 로만 드러나면 보고서가
              편집 가능하다는 것을 알아낼 방법이 마우스를 훑는 것뿐이 된다.

              페이지뷰에는 두지 않는다. 그쪽은 글을 눌러 바로 쓰는 자리이고,
              여기 있는 '수정' 은 마크다운 편집기를 여는 다른 것이다. 나란히
              두면 서식이 적용된 문서를 고치려고 누른 버튼이 마크다운 원문을
              띄우게 된다. */}
          {ribbon === 'home' && view !== 'page' && !editing && (
            <RibbonGroup label={t('편집')}>
            <Button
              size="sm"
              onClick={() => {
                setView('page')
                setDocumentLayout('edit')
                setPageSettingsOpen(false)
              }}
              disabled={writing}
              title={t('굵게·표·그림을 그대로 보면서 고칩니다')}
              aria-label={t('문서 수정')}
            >
              <Pencil size={13} />
              {t('문서 수정')}
            </Button>
            </RibbonGroup>
          )}
          {/* 웹뷰와 페이지뷰. 같은 문서를 두 가지로 볼 뿐이고, 어느 쪽에서
              고쳐도 같은 절에 저장된다. */}
          {ribbon === 'home' && <RibbonGroup label={t('보기')}><Button
            size="sm"
            variant={view === 'page' ? 'primary' : 'secondary'}
            aria-label={view === 'page' ? t('웹뷰') : t('페이지뷰')}
            /* 버튼이 무엇을 하는지는 버튼마다 다르다. The one title described
               the page view and stayed put when the button became 웹뷰, so
               half the time the tooltip explained the view you were leaving. */
            title={view === 'page' ? t('편집하기 좋은 한 줄 문서로 봅니다') : t('서식이 적용된 A4 문서로 봅니다')}
            onClick={() => {
              /**
               * 저장하고 넘어간다.
               *
               * This was `disabled={hasUnsavedEdit}`, so one keystroke in the
               * page editor greyed out the only way back to the web view —
               * with no message saying why, and the 저장 button that would
               * have freed it sitting in a different row. Switching view is
               * not a destructive act; it just needs the edits committed
               * first, which we can do without asking.
               */
              void afterSaving(() => {})
              const next = view === 'page' ? 'web' : 'page'
              setView(next)
              // A page is 794px wide and the panel is often less than half
              // that. Scaled it still fits, but a document scaled to 44% is a
              // document nobody can type in — so entering the page view asks
              // for the room a page needs. Leaving it gives the room back.
              if (onModeChange) {
                // A page is 794px wide, so entering the page view asks for the
                // room a page needs and leaving gives it back — without
                // overriding a reader who has already folded the chat away.
                const wanted: PanelMode =
                  next === 'page' ? (mode === 'narrow' ? 'wide' : mode) : 'narrow'
                setMode(wanted)
                onModeChange(wanted)
              }
            }}
          >
            <FileType2 size={13} />
            {view === 'page' ? t('웹뷰') : t('페이지뷰')}
          </Button>
          {view === 'page' && <Button
            size="sm"
            variant={documentLayout === 'edit' ? 'primary' : 'secondary'}
            aria-label={documentLayout === 'edit' ? t('실제 페이지') : t('내용 편집')}
            onClick={() => setDocumentLayout((current) => current === 'edit' ? 'pages' : 'edit')}
          >
            <Pencil size={13} />
            {documentLayout === 'edit' ? t('실제 페이지') : t('내용 편집')}
          </Button>}
          </RibbonGroup>}
          {ribbon === 'layout' && <RibbonGroup label={t('페이지')}><Button
            size="sm"
            variant={pageSettingsOpen ? 'primary' : 'secondary'}
            aria-label={t('페이지 설정')}
            aria-pressed={pageSettingsOpen}
            onClick={() => {
              setView('page')
              setDocumentLayout('pages')
              setPageSettingsOpen((open) => !open)
            }}
          >
            <FileType2 size={13} />{t('페이지 설정')}
          </Button></RibbonGroup>}
          {/* 마크다운 원문. 한 번에 훑어 고치거나 통째로 붙여 넣을 때의 길이고,
              그렇게 부르지 않으면 '수정' 이라는 이름으로 사람을 그리 보내게
              된다. 서식이 든 절은 이 길로 보내지 않는다 — 크기·서체·정렬·표가
              저장하는 순간 조용히 사라진다. */}
          {ribbon === 'home' && view !== 'page' && !formatted && (
            <RibbonGroup label={t('원문')}>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void startEditing()}
              disabled={writing}
              title={t('마크다운으로 한 번에 고칩니다')}
              aria-label={t('원문 편집')}
            >
              <Code2 size={13} />
              {t('원문')}
            </Button>
            </RibbonGroup>
          )}
          {/* 어떤 양식으로 낼지. 생성 때 한 번 고르고 끝이던 선택을 문서를
              쓰는 도중에도 바꿀 수 있게 한다. 화면은 달라지지 않는다 — 종이는
              하나다 — 달라지는 것은 내보낸 파일이다. 메뉴가 그렇게 말한다. */}
          {ribbon === 'home' && (
            <RibbonGroup label={t('인상')}>
              {/* 덱과 같은 모양으로 고른다.
                  같은 것을 고르는 자리가 한쪽은 버튼 셋이고 한쪽은 메뉴
                  하나면, 두 화면은 한 제품으로 읽히지 않는다. 셋뿐이고
                  누르면 바로 보이는 것이므로 접어 둘 이유가 없다. */}
              {([
                ['editorial', '편집형', '선명한 절 구분'],
                ['poster', '매거진형', '색면 표지와 큰 제목'],
                ['minimal', '미니멀', '작은 제목과 넓은 여백'],
              ] as const).map(([value, label, why]) => (
                <Button
                  key={value}
                  size="sm"
                  disabled={templateSaving}
                  aria-pressed={visualStyle === value}
                  title={t(why)}
                  onClick={() => void afterSaving(() => chooseVisualStyle(value))}
                >
                  {t(label)}
                </Button>
              ))}
            </RibbonGroup>
          )}
          {ribbon === 'home' && (
            <RibbonGroup label={t('색')}><Dropdown
              trigger={() => (
                <Button size="sm" variant="secondary" disabled={templateSaving} aria-label={t('보고서 색 고르기')}>
                  <Palette size={13} />
                  <span className="size-3 rounded-full ring-1 ring-black/10" style={{ backgroundColor: documentAccent }} />
                </Button>
              )}
            >
              <MenuLabel>{t('색 구성')}</MenuLabel>
              {([
                ['#5b5bd6', '보라'], ['#1f6feb', '파랑'], ['#0f766e', '청록'],
                ['#c2410c', '주황'], ['#b91c1c', '빨강'], ['#334155', '먹색'],
              ] as const).map(([colour, label]) => (
                <MenuItem key={colour} icon={<span className="size-3 rounded-full ring-1 ring-black/10" style={{ backgroundColor: colour }} />} checked={documentAccent.toLowerCase() === colour} onClick={() => void chooseDocumentAccent(colour)}>{t(label)}</MenuItem>
              ))}
            </Dropdown></RibbonGroup>
          )}
          {ribbon === 'home' && view === 'page' && (
            <RibbonGroup label={t('양식')}><Dropdown
              trigger={() => (
                <Button size="sm" variant="secondary" disabled={templateSaving} onClick={() => void afterSaving(() => {})}>
                  {templateSaving && <Loader2 size={13} className="animate-spin" />}
                  {documentTemplates.find((row) => row.id === templateId)?.name ?? t('서식')}
                </Button>
              )}
            >
              <MenuLabel>{t('내보내면 이 양식으로 나갑니다')}</MenuLabel>
              {documentTemplates.map((row) => (
                <MenuItem
                  key={row.id}
                  checked={row.id === templateId}
                  onClick={() => void chooseTemplate(row.id)}
                >
                  {row.name}
                </MenuItem>
              ))}
            </Dropdown></RibbonGroup>
          )}
          {ribbon === 'review' && <RibbonGroup label={t('근거')}><Button
            size="sm"
            variant={pane === 'sources' ? 'primary' : 'secondary'}
            aria-label={
              evidenceWarningCount > 0
                ? t('출처 {sources} · 확인 {count}')
                    .replace('{sources}', String(report.sources.length))
                    .replace('{count}', String(evidenceWarningCount))
                : t('출처 {sources}').replace('{sources}', String(report.sources.length))
            }
            onClick={() => setPane((p) => (p === 'sources' ? 'document' : 'sources'))}
          >
            <Quote size={13} />
            {t('출처')} {report.sources.length}
            {evidenceWarningCount > 0 && (
              <span className="text-danger">
                · {t('확인 {count}').replace('{count}', String(evidenceWarningCount))}
              </span>
            )}
          </Button></RibbonGroup>}
          {/* 서식. 편집기가 열려 있을 때만 채워진다 — 편집기가 자기 줄을
              따로 그리는 대신 이 자리로 보낸다. 홈 탭의 마지막에 두는 이유는
              이것만 폭이 스무 개 버튼만큼이기 때문이다: 앞에 두면 웹뷰도
              디자인도 양식도 리본 오른쪽 밖으로 밀려난다. */}
          {ribbon === 'home' && view === 'page' && documentLayout === 'edit' && (
            <RibbonGroup label={t('서식')}>
              <div ref={setToolbarSlot} className="flex items-center" />
            </RibbonGroup>
          )}
          {/* 저장 시점. 되돌릴 수 있다는 사실이 편집 버튼 옆에 붙어 있어야,
              고치기 전에 "잘못 고치면 어쩌지" 를 묻지 않는다. */}
          {ribbon === 'review' && <RibbonGroup label={t('버전')}><VersionHistory
            artifact={report}
            hasUnsavedChanges={hasUnsavedEdit}
            currentData={report}
            // 되돌린 뒤에도 열려 있는 편집기는 되돌리기 이전의 글을 들고 있다.
            // 그대로 저장하면 방금 되돌린 일이 취소된다.
            onRestored={() => {
              openEditor(false)
              setPageEdits(null)
              setPageTitle(null)
              setPageSettingsEdits(null)
              setReviewCommentEdits(null)
              setSaveError(null)
            }}
          /></RibbonGroup>}
          {ribbon === 'insert' && <RibbonGroup label={t('그림')}><AddSectionImage report={report} /></RibbonGroup>}
          {ribbon === 'file' && <RibbonGroup label={t('내보내기')}><Dropdown
            align="right"
            trigger={() => (
              <Button size="sm" onClick={() => void afterSaving(() => {})}>
                <Download size={14} />
                {t('내보내기')}
              </Button>
            )}
          >
            {evidenceWarningCount > 0 && (
              <>
                <MenuLabel>
                  {t('내보내기 전 근거 확인 {count}건').replace(
                    '{count}',
                    String(evidenceWarningCount),
                  )}
                </MenuLabel>
                <MenuItem icon={<TriangleAlert size={14} />} onClick={() => setPane('sources')}>
                  {t('먼저 근거 확인')}
                </MenuItem>
              </>
            )}
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
            {/* 인쇄도 내보내기다. 자기 단추를 하나 갖고 도구줄을 한 줄 더
                접히게 할 만큼 다른 일은 아니다. */}
            <MenuItem icon={<Printer size={14} />} onClick={() => window.print()}>
              {t('인쇄')}
            </MenuItem>
          </Dropdown></RibbonGroup>}
          </ArtifactRibbon>
        </header>

        <div
          ref={docRef}
          className="relative min-h-0 flex-1 overflow-y-auto"
          onMouseUp={readSelection}
        >
          {picked && (
            <div
              ref={handleRef}
              className="animate-fade-up absolute z-30 flex items-center gap-1 rounded-card border border-line bg-panel p-1 shadow-overlay"
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
              <div className="mb-3 flex items-center justify-between gap-3">
                <h2 className="text-lg font-semibold">{t('참고문헌')}</h2>
                <Button size="sm" onClick={() => setAddingSource(true)}>
                  <ListPlus size={13} />
                  {t('자료 추가')}
                </Button>
              </div>
              <SourceList
                sources={report.sources}
                research={report.research}
                style={report.citationStyle}
                onStyle={(style) => void chooseCitationStyle(style)}
                sections={report.sections}
                onJump={(sectionId) => {
                  setPane('document')
                  requestAnimationFrame(() => scrollTo(sectionId))
                }}
                onRemove={(source) => {
                  void saveSources(
                    report.sources.filter((candidate) => candidate.id !== source.id),
                    t('미사용 참고 자료 삭제'),
                  )
                }}
                saving={citationSaving}
              />
              {citationError && <p className="mt-3 text-sm text-danger">{citationError}</p>}
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
                    className="min-h-0 w-full flex-1 resize-none rounded-card border border-line bg-panel px-3 py-2 font-mono text-base leading-relaxed outline-none focus:border-accent"
                    autoFocus
                  />
                </div>
                <div className="flex min-h-0 flex-col gap-1">
                  <p className="text-xs font-medium text-faint">{t('미리보기')}</p>
                  <div
                    aria-label={t('문서 미리보기')}
                    className="min-h-[10rem] flex-1 overflow-auto rounded-card border border-line bg-elevated px-4 py-3"
                  >
                    {draft.trim() ? (
                      <Markdown>{draft}</Markdown>
                    ) : (
                      <p className="text-base text-faint">{t('내용을 입력하면 여기에 결과가 보입니다.')}</p>
                    )}
                  </div>
                </div>
              </div>
              {saveError && (
                <div role="alert" className="rounded-card border border-danger/30 bg-panel px-3 py-2">
                  <p className="text-base text-danger">{saveError}</p>
                  {saveError.includes(t('다른 곳에서 이미 수정')) && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      <Button size="sm" variant="secondary" onClick={() => void copyDocumentRecovery()}>
                        <Copy size={13} />
                        {recoveryCopied ? t('복사됨') : t('내 편집 내용 복사')}
                      </Button>
                      <Button size="sm" variant="primary" disabled={saving} onClick={() => void reloadLatestDocument()}>
                        <RefreshCw size={13} />
                        {t('최신본 불러오기')}
                      </Button>
                    </div>
                  )}
                </div>
              )}
              <p className="text-xs text-faint">
                {t('⌘/Ctrl+Enter 저장 · Esc 취소 · 저장하면 이전 판은 버전 기록에 남습니다')}
              </p>
            </div>
          ) : (
          view === 'page' ? (
            <div className="relative min-h-0 flex-1">
              <DocumentEditor
                key={`${report.id}-${report.version}`}
                report={report}
                templateId={templateId}
                tokens={report.design ?? null}
                editable={!writing}
                layoutMode={documentLayout}
                settingsOpen={pageSettingsOpen}
                onLayoutMode={setDocumentLayout}
                onWebView={() => setView('web')}
                toolbarSlot={ribbon === 'home' ? toolbarSlot : null}
                onDirty={(sections, title, pageSettings, reviewComments) => {
                  setPageEdits(sections)
                  if (title !== undefined) setPageTitle(title)
                  if (pageSettings !== undefined) setPageSettingsEdits(pageSettings)
                  if (reviewComments !== undefined) setReviewCommentEdits(reviewComments)
                }}
              />
              {saveError && (
                <div role="alert" className="absolute inset-x-4 bottom-4 z-20 rounded-card border border-danger/30 bg-panel px-4 py-3 shadow-lg">
                  <p className="text-base text-danger">{saveError}</p>
                  {saveError.includes(t('다른 곳에서 이미 수정')) && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      <Button size="sm" variant="secondary" onClick={() => void copyPageRecovery()}>
                        <Copy size={13} />
                        {recoveryCopied ? t('복사됨') : t('내 편집 내용 복사')}
                      </Button>
                      <Button size="sm" variant="primary" disabled={pageSaving} onClick={() => void reloadLatestPage()}>
                        <RefreshCw size={13} />
                        {t('최신본 불러오기')}
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
          <article className="mx-auto max-w-2xl px-6 py-6">
            <h1 className="mb-6 text-2xl font-semibold tracking-tight">{report.title}</h1>
            {report.sections.map((s, sectionIndex) => (
              <section key={s.id} id={`sec-${s.id}`} className="mb-8 scroll-mt-4">
                <div className="group/sec mb-2 flex items-center gap-2">
                  <h2
                    className={cn('text-lg font-semibold', s.status === 'pending' && 'text-faint')}
                  >
                    {s.heading}
                  </h2>
                  {!editing && (
                    // Opens rightward: this button sits at the left edge of the
                    // document column, and a menu anchored by its right edge
                    // hangs off the panel and is clipped.
                    <Dropdown
                      align="left"
                      trigger={() => (
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-label={t('{name} 절 편집').replace('{name}', s.heading)}
                          disabled={restructuring}
                        >
                          <ListPlus size={12} />
                          {t('절 편집')}
                        </Button>
                      )}
                    >
                      <MenuLabel>{s.heading || t('제목 없음')}</MenuLabel>
                      <MenuItem onClick={() => void addSection(sectionIndex)}>
                        {t('앞에 절 추가')}
                      </MenuItem>
                      <MenuItem onClick={() => void addSection(sectionIndex + 1)}>
                        {t('뒤에 절 추가')}
                      </MenuItem>
                      <MenuItem onClick={() => void duplicateSection(sectionIndex)}>
                        {t('이 절 복제')}
                      </MenuItem>
                      <MenuItem
                        onClick={() => void moveSection(sectionIndex, -1)}
                        disabled={sectionIndex === 0}
                      >
                        {t('위로 옮기기')}
                      </MenuItem>
                      <MenuItem
                        onClick={() => void moveSection(sectionIndex, 1)}
                        disabled={sectionIndex >= report.sections.length - 1}
                      >
                        {t('아래로 옮기기')}
                      </MenuItem>
                      <MenuItem onClick={() => void removeSection(sectionIndex)}>
                        {t('이 절 지우기')}
                      </MenuItem>
                      {s.status === 'done' && (
                        <>
                          <MenuLabel>{t('이 절에 대해')}</MenuLabel>
                          {/* 절 하나만 다시 쓴다. 지도교수 피드백이 넷째 절에
                              오면 나머지 다섯을 다시 쓰는 것은 아무도 원하지
                              않는다. */}
                          <MenuItem
                            icon={<RefreshCw size={13} />}
                            onClick={() => {
                              setRewriting(s.id)
                              setRewriteNote('')
                              setRewriteQuote('')
                            }}
                          >
                            {t('이 절만 다시 쓰기')}
                          </MenuItem>
                          {/* 수치가 틀린 보고서는 슬라이드보다 멀리 간다.
                              발표는 그 방에서 반박당하지만 보고서는 내보내져
                              메일에 붙는다. */}
                          <MenuItem
                            icon={<ShieldQuestion size={13} />}
                            disabled={checking === s.id}
                            onClick={() => void factcheckSection(s.id)}
                          >
                            {s.factCheck ? t('다시 검토') : t('검토')}
                          </MenuItem>
                        </>
                      )}
                    </Dropdown>
                  )}
                  {/* 다시 쓰기와 검토는 위 메뉴 안에 있다.
                      셋이 한 줄에 있었는데 하나만 늘 보이고 둘은 절 위에
                      마우스를 올려야 나타났다 — 같은 줄에서 규칙이 엇갈리면,
                      보이지 않는 쪽은 없는 것이 된다. 절 제목 옆에 늘 보이는
                      손잡이 하나를 두고, 그 절에 할 수 있는 일을 그 안에 모은다.
                      진행 중 표시는 남는다: 무엇이 돌고 있는지는 메뉴를 열지
                      않고도 보여야 한다. */}
                  {checking === s.id && (
                    <Loader2 size={12} className="shrink-0 animate-spin text-muted" />
                  )}
                  {/* 확인이 필요한 주장이 있으면 절을 접어 두어도 보이게. */}
                  {s.factCheck?.claims.some((c) => c.verdict !== 'supported') && (
                    <TriangleAlert size={12} className="shrink-0 text-warn" />
                  )}
                </div>
                {rewriting === s.id && (
                  <div className="mb-3 space-y-2 rounded-card border border-line bg-elevated p-3">
                    {/* 고칠 대목을 먼저 보여 준다. 지시만 남으면 무엇에 대한
                        지시였는지는 보낸 사람 머릿속에만 있다. */}
                    {rewriteQuote && (
                      <div
                        aria-label={t('고칠 대목')}
                        className="flex items-start gap-2 rounded-control border border-accent/25 bg-accent-soft/60 px-2.5 py-1.5"
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
                    <SectionBody
                      section={s}
                      owner={{ artifactId: report.id, sectionId: s.id }}
                    />
                    {s.factCheck?.status === 'done' && (
                      <FactCheckResults
                        check={s.factCheck}
                        onFix={(claim) => void fixClaim(s, claim)}
                      />
                    )}
                    {checkError && checking === null && s.factCheck === undefined && (
                      <p className="mt-2 text-sm text-danger">{checkError}</p>
                    )}
                    {s.status === 'streaming' && (
                      <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-blink bg-accent" />
                    )}
                  </>
                )}
              </section>
            ))}
          </article>
          )
          )}
        </div>
      </div>
      <ConfirmDialog
        open={discardAction !== null}
        onClose={() => setDiscardAction(null)}
        title={t('저장하지 않은 변경 내용이 있습니다')}
        description={t('계속하면 보고서에서 바꾼 내용이 사라집니다.')}
        confirmLabel={discardAction === 'close' ? t('저장하지 않고 닫기') : t('변경 내용 버리기')}
        onConfirm={() => {
          const action = discardAction
          openEditor(false)
          setPageEdits(null)
          setPageTitle(null)
          setPageSettingsEdits(null)
          setReviewCommentEdits(null)
          if (action === 'close') onClose?.()
        }}
      />
    </div>
  )
}
