import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { PageHeader } from '@/components/ui'
import { DesignsSection } from '@/pages/settings/DesignsSection'
import { useT } from '@/lib/useT'

/**
 * Design systems, in the workspace rather than in settings.
 *
 * They were filed under 설정 → 환경설정, below the model pickers, which is
 * where a preference lives — and a design system is not a preference. It is a
 * thing you make, keep and attach, like a project or an agent, so it belongs
 * beside them.
 */
export function DesignsPage() {
  const t = useT()
  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('디자인')}</span>} />
      <PageBody>
        <PageHeader
          title={t('디자인')}
          description={t(
            '프로젝트에 붙이면 슬라이드·보고서·이미지가 같은 색과 서체로 나옵니다. 공문 양식이나 지난 보고서에서 읽어 올 수도 있습니다.',
          )}
        />
        <DesignsSection />
      </PageBody>
    </>
  )
}
