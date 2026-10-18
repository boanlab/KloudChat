import { Card } from '@/components/ui'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { colorsOf, toLines, usePerfOverview } from './data'
import { fmt, num } from './format'
import { ChartCard, Legend, LineChart, ModelLatencyTable, PerfScreen, Section, Table, Thresholds } from './shared'

/** Sessions against latency, latency by model, model server trends and stage durations. */
export function PerfModelsPage() {
  const t = useT()
  const perf = usePerfOverview()
  const { data } = perf
  const th = data?.thresholds
  const overlay = data?.charts.find((c) => c.key === 'sessions_latency')

  return (
    <PerfScreen perf={perf} description={t('세션 수와 모델 지연, 모델 서버 대기열의 추이입니다.')}>
      {/* Sessions vs model latency: two panels on one time axis, one crosshair. */}
      {overlay && (
        <Section
          title={t('세션 수 vs 모델 지연')}
          hint={t('위는 동시 스트림·진행 중 턴, 아래는 지연(초)입니다. 세션이 늘 때 지연이 함께 오르면 GPU 처리량이 한계입니다.')}
        >
          <Card className="p-4" data-testid="perf-overlay">
            {(['left', 'right'] as const).map((axis) => {
              const lines = toLines(
                overlay.series.filter((s) => s.axis === axis),
                colorsOf(overlay.series),
                t,
              )
              return (
                <div key={axis} className={cn(axis === 'right' && 'mt-4')}>
                  <p className="mb-1 text-xs text-faint">{axis === 'left' ? t('세션') : t('지연(초)')}</p>
                  <LineChart
                    lines={lines}
                    unit={axis === 'left' ? '' : 's'}
                    range={perf.range}
                    span={perf.span}
                    threshold={axis === 'right' ? th?.ttftP95MaxSec : undefined}
                    hover={perf.hover}
                    onHover={perf.setHover}
                  />
                  <Legend lines={lines} />
                </div>
              )
            })}
          </Card>
        </Section>
      )}

      {data && (
        <Section
          title={t('모델별 지연')}
          hint={t('LiteLLM 요청 기록에서 분 단위로 집계합니다. p95는 분별 p95의 평균, 최고는 가장 나빴던 분입니다.')}
        >
          <ModelLatencyTable
            rows={data.models}
            errorRateMax={th?.errorRateMax ?? 5}
            empty={
              data.collector.modelLogAt ? t('이 기간에 모델 호출이 없습니다.') : t('LiteLLM 요청 기록을 아직 읽지 못했습니다.')
            }
          />
        </Section>
      )}

      {data && (
        <Section title={t('모델 서버 추이')}>
          <div className="grid gap-3 lg:grid-cols-2">
            <ChartCard perf={perf} chartKey="vllm_queue" unit="" threshold={th?.waitingMax} />
            <ChartCard perf={perf} chartKey="vllm_kv" unit="%" threshold={th?.kvCacheMax} />
            <ChartCard perf={perf} chartKey="gpu_util" unit="%" />
          </div>
        </Section>
      )}

      {data && (
        <Section title={t('진행 과정 단계 소요')}>
          <Table
            empty={t('이 기간에 끝난 단계가 없습니다.')}
            head={[t('단계'), t('횟수'), t('p50'), t('p95'), t('최대')]}
            rows={data.pipeline.map((s) => [s.stage, num(s.runs, 0), fmt(s.p50, 's'), fmt(s.p95, 's'), fmt(s.max, 's')])}
          />
        </Section>
      )}

      {th && <Thresholds th={th} />}
    </PerfScreen>
  )
}

