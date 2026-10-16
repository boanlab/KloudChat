import { useMemo, useState } from 'react'
import { Button, LoadingState, Modal, ReloadNotice } from '@/components/ui'
import type { FileRow } from '@/lib/api'
import { useT } from '@/lib/useT'
import { FileBoxFilter, FileLine } from './FileBoxList'
import { shownFiles, useFileBox, type FileOrigin } from './fileBox'

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
  const box = useFileBox(open)
  const [origin, setOrigin] = useState<FileOrigin>('upload')
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const shown = useMemo(() => shownFiles(box.rows, origin, query), [box.rows, origin, query])
  const close = () => {
    setPicked(new Set())
    setQuery('')
    onClose()
  }
  const toggle = (id: string) =>
    setPicked((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const chosen = box.rows.filter((row) => picked.has(row.id) && !attached.includes(row.id))

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
      <FileBoxFilter rows={box.rows} origin={origin} onOrigin={setOrigin} query={query} onQuery={setQuery} />
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
                      onChange={() => toggle(row.id)}
                    />
                  </label>
                }
              />
            )
          })
        )}
      </div>
    </Modal>
  )
}
