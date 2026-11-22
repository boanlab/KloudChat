/** Splits a message at sentence ends, so a screen can wrap between sentences before it wraps inside one. */
export function splitSentences(text: string): string[] {
  return text.split(/(?<=[.!?。])\s+/).filter(Boolean)
}
