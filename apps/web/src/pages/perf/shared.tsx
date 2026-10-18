import { AlertTriangle, ArrowDown, ArrowLeft, ArrowUp, CheckCircle2, OctagonAlert, RefreshCw } from 'lucide-react'
import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Badge, Button, Card, PageHeader } from '@/components/ui'
import type { PerfLevel, PerfOverview, PerfPoint, PerfRange } from '@/lib/api'
import { currentLocale } from '@/lib/i18n'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { type Column, type Line, type PerfData, LEVEL_TONE, colorsOf, levelLabel, seriesOf, toLines } from './data'
import { PERF_HOME, RANGES, SERIES, type Unit, clock, fmt, num } from './format'

/* ── line chart ─────────────────────────────────────────────────────── */

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
export function LineChart({
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

export function Legend({ lines }: { lines: Line[] }) {
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

/** One overview chart; `only` keeps those series (subject names). */
export function ChartCard({
  perf,
  chartKey,
  unit,
  threshold,
  only,
}: {
  perf: PerfData
  chartKey: string
  unit: Unit
  threshold?: number
  only?: string[]
}) {
  const t = useT()
  const full = perf.data?.charts.find((c) => c.key === chartKey)
  const chart = seriesOf(perf.data, chartKey, only)
  if (!full || !chart) return null
  // Colours from the whole chart, so a series keeps its slot on every screen.
  const lines = toLines(chart.series, colorsOf(full.series), t)
  return (
    <Card className="min-w-0 p-4">
      <p className="mb-2 text-sm font-medium">{t(chart.title)}</p>
      <LineChart
        lines={lines}
        unit={unit}
        range={perf.range}
        span={perf.span}
        threshold={threshold}
        hover={perf.hover}
        onHover={perf.setHover}
      />
      <Legend lines={lines} />
    </Card>
  )
}

/* ── pieces ─────────────────────────────────────────────────────────── */

export function LevelIcon({ level, size = 16 }: { level: PerfLevel; size?: number }) {
  if (level === 'critical') return <OctagonAlert size={size} className="text-danger" />
  if (level === 'warn') return <AlertTriangle size={size} className="text-warn" />
  return <CheckCircle2 size={size} className="text-success" />
}

export function Meter({ value, warn, critical }: { value: number | null; warn?: number; critical?: number }) {
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

/** A figure; `to` makes the whole tile a link to its detail screen. */
export function Tile({
  label,
  value,
  sub,
  meter,
  to,
  tone,
}: {
  label: string
  value: string
  sub?: string
  meter?: React.ReactNode
  to?: string
  tone?: 'warn' | 'danger'
}) {
  const body = (
    <>
      <p className="text-xs text-faint">{label}</p>
      <p
        className={cn(
          'mt-1 text-xl font-semibold tabular-nums',
          tone === 'warn' && 'text-warn',
          tone === 'danger' && 'text-danger',
        )}
      >
        {value}
      </p>
      {meter && <div className="mt-2">{meter}</div>}
      {sub && <p className="mt-1 truncate text-xs text-faint">{sub}</p>}
    </>
  )
  const border = cn(tone === 'warn' && 'border-warn/40', tone === 'danger' && 'border-danger/40')
  if (!to) return <Card className={cn('min-w-0 p-3.5', border)}>{body}</Card>
  return (
    <Link
      to={to}
      className={cn(
        'block min-w-0 rounded-card border border-line bg-panel p-3.5 transition-colors hover:border-line-strong hover:bg-elevated/50',
        border,
      )}
    >
      {body}
    </Link>
  )
}

export function Section({
  title,
  hint,
  action,
  children,
}: {
  title: string
  hint?: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="mt-8">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-base font-semibold">{title}</h2>
        {action && <div className="ml-auto text-sm">{action}</div>}
      </div>
      {hint && <p className="mt-0.5 text-sm text-muted">{hint}</p>}
      <div className="mt-3">{children}</div>
    </section>
  )
}

export function Table({ head, rows, empty }: { head: string[]; rows: React.ReactNode[][]; empty: string }) {
  if (rows.length === 0) return <p className="text-sm text-faint">{empty}</p>
  return (
    <div className="overflow-x-auto rounded-card border border-line">
      <table className="w-full min-w-[36rem] text-sm">
        <thead className="bg-elevated text-left text-xs text-muted">
          <tr>
            {head.map((h, i) => (
              <th key={`${h}-${i}`} className={cn('px-3 py-2 font-medium whitespace-nowrap', i > 0 && 'text-right')}>
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

/** Header buttons sort; the first cell's link covers the row, so the whole row opens its screen. */
export function SortableTable<R>({
  rows,
  columns,
  rowKey,
  href,
  defaultSort,
  empty,
  label,
}: {
  rows: R[]
  columns: Column<R>[]
  rowKey: (row: R) => string
  href: (row: R) => string
  defaultSort: { key: string; dir: 'asc' | 'desc' }
  empty: string
  label: string
}) {
  const t = useT()
  const [sort, setSort] = useState(defaultSort)
  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sort.key) ?? columns[0]
    const sign = sort.dir === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const x = col.sort(a)
      const y = col.sort(b)
      // Missing values last either way.
      if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1
      if (typeof x === 'number' && typeof y === 'number') return (x - y) * sign
      return String(x).localeCompare(String(y), currentLocale()) * sign
    })
  }, [rows, columns, sort])

  if (rows.length === 0) return <p className="text-sm text-faint">{empty}</p>
  return (
    <div className="overflow-x-auto rounded-card border border-line">
      <table className="w-full text-sm" aria-label={label}>
        <thead className="bg-elevated text-left text-xs text-muted">
          <tr>
            {columns.map((c, i) => {
              const active = sort.key === c.key
              return (
                <th
                  key={c.key}
                  scope="col"
                  aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                  className={cn('font-medium whitespace-nowrap', i > 0 && 'text-right', i > 0 && !c.primary && 'max-sm:hidden')}
                >
                  <button
                    type="button"
                    onClick={() =>
                      setSort(
                        active
                          ? { key: c.key, dir: sort.dir === 'asc' ? 'desc' : 'asc' }
                          : { key: c.key, dir: i === 0 ? 'asc' : 'desc' },
                      )
                    }
                    title={t('정렬')}
                    className={cn(
                      'inline-flex min-h-9 items-center gap-1 px-3 py-2 hover:text-fg',
                      i > 0 && 'flex-row-reverse',
                      active && 'text-fg',
                    )}
                  >
                    {c.label}
                    {active &&
                      (sort.dir === 'asc' ? <ArrowUp size={12} aria-hidden /> : <ArrowDown size={12} aria-hidden />)}
                  </button>
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={rowKey(r)} className="relative border-t border-line hover:bg-elevated/50">
              {columns.map((c, j) =>
                j === 0 ? (
                  <td key={c.key} className="px-3 py-2 break-all">
                    <Link
                      to={href(r)}
                      className="font-medium after:absolute after:inset-0 after:content-[''] hover:underline focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-accent"
                    >
                      {c.cell(r)}
                    </Link>
                  </td>
                ) : (
                  <td
                    key={c.key}
                    className={cn('px-3 py-2 text-right tabular-nums whitespace-nowrap', !c.primary && 'max-sm:hidden')}
                  >
                    {c.cell(r)}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ── screen frame ───────────────────────────────────────────────────── */

export function RangeControls({ perf }: { perf: PerfData }) {
  const t = useT()
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex rounded-control border border-line p-0.5" role="group" aria-label={t('기간')}>
        {RANGES.map((r) => (
          <button
            key={r}
            onClick={() => perf.setRange(r)}
            aria-pressed={perf.range === r}
            className={
              perf.range === r
                ? 'min-w-11 rounded-control bg-elevated px-3 py-2 text-sm font-medium max-sm:px-2'
                : 'min-w-11 rounded-control px-3 py-2 text-sm text-muted hover:text-fg max-sm:px-2'
            }
          >
            {{ '1h': t('1시간'), '24h': t('24시간'), '7d': t('7일'), '30d': t('30일') }[r]}
          </button>
        ))}
      </div>
      <Button variant="secondary" onClick={() => void perf.refresh()} disabled={perf.loading} aria-label={t('새로 고침')}>
        <RefreshCw size={14} className={cn(perf.loading && 'animate-spin')} />
      </Button>
    </div>
  )
}

const TABS = [
  { to: PERF_HOME, label: '대시보드', end: true },
  { to: `${PERF_HOME}/host`, label: '호스트 · 컨테이너', end: false },
  { to: `${PERF_HOME}/models`, label: '모델 · 지연', end: false },
]

/** Links to pages: a nav with aria-current, not tabs. */
export function PerfNav() {
  const t = useT()
  return (
    <nav
      aria-label={t('성능 메뉴')}
      className="mb-5 flex gap-1 border-b border-line max-sm:flex-wrap sm:overflow-x-auto [scrollbar-width:none]"
    >
      {TABS.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end={tab.end}
          className={({ isActive }) =>
            cn(
              '-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-base font-medium transition-colors',
              isActive ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg',
            )
          }
        >
          {t(tab.label)}
        </NavLink>
      ))}
    </nav>
  )
}

export function BackToDashboard() {
  const t = useT()
  return (
    <Link to={PERF_HOME} className="mb-4 inline-flex min-h-9 items-center gap-1.5 text-sm text-muted hover:text-fg">
      <ArrowLeft size={14} aria-hidden />
      {t('대시보드로')}
    </Link>
  )
}

/** Top bar, header with range controls, the menu (or a way back) and the load error. */
export function PerfScreen({
  perf,
  description,
  back = false,
  children,
}: {
  perf: PerfData
  description: string
  back?: boolean
  children: React.ReactNode
}) {
  const t = useT()
  return (
    <>
      <TopBar left={t('성능 모니터링')} />
      <PageBody className="max-w-6xl">
        <PageHeader title={t('성능 모니터링')} description={description} action={<RangeControls perf={perf} />} />
        {back ? <BackToDashboard /> : <PerfNav />}
        {perf.error && (
          <p className="mb-4 rounded-control border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
            {perf.error}
          </p>
        )}
        {children}
      </PageBody>
    </>
  )
}

/** Each verdict with its reason, what to do and one evidence chart. */
export function VerdictList({ verdicts }: { verdicts: PerfOverview['verdicts'] }) {
  const t = useT()
  if (verdicts.length === 0) return null
  return (
    <ul className="mt-3 space-y-3">
      {verdicts.map((v, i) => (
        <li key={`${v.code}-${i}`} className="rounded-control border border-line p-3">
          <div className="flex flex-wrap items-center gap-2">
            <LevelIcon level={v.level} />
            <span className="font-medium">{v.title}</span>
            <Badge tone={LEVEL_TONE[v.level]}>{levelLabel(v.level, t)}</Badge>
          </div>
          <p className="mt-1 text-sm text-muted">{v.detail}</p>
          <p className="mt-1 text-sm">
            <span className="font-medium">{t('권장 조치')}</span> · {v.action}
          </p>
          {v.evidence
            .filter((e) => e.points.length > 1)
            .slice(0, 1)
            .map((e) => (
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
  )
}

/** Missing host metrics or a quiet collector, said once. */
export function CollectorNote({ perf }: { perf: PerfData }) {
  const t = useT()
  const { data, stale } = perf
  if (!data || (data.collector.hostAvailable && !stale)) return null
  return (
    <p className="mt-3 text-xs text-faint">
      {!data.collector.hostAvailable &&
        t('호스트·컨테이너 지표가 없습니다: api 컨테이너에 /proc와 cgroup이 읽기 전용으로 연결되어야 합니다.')}{' '}
      {stale && t('수집기가 최근 5분 동안 표본을 쓰지 않았습니다.')}
    </p>
  )
}

export function Thresholds({ th }: { th: PerfOverview['thresholds'] }) {
  const t = useT()
  return (
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
            {t('최근 15분 20건 이상 중 {n}% 이상 실패 → 주의 (3배면 과부하)').replace('{n}', num(th.errorRateMax, 0))}
          </li>
        </ul>
        <p className="mt-3 text-xs text-faint">
          {t('기준값은 api 환경 변수 PERF_* 로 바꿉니다. 짧은 순간의 급증은 판단에 넣지 않습니다(구간 표본의 80% 이상이 넘어야 함).')}
        </p>
      </Card>
    </Section>
  )
}

export function ModelLatencyTable({
  rows,
  errorRateMax,
  empty,
}: {
  rows: PerfOverview['models']
  errorRateMax: number
  empty: string
}) {
  const t = useT()
  return (
    <Table
      empty={empty}
      head={[t('모델'), t('호출'), t('오류율'), t('p50'), t('p95'), t('p95 최고'), t('첫 토큰 p50'), t('출력 토큰')]}
      rows={rows.map((m) => [
        m.model,
        num(m.calls, 0),
        <span key="e" className={cn(m.errorRate >= errorRateMax && 'text-danger')}>
          {fmt(m.errorRate, '%')}
        </span>,
        fmt(m.latencyP50, 's'),
        fmt(m.latencyP95, 's'),
        fmt(m.latencyP95Peak, 's'),
        fmt(m.ttftP50, 's'),
        num(m.outTokens, 0),
      ])}
    />
  )
}

/** Label and value pairs in two columns. */
export function Facts({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[auto_1fr_auto_1fr]">
      {items.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted">{label}</dt>
          <dd className="text-right tabular-nums sm:text-left">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** Current value with the period's peak beside it. */
export function WithPeak({ value, peak }: { value: string; peak: string }) {
  return (
    <>
      {value}
      <span className="ml-1 text-xs text-faint">↑{peak}</span>
    </>
  )
}
