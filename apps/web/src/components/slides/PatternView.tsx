import { ArrowRight, Check, RefreshCw, Star, Target } from 'lucide-react'
import { Fragment, type CSSProperties, type FocusEvent, type ReactNode } from 'react'
import type { Slide } from '@/types'
import { SlideChart } from '@/components/slides/SlideChart'
import { BODY_BOTTOM, BODY_TOP, LEADING, PAD_X, TYPE, em, units } from '@/components/slides/typeScale'
import type { Pattern } from '@/components/slides/patterns'

/** What `SlideView` lends a pattern: its look, accent and unit helpers. */
export interface PatternKit {
  accent: string
  /** Look tokens the other layouts draw with. */
  look: {
    bg: string
    ink: string
    muted: string
    faint: string
    hair: string
    radius: number
    badge: 'square' | 'circle'
    leading: number
    titleWeight: number
  }
  /** The accent tint every filled box uses. */
  tint: string
  /** Text on the accent: white, or the page on `mono`. */
  onAccent: string
  /** Slide units → CSS pixels. */
  px: (n: number) => string
  /** Points → CSS pixels, with the slide's text scale and the 12pt floor. */
  pt: (points: number) => string
  scale: number
  /** A card in the look's style (filled tint or hairline outline). */
  boxed: (extra?: CSSProperties) => CSSProperties
  /** Makes a slot editable; `read` turns the typed text into a slide patch. */
  typed: (key: string, read: (text: string) => Partial<Slide>) => {
    contentEditable?: boolean
    suppressContentEditableWarning?: boolean
    spellCheck?: boolean
    onBlur?: (e: FocusEvent<HTMLElement>) => void
    className?: string
  }
  /** The slot's text, or its stored inline formatting. */
  rich: (key: string, text: string) => ReactNode
  /** The slide as typed so far, for edits that rewrite a whole list. */
  current: () => Slide
  /** The gutter look indents the body, so its box is narrower. */
  gutter: boolean
}

const BODY_HEIGHT = BODY_BOTTOM - BODY_TOP - 4
/** Boxes that fill the body stop short of the foot rule, as the cards layout does. */
const FILL_HEIGHT = 112

/** Connector lines and rings: the accent faded into the page, stronger than a box tint. */
const line = (kit: PatternKit) => `color-mix(in srgb, ${kit.accent} 32%, ${kit.look.bg})`

/** SWOT letters and their colours: strength takes the accent, the rest fixed hues. */
const SWOT: [string, string | null][] = [['S', null], ['W', '#b45309'], ['O', '#15803d'], ['T', '#b91c1c']]
/** The second panel of a pros/cons pair. */
const NEGATIVE = '#b91c1c'

/**
 * A pattern slide's body, drawn by its arrangement (`render`) and parameters. The title
 * above it is `SlideView`'s, as for every body layout.
 */
export function PatternView({ slide, pattern, kit }: { slide: Slide; pattern: Pattern; kit: PatternKit }) {
  const items = (slide.items ?? []).filter(([left, right]) => left?.trim() || right?.trim())
  switch (pattern.render) {
    case 'grid':
      return <Grid items={items} pattern={pattern} kit={kit} />
    case 'list':
      return <List items={items} pattern={pattern} kit={kit} />
    case 'flow':
      return <Flow items={items} pattern={pattern} kit={kit} />
    case 'vflow':
      return <VFlow items={items} kit={kit} />
    case 'stack':
      return <Stack items={items} pattern={pattern} kit={kit} />
    case 'cycle':
      return <Cycle items={items} kit={kit} />
    case 'quad':
      return <Quad items={items} pattern={pattern} kit={kit} />
    case 'columns':
      return <Columns columns={slide.columns ?? []} pattern={pattern} kit={kit} />
    case 'kpi':
      return <Kpi metrics={slide.metrics ?? []} pattern={pattern} kit={kit} />
    case 'chart':
      return slide.chart ? (
        <SlideChart
          chart={{ ...slide.chart, kind: (pattern.params.kind as NonNullable<Slide['chart']>['kind']) ?? slide.chart.kind }}
          accent={kit.accent}
          scale={kit.scale}
          ink={kit.look.ink}
        />
      ) : null
    case 'text':
      return <Text body={slide.body ?? ''} pattern={pattern} kit={kit} />
  }
}

type Pairs = [string, string][]

/** Editable props for one side of `items[i]`. */
function pairSlot(kit: PatternKit, i: number, side: 0 | 1) {
  return kit.typed(`items.${i}.${side}`, (text) => ({
    items: (kit.current().items ?? []).map((pair, at) =>
      at === i ? ((side === 0 ? [text, pair[1]] : [pair[0], text]) as [string, string]) : pair,
    ),
  }))
}

