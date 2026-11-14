import { Download, FilePenLine, FileStack, FileText, Loader2, MessageSquare, Pencil, Presentation, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Badge, Button, Card, ConfirmDialog, EmptyState, Input, Modal, ReloadNotice, Select } from '@/components/ui'
import { BulkBar, PickBox, useBulkSelect } from '@/components/ui/BulkSelect'
import { RelTime } from '@/components/ui/RelTime'
import { LoadMore } from '@/components/ui/ShowMore'
import { ListSkeleton } from '@/components/ui/Skeleton'
import { artifactsApi, errorMessage, workingDocsApi, type WorkingDocRow, type WorkingKind, type WorkingSort } from '@/lib/api'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { WORKING_KINDS, useWorkingDocs } from './useWorkingDocs'

const KIND_LABEL: Record<WorkingKind, string> = {
  hwp: '한글 문서',
  docx: 'Word 문서',
  pptx: 'PowerPoint 문서',
  report: '웹 보고서',
  deck: '웹 슬라이드',
}

function KindGlyph({ kind }: { kind: WorkingKind }) {
  const Icon = kind === 'pptx' || kind === 'deck' ? Presentation : FileText
  return <Icon size={16} className="shrink-0 text-accent" aria-hidden />
}

/** Opens a document where it is edited from chat: beside its conversation; an office file with
 *  none joins a new chat, a web report or web slides with none opens in the artifacts panel. */
function useOpenInEditor() {
  const navigate = useNavigate()
  const newSession = useStore((s) => s.newSession)
  const ensureSession = useStore((s) => s.ensureSession)
  const refreshArtifact = useStore((s) => s.refreshArtifact)
  return async (row: WorkingDocRow) => {
    if (row.sessionId) {
      navigate(`/s/${row.sessionId}?artifact=${row.id}`)
      return
    }
    if (row.kind === 'report' || row.kind === 'deck') {
      navigate(`/artifacts?open=${row.id}`)
      return
    }
    const sessionId = await ensureSession(await newSession('chat', { projectId: row.projectId }))
    await artifactsApi.attachDocument(row.kind, row.id, sessionId)
    await refreshArtifact(row.id)
    navigate(`/s/${sessionId}?artifact=${row.id}`)
  }
}

/** Where a document belongs: its project and its conversation, or neither. */
function DocPlace({ row }: { row: WorkingDocRow }) {
  const t = useT()
  return (
    <>
      {row.projectId && row.projectName && (
        <Link to={`/projects/${row.projectId}`} className="inline-block max-w-full truncate align-middle leading-6 hover:text-fg hover:underline">
          {row.projectEmoji} {row.projectName}
        </Link>
      )}
      {row.sessionId ? (
        <Link to={`/s/${row.sessionId}`} className="inline-block max-w-full truncate align-middle leading-6 hover:text-fg hover:underline">
          {row.sessionTitle || t('제목 없는 대화')}
        </Link>
      ) : (
        <span className="truncate">{t('대화 없음')}</span>
      )}
    </>
  )
}

/**
 * 「작업 문서」: the HWP, Word and PowerPoint files and the web reports and web slides the person
 * works on, one row per document (not per version), newest edit first. Each opens in the editor
 * it has in chat; the rows point at the documents themselves, so an edit there shows here.
 */
