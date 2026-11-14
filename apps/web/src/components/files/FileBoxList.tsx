import { FileImage, FileSpreadsheet, FileText, File as FileIcon, Loader2, Search } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Input, Tabs } from '@/components/ui'
import type { FileRow } from '@/lib/api'
import { ocrNote } from '@/lib/ocr'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'
import { fileSize, type FileBoxCounts, type FileBoxTab } from './fileBox'
import { RelTime } from '@/components/ui/RelTime'

/** The file's kind at a glance: picture, table, document, other. */
function FileGlyph({ row }: { row: FileRow }) {
  const props = { size: 16, className: 'shrink-0 text-accent' }
  if (row.mime.startsWith('image/')) return <FileImage {...props} />
  if (/csv|spreadsheet|excel/.test(row.mime) || /\.(csv|xlsx?)$/i.test(row.name)) return <FileSpreadsheet {...props} />
  if (row.mime.startsWith('text/') || /pdf|word|document|presentation/.test(row.mime)) return <FileText {...props} />
  return <FileIcon {...props} />
}

/** 올린 파일 / 대화에서 만든 파일 (and, in the file box itself, 작업 문서) tabs and the search over them. */
export function FileBoxFilter<T extends FileBoxTab>({
  counts,
  origin,
  onOrigin,
  query,
  onQuery,
  working = false,
}: {
  /** Per tab, for the current search: the server counts every file, not just the page held. */
  counts: FileBoxCounts
  origin: T
  onOrigin: (next: T) => void
  query: string
  onQuery: (next: string) => void
  /** The 「작업 문서」 tab too: the file box page, not a picker. */
  working?: boolean
}) {
  const t = useT()
  const tabs = [
    { id: 'upload', label: t('올린 파일'), count: counts.upload },
    { id: 'generated', label: t('대화에서 만든 파일'), count: counts.generated },
    ...(working ? [{ id: 'working', label: t('작업 문서'), count: counts.working }] : []),
  ] as { id: T; label: string; count?: number }[]
  return (
    <>
      <div className="relative mb-3 max-w-sm">
        <Search size={14} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-faint" />
        <Input
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          aria-label={t('파일 검색')}
          placeholder={origin === 'working' ? t('문서 제목으로 찾기') : t('파일 이름으로 찾기')}
          className="pl-8"
        />
      </div>
      <Tabs<T> value={origin} onChange={onOrigin} tabs={tabs} />
    </>
  )
}

/** Where a file lives: its project, its conversation, or a conversation since deleted. */
function FilePlace({ row }: { row: FileRow }) {
  const t = useT()
  const project = useStore((s) => s.projects.find((p) => p.id === row.projectId))
  const session = useStore((s) => s.sessions.find((c) => c.id === row.sessionId))
  if (project) {
    return (
      <Link to={`/projects/${project.id}`} className="inline-block max-w-full truncate align-middle leading-6 hover:text-fg hover:underline">
        {project.emoji} {project.name}
      </Link>
    )
  }
  if (session) {
    return (
      <Link to={`/s/${session.id}`} className="inline-block max-w-full truncate align-middle leading-6 hover:text-fg hover:underline">
        {session.title || t('제목 없는 대화')}
      </Link>
    )
  }
  return <span className="truncate">{row.sessionId ? t('지운 대화') : t('대화 밖에서 올림')}</span>
}

/** How far a scan or a video is read (「읽는 중」), or how it was read; nothing for others. */
function FileReading({ row }: { row: FileRow }) {
  const t = useT()
  const note = ocrNote(row, t)
  if (!note) return null
  return (
    <p
      data-testid="file-reading"
      data-media={row.mediaState ?? undefined}
      data-ocr={row.ocrState ?? undefined}
      className={cn('flex items-center gap-1 text-xs', note.tone === 'warn' ? 'text-warn' : 'text-faint')}
    >
      {note.tone === 'busy' && <Loader2 size={11} className="animate-spin" />}
      {note.text}
    </p>
  )
}

/** One file: icon, name, size · when · where, and the caller's controls. `onOpen` makes the name open it. */
export function FileLine({
  row,
  lead,
  actions,
  onOpen,
}: {
  row: FileRow
  lead?: ReactNode
  actions?: ReactNode
  onOpen?: () => void
}) {
  return (
    // A phone moves the controls under the name, so the name keeps the row's width.
    <div data-testid="file-row" className="flex flex-wrap items-center gap-x-2 border-b border-line px-1 py-2 last:border-b-0 sm:flex-nowrap">
      {lead}
      <FileGlyph row={row} />
      <div className="min-w-0 flex-1">
        {onOpen ? (
          <button
            type="button"
            onClick={onOpen}
            className="block min-h-6 max-w-full truncate text-left text-base font-medium hover:underline max-sm:line-clamp-2 max-sm:whitespace-normal max-sm:break-all"
            title={row.name}
          >
            {row.name}
          </button>
        ) : (
          <p className="truncate text-base font-medium max-sm:line-clamp-2 max-sm:whitespace-normal max-sm:break-all" title={row.name}>{row.name}</p>
        )}
        <p className="flex min-w-0 flex-wrap items-center gap-x-2 text-xs text-faint">
          <span>{fileSize(row.size)}</span>
          <RelTime iso={row.createdAt} />
          <FilePlace row={row} />
        </p>
        <FileReading row={row} />
      </div>
      {actions && <div className="flex shrink-0 items-center max-sm:-mt-1 max-sm:basis-full max-sm:justify-end">{actions}</div>}
    </div>
  )
}