function PairText({ kit, i, side, text, style }: { kit: PatternKit; i: number; side: 0 | 1; text: string; style?: CSSProperties }) {
  return (
    <div style={style} {...pairSlot(kit, i, side)}>
      {kit.rich(`items.${i}.${side}`, text)}
    </div>
  )
}

/** Name and description sizes stepping down as a box gets smaller. */
function sizesFor(rows: number, cols: number): { name: number; text: number } {
  if (rows >= 2 || cols >= 4) return { name: 16, text: 14 }
  if (cols === 3) return { name: TYPE.cardName, text: TYPE.cardText }
  return { name: 22, text: 18 }
}

function Grid({ items, pattern, kit }: { items: Pairs; pattern: Pattern; kit: PatternKit }) {
  const { px, pt, accent, look } = kit
  const style = pattern.params.style ?? 'card'
  const want = pattern.params.cols ?? 3
  // Four items on a three-column grid sit better as two by two.
  const cols = Math.max(1, Math.min(items.length === 4 && want === 3 ? 2 : want, items.length))
  const rows = Math.ceil(items.length / cols)
  const size = sizesFor(rows, cols)
  // One row of cards sits at its own height, as the cards layout does; people and
  // two-row grids fill the body.
  const fill = rows > 1 || style === 'person'
  return (
    <div
      className="grid min-h-0"
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gridAutoRows: fill ? '1fr' : `minmax(${px(80)}, auto)`, gap: px(8), height: fill ? px(FILL_HEIGHT) : undefined }}
    >
      {items.map(([name, text], i) =>
        style === 'person' ? (
          <div key={i} className="flex min-w-0 flex-col items-center justify-center overflow-hidden text-center" style={kit.boxed({ padding: px(7) })}>
            <div
              className="grid shrink-0 place-items-center"
              style={{ width: px(rows > 1 ? 24 : 34), height: px(rows > 1 ? 24 : 34), borderRadius: '50%', background: accent, color: kit.onAccent, fontSize: pt(rows > 1 ? 16 : 20), fontWeight: 700 }}
              aria-hidden
            >
              {Array.from(name.trim())[0] ?? '?'}
            </div>
            <PairText kit={kit} i={i} side={0} text={name} style={{ fontSize: pt(size.name), fontWeight: 700, marginTop: px(5), lineHeight: 1.25 }} />
            <PairText kit={kit} i={i} side={1} text={text} style={{ fontSize: pt(size.text), color: look.muted, marginTop: px(2), lineHeight: 1.35 }} />
          </div>
        ) : style === 'feature' ? (
          <div key={i} className="flex min-w-0 items-start overflow-hidden" style={kit.boxed({ padding: px(7), gap: px(7) })}>
            <div
              className="grid shrink-0 place-items-center tabular-nums"
              style={{ width: px(18), height: px(18), background: accent, color: kit.onAccent, fontSize: pt(13), fontWeight: 700, borderRadius: look.badge === 'circle' ? '50%' : px(look.radius / 2) }}
              aria-hidden
            >
              {String(i + 1).padStart(2, '0')}
            </div>
            <div className="min-w-0 flex-1">
              <PairText kit={kit} i={i} side={0} text={name} style={{ fontSize: pt(size.name), fontWeight: 700, color: accent, lineHeight: 1.25 }} />
              <PairText kit={kit} i={i} side={1} text={text} style={{ fontSize: pt(size.text), marginTop: px(3), lineHeight: 1.4 }} />
            </div>
          </div>
        ) : (
          <div key={i} className="flex min-w-0 flex-col overflow-hidden" style={kit.boxed({ borderTop: `${px(2)} solid ${accent}`, padding: `${px(10)} ${px(9)}` })}>
            <PairText kit={kit} i={i} side={0} text={name} style={{ fontSize: pt(size.name), fontWeight: 700, color: accent, lineHeight: 1.3 }} />
            <PairText kit={kit} i={i} side={1} text={text} style={{ fontSize: pt(size.text), marginTop: px(5), lineHeight: LEADING.cardText }} />
          </div>
        ),
      )}
    </div>
  )
}

