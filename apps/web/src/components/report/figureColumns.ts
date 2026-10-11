/** Figure columns flush right (`.fig`, head included), total row `.total`; mirrors `services/table_figures.py` (test keeps patterns equal). */
import { isBlank } from '@/lib/placeholders'

/** Money or count header. */
export const NUMERIC_HEAD = /금액|단가|수량|합계|소계|총액|총계|예산|비용|가격|요금|점수|비율|증감|%|(?:^|[\s(（/])(?:천|만|백만|억)?원(?:[)）\s]|$)/
/** One figure; unit and citation marks allowed. */
export const NUMERIC_CELL = /^[-+−△▲▼]?\s*\d[\d,]*(?:\.\d+)?\s*(?:%p?|원|천원|만원|백만원|억원|명|개|건|회|식|부|대|쪽|MM|M\/M|개월|년|일|시간|분|초|점|배|kg|km|ms|GB|MB|TB)?(?:\s*\[\d+\])*$/
/** Dates read as labels, not amounts. */
export const DATE_CELL = /^(?:19|20)\d{2}[.\-/]/
/** No-figure cells: neither make nor break a numeric column. */
export const NO_FIGURE = new Set(['', '-', '–', '—', '·', '자료 없음', '해당 없음', '미정', '확인 필요', '조건 미확인', '(기입)'])
export const TOTAL_LABEL = /^(?:합\s?계|총\s?계|총\s?액|계)(?:\s*\(.{0,30}\))?$/

const plain = (text: string) => text
  .replace(/\*\*(.+?)\*\*/g, '$1')
  .replace(/(?<!\w)\*(.+?)\*(?!\w)/g, '$1')
  .replace(/`(\S(?:[^`]*\S)?)`/g, '$1')
  .replace(/ /g, ' ')
  .trim()

/** `table_figures.figure_columns`: money/count header over ≥ half figures, or all figures (first column only if its header says so). */
export function figureColumns(rows: string[][]): Set<number> {
  const out = new Set<number>()
  if (rows.length < 2) return out
  const [head, ...body] = rows
  const width = Math.max(...rows.map((row) => row.length))
  for (let column = 0; column < width; column += 1) {
    const cells = body
      .filter((row) => column < row.length && !NO_FIGURE.has(plain(row[column])) && !isBlank(row[column]))
      .map((row) => plain(row[column]))
    const figures = cells.filter((cell) => NUMERIC_CELL.test(cell) && !DATE_CELL.test(cell)).length
    if (!figures) continue
    const named = column < head.length && NUMERIC_HEAD.test(plain(head[column]))
    if (named && figures * 2 >= cells.length) out.add(column)
    else if (column && figures === cells.length) out.add(column)
  }
  return out
}

export const isTotal = (row: string[]) => row.length > 0 && TOTAL_LABEL.test(plain(row[0]))

/** `table_figures.mark_html`. */
export function markFigureColumns(html: string): string {
  if (!/<table\b/i.test(html) || typeof DOMParser === 'undefined') return html
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html')
  doc.querySelectorAll('table').forEach((table) => {
    if (table.classList.contains('tb-fields') || table.querySelector('[colspan],[rowspan]')) return
    const rows = [...table.querySelectorAll('tr')]
    const text = rows.map((row) => [...row.children].map((cell) => cell.textContent ?? ''))
    const columns = figureColumns(text)
    rows.forEach((row, at) => {
      if (at && isTotal(text[at])) row.classList.add('total')
      ;[...row.children].forEach((cell, index) => { if (columns.has(index)) cell.classList.add('fig') })
    })
  })
  return doc.body.innerHTML
}
