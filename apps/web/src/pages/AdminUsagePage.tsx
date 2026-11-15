import { Download, TrendingUp } from 'lucide-react'
import { useEffect, useState } from 'react'
import { type StorageReport, usageApi } from '@/lib/api'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Button, Card, Input, PageHeader } from '@/components/ui'
import { DataTable } from '@/components/ui/DataTable'
import { ListSearch, NoMatch } from '@/components/ui/ListSearch'
import { downloadCsv } from '@/lib/csv'
import { matchesQuery, serviceDate, shiftDate } from '@/lib/utils'
import { kindMeta } from '@/lib/kinds'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { useT, useTIn } from '@/lib/useT'

function Bars({
  rows,
  format,
}: {
  rows: { label: string; value: number; sub?: string; color?: string }[]
  format: (v: number) => string
}) {
  const max = Math.max(...rows.map((r) => r.value), 1)
  return (
    // One grid for all rows: the amount and note columns take their widest entry, so a note
    // stays on one line and the bar gives up the room.
    <div className="space-y-2.5 sm:grid sm:grid-cols-[11rem_minmax(0,1fr)_auto_auto] sm:items-center sm:gap-x-3 sm:gap-y-2.5 sm:space-y-0">
      {rows.map((r) => (
        // A phone puts the label and note on one line and the bar under them, so every bar
        // has the same room; the break is no grid cell (display: none) on wider screens.
        <div key={r.label} className="flex flex-wrap items-center gap-x-3 gap-y-1 sm:contents">
          <span className="truncate text-base max-sm:order-1 max-sm:min-w-0 max-sm:flex-1">{r.label}</span>
          <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-elevated max-sm:order-4">
            <div
              className="h-full rounded-full"
              style={{ width: `${(r.value / max) * 100}%`, background: r.color ?? 'var(--accent)' }}
            />
          </div>
          <span className="min-w-20 text-right text-sm whitespace-nowrap tabular-nums text-muted max-sm:order-5">
            {format(r.value)}
          </span>
          {/* Always drawn: a missing cell would shift the next row's cells. */}
          <span className="text-right text-xs whitespace-nowrap text-faint max-sm:order-2">{r.sub}</span>
          <span aria-hidden className="hidden max-sm:order-3 max-sm:block max-sm:basis-full" />
        </div>
      ))}
    </div>
  )
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="p-4">
      <p className="text-xs tracking-wide text-faint uppercase">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-faint">{hint}</p>}
    </Card>
  )
}

const RANGES = [7, 30, 90] as const

type UsageRow = {
  id: string
  name: string
  email: string
  credits: number
  share: number | null
  requests: number
  searches: number
  bytes: number | null
  files: number | null
  /** Holds files but used nothing in the period. */
  idle: boolean
}

