import { ArrowRight } from 'lucide-react'
import { useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Badge } from '@/components/ui'
import {
  argumentText,
  designTemplatePreviewUrl,
  fillPrompt,
  templateText,
  type DesignTemplateRow,
} from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { kindMeta } from '@/lib/kinds'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { useDesignTemplates, useStartTemplate } from '@/lib/useDesignTemplates'
import { useT } from '@/lib/useT'

/** How many the front door shows before handing over to the catalogue. */
const SHOWN = 6

/**
 * A few shapes on the screen everybody lands on, and the way to the rest.
 *
 * It was reachable only from the empty state of a new session, behind a
 * secondary button — which is to say the whole catalogue was invisible to
 * anybody who did not already know it existed. This is the front door: what
 * the answer can look like, beside the choice of what to make.
 *
 * A taste rather than the whole thing. Sixteen cards in a horizontal scroller
 * is a list nobody reaches the end of, and every one of them is a slide that
 * has to be rendered; the 디자인 screen shows all of them, grouped by surface,
 * with room for what each one asks for.
 *
 * Picking one here fills its blanks with their own defaults rather than
 * showing the form. The gallery is where you fill them in; from the home
 * screen the point is to *start*, and the sentence lands in the composer where
 * every word of it is still editable.
 */
export function DesignRail() {
  const t = useT()
  const navigate = useNavigate()
  const enabledKinds = useStore((s) => s.enabledKinds)
  // The home screen is reached before the workspace load on a cold open, which
  // is the fallback fetch this hook exists for.
  const rows = useDesignTemplates()
  const startTemplate = useStartTemplate()

  const english = currentLang() === 'en'
  const visible = useMemo(
    () =>
      rows
        .filter((row) => enabledKinds.includes(row.surface))
        .map((row) => ({ row, text: templateText(row, english) })),
    [rows, enabledKinds, english],
  )
  // Breadth before depth. The catalogue is ordered by id, so its first six are
  // four decks and two pieces of audio — a rail that would say the product
  // makes one kind of thing. One of each surface first, then whatever fits.
  const few = useMemo(() => {
    const seen = new Set<SessionKind>()
    const lead: typeof visible = []
    const rest: typeof visible = []
    for (const card of visible) {
      if (seen.has(card.row.surface)) rest.push(card)
      else {
        seen.add(card.row.surface)
        lead.push(card)
      }
    }
    return [...lead, ...rest].slice(0, SHOWN)
  }, [visible])
  if (visible.length === 0) return null

  const start = (row: DesignTemplateRow, prompt: string) => {
    startTemplate(
      row,
      fillPrompt(
        prompt,
        Object.fromEntries(
          row.arguments.map((a) => [a.name, argumentText(a, english).initial]),
        ),
      ),
    )
    navigate(`/new/${row.surface}`)
  }

  return (
    <section aria-label={t('서식에서 시작')} className="mb-8">
      <div className="mb-2.5 flex flex-wrap items-baseline gap-2">
        <h2 className="text-base font-semibold">{t('서식에서 시작')}</h2>
        <p className="text-sm text-muted">{t('결과물이 어떤 모양으로 나올지 먼저 고릅니다')}</p>
        {/* The rail is a taste, so it has to say how much it is a taste of and
            where the rest is. Without this the catalogue is still a place you
            have to already know about. */}
        <Link
          to="/designs?tab=template"
          className="ml-auto flex items-center gap-1 text-sm text-muted transition-colors hover:text-fg"
        >
          {t('{n}종 모두 보기').replace('{n}', String(visible.length))}
          <ArrowRight size={13} />
        </Link>
      </div>
      {/* A rail rather than a grid: this is the second thing on the screen,
          and a wall of cards would push the recent work off it. */}
      <div className="-mx-1 flex snap-x gap-3 overflow-x-auto px-1 pb-2">
        {few.map(({ row, text }) => {
          const meta = kindMeta[row.surface]
          const Icon = meta.icon
          return (
            <button
              key={row.id}
              onClick={() => start(row, text.examplePrompt)}
              title={text.description}
              className="group w-56 shrink-0 snap-start overflow-hidden rounded-card border border-line bg-panel text-left transition-colors hover:border-line-strong"
            >
              {row.hasPreview && (
                <div className="pointer-events-none h-28 overflow-hidden border-b border-line bg-white">
                  {/* Drawn in the default look, and asking for no other one is
                      the honest answer here: a card on the home screen starts
                      a session with no project, so the defaults are what its
                      deck will actually come out in. The same gallery opened
                      inside a project passes that project's design system. */}
                  <iframe
                    title={text.name}
                    src={designTemplatePreviewUrl(row.id)}
                    sandbox=""
                    loading="lazy"
                    tabIndex={-1}
                    className="h-[440px] w-[880px] origin-top-left border-0"
                    style={{ transform: 'scale(0.255)' }}
                  />
                </div>
              )}
              <div className="space-y-1 p-2.5">
                <div className="flex items-center gap-1.5">
                  <Icon size={12} style={{ color: meta.color }} />
                  <span className="min-w-0 flex-1 truncate text-base font-medium">
                    {text.name}
                  </span>
                  <Badge>{text.category}</Badge>
                </div>
                <p className="line-clamp-2 text-sm text-muted">{text.description}</p>
              </div>
            </button>
          )
        })}
      </div>
    </section>
  )
}
