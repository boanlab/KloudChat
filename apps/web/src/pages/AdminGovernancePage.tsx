import { ShieldCheck } from 'lucide-react'
import { Navigate, useSearchParams } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { PageHeader } from '@/components/ui'
import { AuditLog } from '@/pages/AuditLog'
import { useT } from '@/lib/useT'

export function AdminGovernancePage() {
  const t = useT()
  // `?account=…`: a user's detail view links to the trail filtered to that account.
  const [params] = useSearchParams()

  // Policies moved to 시스템 설정 › 보안; an old bookmark to the policy tab follows them.
  if (params.get('tab') === 'policy') return <Navigate to="/admin/system/policy" replace />

  return (
    <>
      <TopBar left={t('감사 로그')} />
      <PageBody>
        <PageHeader
          title={t('감사 로그')}
          description={t('누가 언제 무엇을 했는지 기록입니다. 관리·시스템과 사용자 활동을 나눠 봅니다.')}
        />
        <AuditLog account={params.get('account') ?? ''} />

        <p className="mt-3 flex items-center gap-1.5 text-sm text-faint">
          <ShieldCheck size={13} />
          {t('기록은 고치거나 삭제할 수 없습니다.')}
        </p>
      </PageBody>
    </>
  )
}
