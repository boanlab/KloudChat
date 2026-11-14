import type { Editor } from '@tiptap/react'
import {
  Bold,
  Brackets,
  Code,
  Code2,
  FileCode2,
  ImagePlus,
  Indent,
  Italic,
  Link2,
  List,
  ListChecks,
  ListOrdered,
  ListTree,
  MessageSquarePlus,
  MessagesSquare,
  Minus,
  Outdent,
  Quote,
  Redo2,
  Search,
  SeparatorHorizontal,
  Strikethrough,
  Table as TableIcon,
  Trash2,
  Undo2,
  Workflow,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { RibbonButton, RibbonGroup } from '@/components/artifacts/ArtifactRibbon'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'
import type { Source } from '@/types'

export type EditorRibbonTab = 'home' | 'insert' | 'review' | 'view'

/** Tiptap 3 does not re-render React itself: re-render per selection change and transaction. */
export function useEditorTick(editor: Editor | null) {
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

// Markdown heading depths: `##` is a section, `###`/`####` sit inside one.
const STYLES = [
  { level: 0, label: '본문', sample: 'text-[13px]' },
  { level: 2, label: '절 제목', sample: 'text-[15px] font-semibold' },
  { level: 3, label: '소제목', sample: 'text-[14px] font-semibold' },
  { level: 4, label: '작은 제목', sample: 'text-[13px] font-semibold' },
] as const

const STARTER_DIAGRAM = 'flowchart LR\n  A[시작] --> B[검토]\n  B --> C[완료]'

/** The report editor's ribbon groups for one tab; Markdown features only. */
export function ReportEditorRibbon({
  editor,
  tab,
  sources,
  sourceMode,
  onSourceMode,
  onFind,
  onComment,
  commentsOpen,
  onComments,
  openComments,
  outlineOpen,
  onOutline,
}: {
  editor: Editor | null
  tab: EditorRibbonTab
  sources: Source[]
  sourceMode: boolean
  onSourceMode: (on: boolean) => void
  onFind: () => void
  onComment: () => void
  commentsOpen: boolean
  onComments: () => void
  openComments: number
  outlineOpen: boolean
  onOutline: () => void
}) {
  const t = useT()
  useEditorTick(editor)
  // The Markdown text is edited instead while 「Markdown 원문」 is on.
  const off = !editor || sourceMode
  const chain = () => editor!.chain().focus()
  const active = (name: string, attrs?: Record<string, unknown>) => Boolean(editor?.isActive(name, attrs))

  const insertPicture = () => {
    if (!editor) return
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'image/png,image/jpeg,image/gif,image/webp'
    input.onchange = () => {
      const file = input.files?.[0]
      if (!file) return
      // Data URI: the exported file carries it.
      const reader = new FileReader()
      reader.onload = () => editor.chain().focus().setImage({ src: String(reader.result), alt: file.name.replace(/\.[^.]+$/, '') }).run()
      reader.readAsDataURL(file)
    }
    input.click()
  }

  const editLink = () => {
    if (!editor) return
    const current = String(editor.getAttributes('link').href ?? '')
    const href = window.prompt(t('링크 주소'), current || 'https://')
    if (href === null) return
    if (!href.trim() || href.trim() === 'https://') chain().extendMarkRange('link').unsetLink().run()
    else chain().extendMarkRange('link').setLink({ href: href.trim() }).run()
  }

  const sourceToggle = (
    <RibbonButton
      big
      icon={<FileCode2 size={16} />}
      label={t('Markdown 원문')}
      title={t('같은 문서를 Markdown 원문으로 보고 고칩니다')}
      pressed={sourceMode}
      disabled={!editor}
      onClick={() => onSourceMode(!sourceMode)}
    />
  )

  if (tab === 'home') {
    const style = STYLES.find((row) => row.level && active('heading', { level: row.level }))?.level ?? 0
    return (
      <>
        <RibbonGroup label={t('실행 취소')}>
          <div data-ribbon-stack="">
            <RibbonButton icon={<Undo2 size={15} />} label={t('실행 취소')} disabled={off || !editor?.can().undo()} onClick={() => chain().undo().run()} />
            <RibbonButton icon={<Redo2 size={15} />} label={t('다시 실행')} disabled={off || !editor?.can().redo()} onClick={() => chain().redo().run()} />
          </div>
        </RibbonGroup>
        <RibbonGroup label={t('글자')}>
          <RibbonButton icon={<Bold size={15} />} label={t('굵게')} pressed={active('bold')} disabled={off} onClick={() => chain().toggleBold().run()} />
          <RibbonButton icon={<Italic size={15} />} label={t('기울임')} pressed={active('italic')} disabled={off} onClick={() => chain().toggleItalic().run()} />
          <RibbonButton icon={<Strikethrough size={15} />} label={t('취소선')} pressed={active('strike')} disabled={off} onClick={() => chain().toggleStrike().run()} />
          <RibbonButton icon={<Code size={15} />} label={t('코드')} pressed={active('code')} disabled={off} onClick={() => chain().toggleCode().run()} />
          <RibbonButton icon={<Link2 size={15} />} label={t('링크')} pressed={active('link')} disabled={off} onClick={editLink} />
        </RibbonGroup>
        <RibbonGroup label={t('단락')}>
          <div data-ribbon-stack="">
            <div>
              <RibbonButton icon={<List size={15} />} label={t('글머리 기호')} pressed={active('bulletList')} disabled={off} onClick={() => chain().toggleBulletList().run()} />
              <RibbonButton icon={<ListOrdered size={15} />} label={t('번호 매기기')} pressed={active('orderedList')} disabled={off} onClick={() => chain().toggleOrderedList().run()} />
              <RibbonButton icon={<ListChecks size={15} />} label={t('할 일 목록')} pressed={active('taskList')} disabled={off} onClick={() => chain().toggleTaskList().run()} />
            </div>
            <div>
              <RibbonButton icon={<Outdent size={15} />} label={t('한 단계 내어쓰기')} disabled={off || !(editor?.can().liftListItem('listItem') || editor?.can().liftListItem('taskItem'))} onClick={() => (editor?.can().liftListItem('taskItem') ? chain().liftListItem('taskItem') : chain().liftListItem('listItem')).run()} />
              <RibbonButton icon={<Indent size={15} />} label={t('한 단계 들여쓰기')} disabled={off || !(editor?.can().sinkListItem('listItem') || editor?.can().sinkListItem('taskItem'))} onClick={() => (editor?.can().sinkListItem('taskItem') ? chain().sinkListItem('taskItem') : chain().sinkListItem('listItem')).run()} />
              <RibbonButton icon={<Quote size={15} />} label={t('인용')} pressed={active('blockquote')} disabled={off} onClick={() => chain().toggleBlockquote().run()} />
            </div>
          </div>
        </RibbonGroup>
        <RibbonGroup label={t('스타일')}>
          <div role="radiogroup" aria-label={t('제목 단계')} className="flex items-stretch gap-1">
            {STYLES.map((row) => (
              <button
                key={row.level}
                type="button"
                role="radio"
                aria-checked={style === row.level}
                disabled={off}
                title={row.level ? `${'#'.repeat(row.level)} ${t(row.label)}` : t(row.label)}
                onMouseDown={(event) => {
                  event.preventDefault()
                  if (off) return
                  if (!row.level) chain().setParagraph().run()
                  else chain().setHeading({ level: row.level }).run()
                }}
                onClick={(event) => {
                  if (event.detail !== 0 || off) return
                  if (!row.level) chain().setParagraph().run()
                  else chain().setHeading({ level: row.level }).run()
                }}
                // A Word style tile: the sample on white paper, its name under it (inline: the group's button rules are a row).
                style={{
                  display: 'inline-flex', flexDirection: 'column', alignItems: 'flex-start', justifyContent: 'center', gap: 3,
                  height: 48, minWidth: 72, padding: '0 8px', background: '#fff', color: '#222',
                  border: '1px solid', borderColor: style === row.level ? 'var(--accent)' : 'var(--border)',
                  boxShadow: style === row.level ? 'inset 0 0 0 1px var(--accent)' : undefined,
                }}
                className="text-left disabled:opacity-40"
              >
                <span className={cn('leading-none', row.sample)}>{t('가나다')}</span>
                <span className="text-[10.5px] leading-none text-[#666]">{t(row.label)}</span>
              </button>
            ))}
          </div>
        </RibbonGroup>
        <RibbonGroup label={t('편집')}>
          <RibbonButton big icon={<Search size={16} />} label={t('찾기 및 바꾸기')} disabled={!editor} onClick={onFind} />
        </RibbonGroup>
        <RibbonGroup label={t('원문')}>{sourceToggle}</RibbonGroup>
      </>
    )
  }

  if (tab === 'insert') {
    const inTable = !off && active('table')
    return (
      <>
        <RibbonGroup label={t('표')}>
          <RibbonButton big icon={<TableIcon size={16} />} label={t('표 넣기')} disabled={off} onClick={() => chain().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()} />
          {inTable && (
            <div data-ribbon-stack="">
              <div>
                <RibbonButton icon={<span className="text-[10px] font-semibold">{t('행+')}</span>} label={t('아래에 행 추가')} onClick={() => chain().addRowAfter().run()} />
                <RibbonButton icon={<span className="text-[10px] font-semibold">{t('행−')}</span>} label={t('현재 행 삭제')} onClick={() => chain().deleteRow().run()} />
              </div>
              <div>
                <RibbonButton icon={<span className="text-[10px] font-semibold">{t('열+')}</span>} label={t('오른쪽에 열 추가')} onClick={() => chain().addColumnAfter().run()} />
                <RibbonButton icon={<span className="text-[10px] font-semibold">{t('열−')}</span>} label={t('현재 열 삭제')} onClick={() => chain().deleteColumn().run()} />
                <RibbonButton icon={<Trash2 size={14} />} label={t('표 지우기')} onClick={() => chain().deleteTable().run()} />
              </div>
            </div>
          )}
        </RibbonGroup>
        <RibbonGroup label={t('그림')}>
          <RibbonButton big icon={<ImagePlus size={16} />} label={t('그림 파일')} title={t('이 기기의 그림을 넣습니다')} disabled={off} onClick={insertPicture} />
        </RibbonGroup>
        <RibbonGroup label={t('도식')}>
          <RibbonButton
            big
            icon={<Workflow size={16} />}
            label={t('도식 넣기')}
            title={t('mermaid 도식을 넣습니다. 두 번 누르면 Markdown 원문에서 고칩니다')}
            disabled={off}
            onClick={() => chain().insertContent({ type: 'fenceBlock', attrs: { lang: 'mermaid', source: STARTER_DIAGRAM } }).run()}
          />
          <RibbonButton icon={<Code2 size={15} />} label={t('코드 블록')} pressed={active('codeBlock')} disabled={off} onClick={() => chain().toggleCodeBlock().run()} />
        </RibbonGroup>
        <RibbonGroup label={t('각주·출처')}>
          <select
            aria-label={t('출처 인용 넣기')}
            title={t('현재 커서에 출처 번호 넣기')}
            disabled={off || !sources.length}
            defaultValue=""
            className="h-8 max-w-40 rounded-control border border-line bg-panel px-2 text-xs text-fg disabled:opacity-40"
            onMouseDown={(event) => event.stopPropagation()}
            onChange={(event) => {
              const ordinal = Number(event.target.value)
              if (ordinal && editor) chain().insertContent(`[${ordinal}]`).run()
              event.currentTarget.value = ''
            }}
          >
            <option value="">{sources.length ? t('출처 인용') : t('출처 없음')}</option>
            {sources.map((source) => (
              <option key={source.id} value={source.ordinal}>
                [{source.ordinal}] {source.title}
              </option>
            ))}
          </select>
          <RibbonButton icon={<Brackets size={15} />} label={t('빈칸 넣기')} title={t('채울 자리를 [내용]으로 남깁니다')} disabled={off} onClick={() => chain().insertContent('[내용]').run()} />
        </RibbonGroup>
        <RibbonGroup label={t('구분')}>
          <RibbonButton big icon={<Minus size={16} />} label={t('구분선')} disabled={off} onClick={() => chain().setHorizontalRule().run()} />
          <RibbonButton big icon={<SeparatorHorizontal size={16} />} label={t('쪽 나누기')} disabled={off} onClick={() => chain().insertContent({ type: 'pageBreak' }).run()} />
        </RibbonGroup>
      </>
    )
  }

  if (tab === 'review') {
    return (
      <RibbonGroup label={t('메모')}>
        <RibbonButton big icon={<MessageSquarePlus size={16} />} label={t('새 메모')} title={t('선택한 문장에 메모')} disabled={off || Boolean(editor?.state.selection.empty)} onClick={onComment} />
        <RibbonButton big icon={<MessagesSquare size={16} />} label={`${t('검토 메모')} ${openComments}`} pressed={commentsOpen} onClick={onComments} />
      </RibbonGroup>
    )
  }

  return (
    <>
      <RibbonGroup label={t('탐색')}>
        <RibbonButton big icon={<ListTree size={16} />} label={t('문서 개요')} pressed={outlineOpen} onClick={onOutline} />
      </RibbonGroup>
      <RibbonGroup label={t('원문')}>{sourceToggle}</RibbonGroup>
    </>
  )
}
