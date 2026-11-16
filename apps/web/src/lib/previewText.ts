/** Prompts the app itself shows between turns; not something the conversation said. */
const INTERNAL_LINES = ['이 질문에 답하려면 외부 검색이 필요합니다', '자료를 찾으려면 외부 검색이 필요합니다']

/** A preview as one plain sentence: no markup or tables, none of the app's own prompts; '' when nothing is left. */
export function previewSentence(raw: string | null | undefined): string {
  let text = (raw ?? '').trim()
  if (!text || INTERNAL_LINES.some((s) => text.startsWith(s))) return ''
  // A table is one run of pipes once whitespace is collapsed: keep what comes before it.
  if (/\|\s*:?-{2,}/.test(text)) text = text.slice(0, text.indexOf('|')).trim()
  if (!text) return ''
  text = text
    .replace(/\s*\[\d+(?:[,\s]+\d+)*\]/g, '')
    .replace(/(^|\s)(?:#{1,6}|[-*>]|\d+\.)\s+/g, '$1')
    .replace(/[*_`~]+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  const end = text.search(/[.!?。]\s|다\.\s/)
  if (end >= 0) text = text.slice(0, end + 1)
  return text.length > 90 ? `${text.slice(0, 89)}…` : text
}
