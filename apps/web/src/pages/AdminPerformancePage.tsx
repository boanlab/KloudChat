import { AlertTriangle, CheckCircle2, Cpu, OctagonAlert, RefreshCw, Server } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Badge, Button, Card, PageHeader } from '@/components/ui'
import {
  type PerfChart,
  type PerfLevel,
  type PerfOverview,
  type PerfPoint,
  type PerfRange,
  errorMessage,
  perfApi,
} from '@/lib/api'
import { currentLocale } from '@/lib/i18n'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

const RANGES: PerfRange[] = ['1h', '24h', '7d', '30d']
const REFRESH_MS = 60_000

/** Fixed categorical order (validated light/dark pairs); a series keeps its slot by name. */
const SERIES = [
  'light-dark(#2a78d6, #3987e5)',
  'light-dark(#eb6834, #d95926)',
  'light-dark(#1baf7a, #199e70)',
  'light-dark(#eda100, #c98500)',
  'light-dark(#e87ba4, #d55181)',
  'light-dark(#4a3aa7, #9085e9)',
]

/* ── formatting ─────────────────────────────────────────────────────── */

type Unit = string

function num(v: number, digits = 1): string {
  return v.toLocaleString(currentLocale(), { maximumFractionDigits: digits })
}

function bytes(v: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let x = v
  while (Math.abs(x) >= 1024 && i < units.length - 1) {
    x /= 1024
    i += 1
  }
  return `${num(x, x >= 100 ? 0 : 1)} ${units[i]}`
}

function fmt(v: number | null | undefined, unit: Unit): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '–'
  if (unit === '%') return `${num(v, v >= 10 ? 0 : 1)}%`
  if (unit === 'bytes') return bytes(v)
  if (unit === 'bps') return `${bytes(v)}/s`
  if (unit === 's') return v >= 10 ? `${num(v, 0)}s` : `${num(v, 2)}s`
  if (unit === 'ms') return `${num(v, 0)}ms`
  return num(v, v >= 10 ? 0 : 1)
}

function clock(ms: number, range: PerfRange): string {
  const d = new Date(ms)
  const opts: Intl.DateTimeFormatOptions =
    range === '1h' || range === '24h'
      ? { hour: '2-digit', minute: '2-digit' }
      : { month: 'numeric', day: 'numeric', hour: '2-digit' }
  return d.toLocaleString(currentLocale(), opts)
}

/* ── line chart ─────────────────────────────────────────────────────── */

interface Line {
  name: string
  color: string
  points: PerfPoint[]
}

function niceMax(v: number): number {
  if (v <= 0) return 1
  const mag = 10 ** Math.floor(Math.log10(v))
  for (const m of [1, 2, 2.5, 5, 10]) if (m * mag >= v) return m * mag
  return 10 * mag
}

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(600)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(Math.max(200, el.clientWidth)))
    ro.observe(el)
    setWidth(Math.max(200, el.clientWidth))
    return () => ro.disconnect()
  }, [])
  return [ref, width]
}

