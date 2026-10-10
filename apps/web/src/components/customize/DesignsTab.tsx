import { Palette } from 'lucide-react'
import { useEffect, useState } from 'react'
import { EmptyState, LoadingState } from '@/components/ui'
import { designsApi, type DesignRow } from '@/lib/api'
import { useT } from '@/lib/useT'

/** 공유된 테마: the themes others shared with everyone (설정 › 테마). */
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
        title={needle ? t('조건에 맞는 테마가 없습니다.') : t('아직 공유된 테마가 없습니다')}
        description={needle ? undefined : t('내 테마를 모두에게 공개하면 여기에 올라옵니다.')}
      />
    )
  }

  return (
    <section aria-label={t('공유된 테마')}>
      <h2 className="mb-3 flex items-center gap-1.5 text-md font-semibold">
        <Palette size={15} aria-hidden />
        {t('공유된 테마')}
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
