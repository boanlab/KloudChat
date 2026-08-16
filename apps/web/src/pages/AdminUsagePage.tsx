import { Download, TrendingUp } from 'lucide-react'
import { useEffect, useState } from 'react'
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
          <span className="w-44 shrink-0 truncate text-[13px]">{r.label}</span>
          <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-elevated">
            <div
              className="h-full rounded-full"
              style={{ width: `${(r.value / max) * 100}%`, background: r.color ?? 'var(--accent)' }}
            />
          </div>
          <span className="w-28 shrink-0 text-right text-[12px] tabular-nums text-muted">
            {format(r.value)}
          </span>
          {r.sub && <span className="w-24 shrink-0 text-right text-[11px] text-faint">{r.sub}</span>}
        </div>
      ))}
    </div>
  )
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="p-4">
      <p className="text-[11px] tracking-wide text-faint uppercase">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-0.5 text-[11px] text-faint">{hint}</p>}
    </Card>
  )
}

const RANGES = [7, 30, 90] as const

export function AdminUsagePage() {
  const t = useT()
  const { usage, loadUsage } = useStore()
  const [days, setDays] = useState<number>(7)

  useEffect(() => {
    void loadUsage(days)
  }, [loadUsage, days])

  const cr = (v: number) => `${v.toLocaleString()} cr`
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
              <div className="flex rounded-lg border border-line p-0.5">
                {RANGES.map((r) => (
                  <button
                    key={r}
                    onClick={() => setDays(r)}
                    className={
                      days === r
                        ? 'min-w-11 rounded-md bg-elevated px-3 py-2 text-[12px] font-medium'
                        : 'min-w-11 rounded-md px-3 py-2 text-[12px] text-muted hover:text-fg'
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
          <Card className="p-10 text-center text-[13px] text-muted">{t('집계를 불러오는 중입니다…')}</Card>
        ) : usage.totals.requests === 0 ? (
          <Card className="p-10 text-center">
            <p className="text-sm font-medium">{t('이 기간에 기록된 응답이 없습니다')}</p>
            <p className="mt-1 text-[13px] text-muted">
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
              <p className="mb-3 flex items-center gap-1.5 text-[13px] font-medium">
                <TrendingUp size={15} />
                {t('일별')} {byCredits ? t('크레딧') : t('응답 수')}
                {!byCredits && (
                  <span className="font-normal text-faint">
                    — {t('이 기간에는 과금되는 모델을 쓰지 않았습니다')}
                  </span>
                )}
              </p>
              <div className="flex h-40 items-end gap-1.5">
                {daily.map((d) => (
                  <div key={d.date} className="flex min-w-0 flex-1 flex-col items-center gap-1">
                    <span className="text-[10px] tabular-nums text-faint">
                      {d.value > 0 ? d.value.toLocaleString() : ''}
                    </span>
                    <div
                      className="w-full rounded-t bg-accent"
                      style={{ height: `${(d.value / maxDaily) * 100}%` }}
                      title={`${t('{n}건').replace('{n}', String(d.requests))} · ${d.credits.toLocaleString()} cr`}
                    />
                    <span className="truncate text-[10px] text-faint">{d.date.slice(5)}</span>
                  </div>
                ))}
              </div>
            </Card>

            <Card className="mb-4 p-4">
              <p className="mb-3 text-[13px] font-medium">{t('모델별')}</p>
              <Bars
                rows={usage.byModel.map((m) => ({
                  label: m.model,
                  value: m.credits,
                  sub: `${t('{n}회').replace('{n}', String(m.requests))} · ${t('{n}명').replace('{n}', String(m.users))}`,
                }))}
                format={cr}
              />
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card className="p-4">
                <p className="mb-3 text-[13px] font-medium">{t('화면별')}</p>
                <Bars
                  rows={usage.bySurface.map((s) => ({
                    label: t(kindMeta[s.kind as SessionKind]?.label ?? s.kind),
                    value: s.credits,
                    sub: t('{n}건').replace('{n}', String(s.requests)),
                    color: kindMeta[s.kind as SessionKind]?.color,
                  }))}
                  format={cr}
                />
              </Card>
              <Card className="p-4">
                <p className="mb-3 text-[13px] font-medium">{t('사용량 상위')}</p>
                <div className="space-y-2">
                  {usage.topUsers.map((u, i) => (
                    <div key={u.id} className="flex items-center gap-3 text-[13px]">
                      <span className="w-4 text-faint tabular-nums">{i + 1}</span>
                      <span className="min-w-0 flex-1 truncate">{u.name}</span>
                      <span className="text-[11px] text-faint">
                        {u.allowance > 0
                          ? `${((u.credits / u.allowance) * 100).toFixed(1)}%`
                          : t('한도 없음')}
                      </span>
                      <span className="w-24 text-right tabular-nums text-muted">
                        {u.credits.toLocaleString()}
                      </span>
                    </div>
                  ))}
                </div>
              </Card>
            </div>
          </>
        )}
      </PageBody>
    </>
  )
}
