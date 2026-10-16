import { FileImage, FileSpreadsheet, FileText, File as FileIcon, Search } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Input, Tabs } from '@/components/ui'
import type { FileRow } from '@/lib/api'
import { relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'
import { fileSize, originOf, type FileOrigin } from './fileBox'

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
  rows,
  origin,
  onOrigin,
  query,
  onQuery,
}: {
  rows: FileRow[]
  origin: FileOrigin
  onOrigin: (next: FileOrigin) => void
  query: string
  onQuery: (next: string) => void
}) {
  const t = useT()
  const count = (o: FileOrigin) => rows.filter((row) => originOf(row) === o).length
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
          { id: 'upload', label: t('올린 파일'), count: count('upload') },
          { id: 'generated', label: t('대화에서 만든 파일'), count: count('generated') },
        ]}
      />
    </>
  )
}

/** Where a file lives: its project, its conversation, or a conversation since deleted. */
export function FilePlace({ row }: { row: FileRow }) {
  const t = useT()
  const project = useStore((s) => s.projects.find((p) => p.id === row.projectId))
  const session = useStore((s) => s.sessions.find((c) => c.id === row.sessionId))
  if (project) {
    return (
      <Link to={`/projects/${project.id}`} className="truncate hover:text-fg hover:underline">
        {project.emoji} {project.name}
      </Link>
    )
  }
  if (session) {
    return (
      <Link to={`/s/${session.id}`} className="truncate hover:text-fg hover:underline">
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
    <div data-testid="file-row" className="flex items-center gap-2 border-b border-line px-1 py-2 last:border-b-0">
      {lead}
      <FileGlyph row={row} />
      <div className="min-w-0 flex-1">
        {onOpen ? (
          <button type="button" onClick={onOpen} className="block max-w-full truncate text-left text-base font-medium hover:underline" title={row.name}>
            {row.name}
          </button>
        ) : (
          <p className="truncate text-base font-medium" title={row.name}>{row.name}</p>
        )}
        <p className="flex min-w-0 flex-wrap items-center gap-x-2 text-xs text-faint">
          <span>{fileSize(row.size)}</span>
          <span>{relativeTime(row.createdAt)}</span>
          <FilePlace row={row} />
        </p>
      </div>
      {actions}
    </div>
  )
}
