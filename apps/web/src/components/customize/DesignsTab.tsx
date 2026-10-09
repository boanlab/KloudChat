import { LayoutGrid, Palette } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { DesignTemplateCard } from '@/components/chat/DesignGallery'
import { EmptyState, LoadingState } from '@/components/ui'
import { designsApi, templateText, type DesignRow, type DesignTemplateRow } from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { kindMeta, kindOrder } from '@/lib/kinds'
import { useDesignTemplates, useStartTemplate } from '@/lib/useDesignTemplates'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'

/** 양식: the templates the product ships, by surface. Picking one opens its surface with it on. */
export function TemplateCatalogue({ query }: { query: string }) {
  const t = useT()
  const navigate = useNavigate()
  const enabledKinds = useStore((s) => s.enabledKinds)
  const rows = useDesignTemplates()
  const startTemplate = useStartTemplate()
  const english = currentLang() === 'en'

  const needle = query.trim().toLowerCase()
  const bySurface = useMemo(() => {
    const groups = new Map<SessionKind, DesignTemplateRow[]>()
    for (const row of rows) {
      if (!enabledKinds.includes(row.surface)) continue
      if (needle) {
        const text = templateText(row, english)
        if (!`${text.name} ${text.description} ${text.category} ${text.fills.join(' ')}`.toLowerCase().includes(needle)) continue
      }
      const found = groups.get(row.surface)
      if (found) found.push(row)
      else groups.set(row.surface, [row])
    }
    return groups
  }, [rows, enabledKinds, needle, english])

  // No session yet: store the pick, then open the surface that consumes it.
  const start = (row: DesignTemplateRow) => {
    startTemplate(row)
    navigate(`/new/${row.surface}`)
  }

  if (bySurface.size === 0) {
    return (
      <EmptyState
        icon={<LayoutGrid size={18} />}
        title={needle ? t('조건에 맞는 양식이 없습니다.') : t('보여 줄 양식이 없습니다')}
        description={needle ? undefined : t('슬라이드나 보고서를 켜면 그 화면에서 쓸 수 있는 양식이 여기에 나옵니다.')}
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
            <section key={kind} aria-label={`${t('양식')} · ${t(meta.label)}`}>
              <h2 className="mb-3 flex items-center gap-1.5 text-md font-semibold">
                <Icon size={15} style={{ color: meta.color }} aria-hidden />
                {`${t(meta.label)} ${t('양식')}`}
                <span className="text-sm font-normal text-faint">{forKind.length}</span>
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

/** 디자인 탐색: the designs others shared with everyone. Templates have their own tab (양식). */
export function SharedDesigns({ query }: { query: string }) {
  const t = useT()
  const [shared, setShared] = useState<DesignRow[] | null>(null)

  useEffect(() => {
    let live = true
    void designsApi
      .list()
      .then((list) => live && setShared(list.filter((row) => !row.mine)))
      .catch(() => live && setShared([]))
    return () => {
      live = false
    }
  }, [])

  if (shared === null) return <LoadingState />
  const needle = query.trim().toLowerCase()
  const sharedShown = shared.filter((row) => !needle || `${row.name} ${row.description}`.toLowerCase().includes(needle))

  if (sharedShown.length === 0) {
    return (
      <EmptyState
        icon={<Palette size={18} />}
        title={needle ? t('조건에 맞는 디자인이 없습니다.') : t('아직 공유된 디자인이 없습니다')}
        description={needle ? undefined : t('내 디자인을 모두에게 공개하면 여기에 올라옵니다.')}
      />
    )
  }

  return (
    <section aria-label={t('공유된 디자인')}>
      <h2 className="mb-3 flex items-center gap-1.5 text-md font-semibold">
        <Palette size={15} aria-hidden />
        {t('공유된 디자인')}
      </h2>
      <ul className="grid gap-3 sm:grid-cols-2">
        {sharedShown.map((row) => (
          <li key={row.id} className="flex items-center gap-3 rounded-card border border-line bg-panel p-4">
            <span aria-hidden className="size-9 shrink-0 rounded-card border border-line" style={{ background: row.tokens.accent }} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-base font-medium">{row.name}</span>
              {row.description && <span className="block truncate text-sm text-muted">{row.description}</span>}
              <span className="block text-sm text-faint">{t('프로젝트와 에이전트에서 고를 수 있습니다.')}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
