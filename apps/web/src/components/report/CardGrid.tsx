/**
 * A grid of labelled lists, and the one box a reader must not skip.
 *
 * Both are fences rather than markup, for the reason `KpiStrip` states: a
 * section body is Markdown, and a raw `<div>` in one is text to every reader
 * of it. `report_export` reads the same two fences and draws them as tables,
 * so what is on the screen is what is in the `.docx`, the `.pdf` and the
 * `.hwpx` — and the words stay words the reader can correct and search.
 *
 *     ```cards
 *     ## 산출물
 *     - 네트워크 전면 교체
 *     ## 목표
 *     - 8개월 안에 완료
 *     ```
 *
 * Two columns is the shape, not four: a card narrower than about a third of
 * the column is a list of two-syllable fragments, which reads as a broken
 * component rather than as a card.
 */

export interface Card {
  title: string
  items: string[]
}

/** Six at most — a third row is the last one anybody reads in a grid. */
export function parseCards(source: string, limit = 6): Card[] {
  const cards: Card[] = []
  for (const raw of (source ?? '').split('\n')) {
    const line = raw.trim()
    if (!line) continue
    if (line.startsWith('#')) {
      cards.push({ title: line.replace(/^#+/, '').trim(), items: [] })
    } else if (cards.length) {
      // `- ` is how a model writes a list and is not part of the words. A line
      // without one is a sentence, and keeps the shape it was written in.
      cards[cards.length - 1].items.push(line.replace(/^[-*]\s*/, '') || line)
    }
  }
  return cards.filter((card) => card.title).slice(0, limit)
}

/** `[제목, 나머지 줄]`. A callout with one line is that line, boxed. */
export function parseCallout(source: string): Card | null {
  const lines = (source ?? '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  if (!lines.length) return null
  return {
    title: lines[0].replace(/^#+/, '').trim(),
    items: lines.slice(1).map((line) => line.replace(/^[-*]\s*/, '')),
  }
}

export function CardGrid({ source }: { source: string }) {
  const cards = parseCards(source)
  if (!cards.length) return null
  return (
    <div className="my-4 grid gap-3 sm:grid-cols-2">
      {cards.map((card, i) => (
        <div key={i} className="rounded-card border border-line px-3.5 py-3">
          {/* 제목 앞의 짧은 색 막대. 카드 넷이 모두 같은 무게로 읽히면 격자가
              아니라 상자 넷이 된다 — 막대가 어디부터 한 칸인지 말한다. */}
          <div className="mb-2 h-1 w-6 rounded-full bg-accent" />
          <p className="mb-1.5 text-md font-semibold leading-snug">{card.title}</p>
          {card.items.length > 0 && (
            <ul className="list-disc space-y-1 pl-4 text-base marker:text-faint">
              {card.items.map((item, j) => (
                <li key={j} className="break-keep">
                  {item}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  )
}

export function Callout({ source }: { source: string }) {
  const callout = parseCallout(source)
  if (!callout?.title) return null
  return (
    // A bar, not a box. The cards and the tables around it are already boxes,
    // and one more frame stops saying "this one".
    <div className="my-4 border-l-[3px] border-accent py-0.5 pl-3.5">
      <p className="text-md font-semibold text-accent">{callout.title}</p>
      {callout.items.map((line, i) => (
        <p key={i} className="mt-1 text-md leading-[1.7] break-keep">
          {line}
        </p>
      ))}
    </div>
  )
}
