import { Download, TrendingUp } from 'lucide-react'
import { useEffect, useState } from 'react'
import { type StorageReport, usageApi } from '@/lib/api'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Button, Card, PageHeader } from '@/components/ui'
import { kindMeta } from '@/lib/kinds'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/**
 * Everything on this screen is computed from stored turns — never seeded.
 * An admin cannot plan a budget against numbers nobody spent, so an empty
 * instance shows empty.
 */
function Bars({
  rows,
  format,
}: {
  rows: { label: string; value: number; sub?: string; color?: string }[]
  format: (v: number) => string
}) {
  const max = Math.max(...rows.map((r) => r.value), 1)
  return (
    <div className="space-y-2.5">
      {rows.map((r) => (
        <div key={r.label} className="flex items-center gap-3">
          <span className="w-44 shrink-0 truncate text-base">{r.label}</span>
          <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-elevated">
            <div
              className="h-full rounded-full"
              style={{ width: `${(r.value / max) * 100}%`, background: r.color ?? 'var(--accent)' }}
            />
          </div>
          <span className="w-28 shrink-0 text-right text-sm tabular-nums text-muted">
            {format(r.value)}
          </span>
          {r.sub && <span className="w-24 shrink-0 text-right text-xs text-faint">{r.sub}</span>}
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

export function AdminUsagePage() {
  const t = useT()
  const { usage, loadUsage } = useStore()
  const [days, setDays] = useState<number>(7)
  const [storage, setStorage] = useState<StorageReport | null>(null)

  useEffect(() => {
    void loadUsage(days)
  }, [loadUsage, days])
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
  // The ledger can charge against no conversation at all, which is a surface
  // the five-kind table has no entry for.
  const surface = (kind: string) =>
    kind in kindMeta ? t(kindMeta[kind as SessionKind].label) : t('기타')
  // Free local models cost nothing, so a credit chart for an instance that only
  // runs them is a row of zeroes. Plot what did happen instead of nothing.
  const byCredits = (usage?.totals.credits ?? 0) > 0
  const daily = (usage?.daily ?? []).map((d) => ({
    ...d,
    value: byCredits ? d.credits : d.requests,
  }))
  const maxDaily = Math.max(...daily.map((d) => d.value), 1)

  const exportCsv = () => {
    if (!usage) return
    const rows = [
      [t('날짜'), t('크레딧'), t('요청')],
      ...usage.daily.map((d) => [d.date, String(d.credits), String(d.requests)]),
      [],
      [t('모델'), t('크레딧'), t('요청'), t('사용자')],
      ...usage.byModel.map((m) => [m.model, String(m.credits), String(m.requests), String(m.users)]),
      ...(usage.totals.otherCredits > 0
        ? [[t('기타'), String(usage.totals.otherCredits), '', '']]
        : []),
    ]
    const csv = rows.map((r) => r.join(',')).join('\n')
    const url = URL.createObjectURL(new Blob([`﻿${csv}`], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `kloudchat-usage-${days}d.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <>
      <TopBar left={t('사용량')} />
      <PageBody>
        <PageHeader
          title={t('사용량')}
          description={t('실제 사용 기록을 집계한 값입니다. 아직 사용한 사람이 없으면 비어 있습니다.')}
          action={
            <div className="flex items-center gap-2">
              <div className="flex rounded-control border border-line p-0.5">
                {RANGES.map((r) => (
                  <button
                    key={r}
                    onClick={() => setDays(r)}
                    className={
                      days === r
                        ? 'min-w-11 rounded-control bg-elevated px-3 py-2 text-sm font-medium'
                        : 'min-w-11 rounded-control px-3 py-2 text-sm text-muted hover:text-fg'
                    }
                  >
                    {t('{n}일').replace('{n}', String(r))}
                  </button>
                ))}
              </div>
              <Button onClick={exportCsv} disabled={!usage}>
                <Download size={15} />
                {t('CSV 내보내기')}
              </Button>
            </div>
          }
        />

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
            <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label={t('사용 크레딧')} value={usage.totals.credits.toLocaleString()} />
              <Stat
                label={t('배정 대비')}
                // Against the active roster only. Counting pending and rejected
                // rows in the denominator is what produced a ratio in the millions.
                value={
                  usage.totals.allocatedCredits > 0
                    ? `${((usage.totals.credits / usage.totals.allocatedCredits) * 100).toFixed(2)}%`
                    : '—'
                }
                hint={t('활성 계정 배정 {n} cr').replace('{n}', usage.totals.allocatedCredits.toLocaleString())}
              />
              <Stat label={t('응답 수')} value={usage.totals.requests.toLocaleString()} />
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
              {/* The bar's percentage is of the space between the two labels,
                  so that space has to be a box with a height of its own — a
                  percentage against an auto-height parent resolves to nothing
                  and the chart draws blank. */}
              <div className="flex h-40 items-stretch gap-1.5">
                {daily.map((d) => (
                  <div key={d.date} className="flex min-w-0 flex-1 flex-col items-center gap-1">
                    <span className="text-2xs tabular-nums text-faint">
                      {d.value > 0 ? d.value.toLocaleString() : ''}
                    </span>
                    <div className="flex w-full flex-1 items-end">
                      <div
                        className="w-full rounded-t bg-accent"
                        // An empty day is an empty column: the server now sends
                        // every day of the window, and a two-pixel stub would
                        // read as a small number rather than none.
                        style={{ height: d.value > 0 ? `${Math.max(3, (d.value / maxDaily) * 100)}%` : 0 }}
                        title={`${t('{n}건').replace('{n}', String(d.requests))} · ${d.credits.toLocaleString()} cr`}
                      />
                    </div>
                    {/* Not truncated — see `MyUsagePage`: `MM-DD` does not fit
                        a column's width over a month and every date came out
                        as `08-…`. */}
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
                          : '',
                    ]
                      .filter(Boolean)
                      .join(' · '),
                  })),
                  // Named rather than dropped: a bar chart that quietly omits
                  // part of the total is how the whole total ended up here.
                  ...(usage.totals.otherCredits > 0
                    ? [{ label: t('기타'), value: usage.totals.otherCredits }]
                    : []),
                ]}
                format={cr}
              />
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
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
                        {t('올린 파일과 만든 그림·클립')}{' '}
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
              <p className="mb-3 text-base font-medium">{t('사용자별')}</p>
              <div className="overflow-x-auto">
                <table className="w-full text-base">
                  <thead className="text-xs text-faint">
                    <tr className="text-left">
                      <th className="py-1 pr-3 font-medium">{t('사용자')}</th>
                      <th className="py-1 pr-3 text-right font-medium">{t('크레딧')}</th>
                      <th className="py-1 pr-3 text-right font-medium">{t('한도 대비')}</th>
                      <th className="py-1 pr-3 text-right font-medium">{t('요청')}</th>
                      <th className="py-1 pr-3 text-right font-medium">{t('저장 용량')}</th>
                      <th className="py-1 text-right font-medium">{t('파일')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.topUsers.map((u) => {
                      const disk = storageOf.get(u.id)
                      return (
                        <tr key={u.id} className="border-t border-line">
                          <td className="min-w-0 max-w-48 truncate py-1.5 pr-3">
                            {u.name}
                            <span className="ml-1 text-xs text-faint">{u.email}</span>
                          </td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">{u.credits.toLocaleString()}</td>
                          <td className="py-1.5 pr-3 text-right text-xs text-faint tabular-nums">
                            {u.allowance > 0
                              ? `${((u.credits / u.allowance) * 100).toFixed(1)}%`
                              : t('한도 없음')}
                          </td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">{u.requests.toLocaleString()}</td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">{disk ? bytes(disk.bytes) : '–'}</td>
                          <td className="py-1.5 text-right tabular-nums text-muted">{disk ? disk.files : '–'}</td>
                        </tr>
                      )
                    })}
                    {(storage?.byUser ?? [])
                      .filter((d) => !usage.topUsers.some((u) => u.id === d.id))
                      .map((d) => (
                        <tr key={d.id} className="border-t border-line text-muted">
                          <td className="min-w-0 max-w-48 truncate py-1.5 pr-3">
                            {d.name}
                            <span className="ml-1 text-xs text-faint">{d.email}</span>
                          </td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">0</td>
                          <td className="py-1.5 pr-3 text-right text-xs text-faint">–</td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">0</td>
                          <td className="py-1.5 pr-3 text-right tabular-nums">{bytes(d.bytes)}</td>
                          <td className="py-1.5 text-right tabular-nums">{d.files}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </>
        )}
      </PageBody>
    </>
  )
}