/** Thin lines, recessive grid, crosshair; `hover`/`onHover` shared by charts on one time axis. */
function LineChart({
  lines,
  unit,
  range,
  span,
  height = 140,
  threshold,
  hover,
  onHover,
}: {
  lines: Line[]
  unit: Unit
  range: PerfRange
  span: [number, number]
  height?: number
  threshold?: number
  hover: number | null
  onHover: (ms: number | null) => void
}) {
  const t = useT()
  const [ref, width] = useWidth<HTMLDivElement>()
  const [own, setOwn] = useState(false)
  const pad = { l: 52, r: 8, t: 8, b: 20 }
  const w = width - pad.l - pad.r
  const h = height - pad.t - pad.b
  const [x0, x1] = span
  const top = niceMax(
    Math.max(threshold ?? 0, ...lines.flatMap((l) => l.points.map((p) => p[1])), unit === '%' ? 10 : 0),
  )
  const yMax = unit === '%' ? Math.min(Math.max(top, 10), 100) : top
  const x = (ms: number) => pad.l + ((ms - x0) / Math.max(x1 - x0, 1)) * w
  const y = (v: number) => pad.t + h - (Math.min(v, yMax) / yMax) * h
  const empty = lines.every((l) => l.points.length === 0)

  const nearest = (ms: number) => {
    let best: number | null = null
    for (const l of lines)
      for (const p of l.points) if (best === null || Math.abs(p[0] - ms) < Math.abs(best - ms)) best = p[0]
    return best
  }
  const hovered = hover !== null ? nearest(hover) : null
  const rows = hovered === null ? [] : lines.map((l) => ({ l, p: l.points.find((p) => p[0] === hovered) }))

  return (
    <div ref={ref} className="relative">
      {empty ? (
        <div style={{ height }} className="grid place-items-center text-sm text-faint">
          {t('표본이 아직 없습니다')}
        </div>
      ) : (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={lines.map((l) => l.name).join(', ')}
          onMouseMove={(e) => {
            const r = e.currentTarget.getBoundingClientRect()
            const px = e.clientX - r.left
            setOwn(true)
            if (px < pad.l) return onHover(null)
            onHover(x0 + ((px - pad.l) / w) * (x1 - x0))
          }}
          onMouseLeave={() => {
            setOwn(false)
            onHover(null)
          }}
          className="block touch-none"
        >
          {[0, 0.5, 1].map((f) => (
            <g key={f}>
              <line
                x1={pad.l}
                x2={pad.l + w}
                y1={y(yMax * f)}
                y2={y(yMax * f)}
                stroke="var(--border)"
                strokeWidth={1}
              />
              <text x={pad.l - 6} y={y(yMax * f) + 4} textAnchor="end" fontSize={11} fill="var(--faint)">
                {fmt(yMax * f, unit)}
              </text>
            </g>
          ))}
          {[x0, (x0 + x1) / 2, x1].map((ms, i) => (
            <text
              key={ms}
              x={x(ms)}
              y={height - 4}
              fontSize={11}
              fill="var(--faint)"
              textAnchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}
            >
              {clock(ms, range)}
            </text>
          ))}
          {threshold !== undefined && threshold <= yMax && (
            <line
              x1={pad.l}
              x2={pad.l + w}
              y1={y(threshold)}
              y2={y(threshold)}
              stroke="var(--danger)"
              strokeDasharray="4 4"
              strokeWidth={1}
            />
          )}
          {lines.map((l) => {
            // Gap > three buckets drawn as a gap.
            const step = l.points.length > 1 ? Math.min(...l.points.slice(1).map((p, i) => p[0] - l.points[i][0])) : 0
            let d = ''
            l.points.forEach((p, i) => {
              const gap = i > 0 && step > 0 && p[0] - l.points[i - 1][0] > 3 * step
              d += `${i === 0 || gap ? 'M' : 'L'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`
            })
            return (
              <path
                key={l.name}
                d={d}
                fill="none"
                style={{ stroke: l.color }}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            )
          })}
          {hovered !== null && (
            <g>
              <line x1={x(hovered)} x2={x(hovered)} y1={pad.t} y2={pad.t + h} stroke="var(--border-strong)" />
              {rows.map(({ l, p }) =>
                p ? (
                  <circle
                    key={l.name}
                    cx={x(p[0])}
                    cy={y(p[1])}
                    r={4}
                    style={{ fill: l.color }}
                    stroke="var(--panel)"
                    strokeWidth={2}
                  />
                ) : null,
              )}
            </g>
          )}
        </svg>
      )}
      {own && hovered !== null && rows.some((r) => r.p) && (
        <div
          className="pointer-events-none absolute top-1 z-10 max-w-64 rounded-control border border-line bg-panel px-2.5 py-1.5 text-xs shadow-sm"
          style={x(hovered) > width / 2 ? { right: width - x(hovered) + 8 } : { left: x(hovered) + 8 }}
        >
          <p className="mb-0.5 text-faint">{new Date(hovered).toLocaleString(currentLocale())}</p>
          {rows.map(({ l, p }) =>
            p ? (
              <p key={l.name} className="flex items-center gap-1.5">
                <span className="inline-block size-2 rounded-full" style={{ background: l.color }} />
                <span className="truncate text-muted">{l.name}</span>
                <span className="ml-auto pl-2 font-medium tabular-nums">{fmt(p[1], unit)}</span>
                {p[2] !== p[1] && <span className="text-faint tabular-nums">↑{fmt(p[2], unit)}</span>}
              </p>
            ) : null,
          )}
        </div>
      )}
    </div>
  )
}

