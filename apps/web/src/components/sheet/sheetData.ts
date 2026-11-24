/**
 * The sheet grid's data, apart from the grid itself: the server's model drawn as Univer
 * workbook data, a Univer workbook read back as cells, and the difference between what was shown
 * and what the grid holds now, as the ops `PATCH /artifacts/{id}/xlsx/cells` takes.
 *
 * Both sides go through the same reading (`readStyle`), so a style only counts as changed when the
 * person changed it. Rows and columns inserted or deleted, and sheets added, renamed or deleted,
 * are tracked as they happen (`Structure`); the baseline is moved the same way before the cells
 * are compared, so the cells that moved are not sent as edits.
 */
import type { SheetModel, SheetModelSheet, SheetOp, SheetStylePatch } from '@/lib/api'

export type Side = 't' | 'b' | 'l' | 'r'
export interface Style {
  font?: { b?: boolean; i?: boolean; u?: boolean; s?: boolean; sz?: number; name?: string; color?: string }
  fill?: string
  numFmt?: string
  align?: { h?: string; v?: string; wrap?: boolean }
  border?: Partial<Record<Side, { style: string; color: string }>>
}
export interface Cell {
  v?: string | number | boolean
  /** Without the leading `=`. */
  f?: string
  s?: Style
}
export interface BaseSheet {
  /** The grid's id for the sheet; stays when it is renamed. */
  id: string
  name: string
  cells: Map<string, Cell>
  merges: Set<string>
  /** Column widths in px, by 0-based column. */
  widths: Map<number, number>
}

/** A change to the shape of a sheet, in the order it was made; `sheet` is the grid's sheet id. */
export type Structure =
  | { kind: 'insertRows' | 'deleteRows' | 'insertCols' | 'deleteCols'; sheet: string; at: number; count: number }

const key = (r: number, c: number) => `${r},${c}`
const unkey = (k: string): [number, number] => {
  const [r, c] = k.split(',')
  return [Number(r), Number(c)]
}

/* ── names and addresses ──────────────────────────────────────────────────────────────────── */

export function colLetters(index: number): string {
  let n = index + 1
  let out = ''
  while (n > 0) {
    out = String.fromCharCode(65 + ((n - 1) % 26)) + out
    n = Math.floor((n - 1) / 26)
  }
  return out
}
export const colIndex = (letters: string) =>
  [...letters].reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0) - 1
export const a1 = (r: number, c: number) => `${colLetters(c)}${r + 1}`

interface Box { r1: number; c1: number; r2: number; c2: number }
export function parseRange(ref: string): Box | null {
  const m = /^\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?$/.exec(ref)
  if (!m) return null
  const c1 = colIndex(m[1])
  const r1 = Number(m[2]) - 1
  return { c1, r1, c2: m[3] ? colIndex(m[3]) : c1, r2: m[4] ? Number(m[4]) - 1 : r1 }
}
const rangeText = (b: Box) =>
  b.r1 === b.r2 && b.c1 === b.c2 ? a1(b.r1, b.c1) : `${a1(b.r1, b.c1)}:${a1(b.r2, b.c2)}`

/* ── the grid's cell style ↔ the model's ──────────────────────────────────────────────────── */

type Raw = Record<string, unknown>
const H: Record<string, number> = { left: 1, center: 2, right: 3, justify: 4, distributed: 5 }
const V: Record<string, number> = { top: 1, center: 2, bottom: 3 }
const BORDER: Record<string, number> = {
  thin: 1, hair: 2, dotted: 3, dashed: 4, dashDot: 5, dashDotDot: 6, double: 7, medium: 8,
  mediumDashed: 9, mediumDashDot: 10, mediumDashDotDot: 11, slantDashDot: 12, thick: 13,
}
const invert = (table: Record<string, number>) =>
  Object.fromEntries(Object.entries(table).map(([name, n]) => [n, name]))
const H_NAME = invert(H)
const V_NAME = invert(V)
const BORDER_NAME = invert(BORDER)
const SIDE_KEY: Record<Side, string> = { t: 't', b: 'b', l: 'l', r: 'r' }

