import { MessageSquare, Search, Trash2, TriangleAlert } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Badge, Button, Card, EmptyState, Input, Modal } from '@/components/ui'
import { kindMeta } from '@/lib/kinds'
import { relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

/**
 * The full conversation list, and the place to tidy it. The sidebar deletes one
 * at a time; this clears a pile in one go.
 *
 * Nothing here is reversible, so the scope of an action is visible before the
 * button is pressed.
 */
export function HistoryPage() {
  const t = useT()
  const { sessions, deleteSessions } = useStore()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [confirmAll, setConfirmAll] = useState(false)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<number | null>(null)

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return sessions
    return sessions.filter((s) => (s.title || '').toLowerCase().includes(needle))
  }, [sessions, query])

  const allShownPicked = shown.length > 0 && shown.every((s) => picked.has(s.id))

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const removePicked = async () => {
    setBusy(true)
    try {
      const count = await deleteSessions({ ids: [...picked] })
      setPicked(new Set())
      setDone(count)
    } finally {
      setBusy(false)
    }
  }

  const removeAll = async () => {
    setBusy(true)
    try {
      const count = await deleteSessions({ all: true })
      setPicked(new Set())
      setConfirmAll(false)
      setDone(count)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <h1 className="text-2xl font-semibold tracking-tight">{t('대화 기록')}</h1>
      <p className="mt-1 text-[13px] text-muted">
        {t('대화 {n}개가 있습니다. 삭제한 대화는 되돌릴 수 없습니다. 대화에서 만든 아티팩트는 아티팩트 화면에 그대로 남습니다.').replace(
          '{n}',
          String(sessions.length),
        )}
      </p>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search size={14} className="absolute top-1/2 left-3 -translate-y-1/2 text-faint" />
          <Input
            aria-label={t('대화 검색')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('제목으로 찾기')}
            className="pl-9"
          />
        </div>
        <Button
          disabled={shown.length === 0}
          onClick={() =>
            setPicked((prev) => {
              const next = new Set(prev)
              // Scoped to what is on screen: a "select all" reaching past an
              // applied filter deletes things nobody saw.
              if (allShownPicked) shown.forEach((s) => next.delete(s.id))
              else shown.forEach((s) => next.add(s.id))
              return next
            })
          }
        >
          {allShownPicked ? t('선택 해제') : t('보이는 항목 전체 선택')}
        </Button>
        <Button variant="danger" disabled={busy || picked.size === 0} onClick={() => void removePicked()}>
          <Trash2 size={14} />
          {t('선택 {n}개 삭제').replace('{n}', String(picked.size))}
        </Button>
        <Button variant="danger" disabled={busy || sessions.length === 0} onClick={() => setConfirmAll(true)}>
          {t('모든 대화 삭제')}
        </Button>
      </div>

      {done !== null && (
        <p className="mt-3 rounded-lg border border-line bg-elevated px-3 py-2 text-[13px] text-muted">
          {done}개의 대화를 삭제했습니다.
        </p>
      )}

      <div className="mt-4 space-y-1.5">
        {shown.length === 0 ? (
          <EmptyState
            icon={<MessageSquare size={18} />}
            title={query ? t('검색 결과가 없습니다') : '아직 대화가 없습니다'}
          />
        ) : (
          shown.map((s) => {
            const meta = kindMeta[s.kind]
            return (
              <Card key={s.id} className="flex items-center gap-3 px-3 py-2.5">
                <input
                  type="checkbox"
                  aria-label={t('{title} 선택').replace('{title}', s.title || t('제목 없는 대화'))}
                  checked={picked.has(s.id)}
                  onChange={() => toggle(s.id)}
                  className="size-4 shrink-0 accent-[var(--accent)]"
                />
                <button
                  className="min-w-0 flex-1 text-left"
                  onClick={() => navigate(`/s/${s.id}`)}
                >
                  <span className="block truncate text-[13px] font-medium">
                    {s.title || t('제목 없는 대화')}
                  </span>
                  <span className="text-[11px] text-faint">{relativeTime(s.updatedAt)}</span>
                </button>
                <Badge>{t(meta?.label ?? s.kind)}</Badge>
              </Card>
            )
          })
        )}
      </div>

      <Modal
        open={confirmAll}
        onClose={() => setConfirmAll(false)}
        title={t('모든 대화를 삭제할까요?')}
        description={t('대화 {n}개와 그 안의 모든 메시지가 사라집니다.').replace('{n}', String(sessions.length))}
      >
        <div className="flex items-start gap-2 rounded-xl border border-danger/30 bg-danger/5 px-3 py-2.5 text-[13px] text-danger">
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          {/* Says what survives as well as what goes. "삭제" that also silently
              took the reports would be the wrong kind of surprise. */}
          <span>
            {t('되돌릴 수 없습니다. 아티팩트와 프로젝트, 메모리는 지워지지 않습니다.')}
          </span>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={() => setConfirmAll(false)}>{t('취소')}</Button>
          <Button variant="danger" disabled={busy} onClick={() => void removeAll()}>
            {busy ? t('삭제 중…') : t('모두 삭제')}
          </Button>
        </div>
      </Modal>
    </div>
  )
}
