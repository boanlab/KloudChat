import { Cpu } from 'lucide-react'
import { Link, Navigate, useSearchParams } from 'react-router-dom'
import { Badge, Card } from '@/components/ui'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { gpuLabel, gpuTone, seriesOf, usePerfOverview, vllmHref, vllmLabel, vllmTone } from './data'
import { PERF_HOME, fmt, nodeOf, num } from './format'
import { ChartCard, Facts, PerfScreen, Section, Table, WithPeak } from './shared'

/** One GPU node: its figures, its utilisation trend and the model servers on it. */
export function PerfGpuPage() {
  const t = useT()
  const [params] = useSearchParams()
  const node = params.get('node')
  const perf = usePerfOverview()
  const { data } = perf
  if (!node) return <Navigate to={PERF_HOME} replace />
  const th = data?.thresholds
  const g = data?.gpus.find((x) => x.node === node)
  const servers = (data?.vllm ?? []).filter((m) => nodeOf(m.model) === nodeOf(node))
  const charted = seriesOf(data, 'gpu_util', [node]) !== null

  return (
    <PerfScreen perf={perf} back description={t('GPU 노드 하나의 지금 상태와 추이, 이 노드의 모델 서버입니다.')}>
      {data && !g && <p className="text-sm text-faint">{t('이 이름의 GPU 노드가 없습니다.')}</p>}
      {g && (
        <Card className="p-4">
          <div className="flex items-center gap-2">
            <Cpu size={16} className="text-muted" />
            <h2 className="text-base font-semibold break-all">{g.node}</h2>
            <Badge tone={gpuTone(g, th)} className="ml-auto">
              {gpuLabel(g, th, t)}
            </Badge>
          </div>
          {g.up ? (
            <Facts
              items={[
                [t('GPU 사용률'), <WithPeak key="u" value={fmt(g.util, '%')} peak={fmt(g.utilPeak, '%')} />],
                [t('GPU 메모리'), fmt(g.memPercent, '%')],
                [
                  t('온도'),
                  g.temp === null ? (
                    '–'
                  ) : (
                    <span key="t" className={cn(g.temp >= (th?.gpuTempMax ?? 85) && 'text-warn')}>{num(g.temp, 0)}°C</span>
                  ),
                ],
                [t('전력'), g.power === null ? '–' : `${num(g.power, 0)} W`],
                [t('노드 CPU'), fmt(g.nodeCpuPercent, '%')],
                [
                  t('노드 메모리'),
                  <span
                    key="m"
                    className={cn(
                      g.nodeMemPercent !== null && g.nodeMemPercent >= (th?.gpuNodeMemMax ?? 99) && 'text-warn',
                    )}
                  >
                    {fmt(g.nodeMemPercent, '%')}
                  </span>,
                ],
              ]}
            />
          ) : (
            <p className="mt-2 text-xs text-faint">{t('gpu-exporter가 없거나 게이트웨이에 주소가 없습니다.')}</p>
          )}
        </Card>
      )}

      {g && (
        <Section title={t('추이')}>
          {charted ? (
            <ChartCard perf={perf} chartKey="gpu_util" unit="%" only={[node]} />
          ) : (
            <p className="text-sm text-faint">
              {t('이 노드의 추이 표본이 아직 없습니다. 사용률이 높은 노드부터 기록됩니다.')}
            </p>
          )}
        </Section>
      )}

      {g && (
        <Section title={t('이 노드의 모델 서버')}>
          <Table
            empty={t('이 노드에서 찾은 모델 서버가 없습니다.')}
            head={[t('모델 서버'), t('상태'), t('처리 중 / 대기'), t('KV 캐시'), t('첫 토큰 p95'), t('생성 속도')]}
            rows={servers.map((m) => [
              <Link key="n" to={vllmHref(m.model)} className="font-medium text-accent hover:underline">
                {m.model}
              </Link>,
              <Badge key="s" tone={vllmTone(m, th)}>
                {vllmLabel(m, th, t)}
              </Badge>,
              `${fmt(m.running, '')} / ${fmt(m.waiting, '')}`,
              fmt(m.kvCache, '%'),
              fmt(m.ttftP95, 's'),
              m.genTps === null ? '–' : `${num(m.genTps, 0)} tok/s`,
            ])}
          />
        </Section>
      )}
    </PerfScreen>
  )
}
