import { describe, expect, it } from 'vitest'
import { csvCell, toCsv } from './csv'
import { ariaSort, compareValues, decodeSort, encodeSort, nextSort, sortRows, sortValue } from './tableSort'

describe('sortValue', () => {
  it('reads byte sizes in binary units', () => {
    expect(sortValue('2.2 GB')).toBeGreaterThan(sortValue('499 MB') as number)
    expect(sortValue('499 MB')).toBeGreaterThan(sortValue('1,023 KB') as number)
    expect(sortValue('1 KB')).toBe(1024)
    expect(sortValue('512 B')).toBe(512)
  })

  it('reads rates, and only the first of a pair', () => {
    expect(sortValue('1.9 MB/s')).toBeGreaterThan(sortValue('3.1 KB/s') as number)
    expect(sortValue('1.9 MB/s / 3.1 KB/s')).toBe(sortValue('1.9 MB/s'))
  })

  it('reads percents, durations, counts and labelled figures', () => {
    expect(sortValue('45%')).toBe(45)
    expect(sortValue('CPU 12% · 40%')).toBe(12)
    expect(sortValue('1.2s')).toBeGreaterThan(sortValue('900ms') as number)
    expect(sortValue('320ms')).toBeCloseTo(0.32)
    expect(sortValue('1,234')).toBe(1234)
    expect(sortValue('-3.5')).toBe(-3.5)
    expect(sortValue('3.4M')).toBe(3_400_000)
    expect(sortValue('1.5k')).toBe(1500)
    expect(sortValue('12,345 cr')).toBe(12345)
  })

  it('orders relative times oldest first when ascending', () => {
    const ago = ['방금', '3분 전', '2시간 전', '5d ago'].map(sortValue) as number[]
    expect([...ago].sort((a, b) => a - b)).toEqual([ago[3], ago[2], ago[1], ago[0]])
  })

  it('keeps names that merely hold digits as text', () => {
    expect(sortValue('gpu-1')).toBe('gpu-1')
    expect(sortValue('local/qwen3.6-27b')).toBe('local/qwen3.6-27b')
    expect(sortValue('api-2')).toBe('api-2')
  })

  it('reads dates, numbers and empty cells', () => {
    expect(sortValue('2026-11-13T09:00:00Z')).toBe(Date.parse('2026-11-13T09:00:00Z'))
    expect(sortValue(7)).toBe(7)
    expect(sortValue(Number.NaN)).toBeNull()
    for (const empty of ['—', '–', '-', '', '  ', null, undefined]) expect(sortValue(empty)).toBeNull()
  })
})

describe('compareValues and sortRows', () => {
  it('puts empty values last both ways', () => {
    const rows = ['2 MB', '—', '1 GB', '10 KB']
    expect(sortRows(rows, (r) => r, 'asc')).toEqual(['10 KB', '2 MB', '1 GB', '—'])
    expect(sortRows(rows, (r) => r, 'desc')).toEqual(['1 GB', '2 MB', '10 KB', '—'])
  })

  it('compares text naturally and keeps equal rows in their order', () => {
    expect(sortRows(['item 10', 'item 2', 'Item 1'], (r) => r, 'asc', 'en')).toEqual(['Item 1', 'item 2', 'item 10'])
    const rows = [
      { id: 'a', v: 1 },
      { id: 'b', v: 1 },
      { id: 'c', v: 0 },
    ]
    expect(sortRows(rows, (r) => r.v, 'desc').map((r) => r.id)).toEqual(['a', 'b', 'c'])
    expect(compareValues(1, 'x', 'asc')).toBeLessThan(0)
  })
})

describe('heading cycle and storage', () => {
  it('goes first direction → other → unsorted, and restarts on another column', () => {
    let s = nextSort(null, 'mem', 'desc')
    expect(s).toEqual({ key: 'mem', dir: 'desc' })
    s = nextSort(s, 'mem', 'desc')
    expect(s).toEqual({ key: 'mem', dir: 'asc' })
    expect(nextSort(s, 'mem', 'desc')).toBeNull()
    expect(nextSort(s, 'cpu')).toEqual({ key: 'cpu', dir: 'asc' })
    expect(ariaSort(s, 'mem')).toBe('ascending')
    expect(ariaSort(s, 'cpu')).toBe('none')
  })

  it('round-trips through text and drops unknown columns', () => {
    expect(decodeSort(encodeSort({ key: 'mem', dir: 'desc' }))).toEqual({ key: 'mem', dir: 'desc' })
    expect(decodeSort('name', ['name'])).toEqual({ key: 'name', dir: 'asc' })
    expect(decodeSort('-gone', ['name'])).toBeNull()
    expect(encodeSort(null)).toBe('')
  })
})

describe('csv', () => {
  it('quotes separators and keeps formula-looking text as text', () => {
    expect(csvCell('a,b')).toBe('"a,b"')
    expect(csvCell('say "hi"')).toBe('"say ""hi"""')
    expect(csvCell('=SUM(A1)')).toBe("'=SUM(A1)")
    expect(csvCell(-3)).toBe('-3')
    expect(csvCell(null)).toBe('')
    expect(toCsv([['a', 1], ['b\nc', 2]])).toBe('a,1\r\n"b\nc",2')
  })
})
