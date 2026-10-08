/**
 * One type scale for documents, in points.
 *
 * `apps/api/app/services/doc_type.py` carries the same table for the page seeds, the
 * reportlab PDF, the `.docx` and the `.hwpx`; a test keeps the two equal. The web view
 * draws these at 96 dpi (`px`). Change a number in both files.
 */
export const TYPE = {
  title: 20,
  lead: 11.5,
  h1: 14,
  h2: 12,
  h3: 11,
  body: 10.5,
  table: 9.5,
  caption: 9,
  note: 9,
  small: 9,
  kpi: 20,
  kpiLabel: 9,
  sectionNumber: 10.5,
  pageNumber: 9,
} as const

/** Line height as a multiple of the size. */
export const LEADING = {
  title: 1.25,
  heading: 1.35,
  body: 1.6,
  table: 1.45,
  note: 1.5,
} as const

/**
 * A format's own scale over `TYPE` and `LEADING`, by `doc_formats` id: the 개조식 1쪽 보고
 * (`doc_type.FORMAT_TYPE`). Its sizes replace the shared ones wherever that format is drawn.
 */
export const FORMAT_TYPE: Record<string, Partial<Record<keyof typeof TYPE, number>>> = {
  gaejo: {
    title: 18,
    h1: 14,
    h2: 13,
    h3: 12,
    body: 12,
    table: 10.5,
    caption: 10.5,
    note: 10.5,
    small: 10,
    sectionNumber: 14,
  },
}

export const FORMAT_LEADING: Record<string, Partial<Record<keyof typeof LEADING, number>>> = {
  gaejo: {
    heading: 1.3,
    body: 1.6,
    table: 1.35,
    note: 1.5,
  },
}

/** Vertical space in points around a format's blocks (`doc_type.FORMAT_SPACE`). */
export const FORMAT_SPACE: Record<string, Record<'headingBefore' | 'headingAfter' | 'item' | 'callout' | 'head', number>> = {
  gaejo: {
    headingBefore: 7,
    headingAfter: 2,
    item: 0,
    callout: 4,
    head: 6,
  },
}

/** A format's page margins in mm when the document sets none (`doc_type.FORMAT_MARGINS`). */
export const FORMAT_MARGINS: Record<string, Record<'top' | 'bottom' | 'left' | 'right', number>> = {
  gaejo: {
    top: 20,
    bottom: 15,
    left: 20,
    right: 20,
  },
}

/** A size in CSS pixels at 96 dpi. */
export function px(pt: number): number {
  return Math.round((pt * 4) / 3 * 100) / 100
}

/** Custom properties for the web view: `--doc-<name>` in px and `--doc-leading-<name>`. */
export function docVariables(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [name, pt] of Object.entries(TYPE)) out[`--doc-${name}`] = `${px(pt)}px`
  for (const [name, ratio] of Object.entries(LEADING)) out[`--doc-leading-${name}`] = `${ratio}`
  return out
}

/** Typeface stacks for the stored `font` token; the exporters pick matching faces. */
export const FAMILIES = {
  gothic: "'Pretendard', 'Pretendard Variable', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif",
  serif: "'Nanum Myeongjo', 'NanumMyeongjo', 'Noto Serif KR', 'Batang', 'Apple Myungjo', Georgia, serif",
} as const

/** The stack for a design's font, or `undefined` to inherit when nothing was chosen. */
export function fontFamilyFor(font?: string | null): string | undefined {
  return font === 'serif' ? FAMILIES.serif : font === 'gothic' ? FAMILIES.gothic : undefined
}

/**
 * The CSS a format with its own scale adds under `.fmt.f-<id>`: the `--doc-*` sizes it
 * overrides (in pt, as print measures them), its spacing and its page margins; empty for
 * a format that keeps the shared scale.
 */
export function formatScaleCss(format: string): string {
  const sizes = FORMAT_TYPE[format]
  const space = FORMAT_SPACE[format]
  if (!sizes || !space) return ''
  const leading = FORMAT_LEADING[format] ?? {}
  const margins = FORMAT_MARGINS[format]
  const variables = [
    ...Object.entries(sizes).map(([name, pt]) => `--doc-${name}: ${pt}pt;`),
    ...Object.entries(leading).map(([name, ratio]) => `--doc-leading-${name}: ${ratio};`),
  ].join(' ')
  const at = `.fmt.f-${format}`
  return `
${at} { ${variables} font-size: var(--doc-body) !important; line-height: var(--doc-leading-body) !important; }
${at} section { margin: 0 !important; }
${at} h2 { margin: ${space.headingBefore}pt 0 ${space.headingAfter}pt !important; font-size: var(--doc-h1) !important; }
${at} :is(.callout, .tb-core) { margin: ${space.callout}pt 0 !important; }
${at} .tb { margin: 0 0 ${space.head}pt !important; }
${at} ul:not(.steps) > li, ${at} ul:not(.steps) ul { margin-top: 0 !important; margin-bottom: ${space.item}pt !important; }
${at} ul:not(.steps) { margin: 0 !important; }
${at} table { margin: ${space.callout}pt 0 !important; }
${at} :is(th, td) { padding: 0.15em 0.5em !important; height: auto !important; }
${margins ? `@page { margin: ${margins.top}mm ${margins.right}mm ${margins.bottom}mm ${margins.left}mm; }` : ''}`
}
