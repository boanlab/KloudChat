import { PageBody } from '@/components/layout/AppShell'
import { SystemTab } from './settings/SystemTab'
import { TopBar } from '@/components/layout/TopBar'
import { useT } from '@/lib/useT'

/**
 * Instance configuration — the backend, the proxy and the mail relay.
 *
 * Its own screen rather than a tab inside Settings: everything there is about
 * the person looking at it, and this is about the deployment.
 */
export function AdminSystemPage() {
  const t = useT()
  return (
    <>
      <TopBar left={<span className="text-[13px] font-medium">{t('시스템')}</span>} />
      <PageBody>
        <h1 className="text-2xl font-semibold tracking-tight">{t('시스템')}</h1>
        <p className="mt-1 text-[13px] text-muted">
          {t('모델 프록시와 메일 발송 설정입니다. 저장 즉시 적용되며 재시작이 필요하지 않습니다.')}
        </p>
        <div className="mt-6">
          <SystemTab />
        </div>
      </PageBody>
    </>
  )
}