/** A model style as the grid's. */
export function toGridStyle(style: Style): Raw {
  const out: Raw = {}
  const font = style.font
  if (font?.b) out.bl = 1
  if (font?.i) out.it = 1
  if (font?.u) out.ul = { s: 1 }
  if (font?.s) out.st = { s: 1 }
  if (font?.sz) out.fs = font.sz
  if (font?.name) out.ff = font.name
  if (font?.color) out.cl = { rgb: font.color }
  if (style.fill) out.bg = { rgb: style.fill }
  if (style.numFmt) out.n = { pattern: style.numFmt }
  if (style.align?.h && H[style.align.h]) out.ht = H[style.align.h]
  if (style.align?.v && V[style.align.v]) out.vt = V[style.align.v]
  if (style.align?.wrap) out.tb = 3
  if (style.border) {
    const bd: Raw = {}
    for (const side of Object.keys(style.border) as Side[]) {
      const edge = style.border[side]
      if (edge && BORDER[edge.style]) bd[SIDE_KEY[side]] = { s: BORDER[edge.style], cl: { rgb: edge.color } }
    }
    if (Object.keys(bd).length) out.bd = bd
  }
  return out
}

const rgbOf = (value: unknown): string | undefined => {
  const rgb = (value as { rgb?: unknown } | undefined)?.rgb
  return typeof rgb === 'string' && /^#?[0-9a-f]{6,8}$/i.test(rgb) ? `#${rgb.slice(-6).toUpperCase()}` : undefined
}

/** The grid's style (an object, or an id into `styles`) as a model style; undefined when plain. */
export function readStyle(raw: unknown, styles: Record<string, unknown> = {}): Style | undefined {
  const s = (typeof raw === 'string' ? styles[raw] : raw) as Raw | undefined
  if (!s || typeof s !== 'object') return undefined
  const out: Style = {}
  const font: NonNullable<Style['font']> = {}
  if (s.bl === 1) font.b = true
  if (s.it === 1) font.i = true
  if ((s.ul as Raw | undefined)?.s === 1) font.u = true
  if ((s.st as Raw | undefined)?.s === 1) font.s = true
  if (typeof s.fs === 'number') font.sz = s.fs
  if (typeof s.ff === 'string' && s.ff) font.name = s.ff
  const color = rgbOf(s.cl)
  if (color) font.color = color
  if (Object.keys(font).length) out.font = font
  const fill = rgbOf(s.bg)
  if (fill) out.fill = fill
  const pattern = (s.n as Raw | undefined)?.pattern
  if (typeof pattern === 'string' && pattern && pattern !== 'General') out.numFmt = pattern
  const align: NonNullable<Style['align']> = {}
  if (typeof s.ht === 'number' && H_NAME[s.ht]) align.h = H_NAME[s.ht]
  if (typeof s.vt === 'number' && V_NAME[s.vt]) align.v = V_NAME[s.vt]
  if (s.tb === 3) align.wrap = true
  if (Object.keys(align).length) out.align = align
  const bd = s.bd as Raw | undefined
  if (bd) {
    const border: NonNullable<Style['border']> = {}
    for (const side of ['t', 'b', 'l', 'r'] as Side[]) {
      const edge = bd[side] as Raw | undefined
      const name = typeof edge?.s === 'number' ? BORDER_NAME[edge.s] : undefined
      if (name) border[side] = { style: name, color: rgbOf(edge?.cl) ?? '#000000' }
    }
    if (Object.keys(border).length) out.border = border
  }
  return Object.keys(out).length ? out : undefined
}

