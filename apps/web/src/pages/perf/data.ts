import type { ReactNode } from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  type PerfChart,
  type PerfLevel,
  type PerfOverview,
  type PerfPoint,
  type PerfRange,
  errorMessage,
  perfApi,
} from '@/lib/api'
import { useT } from '@/lib/useT'
import { PERF_HOME, SERIES } from './format'

const REFRESH_MS = 60_000

export interface Line {
  name: string
  color: string
  points: PerfPoint[]
}

export type GpuRow = PerfOverview['gpus'][number]
export type VllmRow = PerfOverview['vllm'][number]
export type Thresholds = PerfOverview['thresholds']
export type Tone = 'neutral' | 'success' | 'warn' | 'danger'

/** One overview per screen: range, 60 s refresh, time span and the hover shared by its charts. */
export function usePerfOverview() {
  const t = useT()
  const [range, setRange] = useState<PerfRange>('1h')
  const [data, setData] = useState<PerfOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [hover, setHover] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await perfApi.overview(range))
      setError(null)
    } catch (err) {
      setError(errorMessage(err, t('성능 지표를 불러오지 못했습니다.')))
    }
  }, [range, t])

  const refresh = useCallback(async () => {
    setLoading(true)
    await load()
    setLoading(false)
  }, [load])

  useEffect(() => {
    void load()
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, REFRESH_MS)
    return () => window.clearInterval(id)
  }, [load])

  const span = useMemo<[number, number]>(() => {
    const end = data ? new Date(data.generatedAt).getTime() : 0
    const seconds = { '1h': 3600, '24h': 86400, '7d': 7 * 86400, '30d': 30 * 86400 }[range]
    return [end - seconds * 1000, end]
  }, [data, range])

  const last = data?.collector.lastSampleAt
  const stale = !data || !last || new Date(data.generatedAt).getTime() - new Date(last).getTime() > 5 * 60_000

  return { range, setRange, data, error, loading, refresh, span, hover, setHover, stale }
}

export type PerfData = ReturnType<typeof usePerfOverview>

/** Fixed slot order (server sends ≤ six series). */
export function colorsOf(series: PerfChart['series']): (name: string) => string {
  const slots = new Map(series.map((s, i) => [s.name, SERIES[Math.min(i, SERIES.length - 1)]]))
  return (name) => slots.get(name) ?? SERIES[0]
}

export function toLines(
  series: PerfChart['series'],
  colorOf: (name: string) => string,
  t: (text: string) => string,
): Line[] {
  // Only the label part is translated.
  const label = (name: string) => {
    const [head, ...rest] = name.split(' · ')
    return [t(head), ...rest].join(' · ')
  }
  return series.map((s) => ({ name: label(s.name), color: colorOf(s.name), points: s.points }))
}

/** A chart's series, kept to `only` when given; null when nothing is left. */
export function seriesOf(data: PerfOverview | null, key: string, only?: string[]): PerfChart | null {
  const chart = data?.charts.find((c) => c.key === key)
  if (!chart) return null
  if (!only) return chart
  const series = chart.series.filter((s) => only.includes(s.name))
  return series.length ? { ...chart, series } : null
}

export const LEVEL_TONE: Record<PerfLevel, 'success' | 'warn' | 'danger' | 'neutral'> = {
  ok: 'success',
  warn: 'warn',
  critical: 'danger',
  info: 'neutral',
}

export function statusLabel(status: PerfOverview['status'], t: (text: string) => string): string {
  return { ok: t('정상'), warn: t('주의'), critical: t('과부하') }[status]
}

export function levelLabel(level: PerfLevel, t: (text: string) => string): string {
  return level === 'critical' ? t('과부하') : level === 'warn' ? t('주의') : t('참고')
}

export function gpuTone(g: GpuRow, th: Thresholds | undefined): Tone {
  if (!g.up) return 'neutral'
  const hot = g.temp !== null && g.temp >= (th?.gpuTempMax ?? 85)
  const full = g.nodeMemPercent !== null && g.nodeMemPercent >= (th?.gpuNodeMemMax ?? 99)
  return hot || full ? 'warn' : 'success'
}

export function vllmTone(m: VllmRow, th: Thresholds | undefined): Tone {
  if (!m.up) return 'neutral'
  if ((m.waiting ?? 0) > (th?.waitingMax ?? 2)) return 'danger'
  const kvFull = m.kvCache !== null && m.kvCache >= (th?.kvCacheMax ?? 90)
  const slow = m.ttftP95 !== null && m.ttftP95 > (th?.ttftP95MaxSec ?? 10)
  return kvFull || slow ? 'warn' : 'success'
}

export function gpuLabel(g: GpuRow, th: Thresholds | undefined, t: (text: string) => string): string {
  const tone = gpuTone(g, th)
  return tone === 'neutral' ? t('미연결') : tone === 'warn' ? t('주의') : t('응답')
}

export function vllmLabel(m: VllmRow, th: Thresholds | undefined, t: (text: string) => string): string {
  const tone = vllmTone(m, th)
  if (tone === 'neutral') return t('미연결')
  if (tone === 'danger') return t('대기 중')
  return tone === 'warn' ? t('주의') : t('응답')
}

export const gpuHref = (node: string) => `${PERF_HOME}/gpu?node=${encodeURIComponent(node)}`
// Query string: model names carry '/'.
export const vllmHref = (model: string) => `${PERF_HOME}/vllm?target=${encodeURIComponent(model)}`

/** A SortableTable column; `sort` gives the value rows are ordered by. */
export interface Column<R> {
  key: string
  label: string
  sort: (row: R) => number | string | null
  cell: (row: R) => ReactNode
  /** Kept at phone width (the first column always is). */
  primary?: boolean
}
