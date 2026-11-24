import { describe, expect, it } from 'vitest'
import type { SheetModel } from '@/lib/api'
import {
  baselineFrom, baselineOf, diffWorkbook, readWorkbook, renameInFormula, toWorkbookData,
  type GridSheet,
} from './sheetData'

const model = (): SheetModel => ({
  version: 3,
  title: '매출',
  kept: [],
  truncated: false,
  sheets: [
    {
      name: '매출현황', rows: 4, cols: 5,
      cells: [
        { r: 0, c: 0, v: '지점', s: { font: { b: true }, fill: '#DDEBF7' } },
        { r: 0, c: 1, v: '1월' },
        { r: 1, c: 0, v: '서울' },
        { r: 1, c: 1, v: 10, s: { numFmt: '#,##0', align: { h: 'right' } } },
        { r: 2, c: 0, v: '부산' },
        { r: 2, c: 1, v: 5 },
        { r: 3, c: 0, v: '합계' },
        { r: 3, c: 1, v: 15, f: 'SUM(B2:B3)' },
      ],
      merges: ['D1:E1'], colWidths: { '0': 120 }, rowHeights: {},
    },
    { name: '요약', rows: 1, cols: 1, cells: [{ r: 0, c: 0, v: 15, f: "'매출현황'!B4" }], merges: [], colWidths: {}, rowHeights: {} },
  ],
})

/** What the grid would hold after load: the workbook data read back as cells. */
function grid(m: SheetModel): GridSheet[] {
  return readWorkbook(toWorkbookData(m, 'koKR') as never, () => [])
}

describe('a sheet shown and not touched', () => {
  it('differs from itself by nothing, so a save sends no ops', () => {
    const m = model()
    expect(diffWorkbook(baselineOf(m), [], grid(m))).toEqual([])
  })
})

describe('cell edits', () => {
  it('sends a changed value, a cleared cell, a typed formula and a style change', () => {
    const m = model()
    const now = grid(m)
    const first = now[0]
    first.cells.set('1,1', { ...first.cells.get('1,1')!, v: 12 })
    first.cells.delete('2,0')
    first.cells.set('5,0', { v: '새 줄 한글' })
    first.cells.set('0,0', { v: '지점', s: { font: { b: false }, fill: '#FFFF00' } })
    first.cells.set('3,2', { f: 'B4*2', v: 30 })
    const ops = diffWorkbook(baselineOf(m), [], now)
    expect(ops).toContainEqual({ sheet: '매출현황', r: 1, c: 1, v: 12 })
    expect(ops).toContainEqual({ sheet: '매출현황', r: 2, c: 0, v: null })
    expect(ops).toContainEqual({ sheet: '매출현황', r: 5, c: 0, v: '새 줄 한글' })
    expect(ops).toContainEqual({ sheet: '매출현황', r: 3, c: 2, f: 'B4*2', v: 30 })
    // bold dropped (the fill had no bold of its own to keep), fill changed; nothing else named
    const style = ops.find((op) => 'r' in op && op.r === 0 && op.c === 0) as { s: unknown }
    expect(style.s).toEqual({ font: { b: false }, fill: '#FFFF00' })
    expect(ops.filter((op) => 'r' in op)).toHaveLength(5)
  })

  it('sends a formula result that moved with its input, but not an error the grid could not compute', () => {
    const m = model()
    const now = grid(m)
    now[0].cells.set('3,1', { v: 20, f: 'SUM(B2:B3)' })
    now[1].cells.set('0,0', { v: '#NAME?', f: "'매출현황'!B4" })
    const ops = diffWorkbook(baselineOf(m), [], now)
    expect(ops).toEqual([{ sheet: '매출현황', r: 3, c: 1, f: 'SUM(B2:B3)', v: 20 }])
  })

  it('sends merges, unmerges and column widths', () => {
    const m = model()
    const now = grid(m)
    now[0].merges.delete('D1:E1')
    now[0].merges.add('A6:B6')
    now[0].widths.set(1, 140)
    const ops = diffWorkbook(baselineOf(m), [], now)
    expect(ops).toEqual([
      { sheet: '매출현황', op: 'unmerge', ref: 'D1:E1' },
      { sheet: '매출현황', op: 'merge', ref: 'A6:B6' },
      { sheet: '매출현황', op: 'width', c: 1, px: 140 },
    ])
  })
})

