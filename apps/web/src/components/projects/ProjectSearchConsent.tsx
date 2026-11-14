import { useEffect, useState } from 'react'
import { Card, Select } from '@/components/ui'
import { authConfig } from '@/lib/api'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { Project, SearchConsentMode } from '@/types'

/** Least to most strict; a project may only pick the admin's setting or a stricter one. */
const MODES: SearchConsentMode[] = ['allow', 'ask', 'off']

const LABEL: Record<SearchConsentMode, string> = {
  ask: '매번 묻기',
  allow: '항상 허용(검색어만)',
  off: '사용 안 함',
}

/**
 * This project's web search in strict-local turns (its files make every turn strict-local):
 * the admin's setting, or a stricter one (`services/search_consent`).
 */
export function ProjectSearchConsent({ project }: { project: Project }) {
  const t = useT()
  const updateProject = useStore((s) => s.updateProject)
  const [admin, setAdmin] = useState<SearchConsentMode>('ask')

  useEffect(() => {
    let live = true
    void authConfig.get().then((config) => {
      const mode = config.privacy?.strictLocalWebSearch
      if (live && mode && MODES.includes(mode)) setAdmin(mode)
    }).catch(() => {})
    return () => { live = false }
  }, [])

  const choices = MODES.slice(MODES.indexOf(admin))
  const own = project.strictLocalWebSearch ?? null
  // A setting the admin has since tightened past reads as the admin's.
  const value = own && choices.includes(own) ? own : ''

  return (
    <Card className="space-y-3 p-4" data-testid="project-search-consent">
      <div>
        <p className="text-base font-medium">{t('strict-local 대화의 외부 검색')}</p>
        <p className="text-sm text-muted">
          {t('프로젝트 자료가 들어간 대화는 strict-local로 답합니다. 외부 정보가 필요할 때 검색어만 보낼지 정합니다. 관리자 설정보다 엄격하게만 바꿀 수 있습니다.')}
        </p>
      </div>
      <Select
        aria-label={t('strict-local 대화의 외부 검색')}
        value={value}
        onChange={(e) => void updateProject(project.id, {
          strictLocalWebSearch: (e.target.value || null) as SearchConsentMode | null,
        })}
      >
        <option value="">{t('관리자 설정 따름 ({mode})').replace('{mode}', t(LABEL[admin]))}</option>
        {choices.filter((mode) => mode !== admin).map((mode) => (
          <option key={mode} value={mode}>{t(LABEL[mode])}</option>
        ))}
      </Select>
    </Card>
  )
}
