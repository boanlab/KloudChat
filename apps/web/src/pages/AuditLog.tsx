import { Download, Loader2, Search } from 'lucide-react'
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from 'react'
import { adminApi, errorMessage, usageApi, type AuditQuery, type AuditRow } from '@/lib/api'
import {
  ACTION_GROUPS,
  FIELD_LABEL,
  actionLabel,
  actionsMatching,
  changedFields,
  detailWords,
  targetParts,
  valueWords,
} from '@/lib/auditLabels'
import { Badge, Button, Card, Input, Modal, ResourceStatus, Select } from '@/components/ui'
import { CopyButton } from '@/components/ui/CopyButton'
import { SortHeader, useTableSort } from '@/components/ui/DataTable'
import type { SortDir, SortState } from '@/lib/tableSort'
import { Pager } from '@/components/ui/Pager'
import { browserName, formatDateTime, serviceDate, serviceMidnight, shiftDate } from '@/lib/utils'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import { useResource } from '@/lib/useResource'

const SEVERITY: Record<string, 'neutral' | 'warn' | 'danger'> = {
  info: 'neutral',
  warn: 'warn',
  alert: 'danger',
}

const SIZES = [25, 50, 100, 200]

const SORT_KEYS = ['at', 'action', 'target', 'ip'] as const
/** What the trail shows unsorted: newest first. */
const NEWEST: SortState = { key: 'at', dir: 'desc' }

/** Mirrors `audit.MASK`. */
const SECRET = '••••'

type Period = 'all' | 'today' | '7d' | '30d' | 'custom'

/** Service-zone day bounds as ISO, by calendar date; `until` is the next day's start. */
function periodRange(period: Period, from: string, to: string): { since?: string; until?: string } {
  const today = serviceDate(new Date())
  const daysBack = (n: number) => serviceMidnight(shiftDate(today, -n)).toISOString()
  if (period === 'today') return { since: daysBack(0) }
  if (period === '7d') return { since: daysBack(6) }
  if (period === '30d') return { since: daysBack(29) }
  if (period === 'custom') {
    return {
      since: from ? serviceMidnight(from).toISOString() : undefined,
      until: to ? serviceMidnight(shiftDate(to, 1)).toISOString() : undefined,
    }
  }
  return {}
}

/** Debounced. */
function useSettled<T>(value: T, ms = 300): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const id = window.setTimeout(() => setSettled(value), ms)
    return () => window.clearTimeout(id)
  }, [value, ms])
  return settled
}