/** What differs between two cell styles, as the patch the server writes; undefined when they match. */
export function styleDelta(next: Style | undefined, prev: Style | undefined): SheetStylePatch | undefined {
  const out: SheetStylePatch = {}
  const font: NonNullable<SheetStylePatch['font']> = {}
  for (const flag of ['b', 'i', 'u', 's'] as const) {
    if (!!next?.font?.[flag] !== !!prev?.font?.[flag]) font[flag] = !!next?.font?.[flag]
  }
  if (next?.font?.sz !== undefined && next.font.sz !== prev?.font?.sz) font.sz = next.font.sz
  if (next?.font?.name && next.font.name !== prev?.font?.name) font.name = next.font.name
  if (next?.font?.color && next.font.color !== prev?.font?.color) font.color = next.font.color
  if (Object.keys(font).length) out.font = font
  if (next?.fill !== prev?.fill) out.fill = next?.fill ?? null
  if (next?.numFmt !== prev?.numFmt) out.numFmt = next?.numFmt ?? 'General'
  const align: NonNullable<SheetStylePatch['align']> = {}
  if (next?.align?.h !== prev?.align?.h) align.h = next?.align?.h ?? 'general'
  if (next?.align?.v !== prev?.align?.v) align.v = next?.align?.v ?? 'bottom'
  if (!!next?.align?.wrap !== !!prev?.align?.wrap) align.wrap = !!next?.align?.wrap
  if (Object.keys(align).length) out.align = align
  const border: NonNullable<SheetStylePatch['border']> = {}
  for (const side of ['t', 'b', 'l', 'r'] as Side[]) {
    const a = next?.border?.[side]
    const b = prev?.border?.[side]
    if (JSON.stringify(a) !== JSON.stringify(b)) border[side] = a ?? null
  }
  if (Object.keys(border).length) out.border = border
  return Object.keys(out).length ? out : undefined
}

/* ── the model → the grid ─────────────────────────────────────────────────────────────────── */

export const sheetId = (index: number) => `sheet${index}`

/** The model's cells, merges and widths as the baseline a later reading is compared with. */
export function baselineOf(model: SheetModel): BaseSheet[] {
  return model.sheets.map((sheet, i) => {
    const cells = new Map<string, Cell>()
    for (const c of sheet.cells) {
      const cell: Cell = {}
      if (c.v !== undefined && c.v !== '') cell.v = c.v
      if (c.f) cell.f = c.f.replace(/^=/, '')
      const s = c.s ? readStyle(toGridStyle(c.s as Style)) : undefined
      if (s) cell.s = s
      if (cell.v !== undefined || cell.f || cell.s) cells.set(key(c.r, c.c), cell)
    }
    return {
      id: sheetId(i),
      name: sheet.name,
      cells,
      merges: new Set(sheet.merges),
      widths: new Map(Object.entries(sheet.colWidths).map(([c, w]) => [Number(c), w])),
    }
  })
}

/** Univer workbook data for the model (`locale` is the grid's locale name, `koKR` or `enUS`). */
export function toWorkbookData(model: SheetModel, locale: string, name = ''): Raw {
  const styles: Record<string, unknown> = {}
  const sheets: Record<string, unknown> = {}
  let n = 0
  model.sheets.forEach((sheet: SheetModelSheet, i) => {
    const cellData: Record<number, Record<number, Raw>> = {}
    for (const cell of sheet.cells) {
      const out: Raw = {}
      if (cell.f) out.f = cell.f.startsWith('=') ? cell.f : `=${cell.f}`
      if (cell.v !== undefined) {
        out.v = cell.v
        if (typeof cell.v === 'string') out.t = 1
        else if (typeof cell.v === 'boolean') out.t = 3
      }
      if (cell.s) {
        const style = toGridStyle(cell.s as Style)
        if (Object.keys(style).length) {
          const id = `s${n++}`
          styles[id] = style
          out.s = id
        }
      }
      ;(cellData[cell.r] ??= {})[cell.c] = out
    }
    const sheetKey = sheetId(i)
    sheets[sheetKey] = {
      id: sheetKey,
      name: sheet.name,
      rowCount: Math.max(sheet.rows + 20, 100),
      columnCount: Math.max(sheet.cols + 5, 26),
      cellData,
      mergeData: sheet.merges.flatMap((ref) => {
        const box = parseRange(ref)
        return box ? [{ startRow: box.r1, endRow: box.r2, startColumn: box.c1, endColumn: box.c2 }] : []
      }),
      columnData: Object.fromEntries(Object.entries(sheet.colWidths).map(([c, w]) => [c, { w }])),
      rowData: Object.fromEntries(Object.entries(sheet.rowHeights).map(([r, h]) => [r, { h }])),
      ...(sheet.freeze && sheet.freeze.r + sheet.freeze.c > 0
        ? {
            freeze: {
              xSplit: sheet.freeze.c, ySplit: sheet.freeze.r,
              startRow: sheet.freeze.r, startColumn: sheet.freeze.c,
            },
          }
        : {}),
    }
  })
  return {
    id: 'kloudchat-sheet',
    name,
    appVersion: '1',
    locale,
    styles,
    sheetOrder: model.sheets.map((_, i) => sheetId(i)),
    sheets,
  }
}

