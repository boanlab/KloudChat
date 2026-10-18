import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { Card } from '@/components/ui'
import { currentLocale } from '@/lib/i18n'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { statusLabel, usePerfOverview } from './data'
import { fmt, num } from './format'
import {
  ChartCard,
  CollectorNote,
  LevelIcon,
  Meter,
  PerfScreen,
  Section,
  Table,
  Thresholds,
  Tile,
  VerdictList,
} from './shared'

/** Host, containers and api replicas: verdicts in full, host figures, trends and tables. */
export function PerfHostPage() {
  const t = useT()
  const perf = usePerfOverview()
  const { data } = perf
  const status = data?.status ?? 'ok'
  const th = data?.thresholds
  const { hash } = useLocation()
  const loaded = data !== null

  // `#verdicts` from the dashboard: scrolled to once the card has its content.
  useEffect(() => {
    if (loaded && hash === '#verdicts') document.getElementById('verdicts')?.scrollIntoView({ block: 'start' })
  }, [loaded, hash])

  return (
    <PerfScreen perf={perf} description={t('호스트와 컨테이너, api 복제본의 판단 근거와 추이입니다.')}>
      <Card
        id="verdicts"
        className={cn(
          'scroll-mt-20 p-4',
          status === 'critical' && 'border-danger/40',
          status === 'warn' && 'border-warn/40',
        )}
      >
        <div className="flex flex-wrap items-center gap-2">
          <LevelIcon level={status} size={20} />
          <span className="text-lg font-semibold">{statusLabel(status, t)}</span>
          <span className="text-sm text-muted">
            {data?.verdicts.length
              ? t('판단 {n}건').replace('{n}', String(data.verdicts.length))
              : t('기준을 넘은 항목이 없습니다')}
          </span>
          {data?.collector.lastSampleAt && (
            <span className="ml-auto text-xs text-faint">
              {t('마지막 표본')} {new Date(data.collector.lastSampleAt).toLocaleTimeString(currentLocale())}
            </span>
          )}
        </div>
        {data && <VerdictList verdicts={data.verdicts} />}
        <CollectorNote perf={perf} />
      </Card>

      {data && (
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <Tile
            label={t('CPU')}
            value={fmt(data.host.cpuPercent, '%')}
            sub={`${t('최고')} ${fmt(data.host.cpuPeak, '%')} · ${num(data.host.cpus ?? 0, 0)} ${t('코어')}`}
            meter={<Meter value={data.host.cpuPercent} warn={70} critical={90} />}
          />
          <Tile
            label={t('메모리')}
            value={fmt(data.host.memPercent, '%')}
            sub={`${fmt(data.host.memUsed, 'bytes')} / ${fmt(data.host.memTotal, 'bytes')}`}
            meter={<Meter value={data.host.memPercent} warn={th?.memMax ? th.memMax - 10 : 80} critical={th?.memMax} />}
          />
          {data.host.disks.map((d) => (
            <Tile
              key={d.label}
              label={`${t('디스크')} · ${d.label}`}
              value={fmt(d.usedPercent, '%')}
              sub={`${t('남음')} ${fmt(d.free, 'bytes')}`}
              meter={<Meter value={d.usedPercent} warn={th?.diskWarn} critical={th?.diskCritical} />}
            />
          ))}
          <Tile label={t('네트워크')} value={`↓ ${fmt(data.host.netRx, 'bps')}`} sub={`↑ ${fmt(data.host.netTx, 'bps')}`} />
          <Tile
            label={t('부하(1분)')}
            value={fmt(data.host.load1, '')}
            sub={t('코어 수보다 크면 대기 발생')}
            tone={
              data.host.load1 !== null && data.host.cpus !== null && data.host.load1 > data.host.cpus ? 'warn' : undefined
            }
          />
        </div>
      )}

      {data && (
        <Section title={t('추이')}>
          <div className="grid gap-3 lg:grid-cols-2">
            <ChartCard perf={perf} chartKey="host" unit="%" />
            <ChartCard perf={perf} chartKey="components_cpu" unit="%" />
            <ChartCard perf={perf} chartKey="components_mem" unit="bytes" />
            <ChartCard perf={perf} chartKey="net" unit="bps" />
            <ChartCard perf={perf} chartKey="disk" unit="%" />
          </div>
        </Section>
      )}

      {data && (
        <Section
          title={t('컴포넌트')}
          hint={t('CPU 코어는 한 코어=1입니다. api 복제본은 코어 하나로 요청을 처리하므로 0.8을 넘는 상태가 이어지면 복제본을 늘립니다.')}
        >
          <Table
            empty={t('컨테이너 지표가 없습니다.')}
            head={[t('컴포넌트'), t('CPU 코어'), t('최고'), t('메모리'), t('최고'), t('한도 대비'), t('네트워크 ↓/↑'), t('디스크 읽기/쓰기')]}
            rows={data.components.map((c) => [
              c.name,
              fmt(c.cpuCores, ''),
              fmt(c.cpuCoresPeak, ''),
              fmt(c.memBytes, 'bytes'),
              fmt(c.memPeak, 'bytes'),
              c.cpuLimitPercent !== null
                ? `CPU ${fmt(c.cpuLimitPercent, '%')} · ${fmt(c.memLimitPercent, '%')}`
                : fmt(c.memLimitPercent, '%'),
              `${fmt(c.netRx, 'bps')} / ${fmt(c.netTx, 'bps')}`,
              `${fmt(c.ioRead, 'bps')} / ${fmt(c.ioWrite, 'bps')}`,
            ])}
          />
        </Section>
      )}

      {data && (
        <Section title={t('api 복제본')}>
          <Table
            empty={t('복제본 표본이 없습니다.')}
            head={[t('복제본'), t('스트림'), t('최고'), t('진행 중 턴'), t('요청/분'), t('5xx'), t('응답 p95')]}
            rows={data.replicas.map((r) => [
              r.name,
              num(r.streams, 0),
              fmt(r.streamsPeak, ''),
              fmt(r.turns, ''),
              fmt(r.requests, ''),
              fmt(r.errors, ''),
              fmt(r.httpP95Ms, 'ms'),
            ])}
          />
        </Section>
      )}

      {th && <Thresholds th={th} />}
    </PerfScreen>
  )
}
