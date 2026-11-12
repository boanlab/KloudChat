import { describe, expect, it } from 'vitest'
import { CRV_KO, relabelHeader, STAGGER, staggered, translateKo, wordAt } from './slideTools'

const canvas = { width: 960, height: 540 }
const box = (id: string, x: number, y: number) => ({ id, x, y, width: 200, height: 100 })

describe('staggered', () => {
  it('leaves a new element where it is when nothing else starts there', () => {
    expect(staggered(box('new', 100, 200), [box('a', 300, 200)], canvas)).toEqual({ x: 100, y: 200 })
  })

  it('moves each new element one step off those already at its spot', () => {
    const first = box('a', 100, 200)
    expect(staggered(box('b', 100, 200), [first], canvas)).toEqual({ x: 100 + STAGGER, y: 200 + STAGGER })
    const second = box('b', 100 + STAGGER, 200 + STAGGER)
    expect(staggered(box('c', 100, 200), [first, second], canvas))
      .toEqual({ x: 100 + 2 * STAGGER, y: 200 + 2 * STAGGER })
  })

  it('does not count the element itself, and stays on the slide', () => {
    const added = box('a', 700, 400)
    expect(staggered(added, [added], canvas)).toEqual({ x: 700, y: 400 })
    const crowd = Array.from({ length: 10 }, (_, i) => box(`o${i}`, 700 + i * STAGGER, 400 + i * STAGGER))
    const place = staggered(box('new', 700, 400), crowd, canvas)
    expect(place.x + 200).toBeLessThanOrEqual(canvas.width)
    expect(place.y + 100).toBeLessThanOrEqual(canvas.height)
  })
})

describe('relabelHeader', () => {
  const table = {
    rows: [
      { cells: [{ text: 'Header 1' }, { text: 'Header 2' }], height: 40 },
      { cells: [{ text: '' }, { text: '' }], height: 36 },
    ],
    firstRowHeader: true,
  }

  it('names a new table’s columns in the person’s language and keeps the rest', () => {
    const made = relabelHeader(table, '항목 {n}')!
    expect(made.rows[0].cells.map((c) => c.text)).toEqual(['항목 1', '항목 2'])
    expect(made.rows[0].height).toBe(40)
    expect(made.rows[1]).toBe(table.rows[1])
    expect(made.firstRowHeader).toBe(true)
  })

  it('leaves a table someone already wrote alone', () => {
    expect(relabelHeader({ rows: [{ cells: [{ text: '단계' }] }] }, '항목 {n}')).toBeNull()
  })
})

describe('wordAt', () => {
  it('is the word around a caret, or nothing in a space', () => {
    expect(wordAt('추진 배경 수정됨', 7)).toEqual({ start: 6, end: 9 })
    expect(wordAt('추진 배경', 2)).toEqual({ start: 0, end: 2 })
    expect(wordAt('a  b', 2)).toBeNull()
  })
})

describe('translateKo', () => {
  it('gives Korean for the editor’s words that show in the canvas, English otherwise', () => {
    expect(translateKo('pptx.selectionOverlay.rotate')).toBe('회전')
    expect(translateKo('pptx.ribbon.home')).toBeUndefined()
    expect(Object.values(CRV_KO).every((word) => /[가-힣]/.test(word))).toBe(true)
  })
})
