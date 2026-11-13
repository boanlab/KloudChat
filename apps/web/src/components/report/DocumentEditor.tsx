import { EditorContent, useEditor } from '@tiptap/react'
import { fontFamilyFor } from '@/components/report/docType'
import { FilePenLine, Globe2, Loader2, RefreshCw, ZoomIn, ZoomOut } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { DocumentShell } from '@/components/report/DocumentShell'
import { EditableLine } from '@/components/report/EditableLine'
import { TitleBlock } from '@/components/report/TitleBlock'
import { markFigureColumns } from '@/components/report/figureColumns'
import {
  cleanTitleBlock,
  closingIndex,
  counterStyle,
  fmtClass,
  formatCss,
  isAttachment,
  numberHtmlHeadings,
  sectionNumbers,
  shapedSection,
  titleBlockHtml,
} from '@/components/report/docFormats'
import { htmlOf, mermaidSources } from '@/components/report/reportHtml'
import { REPORT_EDITOR_CSS, REPORT_EXTENSIONS, setSectionLabels, textMatches, type SectionLabel } from '@/components/report/reportSchema'
import { ReportEditorRibbon, type EditorRibbonTab } from '@/components/report/ReportEditorRibbon'
import { artifactsApi } from '@/lib/api'
import { diagramKey } from '@/lib/diagramKey'
import { FRAMES, drawFitting, framed, rasterise, theme } from '@/lib/mermaid'
import { docToMarkdown, docToSections, markdownToDoc, sectionOutline, sectionsToDoc } from '@/lib/reportDoc'
import {
  A4_HEIGHT_PX,
  A4_WIDTH_PX,
  useSheet,
} from '@/components/report/usePagination'
import {
  designTemplatesApi,
  errorMessage,
  type DesignTokens,
  type TemplateStyle,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import type { ReportArtifact, ReportSection, ReportTitleBlock } from '@/types'
import { useT } from '@/lib/useT'
import { scopePagedStyles } from '@/components/report/scopePagedStyles'
import { DEFAULT_PAGE_SETTINGS, pagedSheetCss, type PageSettings } from '@/components/report/pagedSheet'
import { PLACEHOLDER_CSS, markPlaceholders } from '@/lib/placeholders'

const escapePagedText = (text: string) => text
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')


/** Scope for Paged.js rules; shared with `index.css`. */
const PAGED_SCOPE = 'paged-report-preview'

/** Continuous sheet with print geometry (A4, margins, cover, section spacing); seed `body` typography repeated, shadow root has no body. */
function sheetGeometryCss(margins: PageSettings['margins']): string {
  return `
  .page.paginated { box-sizing: border-box; width: ${A4_WIDTH_PX}px; max-width: ${A4_WIDTH_PX}px; margin: 0 auto; padding: ${margins.top}mm ${margins.right}mm ${margins.bottom}mm ${margins.left}mm; min-height: ${A4_HEIGHT_PX}px; }
  .page.paginated { color: var(--ink); font-family: var(--font-body); font-size: var(--doc-body); line-height: var(--doc-leading-body); }
  .page.paginated .cover { min-height: 0; padding: 0 0 4mm; margin: 0 0 8mm; }
  /* A cover page fills its sheet, and its margin carries the next block past the page gap. */
  .page.paginated :is(.cover.tb-cover, .cover.cover-page) { min-height: ${297 - margins.top - margins.bottom}mm; padding: 72mm 0 0; margin: 0 0 ${margins.top + margins.bottom}mm; }
  .page.paginated section { margin: 0 0 12mm; }
  /* The editor keeps a paragraph inside every cell; the finished file has bare text there. */
  .page.paginated td > p, .page.paginated th > p { margin: 0; }
  /* Justified like a printed report; Korean words are not broken mid-word. */
  .page.paginated p, .page.paginated li { text-align: justify; word-break: keep-all; overflow-wrap: anywhere; }
  .page.paginated td > p, .page.paginated th > p { text-align: left; }
  .page.paginated :is(td, th).fig > p { text-align: right; }
  /* Figures fit the column of a printed report: centred, at most 85% wide and 110 mm tall. */
  .page.paginated img { display: block; margin: 6mm auto 2mm; max-width: 85%; max-height: 110mm; width: auto; height: auto; object-fit: contain; }
  .page.paginated figure { margin: 6mm 0; text-align: center; }
  .page.paginated figcaption { text-align: center; font-size: 0.85em; color: #555; margin-top: 2mm; }
  .page.paginated p:has(> img:only-child) { text-align: center; }
`
}

function PagedDocument({ html, css, settings, onSettings, settingsOpen, settingsSlot, onEdit, onWebView }: { html: string; css: string; settings: PageSettings; onSettings: (next: PageSettings) => void; settingsOpen: boolean; settingsSlot?: HTMLElement | null; onEdit: () => void; onWebView: () => void }) {
  const t = useT()
  const host = useRef<HTMLDivElement>(null)
  const viewport = useRef<HTMLDivElement>(null)
  const [busy, setBusy] = useState(true)
  const [pages, setPages] = useState(0)
  const [pageSize, setPageSize] = useState({ width: A4_WIDTH_PX, height: A4_HEIGHT_PX })
  const [pageScale, setPageScale] = useState(1)
  // 'fit': follows viewport width; number: reader's zoom.
  const [zoom, setZoom] = useState<'fit' | number>('fit')
  const [failure, setFailure] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const target = host.current
    if (!target) return
    let live = true
    setBusy(true)
    setFailure(null)
    target.replaceChildren()
    // Paged.js lays out at A4 width; the viewport scales the stack.
    target.style.width = `${A4_WIDTH_PX}px`
    const sheet = URL.createObjectURL(new Blob([pagedSheetCss(css, settings)], { type: 'text/css' }))
    // Armed before Paged.js appends the template stylesheet to `document.head`.
    const unscope = scopePagedStyles(`.${PAGED_SCOPE}`)
    let timer: ReturnType<typeof setTimeout> | undefined
    const timeoutMs = (window as Window & { __KLOUDCHAT_PAGINATION_TIMEOUT_MS__?: number })
      .__KLOUDCHAT_PAGINATION_TIMEOUT_MS__ ?? 30_000
    const timedOut = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error('pagination timed out')), timeoutMs)
    })
    const forceFailure = Boolean(
      (window as Window & { __KLOUDCHAT_FORCE_PAGINATION_FAILURE__?: boolean })
        .__KLOUDCHAT_FORCE_PAGINATION_FAILURE__,
    )
    void import('pagedjs')
      .then(({ Previewer }) => forceFailure
        ? Promise.reject(new Error('forced pagination failure'))
        : Promise.race([new Previewer().preview(html, [sheet], target), timedOut]))
      .then((flow) => {
        if (!live) return
        setPages(flow.total)
        setPageSize({
          width: Math.max(A4_WIDTH_PX, target.scrollWidth),
          height: Math.max(A4_HEIGHT_PX, target.scrollHeight),
        })
        setBusy(false)
      })
      .catch(() => {
        if (!live) return
        setFailure(t('페이지를 나누지 못했습니다. 편집 화면에서 내용을 확인해 주세요.'))
        setBusy(false)
      })
      .finally(() => {
        if (timer) clearTimeout(timer)
        URL.revokeObjectURL(sheet)
      })
    return () => { live = false; if (timer) clearTimeout(timer); URL.revokeObjectURL(sheet); unscope(); target.replaceChildren() }
  // `t` is new per render; depending on it would loop.
  }, [html, css, settings, attempt])

  useEffect(() => {
    const node = viewport.current
    if (!node) return
    const fit = () => {
      if (zoom !== 'fit') {
        setPageScale(zoom)
        return
      }
      const gutter = node.clientWidth < 640 ? 16 : 48
      const room = Math.max(1, node.clientWidth - gutter)
      setPageScale(Math.min(1, room / pageSize.width))
    }
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(node)
    return () => observer.disconnect()
  }, [pageSize.width, zoom])

  const step = (direction: 1 | -1) =>
    setZoom(Math.min(2, Math.max(0.5, Math.round((pageScale + direction * 0.1) * 10) / 10)))

  return (
    <div ref={viewport} className="relative min-h-0 flex-1 overflow-auto bg-elevated p-6 max-sm:p-2">
      <div className="sticky top-0 z-20 mb-3 flex flex-wrap justify-end gap-2">
        {!busy && !failure && (
          <div className="flex items-center gap-0.5 rounded-control border border-line bg-panel p-0.5 shadow-sm" role="group" aria-label={t('확대/축소')}>
            <button type="button" aria-label={t('축소')} title={t('축소')} disabled={pageScale <= 0.5} onClick={() => step(-1)} className="grid size-8 place-items-center rounded-control text-muted hover:bg-elevated hover:text-fg disabled:opacity-40"><ZoomOut size={15} /></button>
            <button type="button" aria-label={t('폭에 맞춤')} title={t('폭에 맞춤')} onClick={() => setZoom((current) => (current === 'fit' ? 1 : 'fit'))} className={cn('h-8 min-w-14 rounded-control px-2 text-xs tabular-nums hover:bg-elevated', zoom === 'fit' ? 'text-muted' : 'text-fg')}>{Math.round(pageScale * 100)}%</button>
            <button type="button" aria-label={t('확대')} title={t('확대')} disabled={pageScale >= 2} onClick={() => step(1)} className="grid size-8 place-items-center rounded-control text-muted hover:bg-elevated hover:text-fg disabled:opacity-40"><ZoomIn size={15} /></button>
          </div>
        )}
        {/* In the ribbon slot when given; above the page otherwise. */}
        {settingsOpen && !settingsSlot && (
          <div className="basis-full rounded-card border border-line bg-panel p-3 shadow-sm" aria-label={t('페이지 설정 도구')}>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <label className="text-xs text-muted">{t('머리말')}<input aria-label={t('머리말')} value={settings.header} onChange={(event) => onSettings({ ...settings, header: event.target.value })} className="mt-1 h-9 w-full rounded-control border border-line px-2 text-sm" /></label>
              <label className="text-xs text-muted">{t('꼬리말')}<input aria-label={t('꼬리말')} value={settings.footer} onChange={(event) => onSettings({ ...settings, footer: event.target.value })} className="mt-1 h-9 w-full rounded-control border border-line px-2 text-sm" /></label>
              <label className="text-xs text-muted">{t('쪽 번호')}<select aria-label={t('쪽 번호')} value={settings.pageNumbers} onChange={(event) => onSettings({ ...settings, pageNumbers: event.target.value as PageSettings['pageNumbers'] })} className="mt-1 h-9 w-full rounded-control border border-line px-2 text-sm"><option value="page-total">{t('현재 / 전체')}</option><option value="page">{t('현재 쪽')}</option><option value="none">{t('표시 안 함')}</option></select></label>
              <label className="flex items-end gap-2 pb-2 text-xs text-muted"><input type="checkbox" checked={settings.firstPageHeader} onChange={(event) => onSettings({ ...settings, firstPageHeader: event.target.checked })} />{t('첫 쪽에도 머리말 표시')}</label>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {(['top', 'right', 'bottom', 'left'] as const).map((side) => <label key={side} className="text-xs text-muted">{t({ top: '위 여백', right: '오른쪽 여백', bottom: '아래 여백', left: '왼쪽 여백' }[side])}<input type="number" min={10} max={35} aria-label={t({ top: '위 여백', right: '오른쪽 여백', bottom: '아래 여백', left: '왼쪽 여백' }[side])} value={settings.margins[side]} onChange={(event) => onSettings({ ...settings, margins: { ...settings.margins, [side]: Math.min(35, Math.max(10, Number(event.target.value) || 10)) } })} className="ml-1 h-8 w-16 rounded-control border border-line px-2 text-sm" /> mm</label>)}
            </div>
          </div>
        )}
        {settingsOpen && settingsSlot && createPortal(
          <div className="w-full py-1" aria-label={t('페이지 설정 도구')}>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <label className="text-xs text-muted">{t('머리말')}<input aria-label={t('머리말')} value={settings.header} onChange={(event) => onSettings({ ...settings, header: event.target.value })} className="mt-1 h-9 w-full rounded-control border border-line px-2 text-sm" /></label>
              <label className="text-xs text-muted">{t('꼬리말')}<input aria-label={t('꼬리말')} value={settings.footer} onChange={(event) => onSettings({ ...settings, footer: event.target.value })} className="mt-1 h-9 w-full rounded-control border border-line px-2 text-sm" /></label>
              <label className="text-xs text-muted">{t('쪽 번호')}<select aria-label={t('쪽 번호')} value={settings.pageNumbers} onChange={(event) => onSettings({ ...settings, pageNumbers: event.target.value as PageSettings['pageNumbers'] })} className="mt-1 h-9 w-full rounded-control border border-line px-2 text-sm"><option value="page-total">{t('현재 / 전체')}</option><option value="page">{t('현재 쪽')}</option><option value="none">{t('표시 안 함')}</option></select></label>
              <label className="flex items-end gap-2 pb-2 text-xs text-muted"><input type="checkbox" checked={settings.firstPageHeader} onChange={(event) => onSettings({ ...settings, firstPageHeader: event.target.checked })} />{t('첫 쪽에도 머리말 표시')}</label>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {(['top', 'right', 'bottom', 'left'] as const).map((side) => <label key={side} className="text-xs text-muted">{t({ top: '위 여백', right: '오른쪽 여백', bottom: '아래 여백', left: '왼쪽 여백' }[side])}<input type="number" min={10} max={35} aria-label={t({ top: '위 여백', right: '오른쪽 여백', bottom: '아래 여백', left: '왼쪽 여백' }[side])} value={settings.margins[side]} onChange={(event) => onSettings({ ...settings, margins: { ...settings.margins, [side]: Math.min(35, Math.max(10, Number(event.target.value) || 10)) } })} className="ml-1 h-8 w-16 rounded-control border border-line px-2 text-sm" /> mm</label>)}
            </div>
          </div>,
          settingsSlot,
        )}
      </div>
      {busy && <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted"><Loader2 size={16} className="animate-spin" />{t('페이지를 나누는 중…')}</div>}
      {failure && (
        <div role="alert" className="mx-auto max-w-lg rounded-card border border-danger/30 bg-panel p-5 shadow-sm">
          <p className="font-semibold text-danger">{t('페이지뷰를 만들지 못했습니다')}</p>
          <p className="mt-1 text-sm text-muted">{failure} {t('문서 내용은 그대로 보존되어 있습니다.')}</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" onClick={() => setAttempt((value) => value + 1)} className="inline-flex h-9 items-center gap-1.5 rounded-control bg-accent px-3 text-sm font-medium text-white hover:opacity-90">
              <RefreshCw size={14} />{t('다시 시도')}
            </button>
            <button type="button" onClick={onEdit} className="inline-flex h-9 items-center gap-1.5 rounded-control border border-line px-3 text-sm hover:bg-elevated">
              <FilePenLine size={14} />{t('내용 편집')}
            </button>
            <button type="button" onClick={onWebView} className="inline-flex h-9 items-center gap-1.5 rounded-control border border-line px-3 text-sm hover:bg-elevated">
              <Globe2 size={14} />{t('웹뷰로 보기')}
            </button>
          </div>
        </div>
      )}
      <div
        className="mx-auto"
        style={{ width: pageSize.width * pageScale, height: pageSize.height * pageScale }}
      >
        <div
          ref={host}
          aria-label={t('실제 페이지 미리보기')}
          data-page-count={pages || undefined}
          data-page-scale={pageScale.toFixed(3)}
          className={PAGED_SCOPE}
          style={{
            width: pageSize.width,
            transform: `scale(${pageScale})`,
            transformOrigin: 'top left',
          }}
        />
      </div>
    </div>
  )
}