describe('rows and columns', () => {
  it('moves the baseline with an inserted row, so only what the grid rewrote is sent', () => {
    const m = model()
    const now = grid(m)
    const sheet = now[0]
    // the grid: a row inserted above row 2 (index 1); cells move down, the formula follows
    const moved = new Map<string, (typeof sheet.cells extends Map<string, infer C> ? C : never)>()
    for (const [k, cell] of sheet.cells) {
      const [r, c] = k.split(',').map(Number)
      moved.set(`${r >= 1 ? r + 1 : r},${c}`, k === '3,1' ? { ...cell, f: 'SUM(B3:B4)' } : cell)
    }
    sheet.cells = moved
    const ops = diffWorkbook(
      baselineOf(m),
      [{ kind: 'insertRows', sheet: 'sheet0', at: 1, count: 1 }],
      now,
    )
    expect(ops[0]).toEqual({ sheet: '매출현황', op: 'insertRows', at: 1, count: 1 })
    // the sum moved to row 5 and its range moved: one cell op for the formula, none for plain moved cells
    expect(ops.slice(1)).toEqual([{ sheet: '매출현황', r: 4, c: 1, f: 'SUM(B3:B4)', v: 15 }])
  })

  it('closes up merges and widths with a deleted column', () => {
    const m = model()
    const now = grid(m)
    const sheet = now[0]
    sheet.cells = new Map([...sheet.cells].flatMap(([k, cell]) => {
      const [r, c] = k.split(',').map(Number)
      return c === 0 ? [] : [[`${r},${c - 1}`, cell] as const]
    }))
    sheet.merges = new Set(['C1:D1'])
    sheet.widths = new Map()
    const ops = diffWorkbook(baselineOf(m), [{ kind: 'deleteCols', sheet: 'sheet0', at: 0, count: 1 }], now)
    expect(ops).toEqual([{ sheet: '매출현황', op: 'deleteCols', at: 0, count: 1 }])
  })
})

describe('sheets', () => {
  it('renames, deletes and adds, in an order the server can follow, then sends the new sheet cells', () => {
    const m = model()
    const now = grid(m)
    now[0].name = '판매 현황'
    now.splice(1, 1)
    now.push({ id: 'new', name: '메모', cells: new Map([['0,0', { v: '안녕' }]]), merges: new Set(), widths: new Map() })
    const ops = diffWorkbook(baselineOf(m), [], now)
    expect(ops).toEqual([
      { sheet: '요약', op: 'deleteSheet' },
      { sheet: '매출현황', op: 'renameSheet', name: '판매 현황' },
      { op: 'addSheet', name: '메모', index: 1 },
      { sheet: '메모', r: 0, c: 0, v: '안녕' },
    ])
  })

  it('does not send the rewrite of a formula that names a renamed sheet', () => {
    const m = model()
    const now = grid(m)
    now[0].name = '판매 현황'
    now[1].cells.set('0,0', { v: 15, f: "'판매 현황'!B4" })
    expect(diffWorkbook(baselineOf(m), [], now)).toEqual([
      { sheet: '매출현황', op: 'renameSheet', name: '판매 현황' },
    ])
  })

  it('swaps two names through a temporary one', () => {
    const m = model()
    const now = grid(m)
    now[0].name = '요약'
    now[1].name = '매출현황'
    const ops = diffWorkbook(baselineOf(m), [], now).filter((op) => 'op' in op && op.op === 'renameSheet')
    expect(ops).toHaveLength(4)
  })

  it('takes what was just sent as the new baseline', () => {
    const m = model()
    const now = grid(m)
    now[0].cells.set('1,1', { v: 99 })
    expect(diffWorkbook(baselineFrom(now), [], now)).toEqual([])
  })
})

describe('formulas that name a sheet', () => {
  it('follow a rename, quoting only when needed, and leave text alone', () => {
    expect(renameInFormula("'원본'!B2*2", '원본', '판매 원본')).toBe("'판매 원본'!B2*2")
    expect(renameInFormula('원본!B2+1', '원본', '합계')).toBe('합계!B2+1')
    expect(renameInFormula('"원본!B2"&원본!A1', '원본', 'X1')).toBe("\"원본!B2\"&'X1'!A1")
    expect(renameInFormula('다른원본!A1', '원본', 'Z')).toBe('다른원본!A1')
  })
})
