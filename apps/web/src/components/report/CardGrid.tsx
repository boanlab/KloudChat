// `cards` and `callout` fences; `report_export` reads the same fences.
//
//     ```cards
//     ## 산출물
//     - 네트워크 전면 교체
//     ```

export interface Card {
  title: string
  items: string[]
}

export function parseCards(source: string, limit = 6): Card[] {
  const cards: Card[] = []
  for (const raw of (source ?? '').split('\n')) {
    const line = raw.trim()
    if (!line) continue
    if (line.startsWith('#')) {
      cards.push({ title: line.replace(/^#+/, '').trim(), items: [] })
    } else if (cards.length) {
      cards[cards.length - 1].items.push(line.replace(/^[-*]\s*/, '') || line)
    }
  }
  return cards.filter((card) => card.title).slice(0, limit)
}

/** First line is the title, the rest are body lines. */
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
