import { ArrowRight } from 'lucide-react'
import { useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Badge } from '@/components/ui'
import {
  argumentText,
  fillPrompt,
  templateText,
  type DesignTemplateRow,
} from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { kindMeta } from '@/lib/kinds'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { useDesignTemplates, useStartTemplate, useTemplateUsage } from '@/lib/useDesignTemplates'
import { useT } from '@/lib/useT'

/** The preview document's own width, which the card scales down from. */

const SHOWN = 8

/**
 * A few shapes on the screen everybody lands on, and the way to the rest.
 *
 * It was reachable only from the empty state of a new session, behind a
 * secondary button — which is to say the whole catalogue was invisible to
 * anybody who did not already know it existed. This is the front door: what
 * the answer can look like, beside the choice of what to make.
 *
 * A taste rather than the whole thing. Sixteen cards is a list nobody reaches
 * the end of, and every one of them is a slide that has to be rendered; the
 * 디자인 screen shows all of them, grouped by surface, with room for what each
 * one asks for.
 *
 * Two rows of four rather than a scroller. A rail hides its own contents: the
 * cards past the fold are behind a gesture people do not make, so half of what
 * was on offer was never seen. A grid shows all eight at once and costs the
 * height of one more row.
 *
 * Picking one here fills its blanks with their own defaults rather than
 * showing the form. The gallery is where you fill them in; from the home
 * screen the point is to *start*, and the sentence lands in the composer where
 * every word of it is still editable.
 */
export function DesignRail({
  /**
   * The surface the person has already chosen at the top of the screen.
   *
   * Without it this rail led with one card of each surface — breadth, which is
   * the right answer on a front door and the wrong one under a tab somebody
   * has just pressed. Standing on 보고서 and being offered five 발표 서식 reads
   * as the tab not having done anything.
   */
  surface,
}: {
  surface?: SessionKind
}) {
  const t = useT()
  const navigate = useNavigate()
  const enabledKinds = useStore((s) => s.enabledKinds)
  // The home screen is reached before the workspace load on a cold open, which
  // is the fallback fetch this hook exists for.
  const rows = useDesignTemplates()
  const usage = useTemplateUsage()
  const startTemplate = useStartTemplate()

  const english = currentLang() === 'en'
  const visible = useMemo(
    () =>
      rows
        .filter((row) => enabledKinds.includes(row.surface))
        .map((row) => ({ row, text: templateText(row, english) })),
    [rows, enabledKinds, english],
  )
  const few = useMemo(() => {
    // What this person keeps picking, then what everybody picks. The catalogue
    // is ordered by id — the order the files sit in — so without this the front
    // door leads with whatever sorted first.
    const used = [...visible].sort(
      (a, b) =>
        (usage.mine[b.row.id] ?? 0) - (usage.mine[a.row.id] ?? 0) ||
        (usage.popular[b.row.id] ?? 0) - (usage.popular[a.row.id] ?? 0),
    )
    // Breadth before depth, applied to that order rather than instead of it.
    // Eight cards all of one surface would say the product makes one kind of
    // thing — true of a brand-new installation, where every count is zero and
    // the id order is four decks in a row. One of each surface first, then the
    // most-used of whatever is left.
    //: The chosen surface first, in the order above. Everything else keeps
    //: the one-of-each rule, so a rail that runs out of 보고서 서식 still shows
    //: what else exists rather than three empty slots.
    const chosen = surface ? used.filter((c) => c.row.surface === surface) : []
    const others = surface ? used.filter((c) => c.row.surface !== surface) : used
    const seen = new Set<SessionKind>()
    const lead: typeof visible = []
    const rest: typeof visible = []
    for (const card of others) {
      if (seen.has(card.row.surface)) rest.push(card)
      else {
        seen.add(card.row.surface)
        lead.push(card)
      }
    }
    return [...chosen, ...lead, ...rest].slice(0, SHOWN)
  }, [visible, usage, surface])
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
      {/* Four across, two down, and one column at a time on the way to a
          phone — a 224px card in a 360px viewport is a card with two words per
          line. */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {few.map(({ row, text }) => {
          const meta = kindMeta[row.surface]
          const Icon = meta.icon
          return (
            <button
              key={row.id}
              onClick={() => start(row, text.examplePrompt)}
              title={text.description}
              className="group overflow-hidden rounded-card border border-line bg-panel text-left transition-colors hover:border-line-strong"
            >
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
