import { useMemo, useState } from 'react'
import type { SheetModelSheet } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'
import { colLetters, parseRange, type Style } from './sheetData'
import { formatValue } from './sheetFormat'

/** Rows and columns drawn at most: a plain table, not an editor; the whole file is the download. */
const MAX_ROWS = 500
const MAX_COLS = 60
const DEFAULT_WIDTH = 88

const SIDES = { t: 'borderTop', b: 'borderBottom', l: 'borderLeft', r: 'borderRight' } as const

function cellStyle(style: Style | undefined, number: boolean): React.CSSProperties {
  const out: React.CSSProperties = {}
  if (style?.font?.b) out.fontWeight = 600
  if (style?.font?.i) out.fontStyle = 'italic'
  if (style?.font?.u) out.textDecoration = 'underline'
  if (style?.font?.color) out.color = style.font.color
  if (style?.font?.sz) out.fontSize = `${style.font.sz}pt`
  if (style?.fill) out.backgroundColor = style.fill
  const h = style?.align?.h
  out.textAlign = h === 'center' || h === 'right' || h === 'left' ? h : number ? 'right' : 'left'
  if (style?.align?.v === 'center') out.verticalAlign = 'middle'
  if (style?.align?.v === 'bottom') out.verticalAlign = 'bottom'
  if (style?.align?.wrap) out.whiteSpace = 'pre-wrap'
  for (const [side, css] of Object.entries(SIDES)) {
    const edge = style?.border?.[side as keyof typeof SIDES]
    if (edge) out[css] = `1px solid ${edge.color || '#000'}`
  }
  return out
}

/** One sheet as a read-only table: values as the sheet shows them, merges, widths and the basic styles. */
function SheetGrid({ sheet }: { sheet: SheetModelSheet }) {
  const rows = Math.min(sheet.rows, MAX_ROWS)
  const cols = Math.min(sheet.cols, MAX_COLS)
  const { cells, spans, hidden } = useMemo(() => {
    const byKey = new Map(sheet.cells.map((c) => [`${c.r},${c.c}`, c]))
    const spans = new Map<string, { rows: number; cols: number }>()
    const hidden = new Set<string>()
    for (const ref of sheet.merges) {
      const box = parseRange(ref)
      if (!box) continue
      spans.set(`${box.r1},${box.c1}`, { rows: box.r2 - box.r1 + 1, cols: box.c2 - box.c1 + 1 })
      for (let r = box.r1; r <= box.r2; r++) {
        for (let c = box.c1; c <= box.c2; c++) if (r !== box.r1 || c !== box.c1) hidden.add(`${r},${c}`)
      }
    }
    return { cells: byKey, spans, hidden }
  }, [sheet])
  const width = (c: number) => sheet.colWidths[String(c)] ?? DEFAULT_WIDTH

  return (
    <table className="border-collapse text-[13px] text-neutral-900" style={{ tableLayout: 'fixed' }}>
      <colgroup>
        <col style={{ width: 40 }} />
        {Array.from({ length: cols }, (_, c) => (
          <col key={c} style={{ width: width(c) }} />
        ))}
      </colgroup>
      <thead>
        <tr>
          <th className="sticky top-0 left-0 z-20 border border-neutral-300 bg-neutral-100" />
          {Array.from({ length: cols }, (_, c) => (
            <th
              key={c}
              scope="col"
              className="sticky top-0 z-10 border border-neutral-300 bg-neutral-100 px-1 py-0.5 text-xs font-normal text-neutral-500"
            >
              {colLetters(c)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {Array.from({ length: rows }, (_, r) => (
          <tr key={r}>
            <th
              scope="row"
              className="sticky left-0 z-10 border border-neutral-300 bg-neutral-100 px-1 text-xs font-normal text-neutral-500"
            >
              {r + 1}
            </th>
            {Array.from({ length: cols }, (_, c) => {
              const key = `${r},${c}`
              if (hidden.has(key)) return null
              const cell = cells.get(key)
              const span = spans.get(key)
              const style = cell?.s as Style | undefined
              return (
                <td
                  key={c}
                  rowSpan={span?.rows}
                  colSpan={span?.cols}
                  data-cell={key}
                  className={cn('overflow-hidden border border-neutral-200 px-1.5 py-0.5 align-top', !style?.align?.wrap && 'whitespace-nowrap text-ellipsis')}
                  style={cellStyle(style, typeof cell?.v === 'number')}
                >
                  {formatValue(cell?.v, style?.numFmt)}
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** A workbook for reading: a tab per sheet and its table. Plain HTML, so a share page needs no editor code. */
export function SheetTable({
  sheets,
  truncated,
}: {
  sheets: SheetModelSheet[]
  truncated?: boolean
}) {
  const t = useT()
  const [active, setActive] = useState(0)
  const sheet = sheets[Math.min(active, sheets.length - 1)]
  if (!sheet) return <p className="py-12 text-center text-base text-faint">{t('비어 있는 시트입니다.')}</p>
  const cut = truncated || sheet.rows > MAX_ROWS || sheet.cols > MAX_COLS
  return (
    <div className="flex flex-col" data-sheet-table="">
      <div className="h-[70vh] overflow-auto rounded-t-card border border-line bg-white">
        <SheetGrid key={sheet.name} sheet={sheet} />
      </div>
      <div
        role="tablist"
        aria-label={t('시트 목록')}
        className="flex gap-1 overflow-x-auto rounded-b-card border border-t-0 border-line bg-panel px-2 py-1.5"
      >
        {sheets.map((s, i) => (
          <button
            key={s.name}
            type="button"
            role="tab"
            aria-selected={i === active}
            onClick={() => setActive(i)}
            className={cn(
              'shrink-0 rounded-control px-3 py-1 text-sm transition-colors',
              i === active ? 'bg-elevated font-medium text-fg' : 'text-muted hover:text-fg',
            )}
          >
            {s.name}
          </button>
        ))}
      </div>
      {cut && (
        <p className="pt-2 text-xs text-faint" data-testid="shared-sheet-cut">
          {t('앞부분만 보여 줍니다. 전체는 내려받아 확인하세요.')}
        </p>
      )}
    </div>
  )
}
