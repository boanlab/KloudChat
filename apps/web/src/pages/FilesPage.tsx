import { Download, FolderOpen, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { FileBoxFilter, FileLine } from '@/components/files/FileBoxList'
import { shownFiles, useFileBox, type FileOrigin } from '@/components/files/fileBox'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Button, Card, ConfirmDialog, EmptyState, LoadingState, PageHeader, ReloadNotice } from '@/components/ui'
import { BulkBar, PickBox, useBulkSelect } from '@/components/ui/BulkSelect'
import { downloadFile, errorMessage, filesApi, type FileRow } from '@/lib/api'
import { useT } from '@/lib/useT'

/** 파일함: every file of the account, those the person gave apart from those made in conversations. */
export function FilesPage() {
  const t = useT()
  const box = useFileBox()
  const [origin, setOrigin] = useState<FileOrigin>('upload')
  const [query, setQuery] = useState('')
  const [confirming, setConfirming] = useState<FileRow | null>(null)
  const [error, setError] = useState<string | null>(null)
  const shown = useMemo(() => shownFiles(box.rows, origin, query), [box.rows, origin, query])
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
      box.setRows((rows) => rows.filter((row) => !gone.includes(row.id)))
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
        <FileBoxFilter rows={box.rows} origin={origin} onOrigin={(next) => { setOrigin(next); pick.clear() }} query={query} onQuery={setQuery} />
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
            </>
          )}
        </div>
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
