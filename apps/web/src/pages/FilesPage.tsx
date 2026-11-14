import { Download, Eye, FolderOpen, Trash2, Upload } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { FILE_SORT_FIELDS, FileBoxFilter, FileColumns, FileLine } from '@/components/files/FileBoxList'
import { PhoneSort, useUrlSort } from '@/components/files/ListSort'
import { FilePreview } from '@/components/files/FilePreview'
import { useFileBox, type FileBoxTab } from '@/components/files/fileBox'
import { WorkingDocs } from '@/components/files/WorkingDocs'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Button, Card, ConfirmDialog, EmptyState, PageHeader, ReloadNotice } from '@/components/ui'
import { BulkBar, PickBox, useBulkSelect } from '@/components/ui/BulkSelect'
import { LoadMore } from '@/components/ui/ShowMore'
import { downloadFile, errorMessage, filesApi, type FileRow } from '@/lib/api'
import { ATTACH_ACCEPT, attachRefusal } from '@/lib/attachable'
import { mediaPreflight } from '@/lib/media'
import { useOcrProgress } from '@/lib/ocr'
import { useT } from '@/lib/useT'
import { ListSkeleton } from '@/components/ui/Skeleton'

/** 파일함: every file of the account, those the person gave apart from those made in
 *  conversations, and the documents they work on (「작업 문서」, `?tab=working`). */