export function AdminUsagePage() {
  const t = useT()
  const tPeriod = useTIn('period')
  const { usage, loadUsage } = useStore()
  const [days, setDays] = useState<number>(7)
  // 직접 지정: calendar days in the service zone, both ends included.
  const [custom, setCustom] = useState(false)
  const today = serviceDate(new Date())
  const [from, setFrom] = useState(() => shiftDate(today, -29))
  const [to, setTo] = useState(today)
  const range = custom && from && to && from <= to ? { start: from, end: to } : undefined
  const [storage, setStorage] = useState<StorageReport | null>(null)
  const [userQuery, setUserQuery] = useState('')

  useEffect(() => {
    if (custom && !range) return
    void loadUsage(days, range)
    // `range` is rebuilt every render; its two dates are what matter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadUsage, days, custom, range?.start, range?.end])
  useEffect(() => {
    void usageApi.storage().then(setStorage).catch(() => setStorage(null))
  }, [])
  const bytes = (n: number) => {
    if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`
    if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`
    return `${Math.round(n / 1024)} KB`
  }
  const storageOf = new Map((storage?.byUser ?? []).map((u) => [u.id, u]))
  const [reclaiming, setReclaiming] = useState(false)
  const [reclaimed, setReclaimed] = useState<string | null>(null)
  const reclaim = async () => {
    setReclaiming(true)
    setReclaimed(null)
    try {
      const done = await usageApi.reclaimStorage()
      setReclaimed(
        t('{files}개, {size}를 지웠습니다.')
          .replace('{files}', done.freedFiles.toLocaleString())
          .replace('{size}', bytes(done.freedBytes)),
      )
      setStorage(await usageApi.storage())
    } catch {
      setReclaimed(t('정리하지 못했습니다.'))
    } finally {
      setReclaiming(false)
    }
  }

  const cr = (v: number) => `${v.toLocaleString()} cr`
  // Charges outside a conversation: no kind entry.
  const surface = (kind: string) =>
    kind === 'bot' ? t('공개 챗봇') : kind === 'api' ? t('API 키') : kind in kindMeta ? t(kindMeta[kind as SessionKind].label) : t('기타')
  // Free models bill nothing: zero credits → plot requests.
  const byCredits = (usage?.totals.credits ?? 0) > 0
  const daily = (usage?.daily ?? []).map((d) => ({
    ...d,
    value: byCredits ? d.credits : d.requests,
  }))
  const maxDaily = Math.max(...daily.map((d) => d.value), 1)

  // Everyone who used credits, then those who only hold files.
  const perUser: UsageRow[] = usage
    ? [
        ...usage.topUsers.map((u) => {
          const disk = storageOf.get(u.id)
          return {
            id: u.id,
            name: u.name,
            email: u.email,
            credits: u.credits,
            share: u.allowance > 0 ? (u.credits / u.allowance) * 100 : null,
            requests: u.requests,
            searches: u.searches,
            bytes: disk ? disk.bytes : null,
            files: disk ? disk.files : null,
            idle: false,
          }
        }),
        ...(storage?.byUser ?? [])
          .filter((d) => !usage.topUsers.some((u) => u.id === d.id))
          .map((d) => ({
            id: d.id,
            name: d.name,
            email: d.email,
            credits: 0,
            share: null,
            requests: 0,
            searches: 0,
            bytes: d.bytes,
            files: d.files,
            idle: true,
          })),
      ]
    : []

  const exportCsv = () => {
    if (!usage) return
    downloadCsv(range ? `kloudchat-usage-${range.start}_${range.end}.csv` : `kloudchat-usage-${days}d.csv`, [
      [t('날짜'), t('크레딧'), t('요청'), t('웹 검색')],
      ...usage.daily.map((d) => [d.date, d.credits, d.requests, d.searches]),
      [],
      [t('모델'), t('크레딧'), t('요청'), t('사용자')],
      ...usage.byModel.map((m) => [m.model, m.credits, m.requests, m.users]),
      ...(usage.totals.otherCredits > 0 ? [[t('기타'), usage.totals.otherCredits, '', '']] : []),
      [],
      [t('사용자'), t('이메일'), t('크레딧'), t('한도 대비'), t('요청'), t('웹 검색'), t('저장 용량'), t('파일')],
      ...perUser.map((r) => [
        r.name,
        r.email,
        r.credits,
        r.share === null ? '' : `${r.share.toFixed(1)}%`,
        r.requests,
        r.searches,
        r.bytes ?? '',
        r.files ?? '',
      ]),
    ])
  }

  return (
    <>
      <TopBar left={t('사용량')} />
      <PageBody>
        <PageHeader
          title={t('사용량')}
          description={t('실제 사용 기록을 집계한 값입니다. 아직 사용한 사람이 없으면 비어 있습니다.')}
          action={
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex rounded-control border border-line p-0.5">
                {RANGES.map((r) => (
                  <button
                    key={r}
                    onClick={() => {
                      setCustom(false)
                      setDays(r)
                    }}
                    aria-pressed={!custom && days === r}
                    className={
                      !custom && days === r
                        ? 'min-w-11 rounded-control bg-elevated px-3 py-2 text-sm font-medium'
                        : 'min-w-11 rounded-control px-3 py-2 text-sm text-muted hover:text-fg'
                    }
                  >
                    {t('{n}일').replace('{n}', String(r))}
                  </button>
                ))}
                <button
                  onClick={() => setCustom(true)}
                  aria-pressed={custom}
                  className={
                    custom
                      ? 'min-w-11 rounded-control bg-elevated px-3 py-2 text-sm font-medium'
                      : 'min-w-11 rounded-control px-3 py-2 text-sm text-muted hover:text-fg'
                  }
                >
                  {tPeriod('직접 지정')}
                </button>
              </div>
              <Button onClick={exportCsv} disabled={!usage}>
                <Download size={15} />
                {t('CSV 내보내기')}
              </Button>
            </div>
          }
        />

        {custom && (
          <div className="mb-4 flex flex-wrap items-center gap-2" data-testid="usage-range">
            <Input
              type="date"
              value={from}
              max={to || today}
              onChange={(e) => setFrom(e.target.value)}
              aria-label={t('시작일')}
              className="w-auto"
            />
            <span className="text-faint">–</span>
            <Input
              type="date"
              value={to}
              min={from || undefined}
              max={today}
              onChange={(e) => setTo(e.target.value)}
              aria-label={t('종료일')}
              className="w-auto"
            />
            {!range && <span className="text-sm text-danger">{t('시작일이 종료일보다 늦습니다')}</span>}
          </div>
        )}

        {!usage ? (
          <Card className="p-10 text-center text-base text-muted">{t('집계를 불러오는 중입니다…')}</Card>
        ) : usage.totals.requests === 0 ? (
          <Card className="p-10 text-center">
            <p className="text-base font-medium">{t('이 기간에 기록된 응답이 없습니다')}</p>
            <p className="mt-1 text-base text-muted">
              {t('누군가 대화를 시작하면 여기에 모델별·화면별 사용량이 쌓입니다.')}
            </p>
          </Card>
        ) : (
          <>
            <div className="mb-5 grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
              <Stat label={t('사용 크레딧')} value={usage.totals.credits.toLocaleString()} />
              <Stat
                label={t('배정 대비')}
                // Active roster only.
                value={
                  usage.totals.allocatedCredits > 0
                    ? `${((usage.totals.credits / usage.totals.allocatedCredits) * 100).toFixed(2)}%`
                    : '—'
                }
                hint={t('활성 계정 배정 {n} cr').replace('{n}', usage.totals.allocatedCredits.toLocaleString())}
              />
              <Stat label={t('응답 수')} value={usage.totals.requests.toLocaleString()} />
              <Stat
                label={t('웹 검색')}
                value={usage.totals.searches.toLocaleString()}
                // Per answer: search jumps read against traffic.
                hint={t('응답 1건당 {n}회').replace(
                  '{n}',
                  (usage.totals.searches / Math.max(1, usage.totals.requests)).toFixed(1),
                )}
              />
              <Stat label={t('사용한 사람')} value={String(usage.totals.activeUsers)} />
            </div>

            <Card className="mb-4 p-4">
              <p className="mb-3 flex items-center gap-1.5 text-base font-medium">
                <TrendingUp size={15} />
                {t('일별')} {byCredits ? t('크레딧') : t('응답 수')}
                {!byCredits && (
                  <span className="font-normal text-faint">
                    — {t('이 기간에는 과금되는 모델을 쓰지 않았습니다')}
                  </span>
                )}
              </p>
              {/* Fixed height: bar heights are percentages of it. */}
              <div className="flex h-40 items-stretch gap-1.5">
                {daily.map((d) => (
                  <div key={d.date} className="flex min-w-0 flex-1 flex-col items-center gap-1">
                    <span className="text-2xs tabular-nums text-faint">
                      {d.value > 0 ? d.value.toLocaleString() : ''}
                    </span>
                    <div className="flex w-full flex-1 items-end">
                      <div
                        className="w-full rounded-t bg-accent"
                        style={{ height: d.value > 0 ? `${Math.max(3, (d.value / maxDaily) * 100)}%` : 0 }}
                        title={`${t('{n}건').replace('{n}', String(d.requests))} · ${d.credits.toLocaleString()} cr · ${t('웹 검색 {n}회').replace('{n}', String(d.searches))}`}
                      />
                    </div>
                    {/* Overflows rather than truncates: `MM-DD` wider than a column. */}
                    <span className="overflow-visible text-2xs whitespace-nowrap text-faint">
                      {d.date.slice(5)}
                    </span>
                  </div>
                ))}
              </div>
            </Card>

            <Card className="mb-4 p-4">
              <p className="mb-3 text-base font-medium">{t('모델별')}</p>
              <Bars
                rows={[
                  ...usage.byModel.map((m) => ({
                    label: m.model,
                    value: m.credits,
                    sub: [
                      t('{n}회').replace('{n}', String(m.requests)),
                      t('{n}명').replace('{n}', String(m.users)),
                      m.units && m.unit === 'seconds'
                        ? t('{n}초 받아씀').replace('{n}', m.units.toLocaleString())
                        : m.units && m.unit === 'chunks'
                          ? t('{n}청크 색인').replace('{n}', m.units.toLocaleString())
                          : m.units && m.unit === 'pages'
                            ? t('{n}쪽 글자 읽음').replace('{n}', m.units.toLocaleString())
                            : '',
                    ]
                      .filter(Boolean)
                      .join(' · '),
                  })),
                  ...(usage.totals.otherCredits > 0
                    ? [{ label: t('기타'), value: usage.totals.otherCredits }]
                    : []),
                ]}
                format={cr}
              />
            </Card>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card className="p-4">
                <p className="mb-3 text-base font-medium">{t('화면별')}</p>
                <Bars
                  rows={usage.bySurface.map((s) => ({
                    label: surface(s.kind),
                    value: s.credits,
                    sub: t('{n}건').replace('{n}', String(s.requests)),
                    color: kindMeta[s.kind as SessionKind]?.color,
                  }))}
                  format={cr}
                />
              </Card>
              <Card className="p-4">
                <p className="mb-3 text-base font-medium">{t('저장 공간')}</p>
                {storage ? (
                  <>
                    <div className="flex items-baseline justify-between text-base">
                      <span>
                        {t('올린 파일과 만든 이미지·클립')}{' '}
                        <span className="text-faint">· {t('{n}개').replace('{n}', storage.files.toLocaleString())}</span>
                      </span>
                      <span className="tabular-nums">{bytes(storage.usedBytes)}</span>
                    </div>
                    <div className="mt-2 h-2 overflow-hidden rounded-full bg-elevated">
                      <div
                        className="h-full bg-accent"
                        style={{
                          width: `${Math.min(100, ((storage.diskTotalBytes - storage.diskFreeBytes) / Math.max(1, storage.diskTotalBytes)) * 100)}%`,
                        }}
                      />
                    </div>
                    <p className="mt-1.5 text-xs text-faint">
                      {t('디스크 {used} 사용 · {free} 남음 · 전체 {total}')
                        .replace('{used}', bytes(storage.diskTotalBytes - storage.diskFreeBytes))
                        .replace('{free}', bytes(storage.diskFreeBytes))
                        .replace('{total}', bytes(storage.diskTotalBytes))}
                      <span className="font-mono"> · {storage.path}</span>
                    </p>
                    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-base">
                      <span>
                        {t('삭제된 계정의 파일')}{' '}
                        <span className="tabular-nums">{bytes(storage.orphanBytes)}</span>
                        <span className="text-faint">
                          {' '}
                          · {t('{n}개').replace('{n}', storage.orphanFiles.toLocaleString())}
                          {storage.reclaimAt > 0 &&
                            ` · ${t('디스크가 {pct}% 차면 오래된 것부터 자동으로 지웁니다').replace('{pct}', String(Math.round(storage.reclaimAt * 100)))}`}
                        </span>
                      </span>
                      <Button
                        variant="secondary"
                        size="sm"
                        disabled={reclaiming || storage.orphanFiles === 0}
                        title={
                          reclaiming
                            ? t('정리가 끝날 때까지 기다려 주세요')
                            : storage.orphanFiles === 0
                              ? t('정리할 고아 파일이 없습니다')
                              : undefined
                        }
                        onClick={() => void reclaim()}
                      >
                        {reclaiming ? t('정리하는 중…') : t('고아 파일 정리')}
                      </Button>
                    </div>
                    {reclaimed && <p className="mt-1 text-xs text-success">{reclaimed}</p>}
                  </>
                ) : (
                  <p className="text-base text-muted">{t('저장 공간 정보를 불러오지 못했습니다.')}</p>
                )}
              </Card>
            </div>

            <Card className="mt-4 p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <p className="text-base font-medium">
                  {t('사용자별')}{' '}
                  <span className="text-sm font-normal text-faint tabular-nums">
                    {t('{n}명').replace('{n}', perUser.length.toLocaleString())}
                  </span>
                </p>
                <ListSearch value={userQuery} onChange={setUserQuery} label={t('사용자 검색')} className="w-56" />
              </div>
              <DataTable
                id="admin.usage.users"
                rows={perUser.filter((r) => matchesQuery(userQuery, r.name, r.email))}
                rowKey={(r) => r.id}
                label={t('사용자별')}
                initialSort={{ key: 'credits', dir: 'desc' }}
                maxHeight="32rem"
                className="rounded-none border-0"
                headClassName="bg-panel text-xs text-faint"
                cellClassName="py-1.5 pr-3"
                rowClassName={(r) => (r.idle ? 'text-muted' : undefined)}
                empty={<NoMatch />}
                columns={[
                  {
                    key: 'name',
                    label: t('사용자'),
                    value: (r) => r.name,
                    className: 'max-w-48 min-w-0 truncate',
                    cell: (r) => (
                      <>
                        {r.name}
                        <span className="ml-1 text-xs text-faint">{r.email}</span>
                      </>
                    ),
                  },
                  { key: 'credits', label: t('크레딧'), align: 'right', value: (r) => r.credits, cell: (r) => r.credits.toLocaleString() },
                  {
                    key: 'share',
                    label: t('한도 대비'),
                    align: 'right',
                    value: (r) => r.share,
                    className: 'text-xs text-faint',
                    cell: (r) => (r.share === null ? (r.idle ? '–' : t('한도 없음')) : `${r.share.toFixed(1)}%`),
                  },
                  { key: 'requests', label: t('요청'), align: 'right', value: (r) => r.requests, cell: (r) => r.requests.toLocaleString() },
                  { key: 'searches', label: t('웹 검색'), align: 'right', value: (r) => r.searches, cell: (r) => r.searches.toLocaleString() },
                  { key: 'bytes', label: t('저장 용량'), align: 'right', value: (r) => r.bytes, cell: (r) => (r.bytes === null ? '–' : bytes(r.bytes)) },
                  { key: 'files', label: t('파일'), align: 'right', value: (r) => r.files, className: 'text-muted', cell: (r) => r.files ?? '–' },
                ]}
              />
            </Card>
          </>
        )}
      </PageBody>
    </>
  )
}
