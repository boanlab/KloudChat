import { LayoutGrid } from 'lucide-react'
import { useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { EmptyState, PageHeader, Tabs } from '@/components/ui'
import { DesignTemplateCard } from '@/components/chat/DesignGallery'
import { useDesignTemplates, useStartTemplate } from '@/lib/useDesignTemplates'
import { type DesignTemplateRow } from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { kindMeta, kindOrder } from '@/lib/kinds'
import { DesignsSection } from '@/pages/settings/DesignsSection'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { useT } from '@/lib/useT'

type Tab = 'system' | 'template'

/**
 * The whole catalogue, grouped by the surface each shape is offered on.
 *
 * It existed only behind a per-surface button inside a session, so 회의록 was
 * discoverable by opening a report and 제안 덱 by opening a deck — the shapes
 * were reachable but the catalogue was not. Grouped rather than listed because
 * the surface is what is being chosen between first: a deck and a one-pager
 * are not two versions of the same thing.
 *
 * Drawn in the default look. A card here starts a session with no project, so
 * the defaults are what its document will actually come out in — the same
 * reason the home rail asks the preview route for no tokens either.
 *
 * A disabled surface is left out. Its shapes still ship, but a card whose
 * button opens a screen this workspace turned off is an offer the product
 * cannot keep.
 */
function TemplateCatalogue() {
  const t = useT()
  const navigate = useNavigate()
  const enabledKinds = useStore((s) => s.enabledKinds)
  const rows = useDesignTemplates()
  const startTemplate = useStartTemplate()
  const english = currentLang() === 'en'

  const bySurface = useMemo(() => {
    const groups = new Map<SessionKind, DesignTemplateRow[]>()
    for (const row of rows) {
      if (!enabledKinds.includes(row.surface)) continue
      const found = groups.get(row.surface)
      if (found) found.push(row)
      else groups.set(row.surface, [row])
    }
    return groups
  }, [rows, enabledKinds])

  // The card's button means "start this" here rather than "fill the composer
  // in front of me": there is no session yet, so the surface has to open
  // first. The sentence is waiting in it when it does.
  const start = (row: DesignTemplateRow, prompt: string) => {
    startTemplate(row, prompt)
    navigate(`/new/${row.surface}`)
  }

  if (bySurface.size === 0) {
    return (
      <EmptyState
        icon={<LayoutGrid size={18} />}
        title={t('보여 줄 서식이 없습니다')}
        description={t('슬라이드나 보고서를 켜면 그 화면에서 쓸 수 있는 서식이 여기에 나옵니다.')}
      />
    )
  }

  return (
    <div className="space-y-8">
      {kindOrder
        .filter((kind) => bySurface.has(kind))
        .map((kind) => {
          const meta = kindMeta[kind]
          const Icon = meta.icon
          const forKind = bySurface.get(kind) ?? []
          return (
            <section key={kind} aria-label={t(meta.label)}>
              <h2 className="mb-2.5 flex items-center gap-1.5 text-xs font-semibold tracking-wide text-faint uppercase">
                <Icon size={13} style={{ color: meta.color }} />
                {t(meta.label)}
                <span>{forKind.length}</span>
              </h2>
              <div className="grid gap-3 sm:grid-cols-2">
                {forKind.map((row) => (
                  <DesignTemplateCard key={row.id} row={row} english={english} onPick={start} />
                ))}
              </div>
            </section>
          )
        })}
    </div>
  )
}

/**
 * What a result will look like: the look you make, and the shape it is poured
 * into.
 *
 * Design systems were filed under 설정 → 환경설정, below the model pickers,
 * which is where a preference lives — and a design system is not a preference.
 * It is a thing you make, keep and attach, like a project or an agent, so it
 * belongs beside them.
 *
 * 서식 shares the screen because the two answer one question between them:
 * 디자인 decides the colour, the type and the voice, 서식 decides the shape
 * they are poured into. One is what you make and the other is what the product
 * provides, which is a tab apart rather than a page apart.
 *
 * Which tab is in the query string, so the home rail can point at the half it
 * is a taste of and so a link to either half survives being sent to somebody.
 */
export function DesignsPage() {
  const t = useT()
  const [params, setParams] = useSearchParams()
  const tab: Tab = params.get('tab') === 'template' ? 'template' : 'system'
  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('디자인')}</span>} />
      <PageBody>
        <PageHeader
          title={t('디자인')}
          description={
            tab === 'system'
              ? t(
                  '프로젝트에 붙이면 슬라이드·보고서·이미지가 같은 색과 서체로 나옵니다. 공문 양식이나 지난 보고서에서 읽어 올 수도 있습니다.',
                )
              : t(
                  '제품이 갖고 있는 결과물의 모양입니다. 고르면 그 화면이 열리고 예시 문장이 입력창에 들어갑니다.',
                )
          }
        />
        <Tabs<Tab>
          value={tab}
          onChange={(next) => setParams(next === 'system' ? {} : { tab: next }, { replace: true })}
          tabs={[
            { id: 'system', label: t('디자인 시스템') },
            { id: 'template', label: t('서식') },
          ]}
        />
        <div className="pt-4">{tab === 'system' ? <DesignsSection /> : <TemplateCatalogue />}</div>
      </PageBody>
    </>
  )
}
