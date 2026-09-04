import { Loader2, Trash2, X } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { Button, Modal } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { useT } from '@/lib/useT'

/** Row selection by id, pruned against the rows on screen. */
export function useBulkSelect<T extends { id: string }>(rows: T[]) {
  const [picked, setPicked] = useState<Set<string>>(new Set())

  const present = useMemo(() => new Set(rows.map((r) => r.id)), [rows])
  const ids = useMemo(() => [...picked].filter((id) => present.has(id)), [picked, present])

  const toggle = useCallback((id: string) => {
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const clear = useCallback(() => setPicked(new Set()), [])

  const allPicked = rows.length > 0 && rows.every((r) => picked.has(r.id))
  const toggleAll = useCallback(() => {
    setPicked(allPicked ? new Set() : new Set(rows.map((r) => r.id)))
  }, [allPicked, rows])

  return { picked, ids, count: ids.length, toggle, clear, allPicked, toggleAll }
}

/** Card checkbox; stops the click reaching the card. */
export function PickBox({
  checked,
  onChange,
  label,
  className = '',
}: {
  checked: boolean
  onChange: () => void
  label: string
  className?: string
}) {
  return (
    <label
      className={`grid size-8 shrink-0 cursor-pointer place-items-center ${className}`}
      onClick={(e) => e.stopPropagation()}
    >
      <input
        type="checkbox"
        checked={checked}
        aria-label={label}
        className="size-4 cursor-pointer accent-[var(--color-accent)]"
        onChange={(e) => {
          e.stopPropagation()
          onChange()
        }}
      />
    </label>
  )
}

/** Selection bar with bulk delete and confirmation. */
export function BulkBar({
  count,
  allPicked,
  onToggleAll,
  onClear,
  onDelete,
  title,
  note,
}: {
  count: number
  allPicked: boolean
  onToggleAll: () => void
  onClear: () => void
  onDelete: () => Promise<unknown>
  /** Noun for what is deleted: "프로젝트", "결과물". */
  title: string
  /** What the delete takes with it, shown in the confirmation. */
  note?: string
}) {
  const t = useT()
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (count === 0) return null

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      await onDelete()
      setConfirming(false)
    } catch (err) {
      setError(errorMessage(err, t('삭제하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2 rounded-card border border-accent/30 bg-accent-soft px-3 py-2 text-base">
        <span className="font-medium">{t('{n}개 선택됨').replace('{n}', String(count))}</span>
        <Button size="sm" onClick={onToggleAll}>
          {allPicked ? t('전체 해제') : t('전체 선택')}
        </Button>
        <Button size="sm" onClick={onClear}>
          <X size={13} />
          {t('선택 해제')}
        </Button>
        <Button size="sm" variant="danger" className="ml-auto" onClick={() => setConfirming(true)}>
          <Trash2 size={13} />
          {t('선택 삭제')}
        </Button>
      </div>

      <Modal
        open={confirming}
        onClose={() => setConfirming(false)}
        title={t('{n}개를 삭제할까요?').replace('{n}', String(count))}
        description={t('{title} {n}개가 사라집니다.')
          .replace('{title}', title)
          .replace('{n}', String(count))}
      >
        <p className="text-base text-danger">
          {t('되돌릴 수 없습니다.')}
          {note ? ` ${note}` : ''}
        </p>
        {error && <p className="mt-3 text-base text-danger">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={() => setConfirming(false)}>{t('취소')}</Button>
          <Button variant="danger" disabled={busy} onClick={() => void run()}>
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
            {t('삭제')}
          </Button>
        </div>
      </Modal>
    </>
  )
}