export function AuditLog({ onTotal }: { onTotal?: (total: number) => void }) {
  const t = useT()
  const models = useStore((s) => s.models)
  const [catalogue, setCatalogue] = useState<{ id: string; label: string }[]>([])
  const [query, setQuery] = useState('')
  const [account, setAccount] = useState('')
  const [group, setGroup] = useState('')
  const [period, setPeriod] = useState<Period>('all')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(50)
  const [selected, setSelected] = useState<AuditRow | null>(null)
  const settledQuery = useSettled(query)
  // Server-side order; newest first unless a heading says otherwise (kept in this browser).
  const [sort, toggleSort] = useTableSort('admin.audit', { keys: SORT_KEYS, initial: NEWEST, clearable: false })
  const [exporting, setExporting] = useState(false)
  const setToast = useStore((s) => s.setToast)
  const settledAccount = useSettled(account)

  useEffect(() => {
    // Whole catalogue: models outside the admin's own list still get a name.
    adminApi.catalogue().then(setCatalogue, () => setCatalogue([]))
  }, [])

  const modelName = useCallback(
    (id: string) =>
      catalogue.find((m) => m.id === id)?.label ?? models.find((m) => m.id === id)?.label ?? id,
    [catalogue, models],
  )

  const request = useMemo<AuditQuery>(() => {
    const codes = actionsMatching(settledQuery, t)
    return {
      page,
      size,
      q: settledQuery.trim() || undefined,
      qActions: codes.length ? codes.join(',') : undefined,
      account: settledAccount.trim() || undefined,
      action: ACTION_GROUPS.find((g) => g.id === group)?.codes,
      ...periodRange(period, from, to),
      ...(sort ? { sort: sort.key as NonNullable<AuditQuery['sort']>, order: sort.dir } : {}),
    }
  }, [page, size, settledQuery, settledAccount, group, period, from, to, t, sort])

  const exportCsv = async () => {
    setExporting(true)
    try {
      // Every page under the same filters and order.
      const { page: _page, size: _size, ...filters } = request
      await usageApi.auditCsv(filters)
    } catch (err) {
      setToast({ text: errorMessage(err, t('CSV를 내보내지 못했습니다. 잠시 후 다시 시도하세요.')) })
    } finally {
      setExporting(false)
    }
  }
  const onSort = (key: string, first: SortDir) => {
    toggleSort(key, first)
    setPage(1)
  }

  const audit = useResource(() => usageApi.audit(request), [request])
  const data = audit.data
  useEffect(() => {
    if (!audit.data) return
    setSelected(null)
    if (!request.q && !request.account && !request.action && !request.since && !request.until) {
      onTotal?.(audit.data.total)
    }
    // Once per answer, not per filter edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audit.data])

  // Narrower filter restarts at page 1.
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v)
    setPage(1)
  }

  // Whole row is the control: click, Enter or Space opens its details.
  const rowProps = (e: AuditRow) => ({
    role: 'button',
    tabIndex: 0,
    'data-testid': 'audit-row',
    'aria-label': t('자세히 보기: {action} · {actor} · {at}')
      .replace('{action}', t(actionLabel(e)))
      .replace('{actor}', t(e.actor))
      .replace('{at}', formatDateTime(e.at)),
    onClick: (ev: MouseEvent<HTMLElement>) => {
      // Safari does not focus on click; the dialog returns focus here.
      ev.currentTarget.focus()
      setSelected(e)
    },
    onKeyDown: (ev: KeyboardEvent<HTMLElement>) => {
      if (ev.key !== 'Enter' && ev.key !== ' ') return
      ev.preventDefault()
      setSelected(e)
    },
  })

  const filtered = Boolean(query || account || group || period !== 'all')
  const total = data?.total ?? 0

  return (
    <div className="pt-4">
      <div className="mb-3 grid grid-cols-2 gap-2 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)]">
        <div className="relative col-span-2 lg:col-span-1">
          <Search size={14} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-faint" />
          <Input
            value={query}
            onChange={(e) => reset(setQuery)(e.target.value)}
            placeholder={t('계정 · 행위 · IP 검색')}
            aria-label={t('감사 로그 검색')}
            className="pl-8"
          />
        </div>
        <Select value={group} onChange={(e) => reset(setGroup)(e.target.value)} aria-label={t('행위 종류')}>
          <option value="">{t('모든 행위')}</option>
          {ACTION_GROUPS.map((g) => (
            <option key={g.id} value={g.id}>
              {t(g.label)}
            </option>
          ))}
        </Select>
        <Input
          className="order-last col-span-2 lg:order-none lg:col-span-1"
          value={account}
          onChange={(e) => reset(setAccount)(e.target.value)}
          placeholder={t('계정 (이메일 · 이름)')}
          aria-label={t('계정')}
        />
        <Select
          value={period}
          onChange={(e) => reset(setPeriod)(e.target.value as Period)}
          aria-label={t('기간')}
        >
          <option value="all">{t('전체 기간')}</option>
          <option value="today">{t('오늘')}</option>
          <option value="7d">{t('최근 7일')}</option>
          <option value="30d">{t('최근 30일')}</option>
          <option value="custom">{t('직접 지정')}</option>
        </Select>
      </div>
      {period === 'custom' && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Input
            type="date"
            value={from}
            max={to || undefined}
            onChange={(e) => reset(setFrom)(e.target.value)}
            aria-label={t('시작일')}
            className="w-auto"
          />
          <span className="text-faint">–</span>
          <Input
            type="date"
            value={to}
            min={from || undefined}
            onChange={(e) => reset(setTo)(e.target.value)}
            aria-label={t('종료일')}
            className="w-auto"
          />
        </div>
      )}

      <div className="mb-2 flex flex-wrap items-center justify-end gap-2">
        {/* Phone: the table is a list, so the order is a menu. */}
        <div className="lg:hidden">
          <Select
            value={sort ? `${sort.key}:${sort.dir}` : 'at:desc'}
            onChange={(e) => {
              const [key, dir] = e.target.value.split(':') as [string, SortDir]
              if (key === sort?.key && dir === sort?.dir) return
              // Same column: the toggle flips to the other direction; another: opens at `dir`.
              toggleSort(key, dir)
              setPage(1)
            }}
            aria-label={t('정렬')}
            className="h-8 w-auto text-sm"
          >
            <option value="at:desc">{t('최신순')}</option>
            <option value="at:asc">{t('오래된 순')}</option>
            <option value="action:asc">{t('행위순')}</option>
            <option value="target:asc">{t('대상순')}</option>
            <option value="ip:asc">{t('접속 위치순')}</option>
          </Select>
        </div>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => void exportCsv()}
          disabled={exporting || !data || total === 0}
          title={
            !data || total === 0
              ? t('내보낼 기록이 없습니다')
              : t('지금 조건과 정렬로 모든 쪽을 CSV로 받습니다. 내보낸 일도 감사 로그에 남습니다.')
          }
        >
          {exporting ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
          {t('CSV 내보내기')}
        </Button>
      </div>

      {audit.status === 'error' || !data ? (
        <Card>
          <ResourceStatus
            resource={audit}
            label={t('기록을 불러오는 중입니다…')}
            failed={t('기록을 불러오지 못했습니다.')}
          />
        </Card>
      ) : data.items.length === 0 ? (
        <Card className="p-10 text-center text-base text-muted">
          {filtered ? t('조건에 맞는 기록이 없습니다') : t('아직 기록된 사건이 없습니다')}
        </Card>
      ) : (
        <Card className="overflow-hidden">
          {/* Wide: one line per event; the whole row opens its details. */}
          <table className="hidden w-full table-fixed text-base lg:table">
            <colgroup>
              <col className="w-[11.5rem]" />
              <col className="w-[18%]" />
              <col />
              <col className="w-[18%]" />
              <col className="w-[15rem]" />
            </colgroup>
            <thead className="sticky top-0 z-[1] border-b border-line bg-panel text-left text-xs tracking-wide text-faint uppercase">
              <tr>
                <SortHeader label={t('시각')} columnKey="at" sort={sort} onSort={onSort} first="desc" className="px-4 py-1.5" />
                <th scope="col" className="px-4 py-2.5 font-medium">{t('계정')}</th>
                <SortHeader label={t('행위')} columnKey="action" sort={sort} onSort={onSort} className="px-4 py-1.5" />
                <SortHeader label={t('대상')} columnKey="target" sort={sort} onSort={onSort} className="px-4 py-1.5" />
                <SortHeader label={t('접속 위치')} columnKey="ip" sort={sort} onSort={onSort} className="px-4 py-1.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data.items.map((e) => (
                <tr
                  key={e.id}
                  {...rowProps(e)}
                  className="cursor-pointer whitespace-nowrap hover:bg-elevated focus-visible:bg-elevated focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
                >
                  <td className="truncate px-4 py-2 tabular-nums text-muted">{formatDateTime(e.at)}</td>
                  <td className="truncate px-4 py-2" title={e.actor}>
                    {t(e.actor)}
                  </td>
                  <td className="px-4 py-2">
                    <ActionCell row={e} modelName={modelName} />
                  </td>
                  <td className="truncate px-4 py-2 text-muted">
                    <Target row={e} />
                  </td>
                  <td className="truncate px-4 py-2 text-xs text-faint">
                    <Where row={e} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Narrow: a short summary per event; tapping opens the same details. */}
          <ul className="divide-y divide-line lg:hidden">
            {data.items.map((e) => (
              <li
                key={e.id}
                {...rowProps(e)}
                className="cursor-pointer space-y-1 px-4 py-2.5 hover:bg-elevated focus-visible:bg-elevated focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
              >
                <div className="flex items-baseline justify-between gap-x-3">
                  <span className="shrink-0 text-sm tabular-nums text-muted">{formatDateTime(e.at)}</span>
                  <span className="min-w-0 truncate text-sm" title={e.actor}>
                    {t(e.actor)}
                  </span>
                </div>
                <ActionCell row={e} modelName={modelName} />
                <p className="flex min-w-0 gap-x-2 text-xs text-faint">
                  {targetParts(e).length > 0 && (
                    <span className="min-w-0 truncate text-muted">
                      <Target row={e} />
                    </span>
                  )}
                  <span className="min-w-0 truncate">
                    <Where row={e} />
                  </span>
                </p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {selected && (
        <Modal
          open
          onClose={() => setSelected(null)}
          title={t(actionLabel(selected))}
          width="max-w-2xl"
        >
          <Details row={selected} modelName={modelName} />
        </Modal>
      )}

      {data && total > 0 && (
        <Pager
          page={page}
          size={size}
          total={total}
          onPage={setPage}
          label={t('감사 로그 페이지')}
          rangeTestId="audit-range"
        >
          <Select
            value={String(size)}
            onChange={(e) => reset(setSize)(Number(e.target.value))}
            aria-label={t('페이지당 건수')}
            className="h-8 w-auto pr-8 text-sm"
          >
            {SIZES.map((n) => (
              <option key={n} value={n}>
                {t('{n}건씩').replace('{n}', String(n))}
              </option>
            ))}
          </Select>
        </Pager>
      )}
    </div>
  )
}

/** Badge plus at most a one-line hint; the details live in the dialog. */
function ActionCell({ row, modelName }: { row: AuditRow; modelName: (id: string) => string }) {
  const t = useT()
  const fields = changedFields(row)
  const words = detailWords(row, t)
  const shown = fields.slice(0, 3)
  const only = fields.length === 1 ? row.metadata?.changes?.[fields[0]] : undefined
  const summary =
    only && !(only[0] === SECRET && only[1] === SECRET)
      ? `${t(FIELD_LABEL[fields[0]] ?? fields[0])}: ${valueWords(fields[0], only[0], t, modelName)} → ${valueWords(fields[0], only[1], t, modelName)}`
      : null
  const hint =
    words ||
    summary ||
    (fields.length
      ? shown.map((f) => t(FIELD_LABEL[f] ?? f)).join(' · ') +
        (fields.length > shown.length ? ` ${t('외 {n}개').replace('{n}', String(fields.length - shown.length))}` : '')
      : '')
  return (
    <div className="flex min-w-0 items-center gap-x-2">
      <Badge tone={SEVERITY[row.severity] ?? 'neutral'} className="shrink-0">
        {t(actionLabel(row))}
      </Badge>
      {hint && (
        <span className="min-w-0 truncate text-sm text-muted" title={hint}>
          {hint}
        </span>
      )}
    </div>
  )
}

function Target({ row }: { row: AuditRow }) {
  const t = useT()
  return <>{targetParts(row).map((part) => t(part)).join(' · ') || '—'}</>
}

/** IP · region · browser on one line, so a cell can cut it short. */
function Where({ row }: { row: AuditRow }) {
  const t = useT()
  const browser = browserName(row.userAgent)
  return (
    <span title={row.userAgent || undefined}>
      <span className="font-mono tabular-nums">{row.ip || '—'}</span>
      {row.region && ` · ${t(row.region)}`}
      {browser && ` · ${browser}`}
    </span>
  )
}

/** The dialog body: every recorded fact, then what changed. */
function Details({ row, modelName }: { row: AuditRow; modelName: (id: string) => string }) {
  const t = useT()
  const words = detailWords(row, t)
  const browser = browserName(row.userAgent)
  const place = [row.ip, row.region ? t(row.region) : ''].filter(Boolean).join(' · ')
  const item = (label: string, value: ReactNode) => (
    <Fragment key={label}>
      <dt className="text-faint">{t(label)}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </Fragment>
  )
  return (
    <>
      <dl
        className="grid gap-x-4 gap-y-2 text-base sm:grid-cols-[minmax(6rem,max-content)_1fr]"
        data-testid="audit-detail"
      >
        {item('시각', <span className="tabular-nums">{formatDateTime(row.at)}</span>)}
        {item(
          '계정',
          <span className="inline-flex items-center gap-1">
            {t(row.actor)}
            {row.actor.includes('@') && <CopyButton text={row.actor} label={t('계정')} />}
          </span>,
        )}
        {item('행위', <Badge tone={SEVERITY[row.severity] ?? 'neutral'}>{t(actionLabel(row))}</Badge>)}
        {item('대상', <Target row={row} />)}
        {item('내용', words || '—')}
        {item(
          '접속 위치',
          <span className="inline-flex items-center gap-1">
            <span className="font-mono tabular-nums">{place || '—'}</span>
            {row.ip && <CopyButton text={row.ip} label={t('IP 주소')} />}
          </span>,
        )}
        {item(
          '기록 ID',
          <span className="inline-flex items-center gap-1">
            <span className="font-mono text-xs break-all text-muted">{row.id}</span>
            <CopyButton text={row.id} label={t('기록 ID')} />
          </span>,
        )}
        {item(
          '브라우저',
          row.userAgent ? (
            <>
              {browser && <span className="block">{browser}</span>}
              <span className="block font-mono text-xs break-all text-muted">{row.userAgent}</span>
            </>
          ) : (
            '—'
          ),
        )}
      </dl>
      {changedFields(row).length > 0 && <Changes row={row} modelName={modelName} />}
    </>
  )
}

/** 「무엇이 바뀌었나」: item · before → after; older rows name fields only. */
function Changes({ row, modelName }: { row: AuditRow; modelName: (id: string) => string }) {
  const t = useT()
  const changes = row.metadata?.changes
  const fields = changedFields(row)
  return (
    <div className="rounded-control border border-line bg-panel px-3 py-2" data-testid="audit-changes">
      <p className="mb-1.5 text-xs font-medium text-faint">{t('무엇이 바뀌었나')}</p>
      {changes ? (
        <dl className="grid gap-x-4 gap-y-1.5 text-sm sm:grid-cols-[minmax(8rem,max-content)_1fr]">
          {Object.entries(changes).map(([field, [before, after]]) => (
            <Fragment key={field}>
              <dt className="font-medium">{t(FIELD_LABEL[field] ?? field)}</dt>
              {before === SECRET && after === SECRET ? (
                <dd className="text-muted">{t('새 값으로 바뀜 (값은 기록하지 않음)')}</dd>
              ) : (
                <dd className="flex min-w-0 flex-wrap items-baseline gap-x-1.5 break-words">
                  <span className="sr-only">{t('이전 값')}: </span>
                  <span className="text-muted line-through decoration-faint/60">
                    {valueWords(field, before, t, modelName)}
                  </span>
                  <span aria-hidden="true" className="text-faint">
                    →
                  </span>
                  <span className="sr-only">{t('바뀐 값')}: </span>
                  <span>{valueWords(field, after, t, modelName)}</span>
                </dd>
              )}
            </Fragment>
          ))}
        </dl>
      ) : (
        <>
          <p className="text-sm">{fields.map((f) => t(FIELD_LABEL[f] ?? f)).join(', ')}</p>
          <p className="mt-1 text-xs text-faint">
            {t('이 기록은 값을 남기기 전에 쓰여 바뀐 항목만 있습니다.')}
          </p>
        </>
      )}
    </div>
  )
}
