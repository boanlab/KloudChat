import { describe, expect, it } from 'vitest'
import { formatValue } from './sheetFormat'

describe('formatValue', () => {
  it('shows a plain number as it is and drops float noise', () => {
    expect(formatValue(1200)).toBe('1200')
    expect(formatValue(0.1 + 0.2)).toBe('0.3')
    expect(formatValue('합계')).toBe('합계')
    expect(formatValue(undefined)).toBe('')
    expect(formatValue(true)).toBe('TRUE')
  })
  it('groups thousands and keeps the asked decimals', () => {
    expect(formatValue(1234567, '#,##0')).toBe('1,234,567')
    expect(formatValue(1234.5, '#,##0.00')).toBe('1,234.50')
    expect(formatValue(-1234, '#,##0')).toBe('-1,234')
    expect(formatValue(3.14159, '0.00')).toBe('3.14')
  })
  it('writes a ratio as a percent', () => {
    expect(formatValue(0.125, '0.0%')).toBe('12.5%')
    expect(formatValue(0.5, '0%')).toBe('50%')
  })
  it('writes a date serial as a date', () => {
    // 2026-10-13
    expect(formatValue(46308, 'yyyy-mm-dd')).toBe('2026-10-13')
  })
  it('keeps a currency sign and quoted unit text', () => {
    expect(formatValue(1500, '"₩"#,##0')).toBe('₩1,500')
    expect(formatValue(1500, '#,##0"원"')).toBe('1,500원')
  })
})