/** Page view and the report editor: the whole report as one Markdown-backed document; sections stay the stored unit. */
export function DocumentEditor({
  report,
  templateId,
  tokens,
  editable,
  layoutMode,
  settingsOpen,
  onLayoutMode,
  onWebView,
  onDirty,
  toolbarSlot,
  ribbonTab,
  settingsSlot,
  sourceMode = false,
  onSourceMode,
}: {
  report: ReportArtifact
  /** Empty: plain document seed. */
  templateId: string
  tokens?: DesignTokens | null
  editable: boolean
  layoutMode: 'pages' | 'edit'
  settingsOpen: boolean
  onLayoutMode?: (mode: 'pages' | 'edit') => void
  onWebView?: () => void
  /** Edited sections on every change; title, page settings and comments when changed. */
  onDirty?: (
    sections: ReportSection[],
    title?: string,
    pageSettings?: ReportArtifact['pageSettings'],
    reviewComments?: ReportArtifact['reviewComments'],
    titleBlock?: ReportTitleBlock,
  ) => void
  /** Ribbon portal slot for the editor's groups; in place when absent, hidden when `null`. */
  toolbarSlot?: HTMLElement | null
  /** Which ribbon tab the slot shows. */
  ribbonTab?: EditorRibbonTab
  /** Page-settings portal slot; above the page when absent. */
  settingsSlot?: HTMLElement | null
  /** 「Markdown 원문」: the same document as Markdown text. */
  sourceMode?: boolean
  onSourceMode?: (on: boolean) => void
}) {
  const t = useT()
  const [style, setStyle] = useState<TemplateStyle | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [outlineOpen, setOutlineOpen] = useState(false)
  const [findOpen, setFindOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [replacement, setReplacement] = useState('')
  const [findStatus, setFindStatus] = useState('')
  const [commentsOpen, setCommentsOpen] = useState(false)
  const [comments, setComments] = useState<NonNullable<ReportArtifact['reviewComments']>>(report.reviewComments ?? [])
  const [commentQuote, setCommentQuote] = useState('')
  const [commentSection, setCommentSection] = useState<string | null>(null)
  const [commentDraft, setCommentDraft] = useState('')
  // State, not ref: the page appears a render late, inside the shadow root.
  const [page, setPage] = useState<HTMLDivElement | null>(null)
  // Sections as the editor holds them; null until the first edit.
  const [draft, setDraft] = useState<ReportSection[] | null>(null)
  const [revision, setRevision] = useState(0)
  const sections = draft ?? report.sections
  const { usable, height } = useSheet(page, `${style?.css.length ?? 0}:${revision}:${sourceMode}`)
  const viewport = useRef<HTMLDivElement>(null)
  // Viewport mounts after loading returns: observer armed from a callback ref.
  const [viewportNode, setViewportNode] = useState<HTMLDivElement | null>(null)
  const attachViewport = useCallback((node: HTMLDivElement | null) => {
    viewport.current = node
    setViewportNode(node)
  }, [])
  // Fit A4 across the panel, capped at 1.
  const [scale, setScale] = useState(1)
  useEffect(() => {
    const node = viewportNode
    if (!node) return
    const fit = () => {
      const room = node.clientWidth - (node.clientWidth < 640 ? 16 : 48)
      setScale(room > 0 ? Math.min(1, room / A4_WIDTH_PX) : 1)
    }
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(node)
    return () => observer.disconnect()
  }, [viewportNode])

  useEffect(() => {
    let live = true
    if (!templateId) {
      setStyle(null)
      return
    }
    designTemplatesApi
      .style(templateId, tokens)
      .then((row) => live && setStyle(row))
      // Stored untranslated; `t` is new per render.
      .catch((err) => live && setError(errorMessage(err, '템플릿을 불러오지 못했습니다.')))
    return () => {
      live = false
    }
  }, [templateId, tokens])

  const pictures = useDiagramPictures(sections, report.id, page)

  const [editedTitle, setTitle] = useState<string | null>(null)
  const titleRef = useRef<string | null>(null)
  const [pageSettings, setPageSettings] = useState<PageSettings>({
    ...DEFAULT_PAGE_SETTINGS,
    ...report.pageSettings,
    margins: { ...DEFAULT_PAGE_SETTINGS.margins, ...report.pageSettings?.margins },
    footer: report.pageSettings?.footer ?? tokens?.footer ?? DEFAULT_PAGE_SETTINGS.footer,
  })
  // Purpose head; field values edited in place.
  const [titleBlock, setTitleBlock] = useState<ReportTitleBlock | null>(() => cleanTitleBlock(report.titleBlock))
  const draftRef = useRef<ReportSection[] | null>(null)
  const current = () => draftRef.current ?? report.sections

  const onDirtyRef = useRef(onDirty)
  useEffect(() => {
    onDirtyRef.current = onDirty
  }, [onDirty])
  /** The editor's document, split back into stored sections. */
  const commit = (next: ReportSection[]) => {
    draftRef.current = next
    setDraft(next)
    setRevision((n) => n + 1)
    onDirtyRef.current?.(next)
  }
  const editor = useEditor({
    extensions: REPORT_EXTENSIONS,
    content: sectionsToDoc(report.sections),
    editable,
    immediatelyRender: false,
    onUpdate: ({ editor: live }) => commit(docToSections(live.getJSON(), report.sections)),
  })

  useEffect(() => {
    editor?.setEditable(editable)
  }, [editor, editable])

  // Drawn diagrams reach the editor's diagram blocks.
  useEffect(() => {
    if (!editor) return
    const storage = editor.storage.fenceBlock
    storage.pictures = pictures
    storage.listeners.forEach((render) => render())
  }, [editor, pictures])

  // A drawn block's source is edited as Markdown.
  useEffect(() => {
    if (!editor) return
    editor.storage.fenceBlock.onEdit = () => onSourceMode?.(true)
  }, [editor, onSourceMode])

  const numbering = titleBlock?.numbering ?? 'none'
  const numbers = sectionNumbers(sections, numbering, titleBlock?.lists)
  const labelKey = JSON.stringify(numbering === 'none' ? [] : sections.map((section, index) => [section.id, numbers[index].label, counterStyle(numbers[index]).counterReset]))
  useEffect(() => {
    if (!editor) return
    const labels: Record<string, SectionLabel> = {}
    for (const [id, label, reset] of JSON.parse(labelKey) as [string, string, string][]) labels[id] = { label, reset }
    setSectionLabels(editor, labels)
  }, [editor, labelKey])

  // 「Markdown 원문」: the text is written from the editor on the way in and read back on the way out.
  const [source, setSource] = useState('')
  const outline = useRef<ReturnType<typeof sectionOutline>>([])
  const wasSource = useRef(false)
  useEffect(() => {
    if (!editor || sourceMode === wasSource.current) return
    wasSource.current = sourceMode
    if (sourceMode) {
      const json = editor.getJSON()
      outline.current = sectionOutline(json)
      setSource(docToMarkdown(titleRef.current ?? report.title, json))
    } else {
      const parsed = markdownToDoc(source, outline.current)
      editor.commands.setContent(parsed.doc, { emitUpdate: false })
    }
  // `source` is read at the moment the view closes.
  }, [editor, sourceMode])

  const editSource = (text: string) => {
    setSource(text)
    const parsed = markdownToDoc(text, outline.current)
    const next = docToSections(parsed.doc, report.sections)
    draftRef.current = next
    setDraft(next)
    setRevision((n) => n + 1)
    const title = parsed.title?.trim()
    if (title && title !== (titleRef.current ?? report.title)) {
      titleRef.current = title
      setTitle(title)
      onDirty?.(next, title)
    } else onDirty?.(next)
  }

  const retitle = (next: string) => {
    if (!next || next === (titleRef.current ?? report.title)) return
    titleRef.current = next
    setTitle(next)
    onDirty?.(current(), next)
  }

  const reblock = (next: ReportTitleBlock) => {
    setTitleBlock(next)
    onDirty?.(current(), undefined, undefined, undefined, next)
  }
  // Section closed by 「끝.」 when the format closes; ends its body in the page view.
  const closingAt = closingIndex(titleBlock, sections.length)
  // 개조식 붙임 closes the body: no heading of its own.
  const attachment = (section: ReportSection) => isAttachment(section, titleBlock?.lists)
  const bodyOf = (section: ReportSection, index: number) => htmlOf(
    shapedSection(section, titleBlock, index === closingAt),
    pictures,
    titleBlock?.lists === 'gaejo',
  )

  /** The section a document position falls in. */
  const sectionAt = (pos: number): string | null => {
    if (!editor) return null
    let found: string | null = null
    editor.state.doc.forEach((node, offset) => {
      if (offset > pos) return
      if (node.type.name === 'heading' && node.attrs.sectionId && (node.attrs.level === 2 || node.attrs.level === 3)) {
        found = node.attrs.sectionId as string
      }
    })
    return found ?? sections[0]?.id ?? null
  }

  // Find and replace: the title first, then the document in order, from the caret onwards.
  // A match may cross bold, links or other marks; it stays inside one paragraph or heading.
  const lastHit = useRef<'title' | 'doc' | null>(null)
  const findNext = () => {
    if (!editor || !query) return
    const matches = textMatches(editor.state.doc, query)
    const inTitle = shownTitle.includes(query)
    const total = matches.length + (inTitle ? 1 : 0)
    if (!total) return setFindStatus(t('일치하는 내용이 없습니다'))
    // From the caret on; after the end the title, then the top of the document.
    const after = lastHit.current === 'title' ? 0 : editor.state.selection.to
    const next = matches.find((one) => one.from >= after)
    if (next) {
      lastHit.current = 'doc'
      editor.chain().focus().setTextSelection(next).scrollIntoView().run()
    } else if (inTitle) {
      lastHit.current = 'title'
      viewport.current?.scrollTo({ top: 0, behavior: 'smooth' })
    } else {
      lastHit.current = 'doc'
      editor.chain().focus().setTextSelection(matches[0]).scrollIntoView().run()
    }
    setFindStatus(
      lastHit.current === 'title'
        ? t('{n}개 찾음 · 제목').replace('{n}', String(total))
        : t('{n}개 찾음').replace('{n}', String(total)),
    )
  }

  const replaceCurrent = () => {
    if (!editor || !query) return
    if (lastHit.current === 'title' && shownTitle.includes(query)) {
      retitle(shownTitle.replace(query, replacement))
    } else {
      const { from, to } = editor.state.selection
      if (editor.state.doc.textBetween(from, to, '\ufffc', '\ufffc') === query) {
        editor.chain().focus().insertContentAt({ from, to }, replacement).run()
      }
    }
    findNext()
  }

  const replaceAll = () => {
    if (!editor || !query) return
    const matches = textMatches(editor.state.doc, query)
    // One transaction: one undo puts every section back.
    let transaction = editor.state.tr
    for (const match of [...matches].reverse()) transaction = transaction.insertText(replacement, match.from, match.to)
    if (matches.length) editor.view.dispatch(transaction)
    const titled = shownTitle.includes(query)
    if (titled) retitle(shownTitle.split(query).join(replacement))
    setFindStatus(t('{n}개 바꿈').replace('{n}', String(matches.length + (titled ? 1 : 0))))
  }

  const beginComment = () => {
    if (!editor) return
    const { from, to } = editor.state.selection
    const quote = editor.state.doc.textBetween(from, to, ' ').trim()
    if (!quote) return
    setCommentQuote(quote)
    setCommentSection(sectionAt(from))
    setCommentDraft('')
    setCommentsOpen(true)
  }

  const commitComments = (next: NonNullable<ReportArtifact['reviewComments']>) => {
    setComments(next)
    onDirty?.(current(), titleRef.current ?? undefined, pageSettings, next)
  }

  const addComment = () => {
    if (!commentSection || !commentQuote || !commentDraft.trim()) return
    commitComments([...comments, {
      id: crypto.randomUUID(), sectionId: commentSection, quote: commentQuote,
      body: commentDraft.trim(), status: 'open', createdAt: new Date().toISOString(),
    }])
    setCommentQuote('')
    setCommentDraft('')
  }

  const headingPos = (sectionId: string): number | null => {
    let found: number | null = null
    editor?.state.doc.forEach((node, offset) => {
      if (found === null && node.type.name === 'heading' && node.attrs.sectionId === sectionId) found = offset
    })
    return found
  }

  const goToComment = (comment: NonNullable<ReportArtifact['reviewComments']>[number]) => {
    if (!editor) return
    const start = headingPos(comment.sectionId) ?? 0
    let match: { from: number; to: number } | undefined
    editor.state.doc.descendants((node, pos) => {
      if (match || pos < start || !node.isText || !node.text) return
      const offset = node.text.indexOf(comment.quote)
      if (offset >= 0) match = { from: pos + offset, to: pos + offset + comment.quote.length }
    })
    if (match) editor.chain().focus().setTextSelection(match).scrollIntoView().run()
  }

  const goToSection = (sectionId: string) => {
    const pos = headingPos(sectionId)
    if (!editor || pos === null) return
    const node = editor.view.nodeDOM(pos) as HTMLElement | null
    node?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    editor.chain().setTextSelection(pos + 1).run()
  }

  // Bracket placeholders (「[담당자]」) marked in the paged preview and print.
  // Figure columns flush right (`.fig`), as in the template preview and exporters.
  const shownTitle = editedTitle ?? report.title
  const previewHtml = markFigureColumns(markPlaceholders(titleBlock
    ? `<main class="page paginated-preview ${fmtClass(titleBlock)}">${titleBlockHtml(titleBlock, shownTitle)}${sections.map((section, index) => `<section>${attachment(section) ? '' : !(section.heading ?? '').trim() ? '' : `<h2${numbers[index].label ? ` data-num="${escapePagedText(numbers[index].label)}"` : ''}>${escapePagedText(section.heading)}</h2>`}${numberHtmlHeadings(bodyOf(section, index), numbering, numbers[index])}</section>`).join('')}</main>`
    : `<main class="page paginated-preview"><div class="cover"><h1>${escapePagedText(shownTitle)}</h1></div>${sections.map((section, index) => `<section>${(section.heading ?? '').trim() ? `<h2>${escapePagedText(section.heading)}</h2>` : ''}${bodyOf(section, index)}</section>`).join('')}</main>`))
  const visualStyle = tokens?.visualStyle ?? 'editorial'
  const visualCss = visualStyle === 'poster'
    ? `.page .cover{background:linear-gradient(145deg,var(--accent),color-mix(in srgb,var(--accent) 48%,#111827));color:#fff;padding:30mm 22mm}.page .cover h1{color:#fff;font-size:30pt;max-width:15ch}.page section>h2,.page .ProseMirror>h2{font-size:18pt;border:0;padding:0 0 4mm;color:var(--accent)}.page section{margin-bottom:14mm}.page blockquote,.page .callout{border-radius:3mm;background:color-mix(in srgb,var(--accent) 8%,#fff)}`
    : visualStyle === 'minimal'
      ? `.page .cover{min-height:0;padding:10mm 8mm 8mm;background:color-mix(in srgb,var(--accent) 7%,#fff)}.page .cover h1{font-size:22pt;font-weight:600;max-width:22ch}.page section>h2,.page .ProseMirror>h2{font-size:12pt;font-weight:650;letter-spacing:.08em;border:0;color:var(--muted)}.page section{margin-bottom:9mm}`
      : ''
  // Design typeface unless the template stylesheet sets its own.
  const fontCss = !style?.css && fontFamilyFor(tokens?.font) ? `.page{font-family:${fontFamilyFor(tokens?.font)}}` : ''
  const formatRules = titleBlock ? `\n${formatCss(titleBlock)}` : ''
  const pageCss = `${style?.css ?? ''}\n${visualCss}\n${fontCss}${formatRules}\n${PLACEHOLDER_CSS}`

  if (error) return <p className="p-4 text-sm text-danger">{t(error)}</p>
  if (templateId && !style) {
    return (
      <div className="flex h-full items-center justify-center text-muted">
        <Loader2 size={16} className="animate-spin" />
      </div>
    )
  }

  if (layoutMode === 'pages') {
    return <PagedDocument html={previewHtml} css={pageCss} settings={pageSettings} settingsOpen={settingsOpen} settingsSlot={settingsSlot} onEdit={() => onLayoutMode?.('edit')} onWebView={() => onWebView?.()} onSettings={(next) => { setPageSettings(next); onDirty?.(current(), titleRef.current ?? undefined, next) }} />
  }

  const openComments = comments.filter((comment) => comment.status === 'open').length
  const groups = editable && (
    <ReportEditorRibbon
      editor={editor}
      tab={ribbonTab ?? 'home'}
      sources={report.sources}
      sourceMode={sourceMode}
      onSourceMode={(on) => onSourceMode?.(on)}
      onFind={() => setFindOpen((value) => !value)}
      onComment={beginComment}
      commentsOpen={commentsOpen}
      onComments={() => setCommentsOpen((value) => !value)}
      openComments={openComments}
      outlineOpen={outlineOpen}
      onOutline={() => setOutlineOpen((value) => !value)}
    />
  )
  // A slot given as `null` hides the groups (another ribbon tab is showing).
  const tools = toolbarSlot
    ? createPortal(groups, toolbarSlot)
    : toolbarSlot === null ? null : <div className="flex min-w-0 flex-wrap items-center divide-x divide-line/80 overflow-x-auto border-b border-line bg-panel px-2 py-1">{groups}</div>
  const outlineRows = sections

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-x-hidden">
      {tools}
      {findOpen && !sourceMode && (
        <div role="search" aria-label={t('찾기 및 바꾸기')} className="flex flex-wrap items-center gap-2 border-b border-line bg-panel px-3 py-2">
          <input autoFocus aria-label={t('찾을 내용')} value={query} onChange={(event) => { setQuery(event.target.value); setFindStatus('') }} onKeyDown={(event) => { if (event.key === 'Enter') findNext() }} placeholder={t('찾을 내용')} className="h-8 w-48 rounded-control border border-line px-2 text-sm" />
          <input aria-label={t('바꿀 내용')} value={replacement} onChange={(event) => setReplacement(event.target.value)} placeholder={t('바꿀 내용')} className="h-8 w-48 rounded-control border border-line px-2 text-sm" />
          <button type="button" onClick={findNext} className="h-8 rounded-control border border-line px-3 text-xs hover:bg-elevated">{t('다음 찾기')}</button>
          <button type="button" onClick={replaceCurrent} className="h-8 rounded-control border border-line px-3 text-xs hover:bg-elevated">{t('바꾸기')}</button>
          <button type="button" onClick={replaceAll} className="h-8 rounded-control border border-line px-3 text-xs hover:bg-elevated">{t('모두 바꾸기')}</button>
          <span role="status" className="text-xs text-muted">{findStatus}</span>
        </div>
      )}
      <div className="relative flex min-h-0 flex-1">
        {outlineOpen && (
          <nav aria-label={t('문서 개요')} className="w-56 shrink-0 overflow-auto border-r border-line bg-panel p-3 max-sm:absolute max-sm:inset-y-0 max-sm:left-0 max-sm:z-20 max-sm:shadow-overlay">
            <p className="mb-2 px-2 text-[11px] font-semibold uppercase tracking-wide text-faint">{t('문서 개요')}</p>
            <button type="button" onClick={() => viewport.current?.scrollTo({ top: 0, behavior: 'smooth' })} className="mb-1 block w-full truncate rounded-control px-2 py-2 text-left text-sm font-medium text-fg hover:bg-elevated">
              {shownTitle}
            </button>
            <ol className="space-y-0.5">
              {outlineRows.map((section, index) => (
                <li key={section.id}>
                  <button
                    type="button"
                    title={section.heading}
                    onClick={() => goToSection(section.id)}
                    className={cn('flex w-full gap-2 rounded-control px-2 py-1.5 text-left text-xs text-muted hover:bg-elevated', section.level === 2 && 'pl-5')}
                  >
                    <span className="shrink-0 tabular-nums text-faint">{index + 1}</span>
                    <span className="truncate">{section.heading || t('제목 없음')}</span>
                  </button>
                </li>
              ))}
            </ol>
          </nav>
        )}
        {sourceMode && (
          <div className="flex min-h-[70vh] min-w-0 flex-1 flex-col gap-1 bg-elevated p-4 max-sm:p-2">
            <p className="text-xs text-faint">
              {t('같은 문서의 Markdown 원문입니다.')} <code># {t('제목')}</code>, <code>## {t('절 제목')}</code>, <code>### {t('소제목')}</code>
            </p>
            <textarea
              aria-label={t('문서 원본')}
              value={source}
              spellCheck={false}
              onChange={(event) => editSource(event.target.value)}
              className="min-h-0 w-full flex-1 resize-none rounded-card border border-line bg-panel px-3 py-2 font-mono text-sm leading-relaxed outline-none focus:border-accent"
            />
          </div>
        )}
        <div ref={attachViewport} aria-label={t('보고서 편집 페이지')} hidden={sourceMode} className="min-h-0 min-w-0 flex-1 overflow-auto bg-elevated p-6 max-sm:p-2">
        {/* Sheet scaled, never narrowed; outer box carries the scaled size for the scrollbar. */}
        <div
          className="mx-auto"
          style={{ width: A4_WIDTH_PX * scale, height: Math.max(height, A4_HEIGHT_PX) * scale }}
        >
          <div
            className="relative bg-white shadow-sm ring-1 ring-black/5"
            style={{
              width: A4_WIDTH_PX,
              minHeight: A4_HEIGHT_PX,
              transform: `scale(${scale})`,
              transformOrigin: 'top left',
            }}
          >
          <div className="relative">
            <DocumentShell css={`${pageCss}\n${REPORT_EDITOR_CSS}\n${sheetGeometryCss(pageSettings.margins)}`} className="report-page-shell">
              {/* `paginated`: sheet drawn here; `--sheet-h`: usable page height. */}
              <div
                ref={setPage}
                className={cn('page paginated', fmtClass(titleBlock))}
                style={{ ['--sheet-h' as string]: `${usable}px` } as React.CSSProperties}
              >
                {titleBlock ? (
                  <TitleBlock
                    block={titleBlock}
                    title={shownTitle}
                    paged
                    onChange={editable ? reblock : undefined}
                    titleNode={<EditableLine as="h1" className="tb-title" value={shownTitle} editable={editable} onChange={retitle} />}
                  />
                ) : (
                  <div className="cover">
                    <EditableLine as="h1" value={shownTitle} editable={editable} onChange={retitle} />
                  </div>
                )}
                {/* One editor over every section: `##` headings carry the section ids. */}
                <EditorContent editor={editor} className="doc-body" aria-label={t('보고서 본문')} />
              </div>
            </DocumentShell>
          </div>
          </div>
        </div>
        </div>
        {commentsOpen && (
          <aside aria-label={t('검토 메모')} className="w-72 shrink-0 overflow-auto border-l border-line bg-panel p-3 max-sm:absolute max-sm:inset-y-0 max-sm:right-0 max-sm:z-20 max-sm:w-[calc(100%-2rem)] max-sm:max-w-72 max-sm:shadow-overlay">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold">{t('검토 메모')}</h3>
              <span className="text-xs text-faint">{t('{n}개 미해결').replace('{n}', String(openComments))}</span>
            </div>
            {commentQuote && (
              <div className="mb-3 rounded-card border border-accent/30 bg-accent-soft p-2">
                <blockquote className="line-clamp-3 text-xs text-muted">“{commentQuote}”</blockquote>
                <textarea aria-label={t('메모 내용')} autoFocus value={commentDraft} onChange={(event) => setCommentDraft(event.target.value)} placeholder={t('검토 의견을 입력하세요')} className="mt-2 min-h-20 w-full resize-y rounded-control border border-line bg-panel p-2 text-sm" />
                <div className="mt-2 flex justify-end gap-1">
                  <button type="button" onClick={() => { setCommentQuote(''); setCommentDraft('') }} className="rounded-control px-2 py-1 text-xs text-muted hover:bg-elevated">{t('취소')}</button>
                  <button type="button" disabled={!commentDraft.trim()} onClick={addComment} className="rounded-control bg-accent px-2 py-1 text-xs text-white disabled:opacity-40">{t('메모 추가')}</button>
                </div>
              </div>
            )}
            <div className="space-y-2">
              {comments.map((comment) => (
                <article key={comment.id} className={cn('rounded-card border p-2', comment.status === 'resolved' ? 'border-line bg-elevated/40 opacity-70' : 'border-line bg-panel')}>
                  <button type="button" onClick={() => goToComment(comment)} className="block w-full text-left">
                    <p className="truncate text-xs font-medium text-fg">“{comment.quote}”</p>
                    <p className="mt-1 whitespace-pre-wrap text-sm text-muted">{comment.body}</p>
                  </button>
                  <button type="button" onClick={() => commitComments(comments.map((row) => row.id === comment.id ? { ...row, status: row.status === 'open' ? 'resolved' : 'open' } : row))} className="mt-2 text-xs font-medium text-accent hover:underline">
                    {comment.status === 'open' ? t('해결로 표시') : t('다시 열기')}
                  </button>
                </article>
              ))}
              {!comments.length && !commentQuote && <p className="py-8 text-center text-xs text-faint">{t('선택한 문장에 메모를 남겨 보세요.')}</p>}
            </div>
          </aside>
        )}
      </div>
    </div>
  )
}