export function WorkingDocs({ query, onCount }: { query: string; onCount: (n: number) => void }) {
  const t = useT()
  const navigate = useNavigate()
  const [kind, setKind] = useState<WorkingKind | null>(null)
  const [sort, setSort] = useState<WorkingSort>('updated')
  const docs = useWorkingDocs(true, kind, query, sort)
  const pick = useBulkSelect(docs.rows)
  const openInEditor = useOpenInEditor()
  const [opening, setOpening] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<WorkingDocRow | null>(null)
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [confirming, setConfirming] = useState<WorkingDocRow | null>(null)

  const all = docs.all
  useEffect(() => {
    if (all !== null) onCount(all)
  }, [all, onCount])

  const open = async (row: WorkingDocRow) => {
    setError(null)
    setOpening(row.id)
    try {
      await openInEditor(row)
    } catch (err) {
      setError(errorMessage(err, t('편집기에서 열지 못했습니다.')))
      setOpening(null)
    }
  }

  // the store's copy (an open panel, the artifacts page) follows what changed here
  const forget = (ids: string[]) =>
    useStore.setState((s) => ({ artifacts: s.artifacts.filter((a) => !ids.includes(a.id)) }))

  const remove = async (ids: string[]) => {
    setError(null)
    try {
      if (ids.length === 1) await artifactsApi.remove(ids[0])
      else await artifactsApi.removeMany(ids)
      docs.removeRows(ids)
      forget(ids)
    } catch (err) {
      setError(errorMessage(err, t('삭제하지 못했습니다.')))
      void docs.load()
    }
  }

  const rename = async () => {
    const row = renaming
    const title = name.trim()
    if (!row || !title) return
    setSaving(true)
    setError(null)
    try {
      const saved = await artifactsApi.update(row.id, { title })
      docs.updateRow({ ...row, title: saved.title, updatedAt: saved.updatedAt })
      useStore.setState((s) => ({
        artifacts: s.artifacts.map((a) => (a.id === row.id ? { ...a, title: saved.title } : a)),
      }))
      setRenaming(null)
    } catch (err) {
      setError(errorMessage(err, t('이름을 바꾸지 못했습니다.')))
    } finally {
      setSaving(false)
    }
  }

  const count = (k: WorkingKind) => docs.counts?.[k]
  const shown = docs.rows

  return (
    <div data-testid="working-docs">
      {/* A phone puts the order under the format chips, so the chips keep the row's width. */}
      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
        <div className="flex min-w-0 flex-wrap gap-1.5 sm:flex-1" role="group" aria-label={t('형식')}>
          {[{ kind: null, label: '모든 형식' }, ...WORKING_KINDS].map((row) => (
            <button
              key={row.kind ?? 'all'}
              type="button"
              aria-pressed={kind === row.kind}
              onClick={() => {
                setKind(row.kind)
                pick.clear()
              }}
              className={cn(
                'h-8 rounded-full border px-3 text-sm transition-colors',
                kind === row.kind
                  ? 'border-accent bg-accent-soft text-accent'
                  : 'border-line text-muted hover:bg-elevated hover:text-fg',
              )}
            >
              {t(row.label)}
              {row.kind && count(row.kind) !== undefined && (
                <span className="ml-1 tabular-nums text-faint">{count(row.kind)}</span>
              )}
            </button>
          ))}
        </div>
        <Select
          value={sort}
          onChange={(e) => {
            setSort(e.target.value as WorkingSort)
            pick.clear()
          }}
          aria-label={t('정렬')}
          className="sm:w-auto"
        >
          <option value="updated">{t('최근 수정순')}</option>
          <option value="oldest">{t('오래된 수정순')}</option>
          <option value="title">{t('제목순')}</option>
        </Select>
      </div>
      {docs.failed && <ReloadNotice onRetry={() => void docs.load()} />}
      {error && <p role="alert" className="pt-3 text-base text-danger">{error}</p>}
      <div className="pt-4">
        {docs.loading ? (
          <ListSkeleton />
        ) : docs.failed ? null : shown.length === 0 ? (
          <EmptyState
            icon={<FileStack size={18} />}
            title={query.trim() || kind ? t('찾는 문서가 없습니다') : t('작업 문서가 없습니다')}
            description={
              query.trim() || kind
                ? t('제목의 다른 부분이나 다른 형식으로 찾아보세요.')
                : t('대화에서 만들거나 편집기에서 고친 한글·Word·PowerPoint 문서와 웹 보고서·웹 슬라이드가 여기에 모입니다.')
            }
          />
        ) : (
          <>
            <BulkBar
              count={pick.count}
              allPicked={pick.allPicked}
              onToggleAll={pick.toggleAll}
              onClear={pick.clear}
              title={t('문서')}
              note={t('버전 기록도 함께 사라집니다. 문서를 만든 대화는 그대로 남습니다.')}
              onDelete={async () => {
                await remove(pick.ids)
                pick.clear()
              }}
            />
            <Card className="px-3 py-1">
              {shown.map((row) => (
                // A phone moves the controls under the title, so the title keeps the row's width.
                <div
                  key={row.id}
                  data-testid="working-doc-row"
                  data-kind={row.kind}
                  className="flex flex-wrap items-center gap-x-2 border-b border-line px-1 py-2 last:border-b-0 sm:flex-nowrap"
                >
                  <PickBox
                    checked={pick.picked.has(row.id)}
                    onChange={() => pick.toggle(row.id)}
                    label={t('{name} 선택').replace('{name}', row.title)}
                  />
                  <KindGlyph kind={row.kind} />
                  <div className="min-w-0 flex-1">
                    <button
                      type="button"
                      onClick={() => void open(row)}
                      disabled={opening === row.id}
                      className="block min-h-6 max-w-full truncate text-left text-base font-medium hover:underline max-sm:line-clamp-2 max-sm:whitespace-normal max-sm:break-all"
                      title={row.title}
                    >
                      {row.title || t('제목 없는 문서')}
                    </button>
                    <p className="flex min-w-0 flex-wrap items-center gap-x-2 text-xs text-faint">
                      <Badge>{t(KIND_LABEL[row.kind])}</Badge>
                      <span title={t('버전 {n}개').replace('{n}', String(row.version))}>v{row.version}</span>
                      <RelTime iso={row.updatedAt} />
                      <DocPlace row={row} />
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center max-sm:-mt-1 max-sm:basis-full max-sm:justify-end">
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t('{name} 편집기에서 열기').replace('{name}', row.title)}
                      title={t('편집기에서 열기')}
                      disabled={opening === row.id}
                      onClick={() => void open(row)}
                    >
                      {opening === row.id ? <Loader2 size={14} className="animate-spin" /> : <FilePenLine size={14} />}
                    </Button>
                    {row.sessionId && (
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={t('{name} 대화로 가기').replace('{name}', row.title)}
                        title={t('대화로 가기')}
                        onClick={() => navigate(`/s/${row.sessionId}`)}
                      >
                        <MessageSquare size={14} />
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t('{name} 내려받기').replace('{name}', row.title)}
                      title={t('내려받기')}
                      onClick={() => void workingDocsApi.download(row).catch((err) => setError(errorMessage(err, t('내려받지 못했습니다.'))))}
                    >
                      <Download size={14} />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t('{name} 이름 바꾸기').replace('{name}', row.title)}
                      title={t('이름 바꾸기')}
                      onClick={() => {
                        setName(row.title)
                        setRenaming(row)
                      }}
                    >
                      <Pencil size={14} />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t('{name} 삭제').replace('{name}', row.title)}
                      title={t('이 문서를 삭제합니다')}
                      onClick={() => setConfirming(row)}
                    >
                      <Trash2 size={14} />
                    </Button>
                  </div>
                </div>
              ))}
            </Card>
            {docs.hasMore && (
              <LoadMore
                remaining={docs.total - shown.length}
                loading={docs.loadingMore}
                failed={docs.moreFailed}
                failedText={t('다음 문서를 불러오지 못했습니다.')}
                onMore={() => void docs.loadMore()}
              />
            )}
          </>
        )}
      </div>
      <Modal
        open={!!renaming}
        onClose={() => setRenaming(null)}
        title={t('이름 바꾸기')}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRenaming(null)}>{t('취소')}</Button>
            <Button variant="primary" disabled={saving || !name.trim()} onClick={() => void rename()}>
              {t('저장')}
            </Button>
          </>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void rename()
          }}
        >
          <Input
            value={name}
            maxLength={200}
            autoFocus
            aria-label={t('문서 이름')}
            onChange={(e) => setName(e.target.value)}
          />
        </form>
      </Modal>
      <ConfirmDialog
        open={!!confirming}
        onClose={() => setConfirming(null)}
        onConfirm={() => confirming && void remove([confirming.id])}
        title={t('{name} 삭제').replace('{name}', confirming?.title ?? '')}
        description={t('되돌릴 수 없습니다. 버전 기록도 함께 사라집니다. 문서를 만든 대화는 그대로 남습니다.')}
      />
    </div>
  )
}
