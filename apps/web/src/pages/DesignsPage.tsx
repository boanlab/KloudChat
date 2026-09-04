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

/** Every design template grouped by surface; disabled surfaces are omitted. */
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

  // No session yet: store the pick, then open the surface that consumes it.
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

/** Design systems and templates; the active tab lives in `?tab=`. */
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
