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
import { usePanelNarrow } from '@/lib/usePanelNarrow'
import { Button, Dropdown, MenuItem, MenuLabel, Modal, Textarea } from '@/components/ui'
import { artifactsApi, downloadArtifact as download, errorMessage } from '@/lib/api'
import { fromMarkdown, toMarkdown } from '@/lib/reportMarkdown'
import { cn, formatTokens } from '@/lib/utils'
import type { LintFinding, ReportArtifact, ReportSection, Source } from '@/types'
import { copyText } from '@/lib/clipboard'
import { DocumentEditor } from '@/components/report/DocumentEditor'
import { SectionBody } from '@/components/report/SectionBody'
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
      <div className="rounded-card border border-dashed border-line px-4 py-8 text-center">
        <p className="text-base text-muted">{t('참고한 자료가 없습니다.')}</p>
        <p className="mt-1 text-sm text-faint">
          {t('웹 검색을 켜고 다시 쓰면 찾은 자료가 여기 출처로 붙습니다. 검색 없이 쓴 글에는 붙일 출처가 없습니다.')}
        </p>
      </div>
    )
  }
  return (
    <div className="space-y-2">
      <p className="text-xs text-faint">
        {t('{style} 형식 · {n}건').replace('{style}', style).replace('{n}', String(sources.length))}
      </p>
      {sources.map((src) => {
        const Icon = originIcon[src.origin]
        return (
          <div key={src.id} className="rounded-card border border-line bg-panel p-3">
            <div className="flex items-start gap-2">
              <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-control bg-accent-soft text-2xs font-semibold text-accent">
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
}: {
  report: ReportArtifact
  onClose?: () => void
  /** How much room the document is asking for. The host owns whether there is
   *  any: the same report sits in a resizable side panel on one screen and in
   *  a fixed-width preview dialog on another. */
  onModeChange?: (mode: PanelMode) => void
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
  const [pane, setPane] = useState<'document' | 'sources'>('document')
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
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
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
  const [pageSaving, setPageSaving] = useState(false)

  /**
   * The page view's save. Separate from `saveDocument` because that one round-
   * trips through Markdown — which is exactly what a formatted section cannot
   * survive.
   */
  const savePageEdits = async () => {
    if (!pageEdits && !pageTitle) return
    setPageSaving(true)
    setSaveError(null)
    try {
      const sections = pageEdits ?? report.sections
      const title = pageTitle ?? report.title
      const latest = await artifactsApi.get(report.id).catch(() => null)
      const row = await artifactsApi.update(report.id, {
        data: documentBody({ ...report, title, sections }),
        title,
        summary: t('서식 편집'),
        expectedVersion: latest?.version ?? report.version,
      })
      report.sections = sections
      report.title = title
      report.version = row.version
      setPageEdits(null)
      setPageTitle(null)
    } catch (err) {
      setSaveError(errorMessage(err, t('저장하지 못했습니다.')))
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
    <div ref={panel.ref} className="relative flex h-full min-h-0">
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
        <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
          <FileText size={15} className="shrink-0 text-accent" />
          <p className="min-w-0 flex-1 truncate text-base font-medium max-sm:basis-full">
            {report.title}
          </p>
          {/* No `aria-label`: the words on the button are the name, and an
              `aria-label` of 목차 would replace them — announcing "목차" and
              swallowing the count that is the reason to look at it. */}
          <Button size="sm" onClick={() => setTocOpen((o) => !o)}>
            <ListTree size={13} />
            {t('목차')} {done}/{report.sections.length}
          </Button>
          {/* `저장 시점 v3` 이 바로 옆에서 같은 숫자를 말한다. 둘 중 하나는
              읽는 사람에게 아무것도 더 주지 않으면서 줄 하나를 접히게 만든다. */}
          <LintFindings
            findings={report.lint}
            artifact={report}
            onFix={fixFinding}
            onFixAll={fixAllFindings}
          />
          {/* 편집 진입점. 항상 보이는 자리에 둔다 — hover 로만 드러나면 보고서가
              편집 가능하다는 것을 알아낼 방법이 마우스를 훑는 것뿐이 된다.

              페이지뷰에는 두지 않는다. 그쪽은 글을 눌러 바로 쓰는 자리이고,
              여기 있는 '수정' 은 마크다운 편집기를 여는 다른 것이다. 나란히
              두면 서식이 적용된 문서를 고치려고 누른 버튼이 마크다운 원문을
              띄우게 된다. */}
          {view === 'page' ? null : editing ? (
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
            /* 수정은 언제나 문서를 고치는 곳으로 데려간다.

               이 버튼은 서식이 든 문서만 페이지뷰로 보내고, 그렇지 않은 —
               즉 갓 만들어진 모든 — 보고서는 마크다운 원문 편집기로 보냈다.
               표를 고치려던 사람이 `| --- | --- |` 를 마주하는 자리가 거기다.
               보고서를 쓰러 온 사람이 마크다운 표 문법을 배우러 온 것은
               아니고, 정작 굵게·표 넣기·실행 취소가 다 있는 진짜 편집기는
               '페이지뷰' 라는, 고치는 곳처럼 들리지 않는 이름 뒤에 있었다.

               원문 편집이 나쁜 것은 아니다. 통째로 붙여 넣거나 한 번에 훑어
               고칠 때는 그쪽이 빠르다. 그래서 없애지 않고 옆에 제 이름을
               달아 두었다 — 아래 '원문'. */
            <Button
              size="sm"
              onClick={() => setView('page')}
              disabled={writing}
              title={t('굵게·표·그림을 그대로 보면서 고칩니다')}
              aria-label={t('문서 수정')}
            >
              <Pencil size={13} />
              {t('수정')}
            </Button>
          )}
          {/* 웹뷰와 페이지뷰. 같은 문서를 두 가지로 볼 뿐이고, 어느 쪽에서
              고쳐도 같은 절에 저장된다. */}
          <Button
            size="sm"
            variant={view === 'page' ? 'primary' : 'secondary'}
            aria-label={t('페이지뷰')}
            title={t('서식이 적용된 A4 문서로 봅니다')}
            onClick={() => {
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
          {/* 마크다운 원문. 한 번에 훑어 고치거나 통째로 붙여 넣을 때의 길이고,
              그렇게 부르지 않으면 '수정' 이라는 이름으로 사람을 그리 보내게
              된다. 서식이 든 절은 이 길로 보내지 않는다 — 크기·서체·정렬·표가
              저장하는 순간 조용히 사라진다. */}
          {view !== 'page' && !formatted && (
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
          )}
          {/* 어떤 양식으로 낼지. 생성 때 한 번 고르고 끝이던 선택을 문서를
              쓰는 도중에도 바꿀 수 있게 한다. 화면은 달라지지 않는다 — 종이는
              하나다 — 달라지는 것은 내보낸 파일이다. 메뉴가 그렇게 말한다. */}
          {view === 'page' && (
            <Dropdown
              trigger={() => (
                <Button size="sm" variant="secondary" disabled={templateSaving}>
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
            </Dropdown>
          )}
          {view === 'page' && (pageEdits || pageTitle) && (
            <Button size="sm" variant="primary" disabled={pageSaving} onClick={() => void savePageEdits()}>
              {pageSaving && <Loader2 size={13} className="animate-spin" />}
              {t('저장')}
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
          <VersionHistory
            artifact={report}
            // 되돌린 뒤에도 열려 있는 편집기는 되돌리기 이전의 글을 들고 있다.
            // 그대로 저장하면 방금 되돌린 일이 취소된다.
            onRestored={() => openEditor(false)}
          />
          <PanelControls mode={mode} onCycle={onModeChange && cycleMode} />
          <AddSectionImage report={report} />
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
            {/* 인쇄도 내보내기다. 자기 단추를 하나 갖고 도구줄을 한 줄 더
                접히게 할 만큼 다른 일은 아니다. */}
            <MenuItem icon={<Printer size={14} />} onClick={() => window.print()}>
              {t('인쇄')}
            </MenuItem>
          </Dropdown>
          <PanelControls mode={mode} onClose={onClose} />
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
              {saveError && <p className="text-base text-danger">{saveError}</p>}
              <p className="text-xs text-faint">
                {t('⌘/Ctrl+Enter 저장 · Esc 취소 · 저장하면 이전 판은 버전 기록에 남습니다')}
              </p>
            </div>
          ) : (
          view === 'page' ? (
            <DocumentEditor
              report={report}
              templateId={templateId}
              tokens={report.design ?? null}
              editable={!writing}
              onDirty={(sections, title) => {
                setPageEdits(sections)
                if (title !== undefined) setPageTitle(title)
              }}
            />
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
    </div>
  )
}