/* ── the grid → cells ─────────────────────────────────────────────────────────────────────── */

/** Plain text of a rich-text cell (`p`): its paragraphs joined by newlines. */
function richText(p: unknown): string | undefined {
  const stream = (p as { body?: { dataStream?: unknown } } | undefined)?.body?.dataStream
  if (typeof stream !== 'string') return undefined
  return stream.replace(/\r\n$/, '').replace(/\r/g, '\n')
}

/** One grid cell (and its resolved formula) as a model cell; undefined when empty and plain. */
export function readCell(
  raw: Raw | null | undefined,
  formula: string,
  styles: Record<string, unknown>,
): Cell | undefined {
  if (!raw) return undefined
  const cell: Cell = {}
  let v: unknown = raw.p !== undefined ? richText(raw.p) : raw.v
  const t = raw.t
  if (v !== undefined && v !== null && v !== '') {
    if (typeof v === 'boolean') cell.v = v
    else if (t === 3) cell.v = Boolean(Number(v))
    else if (t === 1 || t === 4) cell.v = String(v)
    else if (typeof v === 'number' || typeof v === 'string') cell.v = v
    v = cell.v
  }
  if (formula) cell.f = formula.replace(/^=/, '')
  const s = readStyle(raw.s, styles)
  if (s) cell.s = s
  return cell.v !== undefined || cell.f || cell.s ? cell : undefined
}

/** What the grid holds, for the comparison. */
export interface GridSheet {
  id: string
  name: string
  cells: Map<string, Cell>
  merges: Set<string>
  widths: Map<number, number>
}

/** Reads the grid's `IWorkbookData` snapshot; `formulas(sheetId)` is the resolved formula grid (`FRange.getFormulas`). */
export function readWorkbook(
  snapshot: Raw,
  formulas: (id: string, rows: number, cols: number) => string[][],
): GridSheet[] {
  const styles = (snapshot.styles ?? {}) as Record<string, unknown>
  const sheets = (snapshot.sheets ?? {}) as Record<string, Raw>
  const order = (snapshot.sheetOrder as string[] | undefined) ?? Object.keys(sheets)
  return order.flatMap((id) => {
    const sheet = sheets[id]
    if (!sheet) return []
    const data = (sheet.cellData ?? {}) as Record<string, Record<string, Raw>>
    let rows = 0
    let cols = 0
    for (const [r, row] of Object.entries(data)) {
      for (const c of Object.keys(row)) {
        rows = Math.max(rows, Number(r) + 1)
        cols = Math.max(cols, Number(c) + 1)
      }
    }
    const grid = rows && cols ? formulas(id, rows, cols) : []
    const cells = new Map<string, Cell>()
    for (const [r, row] of Object.entries(data)) {
      for (const [c, raw] of Object.entries(row)) {
        const own = typeof raw.f === 'string' ? raw.f : ''
        const text = own || (raw.si ? (grid[Number(r)]?.[Number(c)] ?? '') : '')
        const cell = readCell(raw, text, styles)
        if (cell) cells.set(key(Number(r), Number(c)), cell)
      }
    }
    const merges = new Set<string>()
    for (const m of (sheet.mergeData ?? []) as Raw[]) {
      const box = {
        r1: Number(m.startRow), r2: Number(m.endRow), c1: Number(m.startColumn), c2: Number(m.endColumn),
      }
      if (box.r1 !== box.r2 || box.c1 !== box.c2) merges.add(rangeText(box))
    }
    const widths = new Map<number, number>()
    for (const [c, info] of Object.entries((sheet.columnData ?? {}) as Record<string, Raw>)) {
      if (typeof info?.w === 'number') widths.set(Number(c), Math.round(info.w))
    }
    return [{ id, name: String(sheet.name ?? id), cells, merges, widths }]
  })
}

/* ── moving the baseline as the grid moved ────────────────────────────────────────────────── */