/** Picture per mermaid source, keyed by source text; missing ones drawn off-screen (outside ProseMirror) and stored. */
function useDiagramPictures(
  sections: ReportSection[],
  artifactId: string,
  look: HTMLElement | null,
): Map<string, string> {
  const [pictures, setPictures] = useState<Map<string, string>>(() => new Map())
  // Last-resolve signature; else every keystroke loops.
  const resolved = useRef('')

  useEffect(() => {
    const wanted = sections.flatMap((section) => {
      const sources = mermaidSources(section)
      return sources.length ? [[section, sources] as const] : []
    })
    const signature = JSON.stringify(
      wanted.map(([section, sources]) => [Object.keys(section.diagrams ?? {}), sources]),
    ) + String(Boolean(look))
    if (signature === resolved.current) return
    resolved.current = signature

    let live = true
    void (async () => {
      const found = new Map<string, string>()
      const missing: { section: ReportSection; source: string; key: string }[] = []
      for (const [section, sources] of wanted) {
        for (const source of sources) {
          const key = await diagramKey(source)
          const picture = section.diagrams?.[key]
          if (picture) found.set(source, picture)
          else missing.push({ section, source, key })
        }
      }
      if (!live) return
      setPictures(found)
      if (!missing.length || !look) return

      // Off-screen inside the template's shadow root: takes the document's theme.
      const easel = document.createElement('div')
      easel.style.cssText = 'position:absolute;left:-99999px;top:0;width:700px'
      look.appendChild(easel)
      try {
        for (const { section, source, key } of missing) {
          const svg = await drawFitting(source, theme(easel), FRAMES.page)
          if (!live) return
          if (!svg) continue
          // Same frame as the page view: export matches the screen.
          const png = await rasterise(framed(svg, FRAMES.page), 1)
          if (!live) return
          if (!png) continue
          found.set(source, png)
          setPictures(new Map(found))
          if (artifactId) {
            await artifactsApi.storeDiagram(artifactId, section.id, key, png).catch(() => undefined)
          }
        }
      } finally {
        easel.remove()
      }
    })()
    return () => {
      live = false
    }
  }, [sections, artifactId, look])

  return pictures
}
