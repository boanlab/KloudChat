import { useState } from 'react'
import { Button, LoadingState, Modal, ReloadNotice } from '@/components/ui'
import { LoadMore } from '@/components/ui/ShowMore'
import type { FileRow } from '@/lib/api'
import { useT } from '@/lib/useT'
import { FileBoxFilter, FileLine } from './FileBoxList'
import { useFileBox, type FileOrigin } from './fileBox'

/** 「파일함에서 고르기」: files already in the account attached again, nothing uploaded. */
export function FileBoxPicker({
  open,
  onClose,
  onPick,
  attached,
}: {
  open: boolean
  onClose: () => void
  onPick: (rows: FileRow[]) => void
  /** Already on the message: shown ticked and not added twice. */
  attached: string[]
}) {
  const t = useT()
  const [origin, setOrigin] = useState<FileOrigin>('upload')
  const [query, setQuery] = useState('')
  const box = useFileBox(open, origin, query)
  // Picked rows are kept whole: a search or another tab may no longer list them.
  const [picked, setPicked] = useState<Map<string, FileRow>>(new Map())
  const shown = box.rows
  const close = () => {
    setPicked(new Map())
    setQuery('')
    onClose()
  }
  const toggle = (row: FileRow) =>
    setPicked((current) => {
      const next = new Map(current)
      if (next.has(row.id)) next.delete(row.id)
      else next.set(row.id, row)
      return next
    })
  const chosen = [...picked.values()].filter((row) => !attached.includes(row.id))

  return (
    <Modal
      open={open}
      onClose={close}
      title={t('파일함에서 고르기')}
      description={t('올렸거나 대화에서 만든 파일을 이 메시지에 다시 붙입니다.')}
      width="max-w-2xl"
      footer={
        <>
          <Button onClick={close}>{t('취소')}</Button>
          <Button
            variant="primary"
            disabled={chosen.length === 0}
            onClick={() => {
              onPick(chosen)
              close()
            }}
          >
            {chosen.length ? t('{n}개 붙이기').replace('{n}', String(chosen.length)) : t('붙이기')}
          </Button>
        </>
      }
    >
      <FileBoxFilter counts={box.counts} origin={origin} onOrigin={setOrigin} query={query} onQuery={setQuery} />
      {box.failed && <ReloadNotice onRetry={() => void box.load()} />}
      <div className="max-h-[50vh] overflow-y-auto">
        {box.loading ? (
          <LoadingState />
        ) : shown.length === 0 ? (
          <p className="py-6 text-center text-base text-muted">
            {query ? t('찾는 파일이 없습니다') : t('아직 파일이 없습니다')}
          </p>
        ) : (
          shown.map((row) => {
            const already = attached.includes(row.id)
            return (
              <FileLine
                key={row.id}
                row={row}
                lead={
                  <label className="grid size-8 shrink-0 cursor-pointer place-items-center">
                    <input
                      type="checkbox"
                      checked={already || picked.has(row.id)}
                      disabled={already}
                      aria-label={t('{name} 고르기').replace('{name}', row.name)}
                      className="size-4 cursor-pointer accent-[var(--color-accent)]"
                      onChange={() => toggle(row)}
                    />
                  </label>
                }
              />
            )
          })
        )}
        {box.hasMore && (
          <LoadMore
            remaining={box.total - shown.length}
            loading={box.loadingMore}
            failed={box.moreFailed}
            failedText={t('다음 파일을 불러오지 못했습니다.')}
            onMore={() => void box.loadMore()}
          />
        )}
      </div>
    </Modal>
  )
}
