import type { Editor } from '@tiptap/react'
import {
  AlignCenter,
  AlignJustify,
  AlignLeft,
  AlignRight,
  Bold,
  Columns3,
  ImagePlus,
  Italic,
  List,
  ListOrdered,
  Loader2,
  Quote,
  Redo2,
  Rows3,
  Strikethrough,
  Table as TableIcon,
  Trash2,
  Underline,
  Undo2,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { DocumentShell } from '@/components/report/DocumentShell'
import { EditableLine } from '@/components/report/EditableLine'
import { artifactsApi } from '@/lib/api'
import { diagramKey } from '@/lib/diagramKey'
import { draw, rasterise, theme } from '@/lib/mermaid'
import { parse as parsePairs } from '@/components/report/StepList'
import { SectionEditor } from '@/components/report/SectionEditor'
import {
  A4_HEIGHT_PX,
  A4_WIDTH_PX,
  usePagination,
} from '@/components/report/usePagination'
import {
  designTemplatesApi,
  errorMessage,
  type DesignTokens,
  type TemplateStyle,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import type { ReportArtifact, ReportSection } from '@/types'
import { useT } from '@/lib/useT'

/**
 * The bar, in the order a word processor puts things.
 *
 * The first cut was a row of ghost buttons with a `서체` dropdown that hid the
 * sizes inside it, and it read as a row of chips rather than as a toolbar —
 * there was no way to see what the text under the caret already was, which is
 * half of what a formatting bar is for. This one shows state: the face and the
 * size are fields carrying the current value, the toggles light up, and the
 * groups are separated the way every editor separates them.
 */
const FONTS = [
  { label: '문서 기본', value: '' },
  { label: '바탕', value: "'Nanum Myeongjo', 'Batang', serif" },
  { label: '맑은 고딕', value: "'Pretendard', 'Malgun Gothic', sans-serif" },
  { label: '돋움', value: "'Nanum Gothic', 'Dotum', sans-serif" },
  { label: '고정폭', value: "'D2Coding', 'Consolas', monospace" },
]
const SIZES = ['8', '9', '10', '11', '12', '14', '16', '18', '20', '24', '28', '36']
const ALIGNMENTS = [
  { value: 'left', icon: AlignLeft, label: '왼쪽 맞춤' },
  { value: 'center', icon: AlignCenter, label: '가운데 맞춤' },
  { value: 'right', icon: AlignRight, label: '오른쪽 맞춤' },
  { value: 'justify', icon: AlignJustify, label: '양쪽 맞춤' },
] as const

/** A group boundary. Every editor draws one; without them this is a row of chips. */
function Sep() {
  return <span className="mx-1.5 h-5 w-px shrink-0 bg-line" />
}

/**
 * One toggle. Pressed state is the whole point — a bar that cannot tell you
 * the caret is already inside bold text is a bar you have to guess with.
 */
function Tool({
  on,
  label,
  disabled,
  onClick,
  children,
}: {
  on?: boolean
  label: string
  disabled?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      // `onMouseDown` rather than `onClick`: a click steals focus from the
      // document first, and a formatting command with no selection under it
      // does nothing. Preventing the default keeps the caret where it was.
      onMouseDown={(e) => {
        e.preventDefault()
        if (!disabled) onClick()
      }}
      disabled={disabled}
      aria-pressed={on}
      aria-label={label}
      title={label}
      className={cn(
        'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-control transition-colors',
        'disabled:pointer-events-none disabled:opacity-40',
        on ? 'bg-accent-soft text-accent' : 'text-muted hover:bg-elevated hover:text-fg',
      )}
    >
      {children}
    </button>
  )
}

/**
 * Re-renders the bar whenever the caret moves or the document changes.
 *
 * Tiptap 3 stopped re-rendering React on every transaction — a document with
 * six editors in it cannot afford that — so a component that reads
 * `isActive()` has to ask to be told. Without this the bold button lights up
 * once and then lies for the rest of the session, which is worse than not
 * showing state at all.
 */
function useEditorTick(editor: Editor | null) {
  const [, tick] = useState(0)
  useEffect(() => {
    if (!editor) return
    const bump = () => tick((n) => n + 1)
    editor.on('selectionUpdate', bump)
    editor.on('transaction', bump)
    return () => {
      editor.off('selectionUpdate', bump)
      editor.off('transaction', bump)
    }
  }, [editor])
}

function Toolbar({ editor }: { editor: Editor | null }) {
  const t = useT()
  useEditorTick(editor)
  const off = !editor
  //: What the caret is sitting in, so the fields show it rather than a blank.
  const face = (editor?.getAttributes('textStyle').fontFamily as string) ?? ''
  const size = String(
    (editor?.getAttributes('textStyle').fontSize as string) ?? '',
  ).replace(/pt$/, '')

  const run = (fn: () => void) => () => fn()

  const insertImage = () => {
    if (!editor) return
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'image/png,image/jpeg,image/gif,image/webp'
    input.onchange = () => {
      const file = input.files?.[0]
      if (!file) return
      // Read to a data URI rather than uploaded. A report is exported and
      // mailed; a picture that lives at a URL is a picture that is missing by
      // the time somebody opens the attachment.
      const reader = new FileReader()
      reader.onload = () => editor.chain().focus().setImage({ src: String(reader.result) }).run()
      reader.readAsDataURL(file)
    }
    input.click()
  }

  const field =
    'h-8 rounded-control border border-line bg-panel px-2 text-sm text-fg ' +
    'disabled:opacity-40 focus:outline-2 focus:outline-offset-1 focus:outline-accent'

  return (
    <div className="flex flex-wrap items-center gap-0.5 border-b border-line bg-panel px-3 py-1.5">
      <select
        aria-label={t('서체')}
        title={t('서체')}
        disabled={off}
        value={face}
        className={cn(field, 'w-32')}
        onMouseDown={(e) => e.stopPropagation()}
        onChange={(e) =>
          e.target.value
            ? editor?.chain().focus().setFontFamily(e.target.value).run()
            : editor?.chain().focus().unsetFontFamily().run()
        }
      >
        {FONTS.map((f) => (
          <option key={f.label} value={f.value}>
            {t(f.label)}
          </option>
        ))}
      </select>
      <select
        aria-label={t('글자 크기')}
        title={t('글자 크기')}
        disabled={off}
        value={size}
        className={cn(field, 'w-16')}
        onChange={(e) =>
          e.target.value
            ? editor?.chain().focus().setFontSize(`${e.target.value}pt`).run()
            : editor?.chain().focus().unsetFontSize().run()
        }
      >
        <option value="">{t('기본')}</option>
        {SIZES.map((n) => (
          <option key={n} value={n}>
            {n}
          </option>
        ))}
      </select>

      <Sep />

      <Tool
        label={t('굵게')}
        disabled={off}
        on={editor?.isActive('bold')}
        onClick={run(() => editor?.chain().focus().toggleBold().run())}
      >
        <Bold size={15} />
      </Tool>
      <Tool
        label={t('기울임')}
        disabled={off}
        on={editor?.isActive('italic')}
        onClick={run(() => editor?.chain().focus().toggleItalic().run())}
      >
        <Italic size={15} />
      </Tool>
      <Tool
        label={t('밑줄')}
        disabled={off}
        on={editor?.isActive('underline')}
        onClick={run(() => editor?.chain().focus().toggleUnderline().run())}
      >
        <Underline size={15} />
      </Tool>
      <Tool
        label={t('취소선')}
        disabled={off}
        on={editor?.isActive('strike')}
        onClick={run(() => editor?.chain().focus().toggleStrike().run())}
      >
        <Strikethrough size={15} />
      </Tool>

      <Sep />

      {ALIGNMENTS.map((a) => (
        <Tool
          key={a.value}
          label={t(a.label)}
          disabled={off}
          on={editor?.isActive({ textAlign: a.value })}
          onClick={run(() => editor?.chain().focus().setTextAlign(a.value).run())}
        >
          <a.icon size={15} />
        </Tool>
      ))}

      <Sep />

      <Tool
        label={t('글머리 기호')}
        disabled={off}
        on={editor?.isActive('bulletList')}
        onClick={run(() => editor?.chain().focus().toggleBulletList().run())}
      >
        <List size={15} />
      </Tool>
      <Tool
        label={t('번호 매기기')}
        disabled={off}
        on={editor?.isActive('orderedList')}
        onClick={run(() => editor?.chain().focus().toggleOrderedList().run())}
      >
        <ListOrdered size={15} />
      </Tool>
      <Tool
        label={t('인용')}
        disabled={off}
        on={editor?.isActive('blockquote')}
        onClick={run(() => editor?.chain().focus().toggleBlockquote().run())}
      >
        <Quote size={15} />
      </Tool>

      <Sep />

      <Tool
        label={t('표 넣기')}
        disabled={off}
        onClick={run(() =>
          editor?.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(),
        )}
      >
        <TableIcon size={15} />
      </Tool>
      {/* 표 편집 명령은 표 안에 커서가 있을 때만 뜬다. 늘 떠 있으면 열 지우기
          같은 것이 문서 전체에 대한 명령처럼 보인다. */}
      {editor?.isActive('table') && (
        <>
          <Tool
            label={t('아래에 행 추가')}
            onClick={run(() => editor.chain().focus().addRowAfter().run())}
          >
            <Rows3 size={15} />
          </Tool>
          <Tool
            label={t('오른쪽에 열 추가')}
            onClick={run(() => editor.chain().focus().addColumnAfter().run())}
          >
            <Columns3 size={15} />
          </Tool>
          <Tool
            label={t('표 지우기')}
            onClick={run(() => editor.chain().focus().deleteTable().run())}
          >
            <Trash2 size={15} />
          </Tool>
        </>
      )}
      <Tool label={t('그림')} disabled={off} onClick={insertImage}>
        <ImagePlus size={15} />
      </Tool>

      <Sep />

      <Tool
        label={t('실행 취소')}
        disabled={off || !editor?.can().undo()}
        onClick={run(() => editor?.chain().focus().undo().run())}
      >
        <Undo2 size={15} />
      </Tool>
      <Tool
        label={t('다시 실행')}
        disabled={off || !editor?.can().redo()}
        onClick={run(() => editor?.chain().focus().redo().run())}
      >
        <Redo2 size={15} />
      </Tool>

      {off && (
        <span className="ml-2 text-xs text-faint">{t('문단을 눌러 편집을 시작하세요')}</span>
      )}
    </div>
  )
}

/**
 * The paper, and a line where each page is about to end.
 *
 * One continuous sheet rather than a stack of them. Separate sheets were the
 * earlier answer and they were a claim the screen could not back — see
 * `usePagination`. What survives is the part that was always true: the width is
 * A4, the margins are the template's, and the text will break somewhere near
 * each of these lines.
 *
 * Dashed and labelled `n쪽 즈음` for the same reason. A solid rule with "2"
 * beside it reads as a fact; this reads as an estimate, which is what it is
 * until a real layout engine lays the document out.
 */
function PageGuides({ pages, usable }: { pages: number; usable: number }) {
  const t = useT()
  if (pages < 2) return null
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      {Array.from({ length: pages - 1 }, (_, i) => (
        <div
          key={i}
          className="absolute inset-x-0 border-t border-dashed border-line/80"
          style={{ top: (i + 1) * usable }}
        >
          <span className="absolute -top-4 right-3 rounded bg-panel/90 px-1.5 text-[10px] text-faint">
            {t('{n}쪽 즈음').replace('{n}', String(i + 2))}
          </span>
        </div>
      ))}
    </div>
  )
}

/**
 * A report as a document somebody can type in.
 *
 * The alternative this replaces was a choice made once, at generation: pick a
 * 서식 and the result is an HTML artifact nothing can edit; pick none and the
 * result is prose with no shape. Neither could become the other, and a
 * document you cannot revise is not a document, it is a printout.
 *
 * Sections stay the stored unit. A section is already what the rewriter
 * rewrites, what the fact-checker checks and what the wrapper draws a heading
 * for; making the page one editable blob would have thrown all three away for
 * a cosmetic gain.
 */
export function DocumentEditor({
  report,
  templateId,
  tokens,
  editable,
  onDirty,
}: {
  report: ReportArtifact
  /** Which 서식 the document wears. Empty renders the plain document seed. */
  templateId: string
  tokens?: DesignTokens | null
  editable: boolean
  /**
   * Called with the edited sections whenever anything changes, and with the
   * document's title when that is what changed.
   */
  onDirty?: (sections: ReportSection[], title?: string) => void
}) {
  const t = useT()
  const [style, setStyle] = useState<TemplateStyle | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [focused, setFocused] = useState<Editor | null>(null)
  //: Held in state, not a ref: it is portalled into a shadow root that
  //: `DocumentShell` creates in its own effect, so it appears a render after
  //: everything around it. State is what tells the pagination to look again.
  const [page, setPage] = useState<HTMLDivElement | null>(null)
  //: Edited bodies, keyed by section id. Absent means untouched, which is what
  //: keeps a document nobody typed in byte-identical to what was generated.
  const [edits, setEdits] = useState<Record<string, string>>({})
  //: Re-measure when the stylesheet lands or the document is edited. The
  //: observer catches a block growing; neither of these changes its own size.
  const { pages, usable, height } = usePagination(
    page,
    `${style?.css.length ?? 0}:${Object.keys(edits).length}`,
  )
  const viewport = useRef<HTMLDivElement>(null)
  /**
   * How much of an A4 page fits across the panel.
   *
   * Capped at 1: a document is never blown up past its own size, because the
   * point of the page view is to show what the paper will look like. It goes
   * below 1 as far as it has to — a panel narrow enough to make the text
   * unreadable is a panel somebody widens, and the 넓게 보기 control beside
   * this one is how.
   */
  const [scale, setScale] = useState(1)
  useEffect(() => {
    const node = viewport.current
    if (!node) return
    const fit = () => {
      // The padding the scroll box draws, so the page is not flush to the edge.
      const room = node.clientWidth - 48
      setScale(room > 0 ? Math.min(1, room / A4_WIDTH_PX) : 1)
    }
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    let live = true
    if (!templateId) {
      setStyle(null)
      return
    }
    designTemplatesApi
      .style(templateId, tokens)
      .then((row) => live && setStyle(row))
      // Stored untranslated and translated where it is drawn. `useT` returns a
      // new function on every render, so naming `t` in the dependency list
      // below would re-run this effect on every render — fetching the
      // stylesheet again, setting state again, and rendering again.
      .catch((err) => live && setError(errorMessage(err, '서식을 불러오지 못했습니다.')))
    return () => {
      live = false
    }
  }, [templateId, tokens])

  const pictures = useDiagramPictures(report.sections, report.id, page)
  const bodyOf = useCallback(
    (section: ReportSection) => edits[section.id] ?? htmlOf(section, pictures),
    [edits, pictures],
  )

  //: Headings somebody retyped, and the document's own title. Held apart from
  //: `edits` because they are text rather than markup and go back to the
  //: artifact as a heading and a title, not as a section body.
  const [renamed, setRenamed] = useState<Record<string, string>>({})
  const [, setTitle] = useState<string | null>(null)

  const compose = (
    bodies: Record<string, string>,
    headings: Record<string, string>,
  ): ReportSection[] =>
    report.sections.map((s) => ({
      ...s,
      ...(headings[s.id] === undefined ? {} : { heading: headings[s.id] }),
      ...(bodies[s.id] === undefined
        ? {}
        : { content: bodies[s.id], format: 'html' as const }),
    }))

  const change = (section: ReportSection, html: string) => {
    const next = { ...edits, [section.id]: html }
    setEdits(next)
    onDirty?.(compose(next, renamed))
  }

  const rename = (section: ReportSection, heading: string) => {
    if (!heading || heading === section.heading) return
    const next = { ...renamed, [section.id]: heading }
    setRenamed(next)
    onDirty?.(compose(edits, next))
  }

  const retitle = (next: string) => {
    if (!next || next === report.title) return
    setTitle(next)
    onDirty?.(compose(edits, renamed), next)
  }

  if (error) return <p className="p-4 text-sm text-danger">{t(error)}</p>
  if (templateId && !style) {
    return (
      <div className="flex h-full items-center justify-center text-muted">
        <Loader2 size={16} className="animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {editable && <Toolbar editor={focused} />}
      <div ref={viewport} className="min-h-0 flex-1 overflow-auto bg-elevated p-6">
        {/*
          The sheet is A4 and the panel is whatever the panel is.

          Left to `max-width: 100%` the page simply narrowed — measured in a
          352px panel it was a 352px "A4", which is not a page of paper at any
          scale: the line length, the margins and the page breaks were all
          wrong, and the document looked like prose squeezed into a column
          rather than like the thing that comes out of the printer. Scaled
          instead, an A4 page is an A4 page at whatever size there is room for,
          which is what every word processor does with its zoom.

          The outer box carries the scaled height so the scrollbar matches what
          is drawn; the transform alone would leave it measuring the unscaled
          document.
        */}
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
            <DocumentShell css={style?.css ?? ''}>
              {/* `paginated` 는 서식에게 "낱장은 내가 뒤에 그린다" 고 알린다.
                  완성된 파일에는 이 클래스가 없으므로 종이색은 그대로다. */}
              <div
                ref={setPage}
                className="page paginated"
                /* 한 장에 들어가는 높이를 서식에게 알려준다. 시트는 A4 이지
                   창이 아니므로, 서식이 `vh` 로 재던 자리는 이 값을 읽는다. */
                style={{ ['--sheet-h' as string]: `${usable}px` } as React.CSSProperties}
              >
                <div className="cover">
                  {/* 제목과 절 제목도 고칠 수 있어야 한다. 종이처럼 보이고
                      문단에는 타이핑이 되는 화면에서, 커서를 거부하는 제목은
                      의도가 아니라 고장으로 읽힌다. */}
                  <EditableLine
                    as="h1"
                    value={report.title}
                    editable={editable}
                    onChange={retitle}
                  />
                </div>
                {report.sections.map((section) => (
                  <section key={section.id}>
                    <EditableLine
                      value={section.heading}
                      editable={editable}
                      onChange={(heading) => rename(section, heading)}
                    />
                    <SectionEditor
                      html={bodyOf(section)}
                      editable={editable}
                      onReady={setFocused}
                      onChange={(html) => change(section, html)}
                    />
                  </section>
                ))}
              </div>
            </DocumentShell>
          </div>
          <PageGuides pages={pages} usable={usable} />
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * A section's body as HTML, whichever way it was stored.
 *
 * Markdown → HTML happens here rather than on the server because the browser
 * already renders exactly this Markdown, and a second implementation in Python
 * would be a second answer to what a list looks like. Deliberately small: the
 * model writes prose, sub-headings and lists, and anything richer than that
 * arrived as HTML in the first place.
 */
function htmlOf(section: ReportSection, pictures: Map<string, string>): string {
  if (section.format === 'html') return section.content
  // `"` as well as the three that matter in text. Every use of this in here
  // is an attribute value or goes next to one, and a mermaid source is full of
  // quoted labels — `"인건비" : 52` cut `data-source` off at its own first
  // quote, so the fence written back on the next save was a chart with no
  // slices in it. An escaped quote is still a quote in text.
  const escape = (s: string) =>
    s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
  const inline = (s: string) =>
    escape(s)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(?<!\w)\*(.+?)\*(?!\w)/g, '<em>$1</em>')
      .replace(/`(.+?)`/g, '<code>$1</code>')

  const out: string[] = []
  //: The run of list items being collected, and which kind it is. Held apart
  //: from `out` because a list is only known to be finished when something
  //: that is not a list item turns up.
  let items: string[] = []
  let ordered = false
  //: The GFM table being collected, for the same reason.
  //:
  //: Without this a table reached the page view as `<p>| 기준 | 값 |</p>` —
  //: visible in the web view, which renders Markdown properly, and gone as a
  //: table the moment somebody switched to pages. The two views have to show
  //: the same document or neither can be trusted, and the exporters have drawn
  //: real tables since `report_export` learned to.
  let rows: string[][] = []
  //: The fence being collected, and what it was opened with. Fences were not
  //: handled here at all, so a `kpi` or `mermaid` block reached the page view
  //: as its own source set in paragraphs — visible as a figure in the web
  //: view, and as three lines of backticks the moment somebody switched to
  //: pages. The two views have to show the same document.
  let fence: string[] | null = null
  let fenceLang = ''
  const flush = () => {
    if (items.length) {
      const tag = ordered ? 'ol' : 'ul'
      out.push(`<${tag}>${items.map((i) => `<li>${i}</li>`).join('')}</${tag}>`)
      items = []
    }
    if (rows.length) {
      const width = Math.max(...rows.map((r) => r.length))
      const cell = (row: string[], c: number, tag: string) =>
        `<${tag}>${row[c] ?? ''}</${tag}>`
      const [head, ...body] = rows
      const headRow = `<tr>${Array.from({ length: width }, (_, c) => cell(head, c, 'th')).join('')}</tr>`
      const bodyRows = body
        .map(
          (row) =>
            `<tr>${Array.from({ length: width }, (_, c) => cell(row, c, 'td')).join('')}</tr>`,
        )
        .join('')
      out.push(`<table><thead>${headRow}</thead><tbody>${bodyRows}</tbody></table>`)
      rows = []
    }
  }
  for (const raw of (section.content || '').split('\n')) {
    const line = raw.trim()
    const fenceMark = /^```+\s*([A-Za-z0-9_-]*)\s*$/.exec(line)
    if (fence !== null) {
      if (fenceMark) {
        out.push(fenceHtml(fenceLang, fence.join('\n'), escape, pictures))
        fence = null
      } else {
        // Inside a fence, so the raw line — indentation in a fence is content.
        fence.push(raw)
      }
      continue
    }
    if (fenceMark && fenceMark[1]) {
      flush()
      fence = []
      fenceLang = fenceMark[1].toLowerCase()
      continue
    }
    if (!line) {
      // A blank line does not end a table — models put one between every row.
      // The stored text is tidied on the way in; this covers what was written
      // before that and anything edited by hand since.
      if (!rows.length) flush()
      continue
    }
    // A table row, and the `| --- | --- |` rule under its head, which carries
    // no cells of its own.
    const row = /^\|(.+)\|$/.exec(line)
    if (row) {
      if (!/^[\s:|-]+$/.test(row[1])) {
        rows.push(row[1].split(/(?<!\\)\|/).map((c) => inline(c.replace(/\\\|/g, '|').trim())))
      }
      continue
    }
    // A picture on its own line. The writer emits one for every figure
    // somebody approved, and the exporters read the same form back.
    const image = /^!\[([^\]]*)\]\(([^)]+)\)$/.exec(line)
    if (image) {
      flush()
      const caption = escape(image[1])
      out.push(
        `<figure><img src="${escape(image[2])}" alt="${caption}">` +
          (caption ? `<figcaption>${caption}</figcaption>` : '') +
          '</figure>',
      )
      continue
    }
    const heading = /^(#{2,6})\s+(.*)$/.exec(line)
    const bullet = /^[-*+]\s+(.*)$/.exec(line)
    const numbered = /^\d{1,9}[.)]\s+(.*)$/.exec(line)
    const quote = /^>\s*(.*)$/.exec(line)
    if (heading) {
      flush()
      // The wrapper draws the section's own heading, so everything in the body
      // sits below it — h3 at shallowest, which is what the seeds style.
      const level = Math.min(heading[1].length + 1, 6)
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`)
    } else if (bullet) {
      if (ordered) flush()
      ordered = false
      items.push(inline(bullet[1]))
    } else if (numbered) {
      if (!ordered) flush()
      ordered = true
      items.push(inline(numbered[1]))
    } else if (quote) {
      flush()
      out.push(`<blockquote>${inline(quote[1])}</blockquote>`)
    } else {
      flush()
      out.push(`<p>${inline(line)}</p>`)
    }
  }
  // An unterminated fence — a document caught mid-stream. Drawn with what
  // arrived rather than dropped, so a strip appears as its numbers land.
  if (fence !== null) out.push(fenceHtml(fenceLang, fence.join('\n'), escape, pictures))
  flush()
  return out.join('')
}

/**
 * One fenced block as the markup the 서식 styles.
 *
 * A strip becomes `<div class="kpi">` — the seed owns every size and colour in
 * it, which is why the model is not allowed a `style=` and does not need one.
 *
 * A mermaid diagram becomes a `<figure class="diagram">` carrying its own
 * source, and the picture of it if a reader has already had one drawn. The
 * source is the part that matters: a figure that arrived here as a picture
 * alone would survive the save and still lose the diagram, because the text it
 * was drawn from would no longer exist anywhere to change.
 *
 * Every other fence comes out empty. A fence nothing renders is a mistake in
 * the text, and this view exists to look like the printed page — three lines
 * of backticks on it say nothing the web view does not say better.
 */
function fenceHtml(
  lang: string,
  source: string,
  escape: (s: string) => string,
  pictures: Map<string, string>,
): string {
  if (lang === 'kpi') {
    const cells = parsePairs(source, 4)
      .map(
        ([value, label]) =>
          `<div><strong>${escape(value)}</strong><span>${escape(label)}</span></div>`,
      )
      .join('')
    return cells ? `<div class="kpi">${cells}</div>` : ''
  }
  if (lang === 'steps') {
    // An `<ol>` rather than a `<div>`: it *is* an ordered list, and written as
    // one the numbering is the browser's and Word's rather than something the
    // model typed and could get wrong. The seed hangs the rail off it.
    const items = parsePairs(source, 8)
      .map(
        ([name, detail]) =>
          `<li><strong>${escape(name)}</strong>` +
          (detail ? ` <span>${escape(detail)}</span>` : '') +
          '</li>',
      )
      .join('')
    return items ? `<ol class="steps">${items}</ol>` : ''
  }
  if (lang === 'chart') {
    // The numbers, not a drawing of them. The 서식 styles the figure and the
    // exporters read the same source back to build a chart Word can edit.
    return `<figure class="chart" data-source="${escape(source)}"></figure>`
  }
  if (lang === 'mermaid') {
    const picture = pictures.get(source)
    return (
      `<figure class="diagram" data-source="${escape(source)}">` +
      (picture ? `<img src="${escape(picture)}" alt="">` : '') +
      '</figure>'
    )
  }
  return ''
}

/**
 * The picture for each mermaid source in these sections — drawing the ones
 * that do not have one yet.
 *
 * Looking them up was not enough. A picture only existed if somebody had
 * already opened the web view, so a reader who went straight to the page view
 * got a dashed placeholder where a figure belonged, and so did one who watched
 * the document being written and then switched — the picture had been stored
 * on the server by then, but this screen is holding the copy of the artifact
 * it was handed, and nothing had told it.
 *
 * So the missing ones are drawn here. Off-screen, into a detached element:
 * this view's document belongs to ProseMirror, which re-renders its own nodes
 * whenever the document changes and would wipe anything injected into them. It
 * only needs the raster anyway.
 *
 * What is drawn is stored, exactly as the web view stores it — the server
 * compares before writing, so whichever screen gets there first is the one
 * that pays, and the file has its figure either way.
 *
 * Keyed by source text rather than by digest, so the caller needs to know
 * nothing about how a diagram is kept.
 */
function useDiagramPictures(
  sections: ReportSection[],
  artifactId: string,
  look: HTMLElement | null,
): Map<string, string> {
  const [pictures, setPictures] = useState<Map<string, string>>(() => new Map())
  //: What the last resolve was for. Sections re-render constantly — a keystroke
  //: in any of them — and without this the digests would be recomputed on each
  //: one and set new state each time, which is a render loop.
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

      // Drawn into a detached element so ProseMirror never sees it. The
      // element is still styled by the 서식 — `look` is inside its shadow root
      // — so the figure comes out in the document's own colours and face.
      const easel = document.createElement('div')
      easel.style.cssText = 'position:absolute;left:-99999px;top:0;width:700px'
      look.appendChild(easel)
      try {
        for (const { section, source, key } of missing) {
          const svg = await draw(source, theme(easel))
          if (!live) return
          if (!svg) continue
          const png = await rasterise(svg)
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

/** Every mermaid fence in a Markdown section body, in order. */
function mermaidSources(section: ReportSection): string[] {
  if (section.format === 'html') return []
  const out: string[] = []
  let collecting: string[] | null = null
  for (const raw of (section.content || '').split('\n')) {
    const mark = /^```+\s*([A-Za-z0-9_-]*)\s*$/.exec(raw.trim())
    if (collecting !== null) {
      if (mark) {
        out.push(collecting.join('\n'))
        collecting = null
      } else collecting.push(raw)
    } else if (mark && mark[1]?.toLowerCase() === 'mermaid') {
      collecting = []
    }
  }
  if (collecting !== null) out.push(collecting.join('\n'))
  return out
}
