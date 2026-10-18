import { Server } from 'lucide-react'
import { Link, Navigate, useSearchParams } from 'react-router-dom'
import { Badge, Card } from '@/components/ui'
import { useT } from '@/lib/useT'
import { gpuHref, seriesOf, usePerfOverview, vllmLabel, vllmTone } from './data'
import { PERF_HOME, fmt, nodeOf, num } from './format'
import { ChartCard, Facts, ModelLatencyTable, PerfScreen, Section, WithPeak } from './shared'

/** One model server: queue, cache and latency now, their trends and the model's call latency. */
export function PerfServerPage() {
  const t = useT()
  const [params] = useSearchParams()
  const target = params.get('target')
  const perf = usePerfOverview()
  const { data } = perf
  if (!target) return <Navigate to={PERF_HOME} replace />
  const th = data?.thresholds
  const m = data?.vllm.find((x) => x.model === target)
  // Same node: a server named `model · node` and the GPU node with that name.
  const gpu = target.includes(' · ') ? data?.gpus.find((g) => nodeOf(g.node) === nodeOf(target)) : undefined
  const model = target.split(' · ')[0]
  const calls = (data?.models ?? []).filter((x) => x.model === model)
  const charted = ['vllm_queue', 'vllm_kv'].some((key) => seriesOf(data, key, [target]) !== null)

  return (
    <PerfScreen perf={perf} back description={t('모델 서버 하나의 대기열, KV 캐시, 지연과 그 추이입니다.')}>
      {data && !m && <p className="text-sm text-faint">{t('이 이름의 모델 서버가 없습니다.')}</p>}
      {m && (
        <Card className="p-4">
          <div className="flex flex-wrap items-center gap-2">
            <Server size={16} className="text-muted" />
            <h2 className="text-base font-semibold break-all">{m.model}</h2>
            {gpu && (
              <Link to={gpuHref(gpu.node)} className="text-sm text-accent hover:underline">
                {t('노드 {name}').replace('{name}', nodeOf(gpu.node))}
              </Link>
            )}
            <Badge tone={vllmTone(m, th)} className="ml-auto">
              {vllmLabel(m, th, t)}
            </Badge>
          </div>
          {m.up && (
            <Facts
              items={[
                [
                  t('처리 중 / 대기'),
                  <WithPeak
                    key="q"
                    value={`${fmt(m.running, '')} / ${fmt(m.waiting, '')}`}
                    peak={fmt(m.waitingPeak, '')}
                  />,
                ],
                [t('KV 캐시'), <WithPeak key="k" value={fmt(m.kvCache, '%')} peak={fmt(m.kvPeak, '%')} />],
                [t('첫 토큰 p95'), <WithPeak key="f" value={fmt(m.ttftP95, 's')} peak={fmt(m.ttftP95Peak, 's')} />],
                [t('전체 응답 p95'), fmt(m.e2eP95, 's')],
                [t('생성 속도'), m.genTps === null ? '–' : `${num(m.genTps, 0)} tok/s`],
                [t('선점 · 요청'), `${num(m.preemptions, 0)} · ${num(m.requests, 0)}`],
              ]}
            />
          )}
        </Card>
      )}

      {m && (
        <Section title={t('추이')}>
          {charted ? (
            <div className="grid gap-3 lg:grid-cols-2">
              <ChartCard perf={perf} chartKey="vllm_queue" unit="" threshold={th?.waitingMax} only={[target]} />
              <ChartCard perf={perf} chartKey="vllm_kv" unit="%" threshold={th?.kvCacheMax} only={[target]} />
            </div>
          ) : (
            <p className="text-sm text-faint">
              {t('이 서버의 추이 표본이 아직 없습니다. 대기와 KV 캐시가 큰 서버부터 기록됩니다.')}
            </p>
          )}
        </Section>
      )}

      {m && (
        <Section title={t('모델별 지연')}>
          <ModelLatencyTable
            rows={calls}
            errorRateMax={th?.errorRateMax ?? 5}
            empty={t('이 기간에 이 모델의 호출 기록이 없습니다.')}
          />
        </Section>
      )}
    </PerfScreen>
  )
}