/** The span `lo..hi` after `count` lines are inserted at `at` or deleted from it; null when gone. */
export function mapSpan(
  lo: number, hi: number, at: number, count: number, remove: boolean,
): [number, number] | null {
  if (!remove) return [lo >= at ? lo + count : lo, hi >= at ? hi + count : hi]
  const end = at + count
  const nl = lo >= end ? lo - count : lo >= at ? at : lo
  const nh = hi >= end ? hi - count : hi >= at ? at - 1 : hi
  return nl > nh ? null : [nl, nh]
}

function moveRef(ref: string, rows: boolean, at: number, count: number, remove: boolean): string | null {
  const box = parseRange(ref)
  if (!box) return ref
  const span = rows
    ? mapSpan(box.r1, box.r2, at, count, remove)
    : mapSpan(box.c1, box.c2, at, count, remove)
  if (!span) return null
  return rangeText(rows ? { ...box, r1: span[0], r2: span[1] } : { ...box, c1: span[0], c2: span[1] })
}

/** The baseline sheet after one structural change; the formulas of moved cells are left as they were. */
export function moveBaseline(sheet: BaseSheet, change: Structure): BaseSheet {
  const rows = change.kind.endsWith('Rows')
  const remove = change.kind.startsWith('delete')
  const cells = new Map<string, Cell>()
  for (const [k, cell] of sheet.cells) {
    const [r, c] = unkey(k)
    const span = mapSpan(rows ? r : c, rows ? r : c, change.at, change.count, remove)
    if (span) cells.set(rows ? key(span[0], c) : key(r, span[0]), cell)
  }
  const merges = new Set<string>()
  for (const ref of sheet.merges) {
    const moved = moveRef(ref, rows, change.at, change.count, remove)
    if (moved && moved.includes(':')) merges.add(moved)
  }
  const widths = new Map<number, number>()
  for (const [c, w] of sheet.widths) {
    if (rows) {
      widths.set(c, w)
      continue
    }
    const span = mapSpan(c, c, change.at, change.count, remove)
    if (span) widths.set(span[0], w)
  }
  return { ...sheet, cells, merges, widths }
}

const PLAIN_NAME = /^[\p{L}_][\p{L}\p{N}_.]*$/u
const LOOKS_LIKE_ADDRESS = /^(?:[A-Za-z]{1,3}\d+|[Rr]\d*[Cc]\d*)$/
const quoted = (name: string) =>
  PLAIN_NAME.test(name) && !LOOKS_LIKE_ADDRESS.test(name) ? name : `'${name.replace(/'/g, "''")}'`
const escapeRe = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** A formula with references to sheet `from` pointing at `to`; string literals are left alone. */
export function renameInFormula(text: string, from: string, to: string): string {
  const pattern = new RegExp(
    `(?<![\\p{L}\\p{N}_.!'])(?:'${escapeRe(from.replace(/'/g, "''"))}'|${escapeRe(from)})!`, 'gu',
  )
  return text
    .split(/("(?:[^"]|"")*")/)
    .map((part, i) => (i % 2 ? part : part.replace(pattern, () => `${quoted(to)}!`)))
    .join('')
}

/* ── the difference ───────────────────────────────────────────────────────────────────────── */

const isError = (v: unknown) => typeof v === 'string' && /^#[A-Z0-9/?!]+$/.test(v)

/** Cell ops that turn `base` into `now` (same sheet, `name` its name in the file at that point). */
function cellOps(name: string, base: BaseSheet | null, now: GridSheet): SheetOp[] {
  const ops: SheetOp[] = []
  const keys = new Set([...(base?.cells.keys() ?? []), ...now.cells.keys()])
  const order = [...keys].map(unkey).sort((x, y) => x[0] - y[0] || x[1] - y[1])
  for (const [r, c] of order) {
    const was = base?.cells.get(key(r, c))
    const is = now.cells.get(key(r, c))
    const op: { sheet: string; r: number; c: number; v?: string | number | boolean | null; f?: string; s?: SheetStylePatch } = {
      sheet: name, r, c,
    }
    let changed = false
    if (is?.f) {
      // an unchanged formula whose result moved with its inputs is sent with the new result,
      // unless the grid could not compute it (it then keeps the file's)
      if (is.f !== was?.f || (is.v !== was?.v && !isError(is.v) && is.v !== undefined)) {
        op.f = is.f
        op.v = is.v ?? null
        changed = true
      }
    } else if (was?.f || is?.v !== was?.v) {
      op.v = is?.v ?? null
      changed = true
    }
    const s = styleDelta(is?.s, was?.s)
    if (s) {
      op.s = s
      changed = true
    }
    if (changed) ops.push(op as SheetOp)
  }
  const merged = [...now.merges].filter((ref) => !base?.merges.has(ref))
  const unmerged = [...(base?.merges ?? [])].filter((ref) => !now.merges.has(ref))
  for (const ref of unmerged) ops.push({ sheet: name, op: 'unmerge', ref })
  for (const ref of merged) ops.push({ sheet: name, op: 'merge', ref })
  for (const [c, w] of now.widths) {
    if (base?.widths.get(c) !== w) ops.push({ sheet: name, op: 'width', c, px: w })
  }
  return ops
}

