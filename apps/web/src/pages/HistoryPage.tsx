import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, MessageSquare, Search, Trash2, TriangleAlert } from 'lucide-react'
import { Fragment, useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  Input,
  LoadingState,
  Modal,
  ReloadNotice,
} from '@/components/ui'
import { kindMeta } from '@/lib/kinds'
import { errorMessage, historyApi, searchApi, type SearchRun } from '@/lib/api'
import { cn, relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { dayLabel, dayOf } from '@/lib/days'
import { useT } from '@/lib/useT'
import type { SessionKind } from '@/types'

/** Conversations per page; the server pages the list and the search alike. */
const PAGE_SIZE = 50

interface Row {
  id: string
  kind: SessionKind
  title: string
  updatedAt: string
  /** While searching: a marked window of the message that matched. */
  snippet?: SearchRun[]
}

interface Listing {
  rows: Row[]
  total: number
  page: number
}

/** Page numbers around `page`, with gaps as null: 1 … 4 5 6 … 12. */
function pageWindow(page: number, pages: number): (number | null)[] {
  const keep = new Set([1, pages, page - 1, page, page + 1].filter((p) => p >= 1 && p <= pages))
  const out: (number | null)[] = []
  let last = 0
  for (const p of [...keep].sort((a, b) => a - b)) {
    if (p - last > 1) out.push(null)
    out.push(p)
    last = p
  }
  return out
}

/** 처음 · 이전 · 1 2 3 … · 다음 · 마지막; `compact` is the one-line version above the list. */
function Pager({
  page,
  pages,
  onPage,
  compact = false,
}: {
  page: number
  pages: number
  onPage: (page: number) => void
  compact?: boolean
}) {
  const t = useT()
  if (pages <= 1) return null
  const edge = (label: string, to: number, icon: React.ReactNode, disabled: boolean, text?: string) => (
    <Button size="sm" variant="ghost" disabled={disabled} onClick={() => onPage(to)} aria-label={t(label)} title={t(label)}>
      {icon}
      {text && <span className="hidden sm:inline">{t(text)}</span>}
    </Button>
  )
  if (compact) {
    return (
      <nav aria-label={t('페이지 이동(위)')} className="flex items-center gap-1 text-sm text-muted">
        {edge('이전 페이지', page - 1, <ChevronLeft size={14} />, page <= 1)}
        <span className="tabular-nums">
          {t('{page} / {pages}쪽').replace('{page}', String(page)).replace('{pages}', String(pages))}
        </span>
        {edge('다음 페이지', page + 1, <ChevronRight size={14} />, page >= pages)}
      </nav>
    )
  }
  return (
    <nav aria-label={t('페이지 이동')} className="mt-4 flex flex-wrap items-center justify-center gap-1">
      {edge('첫 페이지', 1, <ChevronsLeft size={14} />, page <= 1, '처음')}
      {edge('이전 페이지', page - 1, <ChevronLeft size={14} />, page <= 1)}
      {pageWindow(page, pages).map((p, i) =>
        p === null ? (
          <span key={`gap-${i}`} className="px-1 text-faint">…</span>
        ) : (
          <Button
            key={p}
            size="sm"
            variant={p === page ? 'primary' : 'ghost'}
            aria-current={p === page ? 'page' : undefined}
            aria-label={t('{n}쪽').replace('{n}', String(p))}
            onClick={() => onPage(p)}
            className="min-w-8 tabular-nums"
          >
            {p}
          </Button>
        ),
      )}
      {edge('다음 페이지', page + 1, <ChevronRight size={14} />, page >= pages)}
      {edge('마지막 페이지', pages, <ChevronsRight size={14} />, page >= pages, '마지막')}
    </nav>
  )
}

/** Every conversation, a page at a time, with bulk delete; the search reads titles and messages. */
export function HistoryPage() {
  const t = useT()
  const { deleteSessions } = useStore()
  const navigate = useNavigate()
  // The search dialog's 「대화 기록에서 보기」 carries its words here; words and page live in the address.
  const [params, setParams] = useSearchParams()
  const urlQuery = params.get('q') ?? ''
  const page = Math.max(1, Number.parseInt(params.get('page') ?? '1', 10) || 1)
  const [query, setQuery] = useState(urlQuery)
  const needle = query.trim()
  // Back and forward bring their words with them.
  useEffect(() => {
    setQuery((current) => (current.trim() === urlQuery ? current : urlQuery))
  }, [urlQuery])

  const [listing, setListing] = useState<Listing | null>(null)
  // Every conversation, whatever the search: what 「모든 대화 삭제」 removes.
  const [allTotal, setAllTotal] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState<string | null>(null)
  const [reload, setReload] = useState(0)

  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [confirmAll, setConfirmAll] = useState(false)
  const [confirmPicked, setConfirmPicked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const goTo = useCallback(
    (next: { q?: string; page?: number }) => {
      const q = next.q ?? urlQuery
      const p = next.page ?? page
      const out: Record<string, string> = {}
      if (q) out.q = q
      if (p > 1) out.page = String(p)
      setParams(out, { replace: next.q !== undefined })
    },
    [urlQuery, page, setParams],
  )

  // Typing settles into the address (a new search starts on page 1), without a history entry per key.
  useEffect(() => {
    if (needle === urlQuery) return
    const timer = setTimeout(() => goTo({ q: needle, page: 1 }), 250)
    return () => clearTimeout(timer)
  }, [needle, urlQuery, goTo])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setFailed(null)
    const work = urlQuery
      ? searchApi
          .query(urlQuery, {
            scope: 'sessions', limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE, count: true,
            signal: controller.signal,
          })
          .then((found) => ({
            rows: found.sessions.map((h) => ({ id: h.id, kind: h.kind, title: h.title, updatedAt: h.updatedAt, snippet: h.snippet })),
            total: found.sessionsTotal ?? found.sessions.length,
            page,
          }))
      : historyApi.page(page, PAGE_SIZE, controller.signal).then((found) => {
          setAllTotal(found.total)
          return {
            rows: found.items.map((s) => ({ id: s.id, kind: s.kind, title: s.title, updatedAt: s.updatedAt })),
            total: found.total,
            page: found.page,
          }
        })
    work
      .then((next) => {
        setListing(next)
        // Past the end (after deleting, or an old link): the last page there is.
        if (next.page !== page) goTo({ page: next.page })
        const pages = Math.max(1, Math.ceil(next.total / PAGE_SIZE))
        if (urlQuery && page > pages && next.total > 0) goTo({ page: pages })
      })
      .catch((err) => {
        if (!controller.signal.aborted) setFailed(errorMessage(err, t('대화 기록을 불러오지 못했습니다.')))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [urlQuery, page, reload, goTo, t])

  // The count of every conversation, once, when the page opens on a search.
  useEffect(() => {
    if (allTotal !== null || !urlQuery) return
    void historyApi.page(1, 1).then((found) => setAllTotal(found.total)).catch(() => undefined)
  }, [allTotal, urlQuery])

  const rows = listing?.rows ?? []
  const total = listing?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const allPagePicked = rows.length > 0 && rows.every((s) => picked.has(s.id))

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const onPage = (next: number) => {
    goTo({ page: next })
    document.querySelector('[data-history-top]')?.scrollIntoView({ block: 'start' })
  }

  const removePicked = async () => {
    setBusy(true)
    setError(null)
    try {
      const count = await deleteSessions({ ids: [...picked] })
      setPicked(new Set())
      setDone(count)
      setAllTotal(null)
      setReload((n) => n + 1)
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
      const count = await deleteSessions({ all: true })
      setPicked(new Set())
      setConfirmAll(false)
      setDone(count)
      setAllTotal(0)
      goTo({ page: 1 })
      setReload((n) => n + 1)
    } catch (err) {
      setError(errorMessage(err, t('삭제하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  const everyone = allTotal ?? (urlQuery ? null : total)

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('대화 기록')}</span>} />
      <PageBody>
      <h1 className="text-2xl font-semibold tracking-tight" data-history-top="">{t('대화 기록')}</h1>
      <p className="mt-1 text-base text-muted" data-history-total="">
        {everyone === null
          ? t('삭제한 대화는 되돌릴 수 없고, 그 대화에서 만든 결과물도 함께 지워집니다.')
          : t('대화 {n}개가 있습니다. 삭제한 대화는 되돌릴 수 없고, 그 대화에서 만든 결과물도 함께 지워집니다.').replace(
              '{n}',
              everyone.toLocaleString(),
            )}
      </p>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 max-sm:basis-full">
          <Search size={14} className="absolute top-1/2 left-3 -translate-y-1/2 text-faint" />
          <Input
            aria-label={t('대화 검색')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('제목과 내용으로 찾기')}
            className="pl-9"
          />
        </div>
        <Button
          disabled={rows.length === 0}
          title={rows.length === 0 ? t('지울 대화가 없습니다') : undefined}
          onClick={() =>
            setPicked((prev) => {
              const next = new Set(prev)
              if (allPagePicked) rows.forEach((s) => next.delete(s.id))
              else rows.forEach((s) => next.add(s.id))
              return next
            })
          }
        >
          {allPagePicked ? t('선택 해제') : t('이 페이지 전체 선택')}
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
        <Button variant="danger" disabled={busy || everyone === 0} onClick={() => setConfirmAll(true)}>
          {t('모든 대화 삭제')}
        </Button>
      </div>

      {done !== null && (
        <p className="mt-3 rounded-control border border-line bg-elevated px-3 py-2 text-base text-muted">
          {t('{n}개의 대화를 삭제했습니다.').replace('{n}', String(done))}
        </p>
      )}

      {failed && <ReloadNotice onRetry={() => setReload((n) => n + 1)} />}

      {listing && total > 0 && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-faint tabular-nums" data-history-range="">
            {(urlQuery ? t('「{q}」 검색 결과 {n}개 중 {from}–{to}') : t('{n}개 중 {from}–{to}'))
              .replace('{q}', urlQuery)
              .replace('{n}', total.toLocaleString())
              .replace('{from}', String((listing.page - 1) * PAGE_SIZE + 1))
              .replace('{to}', String((listing.page - 1) * PAGE_SIZE + rows.length))}
          </p>
          <Pager compact page={listing.page} pages={pages} onPage={onPage} />
        </div>
      )}

      <div className={cn('mt-2', loading && listing && 'opacity-60')} aria-busy={loading}>
        {loading && !listing ? (
          <LoadingState />
        ) : rows.length === 0 && !failed ? (
          <EmptyState
            icon={<MessageSquare size={18} />}
            title={urlQuery ? t('검색 결과가 없습니다') : t('아직 대화가 없습니다')}
          />
        ) : (
          <div className="overflow-hidden rounded-card border border-line bg-panel">
            {rows.map((s, i) => {
              const meta = kindMeta[s.kind]
              // Rows are newest-first; a day heading appears where the day changes on this page.
              const day = dayOf(s.updatedAt)
              const first = i === 0 || dayOf(rows[i - 1].updatedAt) !== day
              return (
                <Fragment key={s.id}>
                  {first && (
                    <p className="border-b border-line bg-elevated/60 px-3 py-1 text-xs font-semibold text-faint">
                      {dayLabel(s.updatedAt, t)}
                    </p>
                  )}
                  <div
                    data-history-row=""
                    className="flex min-h-11 items-center gap-2 border-b border-line px-2 last:border-b-0 hover:bg-elevated"
                  >
                    <label className="grid size-9 shrink-0 cursor-pointer place-items-center">
                      <input
                        type="checkbox"
                        aria-label={t('{title} 선택').replace('{title}', s.title || t('제목 없는 대화'))}
                        checked={picked.has(s.id)}
                        onChange={() => toggle(s.id)}
                        className="size-4 accent-[var(--accent)]"
                      />
                    </label>
                    <button
                      className="flex min-w-0 flex-1 flex-col py-2 text-left"
                      onClick={() => navigate(`/s/${s.id}`)}
                    >
                      <span className="truncate text-base">{s.title || t('제목 없는 대화')}</span>
                      {s.snippet && s.snippet.length > 0 && (
                        <span className="truncate text-sm text-muted" data-snippet="">
                          {s.snippet.map((run, n) =>
                            run.hit ? (
                              <mark key={n} className="rounded-sm bg-accent/20 px-px text-fg">{run.text}</mark>
                            ) : (
                              <span key={n}>{run.text}</span>
                            ),
                          )}
                        </span>
                      )}
                    </button>
                    <Badge className="shrink-0 max-sm:hidden">{t(meta?.label ?? s.kind)}</Badge>
                    <span className="w-20 shrink-0 pr-1 text-right text-xs text-faint tabular-nums max-sm:w-14">
                      {relativeTime(s.updatedAt)}
                    </span>
                  </div>
                </Fragment>
              )
            })}
          </div>
        )}
      </div>

      {listing && <Pager page={listing.page} pages={pages} onPage={onPage} />}

      <ConfirmDialog
        open={confirmPicked}
        onClose={() => setConfirmPicked(false)}
        onConfirm={() => void removePicked()}
        title={t('대화 {n}개를 삭제할까요?').replace('{n}', String(picked.size))}
        description={t('되돌릴 수 없습니다. 이 대화들이 만든 결과물(보고서·슬라이드·이미지·오디오·동영상)도 함께 지워지고 공유 링크도 끊깁니다. 프로젝트와 메모리는 남습니다.')}
      />

      <Modal
        open={confirmAll}
        onClose={() => setConfirmAll(false)}
        title={t('모든 대화를 삭제할까요?')}
        description={t('대화 {n}개와 그 안의 모든 메시지가 사라집니다.').replace('{n}', String(everyone ?? total))}
      >
        <div className="flex items-start gap-2 rounded-card border border-danger/30 bg-danger/5 px-3 py-2.5 text-base text-danger">
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          <span>
            {t('되돌릴 수 없습니다. 이 대화들이 만든 결과물(보고서·슬라이드·이미지·오디오·동영상)도 함께 지워지고 공유 링크도 끊깁니다. 프로젝트와 메모리는 남습니다.')}
          </span>
        </div>
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
