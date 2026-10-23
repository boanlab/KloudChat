import { describe, expect, it } from 'vitest'
import { escapeCurrency } from '@/lib/mathDollars'

describe('escapeCurrency', () => {
  it('keeps prices in a sentence as prices', () => {
    expect(escapeCurrency('20, 1k docs, $4, $38, 9s')).toBe('20, 1k docs, \\$4, \\$38, 9s')
    expect(escapeCurrency('Cost is $4 vs $38 per 1k docs.')).toBe('Cost is \\$4 vs \\$38 per 1k docs.')
  })

  it('leaves inline and display math as written', () => {
    expect(escapeCurrency('where $x^2 + y$ holds')).toBe('where $x^2 + y$ holds')
    expect(escapeCurrency('$$\na + b\n$$')).toBe('$$\na + b\n$$')
    expect(escapeCurrency('already \\$5')).toBe('already \\$5')
  })

  it('takes linear time on a long run of dollars', () => {
    const started = performance.now()
    escapeCurrency('$1 '.repeat(20000))
    expect(performance.now() - started).toBeLessThan(500)
  })
})