/**
 * The ops that turn what was shown into what the grid holds: shape changes first (by the sheet
 * names of the file), then sheets deleted, renamed and added, then the changed cells.
 */
export function diffWorkbook(
  baseline: BaseSheet[],
  structure: Structure[],
  now: GridSheet[],
): SheetOp[] {
  const ops: SheetOp[] = []
  const alive = new Set(now.map((sheet) => sheet.id))
  const nameOf = new Map(baseline.map((sheet) => [sheet.id, sheet.name]))
  let moved = baseline
  for (const change of structure) {
    const own = moved.find((sheet) => sheet.id === change.sheet)
    // a sheet made this session starts empty, so its shape changes need no ops; one deleted needs none either
    if (!own || !alive.has(own.id)) continue
    ops.push({ sheet: nameOf.get(own.id)!, op: change.kind, at: change.at, count: change.count })
    moved = moved.map((sheet) => (sheet === own ? moveBaseline(sheet, change) : sheet))
  }
  for (const sheet of baseline) {
    if (!alive.has(sheet.id)) ops.push({ sheet: sheet.name, op: 'deleteSheet' })
  }
  const kept = moved.filter((sheet) => alive.has(sheet.id))
  const renamed = kept.flatMap((sheet) => {
    const next = now.find((x) => x.id === sheet.id)!
    return next.name === sheet.name ? [] : [{ id: sheet.id, from: sheet.name, to: next.name }]
  })
  // names may swap or chain: every renamed sheet leaves its name first
  const clash = renamed.some((r) => kept.some((k) => k.name === r.to && k.id !== r.id))
  const file = new Map(kept.map((sheet) => [sheet.id, sheet.name]))
  if (clash) {
    renamed.forEach((r, i) => ops.push({ sheet: r.from, op: 'renameSheet', name: `__kc${i}` }))
    renamed.forEach((r, i) => ops.push({ sheet: `__kc${i}`, op: 'renameSheet', name: r.to }))
  } else {
    for (const r of renamed) ops.push({ sheet: r.from, op: 'renameSheet', name: r.to })
  }
  for (const r of renamed) file.set(r.id, r.to)
  // formulas the server rewrote for a rename are the baseline's too
  const renameAll = (text: string) => renamed.reduce((t, r) => renameInFormula(t, r.from, r.to), text)
  const base = new Map(
    kept.map((sheet) => {
      const cells = new Map<string, Cell>()
      for (const [k, cell] of sheet.cells) {
        cells.set(k, cell.f ? { ...cell, f: renameAll(cell.f) } : cell)
      }
      return [sheet.id, { ...sheet, cells }]
    }),
  )
  now.forEach((sheet, index) => {
    if (!base.has(sheet.id)) ops.push({ op: 'addSheet', name: sheet.name, index })
  })
  for (const sheet of now) ops.push(...cellOps(sheet.name, base.get(sheet.id) ?? null, sheet))
  return ops
}

/** The grid's state as the next baseline: what was just saved. */
export function baselineFrom(now: GridSheet[]): BaseSheet[] {
  return now.map((sheet) => ({
    id: sheet.id,
    name: sheet.name,
    cells: new Map(sheet.cells),
    merges: new Set(sheet.merges),
    widths: new Map(sheet.widths),
  }))
}