function Legend({ lines }: { lines: Line[] }) {
  if (lines.length < 2) return null
  return (
    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
      {lines.map((l) => (
        <span key={l.name} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-3 rounded-full" style={{ background: l.color }} />
          {l.name}
        </span>
      ))}
    </div>
  )
}

/** Fixed slot order (server sends ≤ six series). */
function colorsOf(series: PerfChart['series']): (name: string) => string {
  const slots = new Map(series.map((s, i) => [s.name, SERIES[Math.min(i, SERIES.length - 1)]]))
  return (name) => slots.get(name) ?? SERIES[0]
}

function toLines(
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

/* ── pieces ─────────────────────────────────────────────────────────── */

const LEVEL_TONE: Record<PerfLevel, 'success' | 'warn' | 'danger' | 'neutral'> = {
  ok: 'success',
  warn: 'warn',
  critical: 'danger',
  info: 'neutral',
}

function LevelIcon({ level, size = 16 }: { level: PerfLevel; size?: number }) {
  if (level === 'critical') return <OctagonAlert size={size} className="text-danger" />
  if (level === 'warn') return <AlertTriangle size={size} className="text-warn" />
  return <CheckCircle2 size={size} className="text-success" />
}

function Meter({ value, warn, critical }: { value: number | null; warn?: number; critical?: number }) {
  const v = Math.max(0, Math.min(value ?? 0, 100))
  const color =
    critical !== undefined && v >= critical
      ? 'var(--danger)'
      : warn !== undefined && v >= warn
        ? 'var(--warn)'
        : 'var(--accent)'
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-elevated">
      <div className="h-full rounded-full" style={{ width: `${v}%`, background: color }} />
    </div>
  )
}

function Tile({
  label,
  value,
  sub,
  meter,
}: {
  label: string
  value: string
  sub?: string
  meter?: React.ReactNode
}) {
  return (
    <Card className="p-3.5">
      <p className="text-xs text-faint">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums">{value}</p>
      {meter && <div className="mt-2">{meter}</div>}
      {sub && <p className="mt-1 text-xs text-faint">{sub}</p>}
    </Card>
  )
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="mt-8">
      <h2 className="text-base font-semibold">{title}</h2>
      {hint && <p className="mt-0.5 text-sm text-muted">{hint}</p>}
      <div className="mt-3">{children}</div>
    </section>
  )
}

