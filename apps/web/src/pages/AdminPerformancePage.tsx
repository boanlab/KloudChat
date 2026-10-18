import { Link } from 'react-router-dom'
import { Badge, Card } from '@/components/ui'
import { currentLocale } from '@/lib/i18n'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import {
  type Column,
  type GpuRow,
  type VllmRow,
  LEVEL_TONE,
  gpuHref,
  gpuLabel,
  gpuTone,
  levelLabel,
  statusLabel,
  usePerfOverview,
  vllmHref,
  vllmLabel,
  vllmTone,
} from './perf/data'
import { PERF_HOME, fmt, num } from './perf/format'
import { CollectorNote, LevelIcon, Meter, PerfScreen, Section, SortableTable, Tile } from './perf/shared'

const TONE_RANK = { danger: 3, warn: 2, success: 1, neutral: 0 }

/** Whole infrastructure on one screen; trends and details on the screens it links to. */
export function AdminPerformancePage() {
  const t = useT()
  const perf = usePerfOverview()
  const { data } = perf
  const status = data?.status ?? 'ok'
  const th = data?.thresholds
  const host = `${PERF_HOME}/host`
  const models = `${PERF_HOME}/models`

  const gpuColumns: Column<GpuRow>[] = [
    { key: 'node', label: t('노드'), sort: (g) => g.node, cell: (g) => g.node },
    {
      key: 'state',
      label: t('상태'),
      primary: true,
      sort: (g) => TONE_RANK[gpuTone(g, th)],
      cell: (g) => <Badge tone={gpuTone(g, th)}>{gpuLabel(g, th, t)}</Badge>,
    },
    { key: 'util', label: t('GPU 사용률'), primary: true, sort: (g) => g.util, cell: (g) => fmt(g.util, '%') },
    { key: 'peak', label: t('최고'), sort: (g) => g.utilPeak, cell: (g) => fmt(g.utilPeak, '%') },
    {
      key: 'mem',
      label: t('메모리'),
      sort: (g) => g.memPercent ?? g.nodeMemPercent,
      cell: (g) => fmt(g.memPercent ?? g.nodeMemPercent, '%'),
    },
    {
      key: 'temp',
      label: t('온도'),
      sort: (g) => g.temp,
      cell: (g) =>
        g.temp === null ? (
          '–'
        ) : (
          <span className={cn(g.temp >= (th?.gpuTempMax ?? 85) && 'text-warn')}>{num(g.temp, 0)}°C</span>
        ),
    },
    { key: 'power', label: t('전력'), sort: (g) => g.power, cell: (g) => (g.power === null ? '–' : `${num(g.power, 0)} W`) },
    { key: 'cpu', label: t('노드 CPU'), sort: (g) => g.nodeCpuPercent, cell: (g) => fmt(g.nodeCpuPercent, '%') },
  ]

  const waitingMax = th?.waitingMax ?? 2
  const vllmColumns: Column<VllmRow>[] = [
    { key: 'model', label: t('모델 서버'), sort: (m) => m.model, cell: (m) => m.model },
    {
      key: 'state',
      label: t('상태'),
      primary: true,
      sort: (m) => TONE_RANK[vllmTone(m, th)],
      cell: (m) => <Badge tone={vllmTone(m, th)}>{vllmLabel(m, th, t)}</Badge>,
    },
    {
      key: 'queue',
      label: t('처리 중 / 대기'),
      primary: true,
      sort: (m) => (m.up ? (m.waiting ?? 0) * 1e6 + (m.running ?? 0) : null),
      cell: (m) => (
        <>
          {fmt(m.running, '')} /{' '}
          <span className={cn((m.waiting ?? 0) > waitingMax && 'font-medium text-danger')}>{fmt(m.waiting, '')}</span>
        </>
      ),
    },
    {
      key: 'kv',
      label: t('KV 캐시'),
      sort: (m) => m.kvCache,
      cell: (m) => (
        <span className={cn(m.kvCache !== null && m.kvCache >= (th?.kvCacheMax ?? 90) && 'text-warn')}>
          {fmt(m.kvCache, '%')}
        </span>
      ),
    },
    {
      key: 'ttft',
      label: t('첫 토큰 p95'),
      sort: (m) => m.ttftP95,
      cell: (m) => (
        <span className={cn(m.ttftP95 !== null && m.ttftP95 > (th?.ttftP95MaxSec ?? 10) && 'text-warn')}>
          {fmt(m.ttftP95, 's')}
        </span>
      ),
    },
    { key: 'e2e', label: t('전체 응답 p95'), sort: (m) => m.e2eP95, cell: (m) => fmt(m.e2eP95, 's') },
    {
      key: 'tps',
      label: t('생성 속도'),
      sort: (m) => m.genTps,
      cell: (m) => (m.genTps === null ? '–' : `${num(m.genTps, 0)} tok/s`),
    },
    { key: 'req', label: t('요청'), sort: (m) => m.requests, cell: (m) => num(m.requests, 0) },
  ]

  const disk = data?.host.disks[0]
  const streams = data?.replicas.reduce((s, r) => s + r.streams, 0) ?? 0
  const p95s = (data?.models ?? []).map((m) => m.latencyP95).filter((v): v is number => v !== null)
  const slowest = p95s.length ? Math.max(...p95s) : null
  const calls = data?.models.reduce((s, m) => s + m.calls, 0) ?? 0
  const errors = data?.models.reduce((s, m) => s + m.errors, 0) ?? 0
  const errorRate = calls ? (100 * errors) / calls : null
  const servers = data?.vllm ?? []
  const down = servers.filter((m) => !m.up).length
  const waiting = servers.reduce((s, m) => s + (m.up ? (m.waiting ?? 0) : 0), 0)
  const load = data?.host.load1 ?? null
  const cpus = data?.host.cpus ?? null

  return (
    <PerfScreen
      perf={perf}
      description={t('호스트, GPU 노드, 모델 서버의 지금 상태를 한 화면에 모읍니다. 추이와 세부 수치는 각 항목에서 봅니다.')}
    >
      {/* Verdict first: one line each, details on the host screen. */}
      <Card
        data-testid="perf-status"
        className={cn('p-4', status === 'critical' && 'border-danger/40', status === 'warn' && 'border-warn/40')}
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
        {data && data.verdicts.length > 0 && (
          <ul className="mt-3 divide-y divide-line rounded-control border border-line">
            {data.verdicts.map((v, i) => (
              <li key={`${v.code}-${i}`} className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 text-sm">
                <LevelIcon level={v.level} />
                <span className="font-medium">{v.title}</span>
                <Badge tone={LEVEL_TONE[v.level]}>{levelLabel(v.level, t)}</Badge>
                <span className="min-w-0 flex-1 truncate text-muted max-sm:basis-full" title={v.action}>
                  {v.action}
                </span>
                <Link to={`${host}#verdicts`} className="ml-auto shrink-0 text-accent hover:underline">
                  {t('자세히')}
                </Link>
              </li>
            ))}
          </ul>
        )}
        <CollectorNote perf={perf} />
      </Card>

      {data && (
        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
          <Tile
            to={host}
            label={t('CPU')}
            value={fmt(data.host.cpuPercent, '%')}
            sub={`${t('최고')} ${fmt(data.host.cpuPeak, '%')} · ${num(data.host.cpus ?? 0, 0)} ${t('코어')}`}
            meter={<Meter value={data.host.cpuPercent} warn={70} critical={90} />}
          />
          <Tile
            to={host}
            label={t('메모리')}
            value={fmt(data.host.memPercent, '%')}
            sub={`${fmt(data.host.memUsed, 'bytes')} / ${fmt(data.host.memTotal, 'bytes')}`}
            meter={<Meter value={data.host.memPercent} warn={th?.memMax ? th.memMax - 10 : 80} critical={th?.memMax} />}
          />
          <Tile
            to={host}
            label={disk ? `${t('디스크')} · ${disk.label}` : t('디스크')}
            value={fmt(disk?.usedPercent, '%')}
            sub={disk ? `${t('남음')} ${fmt(disk.free, 'bytes')}` : undefined}
            meter={disk && <Meter value={disk.usedPercent} warn={th?.diskWarn} critical={th?.diskCritical} />}
          />
          <Tile
            to={host}
            label={t('부하(1분)')}
            value={fmt(load, '')}
            sub={t('코어 수보다 크면 대기 발생')}
            tone={load !== null && cpus !== null && load > cpus ? 'warn' : undefined}
          />
          <Tile
            to={models}
            label={t('동시 스트림')}
            value={num(streams, 0)}
            sub={t('api 복제본 {n}개').replace('{n}', String(data.replicas.length))}
          />
          <Tile
            to={models}
            label={t('모델 p95')}
            value={fmt(slowest, 's')}
            sub={t('가장 느린 모델 기준')}
            tone={slowest !== null && slowest > (th?.ttftP95MaxSec ?? 10) ? 'warn' : undefined}
          />
          <Tile
            to={models}
            label={t('모델 오류율')}
            value={fmt(errorRate, '%')}
            sub={t('호출 {n}건').replace('{n}', num(calls, 0))}
            tone={errorRate !== null && errorRate >= (th?.errorRateMax ?? 5) ? 'danger' : undefined}
          />
          <Tile
            to={models}
            label={t('모델 서버 대기')}
            value={servers.length ? num(waiting, 0) : '–'}
            sub={
              down
                ? t('미연결 {n}개').replace('{n}', String(down))
                : t('노드 {g} · 서버 {s}')
                    .replace('{g}', String(data.gpus.length))
                    .replace('{s}', String(servers.length))
            }
            tone={servers.some((m) => m.up && (m.waiting ?? 0) > waitingMax) ? 'danger' : undefined}
          />
        </div>
      )}

      {data && (
        <Section
          title={t('GPU 노드')}
          hint={t('vLLM 지표와 GPU 노드 지표는 KloudChat-LLM 게이트웨이의 /metrics로 읽습니다.')}
        >
          <SortableTable
            label={t('GPU 노드')}
            rows={data.gpus}
            columns={gpuColumns}
            rowKey={(g) => g.node}
            href={(g) => gpuHref(g.node)}
            defaultSort={{ key: 'util', dir: 'desc' }}
            empty={t('게이트웨이에서 읽은 GPU 노드가 없습니다.')}
          />
        </Section>
      )}

      {data && (
        <Section
          title={t('모델 서버')}
          action={
            <Link to={models} className="text-accent hover:underline">
              {t('모델별 지연 · 추이')}
            </Link>
          }
        >
          <SortableTable
            label={t('모델 서버')}
            rows={servers}
            columns={vllmColumns}
            rowKey={(m) => m.model}
            href={(m) => vllmHref(m.model)}
            defaultSort={{ key: 'queue', dir: 'desc' }}
            empty={t('게이트웨이에서 읽은 모델 서버가 없습니다.')}
          />
        </Section>
      )}
    </PerfScreen>
  )
}
