import { useEffect, useState } from 'react'
import { PageBody } from '@/components/layout/AppShell'
import { Card, EmptyState } from '@/components/ui'
import { kindMeta } from '@/lib/kinds'
import { type MyUsage, meApi } from '@/lib/api'
import { cn, formatTokens } from '@/lib/utils'
import { BarChart3 } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useT } from '@/lib/useT'

/**
 * What this account has spent.
 *
 * All of it belongs to the caller. Unlike the admin screen, which answers for
 * the whole instance, this never shows a colleague's usage.
 */
const RANGES = [7, 30, 90]

export function MyUsagePage() {
  const t = useT()
  const [days, setDays] = useState(30)
  const [data, setData] = useState<MyUsage | null>(null)

  useEffect(() => {
    let live = true
    void meApi.usage(days).then((d) => live && setData(d))
    return () => {
      live = false
    }
  }, [days])

  const cycle = data?.cycle
  const pct = cycle && cycle.allowance > 0 ? Math.min(100, (cycle.used / cycle.allowance) * 100) : 0
  // A month spent entirely on self-hosted models bills nothing, and a chart of
  // zeroes says nothing happened when 260 turns a day did. Plot the turns.
  const byCredits = (data?.totals.credits ?? 0) > 0
  const daily = (data?.daily ?? []).map((d) => ({ ...d, value: byCredits ? d.credits : d.requests }))
  const peak = Math.max(1, ...daily.map((d) => d.value))
  const other = data?.totals.otherCredits ?? 0
  // The ledger can charge against no conversation at all, which is a surface
  // the five-kind table has no entry for.
  const surface = (kind: string) =>
    kind in kindMeta ? t(kindMeta[kind as keyof typeof kindMeta].label) : t('기타')

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('사용량')}</span>} />
      <PageBody>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t('사용량')}</h1>
          <p className="mt-1 text-base text-muted">
            {t('이 계정이 쓴 크레딧입니다. 매달 1일에 배정량으로 다시 채워집니다.')}
          </p>
        </div>
        <div className="flex gap-1 rounded-control border border-line bg-elevated p-1">
          {RANGES.map((r) => (
            <button
              key={r}
              onClick={() => setDays(r)}
              className={cn(
                'min-w-11 rounded-control px-3 py-2 text-base transition-colors',
                days === r ? 'bg-panel font-medium text-fg shadow-raised' : 'text-muted hover:text-fg',
              )}
            >
              {t('{n}일').replace('{n}', String(r))}
            </button>
          ))}
        </div>
      </div>

      {cycle && (
        <Card className="mt-5 p-4">
          <div className="flex items-baseline justify-between">
            <p className="text-base font-medium">{t('이번 달')}</p>
            <p className="text-base text-muted">
              <span className="tabular-nums text-fg">{cycle.remaining.toLocaleString()}</span>{' '}
              {t('남음')}
              {cycle.allowance > 0 && ` · ${t('배정 {n}').replace('{n}', cycle.allowance.toLocaleString())}`}
            </p>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-elevated">
            <div
              className={cn('h-full rounded-full', pct > 90 ? 'bg-danger' : 'bg-accent')}
              style={{ width: `${pct}%` }}
            />
          </div>
          {/* Free models bill nothing, so a busy month can read as zero spend.
              Saying both numbers stops that looking like a broken counter. */}
          <p className="mt-2 text-xs text-faint">
            {t('최근 {days}일 동안 {reqs}회 요청 · {credits} 크레딧. 자체 GPU 모델은 크레딧을 쓰지 않습니다.')
              .replace('{days}', String(days))
              .replace('{reqs}', formatTokens(data?.totals.requests ?? 0))
              .replace('{credits}', (data?.totals.credits ?? 0).toLocaleString())}
          </p>
        </Card>
      )}

      {data && data.totals.requests === 0 ? (
        <div className="mt-6">
          <EmptyState
            icon={<BarChart3 size={18} />}
            title={t('이 기간에 사용 기록이 없습니다')}
            description={t('대화를 시작하면 모델별·화면별 사용량이 여기에 쌓입니다.')}
          />
        </div>
      ) : (
        <>
          <Card className="mt-4 p-4">
            <p className="text-base font-medium">
              {t('일별')} {byCredits ? t('크레딧') : t('응답 수')}
              {!byCredits && (
                <span className="font-normal text-faint">
                  {' '}
                  — {t('이 기간에는 과금되는 모델을 쓰지 않았습니다')}
                </span>
              )}
            </p>
            <div className="mt-3 flex h-28 items-end gap-1">
              {daily.map((d) => (
                <div key={d.date} className="group relative flex-1">
                  <div
                    className="rounded-t bg-accent/70 transition-colors group-hover:bg-accent"
                    style={{ height: `${Math.max(2, (d.value / peak) * 100)}%` }}
                  />
                  <span className="pointer-events-none absolute -top-6 left-1/2 hidden -translate-x-1/2 rounded bg-panel px-1.5 py-0.5 text-xs whitespace-nowrap shadow group-hover:block">
                    {d.date.slice(5)} · {t('{n}회').replace('{n}', String(d.requests))} ·{' '}
                    {t('{n} 크레딧').replace('{n}', d.credits.toLocaleString())}
                  </span>
                </div>
              ))}
            </div>
          </Card>

          {(data?.apiKeys ?? []).length > 0 && (
            <Card className="mt-4 p-4">
              <p className="text-base font-medium">{t('API 키')}</p>
              <p className="mt-0.5 text-sm text-muted">
                {t('코딩 에이전트처럼 외부 도구가 이 키로 쓴 몫입니다. 위 사용량과 따로 집계되지만, 월 한도는 계정 하나에 걸려 있어 키를 여러 개 만들어도 같은 한도를 나눠 씁니다.')}
              </p>
              <ul className="mt-2 space-y-1.5">
                {(data?.apiKeys ?? []).map((k) => (
                  <li key={k.id} className="flex items-baseline justify-between gap-3 text-base">
                    <span className="min-w-0 truncate">
                      {k.name} <span className="font-mono text-sm text-faint">{k.preview}</span>
                    </span>
                    <span className="shrink-0 tabular-nums">
                      {t('{n} 크레딧').replace('{n}', k.credits.toLocaleString())}
                      {k.budgetUsd > 0 && (
                        <span className="text-faint">
                          {' '}
                          · {t('한도 {n}').replace('{n}', (k.budgetUsd * 100_000).toLocaleString())}
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <Card className="p-4">
              <p className="text-base font-medium">{t('모델별')}</p>
              <ul className="mt-2 space-y-1.5">
                {(data?.byModel ?? []).map((m) => (
                  <li key={m.model} className="flex items-baseline justify-between gap-3 text-base">
                    <span className="min-w-0 truncate font-mono text-sm text-muted">
                      {m.model}
                    </span>
                    <span className="shrink-0 tabular-nums">
                      {m.credits.toLocaleString()}{' '}
                      <span className="text-faint">· {t('{n}회').replace('{n}', String(m.requests))}</span>
                    </span>
                  </li>
                ))}
                {other > 0 && (
                  <li className="flex items-baseline justify-between gap-3 text-base">
                    <span className="text-muted">{t('기타')}</span>
                    <span className="shrink-0 tabular-nums">{other.toLocaleString()}</span>
                  </li>
                )}
              </ul>
              {other > 0 && (
                <p className="mt-2 text-xs text-faint">
                  {t('한 모델을 지목할 수 없는 몫입니다. 여러 모델을 한 번에 비교한 요청처럼요.')}
                </p>
              )}
            </Card>
            <Card className="p-4">
              <p className="text-base font-medium">{t('화면별')}</p>
              <ul className="mt-2 space-y-1.5">
                {(data?.bySurface ?? []).map((s) => (
                  <li key={s.kind} className="flex items-baseline justify-between gap-3 text-base">
                    <span className="text-muted">{surface(s.kind)}</span>
                    <span className="shrink-0 tabular-nums">
                      {s.credits.toLocaleString()}{' '}
                      <span className="text-faint">· {t('{n}회').replace('{n}', String(s.requests))}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          </div>
        </>
      )}
      </PageBody>
    </>
  )
}
