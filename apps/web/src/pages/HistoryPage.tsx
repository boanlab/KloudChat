import { MessageSquare, Search, Trash2, TriangleAlert } from 'lucide-react'
import { Fragment, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  Input,
  LoadingState,
  Modal,
  ReloadNotice,
} from '@/components/ui'
import { kindMeta } from '@/lib/kinds'
import { errorMessage } from '@/lib/api'
import { madeLine, relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { useT } from '@/lib/useT'

/** Local calendar day of a timestamp, as `YYYY-M-D`. */
function dayOf(iso: string | null | undefined): string {
  const d = iso ? new Date(iso) : null
  if (!d || Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`
}

/** 오늘, 어제, or the full date. */
function dayLabel(iso: string | null | undefined, t: (s: string) => string): string {
  const d = iso ? new Date(iso) : null
  if (!d || Number.isNaN(d.getTime())) return t('날짜 없음')
  const today = new Date()
  const same = (a: Date, b: Date) => dayOf(a.toISOString()) === dayOf(b.toISOString())
  if (same(d, today)) return t('오늘')
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  if (same(d, yesterday)) return t('어제')
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
}

/** Full conversation list with bulk delete. */
export function HistoryPage() {
  const t = useT()
  const { sessions, deleteSessions, sessionsLoading, sessionsFailed, loadSessions } = useStore()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [confirmAll, setConfirmAll] = useState(false)
  const [confirmPicked, setConfirmPicked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Off by default: deleting conversations must not silently delete artifacts.
  const [alsoArtifacts, setAlsoArtifacts] = useState(false)

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
    setError(null)
    try {
      const count = await deleteSessions({ ids: [...picked], artifacts: alsoArtifacts })
      setPicked(new Set())
      setDone(count)
    } catch (err) {
      setError(errorMessage(err, t('삭제하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  const removeAll = async () => {
    setBusy(true)
    setError(null)
    try {
      const count = await deleteSessions({ all: true, artifacts: alsoArtifacts })
      setPicked(new Set())
      setConfirmAll(false)
      setDone(count)
    } catch (err) {
      setError(errorMessage(err, t('삭제하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('대화 기록')}</span>} />
      <PageBody>
      <h1 className="text-2xl font-semibold tracking-tight">{t('대화 기록')}</h1>
      <p className="mt-1 text-base text-muted">
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
          title={shown.length === 0 ? t('지울 대화가 없습니다') : undefined}
          onClick={() =>
            setPicked((prev) => {
              const next = new Set(prev)
              // Scoped to the filtered list.
              if (allShownPicked) shown.forEach((s) => next.delete(s.id))
              else shown.forEach((s) => next.add(s.id))
              return next
            })
          }
        >
          {allShownPicked ? t('선택 해제') : t('보이는 항목 전체 선택')}
        </Button>
        <Button
          variant="danger"
          disabled={busy || picked.size === 0}
          title={picked.size === 0 ? t('먼저 지울 대화를 고르세요') : undefined}
          onClick={() => setConfirmPicked(true)}
        >
          <Trash2 size={14} />
          {t('선택 {n}개 삭제').replace('{n}', String(picked.size))}
        </Button>
        <Button variant="danger" disabled={busy || sessions.length === 0} onClick={() => setConfirmAll(true)}>
          {t('모든 대화 삭제')}
        </Button>
      </div>

      {done !== null && (
        <p className="mt-3 rounded-control border border-line bg-elevated px-3 py-2 text-base text-muted">
          {done}개의 대화를 삭제했습니다.
        </p>
      )}

      {sessionsFailed && <ReloadNotice onRetry={() => void loadSessions()} />}

      <div className="mt-4 space-y-1.5">
        {sessionsLoading && sessions.length === 0 ? (
          <LoadingState />
        ) : shown.length === 0 ? (
          <EmptyState
            icon={<MessageSquare size={18} />}
            title={query ? t('검색 결과가 없습니다') : t('아직 대화가 없습니다')}
          />
        ) : (
          shown.map((s, i) => {
            const meta = kindMeta[s.kind]
            const made = madeLine(s.made, t)
            // Rows are newest-first; a day heading appears where the day changes.
            const day = dayOf(s.updatedAt)
            const first = i === 0 || dayOf(shown[i - 1].updatedAt) !== day
            return (
              <Fragment key={s.id}>
                {first && (
                  <p className="mt-4 px-1 text-xs font-semibold text-faint first:mt-0">
                    {dayLabel(s.updatedAt, t)}
                  </p>
                )}
              <Card className="flex items-center gap-3 px-3 py-2.5">
                <label className="-m-1.5 grid size-9 shrink-0 cursor-pointer place-items-center">
                  <input
                    type="checkbox"
                    aria-label={t('{title} 선택').replace('{title}', s.title || t('제목 없는 대화'))}
                    checked={picked.has(s.id)}
                    onChange={() => toggle(s.id)}
                    className="size-4 accent-[var(--accent)]"
                  />
                </label>
                <button
                  className="min-w-0 flex-1 text-left"
                  onClick={() => navigate(`/s/${s.id}`)}
                >
                  <span className="block truncate text-base font-medium">
                    {s.title || t('제목 없는 대화')}
                  </span>
                  <span className="block truncate text-xs text-faint">
                    {relativeTime(s.updatedAt)}
                    {made ? ` · ${made}` : ''}
                  </span>
                </button>
                <Badge>{t(meta?.label ?? s.kind)}</Badge>
              </Card>
              </Fragment>
            )
          })
        )}
      </div>

      <ConfirmDialog
        open={confirmPicked}
        onClose={() => setConfirmPicked(false)}
        onConfirm={() => void removePicked()}
        title={t('대화 {n}개를 삭제할까요?').replace('{n}', String(picked.size))}
        description={
          alsoArtifacts
            ? t('되돌릴 수 없습니다. 이 대화들이 만든 결과물도 함께 지워집니다.')
            : t('되돌릴 수 없습니다. 아티팩트와 프로젝트, 메모리는 지워지지 않습니다.')
        }
      />

      <Modal
        open={confirmAll}
        onClose={() => setConfirmAll(false)}
        title={t('모든 대화를 삭제할까요?')}
        description={t('대화 {n}개와 그 안의 모든 메시지가 사라집니다.').replace('{n}', String(sessions.length))}
      >
        <div className="flex items-start gap-2 rounded-card border border-danger/30 bg-danger/5 px-3 py-2.5 text-base text-danger">
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          <span>
            {t('되돌릴 수 없습니다. 아티팩트와 프로젝트, 메모리는 지워지지 않습니다.')}
          </span>
        </div>
        <label className="mt-3 flex cursor-pointer items-start gap-2 text-base">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-[var(--color-danger)]"
            checked={alsoArtifacts}
            onChange={(e) => setAlsoArtifacts(e.target.checked)}
          />
          <span>
            {t('이 대화들이 만든 결과물도 함께 삭제')}
            <span className="block text-sm text-muted">
              {t('보고서, 슬라이드, 이미지, 오디오·동영상. 공유 링크도 함께 끊깁니다.')}
            </span>
          </span>
        </label>
        {error && <p className="mt-3 text-base text-danger">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={() => setConfirmAll(false)}>{t('취소')}</Button>
          <Button variant="danger" disabled={busy} onClick={() => void removeAll()}>
            {busy ? t('삭제 중…') : t('모두 삭제')}
          </Button>
        </div>
      </Modal>
      </PageBody>
    </>
  )
}