export function FilesPage() {
  const t = useT()
  const [params, setParams] = useSearchParams()
  const [tab, setTab] = useState<FileBoxTab>(params.get('tab') === 'working' ? 'working' : 'upload')
  const origin = tab === 'working' ? 'upload' : tab
  const working = tab === 'working'
  const [query, setQuery] = useState('')
  // column sorting, kept in the address; the server sorts every file, not the page held
  const sort = useUrlSort(FILE_SORT_FIELDS, { field: 'created', dir: 'desc' })
  // The server pages, filters by origin and searches names over every file of the account.
  const box = useFileBox(true, origin, query, sort.field, sort.dir)
  // the 「작업 문서」 badge: the files page counts it, the tab's own list once it has answered
  const [workingSeen, setWorkingSeen] = useState<{ q: string; n: number } | null>(null)
  const noteWorking = useCallback((n: number) => setWorkingSeen({ q: query.trim(), n }), [query])
  const counts = workingSeen && workingSeen.q === query.trim() ? { ...box.counts, working: workingSeen.n } : box.counts
  const [confirming, setConfirming] = useState<FileRow | null>(null)
  const [previewing, setPreviewing] = useState<FileRow | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 「/files?open=<id>&t=<seconds>」: a link into a file (a video at a moment) opens its preview
  const opening = params.get('open')
  const at = Number(params.get('t'))
  const start = opening && Number.isFinite(at) && at > 0 ? at : undefined
  useEffect(() => {
    if (!opening) return
    let live = true
    filesApi.get(opening)
      .then((row) => live && setPreviewing(row))
      .catch((err) => live && setError(errorMessage(err, t('파일을 열지 못했습니다.'))))
    return () => {
      live = false
    }
  }, [opening, t])
  const closePreview = () => {
    setPreviewing(null)
    if (opening) setParams({}, { replace: true })
  }
  const shown = box.rows
  const pick = useBulkSelect(shown)
  // scans and videos still being read follow their reading here too
  useOcrProgress(shown, box.updateRow)
  const fileInput = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState<{ name: string; sent: number; total: number } | null>(null)
  const [notes, setNotes] = useState<string[]>([])

  /** Files put straight into the box, outside any conversation; a video goes up in pieces. */
  const upload = async (picked: File[]) => {
    setError(null)
    const problems: string[] = []
    for (const file of picked) {
      const refusal = attachRefusal(file, t)
      if (refusal) {
        problems.push(`${file.name} — ${refusal}`)
        continue
      }
      setUploading({ name: file.name, sent: 0, total: file.size })
      const { note, refusal: tooLong } = await mediaPreflight(file, t).catch(() => ({ note: null, refusal: null }))
      if (tooLong) {
        problems.push(`${file.name} — ${tooLong}`)
        continue
      }
      setNotes(note ? [note] : [])
      try {
        const row = await filesApi.upload(file, {
          onProgress: (sent, total) => setUploading({ name: file.name, sent, total }),
        })
        if (tab !== 'upload') setTab('upload')
        box.addRow(row)
      } catch (err) {
        problems.push(`${file.name} — ${errorMessage(err, t('올리지 못했습니다.'))}`)
      }
    }
    setUploading(null)
    setNotes([])
    if (problems.length) setError(problems.join(' '))
  }

  const remove = async (ids: string[]) => {
    setError(null)
    const gone: string[] = []
    try {
      for (const id of ids) {
        await filesApi.remove(id)
        gone.push(id)
      }
    } catch (err) {
      setError(errorMessage(err, t('삭제하지 못했습니다.')))
    } finally {
      box.removeRows(gone)
    }
  }

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('파일함')}</span>} />
      <PageBody>
        <PageHeader
          title={t('파일함')}
          description={t('올린 파일과 대화에서 만든 파일이 모두 여기 모입니다. 새 대화·보고서·슬라이드에서 「파일함에서 고르기」로 다시 붙일 수 있고, 작업하던 문서는 「작업 문서」에서 다시 엽니다.')}
          action={
            <>
              <input
                ref={fileInput}
                type="file"
                multiple
                accept={ATTACH_ACCEPT}
                className="hidden"
                aria-label={t('파일함에 올릴 파일')}
                onChange={(e) => {
                  const picked = Array.from(e.target.files ?? [])
                  e.target.value = ''
                  void upload(picked)
                }}
              />
              <Button size="sm" disabled={!!uploading} onClick={() => fileInput.current?.click()}>
                <Upload size={14} className={uploading ? 'animate-pulse' : undefined} />
                {uploading ? t('업로드 중') : t('파일 올리기')}
              </Button>
            </>
          }
        />
        {uploading && (
          <div data-testid="upload-progress" className="space-y-1 pb-2 text-xs text-faint">
            <p>
              {t('{name} 올리는 중 {percent}%')
                .replace('{name}', uploading.name)
                .replace('{percent}', String(uploading.total ? Math.floor((uploading.sent / uploading.total) * 100) : 0))}
            </p>
            <div className="h-1 overflow-hidden rounded bg-elevated">
              <div className="h-full bg-accent" style={{ width: `${uploading.total ? (uploading.sent / uploading.total) * 100 : 0}%` }} />
            </div>
            {notes.map((note) => <p key={note} role="status" data-testid="media-estimate" className="text-muted">{note}</p>)}
          </div>
        )}
        <FileBoxFilter
          working
          counts={counts}
          origin={tab}
          onOrigin={(next) => {
            setTab(next)
            pick.clear()
            setParams(next === 'working' ? { tab: 'working' } : {}, { replace: true })
          }}
          query={query}
          onQuery={setQuery}
        />
        {working && <WorkingDocs query={query} onCount={noteWorking} />}
        {!working && box.failed && <ReloadNotice onRetry={() => void box.load()} />}
        {!working && error && <p className="pt-3 text-base text-danger">{error}</p>}
        {!working && (
        <div className="pt-4">
          {box.loading ? (
            <ListSkeleton />
          ) : shown.length === 0 ? (
            <EmptyState
              icon={<FolderOpen size={18} />}
              title={query ? t('찾는 파일이 없습니다') : origin === 'upload' ? t('올린 파일이 없습니다') : t('대화에서 만든 파일이 없습니다')}
              description={
                query
                  ? t('이름의 다른 부분으로 찾아보세요.')
                  : origin === 'upload'
                    ? t('대화·프로젝트에 올린 파일이 여기에 모입니다.')
                    : t('코드 실행 결과, 이미지, 차트처럼 대화에서 만든 파일이 여기에 모입니다.')
              }
            />
          ) : (
            <>
              <BulkBar
                count={pick.count}
                allPicked={pick.allPicked}
                onToggleAll={pick.toggleAll}
                onClear={pick.clear}
                title={t('파일')}
                note={t('프로젝트 파일이면 프로젝트에서도 빠집니다. 그 파일을 쓴 대화의 기록은 남습니다.')}
                onDelete={async () => {
                  await remove(pick.ids)
                  pick.clear()
                }}
              />
              <PhoneSort
                className="mb-2"
                fields={FILE_SORT_FIELDS.map((f) => (f.id === 'created' && origin === 'generated' ? { ...f, label: '만든 때' } : f))}
                sort={sort}
                onSort={sort.set}
              />
              <Card role="table" aria-label={origin === 'upload' ? t('올린 파일') : t('대화에서 만든 파일')} className="px-3 py-1">
                <FileColumns sort={sort} onSort={sort.click} made={origin === 'generated'} />
                {shown.map((row) => (
                  <FileLine
                    key={row.id}
                    columns
                    row={row}
                    onOpen={() => setPreviewing(row)}
                    lead={
                      <PickBox
                        checked={pick.picked.has(row.id)}
                        onChange={() => pick.toggle(row.id)}
                        label={t('{name} 선택').replace('{name}', row.name)}
                      />
                    }
                    actions={
                      <>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('{name} 미리보기').replace('{name}', row.name)}
                          title={t('미리보기')}
                          onClick={() => setPreviewing(row)}
                        >
                          <Eye size={14} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('{name} 내려받기').replace('{name}', row.name)}
                          title={t('내려받기')}
                          onClick={() => void downloadFile(row.id, row.name).catch((err) => setError(errorMessage(err, t('내려받지 못했습니다.'))))}
                        >
                          <Download size={14} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('{name} 삭제').replace('{name}', row.name)}
                          title={t('이 파일을 삭제합니다')}
                          onClick={() => setConfirming(row)}
                        >
                          <Trash2 size={14} />
                        </Button>
                      </>
                    }
                  />
                ))}
              </Card>
              {box.hasMore && (
                <LoadMore
                  remaining={box.total - shown.length}
                  loading={box.loadingMore}
                  failed={box.moreFailed}
                  failedText={t('다음 파일을 불러오지 못했습니다.')}
                  onMore={() => void box.loadMore()}
                />
              )}
            </>
          )}
        </div>
        )}
        <FilePreview row={previewing} onClose={closePreview} start={previewing?.id === opening ? start : undefined} />
        <ConfirmDialog
          open={!!confirming}
          onClose={() => setConfirming(null)}
          onConfirm={() => confirming && void remove([confirming.id])}
          title={t('{name} 삭제').replace('{name}', confirming?.name ?? '')}
          description={t('되돌릴 수 없습니다. 프로젝트 파일이면 프로젝트에서도 빠집니다. 그 파일을 쓴 대화의 기록은 남습니다.')}
        />
      </PageBody>
    </>
  )
}
