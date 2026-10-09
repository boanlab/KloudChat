import { ArrowRight, CornerDownLeft, Loader2, Search, X } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { errorMessage, searchApi, type ArtifactSearchHit, type SearchRun, type SessionSearchHit } from '@/lib/api'
import { kindMeta } from '@/lib/kinds'
import { artifactIcon, artifactLabel } from '@/lib/resultMeta'
import { isMac } from '@/lib/shortcuts'
import { useT } from '@/lib/useT'
import { cn, relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Session } from '@/types'

/** Window event that opens the dialog from anywhere: ⌘K, the sidebar's 검색 box. */
const OPEN_EVENT = 'kloudchat:search'
export const openSearch = () => window.dispatchEvent(new Event(OPEN_EVENT))

/** Rows per group on the first answer; 「더 보기」 asks for this many more. */
const PAGE = 8
/** Conversations listed before anything is typed. */
const RECENT = 8
const DEBOUNCE_MS = 200

type Row =
  | { type: 'recent'; key: string; session: Session }
  | { type: 'session'; key: string; hit: SessionSearchHit }
  | { type: 'artifact'; key: string; hit: ArtifactSearchHit }
  | { type: 'more'; key: string; group: 'sessions' | 'artifacts' }

interface Results {
  query: string
  sessions: SessionSearchHit[]
  artifacts: ArtifactSearchHit[]
  sessionsMore: boolean
  artifactsMore: boolean
}

/** Runs from the server, the query's words marked. */
function Marked({ runs, fallback }: { runs: SearchRun[]; fallback?: string }) {
  if (!runs.length) return <>{fallback ?? ''}</>
  return (
    <>
      {runs.map((run, i) =>
        run.hit ? (
          <mark key={i} className="rounded-sm bg-accent/20 px-px text-fg">
            {run.text}
          </mark>
        ) : (
          <span key={i}>{run.text}</span>
        ),
      )}
    </>
  )
}

