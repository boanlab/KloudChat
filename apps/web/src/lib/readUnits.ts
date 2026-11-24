/** What 「읽어 주기」 reads from each kind of text: a chat answer, a report, a deck, a document's text. */
import type { ReportSection, Slide } from '@/types'
import type { ReadUnit } from './readAloud'

/** Figures and boxes the page draws from fenced source: their source is not words to read. */
const DRAWN = /^```(?:mermaid|chart|kpi|steps|cards|callout)\b[^\n]*\n[\s\S]*?^```[ \t]*$/gm
const COMMENT = /<!--[\s\S]*?-->/g

/** Comments are cut until none is left, so a split like `<!<!-- -->--` cannot rebuild one. */
function readable(markdown: string): string {
  let text = markdown.replace(DRAWN, '')
  for (let before = ''; before !== text; ) {
    before = text
    text = text.replace(COMMENT, '')
  }
  return text.replace(/<!--|-->/g, '').trim()
}

export const answerUnits = (content: string): ReadUnit[] => [{ text: content }]

/** The title first, then every finished section under its heading. */
export function reportUnits(title: string, sections: ReportSection[]): ReadUnit[] {
  const units: ReadUnit[] = []
  if (title.trim()) units.push({ label: title.trim(), heading: title.trim(), text: '' })
  for (const section of sections) {
    if (section.status === 'pending' || section.status === 'streaming') continue
    units.push({
      label: section.heading || title,
      at: section.id,
      heading: section.heading,
      text: readable(section.content),
    })
  }
  return units
}

/** Each slide under its title: the presenter notes when there are any, else the slide's own lines. */
export function deckUnits(slides: Slide[], label: (n: number) => string): ReadUnit[] {
  return slides.map((slide, i) => {
    const notes = (slide.notes ?? '').trim()
    const own = [
      (slide.bullets ?? []).filter((line) => line.trim()).map((line) => `- ${line}`).join('\n'),
      (slide.body ?? '').trim(),
    ]
      .filter(Boolean)
      .join('\n\n')
    return { label: label(i + 1), at: String(i), heading: slide.title, text: notes || own }
  })
}

/** An office document's text as the server read it: lines are paragraphs. */
export function documentUnits(title: string, text: string): ReadUnit[] {
  const body = text
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .join('\n\n')
  return body ? [{ label: title, heading: title, text: body }] : []
}