function List({ items, pattern, kit }: { items: Pairs; pattern: Pattern; kit: PatternKit }) {
  const { px, pt, accent, look } = kit
  const marker = pattern.params.marker ?? 'number'
  const n = Math.max(items.length, 1)
  // Label and description sizes by how many rows share the body.
  const size = n <= 3 ? { name: 20, text: 16 } : n <= 4 ? { name: 18, text: 14 } : { name: 16, text: 13 }
  const unit = units(size.name)
  const badgeSize = unit * 1.45
  const termWidth = Math.round(Math.max(48, Math.min(110, Math.max(0, ...items.map(([left]) => em(left))) * unit * 1.02 + 6)))
  const mark = (i: number): ReactNode => {
    const icon = { size: unit * 1.1 * kit.scale, strokeWidth: 2.5, color: accent }
    if (marker === 'check') return <span className="grid place-items-center" style={{ width: px(badgeSize), height: px(badgeSize), border: `${px(1.2)} solid ${accent}`, borderRadius: px(Math.min(look.radius, 3)) }}><Check {...icon} /></span>
    if (marker === 'star') return <Star {...icon} fill={accent} />
    if (marker === 'target') return <Target {...icon} />
    if (marker === 'ref') return <span className="tabular-nums" style={{ color: accent, fontWeight: 700, fontSize: pt(size.text) }}>[{i + 1}]</span>
    return (
      <span className="grid place-items-center tabular-nums" style={{ width: px(badgeSize), height: px(badgeSize), background: accent, color: kit.onAccent, fontSize: pt(size.text), fontWeight: 700, borderRadius: look.badge === 'circle' ? '50%' : px(look.radius / 2) }}>
        {i + 1}
      </span>
    )
  }
  if (marker === 'qa') {
    return (
      <div className="flex flex-col justify-center" style={{ gap: px(n > 3 ? 5 : 9) }}>
        {items.map(([question, answer], i) => (
          <div key={i} className="flex flex-col" style={{ gap: px(2), paddingBottom: px(n > 3 ? 4 : 7), borderBottom: i < items.length - 1 ? `1px solid ${look.hair}` : undefined }}>
            <div className="flex items-baseline" style={{ gap: px(7) }}>
              <span style={{ color: accent, fontWeight: 800, fontSize: pt(size.name), width: px(unit * 1.2) }} className="shrink-0">Q</span>
              <PairText kit={kit} i={i} side={0} text={question} style={{ fontSize: pt(size.name), fontWeight: 700, lineHeight: 1.3 }} />
            </div>
            <div className="flex items-baseline" style={{ gap: px(7) }}>
              <span style={{ color: look.faint, fontWeight: 800, fontSize: pt(size.name), width: px(unit * 1.2) }} className="shrink-0">A</span>
              <PairText kit={kit} i={i} side={1} text={answer} style={{ fontSize: pt(size.text), color: look.muted, lineHeight: 1.45 }} />
            </div>
          </div>
        ))}
      </div>
    )
  }
  if (marker === 'term') {
    return (
      <div className="flex flex-col justify-center">
        {items.map(([term, meaning], i) => (
          <div key={i} className="flex items-baseline" style={{ gap: px(12), padding: `${px(n > 4 ? 4 : 6)} 0`, borderTop: i === 0 ? `${px(1.5)} solid ${accent}` : `1px solid ${look.hair}` }}>
            <PairText kit={kit} i={i} side={0} text={term} style={{ width: px(termWidth), flexShrink: 0, fontSize: pt(size.name), fontWeight: 700, color: accent, lineHeight: 1.3 }} />
            <PairText kit={kit} i={i} side={1} text={meaning} style={{ minWidth: 0, flex: 1, fontSize: pt(size.text), lineHeight: 1.45 }} />
          </div>
        ))}
      </div>
    )
  }
  return (
    <div className="flex flex-col justify-center" style={{ gap: px(n > 4 ? 4 : 7) }}>
      {items.map(([name, text], i) => (
        <div key={i} className="flex items-start" style={{ gap: px(9) }}>
          <span className="flex shrink-0 justify-center" style={{ minWidth: px(badgeSize), marginTop: px(marker === 'ref' ? 0.5 : 0) }}>{mark(i)}</span>
          <div className="min-w-0 flex-1">
            <PairText kit={kit} i={i} side={0} text={name} style={{ fontSize: pt(marker === 'ref' ? size.text : size.name), fontWeight: 700, lineHeight: 1.3 }} />
            {text?.trim() && (
              <PairText kit={kit} i={i} side={1} text={text} style={{ fontSize: pt(size.text), color: look.muted, marginTop: px(1), lineHeight: 1.4 }} />
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

function Flow({ items, pattern, kit }: { items: Pairs; pattern: Pattern; kit: PatternKit }) {
  const { px, pt, accent, look } = kit
  const style = pattern.params.style ?? 'arrow'
  const n = Math.max(items.length, 1)
  const size = n <= 3 ? { name: 20, text: 16 } : n === 4 ? { name: 18, text: 14 } : { name: 16, text: 13 }
  if (style === 'chevron') {
    const tip = 9
    return (
      <div className="flex flex-col justify-center" style={{ gap: px(8) }}>
        <div className="flex">
          {items.map(([name], i) => {
            const first = i === 0
            const shade = 100 - Math.round((i / Math.max(n - 1, 1)) * 40)
            const clip = first
              ? `polygon(0 0, calc(100% - ${px(tip)}) 0, 100% 50%, calc(100% - ${px(tip)}) 100%, 0 100%)`
              : `polygon(0 0, calc(100% - ${px(tip)}) 0, 100% 50%, calc(100% - ${px(tip)}) 100%, 0 100%, ${px(tip)} 50%)`
            return (
              <div
                key={i}
                className="grid min-w-0 flex-1 place-items-center text-center"
                style={{
                  height: px(34),
                  marginLeft: first ? 0 : px(-tip + 2),
                  paddingLeft: px(first ? 6 : tip + 4),
                  paddingRight: px(tip + 3),
                  clipPath: clip,
                  background: `color-mix(in srgb, ${accent} ${shade}%, ${look.bg})`,
                  color: kit.onAccent,
                }}
              >
                <PairText kit={kit} i={i} side={0} text={name} style={{ fontSize: pt(size.name), fontWeight: 700, lineHeight: 1.2 }} />
              </div>
            )
          })}
        </div>
        <div className="flex" style={{ gap: px(6) }}>
          {items.map(([, text], i) => (
            <PairText key={i} kit={kit} i={i} side={1} text={text} style={{ flex: 1, minWidth: 0, fontSize: pt(size.text), color: look.muted, lineHeight: 1.45, textAlign: 'center', padding: `0 ${px(3)}` }} />
          ))}
        </div>
      </div>
    )
  }
  if (style === 'phase') {
    return (
      <div className="relative flex flex-col justify-center">
        <div className="relative flex" style={{ gap: px(8) }}>
          <div className="absolute" style={{ left: px(6), right: px(6), top: px(23), height: px(2), background: line(kit) }} />
          {items.map(([when, what], i) => (
            <div key={i} className="relative flex min-w-0 flex-1 flex-col">
              <PairText kit={kit} i={i} side={0} text={when} style={{ fontSize: pt(size.name), fontWeight: 700, color: accent, lineHeight: 1.2, height: px(16), whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} />
              <div style={{ width: px(10), height: px(10), marginTop: px(2), borderRadius: '50%', background: accent, border: `${px(2)} solid ${look.bg}`, boxShadow: `0 0 0 ${px(1)} ${accent}` }} />
              <div className="flex-1" style={kit.boxed({ marginTop: px(8), padding: `${px(8)} ${px(8)}`, borderLeft: `${px(2)} solid ${accent}` })}>
                <PairText kit={kit} i={i} side={1} text={what} style={{ fontSize: pt(size.text), lineHeight: 1.45 }} />
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }
  // arrow: equal boxes with an arrow between each pair.
  return (
    <div className="flex items-stretch" style={{ minHeight: px(70) }}>
      {items.map(([name, text], i) => (
        <Fragment key={i}>
          {i > 0 && (
            <span className="grid shrink-0 place-items-center" style={{ width: px(16) }} aria-hidden>
              <ArrowRight size={11 * kit.scale} strokeWidth={2.5} color={accent} />
            </span>
          )}
          <div className="flex min-w-0 flex-1 flex-col justify-center overflow-hidden" style={kit.boxed({ borderTop: `${px(2)} solid ${accent}`, padding: `${px(10)} ${px(8)}` })}>
            <PairText kit={kit} i={i} side={0} text={name} style={{ fontSize: pt(size.name), fontWeight: 700, color: accent, lineHeight: 1.25 }} />
            <PairText kit={kit} i={i} side={1} text={text} style={{ fontSize: pt(size.text), marginTop: px(5), lineHeight: 1.45 }} />
          </div>
        </Fragment>
      ))}
    </div>
  )
}

function VFlow({ items, kit }: { items: Pairs; kit: PatternKit }) {
  const { px, pt, accent, look } = kit
  const n = Math.max(items.length, 1)
  const row = Math.min(30, BODY_HEIGHT / n)
  const size = n <= 3 ? { name: 18, text: 18 } : n <= 4 ? { name: 16, text: 16 } : { name: 14, text: 14 }
  const label = Math.round(Math.max(40, Math.min(100, Math.max(0, ...items.map(([left]) => em(left))) * units(size.name) * 1.02 + 4)))
  return (
    <div className="relative flex flex-col justify-center">
      <div className="absolute" style={{ left: px(label + 9 + 4), top: px(row / 2), bottom: px(row / 2), width: px(1.5), background: line(kit) }} />
      {items.map(([when, what], i) => (
        <div key={i} className="relative flex items-center" style={{ height: px(row), gap: px(9) }}>
          <PairText kit={kit} i={i} side={0} text={when} style={{ width: px(label), flexShrink: 0, textAlign: 'right', fontSize: pt(size.name), fontWeight: 700, color: accent, lineHeight: 1.2 }} />
          <span className="shrink-0" style={{ width: px(9), height: px(9), borderRadius: '50%', background: i === items.length - 1 ? accent : look.bg, border: `${px(2)} solid ${accent}` }} />
          <PairText kit={kit} i={i} side={1} text={what} style={{ minWidth: 0, flex: 1, fontSize: pt(size.text), lineHeight: 1.4 }} />
        </div>
      ))}
    </div>
  )
}

function Stack({ items, pattern, kit }: { items: Pairs; pattern: Pattern; kit: PatternKit }) {
  const { px, pt, accent, look } = kit
  const funnel = pattern.params.shape === 'funnel'
  const n = Math.max(items.length, 1)
  const gap = 3
  const bar = Math.min(28, (BODY_HEIGHT - gap * (n - 1)) / n)
  const size = n <= 3 ? { name: 18, text: 16 } : { name: 16, text: 14 }
  // Each band's top and bottom widths as a share of the shape's column; the whole reads
  // as one triangle (pyramid) or one inverted one (funnel).
  const edge = (k: number) => 0.3 + 0.7 * (k / n)
  return (
    <div className="flex flex-col justify-center" style={{ gap: px(gap) }}>
      {items.map(([name, text], i) => {
        const top = funnel ? edge(n - i) : edge(i)
        const bottom = funnel ? edge(n - i - 1) : edge(i + 1)
        const inset = (share: number) => `${((1 - share) / 2) * 100}%`
        // The first band (the top) is the strongest.
        const shade = 100 - Math.round((i / Math.max(n - 1, 1)) * 45)
        return (
          <div key={i} className="flex items-center" style={{ height: px(bar), gap: px(14) }}>
            <div
              className="grid h-full shrink-0 place-items-center text-center"
              style={{
                width: '46%',
                clipPath: `polygon(${inset(top)} 0, calc(100% - ${inset(top)}) 0, calc(100% - ${inset(bottom)}) 100%, ${inset(bottom)} 100%)`,
                background: `color-mix(in srgb, ${accent} ${shade}%, ${look.bg})`,
                color: kit.onAccent,
              }}
            >
              <PairText kit={kit} i={i} side={0} text={name} style={{ fontSize: pt(size.name), fontWeight: 700, lineHeight: 1.15, maxWidth: `${Math.max(top, bottom) * 92}%` }} />
            </div>
            <div className="flex min-w-0 flex-1 items-center self-stretch" style={{ borderBottom: `1px dashed ${look.hair}` }}>
              <PairText kit={kit} i={i} side={1} text={text} style={{ fontSize: pt(size.text), lineHeight: 1.35 }} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Cycle({ items, kit }: { items: Pairs; kit: PatternKit }) {
  const { px, pt, accent, look } = kit
  const n = Math.max(items.length, 1)
  const W = 400 - PAD_X - (kit.gutter ? 40 : PAD_X)
  const H = BODY_HEIGHT
  const rx = W * 0.33
  const ry = H * 0.36
  const boxW = Math.min(112, n > 4 ? W / 3.4 : W / 2.9)
  const size = n <= 4 ? { name: 16, text: 13 } : { name: 14, text: 12 }
  const at = (k: number) => {
    const a = -Math.PI / 2 + (k / n) * Math.PI * 2
    return { x: W / 2 + rx * Math.cos(a), y: H / 2 + ry * Math.sin(a), a }
  }
  return (
    <div className="relative" style={{ width: '100%', height: px(H) }}>
      <svg className="absolute inset-0" width="100%" height="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" aria-hidden>
        <ellipse cx={W / 2} cy={H / 2} rx={rx} ry={ry} fill="none" stroke={line(kit)} strokeWidth={2} />
        {items.map((_, k) => {
          // An arrowhead halfway to the next item, pointing along the ring.
          const a = -Math.PI / 2 + ((k + 0.5) / n) * Math.PI * 2
          const x = W / 2 + rx * Math.cos(a)
          const y = H / 2 + ry * Math.sin(a)
          const angle = (Math.atan2(ry * Math.cos(a), -rx * Math.sin(a)) * 180) / Math.PI
          return <path key={k} d="M -3.5 -3.5 L 3 0 L -3.5 3.5 Z" fill={accent} transform={`translate(${x} ${y}) rotate(${angle})`} />
        })}
      </svg>
      <div className="absolute grid place-items-center" style={{ left: '50%', top: '50%', transform: 'translate(-50%, -50%)' }} aria-hidden>
        <RefreshCw size={18 * kit.scale} color={accent} strokeWidth={2} opacity={0.6} />
      </div>
      {items.map(([name, text], k) => {
        const p = at(k)
        return (
          <div
            key={k}
            className="absolute flex flex-col items-center overflow-hidden text-center"
            style={{
              left: `${(p.x / W) * 100}%`,
              top: `${(p.y / H) * 100}%`,
              width: px(boxW),
              transform: 'translate(-50%, -50%)',
              ...kit.boxed({ background: look.bg, border: `${px(1.2)} solid ${accent}`, padding: `${px(4)} ${px(6)}` }),
            }}
          >
            <PairText kit={kit} i={k} side={0} text={name} style={{ fontSize: pt(size.name), fontWeight: 700, color: accent, lineHeight: 1.2 }} />
            {text?.trim() && <PairText kit={kit} i={k} side={1} text={text} style={{ fontSize: pt(size.text), color: look.muted, lineHeight: 1.3, marginTop: px(1) }} />}
          </div>
        )
      })}
    </div>
  )
}

function Quad({ items, pattern, kit }: { items: Pairs; pattern: Pattern; kit: PatternKit }) {
  const { px, pt, accent, look } = kit
  const swot = pattern.params.mode === 'swot'
  const cells = items.slice(0, 4)
  return (
    <div className="grid min-h-0" style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gridTemplateRows: 'repeat(2, minmax(0, 1fr))', gap: px(swot ? 6 : 0), height: px(FILL_HEIGHT) }}>
      {cells.map(([name, text], i) => {
        const [letter, hue] = SWOT[i]
        const colour = hue ?? accent
        return swot ? (
          <div key={i} className="flex min-w-0 items-start overflow-hidden" style={kit.boxed({ padding: px(7), gap: px(7), background: `color-mix(in srgb, ${colour} 8%, ${look.bg})`, border: 'none', borderLeft: `${px(2.5)} solid ${colour}` })}>
            <span className="grid shrink-0 place-items-center" style={{ width: px(20), height: px(20), background: colour, color: '#fff', fontSize: pt(18), fontWeight: 800, borderRadius: look.badge === 'circle' ? '50%' : px(look.radius / 2) }}>
              {letter}
            </span>
            <div className="min-w-0 flex-1">
              <PairText kit={kit} i={i} side={0} text={name} style={{ fontSize: pt(16), fontWeight: 700, color: colour, lineHeight: 1.25 }} />
              <PairText kit={kit} i={i} side={1} text={text} style={{ fontSize: pt(14), marginTop: px(2), lineHeight: 1.4 }} />
            </div>
          </div>
        ) : (
          <div
            key={i}
            className="flex min-w-0 flex-col justify-center overflow-hidden"
            style={{
              padding: `${px(6)} ${px(10)}`,
              borderRight: i % 2 === 0 ? `${px(1.5)} solid ${accent}` : undefined,
              borderBottom: i < 2 ? `${px(1.5)} solid ${accent}` : undefined,
              background: i === 0 ? kit.tint : undefined,
            }}
          >
            <PairText kit={kit} i={i} side={0} text={name} style={{ fontSize: pt(18), fontWeight: 700, color: accent, lineHeight: 1.25 }} />
            <PairText kit={kit} i={i} side={1} text={text} style={{ fontSize: pt(14), marginTop: px(3), lineHeight: 1.4 }} />
          </div>
        )
      })}
    </div>
  )
}

function Columns({ columns, pattern, kit }: { columns: { title: string; items: string[] }[]; pattern: Pattern; kit: PatternKit }) {
  const { px, pt, accent, look } = kit
  const tone = pattern.params.tone ?? 'neutral'
  const cols = columns.slice(0, pattern.count[1])
  const most = Math.max(1, ...cols.map((c) => c.items.length))
  const size = most <= 3 ? 18 : most <= 4 ? 16 : 14
  const colourOf = (i: number) =>
    tone === 'posneg' ? (i === 0 ? accent : NEGATIVE) : tone === 'beforeafter' ? (i === 0 ? look.faint : accent) : accent
  const slot = (c: number, key: string, read: (text: string) => Partial<Slide>) => kit.typed(`columns.${c}.${key}`, read)
  const rewrite = (c: number, change: (col: { title: string; items: string[] }) => { title: string; items: string[] }) => ({
    columns: (kit.current().columns ?? []).map((col, at) => (at === c ? change(col) : col)),
  })
  const divider = (i: number): ReactNode => {
    if (i === 0) return null
    if (tone === 'vs') {
      return (
        <div className="grid shrink-0 place-items-center" style={{ width: px(22) }} aria-hidden>
          <span className="grid place-items-center" style={{ width: px(22), height: px(22), borderRadius: '50%', background: look.ink, color: look.bg, fontSize: pt(13), fontWeight: 800 }}>VS</span>
        </div>
      )
    }
    if (tone === 'beforeafter') {
      return (
        <div className="grid shrink-0 place-items-center" style={{ width: px(22) }} aria-hidden>
          <span className="grid place-items-center" style={{ width: px(20), height: px(20), borderRadius: '50%', background: accent }}>
            <ArrowRight size={11 * kit.scale} strokeWidth={2.75} color={kit.onAccent} />
          </span>
        </div>
      )
    }
    return <div className="shrink-0" style={{ width: px(8) }} aria-hidden />
  }
  return (
    <div className="flex min-h-0 items-stretch" style={{ height: px(FILL_HEIGHT) }}>
      {cols.map((col, c) => {
        const colour = colourOf(c)
        const filled = tone === 'posneg' || tone === 'beforeafter'
        return (
          <Fragment key={c}>
            {divider(c)}
            <div
              className="flex min-w-0 flex-1 flex-col overflow-hidden"
              style={filled
                ? { background: `color-mix(in srgb, ${colour} 8%, ${look.bg})`, borderRadius: px(look.radius), border: `1px solid color-mix(in srgb, ${colour} 30%, ${look.bg})` }
                : kit.boxed()}
            >
              <div
                style={{
                  padding: `${px(6)} ${px(9)}`,
                  fontSize: pt(18),
                  fontWeight: 700,
                  lineHeight: 1.25,
                  ...(filled ? { background: colour, color: '#fff' } : { color: colour, borderBottom: `${px(2)} solid ${colour}` }),
                }}
                {...slot(c, 'title', (text) => rewrite(c, (old) => ({ ...old, title: text })))}
              >
                {kit.rich(`columns.${c}.title`, col.title)}
              </div>
              <ul className="m-0 flex list-none flex-col p-0" style={{ padding: `${px(7)} ${px(9)}`, gap: px(4) }}>
                {col.items.map((item, j) => item.trim() && (
                  <li key={j} className="flex" style={{ gap: px(6), fontSize: pt(size), lineHeight: 1.4 }}>
                    <span style={{ color: colour }} aria-hidden>{tone === 'posneg' ? (c === 0 ? '+' : '−') : '•'}</span>
                    <span
                      className="min-w-0"
                      {...slot(c, `items.${j}`, (text) => rewrite(c, (old) => ({ ...old, items: old.items.map((x, at) => (at === j ? text : x)) })))}
                    >
                      {kit.rich(`columns.${c}.items.${j}`, item)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </Fragment>
        )
      })}
    </div>
  )
}

/** `"72%"` → 72; anything else → null. */
function percentOf(value: string): number | null {
  const match = /(-?\d+(?:[.,]\d+)?)\s*%/.exec(value ?? '')
  if (!match) return null
  const n = Number(match[1].replace(',', '.'))
  return Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : null
}

function Kpi({ metrics, pattern, kit }: { metrics: [string, string][]; pattern: Pattern; kit: PatternKit }) {
  const { px, pt, accent, look } = kit
  const mode = pattern.params.mode ?? 'grid'
  const slot = (i: number, side: 0 | 1) =>
    kit.typed(`metrics.${i}.${side}`, (text) => ({
      metrics: (kit.current().metrics ?? []).map((m, at) => (at === i ? ((side === 0 ? [text, m[1]] : [m[0], text]) as [string, string]) : m)),
    }))
  if (mode === 'bars') {
    const n = Math.max(metrics.length, 1)
    const size = n <= 3 ? 18 : 16
    return (
      <div className="flex flex-col justify-center" style={{ gap: px(n <= 3 ? 12 : 8) }}>
        {metrics.map(([value, label], i) => {
          const share = percentOf(value)
          return (
            <div key={i} className="flex flex-col" style={{ gap: px(3) }}>
              <div className="flex items-baseline justify-between" style={{ gap: px(8) }}>
                <span style={{ fontSize: pt(size), lineHeight: 1.3 }} {...slot(i, 1)}>{kit.rich(`metrics.${i}.1`, label)}</span>
                <span className="tabular-nums" style={{ fontSize: pt(size + 4), fontWeight: 700, color: accent, lineHeight: 1.1 }} {...slot(i, 0)}>{kit.rich(`metrics.${i}.0`, value)}</span>
              </div>
              <div style={{ height: px(7), background: kit.tint, borderRadius: px(Math.min(look.radius, 3.5)), overflow: 'hidden' }}>
                <div style={{ width: `${share ?? 0}%`, height: '100%', background: accent, borderRadius: px(Math.min(look.radius, 3.5)) }} />
              </div>
            </div>
          )
        })}
      </div>
    )
  }
  if (mode === 'compare') {
    const [before, after] = [metrics[0], metrics[1]]
    const figure = (pair: [string, string] | undefined, i: number, strong: boolean) =>
      pair && (
        <div className="flex min-w-0 flex-1 flex-col items-center text-center" style={kit.boxed({ padding: `${px(14)} ${px(10)}`, borderTop: `${px(2)} solid ${strong ? accent : look.faint}` })}>
          <div className="tabular-nums" style={{ fontSize: pt(TYPE.bigNumber - 10), fontWeight: 750, lineHeight: 1, color: strong ? accent : look.muted }} {...slot(i, 0)}>
            {kit.rich(`metrics.${i}.0`, pair[0])}
          </div>
          <div style={{ fontSize: pt(TYPE.metricLabel), marginTop: px(8), color: look.muted }} {...slot(i, 1)}>
            {kit.rich(`metrics.${i}.1`, pair[1])}
          </div>
        </div>
      )
    return (
      <div className="flex flex-1 items-center" style={{ gap: px(10) }}>
        {figure(before, 0, false)}
        <span className="grid shrink-0 place-items-center" aria-hidden>
          <ArrowRight size={22 * kit.scale} strokeWidth={2.5} color={accent} />
        </span>
        {figure(after, 1, true)}
      </div>
    )
  }
  const n = Math.max(metrics.length, 1)
  const cols = n <= 4 ? n : 3
  const twoRows = n > cols
  return (
    <div className="grid flex-1 content-center" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gap: px(twoRows ? 8 : 12) }}>
      {metrics.map(([value, label], i) => (
        <div key={i} className="min-w-0" style={kit.boxed({ borderTop: `${px(2)} solid ${accent}`, padding: twoRows ? `${px(8)} ${px(10)}` : `${px(14)} ${px(12)} ${px(16)}` })}>
          <div className="tabular-nums" style={{ fontSize: pt(twoRows ? 32 : n === 4 ? 36 : TYPE.metric), fontWeight: 700, lineHeight: 1.1, color: accent }} {...slot(i, 0)}>
            {kit.rich(`metrics.${i}.0`, value)}
          </div>
          <div style={{ fontSize: pt(twoRows ? 14 : TYPE.metricLabel), marginTop: px(4), color: look.muted }} {...slot(i, 1)}>
            {kit.rich(`metrics.${i}.1`, label)}
          </div>
        </div>
      ))}
    </div>
  )
}

function Text({ body, pattern, kit }: { body: string; pattern: Pattern; kit: PatternKit }) {
  const { px, pt, accent, look } = kit
  const mode = pattern.params.mode ?? 'question'
  const slot = kit.typed('body', (text) => ({ body: text }))
  const balanced = { textWrap: 'balance' } as CSSProperties
  if (mode === 'question') {
    return (
      <div className="relative flex flex-1 items-center justify-center text-center">
        <span
          className="pointer-events-none absolute select-none"
          style={{ left: '50%', top: '50%', transform: 'translate(-50%, -54%)', fontSize: pt(150), fontWeight: 900, lineHeight: 1, color: `color-mix(in srgb, ${accent} 18%, transparent)` }}
          aria-hidden
        >
          ?
        </span>
        <p className="relative" style={{ ...balanced, fontSize: pt(28), fontWeight: look.titleWeight, lineHeight: 1.35, color: look.ink, maxWidth: '86%' }} {...slot}>
          {kit.rich('body', body)}
        </p>
      </div>
    )
  }
  if (mode === 'definition') {
    return (
      <div className="flex flex-col justify-center" style={{ paddingLeft: px(14), paddingBlock: px(6), borderLeft: `${px(3)} solid ${accent}` }}>
        <span style={{ fontSize: pt(14), fontWeight: 700, color: accent, letterSpacing: px(0.6) }}>정의</span>
        <p style={{ fontSize: pt(TYPE.body + 2), lineHeight: 1.5, marginTop: px(6) }} {...slot}>
          {kit.rich('body', body)}
        </p>
      </div>
    )
  }
  return (
    <div className="flex flex-1 flex-col items-start justify-center" style={{ gap: px(10) }}>
      <span style={{ padding: `${px(2.5)} ${px(9)}`, borderRadius: px(99), background: accent, color: kit.onAccent, fontSize: pt(16), fontWeight: 700 }}>가설</span>
      <p style={{ ...balanced, fontSize: pt(26), fontWeight: 600, lineHeight: 1.45, ...kit.boxed({ padding: `${px(12)} ${px(16)}` }), width: '100%' }} {...slot}>
        {kit.rich('body', body)}
      </p>
    </div>
  )
}
