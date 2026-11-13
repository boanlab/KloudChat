import { Download, Eye, FolderOpen, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { FileBoxFilter, FileLine } from '@/components/files/FileBoxList'
import { FilePreview } from '@/components/files/FilePreview'
import { useFileBox, type FileOrigin } from '@/components/files/fileBox'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Button, Card, ConfirmDialog, EmptyState, LoadingState, PageHeader, ReloadNotice } from '@/components/ui'
import { BulkBar, PickBox, useBulkSelect } from '@/components/ui/BulkSelect'
import { LoadMore } from '@/components/ui/ShowMore'
import { downloadFile, errorMessage, filesApi, type FileRow } from '@/lib/api'
import { useT } from '@/lib/useT'

/** 파일함: every file of the account, those the person gave apart from those made in conversations. */
export function FilesPage() {
  const t = useT()
  const [origin, setOrigin] = useState<FileOrigin>('upload')
  const [query, setQuery] = useState('')
  // The server pages, filters by origin and searches names over every file of the account.
  const box = useFileBox(true, origin, query)
  const [confirming, setConfirming] = useState<FileRow | null>(null)
  const [previewing, setPreviewing] = useState<FileRow | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 「/files?open=<id>&t=<seconds>」: a link into a file (a video at a moment) opens its preview
  const [params, setParams] = useSearchParams()
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
          description={t('올린 파일과 대화에서 만든 파일이 모두 여기 모입니다. 새 대화·보고서·슬라이드에서 「파일함에서 고르기」로 다시 붙일 수 있습니다.')}
        />
        <FileBoxFilter counts={box.counts} origin={origin} onOrigin={(next) => { setOrigin(next); pick.clear() }} query={query} onQuery={setQuery} />
        {box.failed && <ReloadNotice onRetry={() => void box.load()} />}
        {error && <p className="pt-3 text-base text-danger">{error}</p>}
        <div className="pt-4">
          {box.loading ? (
            <LoadingState />
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
              <Card className="px-3 py-1">
                {shown.map((row) => (
                  <FileLine
                    key={row.id}
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
