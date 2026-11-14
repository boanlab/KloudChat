import { FileImage, FileSpreadsheet, FileText, File as FileIcon, Search } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Input, Tabs } from '@/components/ui'
import type { FileRow } from '@/lib/api'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'
import { fileSize, type FileOrigin } from './fileBox'
import { RelTime } from '@/components/ui/RelTime'

/** The file's kind at a glance: picture, table, document, other. */
function FileGlyph({ row }: { row: FileRow }) {
  const props = { size: 16, className: 'shrink-0 text-accent' }
  if (row.mime.startsWith('image/')) return <FileImage {...props} />
  if (/csv|spreadsheet|excel/.test(row.mime) || /\.(csv|xlsx?)$/i.test(row.name)) return <FileSpreadsheet {...props} />
  if (row.mime.startsWith('text/') || /pdf|word|document|presentation/.test(row.mime)) return <FileText {...props} />
  return <FileIcon {...props} />
}

/** 올린 파일 / 대화에서 만든 파일 tabs and the name search over them. */
export function FileBoxFilter({
  counts,
  origin,
  onOrigin,
  query,
  onQuery,
}: {
  /** Per origin, for the current search: the server counts every file, not just the page held. */
  counts: Record<FileOrigin, number>
  origin: FileOrigin
  onOrigin: (next: FileOrigin) => void
  query: string
  onQuery: (next: string) => void
}) {
  const t = useT()
  return (
    <>
      <div className="relative mb-3 max-w-sm">
        <Search size={14} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-faint" />
        <Input
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          aria-label={t('파일 검색')}
          placeholder={t('파일 이름으로 찾기')}
          className="pl-8"
        />
      </div>
      <Tabs<FileOrigin>
        value={origin}
        onChange={onOrigin}
        tabs={[
          { id: 'upload', label: t('올린 파일'), count: counts.upload },
          { id: 'generated', label: t('대화에서 만든 파일'), count: counts.generated },
        ]}
      />
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
      </div>
      {actions && <div className="flex shrink-0 items-center max-sm:-mt-1 max-sm:basis-full max-sm:justify-end">{actions}</div>}
    </div>
  )
}