function Table({ head, rows, empty }: { head: string[]; rows: React.ReactNode[][]; empty: string }) {
  if (rows.length === 0) return <p className="text-sm text-faint">{empty}</p>
  return (
    <div className="overflow-x-auto rounded-card border border-line">
      <table className="w-full min-w-[36rem] text-sm">
        <thead className="bg-elevated text-left text-xs text-muted">
          <tr>
            {head.map((h, i) => (
              <th key={h} className={cn('px-3 py-2 font-medium whitespace-nowrap', i > 0 && 'text-right')}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-line">
              {r.map((c, j) => (
                <td key={j} className={cn('px-3 py-2', j > 0 ? 'text-right tabular-nums whitespace-nowrap' : 'break-all')}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ── page ───────────────────────────────────────────────────────────── */

export function AdminPerformancePage() {
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

  const refresh = async () => {
    setLoading(true)
    await load()
    setLoading(false)
  }

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

  const status = data?.status ?? 'ok'
  const statusLabel = { ok: t('정상'), warn: t('주의'), critical: t('과부하') }[status]
  const charts = new Map((data?.charts ?? []).map((c) => [c.key, c]))
  const overlay = charts.get('sessions_latency')
  const th = data?.thresholds

  const chartCard = (key: string, unit: Unit, threshold?: number) => {
    const chart = charts.get(key)
    if (!chart) return null
    const lines = toLines(chart.series, colorsOf(chart.series), t)
    return (
      <Card key={key} className="min-w-0 p-4">
        <p className="mb-2 text-sm font-medium">{t(chart.title)}</p>
        <LineChart
          lines={lines}
          unit={unit}
          range={range}
          span={span}
          threshold={threshold}
          hover={hover}
          onHover={setHover}
        />
        <Legend lines={lines} />
      </Card>
    )
  }

  const collector = data?.collector
  const stale =
    !data ||
    !collector?.lastSampleAt ||
    new Date(data.generatedAt).getTime() - new Date(collector.lastSampleAt).getTime() > 5 * 60_000

  return (
    <>
      <TopBar left={t('성능 모니터링')} />
      <PageBody className="max-w-6xl">
        <PageHeader
          title={t('성능 모니터링')}
          description={t(
            'CPU·메모리·디스크·네트워크, 모델 지연과 GPU 대기열을 1분마다 모아 과부하와 증설 필요를 판단합니다.',
          )}
          action={
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex rounded-control border border-line p-0.5" role="group" aria-label={t('기간')}>
                {RANGES.map((r) => (
                  <button
                    key={r}
                    onClick={() => setRange(r)}
                    aria-pressed={range === r}
                    className={
                      range === r
                        ? 'min-w-11 rounded-control bg-elevated px-3 py-2 text-sm font-medium max-sm:px-2'
                        : 'min-w-11 rounded-control px-3 py-2 text-sm text-muted hover:text-fg max-sm:px-2'
                    }
                  >
                    {{ '1h': t('1시간'), '24h': t('24시간'), '7d': t('7일'), '30d': t('30일') }[r]}
                  </button>
                ))}
              </div>
              <Button variant="secondary" onClick={() => void refresh()} disabled={loading} aria-label={t('새로 고침')}>
                <RefreshCw size={14} className={cn(loading && 'animate-spin')} />
              </Button>
            </div>
          }
        />

        {error && <p className="mb-4 rounded-control border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">{error}</p>}

        {/* Summary: the verdict first, each with its reason and what to do. */}
        <Card
          data-testid="perf-status"
          className={cn(
            'p-4',
            status === 'critical' && 'border-danger/40',
            status === 'warn' && 'border-warn/40',
          )}
        >
          <div className="flex flex-wrap items-center gap-2">
            <LevelIcon level={status} size={20} />
            <span className="text-lg font-semibold">{statusLabel}</span>
            <span className="text-sm text-muted">
              {data?.verdicts.length
                ? t('판단 {n}건').replace('{n}', String(data.verdicts.length))
                : t('기준을 넘은 항목이 없습니다')}
            </span>
            {collector?.lastSampleAt && (
              <span className="ml-auto text-xs text-faint">
                {t('마지막 표본')} {new Date(collector.lastSampleAt).toLocaleTimeString(currentLocale())}
              </span>
            )}
          </div>
          {data && data.verdicts.length > 0 && (
            <ul className="mt-3 space-y-3">
              {data.verdicts.map((v, i) => (
                <li key={`${v.code}-${i}`} className="rounded-control border border-line p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <LevelIcon level={v.level} />
                    <span className="font-medium">{v.title}</span>
                    <Badge tone={LEVEL_TONE[v.level]}>
                      {v.level === 'critical' ? t('과부하') : v.level === 'warn' ? t('주의') : t('참고')}
                    </Badge>
                  </div>
                  <p className="mt-1 text-sm text-muted">{v.detail}</p>
                  <p className="mt-1 text-sm">
                    <span className="font-medium">{t('권장 조치')}</span> · {v.action}
                  </p>
                  {v.evidence.filter((e) => e.points.length > 1).slice(0, 1).map((e) => (
                    <div key={`${e.name}-${e.subject}`} className="mt-2">
                      <p className="text-xs text-faint">
                        {t('근거')}: {e.source}/{e.name} · {e.subject} · {t('최근 {n}분').replace('{n}', String(e.windowMin))}
                        {e.threshold !== null && ` · ${t('기준')} ${num(e.threshold, 2)}`}
                      </p>
                      <LineChart
                        lines={[
                          {
                            name: `${e.name} · ${e.subject}`,
                            color: SERIES[0],
                            points: e.points.map(([ms, val]) => [ms, val, val] as PerfPoint),
                          },
                        ]}
                        unit=""
                        range="1h"
                        span={[e.points[0][0], e.points[e.points.length - 1][0]]}
                        height={80}
                        threshold={e.threshold ?? undefined}
                        hover={null}
                        onHover={() => {}}
                      />
                    </div>
                  ))}
                </li>
              ))}
            </ul>
          )}
          {data && (!collector?.hostAvailable || stale) && (
            <p className="mt-3 text-xs text-faint">
              {!collector?.hostAvailable &&
                t('호스트·컨테이너 지표가 없습니다: api 컨테이너에 /proc와 cgroup이 읽기 전용으로 연결되어야 합니다.')}{' '}
              {stale && t('수집기가 최근 5분 동안 표본을 쓰지 않았습니다.')}
            </p>
          )}
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
            <Tile
              label={t('네트워크')}
              value={`↓ ${fmt(data.host.netRx, 'bps')}`}
              sub={`↑ ${fmt(data.host.netTx, 'bps')}`}
            />
            <Tile label={t('부하(1분)')} value={fmt(data.host.load1, '')} sub={t('코어 수보다 크면 대기 발생')} />
          </div>
        )}

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
                      range={range}
                      span={span}
                      threshold={axis === 'right' ? th?.ttftP95MaxSec : undefined}
                      hover={hover}
                      onHover={setHover}
                    />
                    <Legend lines={lines} />
                  </div>
                )
              })}
            </Card>
          </Section>
        )}

        {data && (
          <Section title={t('GPU 노드 · 모델 서버')} hint={t('vLLM 지표와 GPU 노드 지표는 KloudChat-LLM 게이트웨이의 /metrics로 읽습니다.')}>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {data.gpus.map((g) => (
                <Card key={g.node} className="p-4">
                  <div className="flex items-center gap-2">
                    <Cpu size={16} className="text-muted" />
                    <span className="font-medium break-all">{g.node}</span>
                    <Badge tone={g.up ? 'success' : 'neutral'} className="ml-auto">
                      {g.up ? t('응답') : t('미연결')}
                    </Badge>
                  </div>
                  {g.up ? (
                    <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-sm">
                      <dt className="text-muted">{t('GPU 사용률')}</dt>
                      <dd className="text-right tabular-nums">{fmt(g.util, '%')}</dd>
                      <dt className="text-muted">{t('메모리')}</dt>
                      <dd className="text-right tabular-nums">{fmt(g.memPercent ?? g.nodeMemPercent, '%')}</dd>
                      <dt className="text-muted">{t('온도')}</dt>
                      <dd className="text-right tabular-nums">{g.temp === null ? '–' : `${num(g.temp, 0)}°C`}</dd>
                      <dt className="text-muted">{t('전력')}</dt>
                      <dd className="text-right tabular-nums">{g.power === null ? '–' : `${num(g.power, 0)} W`}</dd>
                      <dt className="text-muted">{t('노드 CPU')}</dt>
                      <dd className="text-right tabular-nums">{fmt(g.nodeCpuPercent, '%')}</dd>
                    </dl>
                  ) : (
                    <p className="mt-2 text-xs text-faint">{t('gpu-exporter가 없거나 게이트웨이에 주소가 없습니다.')}</p>
                  )}
                </Card>
              ))}
              {data.vllm.map((m) => (
                <Card key={m.model} className="p-4">
                  <div className="flex items-center gap-2">
                    <Server size={16} className="text-muted" />
                    <span className="font-medium break-all">{m.model}</span>
                    <Badge
                      tone={!m.up ? 'neutral' : (m.waiting ?? 0) > (th?.waitingMax ?? 2) ? 'danger' : 'success'}
                      className="ml-auto"
                    >
                      {!m.up ? t('미연결') : (m.waiting ?? 0) > (th?.waitingMax ?? 2) ? t('대기 중') : t('응답')}
                    </Badge>
                  </div>
                  {m.up && (
                    <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-sm">
                      <dt className="text-muted">{t('처리 중 / 대기')}</dt>
                      <dd className="text-right tabular-nums">
                        {fmt(m.running, '')} / {fmt(m.waiting, '')}
                        <span className="ml-1 text-xs text-faint">↑{fmt(m.waitingPeak, '')}</span>
                      </dd>
                      <dt className="text-muted">{t('KV 캐시')}</dt>
                      <dd className="text-right tabular-nums">
                        {fmt(m.kvCache, '%')}
                        <span className="ml-1 text-xs text-faint">↑{fmt(m.kvPeak, '%')}</span>
                      </dd>
                      <dt className="text-muted">{t('첫 토큰 p95')}</dt>
                      <dd className="text-right tabular-nums">{fmt(m.ttftP95, 's')}</dd>
                      <dt className="text-muted">{t('전체 응답 p95')}</dt>
                      <dd className="text-right tabular-nums">{fmt(m.e2eP95, 's')}</dd>
                      <dt className="text-muted">{t('생성 속도')}</dt>
                      <dd className="text-right tabular-nums">{m.genTps === null ? '–' : `${num(m.genTps, 0)} tok/s`}</dd>
                      <dt className="text-muted">{t('선점 · 요청')}</dt>
                      <dd className="text-right tabular-nums">
                        {num(m.preemptions, 0)} · {num(m.requests, 0)}
                      </dd>
                    </dl>
                  )}
                </Card>
              ))}
            </div>
            {data.gpus.length === 0 && data.vllm.length === 0 && (
              <p className="text-sm text-faint">{t('게이트웨이에서 읽은 GPU·모델 지표가 없습니다.')}</p>
            )}
          </Section>
        )}

        {data && (
          <Section title={t('추이')}>
            <div className="grid gap-3 lg:grid-cols-2">
              {chartCard('host', '%')}
              {chartCard('components_cpu', '%')}
              {chartCard('components_mem', 'bytes')}
              {chartCard('vllm_queue', '', th?.waitingMax)}
              {chartCard('vllm_kv', '%', th?.kvCacheMax)}
              {chartCard('gpu_util', '%')}
              {chartCard('net', 'bps')}
              {chartCard('disk', '%')}
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
          <Section title={t('모델별 지연')} hint={t('LiteLLM 요청 기록에서 분 단위로 집계합니다. p95는 분별 p95의 평균, 최고는 가장 나빴던 분입니다.')}>
            <Table
              empty={
                collector?.modelLogAt
                  ? t('이 기간에 모델 호출이 없습니다.')
                  : t('LiteLLM 요청 기록을 아직 읽지 못했습니다.')
              }
              head={[t('모델'), t('호출'), t('오류율'), t('p50'), t('p95'), t('p95 최고'), t('첫 토큰 p50'), t('출력 토큰')]}
              rows={data.models.map((m) => [
                m.model,
                num(m.calls, 0),
                <span key="e" className={cn(m.errorRate >= (th?.errorRateMax ?? 5) && 'text-danger')}>
                  {fmt(m.errorRate, '%')}
                </span>,
                fmt(m.latencyP50, 's'),
                fmt(m.latencyP95, 's'),
                fmt(m.latencyP95Peak, 's'),
                fmt(m.ttftP50, 's'),
                num(m.outTokens, 0),
              ])}
            />
          </Section>
        )}

        {data && (
          <>
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
            <Section title={t('진행 과정 단계 소요')}>
              <Table
                empty={t('이 기간에 끝난 단계가 없습니다.')}
                head={[t('단계'), t('횟수'), t('p50'), t('p95'), t('최대')]}
                rows={data.pipeline.map((s) => [s.stage, num(s.runs, 0), fmt(s.p50, 's'), fmt(s.p95, 's'), fmt(s.max, 's')])}
              />
            </Section>
          </>
        )}

        {th && (
          <Section title={t('판단 기준')}>
            <Card className="p-4 text-sm">
              <ul className="space-y-1.5 text-muted">
                <li>
                  <b className="text-fg">{t('GPU 포화')}</b> ·{' '}
                  {t('모델 서버 대기 요청이 {n}건을 넘는 상태가 {m}분 이어짐 → 같은 모델 GPU 복제본 추가')
                    .replace('{n}', num(th.waitingMax, 0))
                    .replace('{m}', String(th.sustainMin))}
                </li>
                <li>
                  <b className="text-fg">{t('KV 캐시')}</b> ·{' '}
                  {t('{n}% 이상이 {m}분 → 주의, 선점이 생기면 과부하')
                    .replace('{n}', num(th.kvCacheMax, 0))
                    .replace('{m}', String(th.sustainMin))}
                </li>
                <li>
                  <b className="text-fg">{t('첫 토큰 지연')}</b> ·{' '}
                  {t('p95가 {n}초를 넘는 상태가 {m}분 → 세션 증가와 함께 오르는지 표시')
                    .replace('{n}', num(th.ttftP95MaxSec, 0))
                    .replace('{m}', String(th.sustainMin))}
                </li>
                <li>
                  <b className="text-fg">{t('api 복제본')}</b> ·{' '}
                  {t('한 복제본이 CPU 한 코어의 {n}% 이상을 {m}분 → 복제본 추가 검토')
                    .replace('{n}', num(th.apiCpuMax, 0))
                    .replace('{m}', String(th.apiCpuSustainMin))}
                </li>
                <li>
                  <b className="text-fg">{t('메모리')}</b> ·{' '}
                  {t('호스트 {n}% 이상 {m}분 → 과부하, 컨테이너 한도 {n}% 이상 → 주의')
                    .replaceAll('{n}', num(th.memMax, 0))
                    .replace('{m}', String(th.sustainMin))}
                </li>
                <li>
                  <b className="text-fg">{t('GPU 노드')}</b> ·{' '}
                  {t('온도 {t}°C 이상, 노드 메모리 {n}% 이상 {m}분 → 주의 (vLLM이 미리 잡는 몫 때문에 평소 97% 안팎)')
                    .replace('{t}', num(th.gpuTempMax, 0))
                    .replace('{n}', num(th.gpuNodeMemMax, 0))
                    .replace('{m}', String(th.sustainMin))}
                </li>
                <li>
                  <b className="text-fg">{t('디스크')}</b> ·{' '}
                  {t('{w}% 이상 주의, {c}% 이상 과부하')
                    .replace('{w}', num(th.diskWarn, 0))
                    .replace('{c}', num(th.diskCritical, 0))}
                </li>
                <li>
                  <b className="text-fg">{t('모델 오류율')}</b> ·{' '}
                  {t('최근 15분 20건 이상 중 {n}% 이상 실패 → 주의 (3배면 과부하)').replace(
                    '{n}',
                    num(th.errorRateMax, 0),
                  )}
                </li>
              </ul>
              <p className="mt-3 text-xs text-faint">
                {t('기준값은 api 환경 변수 PERF_* 로 바꿉니다. 짧은 순간의 급증은 판단에 넣지 않습니다(구간 표본의 80% 이상이 넘어야 함).')}
              </p>
            </Card>
          </Section>
        )}
      </PageBody>
    </>
  )
}
