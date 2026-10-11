import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { SharedDesigns } from '@/components/customize/DesignsTab'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { DesignsSection } from './DesignsSection'

/** 설정 › 테마: own and shared themes (colour, typeface, writing rules); picked in the agent form and 편집 › 테마. */
export function ThemesTab() {
  const t = useT()
  const [params] = useSearchParams()
  const add = params.get('add')
  const [view, setView] = useState<'mine' | 'shared'>('mine')
  return (
    <div className="space-y-4" data-testid="settings-themes">
      <p className="text-base text-muted">
        {t('테마는 문서와 슬라이드의 색·글꼴·글쓰기 규칙입니다. 에이전트 설정과 패널의 편집 › 테마에서 고릅니다.')}
      </p>
      <div className="inline-flex rounded-control border border-line bg-elevated p-0.5" role="group" aria-label={t('보기')}>
        {(['mine', 'shared'] as const).map((id) => (
          <button
            key={id}
            type="button"
            aria-pressed={view === id}
            onClick={() => setView(id)}
            className={cn(
              'h-8 rounded-control px-3 text-base font-medium whitespace-nowrap',
              view === id ? 'bg-panel text-fg shadow-raised' : 'text-muted hover:text-fg',
            )}
          >
            {id === 'mine' ? t('내 테마') : t('공유된 테마')}
          </button>
        ))}
      </div>
      {view === 'mine' ? (
        <DesignsSection key={add ?? 'list'} initialMode={add === 'new' || add === 'extract' ? add : undefined} />
      ) : (
        <SharedDesigns query="" />
      )}
    </div>
  )
}