/** ⌘K: one box over every conversation and result the person has, by title and by content. */
export function SearchDialog() {
  const t = useT()
  const navigate = useNavigate()
  const sessions = useStore((s) => s.sessions)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Results | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState<'sessions' | 'artifacts' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const returnTo = useRef<HTMLElement | null>(null)
  // The answer to an older keystroke must not replace a newer one.
  const request = useRef<AbortController | null>(null)

  useEffect(() => {
    const onOpen = () => {
      returnTo.current = document.activeElement as HTMLElement | null
      setOpen(true)
    }
    window.addEventListener(OPEN_EVENT, onOpen)
    return () => window.removeEventListener(OPEN_EVENT, onOpen)
  }, [])

  const close = useCallback(() => {
    request.current?.abort()
    setOpen(false)
    setQuery('')
    setResults(null)
    setError(null)
    setLoading(false)
    setActive(0)
    returnTo.current?.focus?.()
  }, [])

  useLayoutEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  // A modal: Esc closes it wherever focus is, and focus that wanders out (a screen behind
  // focusing its own box as it loads) comes back to the search box.
  const dialogRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || dialogRef.current?.contains(e.target as Node)) return
      e.preventDefault()
      close()
    }
    const onFocus = (e: FocusEvent) => {
      if (dialogRef.current && !dialogRef.current.contains(e.target as Node)) inputRef.current?.focus()
    }
    window.addEventListener('keydown', onKey)
    document.addEventListener('focusin', onFocus)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.removeEventListener('focusin', onFocus)
    }
  }, [open, close])

  const trimmed = query.trim()

  useEffect(() => {
    if (!open) return
    request.current?.abort()
    if (!trimmed) {
      setResults(null)
      setLoading(false)
      setError(null)
      return
    }
    const controller = new AbortController()
    request.current = controller
    setLoading(true)
    const timer = setTimeout(() => {
      searchApi
        .query(trimmed, { limit: PAGE, signal: controller.signal })
        .then((found) => {
          if (controller.signal.aborted) return
          setResults({
            query: trimmed,
            sessions: found.sessions,
            artifacts: found.artifacts,
            sessionsMore: found.sessionsMore,
            artifactsMore: found.artifactsMore,
          })
          setError(null)
          setActive(0)
        })
        .catch((err) => {
          if (controller.signal.aborted) return
          setError(errorMessage(err, t('검색하지 못했습니다. 잠시 뒤 다시 해 보세요.')))
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false)
        })
    }, DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [open, trimmed, t])

  const more = useCallback(
    async (group: 'sessions' | 'artifacts') => {
      if (!results || loadingMore) return
      setLoadingMore(group)
      try {
        const offset = group === 'sessions' ? results.sessions.length : results.artifacts.length
        const found = await searchApi.query(results.query, { scope: group, limit: PAGE, offset })
        setResults((prev) =>
          prev && prev.query === results.query
            ? group === 'sessions'
              ? { ...prev, sessions: [...prev.sessions, ...found.sessions], sessionsMore: found.sessionsMore }
              : { ...prev, artifacts: [...prev.artifacts, ...found.artifacts], artifactsMore: found.artifactsMore }
            : prev,
        )
      } catch (err) {
        setError(errorMessage(err, t('검색하지 못했습니다. 잠시 뒤 다시 해 보세요.')))
      } finally {
        setLoadingMore(null)
      }
    },
    [results, loadingMore, t],
  )

  const recent = useMemo(
    () => [...sessions].sort((a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt)).slice(0, RECENT),
    [sessions],
  )

  // One flat list for the arrow keys, in the order the groups are drawn.
  const rows = useMemo<Row[]>(() => {
    if (!trimmed) return recent.map((session) => ({ type: 'recent', key: `r-${session.id}`, session }))
    if (!results) return []
    const list: Row[] = results.sessions.map((hit) => ({ type: 'session', key: `s-${hit.id}`, hit }))
    if (results.sessionsMore) list.push({ type: 'more', key: 'more-sessions', group: 'sessions' })
    list.push(...results.artifacts.map((hit): Row => ({ type: 'artifact', key: `a-${hit.id}`, hit })))
    if (results.artifactsMore) list.push({ type: 'more', key: 'more-artifacts', group: 'artifacts' })
    return list
  }, [trimmed, recent, results])

  const choose = (row: Row) => {
    switch (row.type) {
      case 'recent':
        navigate(`/s/${row.session.id}`)
        break
      case 'session':
        navigate(`/s/${row.hit.id}`)
        break
      case 'artifact':
        navigate(
          row.hit.sessionId ? `/s/${row.hit.sessionId}?artifact=${row.hit.id}` : `/artifacts?open=${row.hit.id}`,
        )
        break
      case 'more':
        void more(row.group)
        return
    }
    close()
  }

  // Keep the active row in view while the arrows move it.
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-row="${active}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [active])

  const onKeyDown = (e: React.KeyboardEvent) => {
    // The chord that opened it closes it again.
    if (e.key === 'Escape' || (e.key.toLowerCase() === 'k' && (isMac() ? e.metaKey : e.ctrlKey))) {
      e.preventDefault()
      close()
      return
    }
    if (e.nativeEvent.isComposing) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!rows.length) return
      const step = e.key === 'ArrowDown' ? 1 : -1
      setActive((i) => (i + step + rows.length) % rows.length)
      return
    }
    if (e.key === 'Enter') {
      const row = rows[active]
      if (!row) return
      e.preventDefault()
      choose(row)
    }
  }

  if (!open) return null

  const listId = 'search-results'
  const optionId = (i: number) => `search-option-${i}`
  const sessionRows = rows.filter((r) => r.type === 'session' || (r.type === 'more' && r.group === 'sessions'))
  const artifactRows = rows.filter((r) => r.type === 'artifact' || (r.type === 'more' && r.group === 'artifacts'))

  const renderRow = (row: Row) => {
    const index = rows.indexOf(row)
    const selected = index === active
    const common = {
      id: optionId(index),
      role: 'option' as const,
      'aria-selected': selected,
      'data-row': index,
      onMouseMove: () => setActive(index),
      onClick: () => choose(row),
      className: cn(
        'flex w-full cursor-pointer items-start gap-3 rounded-control px-3 py-2 text-left',
        selected ? 'bg-selected' : 'hover:bg-elevated',
      ),
    }
    if (row.type === 'more') {
      return (
        <div key={row.key} {...common} className={cn(common.className, 'items-center text-sm text-muted')}>
          {loadingMore === row.group ? <Loader2 size={14} className="animate-spin" /> : <ArrowRight size={14} />}
          {row.group === 'sessions' ? t('대화 더 보기') : t('결과물 더 보기')}
        </div>
      )
    }
    if (row.type === 'recent') {
      const meta = kindMeta[row.session.kind]
      const Icon = meta.icon
      return (
        <div key={row.key} {...common}>
          <Icon size={15} className="mt-0.5 shrink-0" style={{ color: meta.color }} aria-hidden />
          <span className="min-w-0 flex-1 truncate text-base">{row.session.title || t('제목 없는 대화')}</span>
          <span className="shrink-0 text-xs text-faint">{relativeTime(row.session.updatedAt)}</span>
        </div>
      )
    }
    const isSession = row.type === 'session'
    const hit = row.hit
    const Icon = isSession ? kindMeta[(hit as SessionSearchHit).kind]?.icon : artifactIcon[(hit as ArtifactSearchHit).kind]
    const color = isSession ? kindMeta[(hit as SessionSearchHit).kind]?.color : undefined
    const label = isSession
      ? t(kindMeta[(hit as SessionSearchHit).kind]?.label ?? '')
      : t(artifactLabel[(hit as ArtifactSearchHit).kind] ?? '')
    return (
      <div key={row.key} {...common} data-hit={isSession ? 'session' : 'artifact'}>
        {Icon && (
          <Icon
            size={15}
            className={cn('mt-0.5 shrink-0', !color && 'text-accent')}
            style={color ? { color } : undefined}
            aria-label={label}
          />
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-base font-medium">
            <Marked runs={hit.titleParts} fallback={hit.title || t('제목 없는 대화')} />
          </span>
          {hit.snippet.length > 0 && (
            <span className="mt-0.5 line-clamp-2 block text-sm text-muted" data-snippet="">
              <Marked runs={hit.snippet} />
            </span>
          )}
        </span>
        <span className="shrink-0 pt-0.5 text-xs text-faint">{relativeTime(hit.updatedAt)}</span>
      </div>
    )
  }

  const empty = trimmed && results && !results.sessions.length && !results.artifacts.length
  const mod = isMac() ? '⌘' : 'Ctrl'

  return (
    <div className="fixed inset-0 z-[150] flex justify-center p-4 pt-[12vh] max-sm:p-0">
      <div className="fixed inset-0 bg-black/45 backdrop-blur-[2px]" onClick={close} aria-hidden />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={t('검색')}
        onKeyDown={onKeyDown}
        className="animate-fade-up relative flex max-h-[70vh] w-full max-w-2xl flex-col overflow-hidden rounded-panel border border-line bg-panel shadow-float max-sm:h-full max-sm:max-h-none max-sm:rounded-none max-sm:border-0"
      >
        <div className="flex items-center gap-2 border-b border-line px-4 max-sm:px-3">
          <Search size={16} className="shrink-0 text-faint" aria-hidden />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            role="combobox"
            aria-expanded={rows.length > 0}
            aria-controls={listId}
            aria-activedescendant={rows[active] ? optionId(active) : undefined}
            aria-autocomplete="list"
            aria-label={t('대화와 결과물 검색')}
            placeholder={t('대화와 결과물 검색')}
            maxLength={200}
            className="h-14 min-w-0 flex-1 bg-transparent text-md outline-none placeholder:text-faint max-sm:h-12"
          />
          {loading && <Loader2 size={15} className="shrink-0 animate-spin text-faint" aria-label={t('검색 중…')} />}
          <button
            type="button"
            onClick={close}
            aria-label={t('닫기')}
            className="grid size-8 shrink-0 place-items-center rounded-control text-faint hover:bg-elevated hover:text-fg"
          >
            <X size={16} />
          </button>
        </div>

        <div ref={listRef} id={listId} role="listbox" aria-label={t('검색 결과')} className="min-h-0 flex-1 overflow-y-auto p-2">
          {!trimmed && (
            <section aria-label={t('최근 대화')}>
              <p className="px-3 pt-1 pb-1.5 text-xs font-semibold text-faint">{t('최근 대화')}</p>
              {rows.length ? rows.map(renderRow) : (
                <p className="px-3 py-6 text-center text-base text-faint">{t('아직 대화가 없습니다')}</p>
              )}
            </section>
          )}
          {trimmed && error && <p className="px-3 py-6 text-center text-base text-danger">{error}</p>}
          {trimmed && !error && !results && loading && (
            <p className="px-3 py-6 text-center text-base text-faint">{t('검색 중…')}</p>
          )}
          {empty && !error && (
            <p className="px-3 py-6 text-center text-base text-faint">
              {t('「{q}」와 맞는 대화나 결과물이 없습니다').replace('{q}', results!.query)}
            </p>
          )}
          {trimmed && sessionRows.length > 0 && (
            <section aria-label={t('대화')} className="mb-2">
              <p className="px-3 pt-1 pb-1.5 text-xs font-semibold text-faint">{t('대화')}</p>
              {sessionRows.map(renderRow)}
            </section>
          )}
          {trimmed && artifactRows.length > 0 && (
            <section aria-label={t('결과물')}>
              <p className="px-3 pt-1 pb-1.5 text-xs font-semibold text-faint">{t('결과물')}</p>
              {artifactRows.map(renderRow)}
            </section>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-4 py-2 text-xs text-faint max-sm:hidden">
          <span>↑↓ {t('이동')}</span>
          <span className="inline-flex items-center gap-1"><CornerDownLeft size={11} aria-hidden /> {t('열기')}</span>
          <span>Esc {t('닫기')}</span>
          <span className="ml-auto">{mod} K</span>
          {trimmed && (
            <button
              type="button"
              className="inline-flex items-center gap-1 text-muted hover:text-fg"
              onClick={() => {
                navigate(`/history?q=${encodeURIComponent(trimmed)}`)
                close()
              }}
            >
              {t('대화 기록에서 보기')}
              <ArrowRight size={11} aria-hidden />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
